"""Read-only accessors for the car chargers (PwM Charger1/2).

The EV Charging segment is still scaffolding, but each charger's
consumption source (text.py's consumption_source_entity, picked on the
wizard's EV Charging page) is already real -- data/consumption_data.py
takes it out of the house consumption (#23), and issue #33 uses the same
charger to keep the house battery from covering a car charge.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import CHARGER_POWER_TRANSLATION_KEYS, CHARGER_TYPE_NOT_INSTALLED, DOMAIN
from ..devices import CHARGERS
from ..helpers.state_helper import read_float_state

# A charger drawing at least this much (W) is charging a car.
CAR_CHARGING_MIN_W = 1000.0
# Without a power sensor: a consumption source that changed within this
# long is charging. Easee's session energy moves in steps of ~1 kWh, i.e.
# every ~6 minutes at 11 kW, and less often at a lower current.
_ENERGY_FALLBACK_SECONDS = 10 * 60


@dataclass
class CarCharging:
    """Whether any charger is charging a car right now, and with how much
    (W) -- power_w is None when a charging charger has no power sensor
    (only its consumption source shows that it charges)."""

    charging: bool
    power_w: float | None


def consumption_source_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Each installed charger's consumption_source_entity (text.py),
    skipping a charger whose type is "Not installed" or whose field is
    empty. Looked up via the entity registry, never by entity_id
    string."""
    registry = er.async_get(hass)
    entity_ids: list[str] = []
    for charger in CHARGERS:
        prefix = f"{entry.entry_id}_{charger['id']}"
        type_entity_id = registry.async_get_entity_id("select", DOMAIN, f"{prefix}_type")
        type_state = hass.states.get(type_entity_id) if type_entity_id else None
        if type_state is not None and type_state.state == CHARGER_TYPE_NOT_INSTALLED:
            continue
        text_entity_id = registry.async_get_entity_id(
            "text", DOMAIN, f"{prefix}_consumption_source_entity"
        )
        text_state = hass.states.get(text_entity_id) if text_entity_id else None
        if text_state is None or text_state.state in ("", "unknown", "unavailable"):
            continue
        entity_ids.append(text_state.state)
    return entity_ids


def charger_power_entity_id(hass: HomeAssistant, source_entity_id: str) -> str | None:
    """The charging-power sensor on the same device as a charger's
    consumption source (CHARGER_POWER_TRANSLATION_KEYS), or None."""
    registry = er.async_get(hass)
    source = registry.async_get(source_entity_id)
    if source is None or source.device_id is None:
        return None
    translation_key = CHARGER_POWER_TRANSLATION_KEYS.get(source.platform)
    if translation_key is None:
        return None
    for entity in er.async_entries_for_device(registry, source.device_id):
        if (
            entity.domain == "sensor"
            and entity.platform == source.platform
            and entity.translation_key == translation_key
        ):
            return entity.entity_id
    return None


def car_charging_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """What car_charging() reads: each charger's power sensor, or its
    consumption source when it has none -- for state-change triggers."""
    return [
        charger_power_entity_id(hass, source) or source
        for source in consumption_source_entity_ids(hass, entry)
    ]


def car_charging(hass: HomeAssistant, entry: ConfigEntry) -> CarCharging:
    """Whether any installed charger is charging a car right now (issue
    #33). A charger with a power sensor charges at CAR_CHARGING_MIN_W or
    more; one without counts as charging while its consumption source has
    changed within the last _ENERGY_FALLBACK_SECONDS (power unknown)."""
    charging = False
    power_known = True
    total_w = 0.0
    for source in consumption_source_entity_ids(hass, entry):
        power_entity_id = charger_power_entity_id(hass, source)
        if power_entity_id is not None:
            watts = _power_w(hass.states.get(power_entity_id))
            if watts is not None and watts >= CAR_CHARGING_MIN_W:
                charging = True
                total_w += watts
            continue
        state = hass.states.get(source)
        value = read_float_state(state)
        if (
            value is not None
            and value > 0
            and (dt_util.utcnow() - state.last_changed).total_seconds() <= _ENERGY_FALLBACK_SECONDS
        ):
            charging = True
            power_known = False
    return CarCharging(charging, total_w if power_known else None)


def _power_w(state) -> float | None:
    """A power sensor's value in W (Easee reports kW)."""
    value = read_float_state(state)
    if value is None:
        return None
    unit = state.attributes.get("unit_of_measurement")
    return value * 1000 if unit == "kW" else value
