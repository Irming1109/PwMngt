from homeassistant.helpers.entity import Entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, DEFAULT_NAME

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Opsæt sensoren fra en konfigurationsindgang."""
    async_add_entities([PwmngtSensor()])

class PwmngtSensor(Entity):
    """En simpel sensor med en fast værdi."""

    def __init__(self):
        """Initialisering."""
        self._attr_name = "PWMngt Sensor"
        self._attr_state = "Hello world"

    @property
    def name(self):
        """Returnerer navnet på sensoren."""
        return self._attr_name

    @property
    def state(self):
        """Returnerer sensorens værdi."""
        return self._attr_state
