"""Inverter own consumption: what the inverter itself uses -- its standby
and the conversion losses when it charges or discharges the battery.

Not a Node-RED port: Claus calculated it in an old Home Assistant
template sensor for his Kostal ("inverter_egetforbrug", issue #77):
PV + grid + battery - home. Energy in through PV, grid import and battery
discharge must either reach the house or stay in the inverter.

Node-RED sensor -> PwMngt:

| Node-RED sensor                         | PwMngt                                   |
|-----------------------------------------|------------------------------------------|
| sensor.inverter_egetforbrug (HA template) | inverter_own_consumption (W)           |
| -- (none)                               | inverter_own_consumption_today (kWh, sensor.py) |

The house's power is not a wizard field: it is the inverter
integration's own sensor (INVERTER_HOUSE_POWER_NAMES), found by
(platform, original_name) like the wizard's auto-detect -- the platform
being the one the mapped Grid power (or Battery power) belongs to.

Deliberate differences from the old template:
- No 400 W cap: measured on Claus's Growatt (2026-11-13/14) the loss
  while charging from PV is ~500 W and up to ~800 W -- real, not noise.
- No "-200 W" damping of jumps: a 5-minute time-weighted average does
  that job (the sources update at different moments, so one sample can
  be off by a whole step).
- No "Home Power from Own = 0 -> 0" (Kostal asleep): with PV and battery
  at 0 and the grid covering the house, the formula gives ~0 by itself,
  and the same rule then works for every inverter.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    ENTITY_KEY_BATTERY_POWER,
    ENTITY_KEY_GRID_POWER,
    ENTITY_KEY_PV1_POWER,
    ENTITY_KEY_PV2_POWER,
    ENTITY_KEY_PV3_POWER,
    INVERTER_HOUSE_POWER_NAMES,
)
from ..devices import pv_device_info
from ..helpers.entity_registry_helper import (
    entity_platform,
    find_entity_by_name,
    registry_entry_names,
)
from ..helpers.state_helper import read_float_state
from ..properties import pv_properties

LOGGER = logging.getLogger(__name__)

# The average the sensor shows (see the module docstring).
_AVERAGE_WINDOW = timedelta(minutes=5)
# At most one state write this often -- four sources each updating every
# ~10 s would otherwise flood the Recorder.
_MIN_WRITE_INTERVAL = timedelta(seconds=10)

_PV_POWER_KEYS = (ENTITY_KEY_PV1_POWER, ENTITY_KEY_PV2_POWER, ENTITY_KEY_PV3_POWER)

PwM_INVERTER_OWN_CONSUMPTION_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="inverter_own_consumption",
        name="Inverter own consumption",
        icon="mdi:flash-triangle-outline",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="W",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


# ---------------------------------------------------------------------------
# Pure calculations -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


def calculate_inverter_own_consumption(
    pv_total: float, grid_power: float, battery_power: float, house_power: float
) -> float:
    """W the inverter uses itself: PV + grid + battery - house, never below
    0. grid_power/battery_power are normalized (positive = buying /
    discharging, see pv_properties.power_sign())."""
    return max(pv_total + grid_power + battery_power - house_power, 0.0)


def time_weighted_mean(
    samples: list[tuple[datetime, float]], now: datetime, window: timedelta
) -> float | None:
    """The mean of a stepwise signal over [now - window, now]: each sample
    holds until the next one. Samples are in time order; one from before
    the window counts from the window's start. None without samples."""
    if not samples:
        return None
    start = now - window
    total = 0.0
    covered = 0.0
    for index, (at, value) in enumerate(samples):
        until = samples[index + 1][0] if index + 1 < len(samples) else now
        seconds = (min(until, now) - max(at, start)).total_seconds()
        if seconds > 0:
            total += value * seconds
            covered += seconds
    return total / covered if covered > 0 else samples[-1][1]


# ---------------------------------------------------------------------------
# Which sensor is the house's power.
# ---------------------------------------------------------------------------


