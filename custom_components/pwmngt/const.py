"""Const used in the integration."""

from datetime import timedelta

# Startup banner
STARTUP = "start info"

CONF_DEFAULT_NAME = "Power Management"

DOMAIN = "pwmngt"

PLATFORMS = ["sensor", "number", "select", "button", "text"]

# ---------------------------------------------------------------------------
# External integrations PwMngt depends on but can't declare as a formal
# dependency. A missing one raises a Home Assistant Repair instead (see
# _async_check_dependencies in __init__.py).
# ---------------------------------------------------------------------------

DEPENDENCY_SOLCAST_SOLAR = "solcast_solar"
DEPENDENCY_STROMLIGNING = "stromligning"

# ---------------------------------------------------------------------------
# Charger brand integrations. Unlike DEPENDENCY_* above, neither is
# required -- EV charging is an opt-in segment, and a charger can be "Not
# installed" -- so these don't raise a Repair. They're only used to narrow
# the EV Charging wizard page's "type" dropdown (config_flow.py's
# _installed_charger_type_options()) down to the brand(s) actually
# configured in this Home Assistant, so the user can't pick a brand he
# doesn't have. The select entity itself (select.<id>_type, see
# PwM_CONFIG_SELECTS in select.py) is unaffected and keeps offering both.
# ---------------------------------------------------------------------------

CHARGER_TYPE_INTEGRATION_DOMAINS: dict[str, str] = {
    "Wallbox": "wallbox",
    "Easee": "easee",
}

# The charger "<id>_type" select's "no charger here" option (select.py,
# PwM_CONFIG_SELECTS). Named so config_flow.py can skip per-charger fields
# that only make sense for an installed charger, without repeating the
# literal string.
CHARGER_TYPE_NOT_INSTALLED = "Not installed"

# ---------------------------------------------------------------------------
# Each brand's own sensor for "how much energy this charger has added,
# reset at the start of every new charging session" -- used by
# config_flow.py's _auto_detect_charger_consumption_entity() to pre-fill a
# charger's "consumption_source_entity" (text.py) once its brand (type) and
# physical device (charging_device_id) are both already known.
#
# (platform, translation_key), matched the same way _auto_detect_entity_id
# already matches Solcast/Strømligning sensors -- never the entity_id
# string. That matters here specifically: on Kasper's own HA, the Wallbox
# sensor this is modelled on is sensor.wallbox_portal_added_energy, sitting
# on a device Home Assistant now calls "Wallbox Pulsar MAX SN 1018689" --
# confirmed live via the entity registry (hass.entities in the frontend).
# "portal" is just whatever the device happened to be named when that
# entity_id was first generated; entity_ids don't get renamed when a
# device is renamed later. Its translation_key, "added_energy", is
# unaffected by any of that.
#
# Easee's equivalent, confirmed from the easee_hass integration's own
# const.py/translations (no live Easee install available to double-check
# against, since Kasper doesn't have one) is "session_energy" -- not
# "lifetime_energy". Easee's API exposes both: sessionEnergy resets at the
# start of each charging session (same behaviour as Wallbox's added_energy,
# and what data/consumption_data.py's car_charger_consumption is built to
# handle), while lifetimeEnergy is a never-resetting odometer-style total
# -- the wrong shape for this field.
# ---------------------------------------------------------------------------

CHARGER_CONSUMPTION_SOURCE_TRANSLATION_KEYS: dict[str, tuple[str, str]] = {
    "Wallbox": ("wallbox", "added_energy"),
    "Easee": ("easee", "session_energy"),
}

# ---------------------------------------------------------------------------
# Inverter brand integrations. Same idea as CHARGER_TYPE_INTEGRATION_DOMAINS
# above, for the Solar PV Plant flow's own "Inverter" field
# (config_flow.py's async_step_pv_inverter) -- narrows that field's
# dropdown down to the brand(s) actually configured in this Home Assistant,
# never required (an install with neither just sees "Not installed" and
# fills in the Core group's fields by hand, same as always).
# ---------------------------------------------------------------------------

