from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Opsæt integrationen via UI."""
    hass.data.setdefault(DOMAIN, {})

    # Videresend setup af sensoren
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Håndter fjernelse af en integration via UI."""
    return await hass.config_entries.async_forward_entry_unload(entry, ["sensor"])