def house_power_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """The inverter integration's own house-power sensor, on the platform
    the mapped Grid power (else Battery power) belongs to -- or None (an
    inverter PwMngt doesn't know, or no single match)."""
    entity_map = entry.options.get(CONF_ENTITY_MAP, {})
    for key in (ENTITY_KEY_GRID_POWER, ENTITY_KEY_BATTERY_POWER):
        source_entity_id = entity_map.get(key)
        platform = entity_platform(hass, source_entity_id)
        name = INVERTER_HOUSE_POWER_NAMES.get(platform) if platform else None
        if name:
            return find_entity_by_name(hass, platform, name) or _find_by_name_suffix(
                hass, source_entity_id, name
            )
    return None


def _find_by_name_suffix(hass: HomeAssistant, source_entity_id: str, name: str) -> str | None:
    """Fallback: the single sensor of the source entity's own config entry
    whose name ends with " <name>". Kostal stores its sensors' names with
    a prefix that needn't be the device's name -- on Rene's install
    "Kostal Home Power" on device "Hjortevej17" (2026-11-14), which
    find_entity_by_name() can't strip. Only within the one config entry,
    so another inverter's sensor can't match."""
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    source = registry.async_get(source_entity_id)
    if source is None or source.config_entry_id is None:
        return None
    matches = [
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, source.config_entry_id)
        if entity.domain == "sensor"
        and any(
            entity_name == name or entity_name.endswith(f" {name}")
            for entity_name in registry_entry_names(entity, device_registry)
        )
    ]
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


class PwMngtInverterOwnConsumptionSensor(SensorEntity):
    """inverter_own_consumption (W), the 5-minute average. Reads 0 when the
    house's power can't be found on this install (CLAUDE.md: an
    unconfigured source shows 0 rather than failing)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = 0.0
        self._entry = entry
        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._pv_ids = [entity_map[key] for key in _PV_POWER_KEYS if entity_map.get(key)]
        self._grid_id = entity_map.get(ENTITY_KEY_GRID_POWER) or None
        self._battery_id = entity_map.get(ENTITY_KEY_BATTERY_POWER) or None
        self._house_id: str | None = None
        self._samples: deque[tuple[datetime, float]] = deque()
        self._last_write: datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._house_id = house_power_entity_id(self.hass, self._entry)
        if self._house_id is None:
            LOGGER.info(
                "No house power sensor found for this inverter; %s stays 0", self.entity_id
            )
            self.async_write_ha_state()
            return

        @callback
        def _handle_source_update(_event) -> None:
            self._recompute()

        sources = [self._house_id, *self._pv_ids, self._grid_id, self._battery_id]
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [entity_id for entity_id in sources if entity_id], _handle_source_update
            )
        )
        self._recompute()

    @property
    def extra_state_attributes(self) -> dict:
        return {"house_power_entity": self._house_id}

    def _read(self, entity_id: str | None) -> float | None:
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None

    @callback
    def _recompute(self) -> None:
        now = dt_util.utcnow()
        house = self._read(self._house_id)
        if house is not None:
            raw = calculate_inverter_own_consumption(
                sum(self._read(entity_id) or 0.0 for entity_id in self._pv_ids),
                (self._read(self._grid_id) or 0.0)
                * pv_properties.power_sign(self._entry, ENTITY_KEY_GRID_POWER),
                (self._read(self._battery_id) or 0.0)
                * pv_properties.power_sign(self._entry, ENTITY_KEY_BATTERY_POWER),
                house,
            )
            self._samples.append((now, raw))
        # Keep one sample from before the window -- it holds into it.
        while len(self._samples) > 1 and self._samples[1][0] <= now - _AVERAGE_WINDOW:
            self._samples.popleft()
        if self._last_write is not None and now - self._last_write < _MIN_WRITE_INTERVAL:
            return
        mean = time_weighted_mean(list(self._samples), now, _AVERAGE_WINDOW)
        if mean is None:
            return
        self._attr_native_value = round(mean)
        self._last_write = now
        self.async_write_ha_state()
