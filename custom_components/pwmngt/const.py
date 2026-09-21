"""Const used in the integration."""

# Startup banner
STARTUP = "start info"

CONF_DEFAULT_NAME = "Power Management"

DOMAIN = "pwmngt"
API_OBJ = "api_obj"

PLATFORMS = ["sensor", "binary_sensor", "number", "select", "button", "text"]

UPDATE_SIGNAL = f"{DOMAIN}_SIGNAL_UPDATE"

# ---------------------------------------------------------------------------
# External integrations PwMngt depends on but can't declare as a formal
# dependency. A missing one raises a Home Assistant Repair instead (see
# _async_check_dependencies in __init__.py).
# ---------------------------------------------------------------------------

DEPENDENCY_SOLCAST_SOLAR = "solcast_solar"
DEPENDENCY_STROMLIGNING = "stromligning"

# ---------------------------------------------------------------------------
# Entity abstraction layer.
#
# The Options wizard lets the user pick the real source entity for each
# abstracted value (an entity picker filtered to the right unit/type).
# CONF_ENTITY_MAP holds that per-installation mapping as a flat dict:
# {<entity key> -> <picked entity_id>}, stored in the config entry's
# options. ENTITY_KEY_* below are the stable identifiers used both by the
# Options wizard and by the platform files (sensor.py etc.) to look up the
# picked entity_id.
# ---------------------------------------------------------------------------

CONF_ENTITY_MAP = "entity_map"

# Solar PV Plant segment (mandatory -- always configured).
ENTITY_KEY_BATTERY_SOC = "battery_soc"
ENTITY_KEY_BATTERY_PV_CHARGED = "battery_pv_charged"
ENTITY_KEY_BATTERY_PV_DISCHARGED = "battery_pv_discharged"
ENTITY_KEY_BATTERY_POWER = "battery_power"
ENTITY_KEY_PV1_POWER = "pv1_power"
ENTITY_KEY_PV2_POWER = "pv2_power"
ENTITY_KEY_PV3_POWER = "pv3_power"
ENTITY_KEY_PV_DIRECT_CONSUMPTION = "pv_direct_consumption"
ENTITY_KEY_PV_TOTAL_CONSUMPTION = "pv_total_consumption"
ENTITY_KEY_GRID_POWER = "grid_power"
ENTITY_KEY_PV_FORECAST_TODAY = "pv_forecast_today"
ENTITY_KEY_PV_FORECAST_TOMORROW = "pv_forecast_tomorrow"

# battery_nightly_target, pv_forecast_daytime_today and
# pv_forecast_daytime_tomorrow are not entity_map fields -- PwMngt
# computes them internally (see sensor.py's PwM_PV_PLACEHOLDER_SENSORS).

# pv_history_period_days is also not an entity_map field -- it's a fixed
# entity (select.pwm_pv_history_period_days) the user sets through his own
# PwMPvCard dashboard card (www/pwm-cards.js).

# Hub segment -- an entity_map field like the Solar PV Plant ones, even
# though the sensor itself lives on the Hub device.
ENTITY_KEY_SPOT_ELECTRICITY_PRICE = "spot_electricity_price"

# ---------------------------------------------------------------------------
# Segments: optional areas of PwMngt a given installation may not need.
# "Power Management" itself isn't in this list -- it's mandatory and always
# configured, the segments below are opt-in on top of it.
# ---------------------------------------------------------------------------

CONF_SEGMENTS = "segments"

SEGMENT_EV_CHARGING = "ev_charging"
SEGMENT_POOL = "pool"
SEGMENT_PV_SURPLUS = "pv_surplus"
