"""Consumption averages: the PV device's household-consumption-average sensor.

See the package docstring in data/__init__.py for the general shape.
Unlike data/balance.py, there's no calculate_* function here yet -- the
averaging logic itself isn't built (see the comment above
PwM_CONSUMPTION_AVERAGES_ATTRIBUTES for what it'll need), so this module is
just PwMngtConsumptionAveragesSensor's scaffolding for now. Add a plain
calculate_* function alongside it once that logic lands, the same way
calculate_balance() sits next to PwMngtBalanceDataSensor in data/balance.py.
"""

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory

from ..devices import pv_device_info

# ---------------------------------------------------------------------------
# PV-device "consumption averages" sensor (read-only, scaffolding only).
# Bundles 12 household-consumption-average values as attributes on a
# single diagnostic entity -- a "*_data" sensor, same naming convention
# and pattern as PwMngtBalanceDataSensor in data/balance.py.
# No calculation logic yet -- state and every attribute report None.
#
# Old Node-RED functions: "Beregn 8-16 & 6-9 & 17-06" / "Beregn
# gennemsnitsforbrug 17-21" / "Beregn gennemsnitsforbrug døgn".
# Recomputing natively needs a 30-day rolling history and time-based
# triggers (kl 16:01/21:01/00:01, plus select.pwm_pv_history_period_days
# changing).
# ---------------------------------------------------------------------------

PwM_CONSUMPTION_AVERAGES_ATTRIBUTES: list[str] = [
    "daytime_average",
    "daytime_average_adjusted",
    "early_morning_average",
    "morning_average",
    "late_morning_average",
    "evening_average",
    "nighttime_average",
    "full_day_average",
    "pool_heating_consumption",
    "mining_consumption",
    "car_charger_consumption",
    "heat_pump_consumption",
]

PwM_CONSUMPTION_AVERAGES_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="consumption_averages",
        name="Consumption averages",
        icon="mdi:chart-timeline-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


class PwMngtConsumptionAveragesSensor(SensorEntity):
    """A scaffolded, read-only PwMngt PV-device sensor bundling the 12
    consumption-average values above as attributes. No live value yet --
    see the comment block above PwM_CONSUMPTION_AVERAGES_ATTRIBUTES.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            key: None for key in PwM_CONSUMPTION_AVERAGES_ATTRIBUTES
        }
