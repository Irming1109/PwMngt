"""Entity base definitions."""

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