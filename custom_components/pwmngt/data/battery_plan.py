"""Battery plan (issue #122): battery_data's minimum-SoC rules laid out hour
by hour, from local midnight today to 06:00 tomorrow -- for the battery
plan card (www/pwm-cards.js, pwm-battery-plan-card).

Not a Node-RED port: Node-RED (and data/battery_data.py, its port) only
works out the minimum SoC for *now*. The hours ahead are worked out by
running the very same calculate_battery_data() once per hour on the
inputs that hour is expected to have, so the rules exist in one place
only -- this module holds no rule of its own, only the assumptions about
the future:
- the SoC stays where it is now, except in a planned grid charge hour
  (Battery charge status: hour_N, charge_target; issue #75's
  today_charge_hours), where it rises evenly towards the charge target,
  and in a sale hour (#64), where it ends at the hour's floor;
- the hour's price is Strømligning's for that hour (with the surcharge);
- no car charges, nothing is blocked, the battery command is "internal"
  (a planned charge hour: charge_now_100);
- today's day plan, sale and PV release are the ones battery_data keeps
  in its ledger; tomorrow's sale comes from Battery charge status.

The hours after midnight are only worked out once tomorrow's real prices
are known (~13:00) -- before that they are "unknown", since the night
charge isn't planned yet and the night price protection can't be known.

It is a plan, not a promise: a car, a blocked charge, the Kostal steps
(#1) and the SoC itself change it. It never feeds back into the control:
battery_data calls it after its own run, on copies, inside a try/except.

Each hour: {"start": ISO time, "rule": a RULE_* key (or "unknown"),
"floor": min SoC % or None, "planned": bool} -- plus price,
held_points, cell_price_min, cell_price_max and margin for the night
price protection (MinSocRule.as_dict()). Past hours carry what was
recorded for them during the day and no floor: the card draws the
Recorder history there.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from .battery_data import (
    BatteryInputs,
    BatteryLedger,
    MinSocRule,
    calculate_battery_data,
)

RULE_UNKNOWN = "unknown"

_PLAN_HOURS = 24 + 6  # today 00:00 to tomorrow 06:00
# The minute a planned hour is worked out for (battery_data rounds to the
# quarter; mid-hour keeps the charge curve between its hourly points).
_PLANNED_MINUTE = 30
_COMMAND_INTERNAL = "internal"
_COMMAND_CHARGE = "charge_now_100"
_STATUS_YES_TODAY = "yes_today"
_STATUS_YES_TOMORROW = "yes_tomorrow"


@dataclass
class DayChargePlan:
    """One day's plan from the night charge (Battery charge status)."""

    # Hours (0-23) of that day with a planned grid charge, and the SoC (%)
    # they charge to; None = no level known.
    charge_hours: list[int] = field(default_factory=list)
    charge_target: float | None = None
    # Issue #64: that day's sale hours and the SoC each stops at -- only
    # used for tomorrow (today's are in battery_data's ledger).
    sale_hours: list[int] = field(default_factory=list)
    sale_floors: list[float] = field(default_factory=list)


def day_charge_plans(
    status: str | None, attributes: dict[str, Any], today: str, soc: float
) -> tuple[DayChargePlan, DayChargePlan]:
    """(today's, tomorrow's) DayChargePlan from Battery charge status:
    "yes_today" is today's own plan, "yes_tomorrow" the one for tomorrow
    (the night after midnight); issue #75's today_charge_hours are today's
    either way and charge today_charge_pct on top of the SoC."""
    today_plan, tomorrow_plan = DayChargePlan(), DayChargePlan()
    flagged = [h for h in range(24) if attributes.get(f"hour_{h}") is True]
    target = _float(attributes.get("charge_target"))
    if status == _STATUS_YES_TODAY:
        today_plan.charge_hours, today_plan.charge_target = flagged, target
    elif status == _STATUS_YES_TOMORROW:
        tomorrow_plan.charge_hours, tomorrow_plan.charge_target = flagged, target
        hours = _ints(attributes.get("sale_hours"))
        floors = attributes.get("sale_floors")
        if isinstance(floors, list) and len(floors) == len(hours):
            try:
                tomorrow_plan.sale_hours = hours
                tomorrow_plan.sale_floors = [float(v) for v in floors]
            except (TypeError, ValueError):
                tomorrow_plan.sale_hours, tomorrow_plan.sale_floors = [], []
    if attributes.get("today_charge_date") == today:
        moved = _ints(attributes.get("today_charge_hours"))
        if moved:
            today_plan.charge_hours = sorted(set(today_plan.charge_hours) | set(moved))
            if today_plan.charge_target is None:
                pct = _float(attributes.get("today_charge_pct")) or 0.0
                today_plan.charge_target = min(100.0, soc + pct)
    return today_plan, tomorrow_plan


