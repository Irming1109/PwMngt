"""Select entities for PwMngt chargers.

Scaffolding only: choosing an option stores it locally and nothing
downstream reacts to it yet. Real behaviour comes in a later step.

Option lists below were read directly from the "options" attribute of the
matching Node-RED Companion select entities for Ladeboks 1 (via Developer
Tools -> Template, read-only), then translated to English.
"""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from .base import PwMngtSelectEntityDescription
from .chargers import CHARGERS, charger_device_info

LOGGER = logging.getLogger(__name__)

# Shown under the device's "Configuration" tab (set once, rarely changed).
CONFIG_SELECTS: list[PwMngtSelectEntityDescription] = [
    PwMngtSelectEntityDescription(
        key="setup_type",
        name="Setup type",
        icon="mdi:ev-station",
        entity_category=EntityCategory.CONFIG,
        options=["Not installed", "Wallbox", "Easee"],
        default_option="Wallbox",
    ),
    PwMngtSelectEntityDescription(
        key="ordered_phases",
        name="Ordered phases",
        icon="mdi:sine-wave",
        entity_category=EntityCategory.CONFIG,
        options=["1", "3"],
        default_option="3",
    ),
    PwMngtSelectEntityDescription(
        key="max_manual_charge_current",
        name="Max manual charge current",
        icon="mdi:current-ac",
        entity_category=EntityCategory.CONFIG,
        options=["6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16"],
        default_option="16",
    ),
    PwMngtSelectEntityDescription(
        key="efficiency",
        name="Efficiency",
        icon="mdi:gauge",
        entity_category=EntityCategory.CONFIG,
        options=["4", "4.5", "5", "5.5", "6", "6.5", "7", "7.5", "8"],
        default_option="5",
    ),
]

# Shown on the main entity list (day-to-day values).
SELECTS: list[PwMngtSelectEntityDescription] = [
    PwMngtSelectEntityDescription(
        key="charge_period",
        name="Charge period",
        icon="mdi:calendar-clock",
        options=["Weekdays", "Every day"],
        default_option="Weekdays",
    ),
    PwMngtSelectEntityDescription(
        key="daily_charge_limit",
        name="Daily charge limit",
        icon="mdi:battery-clock",
        options=["None", "20", "40", "50", "60", "70", "80", "90", "100", "125", "150", "175", "Full"],
        default_option="None",
    ),
    PwMngtSelectEntityDescription(
        key="minimum_range",
        name="Minimum range",
        icon="mdi:map-marker-distance",
        options=["None", "50", "75", "100", "125", "150", "175", "200", "250", "Full"],
        default_option="None",
    ),
    PwMngtSelectEntityDescription(
        key="defer_surplus_charging",
        name="Defer surplus charging",
        icon="mdi:solar-power",
        options=["Inactive", "4 hours", "Until tomorrow"],
        default_option="Inactive",
    ),
    PwMngtSelectEntityDescription(
        key="surplus_charging_active",
        name="Surplus charging active",
        icon="mdi:solar-power",
        options=["No", "Yes"],
        default_option="No",
    ),
    PwMngtSelectEntityDescription(
        key="topup",
        name="Top-up",
        icon="mdi:battery-plus",
        options=["Yes", "No"],
        default_option="No",
    ),
    PwMngtSelectEntityDescription(
        key="phase_switch",
        name="Phase switch",
        icon="mdi:swap-horizontal",
        options=["No", "Yes", "Yes (active)"],
        default_option="No",
    ),
    PwMngtSelectEntityDescription(
        key="startup",
        name="Startup",
        icon="mdi:power",
        options=["No", "Yes"],
        default_option="No",
    ),
    PwMngtSelectEntityDescription(
        key="command",
        name="Command",
        icon="mdi:remote",
        options=["Inactive", "Start", "Stop", "Resume", "Phase switch 1-phase", "Phase switch 3-phase", "Deferred"],
        default_option="Inactive",
    ),
    PwMngtSelectEntityDescription(
        key="state",
        name="State",
        icon="mdi:information-outline",
        options=["No car connected", "Inactive", "Paused", "Active - solar", "Active - manual", "No demand", "Unknown"],
        default_option="Paused",
    ),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up select entities for each configured charger."""
    entities = []

    for charger in CHARGERS:
        for description in CONFIG_SELECTS + SELECTS:
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
        charger: dict,
    ) -> None:
        self.entity_description = description
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
