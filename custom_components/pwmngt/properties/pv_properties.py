"""Read-only property accessors for the PwM PV device."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import DEFAULT_BATTERY_MIN_SOC, DOMAIN, POWER_SIGN_OPTIONS

_DEFAULT_HISTORY_PERIOD_DAYS = 7


def power_sign(entry: ConfigEntry, entity_map_key: str) -> float:
    """-1.0 when the wizard's "invert sign" option for this power field is
    ticked (see POWER_SIGN_OPTIONS in const.py), else 1.0. Multiply the
    source entity's raw value by this, so Grid power is always positive =
    buying from the grid and Battery power positive = discharging,
    whatever the inverter itself reports. Fields without such an option
    are always 1.0."""
    option = POWER_SIGN_OPTIONS.get(entity_map_key)
    return -1.0 if option and entry.options.get(option) else 1.0


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


def solar_data_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of data/solar_data.py's solar_data sensor (its
    "pv_forecast_8_16_tomorrow" etc. attributes -- Old:
    sensor.solproduktion_imorgen_8_16)."""
    return _pv_entity_id(hass, entry, "sensor", "solar_data")


def pv_total_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of sensor.<pv>_pv_total (see PwM_PV_SUM_SENSORS in
    sensor.py) -- the live sum of PV1/PV2/PV3 power. Read by
    data/pv_shift_data.py."""
    return _pv_entity_id(hass, entry, "sensor", "pv_total")


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


def consumption_data_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of data/consumption_data.py's consumption_data sensor."""
    return _pv_entity_id(hass, entry, "sensor", "consumption_data")


def battery_size_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_battery_size (see PwM_PV_CONFIG_SELECTS in
    select.py)."""
    return _pv_entity_id(hass, entry, "select", "battery_size")


def battery_nightly_target_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of the PV device's Battery nightly target sensor (see
    PwM_BATTERY_NIGHTLY_TARGET_SENSORS in data/solar_data.py)."""
    return _pv_entity_id(hass, entry, "sensor", "battery_nightly_target")


def battery_target_end_time_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_battery_target_end_time (see
    PwM_PV_CONFIG_SELECTS in select.py)."""
    return _pv_entity_id(hass, entry, "select", "battery_target_end_time")


def battery_command_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_battery_command (see PwM_PV_CONFIG_SELECTS
    in select.py)."""
    return _pv_entity_id(hass, entry, "select", "battery_command")


def battery_mode_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_battery_mode (see PwM_PV_CONFIG_SELECTS in
    select.py) -- Old: select.pv_battery_mode ("Auto"/"Manuel"). Not
    read by any Node-RED function node itself, but declared there for
    future use; Claus asked (2026-10-14) that PwMngt actually wire it
    up: while it's "manual", the whole charge/no-charge automation
    (data/battery_charge_decision.py and data/battery_charge_level.py)
    must not run at all."""
    return _pv_entity_id(hass, entry, "select", "battery_mode")


def is_battery_mode_manual(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """True while select.<pv>_battery_mode is "manual". Fails safe to
    False (i.e. automation runs) if the entity isn't registered or has
    no state yet -- same reasoning as is_forced_charging() above."""
    entity_id = battery_mode_entity_id(hass, entry)
    state = hass.states.get(entity_id) if entity_id else None
    return state is not None and state.state == "manual"


def battery_charge_status_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of the PV device's Battery charge status sensor (see
    PwM_PV_STATUS_SENSORS in sensor.py)."""
    return _pv_entity_id(hass, entry, "sensor", "battery_charge_status")


def battery_max_charge_effect_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_battery_max_charge_effect (see
    PwM_PV_CONFIG_SELECTS in select.py)."""
    return _pv_entity_id(hass, entry, "select", "battery_max_charge_effect")


def battery_price_difference_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_pv_battery_price_difference (see
    PwM_PV_CONFIG_SELECTS in select.py)."""
    return _pv_entity_id(hass, entry, "select", "pv_battery_price_difference")


def battery_min_soc_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of select.<pv>_battery_min_soc (see PwM_PV_CONFIG_SELECTS
    in select.py)."""
    return _pv_entity_id(hass, entry, "select", "battery_min_soc")


def battery_min_soc(hass: HomeAssistant, entry: ConfigEntry) -> float:
    """The SoC (%) the battery is discharged down to, as set in
    select.<pv>_battery_min_soc -- the "empty" level PwMngt's battery
    calculations count from (Node-RED: a fixed 5). Only used for
    calculations; the inverter's own discharge limit is set in the
    inverter. Falls back to DEFAULT_BATTERY_MIN_SOC while the select isn't
    registered or has no usable state yet."""
    entity_id = battery_min_soc_entity_id(hass, entry)
    state = hass.states.get(entity_id) if entity_id else None
    try:
        return float(state.state) if state is not None else DEFAULT_BATTERY_MIN_SOC
    except ValueError:
        return DEFAULT_BATTERY_MIN_SOC


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
