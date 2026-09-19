"""Select entities for PwMngt chargers.

Scaffolding only: choosing an option stores it locally and nothing
downstream reacts to it yet. Real behaviour comes in a later step.

Option lists below were read directly from the "options" attribute of the
matching Node-RED Companion select entities for Ladeboks 1 (via Developer
Tools -> Template, read-only), then translated to English.

Note: "ordered_phases", "state" and "surplus_charging_active" used to live
here but were moved to sensor.py as read-only ENUM sensors. They are values
the automation reports (not something the user picks from a dropdown), so
Select was the wrong entity type for them -- see CHARGER_SENSORS in
sensor.py instead.
"""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from .base import PwMngtSelectEntityDescription
from .devices import CHARGERS, charger_device_info, hub_device_info, pv_device_info

LOGGER = logging.getLogger(__name__)

# Shown once, under the main "PwM" hub device's "Configuration" tab --
# not tied to a specific charger.
PwM_CONFIG_SELECTS: list[PwMngtSelectEntityDescription] = [
    # Old key="ladeboks_1_opsaetning"
    # Old name="Ladeboks 1 opsætning"
    PwMngtSelectEntityDescription(
        key="charger1_type",
        name="Charger1 type",
        icon="mdi:ev-station",
        entity_category=EntityCategory.CONFIG,
        options=["Not installed", "Wallbox", "Easee"],
        default_option="Wallbox",
    ),
    # Old key="ladeboks_2_opsaetning"
    # Old name="Ladeboks 2 opsætning"
    PwMngtSelectEntityDescription(
        key="charger2_type",
        name="Charger2 type",
        icon="mdi:ev-station",
        entity_category=EntityCategory.CONFIG,
        options=["Not installed", "Wallbox", "Easee"],
        default_option="Wallbox",
    ),
    # Old key="ladebokse_prioritet"
    # Old name="Ladebokse prioritet"
    PwMngtSelectEntityDescription(
        key="charger_priority",
        name="Charger priority",
        icon="mdi:sort-numeric-ascending",
        entity_category=EntityCategory.CONFIG,
        options=["Charger1", "Charger2"],
        default_option="Charger1",
    ),
    # Old key="ladebokse_min_andel_af_solcelle_strom"
    # Old name="Ladebokse min. andel af solcelle strøm (%)"
    PwMngtSelectEntityDescription(
        key="minimum_solar_power_to_charge",
        name="Minimum solar power to charge (%)",
        icon="mdi:solar-power",
        entity_category=EntityCategory.CONFIG,
        options=["90", "80", "70", "60", "50", "40", "30", "20", "10"],
        default_option="90",
    ),
    # Old key="ladebokse_max_afladning_fra_solcelle_batteri"
    # Old name="Ladebokse max afladning fra solcelle batteri"
    PwMngtSelectEntityDescription(
        key="start_charging_at_battery_capacity",
        name="Start charging at battery capacity (%)",
        icon="mdi:battery-arrow-down",
        entity_category=EntityCategory.CONFIG,
        options=["Not installed", "95", "90", "85", "80", "75", "70", "65", "60"],
        default_option="85",
    ),
    # Old key="ladebokse_minimum_pa_solcelle_batteri"
    # Old name="Ladebokse minimum på solcelle batteri"
    PwMngtSelectEntityDescription(
        key="stop_charging_at_battery_capacity",
        name="Stop charging at battery capacity (%)",
        icon="mdi:battery-arrow-up",
        entity_category=EntityCategory.CONFIG,
        options=["Not installed", "95", "90", "85", "80", "75", "70", "65", "60"],
        default_option="95",
    ),
    # Old key="ladebokse_max_belastning"
    # Old name="Ladebokse max belastning"
    PwMngtSelectEntityDescription(
        key="installation_max_load",
        name="Installation max load",
        icon="mdi:fuse",
        entity_category=EntityCategory.CONFIG,
        options=["16", "20", "25", "30"],
        default_option="16",
    ),
    # Old key="ladebokse_pv_styring"
    # Old name="ladebokse:_pv_styring"
    PwMngtSelectEntityDescription(
        key="pv_control",
        name="PV control",
        icon="mdi:solar-power-variant",
        entity_category=EntityCategory.CONFIG,
        options=["Auto", "500", "1000", "2000", "3000"],
        default_option="Auto",
    ),
]

