"""Solar data: PV-forecast figures for parts of today/tomorrow (the
solar_data sensor's attributes) plus the battery nightly target (its own
sensor), all from one calculation.

Port of Claus's Node-RED function "Strøm beregninger" (subflow "Sol
prognose i dag", instanced on tab "Ha data - opsætning"), from Claus's
export dated 30-09-2026 -- Claus's version is the reference.

    Node-RED sensor                     PwMngt
    sensor.batteri_target               sensor battery_nightly_target
    sensor.solproduktion_8_16           solar_data: pv_forecast_8_16_today
    sensor.solproduktion_imorgen_8_16   solar_data: pv_forecast_8_16_tomorrow
    (internal)                          solar_data: pv_forecast_17_21_today

Battery nightly target: the battery charge (in %) needed to cover the
evening/night, i.e.
    (night consumption - PV forecast 17-21 today) x 1.07
    + (night consumption - tomorrow's forecast), only if tomorrow's
      forecast is below night consumption
converted to % of the battery, + 5, kept within 5-95. "Night consumption"
is consumption_data's nighttime_average (Node-RED:
sensor.spidstimer_nat_17_06_snit). It stays a sensor of its own (not an
attribute): it's a key control value other parts react to (see
data/battery_data.py) and worth graphing.

The hourly figures come from the forecast entities picked on the Solar PV
Plant page (ENTITY_KEY_PV_FORECAST_TODAY/_TOMORROW) -- specifically their
"detailedHourly" attribute, which Solcast PV Forecast provides. Another
forecast integration without that attribute leaves everything unknown
(Node-RED stopped with "Solcast-forecast mangler" the same way).

Deliberate differences from Node-RED (each marked "DIFF" below):
- An hour is matched on its "period_start" time, not on its position in
  the list. Node-RED used list position = hour, which is off by one hour
  on the two daylight-saving days (23/25 entries).
- Node-RED's PV correction factor ("Solprognose_faktor", from
  sensor.sol_produktion) isn't ported: it was calculated but -- by Claus's
  own comments ("ligenu kører vi uden at ændre på prognose") -- not applied
  to any output. The inputs only used for it (sensor.sol_produktion,
  sensor.dagtimer_8_16_snit) aren't read.
- Battery size "None" (no battery) gives an unknown target instead of
  Node-RED's NaN.

Triggers: every 20 minutes from 08:00 to 21:00, daily at 06:00, whenever
today's forecast entity is updated (state or attributes -- Solcast
refreshes its forecast a few times a day), and when Battery size changes.
Plus once shortly after startup, so the values aren't unknown until the
next scheduled run. Node-RED: inject "Opdater hver 20. minut 8-21" (its
cron "*/20 8-20 * * *" actually stopped at 20:40; 21:00 added here to match
the node's name, Kasper 2026-09-28), inject "Kør 1 gang kl. 6 hver dag",
"Forecast PV1 i dag" (server-state-changed on
sensor.solcast_pv_forecast_forecast_today, via a 200 ms delay) and
"Batteri størrelse".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import event
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    DOMAIN,
    ENTITY_KEY_PV_FORECAST_TODAY,
    ENTITY_KEY_PV_FORECAST_TOMORROW,
)
from ..devices import pv_device_info
from ..helpers.state_helper import read_float_state
from ..properties import pv_properties
from . import DATA_SENSOR_STATE

LOGGER = logging.getLogger(__name__)

KEY_BATTERY_NIGHTLY_TARGET = "battery_nightly_target"

# ---------------------------------------------------------------------------
# solar_data attributes: forecast PV energy (kWh) for a part of the day,
# local clock hours "from-to" (to not included).
# ---------------------------------------------------------------------------

PwM_SOLAR_DATA_ATTRIBUTES: dict[str, tuple[str, int, int]] = {
    # 17-21 today. Not a Node-RED sensor -- the figure the target is
    # built from, shown for transparency.
    "pv_forecast_17_21_today": ("today", 17, 21),
    # 08-16 today. Old: sensor.solproduktion_8_16 (Claus). Read by
    # Claus's "Beregning" (tab "Automatik Styring").
    "pv_forecast_8_16_today": ("today", 8, 16),
    # 08-16 tomorrow. Old: sensor.solproduktion_imorgen_8_16 (Claus).
    # Read by Claus's "Skal batteri lades".
    "pv_forecast_8_16_tomorrow": ("tomorrow", 8, 16),
}

PwM_SOLAR_DATA_SENSORS: list[SensorEntityDescription] = [
    # New -- groups what Node-RED wrote as separate sensors (see the
    # module docstring). Attribute-only, so its state is DATA_SENSOR_STATE.
    SensorEntityDescription(
        key="solar_data",
        name="Solar data",
        icon="mdi:solar-power-variant-outline",
    ),
]

PwM_BATTERY_NIGHTLY_TARGET_SENSORS: list[SensorEntityDescription] = [
    # Old key="batteri target" (sensor.batteri_target), from Node-RED.
    SensorEntityDescription(
        key=KEY_BATTERY_NIGHTLY_TARGET,
        name="Battery nightly target",
        icon="mdi:battery-clock-outline",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]

# Legacy entities replaced by solar_data's attributes, removed from the
# entity registry on setup -- see async_remove_legacy_solar_entities().
_LEGACY_UNIQUE_ID_SUFFIXES: list[str] = [
    "pv_pv_forecast_daytime_today",     # 11-16 today, not ported
    "pv_pv_forecast_daytime_tomorrow",  # not ported, see the module docstring
]

# Node-RED fallbacks / limits.
_DEFAULT_NIGHT_CONSUMPTION = 5.0   # "|| 5" when the average is missing or 0
_MIN_NIGHT_CONSUMPTION = 2.0       # used when the average is negative
_TARGET_MARGIN = 1.07
_TARGET_FLOOR = 5
_TARGET_CEILING = 95

# Triggers.
_PERIODIC_HOURS = list(range(8, 21))   # Node-RED cron "*/20 8-20 * * *"
_PERIODIC_MINUTES = [0, 20, 40]
_LAST_PERIODIC_HOUR = 21               # plus 21:00, see the module docstring
_DAILY_HOUR = 6                        # Node-RED cron "00 06 * * *"
_STARTUP_DELAY_SECONDS = 10


@callback
def async_remove_legacy_solar_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the entities solar_data replaces from the entity registry,
    so they don't linger as "unavailable" forever."""
    registry = er.async_get(hass)
    for suffix in _LEGACY_UNIQUE_ID_SUFFIXES:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{suffix}"
        )
        if entity_id:
            LOGGER.info("PwMngt: removing legacy solar entity %s", entity_id)
            registry.async_remove(entity_id)


