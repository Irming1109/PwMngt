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
#
# A brand can come from more than one integration: Growatt is either the
# official cloud integration (growatt_server) or the HACS integration
# 0xAHA/Growatt_ModbusTCP (growatt_modbus), which talks to the inverter
# directly -- Claus's install uses the latter.
# ---------------------------------------------------------------------------

GROWATT_MODBUS_PLATFORM = "growatt_modbus"

INVERTER_TYPE_INTEGRATION_DOMAINS: dict[str, tuple[str, ...]] = {
    "Kostal Plenticore": ("kostal_plenticore",),
    "Growatt": ("growatt_server", GROWATT_MODBUS_PLATFORM),
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

# Flat option (Claus, 2026-10-15) -- NOT an entity_map field: a plain
# number the user types in directly, not an entity to pick/mirror. Added
# on top of ENTITY_KEY_SPOT_ELECTRICITY_PRICE's value everywhere PwMngt
# calculates money from it (see properties/hub_properties.py's
# electricity_price()) -- Claus's own supplier charges a flat 0.05 kr/kWh
# + VAT (0.06 kr/kWh total) on top of what his configured Strømligning
# price sensor reports, which that sensor has no way to know about.
# kr/kWh, incl. VAT/moms. Defaults to 0.0 (no surcharge) when unset, so
# nothing changes for an install that doesn't need this.
CONF_ELECTRICITY_PRICE_SURCHARGE = "electricity_price_surcharge"

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
# Current load (A) on each phase of the property's main fuse. Optional --
# not every installation has a phase-current meter. Read by
# data/battery_charge_level.py (Bestem ladeeffekt) to cap how many amps
# the battery charger may add without tripping the main fuse; left empty
# in Options ("Not available"), each reads as 0 (same fallback Node-RED's
# own "parseFloat(...) || 0" already used for a missing sensor). Old:
# sensor.hovedsikring_l1/l2/l3.
ENTITY_KEY_MAIN_FUSE_LOAD_L1 = "main_fuse_load_l1"
ENTITY_KEY_MAIN_FUSE_LOAD_L2 = "main_fuse_load_l2"
ENTITY_KEY_MAIN_FUSE_LOAD_L3 = "main_fuse_load_l3"

# Optional "force sell" override -- a boolean entity (input_boolean,
# switch, or a boolean-ish binary_sensor) the user can point at any
# external automation of their own that wants to force the battery into
# selling mode (data/battery_sell_decision.py writes "block_charging" to
# battery_command) regardless of what that module's own price/forecast
# calculation would otherwise decide. One-way only -- it can only force
# selling, never force the opposite (data/battery_sell_decision.py simply
# never un-blocks while this reads "on"; it doesn't suppress an actual
# block). Left empty ("Not available"), it has no effect at all -- the
# module runs purely on its own calculation, as for an install with no
# such external automation. Not named after any specific external use
# case (e.g. Claus's own tenant-priority automation on Lundekrog, kept
# entirely in his own Node-RED) -- this is a generic hook, not a
# Lundekrog-specific feature.
ENTITY_KEY_SELL_FORCE_OVERRIDE = "sell_force_override"

# battery_nightly_target and solar_data's PV-forecast attributes are not
# entity_map fields -- PwMngt computes them internally (see
# data/solar_data.py).

# pv_history_period_days is also not an entity_map field -- it's a fixed
# entity (select.pwm_pv_history_period_days) the user sets through his own
# PwMPvCard dashboard card (www/pwm-cards.js).

# Hub segment -- an entity_map field like the Solar PV Plant ones, even
# though the sensor itself lives on the Hub device.
ENTITY_KEY_SPOT_ELECTRICITY_PRICE = "spot_electricity_price"

# ---------------------------------------------------------------------------
# Savings tracking -- entity_map fields read by data/savings_data.py, port
# of Claus's Node-RED functions "Beregn indkøbspris med og uden PV" and
# "Beregn balance på ladning af batteri fra grid". Entirely optional: not
# every install has the hourly utility_meter helpers this depends on.
# ENTITY_KEY_SPOT_ELECTRICITY_PRICE above doubles as the "reel" price here
# too (Claus, 2026-10-15: "det er det samme reelt set") -- no separate
# field for it.
# ---------------------------------------------------------------------------

# Dropped (Claus, 2026-10-15/16): ENTITY_KEY_PRICE_WITHOUT_PV, a plain
# entity_map field for Claus's own "uden PV" price sensor (Old: sensor.
# el_kobspris_udenpv). Turned out to be the wrong model entirely: the
# underlying commodity/spot price is identical in the "with PV" and
# "without PV" scenarios (the market price doesn't care how much solar you
# have) -- the only real difference is the electricity TAX (elafgift),
# because without PV you'd hit Denmark's tax threshold faster (same
# consumption, but all of it bought from the grid instead of partly
# self-produced). That's now calculated instead of read from a sensor --
# see ENTITY_KEY_ELECTRICITY_TAX_FULL_RATE below and savings_data.py's
# module docstring. (Same day, same reasoning as dropping ENTITY_KEY_
# PRICE_VARIABLE: a sensor that looked like a separate price turned out to
# have nothing genuinely separate behind it.)

# Denmark's electricity tax (elafgift) has a two-tier threshold: a "full"
# rate on the first N kWh purchased from the grid per billing period, then
# a much-reduced rate (currently ~0.01 kr/kWh incl. VAT -- see
# savings_data.py's _REDUCED_ELECTRICITY_TAX_RATE, hardcoded, not user-
# configurable, because Claus says this reduced rate is always this exact
# figure) on the rest. N is 4000 kWh/year, i.e. 333.3 kWh/month for a
# monthly-settled customer or 1000 kWh/quarter for quarterly-settled --
# which one applies is select.<hub>_billing_period (see hub_properties.
# billing_period_threshold_kwh()). The reduced tier currently applies to
# everyone regardless of consumption (no full-rate tier in effect as of
# 2026), but Claus expects the full-rate tier to return 2028-01-01 --
# select.<hub>_electricity_tax ("Full" vs. "Reduced (electric heating)")
# is the switch for whether this household gets the reduced tier at all;
# "Full" means every kWh is taxed at the full rate, no threshold math.
# Claus's own sensor for the *full* rate (there's no sensor for the
# reduced rate -- see above):
ENTITY_KEY_ELECTRICITY_TAX_FULL_RATE = "electricity_tax_full_rate"

# Four raw, ever-increasing lifetime energy counters (kWh) -- NOT a Home
# Assistant Utility Meter helper. Old: sensor.strom_forbrug_husstand_pr_
# time / sensor.strom_indkob / sensor.batteri_ladet_fra_grid_time /
# sensor.forbrug_fra_batteri_time (those were Claus's own Utility Meter
# sensors; dropped in favour of their underlying raw counters, Claus,
# 2026-10-16, so a hardware swap -- e.g. a new PV inverter -- doesn't
# force him to rebuild a Utility Meter helper too). data/savings_data.py
# reads each one's live .state and derives the hour's own delta itself,
# the same way data/consumption_data.py already does for
# ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL -- including handling a counter
# reset or an entity_map repoint without corrupting the running totals
# this feeds (see savings_data.py's read_counter_delta()).
ENTITY_KEY_HOUSEHOLD_CONSUMPTION_HOURLY = "household_consumption_hourly"
ENTITY_KEY_GRID_IMPORT_HOURLY = "grid_import_hourly"
ENTITY_KEY_BATTERY_GRID_CHARGE_HOURLY = "battery_grid_charge_hourly"
ENTITY_KEY_BATTERY_DISCHARGE_HOURLY = "battery_discharge_hourly"

# Seed fields (DKK) -- each optionally points at the matching sensor
# Claus's own Node-RED already maintains, so PwMngt's own running
# totals start from his real historical figures instead of 0, rather
# than silently resetting years of tracked savings. Applied as a high-
# water mark every time PwMngtSavingsDataSensor is added to hass (every
# HA restart or options reload, not just the very first run ever --
# Claus, 2026-10-16, see savings_data.py's _seed_ledger()): each field
# only ever moves the matching running total UP to the seed sensor's
# current value, never down -- so it's safe to leave these mapped
# indefinitely (an unavailable or decommissioned Node-RED sensor is
# just ignored) and safe to re-run the wizard as often as needed. Left
# unmapped, PwMngt simply keeps that running total at whatever it's
# already tracked on its own -- the normal, fully-supported state for
# a fresh install with no Node-RED history to carry over (Claus,
# 2026-10-15).
#
# No separate seed field for own_production_savings or
# battery_charging_savings (dropped, Claus, 2026-10-16): both are just
# max(0, bigger sum - smaller sum) of two other seeded totals, so seeding
# them from their own old Node-RED sensor risked a mismatch against
# whatever those two sums were seeded from. _seed_ledger() derives both
# with that same formula instead, right after seeding the four sums below.
ENTITY_KEY_SEED_PURCHASE_PRICE_REAL = "seed_purchase_price_real"
ENTITY_KEY_SEED_PURCHASE_PRICE_REAL_WITHOUT_PV = "seed_purchase_price_real_without_pv"
ENTITY_KEY_SEED_BATTERY_GRID_CHARGE_SUM = "seed_battery_grid_charge_sum"
ENTITY_KEY_SEED_BATTERY_GRID_DISCHARGE_SUM = "seed_battery_grid_discharge_sum"

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
# helpers/entity_registry_helper.py's find_entity_by_name() matches each entry against
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
# here is harmless -- find_entity_by_name() just finds zero
# matches and that field stays blank for manual pick, exactly as if this
# dict didn't have an entry for it -- but they should be swapped for
# confirmed names as soon as someone can check a real Growatt install
# (Kasper's own, if he ever gets one, or a report from another user).
#
# Not every Core field has a known equivalent for every brand -- left out
# rather than guessed (e.g. no confirmed Growatt equivalent for the two
# battery charge/discharge *energy* totals, as opposed to power).
#
# Shape: brand -> integration platform -> {Core field: original_name}. A
# brand with more than one integration (Growatt) lists each one's own
# names; whichever is installed is the one that matches.
#
# The growatt_modbus names are confirmed against Claus's own install
# (27-10-2026) and the integration's source (0xAHA/Growatt_ModbusTCP
# v2.0.5, sensor.py). It uses has_entity_name, so original_name is the
# name without the sub-device in front (device "Growatt Battery" + name
# "Battery SOC" -> friendly name "Growatt Battery Battery SOC").
# Battery PV charged, PV direct consumption and PV total consumption have
# no growatt_modbus sensor -- PwMngt calculates them itself when those
# fields are left empty (see data/inverter_derived_data.py).
# ---------------------------------------------------------------------------

INVERTER_CORE_AUTO_DETECT_NAMES: dict[str, dict[str, dict[str, str]]] = {
    "Kostal Plenticore": {
        "kostal_plenticore": {
            ENTITY_KEY_BATTERY_SOC: "Battery SoC",
            ENTITY_KEY_BATTERY_PV_CHARGED: "Battery Charge from PV Total",
            ENTITY_KEY_BATTERY_PV_DISCHARGED: "Battery Discharge Total",
            ENTITY_KEY_BATTERY_POWER: "Battery Power",
            # Daily counter (resets at local midnight). Confirmed live
            # 2026-09-28 on Kasper's install
            # (sensor.pileaas_sol_battery_charge_from_grid_day).
            ENTITY_KEY_BATTERY_GRID_CHARGED_DAY: "Battery Charge from Grid Day",
            ENTITY_KEY_PV1_POWER: "DC1 Power",
            ENTITY_KEY_PV2_POWER: "DC2 Power",
            ENTITY_KEY_PV3_POWER: "DC3 Power",
            ENTITY_KEY_PV_DIRECT_CONSUMPTION: "Home Power from PV",
            # "from PV" -- NOT plain "Home Consumption Total", which is the
            # whole house (PV + battery + grid) and belongs to
            # ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL below. This entry used to
            # point at "Home Consumption Total" by mistake; caught 2026-09-27
            # against Kasper's live install, where his own manual picks were
            # already right (pv_total_consumption mirror = 11669.2 =
            # "Home Consumption from PV Total"; property baseline tracks
            # "Home Consumption Total" = 37536.9). His install was never
            # affected since auto-detect never overwrites an existing pick.
            ENTITY_KEY_PV_TOTAL_CONSUMPTION: "Home Consumption from PV Total",
            ENTITY_KEY_GRID_POWER: "Grid Power",
            # Raw lifetime counter (state_class total_increasing, never resets)
            # -- exactly what data/consumption_data.py needs. Confirmed
            # live 2026-09-27 on Kasper's install (sensor.pileaas_sol_home_consumption_total).
            ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL: "Home Consumption Total",
        },
    },
    "Growatt": {
        GROWATT_MODBUS_PLATFORM: {
            ENTITY_KEY_BATTERY_SOC: "Battery SOC",
            ENTITY_KEY_BATTERY_PV_DISCHARGED: "Battery Discharge Total",
            # Positive = charging (opposite of Kostal) -- see
            # SIGN_INVERTED_PLATFORMS below.
            ENTITY_KEY_BATTERY_POWER: "Battery Power",
            # Grid -> battery today, resets at local midnight.
            ENTITY_KEY_BATTERY_GRID_CHARGED_DAY: "AC Charge Energy Today",
            ENTITY_KEY_PV1_POWER: "PV1 Power",
            ENTITY_KEY_PV2_POWER: "PV2 Power",
            ENTITY_KEY_PV3_POWER: "PV3 Power",
            # Positive = export (opposite of Kostal) -- see
            # SIGN_INVERTED_PLATFORMS below.
            ENTITY_KEY_GRID_POWER: "Grid Power",
            # Lifetime counter of everything the house used (PV + battery
            # + grid), total_increasing.
            ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL: "Load Energy Total",
        },
        # -- best-effort, unverified -- see the dict's own docstring above.
        "growatt_server": {
            ENTITY_KEY_BATTERY_SOC: "Battery percentage",
            ENTITY_KEY_BATTERY_POWER: "Storage charging/discharging",
            # From Home Assistant's own growatt_server strings.json
            # (storage_grid_charged_today), NOT checked against a real install.
            # TLX/MIN inverters name theirs "Batteries charged from grid today"
            # (tlx_batteries_charged_from_grid_today) instead -- also
            # unverified; this dict only holds one name per field, so a TLX
            # install has to pick it by hand for now.
            ENTITY_KEY_BATTERY_GRID_CHARGED_DAY: "Grid charged today",
            ENTITY_KEY_PV1_POWER: "Input 1 Wattage",
            ENTITY_KEY_PV2_POWER: "Input 2 Wattage",
            ENTITY_KEY_PV_DIRECT_CONSUMPTION: "Solar power production",
            ENTITY_KEY_PV_TOTAL_CONSUMPTION: "Load consumption",
            ENTITY_KEY_GRID_POWER: "Import from grid",
            # No ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL guess: it must be a raw,
            # never-resetting lifetime counter, and no Growatt sensor name is
            # known to be one -- left for manual pick rather than risk
            # auto-filling a daily-resetting sensor.
        },
    },
}

# ---------------------------------------------------------------------------
# Sign convention for the two signed power fields. PwMngt expects Grid
# power positive = buying from the grid and Battery power positive =
# discharging (what Kostal Plenticore reports, and what
# data/balance_data.py adds up). An inverter that reports the opposite
# gets the matching "invert" option ticked in the wizard; every reader of
# these two fields goes through pv_properties.power_sign().
#
# SIGN_INVERTED_PLATFORMS: integrations known to report the opposite --
# the wizard ticks the option when it picks a source from one of them.
# growatt_modbus: Grid Power positive = export, Battery Power positive =
# charging (its own sensor descriptions; Claus's Node-RED flips the
# battery too, node "Batteri effekt" in "Ha data - opsaetning").
# ---------------------------------------------------------------------------

CONF_INVERT_GRID_POWER = "invert_grid_power"
CONF_INVERT_BATTERY_POWER = "invert_battery_power"

POWER_SIGN_OPTIONS: dict[str, str] = {
    ENTITY_KEY_GRID_POWER: CONF_INVERT_GRID_POWER,
    ENTITY_KEY_BATTERY_POWER: CONF_INVERT_BATTERY_POWER,
}

SIGN_INVERTED_PLATFORMS = frozenset({GROWATT_MODBUS_PLATFORM})

# growatt_modbus sensors (original_name) that data/inverter_derived_data.py
# calculates Battery PV charged and PV direct consumption from, when those
# fields are left empty. Looked up by (platform, name) like the auto-detect
# above -- never by entity_id.
GROWATT_MODBUS_BATTERY_CHARGE_TOTAL = "Battery Charge Total"
GROWATT_MODBUS_AC_CHARGE_ENERGY_TOTAL = "AC Charge Energy Total"
GROWATT_MODBUS_HOUSE_CONSUMPTION = "House Consumption"

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

# The SoC (%) PwMngt's battery calculations treat as "empty" until
# select.<pv>_battery_min_soc has a value. 5 is what Node-RED hardcodes
# everywhere -- see pv_properties.battery_min_soc().
DEFAULT_BATTERY_MIN_SOC = 5.0
