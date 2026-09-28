"""Read-only property accessors for the PwM PV device."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN

_DEFAULT_HISTORY_PERIOD_DAYS = 7


def _pv_entity_id(hass: HomeAssistant, entry: ConfigEntry, platform: str, key: str) -> str | None:
    """Entity id of one of the PwM PV device's own entities, looked up
    through the entity registry by unique_id ("<entry_id>_pv_<key>") --
    never by guessing the entity_id string, which changes with Home
    Assistant's "_2" de-duplication or a device rename. None if it isn't
    registered yet."""
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_pv_{key}"
    )


def battery_data_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of data/battery_data.py's battery_data sensor."""
    return _pv_entity_id(hass, entry, "sensor", "battery_data")


def is_forced_charging(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Whether battery_data's "forced_charge" attribute (see
    data/battery_data.py) currently reads "on" -- i.e. the battery's
    minimum SoC is above its current SoC, so it's being charged from the
    grid on purpose.

    Until v0.3.21 this read a separate "Forced charge" scaffold sensor
    that nothing ever set; that sensor is gone and the value now lives on
    battery_data. Callers (data/balance_data.py) are unchanged.

    Fails safe to False when there's nothing real to read yet: the entity
    isn't registered, has no state, or hasn't computed anything yet. No
    caching -- an entity-registry lookup is just an in-memory dict lookup,
    cheap enough to redo on every call.
    """
    entity_id = battery_data_entity_id(hass, entry)
    state = hass.states.get(entity_id) if entity_id else None
    return state is not None and state.attributes.get("forced_charge") == "on"


def battery_nightly_target_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of the PV device's Battery nightly target sensor (see
    PwM_PV_SCAFFOLD_SENSORS in sensor.py)."""
    return _pv_entity_id(hass, entry, "sensor", "battery_nightly_target")


def battery_target_end_time_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_battery_target_end_time (see
    PwM_PV_CONFIG_SELECTS in select.py)."""
    return _pv_entity_id(hass, entry, "select", "battery_target_end_time")


def battery_command_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_battery_command (see PwM_PV_CONFIG_SELECTS
    in select.py)."""
    return _pv_entity_id(hass, entry, "select", "battery_command")


def battery_charge_status_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of the PV device's Battery charge status sensor (see
    PwM_PV_STATUS_SENSORS in sensor.py)."""
    return _pv_entity_id(hass, entry, "sensor", "battery_charge_status")


def battery_price_difference_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_pv_battery_price_difference (see
    PwM_PV_CONFIG_SELECTS in select.py)."""
    return _pv_entity_id(hass, entry, "select", "pv_battery_price_difference")


def history_period_days_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.pwm_pv_history_period_days (see
    PwM_PV_CONFIG_SELECTS in select.py), or None if it isn't registered
    yet. Split out from history_period_days() below so a caller that
    needs to subscribe to this select's state changes (see
    data/consumption_data.py) doesn't have to repeat the
    entity-registry lookup itself.
    """
    unique_id = f"{entry.entry_id}_pv_history_period_days"
    return er.async_get(hass).async_get_entity_id("select", DOMAIN, unique_id)


def history_period_days(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """The number of days select.pwm_pv_history_period_days is currently
    set to (7/14/21/30) -- how far back a rolling consumption average
    (see data/consumption_data.py) should look.

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
