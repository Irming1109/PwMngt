"""Consumption data: the PV device's household-consumption sensor --
rolling averages of how much the property uses in fixed parts of the
day, built entirely from Home Assistant's Recorder.

Replaces three earlier modules (Kasper, 2026-09-28):
consumption_snapshots_data.py (8 daily sampling alarms + a midnight
baseline), consumption_charger_data.py (a per-charger since-midnight
accumulator with kl_8/kl_16 snapshots) and consumption_averages_data.py
(the 3 rolling histories, fed at 00:01 from those snapshots). Every one
of those samples only ever existed to feed the rolling histories, and
Recorder already knows each counter's value at any past moment -- so
this module reads each completed day straight from Recorder instead of
sampling it live. That removes the sampling alarms, the midnight
baselines, the restore/lazy-bootstrap logic, the source-swap discard
logic and the 00:01/00:02 ordering dependency; it also makes the day's
last reading exactly 24:00 instead of the old 23:58 ("kl_23_59").

Where the values come from (see helpers/statistics_helper.py):
Recorder's hourly long-term statistics ("sum"), kept indefinitely, so all
_MAX_HISTORY_DAYS days are available immediately -- plus days missed
while Home Assistant was down get filled in afterwards. A source without
statistics (no state_class) falls back to plain state history
(helpers/history_helper.py), which only reaches back Recorder's
purge_keep_days (default 10). Every value this module needs is at a
whole local hour, which is what makes hourly statistics sufficient --
this assumes a time zone with a whole-hour UTC offset (true for Denmark).

When it runs: once a day at 00:15 local (not 00:01 -- Home Assistant
compiles the 23:00-24:00 statistics row a few minutes after midnight),
plus once shortly after startup. Each run fills every day in the last
_MAX_HISTORY_DAYS that isn't in the histories yet, so a run that finds
yesterday not compiled yet is simply retried by the next one.

Old Node-RED functions: "Beregn 8-16 & 6-9 & 17-06" (context list
"Dagtime_forbrug_liste"), "Beregn gennemsnitsforbrug 17-21" (context list
"Spidstimer_forbrug_liste"), "Beregn gennemsnitsforbrug døgn" (context
list "Døgn_forbrug_liste"), and the car-charger correction built from
"Billader_status_kl_8"/"_16" (read off sensor.forbrug_billadere, a daily
utility_meter on charger 1 only -- PwMngt sums every configured charger).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EntityCategory
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import event, restore_state
from homeassistant.util import dt as dt_util

from ..const import (
    CHARGER_TYPE_NOT_INSTALLED,
    CONF_ENTITY_MAP,
    DOMAIN,
    ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL,
)
from ..devices import CHARGERS, pv_device_info
from ..helpers.history_helper import fetch_state_changes_since
from ..helpers.state_helper import read_float_state
from ..helpers.statistics_helper import fetch_hourly_sums
from ..properties import pv_properties
from . import DATA_SENSOR_STATE

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Attributes: rolling averages (kWh per day) over the last
# select.pwm_pv_history_period_days days (7/14/21/30). Each one's period
# of the day is given as local clock hours "from-to"; every value is the
# property's own consumption counter at "to" minus at "from", on the same
# calendar day, unless noted otherwise.
# ---------------------------------------------------------------------------

PwM_CONSUMPTION_DATA_ATTRIBUTES: list[str] = [
    # 08-16. Old: sensor.dagtimer_8_16_snit.
    "daytime_average",
    # 08-16, minus the chargers' own consumption 08-16 (car_charger_consumption
    # below). Pool heating, PV surplus and heat pump (08-16) will be
    # subtracted too once those segments exist, same as Node-RED did.
    # Old: sensor.dagtimer_8_16_korrigeret_snit -- read by Claus's
    # "Min_SOC beregning" (battery charging strategy).
    "daytime_average_adjusted",
    # 06-07.
    "early_morning_average",
    # 07-08.
    "morning_average",
    # 08-09.
    "late_morning_average",
    # 17-21. Old: sensor.spidstimer_17_21_snit.
    "evening_average",
    # 17-24 plus 00-06 of the SAME calendar day -- two pieces, not one
    # continuous night. Kept exactly as Node-RED defined it (Kasper,
    # 2026-09-28). Old: sensor.spidstimer_nat_17_06_snit.
    "nighttime_average",
    # 00-24. Old: sensor.dogn_snit.
    "full_day_average",
    # 08-16 -- not implemented yet (no Pool segment).
    "pool_heating_consumption",
    # 08-16 -- not implemented yet (no PV Surplus segment).
    "pv_surplus_consumption",
    # 08-16, every configured charger summed (each charger's
    # "consumption_source_entity", text.py). Old: Billader_status_kl_16
    # minus Billader_status_kl_8.
    "car_charger_consumption",
    # 08-16 -- not implemented yet (no heat pump category).
    "heat_pump_consumption",
    # Diagnostics, not averages: how many days the histories hold, and
    # the newest one (ISO date).
    "history_days",
    "last_day",
]

PwM_CONSUMPTION_DATA_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="consumption_data",
        name="Consumption data",
        icon="mdi:chart-timeline-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]

# The local clock hours each day's values are read at -- see
# _derive_daily_values() for how they combine. 24 means the following
# midnight.
_BOUNDARY_HOURS: tuple[int, ...] = (0, 6, 7, 8, 9, 16, 17, 21, 24)

# How many calendar days back the histories reach, no matter what
# select.pwm_pv_history_period_days is set to -- same "30" Node-RED capped
# each of its context lists at.
_MAX_HISTORY_DAYS = 30

# A single day's real household consumption above this is implausible --
# see _is_plausible_day(). Kasper's own call on where to draw the line.
_MAX_PLAUSIBLE_FULL_DAY_KWH = 250

# 00:15 local -- see the module docstring's "When it runs".
_DAILY_RUN_HOUR = 0
_DAILY_RUN_MINUTE = 15

# Delay before the first fill after (re)loading, when Home Assistant is
# already running (e.g. an Options-flow reload). Gives the text platform
# -- set up after sensor, see PLATFORMS in const.py -- time to restore
# each charger's consumption_source_entity before it's read.
_STARTUP_DELAY_SECONDS = 30

# Legacy entities this module replaces, removed from the entity registry
# on setup -- see async_remove_legacy_consumption_entities().
_LEGACY_UNIQUE_ID_SUFFIXES: list[str] = [
    "pv_consumption_snapshots_data",
    "pv_consumption_averages_data",
    *(f"{charger['id']}_consumption_data" for charger in CHARGERS),
]


@callback
def async_remove_legacy_consumption_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the 3 kinds of sensor this module replaces from the entity
    registry, so they don't linger as "unavailable" forever. Their stored
    data isn't migrated -- consumption_data rebuilds its histories from
    Recorder on its own first run."""
    registry = er.async_get(hass)
    for suffix in _LEGACY_UNIQUE_ID_SUFFIXES:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{suffix}"
        )
        if entity_id:
            LOGGER.info("PwMngt: removing legacy consumption entity %s", entity_id)
            registry.async_remove(entity_id)


