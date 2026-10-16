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


def billing_period_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<hub>_billing_period (see PwM_CONFIG_SELECTS in
    select.py) -- "Month" or "Quarter"."""
    return _hub_entity_id(hass, entry, "select", "billing_period")


def electricity_tax_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<hub>_electricity_tax (see PwM_CONFIG_SELECTS in
    select.py) -- "Full" or "Reduced (electric heating)"."""
    return _hub_entity_id(hass, entry, "select", "electricity_tax")


# select.<hub>_billing_period's two option strings (select.py).
_BILLING_PERIOD_MONTH = "Month"
_BILLING_PERIOD_QUARTER = "Quarter"

# Denmark's electricity-tax threshold: 4000 kWh/year, i.e. 333.3 kWh per
# calendar month for a monthly-settled customer, or 1000 kWh per calendar
# quarter for a quarterly-settled one (Claus, 2026-10-16). Quarter is 3x
# the monthly figure, not a separately-defined number.
_TAX_THRESHOLD_KWH_MONTH = 4000 / 12  # 333.33...
_TAX_THRESHOLD_KWH_QUARTER = _TAX_THRESHOLD_KWH_MONTH * 3  # 1000.0

# select.<hub>_electricity_tax's option string meaning the reduced tier is
# in play at all for this household (select.py). Anything else ("Full")
# means every kWh is taxed at the full rate -- no threshold math.
_ELECTRICITY_TAX_REDUCED = "Reduced (electric heating)"


def billing_period_is_quarter(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Whether select.<hub>_billing_period is set to "Quarter" (vs. the
    default "Month") -- the flag savings_data.py's tax_period_id() uses to
    pick calendar months vs. calendar quarters as the electricity-tax
    reset period."""
    entity_id = billing_period_entity_id(hass, entry)
    state = hass.states.get(entity_id) if entity_id else None
    return state is not None and state.state == _BILLING_PERIOD_QUARTER


def billing_period_threshold_kwh(hass: HomeAssistant, entry: ConfigEntry) -> float:
    """The electricity-tax full-rate threshold (kWh) for the billing
    period selected in select.<hub>_billing_period -- 333.3 kWh/month or
    1000 kWh/quarter (Claus, 2026-10-16). Defaults to the monthly figure
    if the select is missing/unavailable (same as its own default_option
    in select.py)."""
    entity_id = billing_period_entity_id(hass, entry)
    state = hass.states.get(entity_id) if entity_id else None
    value = state.state if state is not None else None
    if value == _BILLING_PERIOD_QUARTER:
        return _TAX_THRESHOLD_KWH_QUARTER
    return _TAX_THRESHOLD_KWH_MONTH


def electricity_tax_reduced_enabled(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Whether this household ever gets the reduced electricity-tax rate
    above the threshold, per select.<hub>_electricity_tax -- False
    ("Full") means every kWh is taxed at the full rate, no threshold math
    at all (Claus, 2026-10-16). Defaults to False if the select is
    missing/unavailable, matching that select's own default_option
    ("Full") in select.py."""
    entity_id = electricity_tax_entity_id(hass, entry)
    state = hass.states.get(entity_id) if entity_id else None
    if state is None or state.state in ("unknown", "unavailable"):
        return False
    return state.state == _ELECTRICITY_TAX_REDUCED


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
