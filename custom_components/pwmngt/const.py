"""Const used in the integration."""

# Startup banner
STARTUP = "start info"

CONF_DEFAULT_NAME = "Power Management"

DOMAIN = "pwmngt"
API_OBJ = "api_obj"

PLATFORMS = ["sensor", "binary_sensor", "number", "select", "button", "text"]

UPDATE_SIGNAL = f"{DOMAIN}_SIGNAL_UPDATE"

# ---------------------------------------------------------------------------
# Entity abstraction layer.
#
# PwMngt used to assume a fixed naming convention for other integrations'
# entities (e.g. "the inverter is called X, so its battery SoC entity is
# sensor.<x>_sol_battery_soc"). That assumption doesn't hold once you
# compare setups across different users -- everyone names/renames their
# entities differently. Instead, PwMngt's Options wizard lets the user pick
# the real source entity for each abstracted value directly (a dropdown
# filtered to entities of the right unit/type, so the list stays short and
# only shows entities that could actually work).
#
# CONF_ENTITY_MAP holds that per-installation mapping as a flat dict:
# {<entity key> -> <picked entity_id>}, stored in the config entry's
# options. The entity keys below (ENTITY_KEY_*) are the stable identifiers
# used both by the Options wizard (to build/read the picker fields) and by
# the platform files (sensor.py etc., to look up the picked entity_id).
# Expect ENTITY_KEY_* to grow to ~30 entries over time as more of PwMngt's
# segments get their abstraction layer built out -- add a new key here,
# then wire it into the matching wizard step and platform file.
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
ENTITY_KEY_BATTERY_NIGHTLY_TARGET = "battery_nightly_target"
ENTITY_KEY_PV_FORECAST_DAYTIME_TODAY = "pv_forecast_daytime_today"
ENTITY_KEY_PV_FORECAST_DAYTIME_TOMORROW = "pv_forecast_daytime_tomorrow"

# History-period select entity used by the (upcoming) native
# consumption-averages calculation engine to decide how many days of
# rolling history to average over. Kasper already has a "select" helper
# for this in his own HA (select.pwm_pv_history_period_days) -- not a
# sensor, so it isn't part of _SOLAR_PV_PLANT_FIELDS' unit-based picker,
# see config_flow.py's _SOLAR_PV_PLANT_SELECT_FIELDS instead. Wired into
# Options now so it's ready before that engine lands.
ENTITY_KEY_PV_HISTORY_PERIOD_DAYS = "pv_history_period_days"

# ---------------------------------------------------------------------------
# Segments: optional areas of PwMngt a given installation may not need.
# "Power Management" itself isn't in this list -- it's mandatory and always
# configured, the segments below are opt-in on top of it.
# ---------------------------------------------------------------------------

CONF_SEGMENTS = "segments"

SEGMENT_EV_CHARGING = "ev_charging"
SEGMENT_POOL = "pool"
SEGMENT_PV_SURPLUS = "pv_surplus"