# ---------------------------------------------------------------------------
# Pure calculations -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


def accumulate(total_so_far: float, last_raw: float | None, new_raw: float) -> float:
    """Fold one new raw reading into a running total, treating a
    decrease as a counter reset (a new charging session) rather than
    negative consumption. Only used for the state-history fallback --
    long-term statistics' "sum" already does the same thing itself.

    - last_raw is None (first reading): nothing to add yet.
    - new_raw >= last_raw: ordinary rise -- add the difference.
    - new_raw < last_raw: reset -- the new, lower reading is itself the
      new session's consumption so far, so add it.
    """
    if last_raw is None:
        return total_so_far
    if new_raw >= last_raw:
        return round(total_so_far + (new_raw - last_raw), 3)
    return round(total_so_far + new_raw, 3)


def cumulative_at_boundaries(
    changes: list[State], boundary_timestamps: list[int]
) -> dict[int, float]:
    """State-history fallback: turn a timeline of raw readings into the
    same shape statistics_helper.fetch_hourly_sums() returns -- a
    reset-aware running total (see accumulate()) as of each boundary,
    where "as of ts" means the last reading strictly before ts (the same
    convention as an hourly statistics row ending at ts). A boundary
    before the first reading gets no value."""
    result: dict[int, float] = {}
    boundaries = sorted(boundary_timestamps)
    total = 0.0
    last_raw: float | None = None
    idx = 0
    for state in changes:
        changed_at = state.last_changed.timestamp()
        while idx < len(boundaries) and boundaries[idx] <= changed_at:
            if last_raw is not None:
                result[boundaries[idx]] = total
            idx += 1
        raw = read_float_state(state)
        if raw is not None:
            total = accumulate(total, last_raw, raw)
            last_raw = raw
    if last_raw is not None:
        for ts in boundaries[idx:]:
            result[ts] = total
    return result