# Shown once, under the "PwM_PV" solar PV system device's "Configuration"
# tab -- a device related to the hub, for the solar PV system specifically.
# Read from the "Solcelle anlaeg" card on the Konfiguration dashboard, then
# translated to English. Three of the four live source entities are
# input_select helpers (not Node-RED-provided), the fourth is a real select.
PwM_PV_CONFIG_SELECTS: list[PwMngtSelectEntityDescription] = [
    # Old key="battery_size" (input_select.battery_size)
    # Old name="Batteri antal kWh"
    PwMngtSelectEntityDescription(
        key="battery_size",
        name="Battery size (kWh)",
        icon="mdi:home-battery",
        entity_category=EntityCategory.CONFIG,
        options=["None", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
                 "11", "12", "13", "14", "15", "16", "17", "18", "19", "20"],
        default_option="8",
    ),
    # Old key="history_period_days" (input_select.historik_periode)
    # Old name="Antal dage til historik"
    PwMngtSelectEntityDescription(
        key="history_period_days",
        name="History period (days)",
        icon="mdi:history",
        entity_category=EntityCategory.CONFIG,
        options=["7", "14", "21", "30"],
        default_option="7",
    ),
    # Old key="solar_inverter_max_ac_kw" (input_select.sol_inverter_max)
    # Old name="Sol inverter max ydeevne AC KW"
    PwMngtSelectEntityDescription(
        key="solar_inverter_max_ac_kw",
        name="Solar inverter max AC output (kW)",
        icon="mdi:solar-power",
        entity_category=EntityCategory.CONFIG,
        options=["0", "3", "4", "5", "6", "7", "8", "9", "10"],
        default_option="5",
    ),
    # Old key="pv_batteri_pris_difference"
    # Old name="pv batteri pris difference"
    PwMngtSelectEntityDescription(
        key="pv_battery_price_difference",
        name="PV battery price difference",
        icon="mdi:currency-usd",
        entity_category=EntityCategory.CONFIG,
        options=["0.20", "0.30", "0.40", "0.50", "0.60", "0.70", "0.80", "0.90",
                 "1.00", "1.10", "1.20", "1.30", "1.40", "1.50", "1.60", "1.70",
                 "1.80", "1.90", "2.00"],
        default_option="0.80",
    ),
]

# Shown under each charger device's "Configuration" tab (set once, rarely changed).
PwM_CHARGER_CONFIG_SELECTS: list[PwMngtSelectEntityDescription] = [
    # Old key="ladeboks_1_effektivitet" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 effektivitet" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="driving_distance_km_per_kwh",
        name="Driving distance in Km per KwH",
        icon="mdi:gauge",
        entity_category=EntityCategory.CONFIG,
        options=["4", "4.5", "5", "5.5", "6", "6.5", "7", "7.5", "8"],
        default_option="5",
    ),
    # Old key="ladeboks_1_daglig_opladning" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 daglig opladning" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="daily_charge_limit",
        name="Daily charge limit",
        icon="mdi:battery-clock",
        entity_category=EntityCategory.CONFIG,
        options=["None", "20", "40", "50", "60", "70", "80", "90", "100", "125", "150", "175", "Full"],
        default_option="None",
    ),
    # Old key="ladeboks_1_minimum_raekkeevne" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_minimum_rækkeevne" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="minimum_range",
        name="Minimum range",
        icon="mdi:map-marker-distance",
        entity_category=EntityCategory.CONFIG,
        options=["None", "50", "75", "100", "125", "150", "175", "200", "250", "Full"],
        default_option="None",
    ),
    # Old key="ladeboks_1_periode" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 periode" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="charge_period",
        name="Charge period",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.CONFIG,
        options=["Weekdays", "Every day"],
        default_option="Weekdays",
    ),
    # Old key="ladeboks_1_udskyd_pv" (ladeboks_2_... equivalent for Charger2)
    # Old name="ladeboks_1_udskyd_pv" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="defer_surplus_charging",
        name="Defer surplus charging",
        icon="mdi:solar-power",
        entity_category=EntityCategory.CONFIG,
        options=["Inactive", "4 hours", "Until tomorrow"],
        default_option="Inactive",
    ),
    # Old key="ladeboks_1_max_ampere_manuel_ladning" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 max ampere manuel ladning" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="charger_max_load",
        name="Charger max load",
        icon="mdi:current-ac",
        entity_category=EntityCategory.CONFIG,
        options=["6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16"],
        default_option="16",
    ),

]

