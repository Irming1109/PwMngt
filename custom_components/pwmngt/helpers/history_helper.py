"""Recorder-history helper for "since local midnight" calculations that
backfill today's already-passed readings on setup instead of starting
blank -- used by data/consumption_snapshots_data.py (raw-minus-baseline)
and data/consumption_charger_data.py (reset-aware accumulator). Same
trick Home Assistant's own utility_meter uses to resume its cycle after
a restart.
"""

import functools
import logging
from datetime import datetime

from homeassistant.core import HomeAssistant, State

LOGGER = logging.getLogger(__name__)


async def fetch_state_changes_since(
    hass: HomeAssistant, entity_id: str, start_time: datetime
) -> list[State] | None:
    """Every recorded state change for entity_id from start_time to now,
    with the state already active at start_time as the first entry (via
    Recorder's "include_start_time_state") -- a complete, gap-free
    timeline from start_time onward.

    Returns None if Recorder isn't loaded, there's no history for
    entity_id, or the query fails -- callers should fall back to
    whatever they'd otherwise start fresh with.
    """
    if "recorder" not in hass.config.components:
        return None

    try:
        from homeassistant.components.recorder import get_instance, history
    except ImportError:
        return None

    try:
        recorder = get_instance(hass)
        job = functools.partial(
            history.state_changes_during_period,
            hass,
            start_time,
            entity_id=entity_id,
            include_start_time_state=True,
        )
        result = await recorder.async_add_executor_job(job)
    except Exception:  # noqa: BLE001 -- too many possible Recorder/DB
        # failure types to enumerate; any of them just means "no
        # backfill this time".
        LOGGER.warning(
            "PwMngt: could not read history for %s, starting fresh instead",
            entity_id,
            exc_info=True,
        )
        return None

    changes = result.get(entity_id) if result else None
    return changes or None
