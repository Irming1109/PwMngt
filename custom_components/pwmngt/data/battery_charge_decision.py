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
DIFF (issue #52, Claus 2026-11-07): switching to "manual" clears the
status to "not_calculated" (and stops a running automatic grid charge,
same as postponing, issue #18), so the sensor never shows a plan nothing
will carry out. Switching back to "auto" decides again right away (same
as cancelling a postponement, issue #44) instead of acting on a plan
made before the switch.

Hourly nudge (Claus, 2026-10-14: "min node red forsøgte at beregne hver
hele klokketime" -- traced from the flow export, not guessed): on the
*same* tab as "Skal batteri lades" itself, Node-RED has its own inject
"Time" (cron "0 0-23 * * *", every hour on the hour) -> a fixed 30 s
delay -> read battery_charge_status ("Status") -> switch (only if it's
"not_calculated" or "yes_today") -> time-range-switch 539e00bafbeb80df
(14:00-23:30) -> a further fixed 5 s delay -> straight back into "Skal
batteri lades". So a stuck "not_calculated" (or an already-decided
"yes_today" worth re-validating as the evening's prices/surplus data
firms up) gets re-tried every hour through the afternoon/evening,
instead of only on the evening-window triggers below or at midnight.
This is a separate chain from data/battery_charge_level.py's own hourly
trigger (different tab, different purpose -- that one re-runs "Bestem
ladeeffekt", not "Skal batteri lades"), even though both happen to use
the same Node-RED cron pattern and a fixed 30 s first delay.

Partial night charge (issue #43, Claus 2026-11-05 -- new, not in
Node-RED): Node-RED charges all the way to target or not at all ("Nej
(overskud)" when tomorrow's 08-16 forecast minus the day consumption
covers the battery). Here tomorrow's forecast is taken cautiously -- the
middle between Solcast's normal and pessimistic (estimate10) estimate,
so the margin follows the day's uncertainty -- and only what that
expected surplus leaves short is charged: charge_target, the shortfall
in % of the battery (rounded up, at least the battery's minimum SoC, at
most target). PV fills the rest up to target during the day. The same
cautious surplus decides "no_surplus", so a day that only just covered
the battery with the normal forecast now gets a small charge ("rather a
little too much than too little"). data/battery_charge_level.py stops the
charge and plans the night (#22) on charge_target.

Value-based night charge (issue #50, Claus 2026-11-07 -- new, not in
Node-RED): Node-RED compares the cheapest charge hours with the 5 most
expensive evening hours 17-23 and then charges for the whole night
17-06. The expensive hours (typically 17-21) paid for the rest, but the
energy for 21-06 was charged at about the price it saves, plus ~15 %
loss. Now the grid charge only covers the evening hours worth it: the
hours 17-23 tomorrow whose price is at least the break-even price -- the
average charge price + the price difference (select
pv_battery_price_difference, compared with the average of the 5 most
expensive evening hours in Node-RED). The loss is left out on purpose
(Claus, 2026-11-07): the user should be able to see from the prices
alone why an hour counts -- the price difference is the margin for it. The night hours 00-06 never count: their
prices aren't known when the decision is made, and they're usually close
to the charge price. The expected consumption in those hours (net of
tomorrow's PV 17-21) x the nightly target's margin becomes the grid
charge's target, before #43 takes the PV surplus off. No hour worth it
-> "no_price"; a cautious PV surplus covering those hours ->
"no_surplus". Once one hour is worth it, the charge always covers 17-21
too (Claus, 2026-11-07): the battery is free from 17 (data/battery_data.py
-- Node-RED's FRI_17_21_UANSET_PRIS), so a charge that leaves 17-21 short
would be spent there anyway, before the later hour it was meant for. The break-even price uses the charge hours the full
target would need -- more hours, a higher average, so on the safe side.
Battery nightly target keeps covering the whole night: it decides how
much PV the battery keeps (data/battery_data.py's charge curve), and PV
kept for the night saves the full purchase price against selling it at
the spot price.

Daytime hours (issue #59, Claus 2026-11-09 -- new, not in Node-RED):
tomorrow's hours 06-16 whose price is at least the same break-even price
count too. The charge adds what the house is expected to use in them
(consumption_data's hourly average without the chargers, minus Solcast's
normal hourly forecast -- not the cautious one: a battery filled from the
grid on a sunny day would sell its PV cheaply) on top of the evening
hours, and those hours alone are worth a charge too (it then still covers
17-21). The evening part is the floor: data/battery_data.py lets the
battery discharge in those daytime hours, but only down to
evening_reserve_soc, so the evening keeps what was planned for it.

Day end at 16:55 (issue #2, Node-RED: inject "16:55" on the same tab):
a "yes_today" still standing then goes back to "not_calculated" and a
running grid charge is stopped -- see _handle_day_end().

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
  "Ikke beregnet"). Cancelling a deferral (pressing the button again)
  decides again right away, outside the evening window too -- Node-RED's
  "Udskydning annulleres" trigger (issue #44).
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
- battery_target (battery_nightly_target) is treated as "not ready yet"
  (skip the run, see _read_target()) rather than Node-RED's own `|| 0`
  fallback, which Claus hit in practice: right after a restart, a run
  before solar_data had computed anything read target as 0 and
  incorrectly locked in "no_charge_needed" (0% of any battery needs 0
  charge time).
- NEW (issue #64, Claus 2026-11-09, no Node-RED counterpart): with
  select.<pv>_pv_battery_sell_price_difference above 0, tonight's charge
  also fills the room left above the house's own plan (up to 95 %) for
  selling in tomorrow's dear hours: an hour 06-22 counts when its sale
  price (Strømligning's spot price without VAT) minus the charge price
  (the purchase price of the extra charge hours, incl. everything, plus
  _SALE_CHARGE_LOSS) is at least that threshold. The best hours first, each
  at most the battery's max power for an hour minus what the house takes
  of it, and only after the last charge hour. Tomorrow's PV is not
  counted on -- it comes on top. sale_hours/sale_floors (the SoC each sale
  hour stops at -- what the house still needs after it) are carried into
  "yes_today" for data/battery_data.py, which runs the sale.
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
from ..select import signal_set_battery_command
from .solar_data import TARGET_MARGIN

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
    # Issue #24: tonight's planned hours data/battery_charge_level.py's
    # night plan (#22) skipped, and the unplanned ones it charged in after
    # the plan -- for the battery card. Cleared with every new plan.
    "skipped_hours",
    "extended_hours",
    # Issue #24: how tonight's plan ended (target reached, or stopped at
    # 16:55) -- {"finished", "target_reached", "target", "hours",
    # "skipped_hours", "extended_hours"}. Kept after the reset to
    # "not_calculated", so the card can show it until a new decision is
    # made (every new plan, postpone and midnight clear it).
    "last_charge",
    # Issue #37: data/battery_charge_level.py's charge watchdog --
    # {"retries", "max_retries", "failed"}, for the battery card.
    "charge_watchdog",
    # Issue #43: tonight's grid charge goes to this SoC (%) -- target minus
    # what tomorrow's PV is expected to add (see module docstring) -- and
    # the cautious PV surplus (kWh) it is based on. Carried into
    # "yes_today" at midnight.
    "charge_target",
    "expected_pv_surplus",
    # Issue #50: the price an evening hour must reach to be worth charging
    # for (kr/kWh), those hours 17-23 tomorrow, the hours the charge covers
    # (those plus always 17-21), and the energy (kWh) the battery is
    # expected to cover in them. Carried into "yes_today".
    "break_even_price",
    "worth_hours",
    "covered_hours",
    "covered_kwh",
    # Issue #59: tomorrow's daytime hours 06-16 worth charging for, the
    # energy (kWh) the battery is expected to cover in them, and the SoC
    # (%) the battery keeps for the evening in them (data/battery_data.py).
    "day_hours",
    "day_kwh",
    "evening_reserve_soc",
    # Issue #64: tomorrow's hours the battery sells in, the SoC (%) each of
    # them stops at (same order), the energy (kWh) expected sold and the
    # expected profit (kr). Carried into "yes_today" (data/battery_data.py).
    "sale_hours",
    "sale_floors",
    "sale_kwh",
    "sale_profit",
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

# consumption_data's evening_average covers 17-21; nighttime_average 17-24
# plus 00-06 -- the rest of it is spread evenly over those 9 hours.
_EVENING_HOURS = range(17, 21)
_LATE_EVENING_HOURS = range(21, 24)
_NIGHT_HOURS = 13
_REST_OF_NIGHT_HOURS = _NIGHT_HOURS - len(_EVENING_HOURS)

# Billing method (select.py, hub "billing_method") that means a fixed
# price -- no spot-price spread to exploit. Old: "Fastpris".
_BILLING_METHOD_FIXED_PRICE = "Fixed price"
# battery_charge_status meaning "deferred" (Old: "Nej (udskudt)") -- see
# the module docstring's note on the initial_state check fix.
_STATUS_DEFERRED = "no_deferred"
_STATUS_NOT_CALCULATED = "not_calculated"

# battery_command (select.py) options meaning tonight's grid charge is
# running -- stopped when the charge is postponed (see _handle_postpone).
_ACTIVE_CHARGE_COMMANDS = frozenset(
    {"charge_now_25", "charge_now_50", "charge_now_75", "charge_now_100"}
)
_COMMAND_INTERNAL = "internal"  # Old: "Intern".

# Best-effort guess, NOT verified against a live Strømligning install
# (same caveat as the Growatt entries in const.py's
# INVERTER_CORE_AUTO_DETECT_NAMES) -- confirm against the entity registry
# on a real install and correct if it's wrong. Modelled on the already-
# confirmed "current_price_vat" translation_key for the current-price
# sensor (see config_flow.py's _AUTO_DETECT_SOURCES).
_STROMLIGNING_TOMORROW_TRANSLATION_KEY = "tomorrow_available_vat"

# Issue #59: the daytime hours (the hour starting at each) that count, and
# the highest SoC the charge may go to with them (data/battery_data.py's
# min_soc ceiling).
_DAY_HOURS = range(6, 17)
_DAY_CHARGE_CEILING = 95

# Issue #64: the hours the battery may sell in (not 23, so a sale never
# runs over midnight into the next day's books), the charge loss the sale
# must pay for, and Strømligning's tomorrow spot price without VAT.
_SALE_HOURS = range(6, 23)
_SALE_CHARGE_LOSS = 0.10
_STROMLIGNING_TOMORROW_SPOT_EX_VAT_TRANSLATION_KEY = "tomorrow_spotprice_ex_vat"

# Triggers.
_EVENING_WINDOW_START = (18, 0)
_EVENING_WINDOW_END = (23, 30)
_DAILY_TRIGGER_HOUR = 23  # Node-RED: inject "Kl 23", cron "00 23 * * *".
# Node-RED: inject "16:55", cron "55 16 * * *" -- see _handle_day_end().
_DAY_END_HOUR_MINUTE = (16, 55)
# (no periodic poll -- every trigger below is a real Node-RED wire)
# Don't recompute while forecast production tomorrow is below this, or
# while the status is already one of _SKIP_RECOMPUTE_STATUSES. Old: the
# "Prognose for imorgen" trigger-state's own constraints -- see module
# docstring.
_FORECAST_TOMORROW_THRESHOLD = 1.0
_SKIP_RECOMPUTE_STATUSES = {"not_calculated", "no_price", "no_deferred"}

# Hourly nudge (see module docstring). Node-RED: switch comparing against
# "Ikke beregnet"/"Ja (i dag)", then time-range-switch 539e00bafbeb80df.
_HOURLY_RECHECK_STATUSES = {_STATUS_NOT_CALCULATED, "yes_today"}

# What "yes_tomorrow" carries into "yes_today" at midnight besides the hour
# flags (issues #43, #50).
_CARRIED_TO_TODAY = frozenset(
    {
        "charge_target",
        "expected_pv_surplus",
        "break_even_price",
        "worth_hours",
        "covered_hours",
        "covered_kwh",
        "day_hours",
        "day_kwh",
        "evening_reserve_soc",
        "sale_hours",
        "sale_floors",
        "sale_kwh",
        "sale_profit",
    }
)
_HOURLY_RECHECK_WINDOW_START = (14, 0)
_HOURLY_RECHECK_WINDOW_END = (23, 30)
_HOURLY_RECHECK_FIRST_DELAY_SECONDS = 30
_HOURLY_RECHECK_SECOND_DELAY_SECONDS = 5


def signal_postpone_battery_charge(entry_id: str) -> str:
    """Dispatcher signal button.py's postpone_battery_charge button sends
    on press -- PwMngtBatteryChargeDecisionSensor listens and sets
    battery_charge_status to "no_deferred" (see module docstring)."""
    return f"{DOMAIN}_{entry_id}_postpone_battery_charge"


# What data/battery_charge_level.py reports about the current hour
# (signal_charge_hour_marked(), issue #24).
HOUR_MARK_SKIPPED = "skipped"
HOUR_MARK_EXTENDED = "extended"
HOUR_MARK_CHARGED = "charged"


def signal_charge_hour_marked(entry_id: str) -> str:
    """Dispatcher signal data/battery_charge_level.py sends after a run in
    which the night plan skipped the current hour, or a charge ran in it.
    Payload: (HOUR_MARK_*, hour 0-23)."""
    return f"{DOMAIN}_{entry_id}_charge_hour_marked"


def signal_charge_watchdog(entry_id: str) -> str:
    """Dispatcher signal data/battery_charge_level.py sends when its charge
    watchdog (issue #37) changes. Payload: the charge_watchdog dict."""
    return f"{DOMAIN}_{entry_id}_charge_watchdog"


def signal_battery_charge_target_reached(entry_id: str) -> str:
    """Dispatcher signal data/battery_charge_level.py sends once the
    battery's SoC reaches its target while status is "yes_today" (Node-
    RED: the "SOC >= target" gate) -- PwMngtBatteryChargeDecisionSensor
    listens and resets status back to "not_calculated"."""
    return f"{DOMAIN}_{entry_id}_battery_charge_target_reached"


def _auto_detect_tomorrow_prices_entity_id(
    hass: HomeAssistant, translation_key: str = _STROMLIGNING_TOMORROW_TRANSLATION_KEY
) -> str | None:
    """Find the single Strømligning entity exposing tomorrow's prices
    (binary_sensor, "prices" attribute when "on"). Only ever
    auto-detected -- see the module docstring. None unless exactly one
    match exists."""
    registry = er.async_get(hass)
    matches = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DEPENDENCY_STROMLIGNING
        and entry.translation_key == translation_key
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
    # Issue #43: Solcast's pessimistic (estimate10) 08-16 forecast for
    # tomorrow, and the battery's minimum SoC (the lowest charge target).
    expected_solar_tomorrow_8_16_p10: float | None = None
    battery_min_soc: float = 0.0
    # Issue #50: consumption_data's night (17-06, without the chargers, and
    # as measured) and evening (17-21) averages (kWh), and tomorrow's PV
    # forecast 17-21 (kWh). None = unknown.
    night_consumption: float | None = None
    night_consumption_raw: float | None = None
    evening_consumption: float | None = None
    # Issue #58: consumption_data's 21-24 average (without the chargers
    # when known, kWh). None = unknown.
    late_evening_consumption: float | None = None
    # Issue #59: consumption_data's hourly averages for _DAY_HOURS (without
    # the chargers) and tomorrow's normal PV forecast for the same hours
    # (kWh) -- lists in that order. None = unknown.
    day_hourly_consumption: list[float | None] | None = None
    pv_hourly_tomorrow: list[float | None] | None = None
    pv_forecast_17_21_tomorrow: float | None = None
    # Issue #64: tomorrow's sale prices (spot without VAT, kr/kWh, hour
    # 0-23; None = unknown) and the sale threshold (kr/kWh, 0 = off).
    sale_prices_tomorrow: list[float] | None = None
    sale_price_difference: float = 0.0


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def cautious_pv_forecast(normal: float | None, pessimistic: float | None) -> float | None:
    """Issue #43: tomorrow's 08-16 forecast taken cautiously -- the middle
    between Solcast's normal and pessimistic (estimate10) estimate, so the
    margin follows how uncertain the day is. The normal one alone while
    the pessimistic one is missing."""
    if normal is None:
        return None
    if pessimistic is None:
        return normal
    return (normal + min(pessimistic, normal)) / 2


def partial_charge_target(
    battery_target: float, battery_size: float, expected_surplus: float | None, battery_min_soc: float
) -> float:
    """Issue #43: the SoC (%) tonight's grid charge goes to -- the part of
    target that tomorrow's expected PV surplus (kWh) doesn't cover,
    rounded up, at least battery_min_soc, at most battery_target. The full
    target while the surplus is unknown."""
    if expected_surplus is None or battery_size <= 0:
        return battery_target
    missing_pct = battery_target - max(expected_surplus, 0.0) / battery_size * 100
    return float(min(battery_target, max(battery_min_soc, math.ceil(missing_pct))))


def evening_hour_consumption(inputs: BatteryChargeInputs) -> dict[int, float]:
    """See evening_hourly_need() -- for tomorrow's evening."""
    return evening_hourly_need(
        night_consumption=inputs.night_consumption,
        night_consumption_raw=inputs.night_consumption_raw,
        evening_consumption=inputs.evening_consumption,
        late_evening_consumption=inputs.late_evening_consumption,
        pv_17_21=inputs.pv_forecast_17_21_tomorrow,
        battery_target=inputs.battery_target,
        battery_min_soc=inputs.battery_min_soc,
        battery_size=inputs.battery_size,
    )


def evening_hourly_need(
    night_consumption: float | None,
    night_consumption_raw: float | None,
    evening_consumption: float | None,
    late_evening_consumption: float | None,
    pv_17_21: float | None,
    battery_target: float,
    battery_min_soc: float,
    battery_size: float,
) -> dict[int, float]:
    """Issue #50: the energy (kWh) the battery is expected to cover in each
    hour 17-23 tomorrow. 17-21 is the evening average (scaled like the night
    average, without the chargers) minus tomorrow's PV 17-21; 21-24 gets the
    rest of the night average spread evenly over its 9 hours. While the
    averages are missing, the night is what the nightly target covers
    (without its margin and minimum SoC), spread evenly.

    DIFF (issue #58, Claus 2026-11-09): 21-24 is its own measured average
    when consumption_data has it (at most the rest of the night) -- the
    even spread underestimated it by ~30 % at Kasper's, whose house uses
    more before midnight than after."""
    night = night_consumption
    if night is None or night <= 0:
        night = max(battery_target - battery_min_soc, 0.0) / 100 * battery_size / TARGET_MARGIN
        evening = night * len(_EVENING_HOURS) / _NIGHT_HOURS
        pv = 0.0
    else:
        raw = night_consumption_raw
        if evening_consumption is not None and raw:
            evening = evening_consumption * night / raw
        else:
            evening = night * len(_EVENING_HOURS) / _NIGHT_HOURS
        evening = min(evening, night)
        pv = max(pv_17_21 or 0.0, 0.0)
    per_evening_hour = max(evening - pv, 0.0) / len(_EVENING_HOURS)
    per_late_hour = max(night - evening, 0.0) / _REST_OF_NIGHT_HOURS
    late = late_evening_consumption
    if late is not None and late >= 0 and night_consumption:
        per_late_hour = min(late, max(night - evening, 0.0)) / len(_LATE_EVENING_HOURS)
    hourly = {hour: per_evening_hour for hour in _EVENING_HOURS}
    hourly.update({hour: per_late_hour for hour in _LATE_EVENING_HOURS})
    return hourly


def worth_charging(
    prices_tomorrow: list[float], avg_charge_price: float, price_difference: float, hourly_kwh: dict[int, float]
) -> tuple[float, list[int], list[int], float]:
    """Issue #50: (break-even price, the hours 17-23 tomorrow at or above
    it, the hours the charge covers, the energy in kWh the battery covers
    in them). An hour is worth charging for when its price pays the charge
    price plus the price difference (no loss -- see the module
    docstring). When one is, the charge always
    covers 17-21 too -- see the module docstring."""
    break_even = round(avg_charge_price + price_difference, 2)
    worth = [hour for hour in sorted(hourly_kwh) if prices_tomorrow[hour] >= break_even]
    covered = sorted(set(worth) | set(_EVENING_HOURS)) if worth else []
    return break_even, worth, covered, sum(hourly_kwh[hour] for hour in covered)


def worth_day_hours(
    prices_tomorrow: list[float],
    break_even: float,
    consumption: list[float | None] | None,
    pv: list[float | None] | None,
) -> tuple[list[int], float]:
    """Issue #59: (tomorrow's daytime hours at or above the break-even
    price where the house is expected to use more than PV gives, the
    energy in kWh the battery covers in them). Nothing while the hourly
    consumption is unknown."""
    if not consumption or len(consumption) != len(_DAY_HOURS):
        return [], 0.0
    pv_known = bool(pv) and len(pv) == len(_DAY_HOURS)
    hours: list[int] = []
    total = 0.0
    for index, hour in enumerate(_DAY_HOURS):
        used = consumption[index]
        if used is None:
            continue
        sun = (pv[index] or 0.0) if pv_known else 0.0
        need = max(used - sun, 0.0)
        if need > 0 and prices_tomorrow[hour] >= break_even:
            hours.append(hour)
            total += need
    return hours, total


def day_hour_needs(
    consumption: list[float | None] | None, pv: list[float | None] | None
) -> dict[int, float]:
    """Issue #64: the energy (kWh) the battery is expected to cover in each
    _DAY_HOURS hour tomorrow -- the house's average minus the normal PV
    forecast, as worth_day_hours(). Empty while the consumption is unknown."""
    if not consumption or len(consumption) != len(_DAY_HOURS):
        return {}
    pv_known = bool(pv) and len(pv) == len(_DAY_HOURS)
    needs: dict[int, float] = {}
    for index, hour in enumerate(_DAY_HOURS):
        used = consumption[index]
        if used is None:
            continue
        sun = (pv[index] or 0.0) if pv_known else 0.0
        needs[hour] = max(used - sun, 0.0)
    return needs


def plan_sale(
    sale_prices: list[float],
    charge_price: float,
    threshold: float,
    max_power_kw: float,
    house_kwh: dict[int, float],
    room_kwh: float,
    candidates: list[int],
) -> tuple[list[int], float, float, dict[int, float]]:
    """Issue #64: (the sale hours, kWh sold in all, expected profit in kr,
    kWh sold per hour). An hour counts when its sale price minus what a
    sold kWh cost (charge_price plus the charge loss) is at least
    threshold; the best first, each at most max_power_kw for an hour minus
    the house's own use in it, until room_kwh is used."""
    cost = charge_price / (1 - _SALE_CHARGE_LOSS)
    margins = {hour: sale_prices[hour] - cost for hour in candidates}
    left = room_kwh
    sold: dict[int, float] = {}
    for hour in sorted(margins, key=lambda h: (-margins[h], h)):
        if margins[hour] < threshold or left <= 0.05:
            break
        energy = min(max(max_power_kw - house_kwh.get(hour, 0.0), 0.0), left)
        if energy <= 0:
            continue
        sold[hour] = energy
        left -= energy
    hours = sorted(sold)
    total = sum(sold.values())
    profit = sum(energy * margins[hour] for hour, energy in sold.items())
    return hours, total, profit, sold


def sale_floors(
    hours: list[int],
    house_kwh: dict[int, float],
    covered: list[int],
    battery_min_soc: float,
    battery_size: float,
    sold: dict[int, float] | None = None,
) -> list[int]:
    """Issue #64: the SoC (%) each sale hour stops at -- what the house
    still needs from the battery in the planned hours after it (the
    covered day and evening hours) and what the later sale hours sell, on
    top of the minimum SoC."""
    floors = []
    sold = sold or {}
    for hour in hours:
        later = sum(house_kwh.get(h, 0.0) for h in covered if h > hour)
        later += sum(energy for h, energy in sold.items() if h > hour)
        floors.append(
            min(
                _DAY_CHARGE_CEILING,
                int(battery_min_soc + math.ceil(later * TARGET_MARGIN / battery_size * 100)),
            )
        )
    return floors


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

    # Issue #43: cautious PV surplus tomorrow, and the part of target it
    # leaves for tonight's grid charge.
    pv_forecast = cautious_pv_forecast(
        inputs.expected_solar_tomorrow_8_16, inputs.expected_solar_tomorrow_8_16_p10
    )
    expected_surplus = (
        pv_forecast - inputs.day_consumption
        if pv_forecast is not None and inputs.day_consumption is not None
        else None
    )
    def hours_needed(target_pct: float) -> int:
        charge_kwh = (inputs.battery_size * target_pct) / 100
        return math.ceil((charge_kwh * 1000) / battery_max_charge * _CHARGETIME_SAFETY_MARGIN)

    prices_sorted_day = sorted(price_tomorrow_day, key=lambda item: item[1])
    prices_sorted_evening = sorted(price_tomorrow_evening, key=lambda item: item[1], reverse=True)

    # The charge hours the full target would need (#43) -- their average is
    # the charge price issue #50's break-even is based on (see module
    # docstring).
    full_charge_target = partial_charge_target(
        inputs.battery_target, inputs.battery_size, expected_surplus, inputs.battery_min_soc
    )
    full_hours = hours_needed(full_charge_target)

    if full_hours <= 0:
        attributes: dict[str, Any] = {
            key: None for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
            if not key.startswith("hour_")
        }
        for hour in range(24):
            attributes[f"hour_{hour}"] = False
        return "no_charge_needed", attributes

    avg_charge_price = _average([price for _hour, price in prices_sorted_day[:full_hours]])
    evening_hourly = evening_hour_consumption(inputs)
    break_even, worth_hours, covered_hours, covered_kwh = worth_charging(
        inputs.prices_tomorrow,
        avg_charge_price,
        inputs.price_difference_threshold,
        evening_hourly,
    )
    # Issue #59: the daytime hours worth it -- a charge for them alone still
    # covers 17-21 (see module docstring).
    day_hours, day_kwh = worth_day_hours(
        inputs.prices_tomorrow,
        break_even,
        inputs.day_hourly_consumption,
        inputs.pv_hourly_tomorrow,
    )
    if day_hours and not covered_hours:
        covered_hours = list(_EVENING_HOURS)
        covered_kwh = sum(evening_hourly[hour] for hour in covered_hours)
    # Issue #50: tonight's grid charge only covers the hours worth it (and
    # always 17-21) -- issue #59: plus the daytime hours worth it, on top
    # of the evening, which stays the floor during the day --
    # then #43 takes tomorrow's expected PV surplus off.
    evening_reserve = min(
        inputs.battery_target,
        inputs.battery_min_soc + math.ceil(covered_kwh * TARGET_MARGIN / inputs.battery_size * 100),
    )
    day_pct = math.ceil(day_kwh * TARGET_MARGIN / inputs.battery_size * 100) if day_kwh > 0 else 0
    worth_target = min(max(_DAY_CHARGE_CEILING, evening_reserve), evening_reserve + day_pct)
    charge_target = partial_charge_target(
        worth_target, inputs.battery_size, expected_surplus, inputs.battery_min_soc
    )

    # Issue #64: the room left above the house's own plan, filled for
    # selling -- see the module docstring.
    sale_hours: list[int] = []
    sale_kwh = 0.0
    sale_profit = 0.0
    floors: list[int] = []
    if (
        inputs.sale_price_difference > 0
        and inputs.sale_prices_tomorrow
        and len(inputs.sale_prices_tomorrow) == 24
        and inputs.battery_size > 0
    ):
        house_target = charge_target
        house_kwh = dict(day_hour_needs(inputs.day_hourly_consumption, inputs.pv_hourly_tomorrow))
        house_kwh.update(evening_hourly)
        base_hours = max(hours_needed(house_target), 0)
        # The extra charge hours after the house's own, cheapest first --
        # each charges what hours_needed() counts on per hour.
        extra = [price for _hour, price in prices_sorted_day[base_hours:]]
        kwh_per_charge_hour = battery_max_charge / 1000 / _CHARGETIME_SAFETY_MARGIN / TARGET_MARGIN
        room_kwh = max(_DAY_CHARGE_CEILING - house_target, 0.0) / 100 * inputs.battery_size / TARGET_MARGIN
        max_power_kw = (
            inputs.battery_max_charge_raw
            if inputs.battery_max_charge_raw is not None
            else _BATTERY_MAX_CHARGE_FALLBACK
        ) / 1000
        covered_later = sorted(set(day_hours) | set(covered_hours))
        candidates = list(_SALE_HOURS)

        def best_sale(hours_allowed: list[int]) -> tuple[list[int], float, float, dict[int, float]]:
            """The most profitable number of extra charge hours: each one
            more adds energy, but at its own (higher) price."""
            best: tuple[list[int], float, float, dict[int, float]] = ([], 0.0, 0.0, {})
            for count in range(1, len(extra) + 1):
                result = plan_sale(
                    inputs.sale_prices_tomorrow,
                    _average(extra[:count]),
                    inputs.sale_price_difference,
                    max_power_kw,
                    house_kwh,
                    min(room_kwh, count * kwh_per_charge_hour),
                    hours_allowed,
                )
                if result[2] > best[2]:
                    best = result
            return best

        for _attempt in range(3):
            sale_hours, sale_kwh, sale_profit, sold = best_sale(candidates)
            sale_pct = math.ceil(sale_kwh * TARGET_MARGIN / inputs.battery_size * 100) if sale_kwh > 0 else 0
            charge_target = min(_DAY_CHARGE_CEILING, house_target + sale_pct)
            charge_hours = [hour for hour, _price in prices_sorted_day[: max(hours_needed(charge_target), 1)]]
            last_charge_hour = max(charge_hours)
            if not sale_hours or min(sale_hours) > last_charge_hour:
                break
            # The battery must be charged before it can sell.
            candidates = [hour for hour in candidates if hour > last_charge_hour]
            sale_hours, sale_kwh, sale_profit, sold = [], 0.0, 0.0, {}
            charge_target = house_target
        floors = sale_floors(
            sale_hours, house_kwh, covered_later, inputs.battery_min_soc, inputs.battery_size, sold
        )

    cheapest_hours = prices_sorted_day[: max(hours_needed(charge_target), 1)]
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
        "charge_target": charge_target,
        "expected_pv_surplus": None if expected_surplus is None else round(expected_surplus, 2),
        "break_even_price": break_even,
        "worth_hours": worth_hours,
        "covered_hours": covered_hours,
        "covered_kwh": round(covered_kwh, 2),
        "day_hours": day_hours,
        "day_kwh": round(day_kwh, 2),
        "evening_reserve_soc": evening_reserve if day_hours else None,
        "sale_hours": sale_hours,
        "sale_floors": floors,
        "sale_kwh": round(sale_kwh, 2),
        "sale_profit": round(sale_profit, 2),
    }
    for hour in range(24):
        attributes[f"hour_{hour}"] = hour in cheapest_hour_set

    if inputs.billing_method == _BILLING_METHOD_FIXED_PRICE:
        status = "no_price"
    elif inputs.previous_status == _STATUS_DEFERRED:
        status = _STATUS_DEFERRED
    elif not worth_hours and not day_hours and not sale_hours:
        # Issue #50: no evening hour pays for the charge -- issue #59: and
        # no daytime hour either -- issue #64: and no sale.
        status = "no_price"
    elif not sale_hours and expected_surplus is not None and expected_surplus >= min(
        battery_kwh, (covered_kwh + day_kwh) * TARGET_MARGIN
    ):
        # Issue #43: the cautious surplus (Node-RED: the normal forecast)
        # -- issue #50: enough when it covers the hours worth charging for.
        status = "no_surplus"
    else:
        status = "yes_tomorrow"

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
        self._pending_hourly_recheck: Any = None

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

        # Issue #52: clear on "manual", decide again on "auto".
        battery_mode_entity_id = pv_properties.battery_mode_entity_id(self.hass, self._entry)
        if battery_mode_entity_id:
            self.async_on_remove(
                event.async_track_state_change_event(
                    self.hass, [battery_mode_entity_id], self._handle_battery_mode_change
                )
            )

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
        self.async_on_remove(
            event.async_track_time_change(
                self.hass,
                self._handle_day_end,
                hour=_DAY_END_HOUR_MINUTE[0],
                minute=_DAY_END_HOUR_MINUTE[1],
                second=0,
            )
        )
        # Hourly nudge -- see module docstring. Node-RED: inject "Time",
        # cron "0 0-23 * * *".
        self.async_on_remove(
            event.async_track_time_change(
                self.hass, self._handle_hourly_recheck, minute=0, second=0
            )
        )

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_charge_hour_marked(self._entry.entry_id),
                self._handle_hour_marked,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_charge_watchdog(self._entry.entry_id),
                self._handle_charge_watchdog,
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
        # (Node-RED: ha-select "PV batteri ladning pris difference"). Issue
        # #64: the sale threshold too, so a changed sale plan shows at once.
        unconditional_watched = []
        for entity_id in (
            pv_properties.battery_price_difference_entity_id(self.hass, self._entry),
            pv_properties.battery_sell_price_difference_entity_id(self.hass, self._entry),
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
        "no_deferred".

        DIFF (issue #18): postponing also stops a running charge_now_*
        command. Node-RED left it running -- data/battery_charge_level.py
        only re-decides (and stops) while the status is "yes_today", so
        nothing would ever stop it."""
        cancelled = self._attr_native_value == _STATUS_DEFERRED
        if cancelled:
            self._attr_native_value = _STATUS_NOT_CALCULATED
        else:
            self._attr_native_value = _STATUS_DEFERRED
            self._stop_running_charge()
        self._attr_extra_state_attributes = {
            key: None for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
        }
        self.async_write_ha_state()
        if cancelled:
            # Issue #44 (Node-RED: trigger "Udskydning annulleres", outside
            # the evening window too): decide again right away, so a
            # cancellation late in the evening still gives tonight's plan.
            self._run()

    @callback
    def _handle_battery_mode_change(self, state_event) -> None:
        """Issue #52 -- see module docstring. Only a real switch between
        "auto" and "manual" counts; a restore at startup (no old state)
        or unavailable leaves the status alone."""
        old_state = state_event.data.get("old_state")
        new_state = state_event.data.get("new_state")
        if old_state is None or new_state is None:
            return
        if old_state.state == "auto" and new_state.state == "manual":
            self._attr_native_value = _STATUS_NOT_CALCULATED
            self._attr_extra_state_attributes = {
                key: None for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
            }
            self._stop_running_charge()
            self.async_write_ha_state()
        elif old_state.state == "manual" and new_state.state == "auto":
            self._run()

    def _stop_running_charge(self) -> None:
        """Set battery_command to "internal" if a charge_now_* command is
        running -- same "Lader vi ?" gate as data/battery_charge_level.py,
        so the sell decision's or the hold's own command is left alone."""
        entity_id = pv_properties.battery_command_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state not in _ACTIVE_CHARGE_COMMANDS:
            return
        async_dispatcher_send(
            self.hass, signal_set_battery_command(self._entry.entry_id), _COMMAND_INTERNAL
        )

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
            # Issue #43: tonight's charge target carries over with the hours.
            hours = {
                key: value
                for key, value in self._attr_extra_state_attributes.items()
                if key.startswith("hour_") or key in _CARRIED_TO_TODAY
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
    def _handle_charge_watchdog(self, watchdog: dict) -> None:
        """Issue #37: the charge watchdog's state, for the battery card --
        whatever the status (a manual charge has its own)."""
        attributes = dict(self._attr_extra_state_attributes)
        attributes["charge_watchdog"] = watchdog
        self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()

    @callback
    def _handle_hour_marked(self, mark: str, hour: int) -> None:
        """Issue #24: keep skipped_hours/extended_hours for the battery
        card while tonight's plan runs. A charge in a skipped hour (the SoC
        fell further, so the plan started it after all) takes it off the
        skipped list again."""
        if self._attr_native_value != "yes_today":
            return
        attributes = dict(self._attr_extra_state_attributes)
        skipped = list(attributes.get("skipped_hours") or [])
        extended = list(attributes.get("extended_hours") or [])
        if mark == HOUR_MARK_SKIPPED and hour not in skipped:
            skipped.append(hour)
        elif mark == HOUR_MARK_EXTENDED and hour not in extended:
            extended.append(hour)
        elif mark == HOUR_MARK_CHARGED and hour in skipped:
            skipped.remove(hour)
        else:
            return
        attributes["skipped_hours"] = sorted(skipped)
        attributes["extended_hours"] = sorted(extended)
        self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()

    @callback
    def _handle_day_end(self, _now=None) -> None:
        """16:55 (Node-RED: inject "16:55" -> "Ja (i dag)" ->
        ha-sensor "Ikke beregnet" -> "Lader vi ?" -> "Intern", tab
        "Batteri styring NY"): today's planned hours (0-16) are over, so a
        "yes_today" that is still standing -- the target wasn't reached --
        goes back to "not_calculated" for the evening's recompute, and a
        grid charge still running is stopped. A manual "Charge now" (issue
        #17) runs on: select.py ignores the automation's "internal" then.
        Not gated by battery_mode, same as _handle_midnight()."""
        if self._attr_native_value != "yes_today":
            return
        self._finish_plan(target_reached=False)
        self._stop_running_charge()

    @callback
    def _handle_target_reached(self) -> None:
        """data/battery_charge_level.py says the battery's SoC has reached
        its target while status was "yes_today" (Node-RED: the "SOC >=
        target" gate, "Lader vi?"/"Intern" reset chain) -- tonight's grid
        charge is done, so go back to "not_calculated" for the next
        scheduled recompute."""
        self._finish_plan(target_reached=True)

    def _finish_plan(self, target_reached: bool) -> None:
        """Back to "not_calculated", keeping how tonight's plan ended in
        last_charge (issue #24) for the battery card."""
        attributes = self._attr_extra_state_attributes
        last_charge = {
            "finished": dt_util.now().isoformat(timespec="seconds"),
            "target_reached": target_reached,
            # Issue #43: what tonight's charge went to.
            "target": attributes.get("charge_target") or self._read_target(),
            "hours": [hour for hour in range(24) if attributes.get(f"hour_{hour}") is True],
            "skipped_hours": list(attributes.get("skipped_hours") or []),
            "extended_hours": list(attributes.get("extended_hours") or []),
        }
        self._attr_native_value = _STATUS_NOT_CALCULATED
        self._attr_extra_state_attributes = {
            key: None for key in PwM_BATTERY_CHARGE_DECISION_ATTRIBUTES
        }
        self._attr_extra_state_attributes["last_charge"] = last_charge
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._pending_hourly_recheck is not None:
            self._pending_hourly_recheck()
            self._pending_hourly_recheck = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_hourly_recheck(self, _now=None) -> None:
        """Hourly nudge -- see module docstring. Node-RED's own fixed
        30 s delay happens before the status read, not before this
        trigger itself."""
        self._pending_hourly_recheck = event.async_call_later(
            self.hass,
            _HOURLY_RECHECK_FIRST_DELAY_SECONDS,
            self._handle_hourly_recheck_after_first_delay,
        )

    @callback
    def _handle_hourly_recheck_after_first_delay(self, _now=None) -> None:
        self._pending_hourly_recheck = None
        if self._attr_native_value not in _HOURLY_RECHECK_STATUSES:
            return
        if not self._in_hourly_recheck_window():
            return
        self._pending_hourly_recheck = event.async_call_later(
            self.hass,
            _HOURLY_RECHECK_SECOND_DELAY_SECONDS,
            self._handle_hourly_recheck_run,
        )

    @callback
    def _handle_hourly_recheck_run(self, _now=None) -> None:
        self._pending_hourly_recheck = None
        self._run()

    def _in_hourly_recheck_window(self) -> bool:
        now = dt_util.now()
        return (
            _HOURLY_RECHECK_WINDOW_START
            <= (now.hour, now.minute)
            <= _HOURLY_RECHECK_WINDOW_END
        )

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

        battery_target = self._read_target()
        if battery_target is None:
            # battery_nightly_target hasn't computed a value yet -- see
            # _read_target()'s own docstring. Leave the sensor as is and
            # wait for the next trigger instead of locking in a wrong
            # result from a 0 that only means "not ready".
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
            expected_solar_tomorrow_8_16_p10=self._read_solar_forecast_tomorrow_8_16(
                "pv_forecast_8_16_tomorrow_p10"
            ),
            battery_min_soc=pv_properties.battery_min_soc(self.hass, self._entry),
            day_consumption=self._read_consumption_daytime_adjusted(),
            night_consumption=self._read_consumption(
                "nighttime_average_adjusted"
            ) or self._read_consumption("nighttime_average"),
            night_consumption_raw=self._read_consumption("nighttime_average"),
            evening_consumption=self._read_consumption("evening_average"),
            late_evening_consumption=self._read_consumption(
                "late_evening_average_adjusted"
            ) or self._read_consumption("late_evening_average"),
            day_hourly_consumption=self._read_hourly_list(
                pv_properties.consumption_data_entity_id, "day_hourly_average_adjusted"
            ),
            pv_hourly_tomorrow=self._read_hourly_list(
                pv_properties.solar_data_entity_id, "pv_forecast_hourly_6_17_tomorrow"
            ),
            pv_forecast_17_21_tomorrow=self._read_solar_forecast_tomorrow_8_16(
                "pv_forecast_17_21_tomorrow"
            ),
            battery_size=self._read_float_pv(pv_properties.battery_size_entity_id, 0.0) or 0.0,
            battery_target=battery_target,
            price_difference_threshold=self._read_float_pv(
                pv_properties.battery_price_difference_entity_id, _PRICE_DIFFERENCE_FALLBACK
            ),
            previous_status=self._attr_native_value or "not_calculated",
            sale_prices_tomorrow=self._read_sale_prices_tomorrow(),
            sale_price_difference=self._read_float_pv(
                pv_properties.battery_sell_price_difference_entity_id, 0.0
            ) or 0.0,
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

    def _read_target(self) -> float | None:
        """None while battery_nightly_target hasn't computed a value yet
        (typically: solar_data's own coordinator hasn't run once since a
        restart). DIFF (Claus, 2026-10-14): Node-RED's own fallback here
        was `|| 0`, same issue as _read_consumption_daytime_adjusted()
        could have, but Claus hit this one in practice -- right after a
        restart, this ran before solar_data had computed anything, read
        target as 0, and incorrectly locked in "no_charge_needed" (0% of
        any battery needs 0 charge time) instead of waiting for a real
        value. _run() below now skips the whole run while this is None,
        same as it already does for missing tomorrow prices, rather than
        computing a wrong result from a 0 that only means "not ready"."""
        entity_id = pv_properties.battery_nightly_target_entity_id(self.hass, self._entry)
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None

    def _read_solar_forecast_tomorrow_8_16(
        self, attribute: str = "pv_forecast_8_16_tomorrow"
    ) -> float | None:
        entity_id = pv_properties.solar_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        try:
            value = state.attributes.get(attribute)
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def _read_sale_prices_tomorrow(self) -> list[float] | None:
        """Issue #64: Strømligning's spot prices without VAT for tomorrow
        (kr/kWh, hour 0-23), or None. Only while the sensor is "on" -- an
        "off" one carries forecast prices (Strømligning, 2026-11)."""
        entity_id = _auto_detect_tomorrow_prices_entity_id(
            self.hass, _STROMLIGNING_TOMORROW_SPOT_EX_VAT_TRANSLATION_KEY
        )
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state != "on":
            return None
        raw = state.attributes.get("prices")
        if not isinstance(raw, list) or len(raw) < 23:
            return None
        try:
            prices = [round(float(item["price"]), 2) for item in raw[:24]]
        except (TypeError, KeyError, ValueError):
            return None
        if len(prices) == 23:
            prices.append(prices[22])  # DST, as the purchase prices.
        return prices

    def _read_consumption_daytime_adjusted(self) -> float | None:
        return self._read_consumption("daytime_average_adjusted")

    def _read_hourly_list(self, entity_id_of, attribute: str) -> list[float | None] | None:
        """Issue #59: one of the hourly lists (one value per _DAY_HOURS
        hour) on consumption_data or solar_data, or None."""
        entity_id = entity_id_of(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        values = state.attributes.get(attribute) if state is not None else None
        if not isinstance(values, list) or len(values) != len(_DAY_HOURS):
            return None
        result: list[float | None] = []
        for value in values:
            try:
                result.append(None if value is None else float(value))
            except (TypeError, ValueError):
                result.append(None)
        return result

    def _read_consumption(self, attribute: str) -> float | None:
        """One of consumption_data's averages (kWh), or None."""
        entity_id = pv_properties.consumption_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        try:
            value = state.attributes.get(attribute)
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
