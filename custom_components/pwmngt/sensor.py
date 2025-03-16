from homeassistant.helpers.entity import Entity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN

class PwmngtSensor(Entity):
    """En simpel sensor for PWMngt."""

    def __init__(self):
        self._attr_name = "PWMngt Hello World"
        self._attr_unique_id = "pwmngt_hello_world"
        self._attr_state = "Hello world"

    @property
    def device_info(self) -> DeviceInfo:
        """Returnerer enhedsoplysninger, så integrationen vises i UI."""
        return DeviceInfo(
            identifiers={(DOMAIN, "pwmngt_device")},  # Unikt ID for enheden
            name="PWMngt Device",
            manufacturer="Mit Eget Firma",
            model="PWMngt Model 1",
            sw_version="1.0.0",
        )

    @property
    def state(self):
        """Returnerer sensortilstanden."""
        return self._attr_state
