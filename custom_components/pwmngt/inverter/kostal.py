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
switched, so a restart with nothing running switches nothing.

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
from dataclasses import dataclass

from homeassistant.const import STATE_OFF, STATE_ON

from ..const import (
    CONF_ENTITY_MAP,
    ENTITY_KEY_KOSTAL_MIN_SOC,
    ENTITY_KEY_KOSTAL_USAGE_MODE,
    ENTITY_KEYS_KOSTAL_RELAYS,
)
from ..helpers.state_helper import read_float_state
from .writer import COMMAND_INTERNAL, InverterWriter

LOGGER = logging.getLogger(__name__)

USAGE_MODE_NORMAL = "None"
USAGE_MODE_BLOCK_CHARGING = "Battery:TimeControl:Enable"


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
    in the wanted state. states: relay number -> its current state."""
    turn_off = [relay for relay, state in sorted(states.items()) if relay not in wanted_on and state != STATE_OFF]
    turn_on = [relay for relay, state in sorted(states.items()) if relay in wanted_on and state != STATE_ON]
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

    def is_configured(self) -> bool:
        return bool(self._min_soc_entity_id or self._usage_mode_entity_id or self._relay_entity_ids)

    async def async_write_command(
        self, command: str, previous: str | None, startup: bool
    ) -> str | None:
        wanted = kostal_command(command)
        written: list[str] = []

        if self._relay_entity_ids:
            states = {
                number: (state.state if (state := self.hass.states.get(entity_id)) else None)
                for number, entity_id in self._relay_entity_ids.items()
            }
            turn_off, turn_on = relay_changes(wanted.relays_on, states)
            if turn_off:
                await self._async_switch("turn_off", turn_off)
            if turn_on:
                await self._async_switch("turn_on", turn_on)
            if turn_off or turn_on:
                on = "-".join(str(relay) for relay in sorted(wanted.relays_on)) or "none"
                written.append(f"relays on: {on}")

        if self._usage_mode_entity_id:
            state = self.hass.states.get(self._usage_mode_entity_id)
            if state is not None and state.state != wanted.usage_mode:
                if wanted.usage_mode not in (state.attributes.get("options") or []):
                    LOGGER.warning(
                        "%s has no option '%s' -- usage mode not changed",
                        self._usage_mode_entity_id,
                        wanted.usage_mode,
                    )
                else:
                    await self.hass.services.async_call(
                        "select",
                        "select_option",
                        {"entity_id": self._usage_mode_entity_id, "option": wanted.usage_mode},
                        blocking=True,
                    )
                    written.append(f"usage mode: {wanted.usage_mode}")

        if not written:
            return None
        return f"{previous or '-'} -> {command}: {', '.join(written)}"

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
