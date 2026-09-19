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
from .chargers import CHARGERS, charger_device_info, hub_device_info

LOGGER = logging.getLogger(__name__)

# Shown once, under the main "PwM" hub device's "Configuration" tab --
# not tied to a specific charger.
PwM_CONFIG_TEXTS: list[PwMngtTextEntityDescription] = [
]

# Shown under each charger device's "Configuration" tab.
PwM_CHARGER_CONFIG_TEXTS: list[PwMngtTextEntityDescription] = [
    # Old key="ladeboks_1_easee_sn"
    # Old name="Ladeboks_1_Easee_SN"
    PwMngtTextEntityDescription(
        key="serial_number",
        name="Serial number",
        icon="mdi:identifier",
        entity_category=EntityCategory.CONFIG,
        default_value="",
    ),
    # Read live from the "Ladestander konfiguration" card on the
    # Konfiguration dashboard, then translated to English.
    # Old key="ladeboks_2_easee_sn"
    # Old name="Ladeboks_2_Easee_SN"
    PwMngtTextEntityDescription(
        key="charger2_easee_serial_number",
        name="Charger2 Easee serial number",
        icon="mdi:identifier",
        entity_category=EntityCategory.CONFIG,
        default_value="",
    ),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up text entities for the hub device and each configured charger."""
    entities = []

    for description in PwM_CONFIG_TEXTS:
        entities.append(PwMngtText(description, entry))

    for charger in CHARGERS:
        for description in PwM_CHARGER_CONFIG_TEXTS:
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
        charger: dict | None = None,
    ) -> None:
        self.entity_description = description
        if charger is None:
            # Hub-level entity: belongs to the general "PwM" device.
            self._attr_unique_id = f"{entry.entry_id}_{description.key}"
            self._attr_device_info = hub_device_info(entry)
        else:
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
