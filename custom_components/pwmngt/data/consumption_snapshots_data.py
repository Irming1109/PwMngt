"""Consumption snapshots: since-local-midnight snapshots of raw,
ever-increasing consumption counters, sampled at fixed times of day.

See the package docstring in data/__init__.py for the general shape.
PwMngtConsumptionSnapshotsSensor is the Home Assistant wiring: a handful
of fixed daily alarms (one per sample time, per category) call
calculate_since_midnight() below and store the result as an attribute.
data/consumption_averages_data.py's rolling averages
(PwMngtConsumptionAveragesSensor) are the actual consumer of these
snapshots, once that calculation logic is built.

The source entities are raw, ever-increasing lifetime counters (kWh
totals that never reset), not Daily Utility Meter helpers -- this
module captures each category's counter value at local midnight as a
baseline, then reports every later reading as raw-minus-baseline, so no
hand-configured YAML helper is needed to feed this integration.

When a category doesn't have a same-day baseline to resume from yet --
a fresh install, a source configured for the first time, or a restored
baseline from a previous day (Home Assistant was down across local
midnight) -- _backfill_category_from_history() reconstructs today's
baseline and any already-passed sample attributes from Recorder history
instead of leaving them None. If Recorder has nothing to offer, everything
starts blank as before -- _sample()'s own lazy-bootstrap fallback still
covers that.

Swapping the source entity (e.g. a new inverter/meter mapped in under
Options): a baseline is only meaningful for the specific entity it was
captured from -- if the configured entity for a category changes since
the stored baseline was captured, _discard_stale_entity_baseline() below
throws that baseline (and today's in-progress kl_* attributes) away
rather than letting _sample() mix a reading from the new entity against a
baseline from the old one. Depending on whether Recorder has history for
the new entity going back to local midnight, that either reconstructs
today cleanly from the new entity alone, or leaves today short/blank --
never a huge or negative since_midnight value from comparing two
unrelated counters. See that function's docstring for the full reasoning.
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
from ..helpers.history_helper import fetch_state_changes_since
from ..helpers.state_helper import read_float_state

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Every category is sampled at the same 8 fixed times of day, each stored
# as "<category>_kl_<time_key>". Old global keys (Node-RED):
# Husstand_status_kl_6/7/8/9/16/17/21/23_59.
#
# Only "property" (the whole property's total consumption) exists so far.
# Add a category -- PV Surplus (old: mining), chargers, pool heat,
# ground-source heat pump -- by adding one entry to _CATEGORIES and one
# ENTITY_KEY_* in const.py; the sensor, the sampling and the restore
# logic below are all shared.
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

PwM_CONSUMPTION_SNAPSHOTS_ATTRIBUTES: list[str] = [
    f"{category}_kl_{time_key}"
    for category, _ in _CATEGORIES
    for time_key, _, _ in _SAMPLE_TIMES
]

PwM_CONSUMPTION_SNAPSHOTS_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="consumption_snapshots_data",
        name="Consumption snapshots data",
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


class PwMngtConsumptionSnapshotsSensor(RestoreEntity, SensorEntity):
    """A PwM PV-device sensor bundling since-local-midnight consumption
    snapshots (see PwM_CONSUMPTION_SNAPSHOTS_ATTRIBUTES) as attributes, one
    set per configured category.

    Each category gets its own 00:02 alarm (clears yesterday's kl_*
    attributes and captures that day's baseline -- see
    _reset_for_new_day() for why 00:02 and not literally midnight) plus
    one alarm per entry in _SAMPLE_TIMES (captures that time's
    since-midnight snapshot via calculate_since_midnight()). Baseline and
    snapshots survive a restart via RestoreEntity, restored from this
    entity's own previous attributes. Whenever a category doesn't have a
    same-day baseline that way, it's reconstructed from Recorder history
    instead -- see _backfill_category_from_history(). A baseline restored
    for an entity that's no longer this category's configured source is
    thrown away instead -- see _discard_stale_entity_baseline().
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
            key: None for key in PwM_CONSUMPTION_SNAPSHOTS_ATTRIBUTES
        }

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._category_entity_ids: dict[str, str | None] = {
            category: entity_map.get(entity_key) or None
            for category, entity_key in _CATEGORIES
        }
        # category -> (baseline value, ISO date it was captured for, the
        # entity_id it was read from -- None for a baseline restored from
        # before this field existed, see _discard_stale_entity_baseline())
        self._midnight_baselines: dict[str, tuple[float, str, str | None]] = {}

    async def async_added_to_hass(self) -> None:
        """Restore any previous snapshots and baselines, discard any
        baseline whose source entity no longer matches this category's
        configured one, backfill from Recorder history whatever a
        category still doesn't have a same-day baseline for, then start
        the per-category daily alarms."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            for key in PwM_CONSUMPTION_SNAPSHOTS_ATTRIBUTES:
                if key in last_state.attributes:
                    self._attr_extra_state_attributes[key] = last_state.attributes[key]
            for category, _ in _CATEGORIES:
                baseline = last_state.attributes.get(f"_midnight_baseline_{category}")
                baseline_date = last_state.attributes.get(
                    f"_midnight_baseline_date_{category}"
                )
                baseline_entity_id = last_state.attributes.get(
                    f"_midnight_baseline_entity_{category}"
                )
                if baseline is not None and baseline_date is not None:
                    self._midnight_baselines[category] = (
                        float(baseline),
                        baseline_date,
                        baseline_entity_id,
                    )
                    self._attr_extra_state_attributes[
                        f"_midnight_baseline_{category}"
                    ] = baseline
                    self._attr_extra_state_attributes[
                        f"_midnight_baseline_date_{category}"
                    ] = baseline_date
                    self._attr_extra_state_attributes[
                        f"_midnight_baseline_entity_{category}"
                    ] = baseline_entity_id

        today = dt_util.now().date().isoformat()
        for category, _ in _CATEGORIES:
            entity_id = self._category_entity_ids[category]
            if not entity_id:
                LOGGER.warning(
                    "PwMngt: no source entity configured for consumption "
                    "status category '%s', it will stay unavailable",
                    category,
                )
                continue

            self._discard_stale_entity_baseline(category, entity_id)

            baseline = self._midnight_baselines.get(category)
            if baseline is None or baseline[1] != today:
                await self._backfill_category_from_history(category, entity_id, today)

            self.async_on_remove(
                async_track_time_change(
                    self.hass,
                    self._make_midnight_handler(category),
                    hour=0,
                    minute=2,
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

    def _discard_stale_entity_baseline(self, category: str, entity_id: str) -> None:
        """Throw away category's restored baseline (and today's
        in-progress kl_* attributes) if it was captured from a different
        entity than the one now configured -- e.g. Kasper repointed the
        mapping at a new inverter/meter under Options.

        Since-midnight only means anything when the baseline and every
        later reading come from the same physical counter: mixing a
        baseline from the old entity with readings from the new one would
        report either a huge jump (the new entity's own lifetime total,
        if it's been running elsewhere) or a negative one (if it's
        freshly at/near 0) -- neither has anything to do with today's
        real consumption. Rather than try to patch that number, today's
        snapshots for this category are dropped entirely; the very next
        _backfill_category_from_history() call right after this one, or
        the next local-midnight alarm, establishes a fresh baseline
        against the new entity and today (or tomorrow) is measured
        cleanly against it alone.
        """
        baseline = self._midnight_baselines.get(category)
        if baseline is None:
            return

        _, _, baseline_entity_id = baseline
        if baseline_entity_id == entity_id:
            return

        LOGGER.warning(
            "PwMngt: source entity for consumption category '%s' changed "
            "(was %s, now %s) -- discarding today's in-progress snapshots "
            "so they don't mix readings from two different meters; a "
            "fresh baseline will be captured against the new entity",
            category,
            baseline_entity_id,
            entity_id,
        )
        self._midnight_baselines.pop(category, None)
        self._attr_extra_state_attributes[f"_midnight_baseline_{category}"] = None
        self._attr_extra_state_attributes[f"_midnight_baseline_date_{category}"] = None
        self._attr_extra_state_attributes[f"_midnight_baseline_entity_{category}"] = None
        for time_key, _, _ in _SAMPLE_TIMES:
            self._attr_extra_state_attributes[f"{category}_kl_{time_key}"] = None

    async def _backfill_category_from_history(
        self, category: str, entity_id: str, today: str
    ) -> None:
        """Reconstruct this category's midnight baseline and any
        already-passed sample attributes from Recorder history, for when
        we don't have a same-day baseline yet -- a fresh install, a
        source configured for the first time (including right after
        _discard_stale_entity_baseline() above threw an old one away),
        or a restored baseline from a previous day. Leaves everything as
        None if Recorder isn't available or has no history yet --
        _sample()'s own lazy-bootstrap fallback still covers that case.
        """
        local_midnight = dt_util.start_of_local_day()
        changes = await fetch_state_changes_since(
            self.hass, entity_id, dt_util.as_utc(local_midnight)
        )
        if not changes:
            return

        baseline_value = read_float_state(changes[0])
        if baseline_value is None:
            return

        self._midnight_baselines[category] = (baseline_value, today, entity_id)
        self._attr_extra_state_attributes[f"_midnight_baseline_{category}"] = baseline_value
        self._attr_extra_state_attributes[f"_midnight_baseline_date_{category}"] = today
        self._attr_extra_state_attributes[f"_midnight_baseline_entity_{category}"] = entity_id

        sample_times = [
            (time_key, local_midnight.replace(hour=hour, minute=minute))
            for time_key, hour, minute in _SAMPLE_TIMES
        ]
        now = dt_util.now()
        current_value = baseline_value
        sample_idx = 0

        for state in changes[1:]:
            changed_at = dt_util.as_local(state.last_changed)
            while (
                sample_idx < len(sample_times)
                and sample_times[sample_idx][1] <= changed_at
            ):
                time_key, _ = sample_times[sample_idx]
                self._attr_extra_state_attributes[f"{category}_kl_{time_key}"] = (
                    calculate_since_midnight(current_value, baseline_value)
                )
                sample_idx += 1

            value = read_float_state(state)
            if value is not None:
                current_value = value

        while sample_idx < len(sample_times) and sample_times[sample_idx][1] <= now:
            time_key, _ = sample_times[sample_idx]
            self._attr_extra_state_attributes[f"{category}_kl_{time_key}"] = (
                calculate_since_midnight(current_value, baseline_value)
            )
            sample_idx += 1

        LOGGER.info(
            "PwMngt: backfilled consumption-snapshot category '%s' from "
            "history (midnight baseline %.3f)",
            category,
            baseline_value,
        )

    def _make_midnight_handler(self, category: str):
        """A fresh closure per category, so each alarm captures the right
        category rather than whatever `category` ends up being after the
        setup loop finishes."""

        @callback
        def _handler(_now) -> None:
            self._reset_for_new_day(category)

        return _handler

    def _make_sample_handler(self, category: str, time_key: str):
        @callback
        def _handler(_now) -> None:
            self._sample(category, time_key)

        return _handler

    @callback
    def _reset_for_new_day(self, category: str) -> None:
        """00:02 alarm (2 minutes past local midnight, not 00:00 -- see
        below): clear yesterday's kl_* snapshots so they can never be
        mistaken for today's before today's own sample alarms have
        re-populated them, then record this category's raw counter value
        right now as the baseline every later reading today gets compared
        against.

        Why 00:02 and not literally at midnight: data/consumption_averages_data.py's
        PwMngtConsumptionAveragesSensor reads the day that just ended at
        00:01, straight out of these same kl_* attributes -- it relies on
        them still holding yesterday's values at that point (the last one,
        kl_23_59, was captured at 23:58 the evening before and nothing
        else touches them until this alarm). Clearing at 00:00 would wipe
        that data out from under it one minute before it gets read, so
        this alarm runs one minute after that instead. Either way it's
        long done before the earliest real sample time (06:00), so the
        2-minute delay to the baseline itself doesn't matter.
        """
        for time_key, _, _ in _SAMPLE_TIMES:
            self._attr_extra_state_attributes[f"{category}_kl_{time_key}"] = None

        entity_id = self._category_entity_ids[category]
        value = read_float_state(self.hass.states.get(entity_id))
        if value is None:
            self.async_write_ha_state()
            return

        today = dt_util.now().date().isoformat()
        self._midnight_baselines[category] = (value, today, entity_id)
        self._attr_extra_state_attributes[f"_midnight_baseline_{category}"] = value
        self._attr_extra_state_attributes[f"_midnight_baseline_date_{category}"] = today
        self._attr_extra_state_attributes[f"_midnight_baseline_entity_{category}"] = entity_id
        self.async_write_ha_state()

    @callback
    def _sample(self, category: str, time_key: str) -> None:
        """One of the fixed daily alarms: read this category's current
        raw value and store how much it's risen since today's local
        midnight."""
        entity_id = self._category_entity_ids[category]
        raw_value = read_float_state(self.hass.states.get(entity_id))
        if raw_value is None:
            return

        today = dt_util.now().date().isoformat()
        baseline = self._midnight_baselines.get(category)
        if baseline is None or baseline[1] != today:
            # No baseline captured for today yet -- e.g. this fires before
            # the first local-midnight alarm has (right after setup, a
            # restart overnight, or right after
            # _discard_stale_entity_baseline() above threw the previous
            # one away). Treat "now" as the baseline so this reports 0
            # rather than a huge/garbage number -- same fallback Home
            # Assistant's own utility_meter uses on first seeing its
            # source.
            baseline = (raw_value, today, entity_id)
            self._midnight_baselines[category] = baseline
            self._attr_extra_state_attributes[f"_midnight_baseline_{category}"] = raw_value
            self._attr_extra_state_attributes[f"_midnight_baseline_date_{category}"] = today
            self._attr_extra_state_attributes[f"_midnight_baseline_entity_{category}"] = (
                entity_id
            )

        self._attr_extra_state_attributes[f"{category}_kl_{time_key}"] = (
            calculate_since_midnight(raw_value, baseline[0])
        )
        self._attr_available = True
        self.async_write_ha_state()
