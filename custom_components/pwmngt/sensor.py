import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import slugify as util_slugify

from .api import PwMngtAPI
from .base import PwMngtSensorEntityDescription
from .const import DOMAIN, API_OBJ
from .chargers import CHARGERS, charger_device_info

LOGGER = logging.getLogger(__name__)

PwM_SENSORS = [
    PwMngtSensorEntityDescription(
        key="hello_world_str",
        name="Hello world",
        entity_category=None,
        icon="mdi:flash",
        value_fn=lambda pwmngt: pwmngt.get_hello_world2(),
    ),
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):   
    """Setup sensors."""
    sensors = []

    for sensor in PwM_SENSORS:
        entity = PwMngtSensor(sensor, hass, entry)
        LOGGER.info("Added sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for charger in CHARGERS:
        for description in PwM_CHARGER_SENSORS:
            entity = PwMngtChargerSensor(description, entry, charger)
            LOGGER.info("Added charger sensor with entity_id '%s'", entity.entity_id)
            sensors.append(entity)

    async_add_entities(sensors)


class PwMngtSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, description: PwMngtSensorEntityDescription, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__()
        self.entity_description = description
        self._config = entry
        self._hass = hass
        self.api: PwMngtAPI = hass.data[DOMAIN][API_OBJ]

        self._attr_unique_id = util_slugify(
            f"{self.entity_description.key}_{self._config.entry_id}"
        )
        self._attr_should_poll = True

        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._config.entry_id)},
            "name": "PwM",
            "manufacturer": "Power Management",
        }

        self._attr_native_unit_of_measurement = (
            self.entity_description.unit_of_measurement
        )

        async_dispatcher_connect(
            self._hass,
            util_slugify(self.entity_description.update_signal),
            self.handle_update,
        )

    async def handle_attributes(self) -> None:
        """Handle attributes. No extra attributes yet -- placeholder for later."""

    async def handle_update(self) -> None:
        """Handle data update."""
        try:
            self._attr_native_value = self.entity_description.value_fn(
                self._hass.data[DOMAIN][API_OBJ]
            )

            LOGGER.info(
                "Setting value for '%s' to: %s",
                self.entity_id,
                self._attr_native_value,
            )
            await self.handle_attributes()
            self._attr_available = True
 
        except Exception as e:
            if self._attr_available:
                LOGGER.error("The PwMngt API made an invalid response: %s", e)
            self._attr_available = False

    async def async_added_to_hass(self):
        await self.handle_update()
        return await super().async_added_to_hass()

# ---------------------------------------------------------------------------
# Charger status sensors (read-only, scaffolding only).
#
# These have no value_fn / API backing yet -- they report None until real
# data (from Node-RED, Easee, or wherever "Ladeboks 1" ends up being read
# from) is wired up in a later step.
# ---------------------------------------------------------------------------

PwM_CHARGER_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="current",
        name="Current",
        icon="mdi:current-ac",
        native_unit_of_measurement="A",
    ),
    SensorEntityDescription(
        key="power",
        name="Power",
        icon="mdi:flash",
        native_unit_of_measurement="W",
    ),
    SensorEntityDescription(
        key="consumption",
        name="Consumption",
        icon="mdi:lightning-bolt",
        native_unit_of_measurement="kWh",
    ),
    SensorEntityDescription(
        key="km_charged",
        name="Km charged",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement="km",
    ),
    SensorEntityDescription(
        key="remaining",
        name="Remaining",
        icon="mdi:progress-question",
    ),
    # Moved here from select.py: these are values the automation reports,
    # not something the user picks from a dropdown, so a read-only ENUM
    # sensor is the correct entity type rather than a Select.
    SensorEntityDescription(
        key="ordered_phases",
        name="Ordered phases",
        icon="mdi:sine-wave",
        device_class=SensorDeviceClass.ENUM,
        options=["1", "3"],
    ),
    SensorEntityDescription(
        key="state",
        name="State",
        icon="mdi:information-outline",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "No car connected",
            "Inactive",
            "Paused",
            "Active - solar",
            "Active - manual",
            "No demand",
            "Unknown",
        ],
    ),
    SensorEntityDescription(
        key="surplus_charging_active",
        name="Surplus charging active",
        icon="mdi:solar-power",
        device_class=SensorDeviceClass.ENUM,
        options=["No", "Yes"],
    ),
]


class PwMngtChargerSensor(SensorEntity):
    """A scaffolded, read-only PwMngt charger sensor. No live value yet."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        charger: dict,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{charger['id']}_{description.key}"
        self._attr_device_info = charger_device_info(entry, charger)
        self._attr_native_value = None
