"""Consumption status: since-local-midnight snapshots of raw, ever-
increasing consumption counters, sampled at fixed times of day.

See the package docstring in data/__init__.py for the general shape.
PwMngtConsumptionStatusSensor is the Home Assistant wiring: a handful of
fixed daily alarms (one per sample time, per category) call
calculate_since_midnight() below and store the result as an attribute.
data/consumption.py's rolling averages (PwMngtConsumptionDataSensor) are
the actual consumer of these snapshots, once that calculation logic is
built -- same relationship the Node-RED original had between its
"forbrug status" flow and its "gennemsnitsforbrug" flow.

Why "since local midnight" at all: the source entities are raw, ever-
increasing lifetime counters (kWh totals that never reset) -- not the
Daily Utility Meter helpers the Node-RED flow read from. Home Assistant's
own `utility_meter` platform reset those to 0 at every local midnight for
free; PwMngtConsumptionStatusSensor replicates just that one behavior
itself (captures each category's counter value at local midnight as a
baseline, then reports every later reading as raw-minus-baseline), so an
installation doesn't need a hand-configured YAML helper just to feed this
integration.
"""

import logging

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from ..const import CONF_ENTITY_MAP, ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL
from ..devices import pv_device_info
from ..helpers.state_helper import read_float_state

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Every category is sampled at the same 8 fixed times of day (the Node-RED
# original's "Kør kl. X hver dag" injects), and each sample is stored as
# "<category>_kl_<time_key>". Old global keys (Node-RED):
# Husstand_status_kl_6/7/8/9/16/17/21/23_59.
#
# Only "property" (the whole property's total consumption) exists so far.
# Add a category -- mining, chargers, pool heat, ground-source heat pump --
# by adding one entry to _CATEGORIES and one ENTITY_KEY_* in const.py; the
# sensor, the sampling and the restore logic below are all shared.
# ---------------------------------------------------------------------------

_SAMPLE_TIMES: list[tuple[str, int, int]] = [
    ("6", 6, 0),
    ("7", 7, 0),
    ("8", 8, 0),
    ("9", 9, 0),
    ("16", 16, 0),
    ("17", 17, 0),
    ("21", 21, 0),
    ("23_59", 23, 58),
]

_CATEGORIES: list[tuple[str, str]] = [
    ("property", ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL),
]

PwM_CONSUMPTION_STATUS_ATTRIBUTES: list[str] = [
    f"{category}_kl_{time_key}"
    for category, _ in _CATEGORIES
    for time_key, _, _ in _SAMPLE_TIMES
]

PwM_CONSUMPTION_STATUS_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="consumption_status_data",
        name="Consumption status data",
        icon="mdi:clipboard-clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


def calculate_since_midnight(raw_value: float, midnight_baseline: float) -> float:
    """Pure calculation: what a Daily Utility Meter helper on this same
    source would report right now, without one actually being configured
    -- the raw counter's current value minus its value at today's local
    midnight.
    """
    return round(raw_value - midnight_baseline, 3)


class PwMngtConsumptionStatusSensor(RestoreEntity, SensorEntity):
    """A PwM PV-device sensor bundling since-local-midnight consumption
    snapshots (see PwM_CONSUMPTION_STATUS_ATTRIBUTES) as attributes, one
    set per configured category.

    Each category gets its own local-midnight alarm (captures that day's
    baseline) plus one alarm per entry in _SAMPLE_TIMES (captures that
    time's since-midnight snapshot via calculate_since_midnight()). The
    per-category baseline and every snapshot survive a Home Assistant
    restart via RestoreEntity -- restored from this entity's own previous
    attributes, no separate storage needed.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False
        self._attr_extra_state_attributes = {
            key: None for key in PwM_CONSUMPTION_STATUS_ATTRIBUTES
        }

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._category_entity_ids: dict[str, str | None] = {
            category: entity_map.get(entity_key) or None
            for category, entity_key in _CATEGORIES
        }
        # category -> (baseline value, ISO date it was captured for)
        self._midnight_baselines: dict[str, tuple[float, str]] = {}

    async def async_added_to_hass(self) -> None:
        """Restore any previous snapshots and baselines, then start the
        per-category daily alarms."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            for key in PwM_CONSUMPTION_STATUS_ATTRIBUTES:
                if key in last_state.attributes:
                    self._attr_extra_state_attributes[key] = last_state.attributes[key]
            for category, _ in _CATEGORIES:
                baseline = last_state.attributes.get(f"_midnight_baseline_{category}")
                baseline_date = last_state.attributes.get(
                    f"_midnight_baseline_date_{category}"
                )
                if baseline is not None and baseline_date is not None:
                    self._midnight_baselines[category] = (float(baseline), baseline_date)
                    self._attr_extra_state_attributes[
                        f"_midnight_baseline_{category}"
                    ] = baseline
                    self._attr_extra_state_attributes[
                        f"_midnight_baseline_date_{category}"
                    ] = baseline_date

        for category, _ in _CATEGORIES:
            if not self._category_entity_ids[category]:
                LOGGER.warning(
                    "PwMngt: no source entity configured for consumption "
                    "status category '%s', it will stay unavailable",
                    category,
                )
                continue

            self.async_on_remove(
                async_track_time_change(
                    self.hass,
                    self._make_midnight_handler(category),
                    hour=0,
                    minute=0,
                    second=0,
                )
            )
            for time_key, hour, minute in _SAMPLE_TIMES:
                self.async_on_remove(
                    async_track_time_change(
                        self.hass,
                        self._make_sample_handler(category, time_key),
                        hour=hour,
                        minute=minute,
                        second=0,
                    )
                )

        self._attr_available = True
        self.async_write_ha_state()

    def _make_midnight_handler(self, category: str):
        """A fresh closure per category, so each alarm captures the right
        category rather than whatever `category` ends up being after the
        setup loop finishes."""

        @callback
        def _handler(_now) -> None:
            self._capture_midnight_baseline(category)

        return _handler

    def _make_sample_handler(self, category: str, time_key: str):
        @callback
        def _handler(_now) -> None:
            self._sample(category, time_key)

        return _handler

    @callback
    def _capture_midnight_baseline(self, category: str) -> None:
        """Local-midnight alarm: record this category's raw counter value
        right now as the baseline every later reading today gets compared
        against."""
        value = read_float_state(self.hass.states.get(self._category_entity_ids[category]))
        if value is None:
            return

        today = dt_util.now().date().isoformat()
        self._midnight_baselines[category] = (value, today)
        self._attr_extra_state_attributes[f"_midnight_baseline_{category}"] = value
        self._attr_extra_state_attributes[f"_midnight_baseline_date_{category}"] = today
        self.async_write_ha_state()

    @callback
    def _sample(self, category: str, time_key: str) -> None:
        """One of the fixed daily alarms: read this category's current
        raw value and store how much it's risen since today's local
        midnight."""
        raw_value = read_float_state(self.hass.states.get(self._category_entity_ids[category]))
        if raw_value is None:
            return

        today = dt_util.now().date().isoformat()
        baseline = self._midnight_baselines.get(category)
        if baseline is None or baseline[1] != today:
            # No baseline captured for today yet -- e.g. this sample fires
            # before the first local-midnight alarm ever has (right after
            # setup, or a restart during the night). Treat "now" as the
            # baseline so this reports 0 rather than a huge/garbage number
            # -- the same fallback Home Assistant's own utility_meter uses
            # the first time it sees its source.
            baseline = (raw_value, today)
            self._midnight_baselines[category] = baseline
            self._attr_extra_state_attributes[f"_midnight_baseline_{category}"] = raw_value
            self._attr_extra_state_attributes[f"_midnight_baseline_date_{category}"] = today

        self._attr_extra_state_attributes[f"{category}_kl_{time_key}"] = (
            calculate_since_midnight(raw_value, baseline[0])
        )
        self._attr_available = True
        self.async_write_ha_state()
