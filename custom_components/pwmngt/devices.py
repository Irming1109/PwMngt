"""Device topology shared across PwMngt platforms.

This is the one place that defines which Home Assistant devices this
integration creates and how they relate to each other. Every platform file
(select.py, text.py, number.py, button.py, sensor.py) imports its
DeviceInfo builders from here rather than constructing DeviceInfo inline.

- hub_device_info(): the single "PwM" hub device (the integration itself).
- CHARGERS + charger_device_info(): one device per configured charger,
  nested under the hub (_via_hub()). To scaffold another charger, add a
  dict to CHARGERS -- the platform files all loop over this list, so
  nothing else needs to change.
- pv_device_info(): the single "PwM PV" Solar PV Plant device, also
  nested under the hub.

More related devices (e.g. a ground-source heat pump, pool control) will
likely be added the same way later: a new <name>_device_info() function
here (plus a list like CHARGERS if it's a multi-instance device type), then
wired into whichever platform files need it. Keep this file the single
source of truth for device topology rather than spreading DeviceInfo
construction across the platform files -- split it into multiple files
only if it grows large enough that a single file becomes hard to navigate.
"""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

# Issue #78: Home Assistant deprecated DeviceInfo's via_device (the hub's
# identifiers) for via_device_id (the hub's device registry id) -- it stops
# working in 2027.8, and already makes an entity_id rename of a nested
# device's entity fail (HA re-adds the entity and the deprecation report
# raises). Older versions (hacs.json allows 2025.3) don't know
# via_device_id and reject the whole DeviceInfo, so it is only used where
# Home Assistant lists via_device as deprecated.
_DEPRECATED_DEVICE_INFO = getattr(dr, "_DEPRECATED_DEVICE_INFO_PARAMETERS", None)
_VIA_DEVICE_ID_SUPPORTED = isinstance(_DEPRECATED_DEVICE_INFO, dict) and (
    "via_device" in _DEPRECATED_DEVICE_INFO
)
_HUB_NAME = "PwM"
_MANUFACTURER = "Power Management"
# config entry id -> the hub device's registry id (async_register_hub_device()).
_HUB_DEVICE_IDS: dict[str, str] = {}

CHARGERS = [
    {"id": "charger1", "name": "PwM Charger1"},
    {"id": "charger2", "name": "PwM Charger2"},
]


def charger_device_info(entry: ConfigEntry, charger: dict) -> DeviceInfo:
    """Build the DeviceInfo for a charger device.

    The charger is linked to the main hub device (the config entry itself)
    (_via_hub()), so it shows up nested under "PwM" in the
    Home Assistant device list.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, charger["id"])},
        name=charger["name"],
        manufacturer=_MANUFACTURER,
        model="Charger",
        **_via_hub(entry),
    )


def hub_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build the DeviceInfo for the main "PwM" hub device itself.

    Use this for entities that belong to the integration as a whole
    (general configuration) rather than to one specific charger.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=_HUB_NAME,
        manufacturer=_MANUFACTURER,
    )


def pv_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build the DeviceInfo for the "PwM PV" Solar PV Plant device.

    A related device nested under the main hub (same _via_hub() as a
    charger), for configuration that belongs to the Solar PV Plant
    rather than to the hub in general or to a specific charger.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_pv")},
        name="PwM PV",
        manufacturer=_MANUFACTURER,
        model="Solar PV Plant",
        **_via_hub(entry),
    )


@callback
def async_register_hub_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create (or find) the hub device before any platform adds entities,
    so the nested devices can name it by device id (issue #78). Called
    from async_setup_entry() before the platforms are forwarded."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=_HUB_NAME,
        manufacturer=_MANUFACTURER,
    )
    _HUB_DEVICE_IDS[entry.entry_id] = device.id


def _via_hub(entry: ConfigEntry) -> dict[str, Any]:
    """The DeviceInfo field that nests a device under the hub: via_device_id
    where Home Assistant supports it and the hub is registered, else the
    old via_device."""
    hub_device_id = _HUB_DEVICE_IDS.get(entry.entry_id)
    if _VIA_DEVICE_ID_SUPPORTED and hub_device_id:
        return {"via_device_id": hub_device_id}
    return {"via_device": (DOMAIN, entry.entry_id)}
