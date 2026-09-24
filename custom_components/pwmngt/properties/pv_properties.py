"""Read-only property accessors for the PwM PV device."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN


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
