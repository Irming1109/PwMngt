"""Recorder long-term-statistics helper -- used by data/consumption_data.py
to read a source counter's value at exact hour boundaries (e.g. "at
16:00") for any past day, without PwMngt ever having sampled it itself.

Why long-term statistics rather than plain state history: Recorder purges
state history after purge_keep_days (default 10 -- confirmed as the
effective limit on Kasper's install 2026-09-28), while the hourly
statistics Home Assistant compiles for every sensor with a state_class
are kept indefinitely (Kasper's go back to July 2024). That's what lets
consumption_data fill all 30 days of its rolling histories straight away.
"""

import functools
import logging
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

LOGGER = logging.getLogger(__name__)

# Issue #130: Home Assistant only takes a drop of more than 10 % as a
# meter reset (smaller ones it logs as "not strictly increasing") --
# counter_values() follows the same line.
_RESET_DROP_FACTOR = 0.9


async def fetch_hourly_sums(
    hass: HomeAssistant,
    statistic_ids: list[str],
    start_time: datetime,
    end_time: datetime,
    lifetime_ids: frozenset[str] = frozenset(),
) -> dict[str, dict[int, float]]:
    """Each statistic_id's hourly "sum" between start_time and end_time,
    keyed by the UTC epoch second at which each hour ENDS -- i.e.
    result[entity_id][ts] is the counter's cumulative value as of ts, the
    same thing as "the last state before ts".

    Verified live 2026-09-28 against Kasper's
    sensor.pileaas_sol_home_consumption_total: the hourly row covering
    15:00-16:00 held 37496.2, exactly the last recorded state before
    16:00 in plain state history.

    "sum" rather than "state": for a total_increasing sensor, Home
    Assistant's statistics engine already treats a drop in state as a
    meter reset and keeps "sum" ever-increasing across it. That's exactly
    what a charger's per-session counter (Wallbox added_energy, Easee
    session_energy) needs. lifetime_ids (issue #130): counters that are
    meant never to reset, like the property's lifetime total -- for them
    a brief drop to 0 is an outage, not a reset, see counter_values().

    Only entities with a state_class get statistics at all. An entity
    without any rows in the range is simply absent from the result --
    the caller falls back to plain state history for it (see
    data/consumption_data.py). Returns {} if Recorder isn't loaded or the
    query fails.
    """
    if not statistic_ids or "recorder" not in hass.config.components:
        return {}

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import (
            statistics_during_period,
        )
    except ImportError:
        return {}

    try:
        job = functools.partial(
            statistics_during_period,
            hass,
            start_time,
            end_time,
            statistic_ids=set(statistic_ids),
            period="hour",
            units=None,
            types={"sum", "state"},
        )
        result = await get_instance(hass).async_add_executor_job(job)
    except Exception:  # noqa: BLE001 -- same reasoning as history_helper.py
        LOGGER.warning(
            "PwMngt: could not read long-term statistics for %s",
            statistic_ids,
            exc_info=True,
        )
        return {}

    sums_by_id: dict[str, dict[int, float]] = {}
    for statistic_id, rows in (result or {}).items():
        rows_by_end: list[tuple[int, float, float | None]] = []
        for row in rows:
            value = row.get("sum")
            start = row.get("start")
            if value is None or start is None:
                continue
            # Recent Home Assistant versions return "start" as a float
            # epoch timestamp, older ones as a datetime -- accept both.
            if isinstance(start, datetime):
                start = start.timestamp()
            state = row.get("state")
            rows_by_end.append(
                (int(start) + 3600, float(value), None if state is None else float(state))
            )
        if not rows_by_end:
            continue
        if statistic_id in lifetime_ids:
            sums_by_id[statistic_id] = counter_values(statistic_id, rows_by_end)
        else:
            sums_by_id[statistic_id] = {end: total for end, total, _state in rows_by_end}
    return sums_by_id


def counter_values(
    statistic_id: str, rows: list[tuple[int, float, float | None]]
) -> dict[int, float]:
    """Issue #130: a lifetime counter's cumulative value at each hour's
    end, from (hour end, "sum", "state") rows.

    Home Assistant treats any real drop of a total_increasing sensor as a
    meter reset and, when the value comes back, adds all of it to "sum".
    A source that reports 0 for a second (Claus's Growatt Modbus "Load
    Energy Total" 2026-11-23 21:11: 2648.0 -> 0.0 -> 2648.0) thereby adds
    its whole lifetime value -- +2648 kWh in one hour, and consumption_data
    threw away the day as implausible, five days in a month.

    The end-of-hour "state" doesn't see such a dip. So:
    - The counter never really drops in the range: the value is "state",
      anchored to the first "sum" so the scale matches the rest of the
      series. A drop that is back at or above the earlier value in the
      next hour is a dip at the hour's end, not a reset.
    - It does drop after all (a replaced meter, a Utility Meter with a
      cycle): "sum", which handles a real reset.
    - A row without "state": "sum" as well.

    Only for lifetime counters: a charger's session counter can reset and
    pass the old session's value within one hour, which the end-of-hour
    "state" can't tell from a dip -- "sum" is right for those.
    """
    rows = sorted(rows)
    sums = {end: total for end, total, _state in rows}
    states = [state for _end, _total, state in rows]
    if any(state is None for state in states):
        return sums

    cleaned: list[float] = []
    for index, state in enumerate(states):
        previous = cleaned[-1] if cleaned else None
        if previous is not None and state < previous:
            following = states[index + 1] if index + 1 < len(states) else None
            is_reset = state < previous * _RESET_DROP_FACTOR
            if is_reset and (following is None or following < previous):
                return sums  # A real reset -- "sum" handles it.
            # A dip at the hour's end, or a small glitch HA doesn't take
            # as a reset either.
            state = previous
        cleaned.append(state)

    first_sum = rows[0][1]
    values: dict[int, float] = {}
    for (end, _total, _state), state in zip(rows, cleaned):
        values[end] = round(first_sum + state - cleaned[0], 3)
    previous_gap = 0.0
    for end, _total, _state in rows:
        gap = sums[end] - values[end]
        if abs(gap - previous_gap) > 0.5:
            LOGGER.info(
                "PwMngt: %s statistics sum jumps %.1f in the hour ending %s "
                "although the counter didn't reset -- using the counter's own value",
                statistic_id,
                gap - previous_gap,
                dt_util.as_local(dt_util.utc_from_timestamp(end)).isoformat(),
            )
        previous_gap = gap
    return values