def plan_battery_day(
    inputs: BatteryInputs,
    ledger: BatteryLedger,
    min_soc_now: float,
    rule_now: MinSocRule,
    today_plan: DayChargePlan,
    tomorrow_plan: DayChargePlan,
    prices_today: dict[int, float],
    prices_tomorrow: dict[int, float],
    past: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """The plan, one entry per hour -- see the module docstring. inputs and
    ledger are this run's (the ledger after it); neither is changed."""
    now = inputs.now
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    current = now.hour
    soc = inputs.battery_soc
    entries: list[dict[str, Any]] = []
    for offset in range(_PLAN_HOURS):
        is_today = offset < 24
        hour = offset % 24
        # Local wall-clock hours; a DST day is shown as 24 plain hours.
        start = (midnight + timedelta(days=offset // 24)).replace(hour=hour)
        if is_today and hour < current:
            entries.append({**past.get(hour, {"rule": None}), "start": start.isoformat(), "floor": None, "planned": False})
            continue
        if is_today and hour == current:
            entries.append(
                {**rule_now.as_dict(), "start": start.isoformat(), "floor": round(min_soc_now), "planned": False}
            )
            continue
        prices = prices_today if is_today else prices_tomorrow
        price = prices.get(hour)
        if price is None:
            entries.append({"rule": RULE_UNKNOWN, "start": start.isoformat(), "floor": None, "planned": True})
            continue

        plan = today_plan if is_today else tomorrow_plan
        charging = hour in plan.charge_hours
        if charging and plan.charge_target is not None and soc < plan.charge_target:
            left = sum(1 for h in plan.charge_hours if h >= hour)
            soc = soc + (plan.charge_target - soc) / max(left, 1)
        hour_inputs = replace(
            inputs,
            now=start.replace(minute=_PLANNED_MINUTE),
            battery_soc=soc,
            electricity_price=price,
            battery_command=_COMMAND_CHARGE if charging else _COMMAND_INTERNAL,
            charge_status=inputs.charge_status if is_today else (_STATUS_YES_TODAY if plan.charge_hours else ""),
            charger_states=[],
            car_charging_lock=False,
            grid_feeds_battery=False,
            # Today: the day plan and sale in the ledger. Tomorrow: its own
            # sale (the ledger's are today's and drop at midnight).
            day_plan_hours=None,
            day_plan_reserve=None,
            sale_hours=None if is_today else plan.sale_hours,
            sale_floors=None if is_today else plan.sale_floors,
            sale_target=None if is_today or not plan.sale_hours else plan.charge_target,
            pv_release=None,
        )
        min_soc, ledger, _forced, _behind, rule = calculate_battery_data(hour_inputs, ledger)
        entries.append({**rule.as_dict(), "start": start.isoformat(), "floor": min_soc, "planned": True})
        if ledger.selling:
            # The sale runs the battery down to the hour's floor.
            soc = min(soc, float(min_soc))
            ledger = replace(ledger, selling=False)
    return entries


def recorded_hour(entry: dict[str, Any]) -> dict[str, Any]:
    """What is kept of the current hour's entry for the rest of the day
    (the past hours' rule and its numbers, without time and floor)."""
    return {k: v for k, v in entry.items() if k not in ("start", "floor", "planned")}


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _ints(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    try:
        return [int(v) for v in value]
    except (TypeError, ValueError):
        return []
