"""Growatt writer (issue #126) -- carries battery_command to a Growatt read
through growatt_modbus (VPP remote control) while "Inverter control" is on
(inverter/control.py decides when).

Port of Claus's function node "Oversætter PwMngt's battery_command til
Growatt VPP-kommandoer" (tab "Batteri styring NY", 24-11-2026), which merged
the earlier "Lad nu - Growatt" and "Oversætter til Growatt":
- charge_now_X: growatt_modbus.set_battery_mode "charge" with X % of the
  battery's max charge power, as a Growatt-% (one % = 1 % of the
  inverter's max AC output -- the same step as data/battery_hold.py).
- discharge_now_X (issue #64, selling): "discharge" with X % of the same.
- hold_min_soc and block_charging (issue #28): "discharge" at the power of
  "Battery hold power" (data/battery_hold.py); afterwards each change of
  that power is only a number.set_value on the remote power number (no
  EEPROM write). Switching between the two only changes the power.
- Leaving any of these for anything else: "release" -- at once.
- Anything else (internal, ...) after nothing of the above: nothing.

Every mode change (set_battery_mode) is written to EEPROM and logged in the
logbook; power changes aren't logged, as in Node-RED.

growatt_modbus rejects a write to register 30407 (remote power enable) or
30409 (power) within 30 s of the last one ("Failed to enable remote power
control"); only release is exempt. So the time of the last write per
register is kept, and a start or power change that comes too early waits
(schedule_sync()) and is sent then with the newest values -- a newer
command replaces a waiting one. Release is always sent at once.

Issue #135 (new in PwMngt -- Node-RED doesn't check it): growatt_modbus'
"VPP Control State" shows whether the inverter really runs what was
written. Under charge/discharge/hold it is charge, discharge or
armed_idle (under hold it follows the power's sign -- seen on Claus's
Growatt 17.-26.11.2026), after a release "released". So only that split
is checked: under a remote command "released" is wrong, after PwMngt's
own release charge/discharge/armed_idle is (other states, unknown and
unavailable don't count). Wrong for VPP_MISMATCH_SECONDS (and at least
that long after the last mode write) -> the command is sent again, at
most MAX_RESENDS times per command since each costs an EEPROM write;
then a warning and a Repair, cleared when the state is right again.
Under hold, the remote power number itself is also compared with the
power PwMngt wrote, and put right when it differs (no EEPROM write; at
most MAX_POWER_CORRECTIONS in a row, so a number that rounds or clamps
the value isn't written every 31 s).

DIFF: Node-RED uses a fixed 150 W per Growatt-% (15 kW / 100); PwMngt
takes 1 % of "Solar inverter max AC output", as data/battery_hold.py does
(issue #54) -- the same 150 W on Claus's 15 kW inverter.

Node-RED sensor -> PwMngt:
| Node-RED                                                | PwMngt                                  |
|---------------------------------------------------------|-----------------------------------------|
| select.pwm_pv_battery_command (read)                    | battery_command                         |
| sensor.pwm_pv_battery_hold_power (read)                 | Battery hold power                      |
| select.pwm_pv_battery_max_charge_effect_w (read)        | Battery max charge effect (W)           |
| W_PR_PCT = 150                                          | 1 % of Solar inverter max AC output     |
| number.growatt_battery_remote_charge_and_discharge_power | growatt_remote_power (wizard field)    |
| DEVICE_ID (the inverter device)                         | that number's device's via_device       |
"""

from __future__ import annotations

import logging
import math
import time

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from ..const import CONF_ENTITY_MAP, DOMAIN, ENTITY_KEY_GROWATT_REMOTE_POWER
from ..helpers.state_helper import read_float_state
from ..properties import pv_properties
from .writer import InverterWriter

LOGGER = logging.getLogger(__name__)

GROWATT_MODBUS_DOMAIN = "growatt_modbus"

# Registers growatt_modbus locks for 30 s after a write (+1 s margin).
REGISTER_ENABLE = 30407  # written by charge/discharge (1) and release (0)
REGISTER_POWER = 30409  # written by charge/discharge and the power number
LOCKOUT_SECONDS = 31.0

# Node-RED's fallbacks: 150 W per Growatt-% and a 3000 W battery.
DEFAULT_STEP_W = 150.0
DEFAULT_MAX_CHARGE_W = 3000.0

COMMANDS_HELD = frozenset({"hold_min_soc", "block_charging"})

