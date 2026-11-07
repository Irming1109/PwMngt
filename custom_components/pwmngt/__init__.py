import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import async_get_integration

from .services import async_setup_services
from .const import (
    DOMAIN,
    PLATFORMS,
    DEPENDENCY_SOLCAST_SOLAR,
    DEPENDENCY_STROMLIGNING,
)

LOGGER = logging.getLogger(__name__)

# Where this integration's custom Lovelace cards (pwm-hub-card,
# pwm-charger-card, pwm-pv-card -- see www/pwm-cards.js) are served from.
# Registered once per Home Assistant instance in _async_register_frontend().
FRONTEND_URL_BASE = "/pwmngt_frontend"
FRONTEND_JS_FILENAME = "pwm-cards.js"
# Issue #47: the copy under <config>/www, served as /local/pwmngt/ -- see
# _async_register_frontend().
LOCAL_FRONTEND_DIR = "pwmngt"
LOCAL_URL_BASE = f"/local/{LOCAL_FRONTEND_DIR}"
FRONTEND_REGISTERED = "frontend_registered"
_LOVELACE_DOMAIN = "lovelace"

# This function is called when the integration is configured through the UI (via Config Flow)
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    LOGGER.info("Setting up entry for %s", DOMAIN)
    hass.data.setdefault(DOMAIN, {})

    await _async_register_frontend(hass)

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
    - Strømligning (stromligning): backs the required "Spot electricity
      price" field in Options -> Solar PV Plant (see PwM_HUB_MIRROR_SENSORS
      in sensor.py) -- without it there's nothing to pick for that field,
      and the wizard can't be completed at all.

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

    # Kept for clients that still have the old address, and as the
    # fallback below.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL_BASE, str(www_path), cache_headers=False)]
    )
    # Issue #47 (Claus, 2026-11-07): /pwmngt_frontend only exists once this
    # config entry is set up, but the web server answers before that --
    # the Android companion app reloads the dashboard right after a
    # restart, got a 404 for the cards, and showed a red "!" until its
    # frontend cache was cleared (a module that failed to load isn't
    # fetched again until the page reloads). /local is registered by the
    # frontend integration early in startup, so the cards are served from
    # a copy under <config>/www/pwmngt instead -- when that folder existed
    # at startup (Home Assistant only registers /local then).
    base = (
        LOCAL_URL_BASE
        if await hass.async_add_executor_job(_copy_cards_to_local_www, hass, www_path)
        else FRONTEND_URL_BASE
    )
    # The query string cache-busts the browser: bumping manifest.json's
    # version (which every release already does) is enough to make sure a
    # HACS update is picked up instead of a stale cached copy of the JS.
    url = f"{base}/{FRONTEND_JS_FILENAME}?v={integration.version}"
    add_extra_js_url(hass, url)

    # The companion app (iOS and Android) doesn't reliably load extra JS
    # URLs -- every PwM card showed "Configuration error" there, while the
    # browser was fine; a Lovelace resource for the same file fixed it
    # (Claus, 2026-11-04, issue #26). So the cards are also registered as
    # a dashboard resource; www/pwm-cards.js copes with being loaded twice.
    # The resources are only ready once Home Assistant has started.
    if hass.is_running:
        await _async_register_lovelace_resource(hass, url)
    else:

        async def _register_when_started(_event) -> None:
            await _async_register_lovelace_resource(hass, url)

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_when_started)

    hass.data[DOMAIN][FRONTEND_REGISTERED] = True


def _copy_cards_to_local_www(hass: HomeAssistant, www_path: Path) -> bool:
    """Copy the cards to <config>/www/pwmngt (only when changed). True
    when /local can serve them, i.e. <config>/www existed already -- a
    folder created now is only served after the next restart. Runs in
    the executor (file I/O)."""
    local_www = Path(hass.config.path("www"))
    served = local_www.is_dir()
    try:
        target_dir = local_www / LOCAL_FRONTEND_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        source = www_path / FRONTEND_JS_FILENAME
        target = target_dir / FRONTEND_JS_FILENAME
        content = source.read_bytes()
        if not target.is_file() or target.read_bytes() != content:
            target.write_bytes(content)
    except OSError:
        LOGGER.warning("PwMngt: could not copy the cards to %s", local_www, exc_info=True)
        return False
    return served


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> None:
    """Add url as a "JavaScript module" dashboard resource, or update the
    version of an existing PwMngt one (also one added by hand). Storage
    mode only -- resources configured in YAML are read-only, and there
    add_extra_js_url() alone has to do. Never fails the setup."""
    try:
        # The lovelace integration's own data: a LovelaceData dataclass
        # (key LOVELACE_DATA == "lovelace") in current versions, a dict in
        # older ones -- the key is the same string either way.
        lovelace = hass.data.get(_LOVELACE_DOMAIN)
        resources = (
            lovelace.get("resources")
            if isinstance(lovelace, dict)
            else getattr(lovelace, "resources", None)
        )
        if not isinstance(resources, ResourceStorageCollection):
            LOGGER.debug("PwMngt: dashboard resources are not in storage mode -- not registering the cards there")
            return
        # Loads the stored resources first; async_items() alone doesn't.
        await resources.async_get_info()

        # Both addresses (issue #47) -- the old one is moved to the new.
        paths = {
            f"{FRONTEND_URL_BASE}/{FRONTEND_JS_FILENAME}",
            f"{LOCAL_URL_BASE}/{FRONTEND_JS_FILENAME}",
        }
        ours = [item for item in resources.async_items() if item.get("url", "").split("?")[0] in paths]
        if not ours:
            await resources.async_create_item({"res_type": "module", "url": url})
            LOGGER.info("PwMngt: registered the cards as dashboard resource %s", url)
            return
        first, *duplicates = ours
        if first.get("url") != url or first.get("type") != "module":
            await resources.async_update_item(first["id"], {"res_type": "module", "url": url})
            LOGGER.info("PwMngt: updated the cards' dashboard resource to %s", url)
        for item in duplicates:
            await resources.async_delete_item(item["id"])
    except Exception:  # noqa: BLE001 -- the cards still load via add_extra_js_url
        LOGGER.warning("PwMngt: could not register the cards as a dashboard resource", exc_info=True)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting a PwMngt device from the Home Assistant UI.

    Without this hook, HA hides the "Delete device" button for any device
    still tied to a live config entry. Devices this integration still
    creates (the hub, current chargers, PwM PV, ...) simply get recreated
    on the next reload, so it's safe to always allow removal here.
    """
    return True
