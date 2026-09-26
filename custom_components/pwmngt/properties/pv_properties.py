"""Read-only property accessors for the PwM PV device."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN

_DEFAULT_HISTORY_PERIOD_DAYS = 7


def is_forced_charging(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Whether PwMngt's own Forced charge scaffold sensor (see
    PwM_PV_SCAFFOLD_SENSORS in sensor.py) currently reports "on".

    Fails safe to False in both cases where there's nothing real to read
    yet: the entity hasn't been registered at all, or it has but hasn't
    been given a real value yet (still scaffolding -- the logic that sets
    it isn't built). No caching -- an entity-registry lookup is just an
    in-memory dict lookup, cheap enough to redo on every call.
    """
    unique_id = f"{entry.entry_id}_pv_forced_charge"
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    if not entity_id:
        return False
    state = hass.states.get(entity_id)
    return state is not None and state.state == "on"


def history_period_days_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.pwm_pv_history_period_days (see
    PwM_PV_CONFIG_SELECTS in select.py), or None if it isn't registered
    yet. Split out from history_period_days() below so a caller that
    needs to subscribe to this select's state changes (see
    data/consumption_averages_data.py) doesn't have to repeat the
    entity-registry lookup itself.
    """
    unique_id = f"{entry.entry_id}_pv_history_period_days"
    return er.async_get(hass).async_get_entity_id("select", DOMAIN, unique_id)


def history_period_days(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """The number of days select.pwm_pv_history_period_days is currently
    set to (7/14/21/30) -- how far back a rolling consumption average
    (see data/consumption_averages_data.py) should look.

    Falls back to _DEFAULT_HISTORY_PERIOD_DAYS in both cases where
    there's nothing real to read yet: the entity hasn't been registered
    at all, or its state isn't one of the expected numbers. No caching --
    same reasoning as is_forced_charging() above.
    """
    entity_id = history_period_days_entity_id(hass, entry)
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return _DEFAULT_HISTORY_PERIOD_DAYS
    try:
        return int(state.state)
    except ValueError:
        return _DEFAULT_HISTORY_PERIOD_DAYS


def consumption_snapshot_kl(
    hass: HomeAssistant, entry: ConfigEntry, category: str, time_key: str
) -> float | None:
    """One category's since-local-midnight consumption snapshot at one of
    data/consumption_snapshots_data.py's fixed sample times -- e.g.
    category="property", time_key="16" reads that sensor's
    "property_kl_16" attribute.

    Returns None if that sensor isn't registered yet, or hasn't captured
    this particular sample yet (e.g. it's earlier in the day than
    time_key, or a fresh install with no history to backfill from). No
    caching -- same reasoning as is_forced_charging() above.
    """
    unique_id = f"{entry.entry_id}_pv_consumption_snapshots_data"
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return None
    value = state.attributes.get(f"{category}_kl_{time_key}")
    return float(value) if value is not None else None
