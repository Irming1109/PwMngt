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

LOGGER = logging.getLogger(__name__)


async def fetch_hourly_sums(
    hass: HomeAssistant,
    statistic_ids: list[str],
    start_time: datetime,
    end_time: datetime,
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
    session_energy) needs, and does no harm for a never-resetting counter
    like the property's lifetime total.

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
            types={"sum"},
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
        sums: dict[int, float] = {}
        for row in rows:
            value = row.get("sum")
            start = row.get("start")
            if value is None or start is None:
                continue
            # Recent Home Assistant versions return "start" as a float
            # epoch timestamp, older ones as a datetime -- accept both.
            if isinstance(start, datetime):
                start = start.timestamp()
            sums[int(start) + 3600] = float(value)
        if sums:
            sums_by_id[statistic_id] = sums
    return sums_by_id