# Issue #135 -- see the module docstring.
VPP_REMOTE_STATES = frozenset({"charge", "discharge", "armed_idle"})
VPP_RELEASED = "released"
VPP_MISMATCH_SECONDS = 3 * 60.0
MAX_RESENDS = 2
MAX_POWER_CORRECTIONS = 3
ISSUE_VPP_NOT_FOLLOWING = "growatt_vpp_not_following"


def js_round(value: float) -> int:
    """Math.round() as in the Node-RED node: .5 rounds up (Python rounds to
    even)."""
    return math.floor(value + 0.5)


def command_percent(command: str, prefix: str) -> int | None:
    """X from "<prefix>X" (charge_now_50 -> 50), else None."""
    if command.startswith(prefix):
        try:
            return int(command[len(prefix):])
        except ValueError:
            return None
    return None


def charge_percent(command: str | None) -> int | None:
    return command_percent(command or "", "charge_now_")


def discharge_percent(command: str | None) -> int | None:
    return command_percent(command or "", "discharge_now_")


def is_held(command: str | None) -> bool:
    return command in COMMANDS_HELD


def is_remote(command: str | None) -> bool:
    """A command that runs the inverter under remote control."""
    return charge_percent(command) is not None or discharge_percent(command) is not None or is_held(command)


def growatt_percent(watts: float, step_w: float) -> int:
    """W -> Growatt-% (1-100), for charge_now_X and discharge_now_X."""
    return min(100, max(1, js_round(watts / step_w)))


def vpp_mismatch(remote_expected: bool, vpp_state: str | None) -> bool:
    """Issue #135: True when "VPP Control State" clearly contradicts what
    PwMngt wrote (remote_expected: a charge/discharge/hold was written,
    False: a release was)."""
    if remote_expected:
        return vpp_state == VPP_RELEASED
    return vpp_state in VPP_REMOTE_STATES


def hold_percent(hold_power_w: float, step_w: float) -> int:
    """The remote power number's value for "Battery hold power" (Node-RED
    holdPct(): negative = the inverter delivers)."""
    return -min(100, js_round(hold_power_w / step_w))


def hold_start_percent(hold_power_w: float | None, step_w: float) -> int:
    """The power_percent the hold starts with: PwMngt's current power, at
    least 1 % (1 % when it can't be read yet)."""
    if hold_power_w is None:
        return 1
    return max(1, -hold_percent(hold_power_w, step_w))


