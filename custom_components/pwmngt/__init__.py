from homeassistant.core import HomeAssistant

from .const import DOMAIN

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up integrationen."""
    hass.states.async_set(f"sensor.{DOMAIN}", "Energy manager")
    return True