def day_boundaries(day: date) -> dict[int, datetime]:
    """Each _BOUNDARY_HOURS hour of day as an aware local datetime (24 =
    the following midnight). Built from wall-clock time, so a DST day
    still reads "16:00" as 16:00 local."""
    tz = dt_util.get_default_time_zone()
    return {
        hour: (
            datetime.combine(day, time(hour), tzinfo=tz)
            if hour < 24
            else datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
        )
        for hour in _BOUNDARY_HOURS
    }


def _derive_daily_values(v: dict[int, float]) -> dict[str, float]:
    """One day's per-period figures from the property counter's value at
    each _BOUNDARY_HOURS hour -- the periods documented next to
    PwM_CONSUMPTION_DATA_ATTRIBUTES."""
    return {
        "early_morning": round(v[7] - v[6], 2),
        "morning": round(v[8] - v[7], 2),
        "late_morning": round(v[9] - v[8], 2),
        "daytime": round(v[16] - v[8], 2),
        "evening": round(v[21] - v[17], 2),
        "nighttime": round((v[24] - v[17]) + (v[6] - v[0]), 2),
        "full_day": round(v[24] - v[0], 2),
    }


def _is_plausible_day(values: dict[str, float]) -> bool:
    """Whether one day's figures are sane enough to enter the histories:
    every one >= 0 (the counter only ever increases) and the full day at
    most _MAX_PLAUSIBLE_FULL_DAY_KWH. A day that fails is skipped (shows
    up as one missing day) rather than left to skew a 7-30 day average."""
    if any(value < 0 for value in values.values()):
        return False
    return values["full_day"] <= _MAX_PLAUSIBLE_FULL_DAY_KWH


