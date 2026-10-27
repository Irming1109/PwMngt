"""Entity registry lookups by an integration's own entity name.

Shared by the options wizard's inverter auto-detect (config_flow.py) and
the sensors PwMngt calculates from an inverter's own entities
(data/inverter_derived_data.py) -- both must find the same entity the
same way, never by guessing an entity_id string.
"""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


def find_entity_by_name(hass: HomeAssistant, platform: str, name: str) -> str | None:
    """The single entity_id from `platform` whose entity registry
    original_name matches `name` (see registry_entry_names() for the
    forms that count).

    original_name rather than translation_key: the inverter integrations
    PwMngt knows (Kostal Plenticore, Growatt) don't set translation_key on
    their sensors -- confirmed against Kasper's live Kostal install and
    the growatt_modbus source. Like translation_key it doesn't care what
    the entity_id happens to be or what the device is currently named; it
    only stops matching if the user renamed the entity's own name.

    None unless exactly one match exists -- e.g. two inverters of the same
    brand would otherwise risk mixing up whose "Grid Power" is whose.
    """
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    matches = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == platform
        and name in registry_entry_names(entry, device_registry)
    ]
    return matches[0] if len(matches) == 1 else None


def entity_platform(hass: HomeAssistant, entity_id: str | None) -> str | None:
    """The integration (platform) an entity belongs to, or None if it isn't
    in the entity registry (e.g. a YAML template sensor without unique_id)."""
    if not entity_id:
        return None
    entry = er.async_get(hass).async_get(entity_id)
    return entry.platform if entry is not None else None


def registry_entry_names(entry, device_registry) -> set[str]:
    """Every form of an entity registry entry's integration-given name that
    find_entity_by_name() should match against.

    An integration that doesn't use has_entity_name (Kostal Plenticore
    doesn't) stores its full name, device name included, as original_name
    -- e.g. "Pileaas-Sol Battery SoC" on Kasper's install (device "Pileaas-Sol"), not "Battery
    SoC". Recent Home Assistant versions also keep the device-name-stripped
    form as original_name_unprefixed, and that stripped form is what the
    frontend and the entity-registry websocket API show (which is why an
    earlier check against those looked right while this function matched
    nothing -- found 2026-09-28). So: the raw original_name, the
    unprefixed one when Home Assistant provides it, and -- for versions
    that don't -- original_name with the entity's own device name
    stripped off the front by hand.
    """
    names: set[str] = set()
    if entry.original_name:
        names.add(entry.original_name)
    unprefixed = getattr(entry, "original_name_unprefixed", None)
    if unprefixed:
        names.add(unprefixed)
    if entry.original_name and entry.device_id:
        device = device_registry.async_get(entry.device_id)
        for device_name in (
            (device.name_by_user, device.name) if device is not None else ()
        ):
            prefix = f"{device_name} " if device_name else None
            if prefix and entry.original_name.startswith(prefix):
                names.add(entry.original_name[len(prefix):])
    return names
