from homeassistant.helpers.entity import Entity
from .const import DOMAIN, DEFAULT_NAME

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up sensoren."""
    async_add_entities([PwMngt()], True)

class HelloWorldSensor(Entity):
    """En simpel sensor, der returnerer en fast værdi."""

    def __init__(self):
        """Initialisering."""
        self._attr_name = DEFAULT_NAME
        self._attr_state = "Hello world"

    @property
    def name(self):
        """Returnerer navnet på sensoren."""
        return self._attr_name

    @property
    def state(self):
        """Returnerer sensorens værdi."""
        return self._attr_state
