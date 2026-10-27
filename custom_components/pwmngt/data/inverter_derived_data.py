"""Inverter-derived data: Solar PV Plant values PwMngt calculates itself
when the inverter integration has no sensor for them.

Port of Claus's Node-RED functions on tab "Ha data - opsaetning" (export
27-10-2026), written for his Growatt inverter read through the HACS
integration growatt_modbus (0xAHA/Growatt_ModbusTCP):
- "Data" after "Batteri ladet total": Battery Charge Total minus AC
  Charge Energy Total ("Growatt inverter leverer en total sensor for
  ladning (inkl. PV og GRID) og en sensor for grid ladning, saa vi skal
  selv beregne PV ladning").
- "Data" after "PV forbrug direkte": min(PV, max(house - grid import -
  battery discharge, 0)), with PV = "Sum af PV" (PV1 + PV2 + PV3, 0 at or
  below 3 W).

Node-RED sensor -> PwMngt:

| Node-RED sensor                | PwMngt                                          |
|--------------------------------|-------------------------------------------------|
| sensor.batteri_ladet           | battery_pv_charged (PwMngtBatteryPvChargedDerivedSensor) |
| sensor.pv_forbrug_direkte      | pv_direct_consumption (PwMngtPvDirectConsumptionDerivedSensor) |
| sensor.pv_total ("Sum af PV")  | only inside calculate_pv_direct_consumption()   |
| -- (none)                      | pv_total_consumption (sensor.py's PwMngtEnergyIntegrationSensor over pv_direct_consumption) |

Each one replaces the plain mirror sensor with the same unique_id, so the
entity_id stays the same -- but only when its wizard field is left empty
and the growatt_modbus sensors it needs exist (derivable_entity_map_keys()).
A picked source entity always wins, e.g. Kostal's own "Battery Charge
from PV Total".

Deliberate differences from Node-RED:
- No offset for the previous inverter's history (Node-RED adds 15335.6
  kWh to Battery PV charged). Nothing in PwMngt uses these totals'
  absolute value: data/savings_data.py books each hour's change and
  data/consumption_data.py differences Recorder's statistics "sum" --
  checked with Claus 27-10-2026.
- Grid import and battery discharge come from the Grid power / Battery
  power fields (normalized by pv_properties.power_sign(), so the parts
  above 0 are exactly import / discharge) rather than Growatt's own
  "Grid Import Power" / "Battery Discharge Power" -- the same values,
  without two more sensors to look up.
- pv_total_consumption (kWh) has no Node-RED counterpart: it integrates
  pv_direct_consumption over time, the way Kostal's own "Home Consumption
  from PV Total" counts it (Claus, 27-10-2026).
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from ..const import (
    CONF_ENTITY_MAP,
    ENTITY_KEY_BATTERY_POWER,
    ENTITY_KEY_BATTERY_PV_CHARGED,
    ENTITY_KEY_GRID_POWER,
    ENTITY_KEY_PV1_POWER,
    ENTITY_KEY_PV2_POWER,
    ENTITY_KEY_PV3_POWER,
    ENTITY_KEY_PV_DIRECT_CONSUMPTION,
    ENTITY_KEY_PV_TOTAL_CONSUMPTION,
    GROWATT_MODBUS_AC_CHARGE_ENERGY_TOTAL,
    GROWATT_MODBUS_BATTERY_CHARGE_TOTAL,
    GROWATT_MODBUS_HOUSE_CONSUMPTION,
    GROWATT_MODBUS_PLATFORM,
)
from ..devices import pv_device_info
from ..helpers.entity_registry_helper import find_entity_by_name
from ..helpers.state_helper import read_float_state
from ..properties import pv_properties

LOGGER = logging.getLogger(__name__)

# Node-RED "Sum af PV": a total at or below this many watts reads 0
# (inverter noise at night).
_PV_TOTAL_NOISE_W = 3.0

_PV_POWER_KEYS = (ENTITY_KEY_PV1_POWER, ENTITY_KEY_PV2_POWER, ENTITY_KEY_PV3_POWER)


# ---------------------------------------------------------------------------
# Pure calculations -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


def calculate_battery_pv_charged(charge_total: float, grid_charge_total: float) -> float:
    """kWh charged into the battery from PV in total: everything charged
    minus what was charged from the grid."""
    return charge_total - grid_charge_total


def calculate_pv_direct_consumption(
    pv_powers: list[float],
    house_power: float,
    grid_power: float,
    battery_power: float,
) -> float:
    """W used by the house straight from PV, not through the battery.

    grid_power/battery_power are normalized (positive = buying /
    discharging, see pv_properties.power_sign()). Whatever the house uses
    beyond grid import and battery discharge must come from PV -- capped
    at what PV produces right now, and never below 0.
    """
    pv_total = sum(pv_powers)
    if pv_total <= _PV_TOTAL_NOISE_W:
        pv_total = 0.0
    grid_import = max(grid_power, 0.0)
    battery_discharge = max(battery_power, 0.0)
    return round(min(pv_total, max(house_power - grid_import - battery_discharge, 0.0)))


# ---------------------------------------------------------------------------
# Which fields can be derived on this install.
# ---------------------------------------------------------------------------


def _growatt_modbus_entity_id(hass: HomeAssistant, name: str) -> str | None:
    return find_entity_by_name(hass, GROWATT_MODBUS_PLATFORM, name)


def derivable_entity_map_keys(hass: HomeAssistant) -> set[str]:
    """The Solar PV Plant fields PwMngt can calculate itself here, because
    the growatt_modbus sensors each one needs exist. Used by the wizard
    (such a field isn't required) and by sensor.py (an empty such field
    gets the calculated sensor instead of the mirror)."""
    keys: set[str] = set()
    if _growatt_modbus_entity_id(
        hass, GROWATT_MODBUS_BATTERY_CHARGE_TOTAL
    ) and _growatt_modbus_entity_id(hass, GROWATT_MODBUS_AC_CHARGE_ENERGY_TOTAL):
        keys.add(ENTITY_KEY_BATTERY_PV_CHARGED)
    if _growatt_modbus_entity_id(hass, GROWATT_MODBUS_HOUSE_CONSUMPTION):
        keys.add(ENTITY_KEY_PV_DIRECT_CONSUMPTION)
        # Integrates pv_direct_consumption, so derivable whenever that is.
        keys.add(ENTITY_KEY_PV_TOTAL_CONSUMPTION)
    return keys


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


class _PwMngtDerivedSensor(SensorEntity):
    """Shared wiring: recalculate whenever one of self._source_entity_ids
    changes. A subclass fills that list in __init__ and implements
    _calculate() (None = can't calculate right now -> unavailable)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry) -> None:
        self.entity_description = description
        # Same unique_id as the mirror sensor this replaces (sensor.py's
        # PwMngtPvMirrorSensor), so the entity_id doesn't change.
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False
        self._entry = entry
        self._source_entity_ids: list[str] = []

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _handle_source_update(_event) -> None:
            self._recompute()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._source_entity_ids, _handle_source_update
            )
        )
        self._recompute()

    def _read(self, entity_id: str | None) -> float | None:
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None

    def _calculate(self) -> float | None:
        raise NotImplementedError

    @callback
    def _recompute(self) -> None:
        value = self._calculate()
        self._attr_available = value is not None
        if value is not None:
            self._attr_native_value = value
        self.async_write_ha_state()


