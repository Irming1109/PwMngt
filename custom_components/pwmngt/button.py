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

PwM_CHARGER_BUTTONS: list[ButtonEntityDescription] = [
    # Old key="ladeboks_1_lad_nat_5" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nat_5" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_tonight_5a", name="Charge tonight 5A", icon="mdi:weather-night"),
    # Old key="ladeboks_1_lad_nat_10" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nat_10" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_tonight_10a", name="Charge tonight 10A", icon="mdi:weather-night"),
    # Old key="ladeboks_1_lad_nat_50" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nat_50" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_tonight_50a", name="Charge tonight 50A", icon="mdi:weather-night"),
    # Old key="ladeboks_1_lad_nat_reset" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nat_reset" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_tonight_reset", name="Charge tonight reset", icon="mdi:restore"),
    # Old key="ladeboks_1_lad_nu_5" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_5" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_5a", name="Charge now 5A", icon="mdi:ev-station"),
    # Old key="ladeboks_1_lad_nu_10" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_10" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_10a", name="Charge now 10A", icon="mdi:ev-station"),
    # Old key="ladeboks_1_lad_nu_50" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_50" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_50a", name="Charge now 50A", icon="mdi:ev-station"),
    # Old key="ladeboks_1_lad_nu_fuld" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_fuld" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_full", name="Charge now full", icon="mdi:battery-charging-100"),
    # Old key="ladeboks_1_lad_nu_reset" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_reset" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_reset", name="Charge now reset", icon="mdi:restore"),
    # Old key="ladeboks_1_tom_batteri_5" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 tom batteri 5" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="discharge_battery_5a", name="Discharge battery 5A", icon="mdi:battery-arrow-down"),
    # Old key="ladeboks_1_tom_batteri_10" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 tom batteri 10" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="discharge_battery_10a", name="Discharge battery 10A", icon="mdi:battery-arrow-down"),
    # Old key="ladeboks_1_tom_batteri_reset" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 tom batteri reset" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="discharge_battery_reset", name="Discharge battery reset", icon="mdi:restore"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up button entities for each configured charger."""
    entities = []

    for charger in CHARGERS:
        for description in PwM_CHARGER_BUTTONS:
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
