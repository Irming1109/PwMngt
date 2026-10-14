"""Button entities for PwMngt.

The charger buttons below are scaffolding only: pressing one logs that it
was pressed, no action is actually sent anywhere yet. postpone_battery_charge
(the PV device's only button so far) is live -- see its own description.
"""

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .base import use_description_key_as_translation_key
from .data.battery_charge_decision import signal_postpone_battery_charge
from .devices import CHARGERS, charger_device_info, pv_device_info

LOGGER = logging.getLogger(__name__)

# Shown once, under the PV device.
PwM_PV_BUTTONS: list[ButtonEntityDescription] = [
    # Old key="pv_battery_udskyd" (button.pv_battery_udskyd), from Node-RED.
    # Old name="Udskyd ladning"
    # Toggles tonight's grid charge on/off: flips battery_charge_status
    # (data/battery_charge_decision.py) between "no_deferred" and
    # "not_calculated", same as Node-RED's own "Udskudt ?" toggle --
    # see that module's docstring for the exact rule and the midnight
    # reset.
    ButtonEntityDescription(
        key="postpone_battery_charge",
        name="Postpone battery charge",
        icon="mdi:pause-circle-outline",
    ),
]

PwM_CHARGER_BUTTONS: list[ButtonEntityDescription] = [
    # Old key="ladeboks_1_lad_nat_5" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nat_5" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_tonight_5km", name="Charge tonight 5 km", icon="mdi:weather-night"),
    # Old key="ladeboks_1_lad_nat_10" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nat_10" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_tonight_10km", name="Charge tonight 10 km", icon="mdi:weather-night"),
    # Old key="ladeboks_1_lad_nat_50" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nat_50" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_tonight_50km", name="Charge tonight 50 km", icon="mdi:weather-night"),
    # Old key="ladebolks_1_lad_nat_fuld" (ladebolks_2_... equivalent for Charger2)
    # Old name="Ladebolks_1_lad_nat_fuld" (Ladebolks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_tonight_full", name="Charge tonight full", icon="mdi:battery-charging-100"),
    # Old key="ladeboks_1_lad_nat_reset" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nat_reset" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_tonight_reset", name="Charge tonight reset", icon="mdi:restore"),
    # Old key="ladeboks_1_lad_nu_5" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_5" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_5km", name="Charge now 5 km", icon="mdi:ev-station"),
    # Old key="ladeboks_1_lad_nu_10" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_10" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_10km", name="Charge now 10 km", icon="mdi:ev-station"),
    # Old key="ladeboks_1_lad_nu_50" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_50" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_50km", name="Charge now 50 km", icon="mdi:ev-station"),
    # Old key="ladeboks_1_lad_nu_fuld" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_fuld" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_full", name="Charge now full", icon="mdi:battery-charging-100"),
    # Old key="ladeboks_1_lad_nu_reset" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_lad_nu_reset" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="charge_now_reset", name="Charge now reset", icon="mdi:restore"),
    # Old key="ladeboks_1_tom_batteri_5" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 tom batteri 5" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="decrease_min_soc_5pct", name="Decrease min SoC 5%", icon="mdi:battery-arrow-down"),
    # Old key="ladeboks_1_tom_batteri_10" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 tom batteri 10" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="decrease_min_soc_10pct", name="Decrease min SoC 10%", icon="mdi:battery-arrow-down"),
    # Old key="ladeboks_1_tom_batteri_reset" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 tom batteri reset" (Ladeboks 2 ... equivalent for Charger2)
    ButtonEntityDescription(key="decrease_min_soc_reset", name="Decrease min SoC reset", icon="mdi:restore"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up button entities for the PV device and each configured charger."""
    entities = []

    for description in PwM_PV_BUTTONS:
        entities.append(PwMngtPvButton(description, entry))

    for charger in CHARGERS:
        for description in PwM_CHARGER_BUTTONS:
            entities.append(PwMngtButton(description, entry, charger))

    use_description_key_as_translation_key(entities)
    async_add_entities(entities)


class PwMngtPvButton(ButtonEntity):
    """A PV-device button entity (see PwM_PV_BUTTONS)."""

    _attr_has_entity_name = True

    def __init__(self, description: ButtonEntityDescription, entry: ConfigEntry) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._entry = entry

    async def async_press(self) -> None:
        if self.entity_description.key == "postpone_battery_charge":
            async_dispatcher_send(
                self.hass, signal_postpone_battery_charge(self._entry.entry_id)
            )


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
