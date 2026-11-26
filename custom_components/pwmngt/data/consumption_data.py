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
Recorder's hourly long-term statistics ("sum" -- for the property
counter its end-of-hour "state" where "sum" took an outage for a meter
reset, issue #130), kept indefinitely, so all
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

Deliberate differences from Node-RED (Claus, 2026-11-03):
- Every average except car_charger_consumption is a trimmed mean -- the
  highest and lowest days are dropped first, so one odd day can't skew
  it (see _average()).
- nighttime_average_adjusted: the night (17-24 + 00-06) without the car
  chargers' consumption, like daytime_average_adjusted for 08-16.
  Node-RED only corrected the daytime, so a night car charge raised
  Battery nightly target -- but the battery is never meant to cover a
  car charge.
- late_evening_average (issue #58, Claus 2026-11-09): 21-24 on its own,
  with and without the chargers. The night charge (data/
  battery_charge_decision.py) spread the night evenly over 21-06, which
  underestimated 21-24 by ~30 % where the house uses more before
  midnight than after (Kasper: ~0.64 vs ~0.35 kWh an hour).
- day_hourly_average_adjusted (issue #59, Claus 2026-11-09): each hour
  06-17 on its own, without the chargers -- the night charge can then
  cover the expensive daytime hours too (data/battery_charge_decision.py).
- The inverter's own use (issue #111, Claus 2026-11-21; issue #129,
  2026-11-25 for every brand and the single daytime hours): the house
  counter doesn't count what the inverter itself uses -- neither
  Growatt's "Load Energy Total" nor Kostal's "Home Consumption Total"
  (Rene: exactly "Home Power"). So the evening/night figures (17-24 +
  00-06) and each hour of day_hourly_average_adjusted get it added, and
  the plans use what the battery really delivers: on Claus's Growatt
  ~12 % on top of the house while discharging, on Rene's Kostal ~6 %.
  Hours with a grid charge are left out -- that loss is paid by the grid,
  not the battery. The daytime totals (08-16) stay as measured: they are
  set against Solcast's forecast for the PV surplus.
- battery_factor (issue #129): kWh of SoC the battery loses per kWh of
  the above (house + own use - grid) in the hours only the battery
  covered the house, over the last _FACTOR_DAYS days -- the measured
  replacement of the fixed 7 % (TARGET_MARGIN), see
  pv_properties.battery_factor(). Measured against the same counters the
  plans use, so a house counter that does count the inverter after all
  comes out below 1 instead of being counted twice. Only the hours after
  the last change of the battery size are measured (a replaced or
  extended battery), and the old factor is dropped until they are enough.
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EntityCategory
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import event, restore_state
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from ..const import (
    BATTERY_FACTOR_MAX,
    BATTERY_FACTOR_MIN,
    CONF_ENTITY_MAP,
    DOMAIN,
    ENTITY_KEY_BATTERY_GRID_CHARGE_HOURLY,
    ENTITY_KEY_BATTERY_GRID_CHARGED_DAY,
    ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL,
)
from ..devices import CHARGERS, pv_device_info
from ..helpers.history_helper import fetch_state_changes_since
from ..helpers.state_helper import read_float_state
from ..helpers.statistics_helper import fetch_hourly_means, fetch_hourly_sums
from ..properties import charger_properties, pv_properties
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
    # The same night, minus the chargers' own consumption in it. DIFF
    # (Claus, 2026-11-03): Node-RED only corrected the daytime -- but the
    # battery is never meant to cover a car charge, so Battery nightly
    # target (data/solar_data.py) reads this one.
    "nighttime_average_adjusted",
    # 21-24, as measured and without the chargers -- issue #58, read by
    # data/battery_charge_decision.py for tomorrow's hours 21-23.
    "late_evening_average",
    "late_evening_average_adjusted",
    # Issue #59: a list, one average per hour CONSUMPTION_DAY_HOURS (06,
    # 07 ... 16), without the chargers -- read by
    # data/battery_charge_decision.py for tomorrow's daytime hours.
    "day_hourly_average_adjusted",
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
    # Issue #111: the inverter's own use (kWh) in the night hours 17-24 +
    # 00-06 without grid-charge hours -- already part of the evening/night
    # averages above. None without an own-use sensor.
    "inverter_own_use_night_average",
    # Issue #129: kWh of SoC per kWh the house takes from the battery
    # (see the module docstring), None until enough is measured, and the
    # number of hours it is measured over.
    "battery_factor",
    "battery_factor_hours",
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
_BOUNDARY_HOURS: tuple[int, ...] = (0, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 21, 24)

# Issue #59: the hours day_hourly_average_adjusted has one value for (the
# hour starting at each).
CONSUMPTION_DAY_HOURS = range(6, 17)

# How many calendar days back the histories reach, no matter what
# select.pwm_pv_history_period_days is set to -- same "30" Node-RED capped
# each of its context lists at.
_MAX_HISTORY_DAYS = 30

# A single day's real household consumption above this is implausible --
# see _is_plausible_day(). Kasper's own call on where to draw the line.
_MAX_PLAUSIBLE_FULL_DAY_KWH = 250

# Part of the source signature, so a change in what each history entry
# holds rebuilds the histories from Recorder once. v2: nighttime_adjusted,
# v3: late_evening (issue #58), v4: the day's single hours (issue #59),
# v5: the inverter's own use in the evening/night figures (issue #111),
# v6: ... for every brand and in the single daytime hours (issue #129),
# v7: the grid-charge and own-use counters from their end-of-hour state,
# so an inverter restart's dip to 0 no longer adds their whole value
# (issue #131).
_HISTORY_FORMAT = "v7"

# Trimmed averages (see _average()): drop this share of the days at each
# end -- one day at 7 days of history, two at 14, three at 21-30 -- but
# only with at least _TRIM_MIN_VALUES days, else a plain mean.
_TRIM_SHARE = 0.10
_TRIM_MIN_VALUES = 5

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
        "late_evening": round(v[24] - v[21], 2),
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


def _trimmed(values: list[float]) -> list[float]:
    """values without the highest and lowest _TRIM_SHARE of them (at
    least one at each end) -- see _average(). Unchanged below
    _TRIM_MIN_VALUES values, where too little would be left."""
    if len(values) < _TRIM_MIN_VALUES:
        return values
    cut = math.ceil(len(values) * _TRIM_SHARE)
    return sorted(values)[cut:-cut]


def _average(
    history: list[dict[str, Any]],
    field_name: str,
    period_days: int,
    newest: date,
    trim: bool = True,
) -> float | None:
    """Mean of one field over the entries dated within the last
    period_days calendar days up to newest (yesterday) -- a missing day
    just shrinks the divisor, same "use what's there" rule Node-RED had.
    Entries whose field is None (e.g. car_charger for a day a charger
    source had no data) are left out. None if nothing's left.

    DIFF (Claus, 2026-11-03): a trimmed mean -- the highest and lowest
    days are dropped first (see _trimmed()). One odd day that stays under
    _MAX_PLAUSIBLE_FULL_DAY_KWH otherwise skews the average for the whole
    period: on 2026-11-02 a 44 kWh car charge at 00-04 lifted
    nighttime_average from ~29 to 38 kWh (nighttime_average_adjusted now
    takes the chargers out, but a counter glitch would do the same). A
    real change (a cold spell) still moves the trimmed mean from
    its second day on. trim=False for a value that is spiky by nature
    (car_charger: mostly 0, a few charging days), where dropping the
    highest day would hide real consumption."""
    cutoff = newest - timedelta(days=max(1, period_days) - 1)
    values = [
        entry[field_name]
        for entry in history
        if entry.get(field_name) is not None
        and date.fromisoformat(entry["date"]) >= cutoff
    ]
    if trim:
        values = _trimmed(values)
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def calculate_daytime_averages(
    history: list[dict[str, Any]], period_days: int, newest: date
) -> dict[str, float | None]:
    """The averages the daytime history supplies. Old Node-RED context
    list: "Dagtime_forbrug_liste"."""
    return {
        "early_morning_average": _average(history, "early_morning", period_days, newest),
        "morning_average": _average(history, "morning", period_days, newest),
        "late_morning_average": _average(history, "late_morning", period_days, newest),
        "daytime_average": _average(history, "daytime", period_days, newest),
        "daytime_average_adjusted": _average(history, "daytime_adjusted", period_days, newest),
        "nighttime_average": _average(history, "nighttime", period_days, newest),
        "nighttime_average_adjusted": _average(
            history, "nighttime_adjusted", period_days, newest
        ),
        "late_evening_average": _average(history, "late_evening", period_days, newest),
        "late_evening_average_adjusted": _average(
            history, "late_evening_adjusted", period_days, newest
        ),
        "day_hourly_average_adjusted": [
            _average(history, f"hour_{hour}_adjusted", period_days, newest)
            for hour in CONSUMPTION_DAY_HOURS
        ],
        "car_charger_consumption": _average(
            history, "car_charger", period_days, newest, trim=False
        ),
        "inverter_own_use_night_average": _average(
            history, "inverter_night", period_days, newest
        ),
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


def _charger_consumption(
    series: dict[str, dict[int, float]],
    charger_entity_ids: list[str],
    timestamps: dict[int, int],
    periods: tuple[tuple[int, int], ...],
) -> float | None:
    """Every configured charger's consumption summed over periods (pairs
    of _BOUNDARY_HOURS hours). 0.0 with no charger configured; None if a
    charger has no value at a boundary or implausibly went down."""
    total = 0.0
    for entity_id in charger_entity_ids:
        charger_series = series.get(entity_id, {})
        for start, end in periods:
            at_start = charger_series.get(timestamps[start])
            at_end = charger_series.get(timestamps[end])
            if at_start is None or at_end is None or at_end < at_start:
                return None
            total += at_end - at_start
    return total


# Issue #94: a property counter that counts the battery's charging too
# turns a night with a grid charge into a night with a high consumption --
# the nightly target goes up, the next night charges more, and so on.
# Tested on Claus's, Kasper's and Rene's nights 27/10-17/11 (night use
# without the chargers against the grid charge): their house counters
# gave slope 0.12-0.46 / r 0.12-0.34, their grid-import counters (which
# do count the charge) 1.15-1.41 / r 0.70-0.84.
ISSUE_CONSUMPTION_COUNTS_BATTERY = "consumption_counts_battery"
_BATTERY_IN_CONSUMPTION_MIN_NIGHTS = 10
_BATTERY_IN_CONSUMPTION_MIN_EACH = 3  # nights with and without a charge
_CHARGE_NIGHT_KWH = 2.0
_NO_CHARGE_NIGHT_KWH = 1.0
_BATTERY_IN_CONSUMPTION_MIN_SLOPE = 0.7
_BATTERY_IN_CONSUMPTION_MIN_R = 0.6


def consumption_includes_battery_charge(nights: list[tuple[float, float]]) -> bool | None:
    """Issue #94: whether a night's consumption (second value, kWh,
    without the chargers) rises with that night's grid charge of the
    battery (first value, kWh) as if the charge were part of it -- the
    regression slope and correlation over the nights. None while there
    aren't enough nights with and without a charge to tell."""
    if len(nights) < _BATTERY_IN_CONSUMPTION_MIN_NIGHTS:
        return None
    charged = sum(1 for charge, _ in nights if charge >= _CHARGE_NIGHT_KWH)
    uncharged = sum(1 for charge, _ in nights if charge < _NO_CHARGE_NIGHT_KWH)
    if charged < _BATTERY_IN_CONSUMPTION_MIN_EACH or uncharged < _BATTERY_IN_CONSUMPTION_MIN_EACH:
        return None
    count = len(nights)
    mean_x = sum(charge for charge, _ in nights) / count
    mean_y = sum(use for _, use in nights) / count
    sxy = sum((charge - mean_x) * (use - mean_y) for charge, use in nights)
    sxx = sum((charge - mean_x) ** 2 for charge, _ in nights)
    syy = sum((use - mean_y) ** 2 for _, use in nights)
    if sxx <= 0 or syy <= 0:
        return False
    slope = sxy / sxx
    correlation = sxy / math.sqrt(sxx * syy)
    return slope >= _BATTERY_IN_CONSUMPTION_MIN_SLOPE and correlation >= _BATTERY_IN_CONSUMPTION_MIN_R


def night_grid_charge(
    day: date, series: dict[str, dict[int, float]], entity_id: str
) -> float | None:
    """Issue #94: the battery's grid charge (kWh) in the same night hours
    as nighttime (17-24 + 00-06 of day)."""
    timestamps = {
        hour: int(moment.timestamp()) for hour, moment in day_boundaries(day).items()
    }
    return _charger_consumption(series, [entity_id], timestamps, ((17, 24), (0, 6)))


# Issue #111: the hours (each the hour starting at it) the inverter's own
# use is added to, per figure -- the evening/night ones the battery covers.
_INVERTER_EVENING_HOURS: tuple[int, ...] = (17, 18, 19, 20)
_INVERTER_LATE_EVENING_HOURS: tuple[int, ...] = (21, 22, 23)
_INVERTER_NIGHT_HOURS: tuple[int, ...] = (
    *_INVERTER_EVENING_HOURS, *_INVERTER_LATE_EVENING_HOURS, 0, 1, 2, 3, 4, 5
)
# An hour whose grid charge reaches this (kWh) is a charge hour.
_GRID_CHARGE_HOUR_KWH = 0.1

# Issue #129: battery_factor is measured over this many days back (the
# SoC's state history reaches Recorder's purge_keep_days, 10 by default),
# in hours the battery discharged at least _FACTOR_MIN_BATTERY_KW while
# PV gave at most _FACTOR_MAX_PV_KW and the battery wasn't grid charged.
# It needs at least _FACTOR_MIN_HOURS of them, holding a battery's full
# size or more in all -- a few hours of a whole-percent SoC are too
# coarse.
_FACTOR_DAYS = 10
_FACTOR_MIN_BATTERY_KW = 0.3
_FACTOR_MAX_PV_KW = 0.05
_FACTOR_MIN_HOURS = 12


@dataclass
class FactorHour:
    """Issue #129: one hour for calculate_battery_factor() -- energies in
    kWh, powers as the hour's mean in kW."""

    soc_drop: float  # SoC points at the hour's start minus its end
    consumption: float  # the house counter's change
    own_use: float  # the inverter's own use
    grid: float  # bought from the grid
    battery_kw: float  # positive = discharging
    pv_kw: float
    grid_charge: float  # the battery's grid-charge counter's change


def calculate_battery_factor(
    hours: list[FactorHour], battery_size: float
) -> tuple[float, int] | None:
    """Issue #129: (battery_factor, hours measured) -- the SoC energy the
    battery lost over what the house took from it (house counter + own
    use - grid) in the hours only the battery covered the house. None
    while too little is measured or the result is outside
    BATTERY_FACTOR_MIN/MAX (a measuring error rather than the battery)."""
    if battery_size <= 0:
        return None
    used = [
        hour
        for hour in hours
        if hour.battery_kw >= _FACTOR_MIN_BATTERY_KW
        and hour.pv_kw <= _FACTOR_MAX_PV_KW
        and hour.grid_charge < _GRID_CHARGE_HOUR_KWH
    ]
    soc_kwh = sum(hour.soc_drop for hour in used) / 100 * battery_size
    house_kwh = sum(hour.consumption + hour.own_use - max(hour.grid, 0.0) for hour in used)
    if len(used) < _FACTOR_MIN_HOURS or house_kwh < battery_size or soc_kwh <= 0:
        return None
    factor = round(soc_kwh / house_kwh, 3)
    if not BATTERY_FACTOR_MIN <= factor <= BATTERY_FACTOR_MAX:
        LOGGER.warning(
            "PwMngt: measured battery_factor %s is outside %s-%s -- not used",
            factor,
            BATTERY_FACTOR_MIN,
            BATTERY_FACTOR_MAX,
        )
        return None
    return factor, len(used)


def soc_at_hours(changes: list[State], hour_ends: list[int]) -> dict[int, float]:
    """Issue #129: the SoC (%) as of each timestamp -- the last valid
    reading before it, like an hourly statistics row ending there."""
    result: dict[int, float] = {}
    points = sorted(set(hour_ends) | {hour_end - 3600 for hour_end in hour_ends})
    index = 0
    last: float | None = None
    for state in changes:
        changed_at = state.last_changed.timestamp()
        while index < len(points) and points[index] <= changed_at:
            if last is not None:
                result[points[index]] = last
            index += 1
        value = read_float_state(state)
        if value is not None:
            last = value
    if last is not None:
        for point in points[index:]:
            result[point] = last
    return result


def last_value_change(changes: list[State] | None) -> datetime | None:
    """Issue #129: when a numeric state last changed to a new value, or
    None if it kept one value. unknown/unavailable (a restart, a reload)
    are skipped, so only a real change counts -- e.g. a new battery size
    after the battery was replaced or extended."""
    changed_at: datetime | None = None
    previous: float | None = None
    for state in changes or []:
        value = read_float_state(state)
        if value is None:
            continue
        if previous is not None and value != previous:
            changed_at = state.last_changed
        previous = value
    return changed_at


def _difference(series: dict[int, float] | None, start: int, end: int) -> float | None:
    """A cumulative series' change from start to end, or None."""
    if not series or start not in series or end not in series:
        return None
    return series[end] - series[start]


def inverter_own_use(
    day: date,
    series: dict[str, dict[int, float]],
    own_entity_id: str,
    grid_charge_entity_id: str | None,
    hours: tuple[int, ...],
) -> float | None:
    """Issue #111: kWh the inverter used itself in the given hours of day,
    without the hours the battery charged from the grid (that loss is paid
    by the grid, not the battery) or the grid-charge counter has no value
    for. None if the own-use counter has no value for any of the hours --
    e.g. days before it existed (issue #77)."""
    tz = dt_util.get_default_time_zone()
    own = series.get(own_entity_id, {})
    charge = series.get(grid_charge_entity_id, {}) if grid_charge_entity_id else None
    total = 0.0
    found = False
    for hour in hours:
        start = int(datetime.combine(day, time(hour), tzinfo=tz).timestamp())
        end = start + 3600
        at_start, at_end = own.get(start), own.get(end)
        if at_start is None or at_end is None or at_end < at_start:
            continue
        found = True
        if charge is not None:
            charge_start, charge_end = charge.get(start), charge.get(end)
            if (
                charge_start is None
                or charge_end is None
                or charge_end - charge_start >= _GRID_CHARGE_HOUR_KWH
            ):
                continue
        total += at_end - at_start
    return round(total, 2) if found else None


def build_day(
    day: date,
    series: dict[str, dict[int, float]],
    property_entity_id: str,
    charger_entity_ids: list[str],
    own_entity_id: str | None = None,
    grid_charge_entity_id: str | None = None,
) -> dict[str, Any] | None:
    """One day's history figures from each source's cumulative values
    (see fetch_hourly_sums()/cumulative_at_boundaries()), or None if the
    property counter is missing any boundary or fails _is_plausible_day().

    car_charger is 0.0 with no charger configured, and None if a
    configured charger has no data for 08/16 that day (or implausibly
    went down) -- daytime_adjusted is then None too, and that day is
    left out of those two averages only (see _average()). The same goes
    for nighttime_adjusted with the chargers' 17-24 and 00-06 values.

    own_entity_id (issues #111/#129): the inverter's own use, added to
    evening, late_evening, nighttime and each daytime hour (see
    inverter_own_use()) -- None without an own-use sensor."""
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

    values["inverter_night"] = None
    if own_entity_id:
        for name, hours in (
            ("evening", _INVERTER_EVENING_HOURS),
            ("late_evening", _INVERTER_LATE_EVENING_HOURS),
            ("nighttime", _INVERTER_NIGHT_HOURS),
        ):
            own_use = inverter_own_use(day, series, own_entity_id, grid_charge_entity_id, hours)
            if own_use is not None:
                values[name] = round(values[name] + own_use, 2)
            if name == "nighttime":
                values["inverter_night"] = own_use

    car_charger = _charger_consumption(series, charger_entity_ids, timestamps, ((8, 16),))
    car_charger_night = _charger_consumption(
        series, charger_entity_ids, timestamps, ((17, 24), (0, 6))
    )
    car_charger_late_evening = _charger_consumption(
        series, charger_entity_ids, timestamps, ((21, 24),)
    )

    values["car_charger"] = round(car_charger, 2) if car_charger is not None else None
    values["daytime_adjusted"] = (
        round(values["daytime"] - car_charger, 2) if car_charger is not None else None
    )
    values["nighttime_adjusted"] = (
        round(max(values["nighttime"] - car_charger_night, 0.0), 2)
        if car_charger_night is not None
        else None
    )
    values["late_evening_adjusted"] = (
        round(max(values["late_evening"] - car_charger_late_evening, 0.0), 2)
        if car_charger_late_evening is not None
        else None
    )
    # Issue #59: each daytime hour without the chargers -- issue #129:
    # with the inverter's own use.
    for hour in CONSUMPTION_DAY_HOURS:
        chargers = _charger_consumption(
            series, charger_entity_ids, timestamps, ((hour, hour + 1),)
        )
        own_use = (
            inverter_own_use(day, series, own_entity_id, grid_charge_entity_id, (hour,))
            if own_entity_id
            else None
        )
        values[f"hour_{hour}_adjusted"] = (
            round(
                max(values_at[hour + 1] - values_at[hour] - chargers, 0.0) + (own_use or 0.0), 2
            )
            if chargers is not None
            else None
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
    # Issue #129: the last measured battery_factor and its hours, so the
    # plans keep it while the first measurement after a restart runs.
    battery_factor: float | None = None
    battery_factor_hours: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "daytime_history": self.daytime_history,
            "evening_history": self.evening_history,
            "full_day_history": self.full_day_history,
            "source_signature": self.source_signature,
            "battery_factor": self.battery_factor,
            "battery_factor_hours": self.battery_factor_hours,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtConsumptionDataStoredData":
        return cls(
            daytime_history=list(restored.get("daytime_history") or []),
            evening_history=list(restored.get("evening_history") or []),
            full_day_history=list(restored.get("full_day_history") or []),
            source_signature=restored.get("source_signature"),
            battery_factor=restored.get("battery_factor"),
            battery_factor_hours=restored.get("battery_factor_hours"),
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
            battery_factor=self._attr_extra_state_attributes.get("battery_factor"),
            battery_factor_hours=self._attr_extra_state_attributes.get("battery_factor_hours"),
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
            self._attr_extra_state_attributes["battery_factor"] = data.battery_factor
            self._attr_extra_state_attributes["battery_factor_hours"] = data.battery_factor_hours

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

    def _grid_charge_entity_id(self) -> str | None:
        """The battery's grid-charge counter (hourly, else daily)."""
        entity_map = self._entry.options.get(CONF_ENTITY_MAP, {})
        return entity_map.get(ENTITY_KEY_BATTERY_GRID_CHARGE_HOURLY) or entity_map.get(
            ENTITY_KEY_BATTERY_GRID_CHARGED_DAY
        ) or None

    def _lifetime_ids(self, property_entity_id: str | None = None) -> frozenset[str]:
        """Issues #130/#131: the counters that never reset -- the property
        counter and the "total" grid-charge counter -- see
        statistics_helper.counter_values()."""
        entity_map = self._entry.options.get(CONF_ENTITY_MAP, {})
        return frozenset(
            entity_id
            for entity_id in (
                property_entity_id,
                entity_map.get(ENTITY_KEY_BATTERY_GRID_CHARGE_HOURLY),
            )
            if entity_id
        )

    def _daily_ids(self) -> frozenset[str]:
        """Issue #131: the counters that reset once a day -- the daily
        grid-charge counter and the inverter's own use today -- see
        statistics_helper.daily_counter_values()."""
        entity_map = self._entry.options.get(CONF_ENTITY_MAP, {})
        return frozenset(
            entity_id
            for entity_id in (
                entity_map.get(ENTITY_KEY_BATTERY_GRID_CHARGED_DAY),
                self._inverter_own_use_entity_id(),
            )
            if entity_id
        )

    def _inverter_own_use_entity_id(self) -> str | None:
        """Issues #111/#129: the inverter's own use today -- for every
        brand, see the module docstring."""
        return pv_properties.inverter_own_consumption_today_entity_id(self.hass, self._entry)

    def _charger_entity_ids(self) -> list[str]:
        """Each installed charger's consumption source -- see
        charger_properties.consumption_source_entity_ids()."""
        return charger_properties.consumption_source_entity_ids(self.hass, self._entry)

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
            own_entity_id = self._inverter_own_use_entity_id()
            grid_charge_entity_id = self._grid_charge_entity_id() if own_entity_id else None
            own_sources = [
                entity_id for entity_id in (own_entity_id, grid_charge_entity_id) if entity_id
            ]

            signature = "|".join(
                [_HISTORY_FORMAT, property_entity_id, *sorted(charger_entity_ids), *own_sources]
            )
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
                # Issue #130: the property counter is a lifetime total --
                # a brief drop to 0 must not count as a reset.
                series = await self._async_fetch_series(
                    [property_entity_id, *charger_entity_ids, *own_sources],
                    missing,
                    frozenset({property_entity_id}),
                )
                added = 0
                for day in missing:
                    values = build_day(
                        day,
                        series,
                        property_entity_id,
                        charger_entity_ids,
                        own_entity_id,
                        grid_charge_entity_id,
                    )
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
            await self._async_measure_battery_factor(
                property_entity_id, own_entity_id, grid_charge_entity_id
            )
            self.async_write_ha_state()
            await self._async_check_battery_in_consumption(property_entity_id)

    async def _async_measure_battery_factor(
        self,
        property_entity_id: str,
        own_entity_id: str | None,
        grid_charge_entity_id: str | None,
    ) -> None:
        """Issue #129: measure battery_factor (see the module docstring).
        Keeps the last value when this run can't measure one."""
        soc_id = pv_properties.battery_soc_entity_id(self.hass, self._entry)
        battery_id = pv_properties.battery_power_entity_id(self.hass, self._entry)
        pv_id = pv_properties.pv_total_entity_id(self.hass, self._entry)
        grid_id = pv_properties.grid_power_entity_id(self.hass, self._entry)
        size_id = pv_properties.battery_size_entity_id(self.hass, self._entry)
        battery_size = read_float_state(self.hass.states.get(size_id) if size_id else None)
        if not (soc_id and battery_id and pv_id and grid_id and battery_size):
            return
        end = dt_util.now().replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=_FACTOR_DAYS)
        # A new battery size (the battery replaced or extended) makes the
        # hours before it count each SoC point with the wrong kWh -- only
        # the hours after it are measured, and until they are enough the
        # plans fall back to DEFAULT_BATTERY_FACTOR instead of keeping a
        # factor measured on the old battery.
        size_changed = last_value_change(
            await fetch_state_changes_since(
                self.hass, size_id, dt_util.as_utc(start), dt_util.as_utc(end)
            )
        )
        if size_changed is not None:
            start = max(
                start,
                dt_util.as_local(size_changed).replace(minute=0, second=0, microsecond=0)
                + timedelta(hours=1),
            )
        counters = [
            entity_id
            for entity_id in (property_entity_id, own_entity_id, grid_charge_entity_id)
            if entity_id
        ]
        sums = await fetch_hourly_sums(
            self.hass,
            counters,
            start,
            end,
            self._lifetime_ids(property_entity_id),
            self._daily_ids(),
        )
        means = await fetch_hourly_means(self.hass, [battery_id, pv_id, grid_id], start, end)
        soc_changes = await fetch_state_changes_since(
            self.hass, soc_id, dt_util.as_utc(start), dt_util.as_utc(end)
        )
        consumption = sums.get(property_entity_id)
        if not soc_changes or not consumption:
            if size_changed is not None:
                self._drop_battery_factor(size_changed)
            return
        soc = soc_at_hours(soc_changes, list(consumption))
        samples: list[FactorHour] = []
        for hour_end in consumption:
            hour_start = hour_end - 3600
            used = _difference(consumption, hour_start, hour_end)
            own = _difference(sums.get(own_entity_id), hour_start, hour_end) if own_entity_id else 0.0
            charged = (
                _difference(sums.get(grid_charge_entity_id), hour_start, hour_end)
                if grid_charge_entity_id
                else 0.0
            )
            battery_w = means.get(battery_id, {}).get(hour_end)
            pv_w = means.get(pv_id, {}).get(hour_end)
            grid_w = means.get(grid_id, {}).get(hour_end)
            soc_start, soc_end = soc.get(hour_start), soc.get(hour_end)
            if None in (used, own, charged, battery_w, pv_w, grid_w, soc_start, soc_end):
                continue
            samples.append(
                FactorHour(
                    soc_drop=soc_start - soc_end,
                    consumption=used,
                    own_use=own,
                    grid=grid_w / 1000,
                    battery_kw=battery_w / 1000,
                    pv_kw=pv_w / 1000,
                    grid_charge=charged,
                )
            )
        result = calculate_battery_factor(samples, battery_size)
        if result is None:
            if size_changed is not None:
                self._drop_battery_factor(size_changed)
            return
        factor, hours = result
        attributes = self._attr_extra_state_attributes
        if attributes.get("battery_factor") != factor:
            LOGGER.info(
                "PwMngt: battery_factor %s measured over %d hours (was %s)",
                factor,
                hours,
                attributes.get("battery_factor"),
            )
        attributes["battery_factor"] = factor
        attributes["battery_factor_hours"] = hours

    def _drop_battery_factor(self, size_changed: datetime) -> None:
        """Issue #129: forget a battery_factor measured before the battery
        size changed, while the hours after it are too few to measure a
        new one -- the plans use DEFAULT_BATTERY_FACTOR meanwhile."""
        attributes = self._attr_extra_state_attributes
        if attributes.get("battery_factor") is None:
            return
        LOGGER.info(
            "PwMngt: battery_factor %s dropped -- the battery size changed %s, "
            "too few hours measured since",
            attributes.get("battery_factor"),
            dt_util.as_local(size_changed).isoformat(timespec="minutes"),
        )
        attributes["battery_factor"] = None
        attributes["battery_factor_hours"] = None

    async def _async_check_battery_in_consumption(self, property_entity_id: str) -> None:
        """Issue #94: a Repair while the property counter looks like it
        counts the battery's grid charge too (see
        consumption_includes_battery_charge()); cleared once it doesn't.
        Left as it is while there's too little to tell."""
        grid_charge_entity_id = self._grid_charge_entity_id()
        if not grid_charge_entity_id:
            return
        night_use = {
            entry["date"]: (
                entry["nighttime_adjusted"]
                if entry.get("nighttime_adjusted") is not None
                else entry.get("nighttime")
            )
            for entry in self._daytime_history
        }
        days = sorted(date.fromisoformat(day) for day, use in night_use.items() if use is not None)
        if not days:
            return
        series = await self._async_fetch_series([grid_charge_entity_id], days)
        nights = []
        for day in days:
            charge = night_grid_charge(day, series, grid_charge_entity_id)
            if charge is not None:
                nights.append((charge, night_use[day.isoformat()]))
        result = consumption_includes_battery_charge(nights)
        if result is None:
            return
        if result:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_CONSUMPTION_COUNTS_BATTERY,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_CONSUMPTION_COUNTS_BATTERY,
                translation_placeholders={"entity_id": property_entity_id},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_CONSUMPTION_COUNTS_BATTERY)

    async def _async_fetch_series(
        self,
        entity_ids: list[str],
        days: list[date],
        lifetime_ids: frozenset[str] = frozenset(),
    ) -> dict[str, dict[int, float]]:
        """Cumulative values at every boundary of every day in days, per
        entity: long-term statistics first, state history for any entity
        that has no statistics in the range (see module docstring).
        lifetime_ids: see fetch_hourly_sums() (issue #130); the grid-charge
        counters and the own use are added here (issue #131)."""
        start = day_boundaries(days[0])[0] - timedelta(hours=1)
        end = day_boundaries(days[-1])[24]
        series = await fetch_hourly_sums(
            self.hass,
            entity_ids,
            start,
            end,
            lifetime_ids | self._lifetime_ids(),
            self._daily_ids(),
        )

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
                "nighttime_adjusted": values["nighttime_adjusted"],
                "late_evening": values["late_evening"],
                "late_evening_adjusted": values["late_evening_adjusted"],
                **{
                    f"hour_{hour}_adjusted": values[f"hour_{hour}_adjusted"]
                    for hour in CONSUMPTION_DAY_HOURS
                },
                "car_charger": values["car_charger"],
                "inverter_night": values["inverter_night"],
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