class GrowattWriter(InverterWriter):
    """See the module docstring."""

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry)
        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._power_entity_id: str | None = entity_map.get(ENTITY_KEY_GROWATT_REMOTE_POWER) or None
        self._device_id = self._inverter_device_id()
        # register -> time.monotonic() of its last write.
        self._written: dict[int, float] = {}
        # The power last written under hold/block (None: not under hold).
        self._last_hold_percent: int | None = None
        # A start (charge/discharge/hold) waiting for the lockout: (command,
        # previous).
        self._pending: tuple[str, str | None] | None = None
        # Issue #135: what the inverter should show -- True: under remote
        # control, False: released by PwMngt, None: not known (nothing
        # written yet, or "internal" without a release), not checked.
        self._remote_expected: bool | None = None
        self._mismatch_since: float | None = None
        self._resends = 0
        self._vpp_issue_raised = False
        self._power_corrections = 0

    def _inverter_device_id(self) -> str | None:
        """The growatt_modbus inverter device: the remote power number sits
        on a sub-device ("Growatt Battery") whose via_device is it."""
        if not self._power_entity_id:
            return None
        entry = er.async_get(self.hass).async_get(self._power_entity_id)
        if entry is None or entry.device_id is None:
            return None
        device = dr.async_get(self.hass).async_get(entry.device_id)
        if device is None:
            return None
        return device.via_device_id or device.id

    def is_configured(self) -> bool:
        return bool(self._power_entity_id and self._device_id)

    def trigger_entity_ids(self) -> list[str]:
        # Issue #135: the remote power number and VPP Control State too.
        entity_ids = [
            pv_properties.battery_hold_power_entity_id(self.hass, self.entry),
            self._power_entity_id,
            pv_properties.growatt_vpp_control_state_entity_id(self.hass),
        ]
        return [entity_id for entity_id in entity_ids if entity_id]

    # -- Reading -----------------------------------------------------------

    def _read(self, entity_id: str | None) -> float | None:
        return read_float_state(self.hass.states.get(entity_id) if entity_id else None)

    def _step_w(self) -> float:
        """1 % of the inverter's max AC output (W), as data/battery_hold.py."""
        max_kw = self._read(pv_properties.inverter_max_ac_kw_entity_id(self.hass, self.entry))
        return max_kw * 10.0 if max_kw and max_kw > 0 else DEFAULT_STEP_W

    def _max_charge_w(self) -> float:
        value = self._read(pv_properties.battery_max_charge_effect_entity_id(self.hass, self.entry))
        return value if value else DEFAULT_MAX_CHARGE_W

    def _hold_power_w(self) -> float | None:
        return self._read(pv_properties.battery_hold_power_entity_id(self.hass, self.entry))

    def _wait(self, *registers: int) -> float:
        """Seconds until all `registers` may be written again (0 = now)."""
        now = time.monotonic()
        return max(
            [0.0] + [self._written.get(register, -math.inf) + LOCKOUT_SECONDS - now for register in registers]
        )

    def _stamp(self, *registers: int) -> None:
        now = time.monotonic()
        for register in registers:
            self._written[register] = now

    # -- Writing -----------------------------------------------------------

    async def _async_set_mode(self, mode: str, power_percent: int | None = None) -> None:
        data = {"device_id": self._device_id, "mode": mode}
        if power_percent is not None:
            data["power_percent"] = power_percent
        await self.hass.services.async_call(GROWATT_MODBUS_DOMAIN, "set_battery_mode", data, blocking=True)

    async def async_write_command(
        self, command: str, previous: str | None, startup: bool
    ) -> str | None:
        # Issue #135: a new command gets its own resends.
        self._resends = 0
        self._mismatch_since = None
        self._power_corrections = 0

        if startup and command == previous:
            # The inverter already runs it (Node-RED's state survived a
            # restart the same way) -- only the hold power may need writing.
            self._remote_expected = True if is_remote(command) else None
            await self._async_follow_hold_power(command)
            return None

        if is_held(command) and is_held(previous):
            # Hold <-> block charging: the same remote control, only the
            # power changes -- no new mode, no EEPROM write.
            self._pending = None
            await self._async_follow_hold_power(command)
            return f"{previous} -> {command}: power only"

        if is_remote(command):
            self._pending = (command, previous)
            return await self._async_start_if_allowed()

        if is_remote(previous) or self._pending is not None:
            # Release is exempt from the lockout -- sent at once, and it
            # cancels a start still waiting.
            self._pending = None
            self._last_hold_percent = None
            self.cancel_scheduled_sync()
            await self._async_release()
            return f"{previous or '-'} -> {command}: release [EEPROM]"
        return None

    async def async_follow(self, command: str) -> str | None:
        if self._pending is not None:
            if self._pending[0] == command:
                return await self._async_start_if_allowed()
            self._pending = None
        await self._async_follow_hold_power(command)
        return await self._async_check_vpp(command)

    @callback
    def stop(self) -> None:
        super().stop()
        self._clear_vpp_issue()

    async def _async_release(self) -> None:
        await self._async_set_mode("release")
        self._stamp(REGISTER_ENABLE)
        self._remote_expected = False

    # -- Issue #135: checking VPP Control State ------------------------------

    def _vpp_state(self) -> str | None:
        entity_id = pv_properties.growatt_vpp_control_state_entity_id(self.hass)
        state = self.hass.states.get(entity_id) if entity_id else None
        return state.state if state is not None else None

    async def _async_check_vpp(self, command: str) -> str | None:
        """Send the command again when VPP Control State has contradicted
        it for a while -- see the module docstring."""
        if self._remote_expected is None:
            return None
        vpp_state = self._vpp_state()
        if not vpp_mismatch(self._remote_expected, vpp_state):
            self._mismatch_since = None
            if vpp_state in VPP_REMOTE_STATES or vpp_state == VPP_RELEASED:
                if self._resends or self._vpp_issue_raised:
                    LOGGER.info("Growatt: VPP Control State follows %s again (%s)", command, vpp_state)
                self._resends = 0
                self._clear_vpp_issue()
            return None

        now = time.monotonic()
        if self._mismatch_since is None:
            self._mismatch_since = now
        wait = max(
            self._mismatch_since + VPP_MISMATCH_SECONDS - now,
            self._written.get(REGISTER_ENABLE, -math.inf) + VPP_MISMATCH_SECONDS - now,
        )
        if wait > 0:
            self.schedule_check(wait)
            return None

        if self._resends < MAX_RESENDS:
            self._resends += 1
            self._mismatch_since = None
            attempt = f"({self._resends}/{MAX_RESENDS})"
            LOGGER.warning(
                "Growatt: VPP Control State is %s under %s -- sending it again %s", vpp_state, command, attempt
            )
            if not self._remote_expected:
                await self._async_release()
                return f"{command}: VPP {vpp_state}, release sent again {attempt} [EEPROM]"
            self._pending = (command, command)
            description = await self._async_start_if_allowed()
            return f"VPP {vpp_state}, sent again {attempt} -- {description}" if description else None

        if not self._vpp_issue_raised:
            self._vpp_issue_raised = True
            LOGGER.warning(
                "Growatt: VPP Control State is still %s under %s after %d resends -- Repair raised",
                vpp_state,
                command,
                MAX_RESENDS,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_VPP_NOT_FOLLOWING,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_VPP_NOT_FOLLOWING,
                translation_placeholders={"command": command, "state": str(vpp_state)},
            )
            return f"{command}: VPP {vpp_state} doesn't follow -- Repair raised"
        return None

    def _clear_vpp_issue(self) -> None:
        if self._vpp_issue_raised:
            self._vpp_issue_raised = False
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_VPP_NOT_FOLLOWING)

    async def _async_start_if_allowed(self) -> str | None:
        """Send the waiting start now, or ask for a sync once the lockout
        is over."""
        wait = self._wait(REGISTER_ENABLE, REGISTER_POWER)
        if wait > 0:
            LOGGER.debug("Growatt: %s waits %.0f s for the register lockout", self._pending, wait)
            self.schedule_sync(wait)
            return None
        pending = self._pending
        try:
            self._pending = None
            return await self._async_start(*pending)
        except Exception:
            # Kept and tried again after a lockout period (inverter/control.py
            # logs the failure).
            self._pending = pending
            self.schedule_sync(LOCKOUT_SECONDS)
            raise

    async def _async_start(self, command: str, previous: str | None) -> str:
        step_w = self._step_w()
        source = f"{previous or '-'} -> {command}"

        charge = charge_percent(command)
        discharge = discharge_percent(command)
        if charge is not None or discharge is not None:
            percent = charge if charge is not None else discharge
            watts = self._max_charge_w() * percent / 100
            power_percent = growatt_percent(watts, step_w)
            mode = "charge" if charge is not None else "discharge"
            self._last_hold_percent = None
            await self._async_set_mode(mode, power_percent)
            self._stamp(REGISTER_ENABLE, REGISTER_POWER)
            self._remote_expected = True
            return f"{source}: {mode} {round(watts)} W (Growatt {power_percent} %) [EEPROM]"

        # hold_min_soc / block_charging: start with PwMngt's current power.
        start_percent = hold_start_percent(self._hold_power_w(), step_w)
        self._last_hold_percent = -start_percent
        await self._async_set_mode("discharge", start_percent)
        self._stamp(REGISTER_ENABLE, REGISTER_POWER)
        self._remote_expected = True
        return f"{source}: discharge {start_percent} % (hold) [EEPROM]"

    async def _async_follow_hold_power(self, command: str) -> None:
        """Under hold/block charging: write a changed "Battery hold power" to
        the remote power number (no EEPROM write, not logged) -- and, issue
        #135, put the number right when it no longer shows what was
        written."""
        if not is_held(command):
            return
        hold_power_w = self._hold_power_w()
        if hold_power_w is None:
            return  # "unknown" right after a start
        percent = hold_percent(hold_power_w, self._step_w())
        correcting = False
        if percent == self._last_hold_percent:
            actual = self._read(self._power_entity_id)
            if actual is None or js_round(actual) == percent:
                self._power_corrections = 0
                return
            if self._power_corrections >= MAX_POWER_CORRECTIONS:
                return
            correcting = True
        # The lockout also gives a fresh write time to show in the number
        # before it is compared again.
        wait = self._wait(REGISTER_POWER)
        if wait > 0:
            self.schedule_check(wait)
            return
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": self._power_entity_id, "value": percent},
            blocking=True,
        )
        self._stamp(REGISTER_POWER)
        self._last_hold_percent = percent
        if correcting:
            self._power_corrections += 1
            LOGGER.info(
                "Growatt: remote power showed something else than %s %% -- written again (%d/%d)",
                percent,
                self._power_corrections,
                MAX_POWER_CORRECTIONS,
            )
        else:
            self._power_corrections = 0
            LOGGER.debug("Growatt: hold power %.0f W -> %s %%", hold_power_w, percent)