# ---------------------------------------------------------------------------
# Pure calculations -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


def _js_round(value: float, decimals: int = 0) -> float:
    """Round like JavaScript's toFixed(): half up, on the number's exact
    binary value (so 3.85, stored as 3.8499999..., gives 3.8). Python's
    round() rounds half to even instead."""
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def sum_pv_forecast(detailed_hourly: list[dict[str, Any]], start_hour: int, end_hour: int) -> float:
    """Forecast PV energy (kWh) from start_hour up to (not including)
    end_hour, from Solcast's "detailedHourly" list. Node-RED: beregn_pv().

    DIFF: an entry's hour is taken from its "period_start" (local time), not
    from its position in the list -- see the module docstring. An entry
    without a readable period_start falls back to its position."""
    total = 0.0
    for index, entry in enumerate(detailed_hourly):
        if not isinstance(entry, dict):
            continue
        hour = index
        period_start = entry.get("period_start")
        if period_start is not None:
            parsed = (
                period_start
                if isinstance(period_start, datetime)
                else dt_util.parse_datetime(str(period_start))
            )
            if parsed is not None:
                hour = dt_util.as_local(parsed).hour
        estimate = entry.get("pv_estimate")
        if start_hour <= hour < end_hour and estimate is not None:
            try:
                total += float(estimate)
            except (TypeError, ValueError):
                continue
    return total