INVERTER_TYPE_INTEGRATION_DOMAINS: dict[str, str] = {
    "Kostal Plenticore": "kostal_plenticore",
    "Growatt": "growatt_server",
}

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
# Energy charged into the battery from the grid today (kWh), a counter
# that resets at local midnight. Read by data/battery_data.py to tell
# whether a SoC rise came from the grid or from PV. Old:
# sensor.batteri_ladet_fra_grid_pr_dag (a daily utility_meter in Claus's
# HA, read by Node-RED's "Min_SOC beregning").
ENTITY_KEY_BATTERY_GRID_CHARGED_DAY = "battery_grid_charged_day"
ENTITY_KEY_PV1_POWER = "pv1_power"
ENTITY_KEY_PV2_POWER = "pv2_power"
ENTITY_KEY_PV3_POWER = "pv3_power"
ENTITY_KEY_PV_DIRECT_CONSUMPTION = "pv_direct_consumption"
ENTITY_KEY_PV_TOTAL_CONSUMPTION = "pv_total_consumption"
ENTITY_KEY_GRID_POWER = "grid_power"
ENTITY_KEY_PV_FORECAST_TODAY = "pv_forecast_today"
ENTITY_KEY_PV_FORECAST_TOMORROW = "pv_forecast_tomorrow"

# battery_nightly_target and solar_data's PV-forecast attributes are not
# entity_map fields -- PwMngt computes them internally (see
# data/solar_data.py).

# pv_history_period_days is also not an entity_map field -- it's a fixed
# entity (select.pwm_pv_history_period_days) the user sets through his own
# PwMPvCard dashboard card (www/pwm-cards.js).

# Hub segment -- an entity_map field like the Solar PV Plant ones, even
# though the sensor itself lives on the Hub device.
ENTITY_KEY_SPOT_ELECTRICITY_PRICE = "spot_electricity_price"

# Consumption tracking -- entity_map field(s) read by
# data/consumption_data.py straight from Recorder (hourly long-term
# statistics) to build its rolling averages. Each is a raw,
# ever-increasing lifetime counter (kWh) with a state_class -- NOT a
# Daily Utility Meter helper.
ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL = "property_consumption_total"

# ---------------------------------------------------------------------------
# Once the Solar PV Plant flow's own "Inverter" field
# (config_flow.py's async_step_pv_inverter, narrowed to installed brands by
# INVERTER_TYPE_INTEGRATION_DOMAINS above) names a brand, this is what
# auto-fills the Core group's fields (see _SOLAR_PV_PLANT_GROUPS) --
# config_flow.py's _auto_detect_entity_by_name() matches each entry against
# the entity registry, one brand + one Core field (ENTITY_KEY_*) at a time.
#
# Deliberately (platform, original_name) here, NOT (platform,
# translation_key) like CHARGER_CONSUMPTION_SOURCE_TRANSLATION_KEYS above.
# Checked directly against Kasper's own live Kostal Plenticore install
# (Pileaas_Sol) via its entity registry: none of its sensors carry a
# translation_key at all -- this integration predates that Home Assistant
# convention and sets each sensor's `name` directly in its own
# SensorEntityDescription instead (e.g. sensor.pileaas_sol_grid_power's
# registry entry has original_name "Grid Power", no translation_key
# property, confirmed live 2026-09-26). original_name is the next most
# stable thing Home Assistant exposes for matching: like translation_key,
# it doesn't care what the entity_id happens to be (see
# CHARGER_CONSUMPTION_SOURCE_TRANSLATION_KEYS's own note on that) or what
# the device is currently named -- it only stops matching if the user has
# explicitly renamed that specific entity's friendly name in the UI.
#
# Kostal Plenticore's names below are directly confirmed this way, one for
# one, against Kasper's own install. Growatt's are NOT -- Kasper doesn't
# have a live Growatt to check against, and Home Assistant's own
# growatt_server source wasn't reachable during this research (see the
# chat where this was added for what was tried). They're inferred from a
# well-known third-party fork Home Assistant's own integration descends
# from (indykoning/home-assistant-growatt-server), retitled to the same
# Title Case convention used everywhere else in this file. A wrong guess
# here is harmless -- _auto_detect_entity_by_name() just finds zero
# matches and that field stays blank for manual pick, exactly as if this
# dict didn't have an entry for it -- but they should be swapped for
# confirmed names as soon as someone can check a real Growatt install
# (Kasper's own, if he ever gets one, or a report from another user).
#
# Not every Core field has a known equivalent for every brand -- left out
# rather than guessed (e.g. no confirmed Growatt equivalent for the two
# battery charge/discharge *energy* totals, as opposed to power).
# ---------------------------------------------------------------------------

