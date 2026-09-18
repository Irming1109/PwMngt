"""Charger device definitions shared across PwMngt platforms.

Each entry in CHARGERS becomes its own Home Assistant device, nested under
the main Power Management hub device. To scaffold another charger later
(e.g. "PwM Charger 2"), add a dict here -- the number/select/button/text/sensor
platform files all loop over this list, so nothing else needs to change.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

CHARGERS = [
    {"id": "charger_1", "name": "PwM Charger 1"},
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
