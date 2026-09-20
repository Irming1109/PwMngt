import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import async_get_integration

import voluptuous as vol

from .services import async_setup_services
from .api import PwMngtAPI
from .const import (
    DOMAIN,
    PLATFORMS,
    API_OBJ,
    DEPENDENCY_SOLCAST_SOLAR,
    DEPENDENCY_STROMLIGNING,
)

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

    _async_check_dependencies(hass)

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


def _async_check_dependencies(hass: HomeAssistant) -> None:
    """Raise (or clear) a Home Assistant Repair for each external
    integration PwMngt effectively depends on today but can't enforce
    through Home Assistant's own mechanisms.

    - Solcast PV Forecast (solcast_solar): backs the required "PV forecast
      today"/"PV forecast tomorrow" fields in Options -> Solar PV Plant --
      without it there's nothing to pick for those two fields, and the
      wizard can't be completed at all (they're required).
    - Strømligning (stromligning): backs the hub's "Spot electricity
      price" sensor (see PwM_HUB_MIRROR_SENSORS in sensor.py), which is
      hardcoded to one of its entities rather than user-configurable, so
      there's no wizard field to attach a warning to for this one -- the
      Repair is the only place this gets surfaced at all.

    Checked on every setup (fresh install, every Home Assistant restart,
    and every Options-flow-triggered reload -- see
    PwMngtOptionsFlow._do_update), so the Repair also clears itself
    automatically the next time either integration gets installed, with
    nothing for the user to dismiss by hand.

    Home Assistant's issue_registry helpers here are synchronous
    (@callback) despite the "async_" naming convention -- not awaited.
    """
    checks = [
        (
            DEPENDENCY_SOLCAST_SOLAR,
            "missing_solcast_solar",
            "https://github.com/BJReplay/ha-solcast-solar",
        ),
        (
            DEPENDENCY_STROMLIGNING,
            "missing_stromligning",
            "https://github.com/MTrab/stromligning",
        ),
    ]
    for dependency_domain, issue_id, learn_more_url in checks:
        if hass.config_entries.async_entries(dependency_domain):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
        else:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=issue_id,
                learn_more_url=learn_more_url,
            )


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