@dataclass
class SolarInputs:
    night_consumption: float | None  # consumption_data nighttime_average (kWh)
    battery_size_kwh: float | None   # None = no battery / unknown
    today_hourly: list[dict[str, Any]]
    tomorrow_hourly: list[dict[str, Any]]
    tomorrow_total: float            # tomorrow's forecast (kWh), the entity's state


def calculate_solar_data(inputs: SolarInputs) -> dict[str, float | None]:
    """One run of Node-RED's "Strøm beregninger". Returns every
    PwM_SOLAR_DATA_ATTRIBUTES value plus KEY_BATTERY_NIGHTLY_TARGET
    (None = unknown)."""
    hourly = {"today": inputs.today_hourly, "tomorrow": inputs.tomorrow_hourly}
    values: dict[str, float | None] = {
        key: _js_round(sum_pv_forecast(hourly[day], start, end), 1)
        for key, (day, start, end) in PwM_SOLAR_DATA_ATTRIBUTES.items()
    }

    night = inputs.night_consumption
    # Node-RED: parseFloat(...) || 5 -- missing *and* exactly 0 both give 5.
    if not night:
        night = _DEFAULT_NIGHT_CONSUMPTION
    # Guards against a data error in the average (Node-RED comment).
    if night <= 0:
        night = _MIN_NIGHT_CONSUMPTION

    # Unrounded, as in Node-RED.
    pv_17_21_today = sum_pv_forecast(inputs.today_hourly, 17, 21)
    target_kwh = (night - pv_17_21_today) * _TARGET_MARGIN
    if target_kwh <= 0:
        target_kwh = 0.0
    if inputs.tomorrow_total <= night:
        target_kwh += night - inputs.tomorrow_total

    target: float | None = None
    if inputs.battery_size_kwh:
        battery = target_kwh / inputs.battery_size_kwh * 100 + 5
        battery = min(max(battery, _TARGET_FLOOR), _TARGET_CEILING)
        target = _js_round(battery)
    values[KEY_BATTERY_NIGHTLY_TARGET] = target
    return values


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


