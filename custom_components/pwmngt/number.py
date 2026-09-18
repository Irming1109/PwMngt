"""Number entities for PwMngt chargers.

Scaffolding only: pressing/changing these stores the value locally and
nothing downstream reacts to it yet. Real behaviour comes in a later step.

min/max/step/unit below were read directly from the matching Node-RED
Companion number entities for Ladeboks 1 (via Developer Tools -> Template,
read-only).
"""

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from .base import PwMngtNumberEntityDescription
from .chargers import CHARGERS, charger_device_info

LOGGER = logging.getLogger(__name__)

# Shown under the device's "Configuration" tab (set once, rarely changed).
PwM_CHARGER_CONFIG_NUMBERS: list[PwMngtNumberEntityDescription] = [
    PwMngtNumberEntityDescription(
        key="phase_count",
        name="Phase count",
        icon="mdi:sine-wave",
        entity_category=EntityCategory.CONFIG,
        native_min_value=1,
        native_max_value=3,
        native_step=1,
        default_value=3,
    ),
]

# Shown on the main entity list (day-to-day values).
PwM_CHARGER_NUMBERS: list[PwMngtNumberEntityDescription] = [
    PwMngtNumberEntityDescription(
        key="battery_charge",
        name="Battery charge",
        icon="mdi:battery-charging",
        native_unit_of_measurement="%",
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        default_value=0,
    ),
    PwMngtNumberEntityDescription(
        key="charge_now_amount",
        name="Charge now amount",
        icon="mdi:ev-station",
        native_min_value=0,
        native_max_value=1000,
        native_step=5,
        default_value=0,
    ),
    PwMngtNumberEntityDescription(
        key="charge_tonight_amount",
        name="Charge tonight amount",
        icon="mdi:weather-night",
        native_min_value=0,
        native_max_value=1000,
        native_step=1,
        default_value=0,
    ),
    PwMngtNumberEntityDescription(
        key="estimated_range",
        name="Estimated range",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement="km",
        native_min_value=0,
        native_max_value=1000,
        native_step=1,
        default_value=0,
    ),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up number entities for each configured charger."""
    entities = []

    for charger in CHARGERS:
        for description in PwM_CHARGER_CONFIG_NUMBERS + PwM_CHARGER_NUMBERS:
            entities.append(PwMngtNumber(description, entry, charger))

    async_add_entities(entities)


class PwMngtNumber(NumberEntity):
    """A scaffolded PwMngt number entity. No live behaviour yet."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False

    def __init__(
        self,
        description: PwMngtNumberEntityDescription,
        entry: ConfigEntry,
        charger: dict,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{charger['id']}_{description.key}"
        self._attr_device_info = charger_device_info(entry, charger)
        self._attr_native_value = description.default_value

    async def async_set_native_value(self, value: float) -> None:
        """Store the value locally. Functionality is not implemented yet."""
        LOGGER.info(
            "%s set to %s (no functionality implemented yet)", self.entity_id, value
        )
        self._attr_native_value = value
        self.async_write_ha_state()
