"""Battery charge decision: the PV device's battery_charge_status sensor
-- whether the battery should be charged from the grid tonight, based on
tomorrow's spot price spread.

Port of Claus's Node-RED function "Skal batteri lades" (tab "Batteri
styring NY"), which writes sensor.pv_battery_charge. data/battery_data.py
already reads this sensor's state (only "yes_today" matters there) and
its avg_cheapest_hours attribute.

select.battery_mode (select.py, Old: select.pv_battery_mode -- declared
in Node-RED but never actually read anywhere there) gates _run() too:
while it's "manual", _run() does nothing at all (Claus, 2026-10-14) --
see data/battery_charge_level.py's own docstring for the matching gate
on the actuation side. _handle_midnight() and the postpone button are
NOT gated by it -- see _handle_midnight()'s own docstring.

One piece of data/battery_charge_level.py's own hourly trigger chain
loops back here: when its "Status" read comes back "not_calculated"
instead of "yes_today", Node-RED's companion branch (switch
5c8b46b117d60fef -> time-range-switch 539e00bafbeb80df, 14:00-23:30) is
meant to re-run "Skal batteri lades" itself -- deliberately left
unported (not wired anywhere in this module either), so a stale
"not_calculated" during that window just waits for the normal
recompute triggers below instead of also getting an hourly nudge.

Deliberate differences from Node-RED (each marked "DIFF" below):
- The "Udskyd ladning" (postpone) button (button.py's
  postpone_battery_charge, Old: button.pv_battery_udskyd) ported as a
  genuine toggle, same as Node-RED's own "Udskudt ?" api-current-state
  node (Claus, 2026-10-14: "den skal virke som den gør i node-red ...
  den skal toggle"): already deferred ("no_deferred") -> back to
  "not_calculated"; anything else -> "no_deferred". Both directions
  clear all attributes (Node-RED's own "Udskyd"/"Ikke beregnet"
  ha-sensor nodes here both list no attributes at all). It also resets
  back to "not_calculated" at local midnight if still deferred -- new
  behaviour, not part of Node-RED's own Midnat chain; see
  _handle_midnight() for the two transitions that ARE Node-RED's own
  ("Ja (i morgen)" -> "Ja (i dag)", "Nej (pris)"/"Nej (overskud)" ->
  "Ikke beregnet"). The "Udskydning annulleres" trigger (which bypassed
  the evening window entirely when cancelling a deferral mid-evening)
  isn't ported -- cancelling a deferral now simply means pressing the
  button again, waiting for the next scheduled recompute, or midnight.
- The *check* for a deferred status, inside "Skal batteri lades" itself,
  is fixed rather than ported literally (Claus, 2026-10-14): Node-RED
  compares `initial_state === "Udskudt"` -- the bare word -- against the
  sensor's own previous state, but every place that actually *sets* the
  status writes "Nej (udskudt)" instead, so in Node-RED that comparison
  can never match and a deferral is silently overwritten on the very next
  recompute. Here it compares against "no_deferred" (this module's own
  name for "Nej (udskudt)") instead, so a deferral actually survives a
  recompute -- same priority as Node-RED intended (checked right after
  the fixed-price billing method, before the surplus/price-difference
  checks).
- The trigger that gates recomputation on "is tomorrow's PV forecast in
  yet" (Node-RED node "Prognose for imorgen") has a typo in Claus's own
  flow: its second constraint names entity_id "select.pv_battery_charge",
  which doesn't exist anywhere -- the values it's compared against
  ("Ikke beregnet, Nej (pris), Udskudt") are battery_charge_status
  values, so this is read as meaning sensor.pv_battery_charge (this
  sensor's own previous state), confirmed with Claus 2026-10-14. Ported
  as intended: don't recompute while forecast production tomorrow < 1, or
  while the current status is already "not_calculated"/"no_price"/
  "no_deferred". sensor.forventet_produktion_i_morgen is
  ENTITY_KEY_PV_FORECAST_TOMORROW (confirmed the same entity, Claus
  2026-10-14).
Node-RED inputs not carried over because PwMngt doesn't have an
abstracted equivalent yet, or because they're honestly unused:
(none -- every input this function's result actually depends on now has
a PwMngt source, see the module's own _run()).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import event, restore_state
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    DEPENDENCY_STROMLIGNING,
    DOMAIN,
    ENTITY_KEY_PV_FORECAST_TOMORROW,
)
from ..devices import pv_device_info
from ..helpers.state_helper import read_float_state
from ..properties import hub_properties, pv_properties

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Attributes. Each notes its Node-RED attribute name on
# sensor.pv_battery_charge.
# ---------------------------------------------------------------------------

PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES: list[str] = [
    # Average price (kr/kWh) of the cheapest hours picked for grid
    # charging, and of the most expensive evening hours they're compared
    # against. Old: "gns_billigste_timer" / "gns_dyreste_timer".
    "avg_cheapest_hours",
    "avg_most_expensive_hours",
    # avg_most_expensive_hours - avg_cheapest_hours. Old: "forskel_pris".
    "price_difference",
    # Whether tomorrow's hour N (0-23) is one of the cheapest hours picked
    # for grid charging. Old: "hour0".."hour23" (no underscore).
    *[f"hour_{hour}" for hour in range(24)],
]

PwM_BATTERY_CHARGE_DECISION_SENSORS: list[SensorEntityDescription] = [
    # Old key="pv_battery_charge" (sensor.pv_battery_charge), from Node-RED.
    # Old name="Battery charge"
    SensorEntityDescription(
        key="battery_charge_status",
        name="Battery charge status",
        icon="mdi:battery-charging-outline",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "not_calculated",    # Old: "Ikke beregnet"
            "yes_today",         # Old: "Ja (i dag)"
            "yes_tomorrow",      # Old: "Ja (i morgen)"
            "no_charge_needed",  # Old: "Nej (intet ladebehov)"
            "no_surplus",        # Old: "Nej (overskud)"
            "no_price",          # Old: "Nej (pris)"
            "no_deferred",       # Old: "Nej (udskudt)"
        ],
    ),
]

# Node-RED fallbacks for inputs that are missing/unknown.
_NUM_MOST_EXPENSIVE_HOURS = 5
_BATTERY_MAX_CHARGE_FALLBACK = 3000.0  # W
_CHARGE_POWER_UTILIZATION = 0.75
_PRICE_DIFFERENCE_FALLBACK = 1.2
_CHARGETIME_SAFETY_MARGIN = 1.10
_DAY_END_HOUR = 17  # Day window: hours 0-16, evening: 17-23.

# Billing method (select.py, hub "billing_method") that means a fixed
# price -- no spot-price spread to exploit. Old: "Fastpris".
_BILLING_METHOD_FIXED_PRICE = "Fixed price"
# battery_charge_status meaning "deferred" (Old: "Nej (udskudt)") -- see
# the module docstring's note on the initial_state check fix.
_STATUS_DEFERRED = "no_deferred"
_STATUS_NOT_CALCULATED = "not_calculated"

# Best-effort guess, NOT verified against a live Strømligning install
# (same caveat as the Growatt entries in const.py's
# INVERTER_CORE_AUTO_DETECT_NAMES) -- confirm against the entity registry
# on a real install and correct if it's wrong. Modelled on the already-
# confirmed "current_price_vat" translation_key for the current-price
# sensor (see config_flow.py's _AUTO_DETECT_SOURCES).
_STROMLIGNING_TOMORROW_TRANSLATION_KEY = "tomorrow_available_vat"

# Triggers.
_EVENING_WINDOW_START = (18, 0)
_EVENING_WINDOW_END = (23, 30)
_DAILY_TRIGGER_HOUR = 23  # Node-RED: inject "Kl 23", cron "00 23 * * *".
# (no periodic poll -- every trigger below is a real Node-RED wire)
# Don't recompute while forecast production tomorrow is below this, or
# while the status is already one of _SKIP_RECOMPUTE_STATUSES. Old: the
# "Prognose for imorgen" trigger-state's own constraints -- see module
# docstring.
_FORECAST_TOMORROW_THRESHOLD = 1.0
_SKIP_RECOMPUTE_STATUSES = {"not_calculated", "no_price", "no_deferred"}


def signal_postpone_battery_charge(entry_id: str) -> str:
    """Dispatcher signal button.py's postpone_battery_charge button sends
    on press -- PwMngtBatteryChargeDecisionSensor listens and sets
    battery_charge_status to "no_deferred" (see module docstring)."""
    return f"{DOMAIN}_{entry_id}_postpone_battery_charge"


def signal_battery_charge_target_reached(entry_id: str) -> str:
    """Dispatcher signal data/battery_charge_level.py sends once the
    battery's SoC reaches its target while status is "yes_today" (Node-
    RED: the "SOC >= target" gate) -- PwMngtBatteryChargeDecisionSensor
    listens and resets status back to "not_calculated"."""
    return f"{DOMAIN}_{entry_id}_battery_charge_target_reached"


def _auto_detect_tomorrow_prices_entity_id(hass: HomeAssistant) -> str | None:
    """Find the single Strømligning entity exposing tomorrow's prices
    (binary_sensor, "prices" attribute when "on"). Only ever
    auto-detected -- see the module docstring. None unless exactly one
    match exists."""
    registry = er.async_get(hass)
    matches = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DEPENDENCY_STROMLIGNING
        and entry.translation_key == _STROMLIGNING_TOMORROW_TRANSLATION_KEY
    ]
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# Pure calculations -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


@dataclass
class BatteryChargeInputs:
    """Everything one run reads from Home Assistant, already resolved to
    plain values (Node-RED fallbacks applied by the caller)."""

    # Tomorrow's hourly prices (kr/kWh), hour 0-23, already rounded to 2
    # decimals. Exactly 24 entries -- the caller fills a missing hour 23
    # (DST) the same way Node-RED did.
    prices_tomorrow: list[float]
    battery_max_charge_raw: float | None
    billing_method: str
    expected_solar_tomorrow_8_16: float | None
    day_consumption: float | None
    battery_size: float
    battery_target: float
    price_difference_threshold: float
    # The sensor's own current status, going into this run (Node-RED:
    # initial_state, see module docstring). Checked right after billing
    # method, before the surplus/price-difference branches.
    previous_status: str


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def calculate_battery_charge_decision(
    inputs: BatteryChargeInputs,
) -> tuple[str, dict[str, Any]]:
    """One run of Node-RED's "Skal batteri lades". Returns (status,
    attributes). Pure -- testable on its own. Caller (_run() below) is
    responsible for not calling this at all when tomorrow's prices aren't
    available yet -- Node-RED throws and produces no output in that case,
    which here means: don't touch the sensor, keep showing the last run's
    value.
    """
    battery_max_charge = (
        inputs.battery_max_charge_raw
        if inputs.battery_max_charge_raw is not None
        else _BATTERY_MAX_CHARGE_FALLBACK
    ) * _CHARGE_POWER_UTILIZATION

    price_tomorrow_day = [(h, inputs.prices_tomorrow[h]) for h in range(0, _DAY_END_HOUR)]
    price_tomorrow_evening = [
        (h, inputs.prices_tomorrow[h]) for h in range(_DAY_END_HOUR, 24)
    ]

    battery_kwh = (inputs.battery_size * inputs.battery_target) / 100
    chargetime_needed = math.ceil(
        (battery_kwh * 1000) / battery_max_charge * _CHARGETIME_SAFETY_MARGIN
    )

    prices_sorted_day = sorted(price_tomorrow_day, key=lambda item: item[1])
    prices_sorted_evening = sorted(price_tomorrow_evening, key=lambda item: item[1], reverse=True)

    if chargetime_needed <= 0:
        attributes: dict[str, Any] = {
            "avg_cheapest_hours": None,
            "avg_most_expensive_hours": None,
            "price_difference": None,
        }
        for hour in range(24):
            attributes[f"hour_{hour}"] = False
        return "no_charge_needed", attributes

    cheapest_hours = prices_sorted_day[:chargetime_needed]
    most_expensive_hours = prices_sorted_evening[:_NUM_MOST_EXPENSIVE_HOURS]

    avg_cheapest_hours = round(_average([price for _hour, price in cheapest_hours]), 2)
    avg_most_expensive_hours = round(
        _average([price for _hour, price in most_expensive_hours]), 2
    )
    price_difference = round(avg_most_expensive_hours - avg_cheapest_hours, 2)

    cheapest_hour_set = {hour for hour, _price in cheapest_hours}
    attributes = {
        "avg_cheapest_hours": avg_cheapest_hours,
        "avg_most_expensive_hours": avg_most_expensive_hours,
        "price_difference": price_difference,
    }
    for hour in range(24):
        attributes[f"hour_{hour}"] = hour in cheapest_hour_set

    if inputs.billing_method == _BILLING_METHOD_FIXED_PRICE:
        status = "no_price"
    elif inputs.previous_status == _STATUS_DEFERRED:
        status = _STATUS_DEFERRED
    elif (
        inputs.expected_solar_tomorrow_8_16 is not None
        and inputs.day_consumption is not None
        and (inputs.expected_solar_tomorrow_8_16 - inputs.day_consumption) >= battery_kwh
    ):
        status = "no_surplus"
    elif price_difference >= inputs.price_difference_threshold:
        status = "yes_tomorrow"
    else:
        status = "no_price"

    return status, attributes


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


@dataclass
class PwMngtBatteryChargeDecisionStoredData(restore_state.ExtraStoredData):
    """What PwMngtBatteryChargeDecisionSensor restores across a restart:
    just enough to show a sensible value until the next run, same idea as
    other _data.py modules' restore handling."""

    status: str | None
    attributes: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "attributes": self.attributes}

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtBatteryChargeDecisionStoredData":
        return cls(
            status=restored.get("status"),
            attributes=dict(restored.get("attributes") or {}),
        )


