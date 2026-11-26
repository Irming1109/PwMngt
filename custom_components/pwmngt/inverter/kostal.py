"""Kostal Plenticore writer (issue #125) -- carries battery_command and
min_soc to a Kostal while "Inverter control" is on (inverter/control.py
decides when).

Port of Claus's Kostal translator (Node-RED function node, issue #1,
04-11-2026), which replaced his subflow "PV Batteri styring" and the
node "Gem værdi" in tab "Ha data - opsætning" (Claus's export
30-10-2026):
- Grid charge and discharge go through four relays (e.g. Shelly, dry
  contact) on the inverter's digital inputs DI1-DI4. The inverter has a
  table that maps each combination of the inputs to a battery power, so
  the combinations below must match that table.
- block_charging sets the inverter's "Battery Charging / Usage mode" to
  Battery:TimeControl:Enable; everything else sets it back to None. Time
  control only switches on the inverter's own weekly schedule, which must
  be "Battery charge blocked, battery discharge permitted in the event
  of demand from house" in every slot (the wizard says so) -- as on
  Claus's Lundekrog Kostal. Smart battery control is deliberately not
  kept: PwMngt takes over the battery control (Claus, 2026-11-24).
- min_soc goes to the inverter's "Battery min SoC" number (already in
  steps of 10 on a Kostal -- data/battery_data.py, issue #36).
- hold_min_soc (Growatt only) and anything unknown count as internal:
  a Kostal holds its min SoC itself.

Relays to turn off are turned off before those to turn on, so the
inverter never sees an unintended combination on the way (a moment of
"internal" is harmless). Only relays that aren't already right are
switched, so a restart with nothing running switches nothing. A relay
that is unavailable/unknown is left alone -- Home Assistant can't switch
it anyway -- and put right once it is back (below).

Issue #134 -- the relays (and the usage mode) are followed, not only
written: while battery_command stands still, a relay or usage mode that
doesn't match it (switched by hand, a Shelly that restarted in the wrong
position, a switch that didn't take) is put right, with a logbook entry.
A correction gets CORRECTION_SETTLE_SECONDS to show in Home Assistant
before it is checked again (also after an ordinary write, so a Shelly
that reports its new state a moment late isn't "corrected"). After
MAX_CORRECTIONS corrections in a row that didn't take, PwMngt stops
trying for that command and raises a Repair, cleared when the relays
are right again. Port of Claus's flow "Shelly kontakter til batteri
styring" (tab "Overvågning / Notifikation", export 30-10-2026), which
only notifies when a relay has been neither on nor off for 3 minutes --
here also a Repair (RELAY_UNAVAILABLE_SECONDS); checking the combination
itself is new in PwMngt.

Node-RED sensor -> PwMngt:
| Node-RED                                       | PwMngt (wizard field)        |
|------------------------------------------------|------------------------------|
| switch.pv_batteri_styring_1..4_switch_0        | kostal_relay_1..4            |
| select.battery_charging_usage_mode_lundekrog   | kostal_usage_mode            |
| number.battery_min_soc_lundekrog               | kostal_min_soc               |
| select.pwm_pv_battery_command (read)           | battery_command              |
| sensor.pwm_pv_battery_data (read)              | Battery data (min_soc)       |
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir

from ..const import (
    CONF_ENTITY_MAP,
    DOMAIN,
    ENTITY_KEY_KOSTAL_MIN_SOC,
    ENTITY_KEY_KOSTAL_USAGE_MODE,
    ENTITY_KEYS_KOSTAL_RELAYS,
)
from ..helpers.state_helper import read_float_state
from .writer import COMMAND_INTERNAL, InverterWriter

LOGGER = logging.getLogger(__name__)

USAGE_MODE_NORMAL = "None"
USAGE_MODE_BLOCK_CHARGING = "Battery:TimeControl:Enable"

# Issue #134 -- see the module docstring.
CORRECTION_SETTLE_SECONDS = 30.0
MAX_CORRECTIONS = 3
# Claus's Node-RED notifies after 3 minutes.
RELAY_UNAVAILABLE_SECONDS = 3 * 60.0
ISSUE_RELAYS_NOT_FOLLOWING = "kostal_relays_not_following"
ISSUE_RELAY_UNAVAILABLE = "kostal_relay_unavailable"

_UNREADABLE_STATES = (None, STATE_UNAVAILABLE, STATE_UNKNOWN)


@dataclass(frozen=True)
class KostalCommand:
    """Relays (1-4) on, and the usage mode, for one battery_command."""

    relays_on: frozenset[int]
    usage_mode: str


def _command(relays: tuple[int, ...] = (), usage_mode: str = USAGE_MODE_NORMAL) -> KostalCommand:
    return KostalCommand(frozenset(relays), usage_mode)


# From Claus's subflow "PV Batteri styring" (node names "Digital 1-3 on"
# etc.) -- the combinations his inverter's DI table is set up for.
KOSTAL_COMMANDS: dict[str, KostalCommand] = {
    COMMAND_INTERNAL: _command(),
    "block_charging": _command(usage_mode=USAGE_MODE_BLOCK_CHARGING),
    "charge_now_25": _command((1, 3)),
    "charge_now_50": _command((1, 2, 3)),
    "charge_now_75": _command((1, 4)),
    "charge_now_100": _command((1, 2)),
    "discharge_now_25": _command((3,)),
    "discharge_now_50": _command((2, 3)),
    "discharge_now_75": _command((4,)),
    "discharge_now_100": _command((2,)),
}


def kostal_command(command: str) -> KostalCommand:
    """What a battery_command means on a Kostal -- internal for anything
    not in KOSTAL_COMMANDS (hold_min_soc, unknown)."""
    return KOSTAL_COMMANDS.get(command, KOSTAL_COMMANDS[COMMAND_INTERNAL])


def relay_changes(
    wanted_on: frozenset[int], states: dict[int, str | None]
) -> tuple[list[int], list[int]]:
    """(relays to turn off, relays to turn on) -- only those not already
    in the wanted state, and not those that can't be read (unavailable,
    unknown, missing). states: relay number -> its current state."""
    known = sorted((relay, state) for relay, state in states.items() if state not in _UNREADABLE_STATES)
    turn_off = [relay for relay, state in known if relay not in wanted_on and state != STATE_OFF]
    turn_on = [relay for relay, state in known if relay in wanted_on and state != STATE_ON]
    return turn_off, turn_on


def clamp_to_number(value: float, minimum: float | None, maximum: float | None) -> float:
    """value inside the number entity's own min/max (when known)."""
    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