class PwMngtSolarDataCoordinator(DataUpdateCoordinator[dict[str, float | None]]):
    """Runs calculate_solar_data() on the Node-RED schedule and hands the
    result to solar_data and Battery nightly target. No polling
    (update_interval=None) -- every run is triggered explicitly."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name="PwMngt solar data",
            update_interval=None,
        )
        self._entry = entry
        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._today_entity_id = entity_map.get(ENTITY_KEY_PV_FORECAST_TODAY) or None
        self._tomorrow_entity_id = entity_map.get(ENTITY_KEY_PV_FORECAST_TOMORROW) or None
        self._unsubscribers: list = []
        self._battery_size_unsub = None
        self.data = {}

    async def _async_update_data(self) -> dict[str, float | None]:
        """Only used by a manual refresh (e.g. homeassistant.update_entity):
        recalculate now, or keep the last values when the forecast isn't
        available."""
        inputs = self._read_inputs()
        if inputs is None:
            return self.data or {}
        return calculate_solar_data(inputs)

    @callback
    def async_start(self) -> None:
        """Subscribe to the triggers and schedule a first run."""
        self._unsubscribers.append(
            event.async_track_time_change(
                self.hass, self._handle_trigger,
                hour=_PERIODIC_HOURS, minute=_PERIODIC_MINUTES, second=0,
            )
        )
        self._unsubscribers.append(
            event.async_track_time_change(
                self.hass, self._handle_trigger,
                hour=[_DAILY_HOUR, _LAST_PERIODIC_HOUR], minute=0, second=0,
            )
        )
        # Any update of today's forecast -- attribute-only ones too, since
        # Solcast's refresh may leave the day total unchanged while the
        # hourly detail moves (Node-RED: "output only on state change" off).
        if self._today_entity_id:
            self._unsubscribers.append(
                event.async_track_state_change_event(
                    self.hass, [self._today_entity_id], self._handle_trigger
                )
            )
        # The Battery size select and consumption_data are set up alongside
        # this, so they may not be registered yet -- subscribe and run once
        # a little later (and only after Home Assistant has started).
        if self.hass.is_running:
            self._unsubscribers.append(
                event.async_call_later(self.hass, _STARTUP_DELAY_SECONDS, self._handle_startup)
            )
        else:
            self._unsubscribers.append(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._handle_startup
                )
            )

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubscribers:
            try:
                unsub()
            except ValueError:
                # A listen_once listener that already fired can't be
                # removed again.
                pass
        self._unsubscribers.clear()
        if self._battery_size_unsub is not None:
            self._battery_size_unsub()
            self._battery_size_unsub = None

    @callback
    def _handle_startup(self, _event_or_time=None) -> None:
        battery_size_entity_id = pv_properties.battery_size_entity_id(self.hass, self._entry)
        if battery_size_entity_id and self._battery_size_unsub is None:
            self._battery_size_unsub = event.async_track_state_change_event(
                self.hass, [battery_size_entity_id], self._handle_trigger
            )
        self._run()

    @callback
    def _handle_trigger(self, _event_or_time=None) -> None:
        self._run()

    @callback
    def _run(self) -> None:
        inputs = self._read_inputs()
        if inputs is None:
            return
        self.async_set_updated_data(calculate_solar_data(inputs))

    def _read_inputs(self) -> SolarInputs | None:
        """None when the forecast (with its hourly detail) isn't available
        -- the sensors then keep their last values, as in Node-RED."""
        today = self.hass.states.get(self._today_entity_id) if self._today_entity_id else None
        tomorrow = self.hass.states.get(self._tomorrow_entity_id) if self._tomorrow_entity_id else None
        today_hourly = today.attributes.get("detailedHourly") if today else None
        tomorrow_hourly = tomorrow.attributes.get("detailedHourly") if tomorrow else None
        tomorrow_total = read_float_state(tomorrow)
        if not isinstance(today_hourly, list) or not isinstance(tomorrow_hourly, list) or tomorrow_total is None:
            LOGGER.debug(
                "PwMngt: solar data skipped -- no hourly forecast "
                "(\"detailedHourly\") on the PV forecast entities"
            )
            return None

        return SolarInputs(
            night_consumption=self._read_night_consumption(),
            battery_size_kwh=self._read_battery_size(),
            today_hourly=today_hourly,
            tomorrow_hourly=tomorrow_hourly,
            tomorrow_total=tomorrow_total,
        )

    def _read_night_consumption(self) -> float | None:
        entity_id = pv_properties.consumption_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        value = state.attributes.get("nighttime_average") if state else None
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def _read_battery_size(self) -> float | None:
        entity_id = pv_properties.battery_size_entity_id(self.hass, self._entry)
        size = read_float_state(self.hass.states.get(entity_id)) if entity_id else None
        return size if size else None


class _PwMngtSolarEntity(CoordinatorEntity[PwMngtSolarDataCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PwMngtSolarDataCoordinator,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)

    @property
    def available(self) -> bool:
        # Never "unavailable" just because nothing has run yet.
        return True


class PwMngtSolarDataSensor(_PwMngtSolarEntity):
    """The PV device's solar_data sensor: PwM_SOLAR_DATA_ATTRIBUTES as
    attributes (None until the first run), DATA_SENSOR_STATE as state."""

    @property
    def native_value(self) -> str:
        # Attribute-only "_data" sensor -- see DATA_SENSOR_STATE in
        # data/__init__.py.
        return DATA_SENSOR_STATE

    @property
    def extra_state_attributes(self) -> dict[str, float | None]:
        data = self.coordinator.data or {}
        return {key: data.get(key) for key in PwM_SOLAR_DATA_ATTRIBUTES}


class PwMngtBatteryNightlyTargetSensor(_PwMngtSolarEntity):
    """The PV device's Battery nightly target sensor (%). Unknown until the
    first run, or while Battery size is "None"."""

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get(KEY_BATTERY_NIGHTLY_TARGET)
