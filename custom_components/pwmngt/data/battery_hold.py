"""Battery hold: keeps a Growatt battery at battery_data's minimum SoC by
switching battery_command to "hold_min_soc" and publishing the power the
inverter should deliver to the house meanwhile ("Battery hold power").

Port of Claus's Node-RED function '"Lås" batteri - growatt' (tab
"Batteri styring NY", export 28-10-2026), written for his Growatt WIT,
which can't hold a minimum SoC on its own. The hold engages at once when
SoC is below the limit, and at the limit only once the battery has kept
discharging for a while; it releases again when SoC is HYSTERESIS points
above the limit. Meanwhile the inverter runs normal Load First.

PwMngt never talks to the inverter itself (Claus, 2026-10-28): where
Node-RED called growatt_modbus.set_battery_mode and number.set_value on
the remote power, this module only writes its own entities. Claus's own
Node-RED translates them, the same way his "Lad nu - Growatt" translates
the charge_now_* commands:
- battery_command changes TO "hold_min_soc": set_battery_mode "discharge".
- battery_command changes FROM "hold_min_soc": set_battery_mode "release"
  and remote power 0.
- Battery hold power changes to a number while battery_command is
  "hold_min_soc": remote power = -round(W / W per Growatt-%). The state is
  "unknown" from the moment the hold starts until its first power write.

Only created on an install with growatt_modbus' "House Consumption" sensor
(battery_hold_supported()) -- an inverter that holds its minimum SoC
itself (e.g. Kostal) gets battery_data written to it instead (issue #1).

Node-RED sensor -> PwMngt:

| Node-RED                                   | PwMngt                                           |
|--------------------------------------------|--------------------------------------------------|
| sensor.pv_battery_status (state, the limit) | battery_data (data/battery_data.py)              |
| sensor.batteri_soc                         | Battery SoC field                                |
| sensor.growatt_solar_solar_total_power     | pv_total (PV1 + PV2 + PV3)                       |
| sensor.growatt_load_house_consumption      | growatt_modbus "House Consumption" (auto-detected) |
| sensor.batteri_effekt                      | Battery power field, via pv_properties.power_sign() |
| select.pv_batteri_styring                  | battery_command (read; "hold_min_soc"/"internal" written) |
| output 1, growatt_modbus.set_battery_mode  | battery_command -> Claus's Node-RED translator   |
| output 2, number.set_value (remote power)  | battery_hold_power (W) -> Claus's Node-RED translator |
| node.status text                           | the "status" attribute                           |

Deliberate differences from Node-RED (agreed with Claus, issue #12):
- The minimum time between switches (10 min) also applies when the hold
  starts, not only when it releases. Node-RED could start and stop every
  quarter of an hour when the limit hovers around 10 % (20 % in winter).
- battery_command itself says whether the hold is active, instead of a
  remembered flag: the hold can't get stuck when another command (or the
  user) takes over -- the translator releases as soon as battery_command
  leaves "hold_min_soc", whoever changes it. Node-RED paused only for
  "Lad nu"; here every other command pauses it ("charge_now_*",
  "block_*", "discharge_now_*" all outrank the hold), and the hold only
  ever writes battery_command while it is "internal" or "hold_min_soc".
- Paused while battery_mode is "manual", like the other battery_command
  writers (battery_charge_level.py, battery_sell_decision.py).
- Power in W rather than Growatt-%: W per % depends on the inverter
  model, so the conversion lives in the translator. The deadbands are
  Node-RED's 2 / 1 Growatt-% at its 150 W per %.
- Winter (November-January) is read in Home Assistant's own time zone
  rather than a fixed Europe/Copenhagen.

Triggers (as Node-RED): every minute, and whenever PV total, battery_data
or battery_command changes -- plus battery_mode, for the manual pause.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import event
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    ENTITY_KEY_BATTERY_POWER,
    ENTITY_KEY_BATTERY_SOC,
    GROWATT_MODBUS_HOUSE_CONSUMPTION,
    GROWATT_MODBUS_PLATFORM,
)
from ..devices import pv_device_info
from ..helpers.entity_registry_helper import find_entity_by_name
from ..helpers.state_helper import read_float_state
from ..properties import pv_properties
from ..select import signal_set_battery_command

LOGGER = logging.getLogger(__name__)

COMMAND_HOLD = "hold_min_soc"  # New in PwMngt -- no Node-RED option.
_COMMAND_INTERNAL = "internal"  # Old: "Intern".

# Node-RED's settings, same names translated.
_FLOOR_LIMIT = 10  # BUND: Growatt's lowest cutoff -- a limit at or below means no hold.
_WINTER_MIN_LIMIT = 20  # VINTER_MIN: Nov-Jan, hold only from a limit of 20 %.
_WINTER_MONTHS = (11, 12, 1)
_MARGIN_W = 300  # MARGIN_W: safety margin at the limit (clouds).
_LOW_PV_W = 1000  # LAV_PV_W: below this, a hard hold at 0 W (never discharges).
_FULL_SOC = 98  # FULD_SOC: above this, PV surplus may go to the grid.
_COOLDOWN_SECONDS = 30  # COOLDOWN_MS: the growatt_modbus lock per register.
_DEADBAND_UP_W = 300  # DEADBAND_OP: 2 Growatt-% at 150 W per %.
_DEADBAND_DOWN_W = 150  # DEADBAND_NED: 1 % -- quicker, to avoid discharging.
_HYSTERESIS = 5  # HYSTERESE: points above the limit before releasing.
_MIN_SWITCH_SECONDS = 10 * 60  # MIN_SKIFT_MS: spares the inverter's EEPROM.
_DISCHARGE_W = 100  # AFLAD_W: discharging above this counts as "drawing".
_DISCHARGE_SECONDS = 3 * 60  # AFLAD_TID_MS: at the limit, this long in a row.

_RUN_INTERVAL_SECONDS = 60  # Node-RED inject "1 Minut".

# The "status" attribute (Node-RED's node.status text). Translated in
# strings.json under entity.sensor.battery_hold_power.state_attributes.
STATUS_INVALID_INPUT = "invalid_input"
STATUS_PAUSED_MANUAL = "paused_manual"
STATUS_PAUSED_OTHER_COMMAND = "paused_other_command"
STATUS_RELEASED_NO_LIMIT = "released_no_limit"
STATUS_RELEASED_WINTER = "released_winter"
STATUS_WAITING_DISCHARGE = "waiting_discharge"
STATUS_AT_LIMIT = "at_limit"
STATUS_WAITING_MIN_SWITCH_TIME = "waiting_min_switch_time"
STATUS_HOLD_STARTED = "hold_started"
STATUS_RELEASED = "released"
STATUS_ABOVE_LIMIT = "above_limit"
STATUS_CATCHING_UP = "catching_up"
STATUS_HARD_HOLD_LOW_PV = "hard_hold_low_pv"
STATUS_HOLDING_AT_LIMIT = "holding_at_limit"
STATUS_NORMAL_OPERATION = "normal_operation"

PwM_BATTERY_HOLD_SENSORS: list[SensorEntityDescription] = [
    # New in PwMngt -- Node-RED wrote this straight to
    # number.growatt_battery_remote_charge_and_discharge_power.
    SensorEntityDescription(
        key="battery_hold_power",
        name="Battery hold power",
        icon="mdi:battery-lock",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="W",
    ),
]


def battery_hold_supported(hass: HomeAssistant) -> bool:
    """Whether this install gets the hold: a Growatt read through
    growatt_modbus, recognized by its "House Consumption" sensor (the
    hold needs it). Looked up by (platform, name), never by entity_id."""
    return find_entity_by_name(hass, GROWATT_MODBUS_PLATFORM, GROWATT_MODBUS_HOUSE_CONSUMPTION) is not None


# ---------------------------------------------------------------------------
# Pure calculation -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


@dataclass
class BatteryHoldState:
    """What the hold remembers between runs (Node-RED: context
    "laas_state"). Times are epoch seconds; 0 = never."""

    # Whether battery_command was "hold_min_soc" after the last run -- to
    # notice someone else setting or leaving it in between.
    holding: bool = False
    # The power last published (W). None = the hold has just started and
    # the first write is still due (Node-RED: sidst_pct = -1).
    power_w: float | None = 0.0
    last_write: float = 0.0
    last_switch: float = 0.0
    discharge_start: float = 0.0


@dataclass
class BatteryHoldInputs:
    limit: float | None  # battery_data's min SoC (%).
    soc: float | None
    pv_power: float | None
    house_power: float | None
    battery_power: float | None  # Normalized: positive = discharging.
    battery_command: str | None
    battery_mode_manual: bool
    now: float  # Epoch seconds.
    winter: bool


@dataclass
class BatteryHoldResult:
    state: BatteryHoldState
    command: str | None  # battery_command to write, None = leave it.
    status: str
    active: bool
    target_power_w: float | None  # Before deadband/cooldown.


def calculate_battery_hold(state: BatteryHoldState, inputs: BatteryHoldInputs) -> BatteryHoldResult:
    """One run of '"Lås" batteri - growatt' -- see the module docstring
    for what differs from Node-RED."""
    numbers = (inputs.limit, inputs.soc, inputs.pv_power, inputs.house_power, inputs.battery_power)
    if any(value is None for value in numbers) or not inputs.battery_command:
        # Node-RED: "Ugyldigt input - intet ændret".
        return BatteryHoldResult(
            state, None, STATUS_INVALID_INPUT, inputs.battery_command == COMMAND_HOLD, None
        )

    limit, soc, pv_power, house_power = inputs.limit, inputs.soc, inputs.pv_power, inputs.house_power
    now = inputs.now
    st = replace(state)

    # How long has the battery been discharging in a row?
    if inputs.battery_power > _DISCHARGE_W:
        if not st.discharge_start:
            st.discharge_start = now
    else:
        st.discharge_start = 0.0
    discharge_seconds = now - st.discharge_start if st.discharge_start else 0.0

    # Paused while battery_command belongs to someone else. Forgets its
    # state, so it starts cleanly afterwards (Node-RED: tvangslad).
    paused_status = None
    if inputs.battery_mode_manual:
        paused_status = STATUS_PAUSED_MANUAL
    elif inputs.battery_command not in (_COMMAND_INTERNAL, COMMAND_HOLD):
        paused_status = STATUS_PAUSED_OTHER_COMMAND
    if paused_status is not None:
        st.holding = inputs.battery_command == COMMAND_HOLD
        st.power_w = 0.0
        st.last_switch = 0.0  # May hold at once when the other command ends.
        st.discharge_start = 0.0
        return BatteryHoldResult(
            st, None, paused_status, inputs.battery_command == COMMAND_HOLD, None
        )

    active = inputs.battery_command == COMMAND_HOLD
    if active and not st.holding:
        # Set by someone else (e.g. by hand): force the first power write.
        st.power_w = None
    elif not active:
        st.power_w = 0.0

    # Should the hold be active? Only once SoC has reached the limit --
    # release again when PV has charged it a bit above.
    switch_ok = now - st.last_switch >= _MIN_SWITCH_SECONDS
    should_hold = active
    status = None

    if (limit < _WINTER_MIN_LIMIT) if inputs.winter else (limit <= _FLOOR_LIMIT):
        # No (relevant) limit: always normal operation, at once.
        should_hold = False
        status = (
            STATUS_RELEASED_WINTER
            if inputs.winter and limit > _FLOOR_LIMIT
            else STATUS_RELEASED_NO_LIMIT
        )
    elif not active and soc <= limit:
        if soc < limit or discharge_seconds >= _DISCHARGE_SECONDS:
            # Below the limit: hold at once (all PV into the battery to
            # catch up). At the limit: only while the battery keeps
            # discharging -- otherwise the inverter manages by itself.
            if switch_ok:
                should_hold = True
            else:
                status = STATUS_WAITING_MIN_SWITCH_TIME
        else:
            status = STATUS_WAITING_DISCHARGE if discharge_seconds > 0 else STATUS_AT_LIMIT
    elif active and soc >= min(limit + _HYSTERESIS, _FULL_SOC):
        if switch_ok:
            should_hold = False
        else:
            status = STATUS_WAITING_MIN_SWITCH_TIME

    if should_hold and not active:
        st.holding = True
        st.power_w = None
        st.last_write = now  # The start writes the power too -- respect the lock.
        st.last_switch = now
        return BatteryHoldResult(st, COMMAND_HOLD, STATUS_HOLD_STARTED, True, None)
    if not should_hold and active:
        st.holding = False
        st.power_w = 0.0
        st.last_write = now
        st.last_switch = now
        return BatteryHoldResult(st, _COMMAND_INTERNAL, status or STATUS_RELEASED, False, None)

    st.holding = active
    if not active:
        return BatteryHoldResult(st, None, status or STATUS_NORMAL_OPERATION, active, None)

    # The power while the hold is active.
    if soc > limit:
        # Within the hysteresis: the battery may discharge down to the limit.
        target = max(house_power, pv_power) if soc >= _FULL_SOC else house_power
        status = status or STATUS_ABOVE_LIMIT
    elif soc < limit:
        # Below the limit: all PV into the battery, the grid covers the house.
        target = 0.0
        status = STATUS_CATCHING_UP
    elif pv_power < _LOW_PV_W:
        # At the limit with little PV: hard hold.
        target = 0.0
        status = STATUS_HARD_HOLD_LOW_PV
    else:
        # At the limit: PV covers the house as far as it reaches, minus a margin.
        target = max(0.0, min(house_power, pv_power - _MARGIN_W))
        status = STATUS_HOLDING_AT_LIMIT
    target = float(round(target))

    # Deadband: quick when the output goes DOWN (towards 0), slower UP.
    if st.power_w is None:
        due = True
    else:
        difference = target - st.power_w
        due = abs(difference) >= (_DEADBAND_DOWN_W if difference < 0 else _DEADBAND_UP_W)
    if due and now - st.last_write >= _COOLDOWN_SECONDS:
        st.power_w = target
        st.last_write = now

    return BatteryHoldResult(st, None, status, active, target)


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


@dataclass
class PwMngtBatteryHoldStoredData(ExtraStoredData):
    """BatteryHoldState across restarts (Node-RED keeps its context in a
    file for the same reason: the minimum switch time and cooldown must
    survive a restart)."""

    state: BatteryHoldState

    def as_dict(self) -> dict[str, Any]:
        return vars(self.state).copy()

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtBatteryHoldStoredData":
        power_w = restored.get("power_w", 0.0)
        return cls(
            BatteryHoldState(
                holding=bool(restored.get("holding", False)),
                power_w=float(power_w) if power_w is not None else None,
                last_write=float(restored.get("last_write") or 0.0),
                last_switch=float(restored.get("last_switch") or 0.0),
                discharge_start=float(restored.get("discharge_start") or 0.0),
            )
        )


class PwMngtBatteryHoldSensor(RestoreEntity, SensorEntity):
    """Battery hold power -- runs calculate_battery_hold() and writes
    battery_command. See the module docstring."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Changes with every PV update -- not worth a row in the Recorder each time.
    _unrecorded_attributes = frozenset({"target_power"})

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry, hass: HomeAssistant) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {}
        self._entry = entry
        self._state = BatteryHoldState()

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._soc_entity_id = entity_map.get(ENTITY_KEY_BATTERY_SOC) or None
        self._battery_power_entity_id = entity_map.get(ENTITY_KEY_BATTERY_POWER) or None
        self._house_entity_id = find_entity_by_name(
            hass, GROWATT_MODBUS_PLATFORM, GROWATT_MODBUS_HOUSE_CONSUMPTION
        )

    @property
    def extra_restore_state_data(self) -> PwMngtBatteryHoldStoredData:
        return PwMngtBatteryHoldStoredData(self._state)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        restored = await self.async_get_last_extra_data()
        if restored is not None:
            self._state = PwMngtBatteryHoldStoredData.from_dict(restored.as_dict()).state

        self.async_on_remove(
            event.async_track_time_interval(
                self.hass, self._handle_trigger, timedelta(seconds=_RUN_INTERVAL_SECONDS)
            )
        )
        trigger_entity_ids = [
            entity_id
            for entity_id in (
                pv_properties.pv_total_entity_id(self.hass, self._entry),
                pv_properties.battery_data_entity_id(self.hass, self._entry),
                pv_properties.battery_command_entity_id(self.hass, self._entry),
                pv_properties.battery_mode_entity_id(self.hass, self._entry),
            )
            if entity_id
        ]
        if trigger_entity_ids:
            self.async_on_remove(
                event.async_track_state_change_event(self.hass, trigger_entity_ids, self._handle_trigger)
            )
        self._run()

    @callback
    def _handle_trigger(self, _event_or_now=None) -> None:
        self._run()

    @callback
    def _run(self) -> None:
        result = calculate_battery_hold(self._state, self._read_inputs())
        self._state = result.state

        native_value = result.state.power_w if result.active else 0.0
        attributes = {
            "active": result.active,
            "status": result.status,
            "target_power": result.target_power_w,
        }
        if native_value != self._attr_native_value or attributes != self._attr_extra_state_attributes:
            self._attr_native_value = native_value
            self._attr_extra_state_attributes = attributes
            self.async_write_ha_state()

        # Last: the select's state change re-triggers this sensor at once.
        if result.command is not None:
            async_dispatcher_send(
                self.hass, signal_set_battery_command(self._entry.entry_id), result.command
            )

    def _read_inputs(self) -> BatteryHoldInputs:
        battery_power = self._read(self._battery_power_entity_id)
        if battery_power is not None:
            battery_power *= pv_properties.power_sign(self._entry, ENTITY_KEY_BATTERY_POWER)
        command_entity_id = pv_properties.battery_command_entity_id(self.hass, self._entry)
        command_state = self.hass.states.get(command_entity_id) if command_entity_id else None
        return BatteryHoldInputs(
            limit=self._read(pv_properties.battery_data_entity_id(self.hass, self._entry)),
            soc=self._read(self._soc_entity_id),
            pv_power=self._read(pv_properties.pv_total_entity_id(self.hass, self._entry)),
            house_power=self._read(self._house_entity_id),
            battery_power=battery_power,
            battery_command=(
                command_state.state
                if command_state is not None and command_state.state not in ("unknown", "unavailable")
                else None
            ),
            battery_mode_manual=pv_properties.is_battery_mode_manual(self.hass, self._entry),
            now=dt_util.utcnow().timestamp(),
            winter=dt_util.now().month in _WINTER_MONTHS,
        )

    def _read(self, entity_id: str | None) -> float | None:
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None
