"""Button entities for PwMngt chargers.

Scaffolding only: pressing a button logs that it was pressed. No action is
actually sent anywhere yet -- that comes in a later step.
"""

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .chargers import CHARGERS, charger_device_info

LOGGER = logging.getLogger(__name__)

BUTTONS: list[ButtonEntityDescription] = [
    ButtonEntityDescription(key="charge_tonight_5a", name="Charge tonight 5A", icon="mdi:weather-night"),
    ButtonEntityDescription(key="charge_tonight_10a", name="Charge tonight 10A", icon="mdi:weather-night"),
    ButtonEntityDescription(key="charge_tonight_50a", name="Charge tonight 50A", icon="mdi:weather-night"),
    ButtonEntityDescription(key="charge_tonight_reset", name="Charge tonight reset", icon="mdi:restore"),
    ButtonEntityDescription(key="charge_now_5a", name="Charge now 5A", icon="mdi:ev-station"),
    ButtonEntityDescription(key="charge_now_10a", name="Charge now 10A", icon="mdi:ev-station"),
    ButtonEntityDescription(key="charge_now_50a", name="Charge now 50A", icon="mdi:ev-station"),
    ButtonEntityDescription(key="charge_now_full", name="Charge now full", icon="mdi:battery-charging-100"),
    ButtonEntityDescription(key="charge_now_reset", name="Charge now reset", icon="mdi:restore"),
    ButtonEntityDescription(key="discharge_battery_5a", name="Discharge battery 5A", icon="mdi:battery-arrow-down"),
    ButtonEntityDescription(key="discharge_battery_10a", name="Discharge battery 10A", icon="mdi:battery-arrow-down"),
    ButtonEntityDescription(key="discharge_battery_reset", name="Discharge battery reset", icon="mdi:restore"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up button entities for each configured charger."""
    entities = []

    for charger in CHARGERS:
        for description in BUTTONS:
            entities.append(PwMngtButton(description, entry, charger))

    async_add_entities(entities)


class PwMngtButton(ButtonEntity):
    """A scaffolded PwMngt button entity. No live behaviour yet."""

    _attr_has_entity_name = True

    def __init__(
        self,
        description: ButtonEntityDescription,
        entry: ConfigEntry,
        charger: dict,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{charger['id']}_{description.key}"
        self._attr_device_info = charger_device_info(entry, charger)

    async def async_press(self) -> None:
        """Log the press. Functionality is not implemented yet."""
        LOGGER.info(
            "%s pressed (no functionality implemented yet)", self.entity_id
        )