def _average(
    history: list[dict[str, Any]], field_name: str, period_days: int, newest: date
) -> float | None:
    """Mean of one field over the entries dated within the last
    period_days calendar days up to newest (yesterday) -- a missing day
    just shrinks the divisor, same "use what's there" rule Node-RED had.
    Entries whose field is None (e.g. car_charger for a day a charger
    source had no data) are left out. None if nothing's left."""
    cutoff = newest - timedelta(days=max(1, period_days) - 1)
    values = [
        entry[field_name]
        for entry in history
        if entry.get(field_name) is not None
        and date.fromisoformat(entry["date"]) >= cutoff
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def calculate_daytime_averages(
    history: list[dict[str, Any]], period_days: int, newest: date
) -> dict[str, float | None]:
    """The 7 averages the daytime history supplies. Old Node-RED context
    list: "Dagtime_forbrug_liste"."""
    return {
        "early_morning_average": _average(history, "early_morning", period_days, newest),
        "morning_average": _average(history, "morning", period_days, newest),
        "late_morning_average": _average(history, "late_morning", period_days, newest),
        "daytime_average": _average(history, "daytime", period_days, newest),
        "daytime_average_adjusted": _average(history, "daytime_adjusted", period_days, newest),
        "nighttime_average": _average(history, "nighttime", period_days, newest),
        "car_charger_consumption": _average(history, "car_charger", period_days, newest),
    }


def calculate_evening_average(
    history: list[dict[str, Any]], period_days: int, newest: date
) -> float | None:
    """Old Node-RED context list: "Spidstimer_forbrug_liste"."""
    return _average(history, "evening", period_days, newest)


def calculate_full_day_average(
    history: list[dict[str, Any]], period_days: int, newest: date
) -> float | None:
    """Old Node-RED context list: "Døgn_forbrug_liste"."""
    return _average(history, "full_day", period_days, newest)


def build_day(
    day: date,
    series: dict[str, dict[int, float]],
    property_entity_id: str,
    charger_entity_ids: list[str],
) -> dict[str, Any] | None:
    """One day's history figures from each source's cumulative values
    (see fetch_hourly_sums()/cumulative_at_boundaries()), or None if the
    property counter is missing any boundary or fails _is_plausible_day().

    car_charger is 0.0 with no charger configured, and None if a
    configured charger has no data for 08/16 that day (or implausibly
    went down) -- daytime_adjusted is then None too, and that day is
    left out of those two averages only (see _average())."""
    timestamps = {
        hour: int(moment.timestamp()) for hour, moment in day_boundaries(day).items()
    }
    property_series = series.get(property_entity_id, {})
    values_at: dict[int, float] = {}
    for hour, ts in timestamps.items():
        if ts not in property_series:
            return None
        values_at[hour] = property_series[ts]

    values: dict[str, Any] = _derive_daily_values(values_at)
    if not _is_plausible_day(values):
        LOGGER.warning(
            "PwMngt: consumption figures for %s look implausible (%s) -- "
            "skipping that day rather than risk skewing the averages",
            day,
            values,
        )
        return None

    car_charger: float | None = 0.0
    for entity_id in charger_entity_ids:
        charger_series = series.get(entity_id, {})
        at_8 = charger_series.get(timestamps[8])
        at_16 = charger_series.get(timestamps[16])
        if at_8 is None or at_16 is None or at_16 < at_8:
            car_charger = None
            break
        car_charger += at_16 - at_8

    values["car_charger"] = round(car_charger, 2) if car_charger is not None else None
    values["daytime_adjusted"] = (
        round(values["daytime"] - car_charger, 2) if car_charger is not None else None
    )
    values["date"] = day.isoformat()
    return values


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


@dataclass
class PwMngtConsumptionDataStoredData(restore_state.ExtraStoredData):
    """What PwMngtConsumptionDataSensor restores across a restart: its 3
    rolling histories (newest first), plus which sources they were built
    from -- if that changes (a different property counter or charger
    source picked), everything is rebuilt from Recorder rather than
    mixing two different meters. Kept out of extra_state_attributes, same
    reasoning as PwMngtBalanceStoredData in data/balance_data.py."""

    daytime_history: list[dict[str, Any]]
    evening_history: list[dict[str, Any]]
    full_day_history: list[dict[str, Any]]
    source_signature: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "daytime_history": self.daytime_history,
            "evening_history": self.evening_history,
            "full_day_history": self.full_day_history,
            "source_signature": self.source_signature,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtConsumptionDataStoredData":
        return cls(
            daytime_history=list(restored.get("daytime_history") or []),
            evening_history=list(restored.get("evening_history") or []),
            full_day_history=list(restored.get("full_day_history") or []),
            source_signature=restored.get("source_signature"),
        )


class PwMngtConsumptionDataSensor(restore_state.RestoreEntity, SensorEntity):
    """The PV device's consumption_data sensor: PwM_CONSUMPTION_DATA_ATTRIBUTES
    as attributes, from 3 rolling day-histories (daytime -- which also
    carries the car-charger figures --, evening, full day) filled from
    Recorder by _async_fill_missing_days(). Averages are recomputed after
    every fill and whenever select.pwm_pv_history_period_days changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        # Attribute-only "_data" sensor: a constant state instead of None
        # so the UI doesn't show "Unknown" -- see DATA_SENSOR_STATE in
        # data/__init__.py.
        self._attr_native_value = DATA_SENSOR_STATE
        self._attr_available = False
        self._attr_extra_state_attributes = {
            key: None for key in PwM_CONSUMPTION_DATA_ATTRIBUTES
        }

        self._entry = entry
        self._daytime_history: list[dict[str, Any]] = []
        self._evening_history: list[dict[str, Any]] = []
        self._full_day_history: list[dict[str, Any]] = []
        self._source_signature: str | None = None
        self._fill_lock = asyncio.Lock()

    @property
    def extra_restore_state_data(self) -> PwMngtConsumptionDataStoredData:
        return PwMngtConsumptionDataStoredData(
            daytime_history=self._daytime_history,
            evening_history=self._evening_history,
            full_day_history=self._full_day_history,
            source_signature=self._source_signature,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the histories, start the daily fill and the
        history-period listener, and schedule a first fill shortly after
        startup (see _STARTUP_DELAY_SECONDS)."""
        await super().async_added_to_hass()

        restored = await self.async_get_last_extra_data()
        if restored is not None:
            data = PwMngtConsumptionDataStoredData.from_dict(restored.as_dict())
            self._daytime_history = data.daytime_history
            self._evening_history = data.evening_history
            self._full_day_history = data.full_day_history
            self._source_signature = data.source_signature

        self.async_on_remove(
            event.async_track_time_change(
                self.hass,
                self._handle_daily_run,
                hour=_DAILY_RUN_HOUR,
                minute=_DAILY_RUN_MINUTE,
                second=0,
            )
        )

        history_period_entity_id = pv_properties.history_period_days_entity_id(
            self.hass, self._entry
        )
        if history_period_entity_id:
            self.async_on_remove(
                event.async_track_state_change_event(
                    self.hass,
                    [history_period_entity_id],
                    self._handle_history_period_changed,
                )
            )

        if self.hass.is_running:
            self.async_on_remove(
                event.async_call_later(
                    self.hass, _STARTUP_DELAY_SECONDS, self._handle_daily_run
                )
            )
        else:
            # Not wrapped in async_on_remove(): a listen_once listener
            # removes itself when it fires, and removing it again later
            # would log an error.
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._handle_daily_run
            )

        self._recompute()
        self._attr_available = True
        self.async_write_ha_state()

    @callback
    def _handle_history_period_changed(self, _event) -> None:
        """Recompute over the new period; the histories are untouched --
        same as Node-RED's msg.topic == "Historik" branch."""
        self._recompute()
        self.async_write_ha_state()

    @callback
    def _handle_daily_run(self, _now_or_event=None) -> None:
        self.hass.async_create_task(self._async_fill_missing_days())

    def _property_entity_id(self) -> str | None:
        return self._entry.options.get(CONF_ENTITY_MAP, {}).get(
            ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL
        ) or None

    def _charger_entity_ids(self) -> list[str]:
        """Each installed charger's consumption_source_entity (text.py),
        skipping a charger whose type is "Not installed" or whose field is
        empty. Looked up via the entity registry, never by entity_id
        string."""
        registry = er.async_get(self.hass)
        entity_ids: list[str] = []
        for charger in CHARGERS:
            prefix = f"{self._entry.entry_id}_{charger['id']}"
            type_entity_id = registry.async_get_entity_id("select", DOMAIN, f"{prefix}_type")
            type_state = self.hass.states.get(type_entity_id) if type_entity_id else None
            if type_state is not None and type_state.state == CHARGER_TYPE_NOT_INSTALLED:
                continue
            text_entity_id = registry.async_get_entity_id(
                "text", DOMAIN, f"{prefix}_consumption_source_entity"
            )
            text_state = self.hass.states.get(text_entity_id) if text_entity_id else None
            if text_state is None or text_state.state in ("", "unknown", "unavailable"):
                continue
            entity_ids.append(text_state.state)
        return entity_ids

    async def _async_fill_missing_days(self) -> None:
        """Add every day of the last _MAX_HISTORY_DAYS (ending yesterday)
        that isn't in the histories yet, read from Recorder, then
        recompute. Rebuilds from scratch if the configured sources
        changed since the histories were built."""
        async with self._fill_lock:
            property_entity_id = self._property_entity_id()
            if not property_entity_id:
                LOGGER.warning(
                    "PwMngt: no property consumption counter configured -- "
                    "consumption_data has nothing to read"
                )
                return
            charger_entity_ids = self._charger_entity_ids()

            signature = "|".join([property_entity_id, *sorted(charger_entity_ids)])
            if signature != self._source_signature:
                if self._source_signature is not None:
                    LOGGER.info(
                        "PwMngt: consumption sources changed (%s -> %s) -- "
                        "rebuilding consumption_data's histories from Recorder",
                        self._source_signature,
                        signature,
                    )
                self._daytime_history = []
                self._evening_history = []
                self._full_day_history = []
                self._source_signature = signature

            yesterday = dt_util.now().date() - timedelta(days=1)
            oldest = yesterday - timedelta(days=_MAX_HISTORY_DAYS - 1)
            have = {entry["date"] for entry in self._full_day_history}
            missing = [
                oldest + timedelta(days=offset)
                for offset in range(_MAX_HISTORY_DAYS)
                if (oldest + timedelta(days=offset)).isoformat() not in have
            ]

            if missing:
                series = await self._async_fetch_series(
                    [property_entity_id, *charger_entity_ids], missing
                )
                added = 0
                for day in missing:
                    values = build_day(day, series, property_entity_id, charger_entity_ids)
                    if values is None:
                        continue
                    self._add_day(values)
                    added += 1
                if added:
                    LOGGER.info(
                        "PwMngt: consumption_data added %d day(s) from Recorder",
                        added,
                    )

            self._trim_histories(oldest)
            self._recompute()
            self.async_write_ha_state()

    async def _async_fetch_series(
        self, entity_ids: list[str], days: list[date]
    ) -> dict[str, dict[int, float]]:
        """Cumulative values at every boundary of every day in days, per
        entity: long-term statistics first, state history for any entity
        that has no statistics in the range (see module docstring)."""
        start = day_boundaries(days[0])[0] - timedelta(hours=1)
        end = day_boundaries(days[-1])[24]
        series = await fetch_hourly_sums(self.hass, entity_ids, start, end)

        boundary_timestamps = [
            int(moment.timestamp())
            for day in days
            for moment in day_boundaries(day).values()
        ]
        for entity_id in entity_ids:
            if entity_id in series:
                continue
            changes = await fetch_state_changes_since(
                self.hass, entity_id, dt_util.as_utc(start), dt_util.as_utc(end)
            )
            if changes:
                LOGGER.debug(
                    "PwMngt: no long-term statistics for %s, using state history",
                    entity_id,
                )
                series[entity_id] = cumulative_at_boundaries(changes, boundary_timestamps)
        return series

    def _add_day(self, values: dict[str, Any]) -> None:
        """Split one build_day() result across the 3 histories."""
        day = values["date"]
        self._daytime_history.append(
            {
                "date": day,
                "early_morning": values["early_morning"],
                "morning": values["morning"],
                "late_morning": values["late_morning"],
                "daytime": values["daytime"],
                "daytime_adjusted": values["daytime_adjusted"],
                "nighttime": values["nighttime"],
                "car_charger": values["car_charger"],
            }
        )
        self._evening_history.append({"date": day, "evening": values["evening"]})
        self._full_day_history.append({"date": day, "full_day": values["full_day"]})

    def _trim_histories(self, oldest: date) -> None:
        """Newest first, and nothing older than oldest."""
        cutoff = oldest.isoformat()
        for name in ("_daytime_history", "_evening_history", "_full_day_history"):
            history = [entry for entry in getattr(self, name) if entry["date"] >= cutoff]
            history.sort(key=lambda entry: entry["date"], reverse=True)
            setattr(self, name, history)

    def _recompute(self) -> None:
        """Recalculate every average over the currently selected period.
        Pool heating, PV surplus and heat pump stay None until those
        segments exist."""
        period_days = pv_properties.history_period_days(self.hass, self._entry)
        newest = dt_util.now().date() - timedelta(days=1)
        attributes = self._attr_extra_state_attributes
        attributes.update(
            calculate_daytime_averages(self._daytime_history, period_days, newest)
        )
        attributes["evening_average"] = calculate_evening_average(
            self._evening_history, period_days, newest
        )
        attributes["full_day_average"] = calculate_full_day_average(
            self._full_day_history, period_days, newest
        )
        attributes["history_days"] = len(self._full_day_history)
        attributes["last_day"] = (
            self._full_day_history[0]["date"] if self._full_day_history else None
        )