class PwMngtBatteryChargeDecisionSensor(restore_state.RestoreEntity, SensorEntity):
    """The PV device's battery_charge_status sensor. See the module
    docstring for what triggers a run."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = "not_calculated"
        self._attr_extra_state_attributes = {
            key: None for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
        }
        self._entry = entry
        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._forecast_tomorrow_entity_id = entity_map.get(ENTITY_KEY_PV_FORECAST_TOMORROW) or None

    @property
    def extra_restore_state_data(self) -> PwMngtBatteryChargeDecisionStoredData:
        return PwMngtBatteryChargeDecisionStoredData(
            status=self._attr_native_value,
            attributes=dict(self._attr_extra_state_attributes),
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        restored = await self.async_get_last_extra_data()
        if restored is not None:
            data = PwMngtBatteryChargeDecisionStoredData.from_dict(restored.as_dict())
            if data.status is not None:
                self._attr_native_value = data.status
                self._attr_extra_state_attributes = {
                    key: data.attributes.get(key)
                    for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
                }

        # Postpone button (button.py) and the local-midnight reset.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_postpone_battery_charge(self._entry.entry_id),
                self._handle_postpone,
            )
        )
        self.async_on_remove(
            event.async_track_time_change(
                self.hass, self._handle_midnight, hour=0, minute=0, second=0
            )
        )

        # data/battery_charge_level.py (the "SOC >= target" gate) -- the
        # charge finished, so stop treating tonight as "still charging".
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_battery_charge_target_reached(self._entry.entry_id),
                self._handle_target_reached,
            )
        )

        tomorrow_entity_id = _auto_detect_tomorrow_prices_entity_id(self.hass)
        if not tomorrow_entity_id:
            LOGGER.warning(
                "PwMngt: could not find the Strømligning 'tomorrow prices' "
                "entity (looked for translation_key '%s') -- '%s' will "
                "stay at its last known value",
                _STROMLIGNING_TOMORROW_TRANSLATION_KEY,
                self.entity_description.key,
            )
            return
        self._tomorrow_entity_id = tomorrow_entity_id

        # Price-difference threshold change -- unconditional, window-gated
        # (Node-RED: ha-select "PV batteri ladning pris difference").
        unconditional_watched = []
        for entity_id in (
            pv_properties.battery_price_difference_entity_id(self.hass, self._entry),
        ):
            if entity_id:
                unconditional_watched.append(entity_id)
        if unconditional_watched:
            self.async_on_remove(
                event.async_track_state_change_event(
                    self.hass, unconditional_watched, self._handle_unconditional_trigger
                )
            )

        # Daily at 23:00 -- unconditional, window-gated (trivially true at
        # 23:00). Node-RED: inject "Kl 23".
        self.async_on_remove(
            event.async_track_time_change(
                self.hass,
                self._handle_unconditional_trigger,
                hour=_DAILY_TRIGGER_HOUR,
                minute=0,
                second=0,
            )
        )

        # PV forecast tomorrow >= 1, and the current status isn't already
        # one that should be left alone -- window-gated (Node-RED:
        # "Prognose for imorgen", outputInitially -- see module docstring).
        if self._forecast_tomorrow_entity_id:
            self.async_on_remove(
                event.async_track_state_change_event(
                    self.hass,
                    [self._forecast_tomorrow_entity_id],
                    self._handle_forecast_trigger,
                )
            )
            self._handle_forecast_trigger(None)  # outputInitially: true
        else:
            LOGGER.warning(
                "PwMngt: no PV forecast tomorrow entity configured "
                "(Options -> Solar PV Plant) -- '%s' will only recompute "
                "on a price-difference change or at 23:00",
                self.entity_description.key,
            )

    @callback
    def _handle_postpone(self) -> None:
        """The postpone_battery_charge button was pressed -- a toggle,
        same as Node-RED's own "Udskudt ?" node (see module docstring):
        already deferred -> back to "not_calculated"; anything else ->
        "no_deferred"."""
        if self._attr_native_value == _STATUS_DEFERRED:
            self._attr_native_value = _STATUS_NOT_CALCULATED
        else:
            self._attr_native_value = _STATUS_DEFERRED
        self._attr_extra_state_attributes = {
            key: None for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
        }
        self.async_write_ha_state()

    @callback
    def _handle_midnight(self, _now=None) -> None:
        """Local midnight (Node-RED: inject "Midnat"). Three independent
        checks, same as Node-RED's own three parallel branches off that
        inject (plus one addition):
        - "yes_tomorrow" -> "yes_today" (Node-RED: "Ja (i morgen) -
          true?" -> "Ja (i dag)"): last evening's "tomorrow" plan is now
          tonight's. The hour_0.._23 cheap-hour flags carry forward
          unchanged; avg_cheapest_hours/avg_most_expensive_hours/
          price_difference do NOT (Node-RED's own "Ja (i dag)" ha-sensor
          node only lists hour0..hour23 in its own attributes -- so
          those three clear to None here too).
        - "no_price"/"no_surplus" -> "not_calculated" (Node-RED: "Nej"
          -> "Ikke beregnet"): fully cleared, ready for the next
          scheduled recompute.
        - "no_deferred" -> "not_calculated": Claus's own addition
          (2026-10-14), not part of Node-RED's own Midnat chain -- see
          module docstring.
        Any other status (already "yes_today", "not_calculated",
        "no_charge_needed") is left alone.
        """
        if self._attr_native_value == "yes_tomorrow":
            hours = {
                key: value
                for key, value in self._attr_extra_state_attributes.items()
                if key.startswith("hour_")
            }
            self._attr_native_value = "yes_today"
            self._attr_extra_state_attributes = {
                key: None for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
            }
            self._attr_extra_state_attributes.update(hours)
            self.async_write_ha_state()
        elif self._attr_native_value in ("no_price", "no_surplus", _STATUS_DEFERRED):
            self._attr_native_value = _STATUS_NOT_CALCULATED
            self._attr_extra_state_attributes = {
                key: None for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
            }
            self.async_write_ha_state()

    @callback
    def _handle_target_reached(self) -> None:
        """data/battery_charge_level.py says the battery's SoC has reached
        its target while status was "yes_today" (Node-RED: the "SOC >=
        target" gate, "Lader vi?"/"Intern" reset chain) -- tonight's grid
        charge is done, so go back to "not_calculated" for the next
        scheduled recompute."""
        self._attr_native_value = _STATUS_NOT_CALCULATED
        self._attr_extra_state_attributes = {
            key: None for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
        }
        self.async_write_ha_state()

    def _in_evening_window(self) -> bool:
        now = dt_util.now()
        return _EVENING_WINDOW_START <= (now.hour, now.minute) <= _EVENING_WINDOW_END

    @callback
    def _handle_unconditional_trigger(self, _event_or_time=None) -> None:
        if self._in_evening_window():
            self._run()

    @callback
    def _handle_forecast_trigger(self, _state_event) -> None:
        if not self._in_evening_window():
            return
        forecast = read_float_state(
            self.hass.states.get(self._forecast_tomorrow_entity_id)
        )
        if forecast is None or forecast < _FORECAST_TOMORROW_THRESHOLD:
            return
        if self._attr_native_value in _SKIP_RECOMPUTE_STATUSES:
            return
        self._run()

    @callback
    def _run(self) -> None:
        if pv_properties.is_battery_mode_manual(self.hass, self._entry):
            # select.battery_mode (select.py) is "manual" -- the whole
            # charge/no-charge automation is switched off (Claus,
            # 2026-10-14). Leave the sensor exactly as it is.
            return

        tomorrow_entity_id = getattr(self, "_tomorrow_entity_id", None)
        state = self.hass.states.get(tomorrow_entity_id) if tomorrow_entity_id else None
        if state is None or state.state != "on":
            # Node-RED: throws ("No electricity price data available for
            # tomorrow") and produces no output. Leave the sensor as is.
            return

        raw_prices = state.attributes.get("prices")
        if not isinstance(raw_prices, list) or not raw_prices:
            return

        prices: list[float | None] = [None] * 24
        for index, item in enumerate(raw_prices[:24]):
            try:
                prices[index] = round(float(item["price"]), 2)
            except (TypeError, KeyError, ValueError):
                return
        if prices[23] is None and prices[22] is not None:
            # DST: an hour can be missing on the day the clock changes.
            prices[23] = prices[22]
        if any(p is None for p in prices):
            return

        inputs = BatteryChargeInputs(
            prices_tomorrow=prices,  # type: ignore[arg-type]
            battery_max_charge_raw=self._read_float_pv(
                pv_properties.battery_max_charge_effect_entity_id, None
            ),
            billing_method=self._read_state_hub(
                hub_properties.billing_method_entity_id, ""
            ),
            expected_solar_tomorrow_8_16=self._read_solar_forecast_tomorrow_8_16(),
            day_consumption=self._read_consumption_daytime_adjusted(),
            battery_size=self._read_float_pv(pv_properties.battery_size_entity_id, 0.0) or 0.0,
            battery_target=self._read_target(),
            price_difference_threshold=self._read_float_pv(
                pv_properties.battery_price_difference_entity_id, _PRICE_DIFFERENCE_FALLBACK
            ),
            previous_status=self._attr_native_value or "not_calculated",
        )

        status, attributes = calculate_battery_charge_decision(inputs)
        self._attr_native_value = status
        self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()

    # -- input readers (Node-RED fallback when missing/unknown) ----------

    def _read_float_pv(self, lookup, default: float | None) -> float | None:
        entity_id = lookup(self.hass, self._entry)
        value = read_float_state(self.hass.states.get(entity_id)) if entity_id else None
        return default if value is None else value

    def _read_state_hub(self, lookup, default: str) -> str:
        entity_id = lookup(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state

    def _read_target(self) -> float:
        entity_id = pv_properties.battery_nightly_target_entity_id(self.hass, self._entry)
        value = read_float_state(self.hass.states.get(entity_id)) if entity_id else None
        return value if value is not None else 0.0

    def _read_solar_forecast_tomorrow_8_16(self) -> float | None:
        entity_id = pv_properties.solar_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        try:
            value = state.attributes.get("pv_forecast_8_16_tomorrow")
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def _read_consumption_daytime_adjusted(self) -> float | None:
        entity_id = pv_properties.consumption_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        try:
            value = state.attributes.get("daytime_average_adjusted")
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
