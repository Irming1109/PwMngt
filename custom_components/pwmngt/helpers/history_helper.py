"""Recorder state-history helper -- used by data/consumption_data.py as
the fallback source for a counter that has no long-term statistics (no
state_class), see helpers/statistics_helper.py. Plain state history only
reaches back Recorder's purge_keep_days (default 10).
"""

import functools
import logging
from datetime import datetime

from homeassistant.core import HomeAssistant, State

LOGGER = logging.getLogger(__name__)


async def fetch_state_changes_since(
    hass: HomeAssistant,
    entity_id: str,
    start_time: datetime,
    end_time: datetime | None = None,
) -> list[State] | None:
    """Every recorded state change for entity_id from start_time to
    end_time (or now, if None), with the state already active at
    start_time as the first entry (via Recorder's
    "include_start_time_state") -- a complete, gap-free timeline over
    that window.

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
            end_time,
            entity_id=entity_id,
            include_start_time_state=True,
        )
        result = await recorder.async_add_executor_job(job)
    except Exception:  # noqa: BLE001 -- too many possible Recorder/DB
        # failure types to enumerate; any of them just means "no
        # data this time".
        LOGGER.warning(
            "PwMngt: could not read history for %s",
            entity_id,
            exc_info=True,
        )
        return None

    changes = result.get(entity_id) if result else None
    return changes or None
