"""Const used in the integration."""

# Startup banner
STARTUP = "start info"

CONF_DEFAULT_NAME = "Power Management"

DOMAIN = "pwmngt"
API_OBJ = "api_obj"

PLATFORMS = ["sensor", "binary_sensor", "number", "select", "button", "text"]

UPDATE_SIGNAL = f"{DOMAIN}_SIGNAL_UPDATE"
# Name of the solar inverter device as it appears in this Home Assistant
# instance -- used to build entity_ids like "<inverter_name>_sol_battery_soc"
# for reading values from it (see PwM_PV_MIRROR_SENSORS in sensor.py).
# Currently only Kostal inverters are supported, and we're assuming all of
# them expose the same suffix pattern regardless of the device's own name.
CONF_INVERTER_NAME = "inverter_name"