# Shown on the main entity list (day-to-day values).
PwM_CHARGER_SELECTS: list[PwMngtSelectEntityDescription] = [
    # Old key="ladeboks_1_efterfyld" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_efterfyld" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="topup",
        name="Top-up",
        icon="mdi:battery-plus",
        options=["Yes", "No"],
        default_option="No",
    ),
    # NOTE: the "Yes (active)" option suggests this may mix a user command
    # (No/Yes) with an automation-reported status (currently switched). If
    # that is confirmed, this should likely be split into a settable
    # select/switch plus a separate read-only status sensor, similar to the
    # ordered_phases/state/surplus_charging_active move above.
    # Old key="ladeboks_1_faseskift" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 faseskift" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="phase_switch",
        name="Phase switch",
        icon="mdi:swap-horizontal",
        options=["No", "Yes", "Yes (active)"],
        default_option="No",
    ),
    # Old key="ladeboks_1_opstart" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 opstart" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="startup",
        name="Startup",
        icon="mdi:power",
        options=["No", "Yes"],
        default_option="No",
    ),
    # Old key="ladeboks_1_kommando" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 kommando" (Ladeboks 2 ... equivalent for Charger2)
    PwMngtSelectEntityDescription(
        key="command",
        name="Command",
        icon="mdi:remote",
        options=["Inactive", "Start", "Stop", "Resume", "Phase switch 1-phase", "Phase switch 3-phase", "Deferred"],
        default_option="Inactive",
    ),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up select entities for the hub, the PV device, and each configured charger."""
    entities = []

    for description in PwM_CONFIG_SELECTS:
        entities.append(PwMngtSelect(description, entry))

    for description in PwM_PV_CONFIG_SELECTS:
        entities.append(PwMngtSelect(description, entry, pv=True))

    for charger in CHARGERS:
        for description in PwM_CHARGER_CONFIG_SELECTS + PwM_CHARGER_SELECTS:
            entities.append(PwMngtSelect(description, entry, charger))

    async_add_entities(entities)


class PwMngtSelect(SelectEntity):
    """A scaffolded PwMngt select entity. No live behaviour yet."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: PwMngtSelectEntityDescription,
        entry: ConfigEntry,
        charger: dict | None = None,
        pv: bool = False,
    ) -> None:
        self.entity_description = description
        if pv:
            # PV-device-level entity: belongs to the related "PwM_PV" device.
            self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
            self._attr_device_info = pv_device_info(entry)
        elif charger is None:
            # Hub-level entity: belongs to the general "PwM" device.
            self._attr_unique_id = f"{entry.entry_id}_{description.key}"
            self._attr_device_info = hub_device_info(entry)
        else:
            self._attr_unique_id = f"{entry.entry_id}_{charger['id']}_{description.key}"
            self._attr_device_info = charger_device_info(entry, charger)
        self._attr_current_option = description.default_option

    async def async_select_option(self, option: str) -> None:
        """Store the chosen option locally. Functionality is not implemented yet."""
        LOGGER.info(
            "%s set to '%s' (no functionality implemented yet)", self.entity_id, option
        )
        self._attr_current_option = option
        self.async_write_ha_state()