class KostalWriter(InverterWriter):
    """See the module docstring."""

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry)
        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._min_soc_entity_id: str | None = entity_map.get(ENTITY_KEY_KOSTAL_MIN_SOC) or None
        self._usage_mode_entity_id: str | None = entity_map.get(ENTITY_KEY_KOSTAL_USAGE_MODE) or None
        relays = {
            number: entity_map.get(key) or None
            for number, key in enumerate(ENTITY_KEYS_KOSTAL_RELAYS, start=1)
        }
        # The relays only count with all four: a command whose combination
        # misses one would give the inverter the wrong power.
        self._relay_entity_ids: dict[int, str] = (
            relays if all(relays.values()) else {}
        )
        # Issue #134: corrections in a row that didn't take, when the last
        # switch may still be on its way (time.monotonic()), and since when
        # a relay has been unreadable (None: all readable).
        self._corrections = 0
        self._settle_until = 0.0
        self._unreadable_since: float | None = None
        self._not_following_raised = False
        self._unreadable_raised = False
        self._warned_options: set[str] = set()

    def is_configured(self) -> bool:
        return bool(self._min_soc_entity_id or self._usage_mode_entity_id or self._relay_entity_ids)

    def trigger_entity_ids(self) -> list[str]:
        """Issue #134: a relay or the usage mode changing under the same
        command is put right at once."""
        entity_ids = list(self._relay_entity_ids.values())
        if self._usage_mode_entity_id:
            entity_ids.append(self._usage_mode_entity_id)
        return entity_ids

    async def async_write_command(
        self, command: str, previous: str | None, startup: bool
    ) -> str | None:
        # A new command starts with a clean slate of corrections.
        self._corrections = 0
        written = await self._async_apply(kostal_command(command))
        if not written:
            return None
        return f"{previous or '-'} -> {command}: {', '.join(written)}"

    async def async_follow(self, command: str) -> str | None:
        """Issue #134: put relays and usage mode right while the command
        stands still -- see the module docstring."""
        now = time.monotonic()
        recheck: list[float] = []
        if (left := self._check_unreadable(now)) is not None:
            recheck.append(left)

        description = None
        wanted = kostal_command(command)
        differs = self._differs(wanted)
        if not differs and now < self._settle_until and self._corrections:
            # Only counts as put right if it still is once settled --
            # otherwise PwMngt would fight something that switches back.
            recheck.append(self._settle_until - now)
        elif not differs:
            if self._corrections or self._not_following_raised:
                LOGGER.info("Kostal: relays and usage mode follow %s again", command)
            self._corrections = 0
            self._clear_issue_not_following()
        elif now < self._settle_until:
            recheck.append(self._settle_until - now)
        elif self._corrections < MAX_CORRECTIONS:
            self._corrections += 1
            written = await self._async_apply(wanted)
            recheck.append(CORRECTION_SETTLE_SECONDS)
            description = (
                f"{command}: put right ({self._corrections}/{MAX_CORRECTIONS}) -- {', '.join(written)}"
            )
        elif not self._not_following_raised:
            self._not_following_raised = True
            LOGGER.warning(
                "Kostal: relays/usage mode still don't follow %s after %d corrections -- Repair raised",
                command,
                MAX_CORRECTIONS,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_RELAYS_NOT_FOLLOWING,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_RELAYS_NOT_FOLLOWING,
                translation_placeholders={"command": command, "entities": ", ".join(self.trigger_entity_ids())},
            )
            description = f"{command}: relays/usage mode don't follow -- Repair raised"

        if recheck:
            self.schedule_sync(min(recheck))
        return description

    @callback
    def stop(self) -> None:
        super().stop()
        self._clear_issue_not_following()
        self._clear_issue_unreadable()
        self._unreadable_since = None

    # -- Relays and usage mode --------------------------------------------

    def _relay_states(self) -> dict[int, str | None]:
        return {
            number: (state.state if (state := self.hass.states.get(entity_id)) else None)
            for number, entity_id in self._relay_entity_ids.items()
        }

    def _usage_mode_to_write(self, wanted: KostalCommand) -> bool:
        """True when the usage mode can be read, isn't the wanted one and
        has it as an option."""
        if not self._usage_mode_entity_id:
            return False
        state = self.hass.states.get(self._usage_mode_entity_id)
        if state is None or state.state in _UNREADABLE_STATES or state.state == wanted.usage_mode:
            return False
        if wanted.usage_mode not in (state.attributes.get("options") or []):
            # Once per option -- this runs on every sync.
            if wanted.usage_mode not in self._warned_options:
                self._warned_options.add(wanted.usage_mode)
                LOGGER.warning(
                    "%s has no option '%s' -- usage mode not changed",
                    self._usage_mode_entity_id,
                    wanted.usage_mode,
                )
            return False
        return True

    def _differs(self, wanted: KostalCommand) -> bool:
        turn_off, turn_on = relay_changes(wanted.relays_on, self._relay_states())
        return bool(turn_off or turn_on) or self._usage_mode_to_write(wanted)

    async def _async_apply(self, wanted: KostalCommand) -> list[str]:
        """Switch what isn't as `wanted` -- what was written, for the
        logbook."""
        written: list[str] = []
        if self._relay_entity_ids:
            turn_off, turn_on = relay_changes(wanted.relays_on, self._relay_states())
            if turn_off:
                await self._async_switch("turn_off", turn_off)
            if turn_on:
                await self._async_switch("turn_on", turn_on)
            if turn_off or turn_on:
                on = "-".join(str(relay) for relay in sorted(wanted.relays_on)) or "none"
                written.append(f"relays on: {on}")

        if self._usage_mode_to_write(wanted):
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": self._usage_mode_entity_id, "option": wanted.usage_mode},
                blocking=True,
            )
            written.append(f"usage mode: {wanted.usage_mode}")

        if written:
            self._settle_until = time.monotonic() + CORRECTION_SETTLE_SECONDS
        return written

    def _check_unreadable(self, now: float) -> float | None:
        """A Repair once a relay has been unreadable for
        RELAY_UNAVAILABLE_SECONDS. Returns the seconds left until then, or
        None when there is nothing to wait for."""
        unreadable = [
            self._relay_entity_ids[relay]
            for relay, state in sorted(self._relay_states().items())
            if state in _UNREADABLE_STATES
        ]
        if not unreadable:
            self._unreadable_since = None
            self._clear_issue_unreadable()
            return None
        if self._unreadable_since is None:
            self._unreadable_since = now
        left = self._unreadable_since + RELAY_UNAVAILABLE_SECONDS - now
        if left > 0:
            return left
        if not self._unreadable_raised:
            self._unreadable_raised = True
            LOGGER.warning("Kostal: relay(s) unavailable for 3 minutes: %s", ", ".join(unreadable))
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_RELAY_UNAVAILABLE,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_RELAY_UNAVAILABLE,
                translation_placeholders={"entities": ", ".join(unreadable)},
            )
        return None

    def _clear_issue_not_following(self) -> None:
        if self._not_following_raised:
            self._not_following_raised = False
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_RELAYS_NOT_FOLLOWING)

    def _clear_issue_unreadable(self) -> None:
        if self._unreadable_raised:
            self._unreadable_raised = False
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_RELAY_UNAVAILABLE)

    async def _async_switch(self, service: str, relays: list[int]) -> None:
        """Turn relays on/off -- homeassistant.turn_on/off, so any on/off
        entity (switch, input_boolean) works."""
        await self.hass.services.async_call(
            "homeassistant",
            service,
            {"entity_id": [self._relay_entity_ids[relay] for relay in relays]},
            blocking=True,
        )

    def supports_min_soc(self) -> bool:
        return bool(self._min_soc_entity_id)

    def read_min_soc(self) -> float | None:
        if not self._min_soc_entity_id:
            return None
        return read_float_state(self.hass.states.get(self._min_soc_entity_id))

    async def async_write_min_soc(self, min_soc: float) -> str | None:
        if not self._min_soc_entity_id:
            return None
        state = self.hass.states.get(self._min_soc_entity_id)
        if state is None:
            return None
        value = clamp_to_number(
            min_soc, _float_or_none(state.attributes.get("min")), _float_or_none(state.attributes.get("max"))
        )
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": self._min_soc_entity_id, "value": value},
            blocking=True,
        )
        return f"min_soc {value:g} %"


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
