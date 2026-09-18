"""Text entities for PwMngt chargers.

Scaffolding only: setting the value stores it locally and nothing
downstream reacts to it yet. Real behaviour comes in a later step.
"""

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from .base import PwMngtTextEntityDescription
from .chargers import CHARGERS, charger_device_info

LOGGER = logging.getLogger(__name__)

CONFIG_TEXTS: list[PwMngtTextEntityDescription] = [
    PwMngtTextEntityDescription(
        key="serial_number",
        name="Serial number",
        icon="mdi:identifier",
        entity_category=EntityCategory.CONFIG,
        default_value="",
    ),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up text entities for each configured charger."""
    entities = []

    for charger in CHARGERS:
        for description in CONFIG_TEXTS:
            entities.append(PwMngtText(description, entry, charger))

    async_add_entities(entities)


class PwMngtText(TextEntity):
    """A scaffolded PwMngt text entity. No live behaviour yet."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: PwMngtTextEntityDescription,
        entry: ConfigEntry,
        charger: dict,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{charger['id']}_{description.key}"
        self._attr_device_info = charger_device_info(entry, charger)
        self._attr_native_value = description.default_value

    async def async_set_value(self, value: str) -> None:
        """Store the value locally. Functionality is not implemented yet."""
        LOGGER.info(
            "%s set to '%s' (no functionality implemented yet)", self.entity_id, value
        )
        self._attr_native_value = value
        self.async_write_ha_state()
