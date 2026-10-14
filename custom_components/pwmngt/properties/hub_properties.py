"""Read-only property accessors for the PwM hub device."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN


def _hub_entity_id(hass: HomeAssistant, entry: ConfigEntry, platform: str, key: str) -> str | None:
    """Entity id of one of the PwM hub device's own entities, looked up
    through the entity registry by unique_id ("<entry_id>_<key>") -- same
    reasoning as pv_properties._pv_entity_id, just without the "_pv_"
    infix hub-level entities don't get (see select.py's PwMngtSelect)."""
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )


def tariff_fuse_size_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<hub>_tariff_fuse_size (see PwM_CONFIG_SELECTS
    in select.py) -- the property's main fuse rating (A per phase)."""
    return _hub_entity_id(hass, entry, "select", "tariff_fuse_size")


def billing_method_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<hub>_billing_method (see PwM_CONFIG_SELECTS in
    select.py) -- fixed-price vs. variable (spot) billing."""
    return _hub_entity_id(hass, entry, "select", "billing_method")