INVERTER_CORE_AUTO_DETECT_NAMES: dict[str, dict[str, tuple[str, str]]] = {
    "Kostal Plenticore": {
        ENTITY_KEY_BATTERY_SOC: ("kostal_plenticore", "Battery SoC"),
        ENTITY_KEY_BATTERY_PV_CHARGED: ("kostal_plenticore", "Battery Charge from PV Total"),
        ENTITY_KEY_BATTERY_PV_DISCHARGED: ("kostal_plenticore", "Battery Discharge Total"),
        ENTITY_KEY_BATTERY_POWER: ("kostal_plenticore", "Battery Power"),
        # Daily counter (resets at local midnight). Confirmed live
        # 2026-09-28 on Kasper's install
        # (sensor.pileaas_sol_battery_charge_from_grid_day).
        ENTITY_KEY_BATTERY_GRID_CHARGED_DAY: ("kostal_plenticore", "Battery Charge from Grid Day"),
        ENTITY_KEY_PV1_POWER: ("kostal_plenticore", "DC1 Power"),
        ENTITY_KEY_PV2_POWER: ("kostal_plenticore", "DC2 Power"),
        ENTITY_KEY_PV3_POWER: ("kostal_plenticore", "DC3 Power"),
        ENTITY_KEY_PV_DIRECT_CONSUMPTION: ("kostal_plenticore", "Home Power from PV"),
        # "from PV" -- NOT plain "Home Consumption Total", which is the
        # whole house (PV + battery + grid) and belongs to
        # ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL below. This entry used to
        # point at "Home Consumption Total" by mistake; caught 2026-09-27
        # against Kasper's live install, where his own manual picks were
        # already right (pv_total_consumption mirror = 11669.2 =
        # "Home Consumption from PV Total"; property baseline tracks
        # "Home Consumption Total" = 37536.9). His install was never
        # affected since auto-detect never overwrites an existing pick.
        ENTITY_KEY_PV_TOTAL_CONSUMPTION: ("kostal_plenticore", "Home Consumption from PV Total"),
        ENTITY_KEY_GRID_POWER: ("kostal_plenticore", "Grid Power"),
        # Raw lifetime counter (state_class total_increasing, never resets)
        # -- exactly what data/consumption_data.py needs. Confirmed
        # live 2026-09-27 on Kasper's install (sensor.pileaas_sol_home_consumption_total).
        ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL: ("kostal_plenticore", "Home Consumption Total"),
    },
    "Growatt": {
        # -- best-effort, unverified -- see the dict's own docstring above.
        ENTITY_KEY_BATTERY_SOC: ("growatt_server", "Battery percentage"),
        ENTITY_KEY_BATTERY_POWER: ("growatt_server", "Storage charging/discharging"),
        # From Home Assistant's own growatt_server strings.json
        # (storage_grid_charged_today), NOT checked against a real install.
        # TLX/MIN inverters name theirs "Batteries charged from grid today"
        # (tlx_batteries_charged_from_grid_today) instead -- also
        # unverified; this dict only holds one name per field, so a TLX
        # install has to pick it by hand for now.
        ENTITY_KEY_BATTERY_GRID_CHARGED_DAY: ("growatt_server", "Grid charged today"),
        ENTITY_KEY_PV1_POWER: ("growatt_server", "Input 1 Wattage"),
        ENTITY_KEY_PV2_POWER: ("growatt_server", "Input 2 Wattage"),
        ENTITY_KEY_PV_DIRECT_CONSUMPTION: ("growatt_server", "Solar power production"),
        ENTITY_KEY_PV_TOTAL_CONSUMPTION: ("growatt_server", "Load consumption"),
        ENTITY_KEY_GRID_POWER: ("growatt_server", "Import from grid"),
        # No ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL guess: it must be a raw,
        # never-resetting lifetime counter, and no Growatt sensor name is
        # known to be one -- left for manual pick rather than risk
        # auto-filling a daily-resetting sensor.
    },
}

# ---------------------------------------------------------------------------
# Segments: optional areas of PwMngt a given installation may not need.
# "Power Management" itself isn't in this list -- it's mandatory and always
# configured, the segments below are opt-in on top of it.
# ---------------------------------------------------------------------------

CONF_SEGMENTS = "segments"

SEGMENT_EV_CHARGING = "ev_charging"
SEGMENT_POOL = "pool"
SEGMENT_PV_SURPLUS = "pv_surplus"

# ---------------------------------------------------------------------------
# Restore-state handling, shared across the hub.
#
# If Home Assistant was down longer than this, any RestoreEntity's stashed
# state is too stale to resume into -- it would splice pre-outage and
# post-outage readings together as if no time had passed. Past this age,
# affected entities should start fresh instead of resuming from the
# restored state (e.g. data/balance_data.py's PwMngtBalanceDataSensor). This is
# a hub-wide constant so every "_restore"-style mechanism in PwMngt applies
# the same cutoff -- don't give a restore mechanism its own local copy of
# this value.
# ---------------------------------------------------------------------------

RESTORE_MAX_AGE = timedelta(minutes=30)