class PwMngtBatteryPvChargedDerivedSensor(_PwMngtDerivedSensor):
    """battery_pv_charged = growatt_modbus Battery Charge Total - AC Charge
    Energy Total. Unavailable while either is (Node-RED sends nothing on
    an invalid value either)."""

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry, hass: HomeAssistant) -> None:
        super().__init__(description, entry)
        self._charge_total_id = _growatt_modbus_entity_id(hass, GROWATT_MODBUS_BATTERY_CHARGE_TOTAL)
        self._grid_charge_total_id = _growatt_modbus_entity_id(
            hass, GROWATT_MODBUS_AC_CHARGE_ENERGY_TOTAL
        )
        self._source_entity_ids = [self._charge_total_id, self._grid_charge_total_id]

    def _calculate(self) -> float | None:
        charge_total = self._read(self._charge_total_id)
        grid_charge_total = self._read(self._grid_charge_total_id)
        if charge_total is None or grid_charge_total is None:
            return None
        return round(calculate_battery_pv_charged(charge_total, grid_charge_total), 2)


class PwMngtPvDirectConsumptionDerivedSensor(_PwMngtDerivedSensor):
    """pv_direct_consumption from growatt_modbus House Consumption and the
    PV1-3 / Grid power / Battery power fields. Like Node-RED, a missing or
    unavailable input counts as 0 -- except the house itself, without which
    there is nothing to calculate."""

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry, hass: HomeAssistant) -> None:
        super().__init__(description, entry)
        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._house_id = _growatt_modbus_entity_id(hass, GROWATT_MODBUS_HOUSE_CONSUMPTION)
        self._pv_ids = [entity_map.get(key) for key in _PV_POWER_KEYS if entity_map.get(key)]
        self._grid_id = entity_map.get(ENTITY_KEY_GRID_POWER) or None
        self._battery_id = entity_map.get(ENTITY_KEY_BATTERY_POWER) or None
        self._source_entity_ids = [
            entity_id
            for entity_id in (self._house_id, *self._pv_ids, self._grid_id, self._battery_id)
            if entity_id
        ]

    def _calculate(self) -> float | None:
        house = self._read(self._house_id)
        if house is None:
            return None
        return calculate_pv_direct_consumption(
            [self._read(entity_id) or 0.0 for entity_id in self._pv_ids],
            house,
            (self._read(self._grid_id) or 0.0)
            * pv_properties.power_sign(self._entry, ENTITY_KEY_GRID_POWER),
            (self._read(self._battery_id) or 0.0)
            * pv_properties.power_sign(self._entry, ENTITY_KEY_BATTERY_POWER),
        )
