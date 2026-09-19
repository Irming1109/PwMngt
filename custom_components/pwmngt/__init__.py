import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.device_registry import DeviceEntry

import voluptuous as vol

from .services import async_setup_services
from .api import PwMngtAPI
from .const import DOMAIN, PLATFORMS, API_OBJ

LOGGER = logging.getLogger(__name__)

# This function is called when the integration is configured through the UI (via Config Flow)
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    LOGGER.info("Setting up entry for %s", DOMAIN)
    hass.data.setdefault(DOMAIN, {})

    api = PwMngtAPI(hass, entry)
    hass.data[DOMAIN][API_OBJ] = api

    await async_setup_services(hass)

    # Forward config entry setup to the sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

# Clean up when the integration is removed
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
 
    LOGGER.info("Removing entry for %s", DOMAIN)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(API_OBJ, None)  # Ensure API_OBJ is removed
        return True
    return False

async def async_setup(hass: HomeAssistant, config: dict):
    LOGGER.info("Setting up %s", DOMAIN)

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting a PwMngt device from the Home Assistant UI.

    Without this hook, HA hides the "Delete device" button for any device
    still tied to a live config entry -- which is why an old device (e.g.
    a retired charger) couldn't be removed from the UI even after all its
    entities were deleted by hand. Devices this integration still creates
    (the hub, current chargers, PwM PV, ...) simply get recreated on the
    next reload if one is ever deleted by mistake, so it's safe to always
    allow removal here.
    """
    return True
