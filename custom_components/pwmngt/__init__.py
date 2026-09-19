import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.loader import async_get_integration

import voluptuous as vol

from .services import async_setup_services
from .api import PwMngtAPI
from .const import DOMAIN, PLATFORMS, API_OBJ

LOGGER = logging.getLogger(__name__)

# Where this integration's custom Lovelace cards (pwm-hub-card,
# pwm-charger-card, pwm-pv-card -- see www/pwm-cards.js) are served from.
# Registered once per Home Assistant instance in _async_register_frontend().
FRONTEND_URL_BASE = "/pwmngt_frontend"
FRONTEND_JS_FILENAME = "pwm-cards.js"
FRONTEND_REGISTERED = "frontend_registered"

# This function is called when the integration is configured through the UI (via Config Flow)
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    LOGGER.info("Setting up entry for %s", DOMAIN)
    hass.data.setdefault(DOMAIN, {})

    await _async_register_frontend(hass)

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


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve PwMngt's custom Lovelace cards and load them for every dashboard.

    This lets Kasper (or anyone installing PwMngt via HACS) drop
    pwm-hub-card / pwm-charger-card / pwm-pv-card onto a dashboard without
    ever manually adding a Lovelace resource -- the integration registers
    its own www/ folder as a static path and injects a <script> tag for
    it, the same pattern Home Assistant's docs describe for integrations
    that ship their own frontend:
    https://developers.home-assistant.io/docs/frontend/custom-ui/registering-resources/

    Guarded to run only once per Home Assistant instance (via hass.data),
    even across multiple config entries or reloads.
    """
    if hass.data.get(DOMAIN, {}).get(FRONTEND_REGISTERED):
        return

    integration = await async_get_integration(hass, DOMAIN)
    www_path = Path(__file__).parent / "www"

    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL_BASE, str(www_path), cache_headers=False)]
    )
    # The query string cache-busts the browser: bumping manifest.json's
    # version (which every release already does) is enough to make sure a
    # HACS update is picked up instead of a stale cached copy of the JS.
    add_extra_js_url(
        hass, f"{FRONTEND_URL_BASE}/{FRONTEND_JS_FILENAME}?v={integration.version}"
    )

    hass.data[DOMAIN][FRONTEND_REGISTERED] = True


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
