"""Read-only property accessors for the PwM hub device."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import (
    CONF_ELECTRICITY_PRICE_SURCHARGE,
    CONF_ENTITY_MAP,
    DOMAIN,
    ENTITY_KEY_SPOT_ELECTRICITY_PRICE,
)
from ..helpers.state_helper import read_float_state


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


def electricity_price(hass: HomeAssistant, entry: ConfigEntry) -> float | None:
    """The electricity price (kr/kWh) to use everywhere PwMngt calculates
    money from the configured spot-price entity (Options -> Solar PV
    Plant -> Strømligning) -- that entity's current value, plus
    CONF_ELECTRICITY_PRICE_SURCHARGE added on top.

    Added (Claus, 2026-10-15): his own supplier charges a flat surcharge
    on top of what his configured Strømligning sensor reports, which that
    sensor has no way to know about itself. None of PwMngt's own code
    should read ENTITY_KEY_SPOT_ELECTRICITY_PRICE's entity directly any
    more -- always go through this instead, or the surcharge silently
    goes missing wherever it was skipped.

    Returns None (never a bare default) when the entity isn't configured
    or its state isn't a valid number -- same "don't invent a price"
    stance as every other input in this integration; it's each caller's
    own job to decide what, if anything, to fall back to.
    """
    entity_map = entry.options.get(CONF_ENTITY_MAP, {})
    entity_id = entity_map.get(ENTITY_KEY_SPOT_ELECTRICITY_PRICE) or None
    if entity_id is None:
        return None
    base_price = read_float_state(hass.states.get(entity_id))
    if base_price is None:
        return None
    surcharge = entry.options.get(CONF_ELECTRICITY_PRICE_SURCHARGE, 0.0) or 0.0
    return base_price + surcharge
