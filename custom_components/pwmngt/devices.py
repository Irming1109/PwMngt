"""Device topology shared across PwMngt platforms.

This is the one place that defines which Home Assistant devices this
integration creates and how they relate to each other. Every platform file
(select.py, text.py, number.py, button.py, sensor.py) imports its
DeviceInfo builders from here rather than constructing DeviceInfo inline.

- hub_device_info(): the single "PwM" hub device (the integration itself).
- CHARGERS + charger_device_info(): one device per configured charger,
  nested under the hub via via_device. To scaffold another charger, add a
  dict to CHARGERS -- the platform files all loop over this list, so
  nothing else needs to change.
- pv_device_info(): the single "PwM_PV" solar PV system device, also
  nested under the hub.

More related devices (e.g. a ground-source heat pump, pool control) will
likely be added the same way later: a new <name>_device_info() function
here (plus a list like CHARGERS if it's a multi-instance device type), then
wired into whichever platform files need it. Keep this file the single
source of truth for device topology rather than spreading DeviceInfo
construction across the platform files -- split it into multiple files
only if it grows large enough that a single file becomes hard to navigate.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

CHARGERS = [
    {"id": "charger1", "name": "PwM Charger1"},
    {"id": "charger2", "name": "PwM Charger2"},
]


def charger_device_info(entry: ConfigEntry, charger: dict) -> DeviceInfo:
    """Build the DeviceInfo for a charger device.

    The charger is linked to the main hub device (the config entry itself)
    via via_device, so it shows up nested under "PwM" in the
    Home Assistant device list.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, charger["id"])},
        name=charger["name"],
        manufacturer="Power Management",
        model="Charger",
        via_device=(DOMAIN, entry.entry_id),
    )


def hub_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build the DeviceInfo for the main "PwM" hub device itself.

    Use this for entities that belong to the integration as a whole
    (general configuration) rather than to one specific charger.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="PwM",
        manufacturer="Power Management",
    )


def pv_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build the DeviceInfo for the "PwM_PV" solar PV system device.

    A related device nested under the main hub (same via_device pattern as
    a charger), for configuration that belongs to the solar PV system
    rather than to the hub in general or to a specific charger.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_pv")},
        name="PwM_PV",
        manufacturer="Power Management",
        model="Solar PV system",
        via_device=(DOMAIN, entry.entry_id),
    )
