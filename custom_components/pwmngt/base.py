"""Entity base definitions.

Documentation convention: whenever an entity's key/name is created by
translating or renaming an existing source (a Danish Node-RED/HA entity,
or an earlier key/name of our own), add a two-line comment directly above
its EntityDescription(...) call, e.g.:

    # Old key="ladeboks_1_opsaetning"
    # Old name="Ladeboks 1 opsaetning"
    PwMngtSelectEntityDescription(
        key="charger1_type",
        name="Charger1 type",
        ...
    ),

This is so the original Node-RED/HA entity (or our own earlier name) can be
found again later when wiring up real functionality/data. Apply this to
every new or renamed entity across select.py, text.py, number.py,
button.py and sensor.py.
"""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.sensor import SensorEntityDescription

from .api import PwMngtAPI
from .const import UPDATE_SIGNAL


@dataclass
class PwMngtBaseEntityDescriptionMixin:
    """Describes a basic PwMngt entity."""

    value_fn: Callable[[PwMngtAPI], bool | str | int | float]


@dataclass
class PwMngtSensorEntityDescription(
    SensorEntityDescription, PwMngtBaseEntityDescriptionMixin
):
    """Describes a PwMngt sensor."""

    unit_fn: Callable[[PwMngtAPI], None] = None
    update_signal: str = UPDATE_SIGNAL


@dataclass
class PwMngtBinarySensorEntityDescription(
    BinarySensorEntityDescription, PwMngtBaseEntityDescriptionMixin
):
    """Describes a PwMngt sensor."""

    unit_fn: Callable[[PwMngtAPI], None] = None

# ---------------------------------------------------------------------------
# Scaffolding-only entity descriptions for charger entities (number/select/
# text). These deliberately do NOT use PwMngtBaseEntityDescriptionMixin's
# value_fn, because there is no backing API/data source yet -- entities just
# hold a local default value until real functionality is wired up. Buttons
# and sensors use Home Assistant's own EntityDescription classes directly.
# ---------------------------------------------------------------------------

from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.select import SelectEntityDescription
from homeassistant.components.text import TextEntityDescription


@dataclass
class PwMngtNumberEntityDescription(NumberEntityDescription):
    """Describes a PwMngt number entity (no live value wired up yet)."""

    default_value: float | None = None


@dataclass
class PwMngtSelectEntityDescription(SelectEntityDescription):
    """Describes a PwMngt select entity (no live value wired up yet)."""

    default_option: str | None = None


@dataclass
class PwMngtTextEntityDescription(TextEntityDescription):
    """Describes a PwMngt text entity (no live value wired up yet)."""

    default_value: str | None = None
