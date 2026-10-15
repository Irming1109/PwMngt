"""Battery data: the PV device's battery_data sensor -- the battery's
minimum SoC right now (its state), plus a running ledger of where the
battery's content came from (grid or PV, and at what price).

Port of Claus's Node-RED function "Min_SOC beregning" (tab "Batteri
styring NY"), which writes sensor.pv_battery_status. Ported from Claus's
corrected export (the one superseding the 30-09-2026 export, which
contained an extra "backup" copy of this function and an unrelated
relay-switching subflow that are not part of this automation and were
not ported). Only the calculation is ported: in Node-RED the state is
then written to the inverter's own minimum-SoC number (via "Batteri
status - onsket min_soc" in "Ha data - opsaetning") -- PwMngt does not do
that; battery_data only computes and shows the value.

This is meant to be a literal, 100%-identical port, including the
following Node-RED quirks that are reproduced on purpose rather than
"fixed":
- If the "grid charged today" counter is lower than last run's value --
  which happens every day at local midnight when that counter resets --
  Node-RED does not treat it as "reset from 0". It simply refuses to
  book that run's SoC rise as grid OR PV: the rise is silently dropped
  from the soc_grid/soc_pv/soc_cells ledger (soc_previous still moves
  forward, so the lost percentage points can never be recovered). This
  is a known rough edge in Claus's flow, kept here deliberately because
  an exact port was asked for -- not something to quietly improve.
- Both "sensor.batteri_soc" and the grid-charged-today sensor must have a
  real value or the whole run is skipped (no sensor update at all, same
  as returning null in Node-RED) -- there's no fallback to "SoC 5" or
  "grid charged 0" the way other, less important inputs get a fallback.
- Inputs Node-RED reads but never uses in the result (consumption
  averages, today's/tomorrow's PV forecast, battery size, the
  "overskud"/"batteri_ledigt" helper values) are not read here either --
  they don't affect min_soc, soc_grid/pv or the cell ledger in Node-RED.
- forced_charge is "on"/"off" (lowercase, what is_forced_charging() and
  Home Assistant expect) rather than Node-RED's "On"/"Off" -- a spelling
  choice local to this entity, not a behavioural difference. Node-RED's
  one latch that reads this value back ("stop forcing once SoC reaches
  min_soc") is dead code there: by the time it runs, tvangslad is
  already 0 whenever min_soc <= SoC, so it's left out here too.
- The per-percent-point cells are one list attribute ("soc_cells"),
  not 100 separate attributes soc_1..soc_100.

New in this export (not present in the previously-ported version):
- A per-cell price floor. Grid cells bought at a price more than
  NAT_PRIS_TILLAEG (0.30 kr/kWh) cheaper than *right now*'s price are
  protected from discharge at night (21:00-05:59); PV cells and grid
  cells within that margin are free to use. This replaces the old
  all-or-nothing "protect every grid cell at night" rule.
- A "restart plan": if the battery command blocks charging
  ("block_charging") or blocks the battery outright ("block_battery")
  during 06:00-16:59, that moment (today's date, time of day, SoC) is
  remembered. While that plan is still "active" (same calendar day,
  recorded after 09:00), the charge curve (wanted_charge_now) restarts
  from that point instead of from 09:00/SoC 5 -- so resuming after a
  daytime block doesn't demand catching up to where the curve "should"
  already be.
- The night-time (21:00-05:59) and target-end-time grid protections are
  now capped (at the current SoC, respectively the target) rather than
  being able to push min_soc arbitrarily high, and the target-end-time
  protection no longer applies before 06:00 or during 17:00-20:59.

Deliberate difference from Node-RED (DIFF, Claus 2026-10-15): forced_charge
also reads "on" while battery_command is actively one of the charge_now_*
options (select.py, Old: "Lad nu - 25/50/75/100% effekt"), on top of the
original min_soc > soc check. Node-RED's own "Min_SOC beregning" never
checked this, so forced_charge stayed "Off" during an active charge_now_*
command whenever this calculation's own min_soc ladder had no reason to
raise min_soc above soc -- most commonly while battery_charge_status is
already "yes_today" (the grid charge is being driven by a separate
"charge_now_*" select write instead, see data/battery_charge_level.py, so
this calculation's own grid_charge_planned_today branch deliberately holds
min_soc at the current SoC). Confirmed against a live mismatch between
PwMngt and Node-RED during the 15-10-2026 parallel-validation window:
data/balance_data.py's is_forced_charging() gate reads this same
forced_charge value to decide whether to cancel the battery's power out of
the grid-balance reading -- under-reporting forced_charge there made
balance_data.py wrongly net the battery's charging draw out of the
balance, showing a false surplus while actually buying from the grid to
charge. The same fix was also given to Claus for his own Node-RED "Min_SOC
beregning"/Balance functions, as separate, independent code outside this
repo.

Triggers (same as Node-RED, see PwMngtBatteryDataSensor): Battery SoC
changes, Battery nightly target changes (only 00:01-16:59), target end
time / battery command / either charger's state changes, every 15 min
06:00-23:45, and once at startup. Runs at most once per 5 seconds (Node-RED
"limit 1 msg/5s"); triggers in between are merged into one run.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import event, restore_state
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    DOMAIN,
    ENTITY_KEY_BATTERY_GRID_CHARGED_DAY,
    ENTITY_KEY_BATTERY_SOC,
    ENTITY_KEY_SPOT_ELECTRICITY_PRICE,
)
from ..devices import CHARGERS, pv_device_info
from ..helpers.state_helper import read_float_state
from ..properties import pv_properties

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Attributes. Each notes its Node-RED attribute name on
# sensor.pv_battery_status.
# ---------------------------------------------------------------------------

PwM_BATTERY_DATA_ATTRIBUTES: list[str] = [
    # Percentage points of the current SoC charged from the grid / from PV
    # (simple sums). Old: "soc_grid" / "soc_pv".
    "soc_grid",
    "soc_pv",
    # One entry per percentage point in the battery: 0 = PV, anything else
    # = the electricity price (kr/kWh) when that point was charged from the
    # grid. Always sorted most expensive grid first, PV last. Old: soc_1 ..
    # soc_100 (one attribute each).
    "soc_cells",
    # len(soc_cells). Old: "soc_count".
    "soc_count",
    # "on" while min_soc > SoC, i.e. the battery is being charged from the
    # grid on purpose. Read through pv_properties.is_forced_charging().
    # Old: "forced_charge" (and before that input_boolean.tvangslad_batteri).
    "forced_charge",
    # Average price of the cheapest hours picked for grid charging, passed
    # through from Battery charge status. Old: "gns_billigste_timer".
    "avg_cheapest_hours",
    # This run's inputs, kept for the next run. Old: "soc_previous" /
    # "grid_charge_previous".
    "soc_previous",
    "grid_charge_previous",
    # Running totals of percentage points discharged: from the grid share,
    # and overall. Never reset except when the battery empties out (SoC
    # <= 5). Old: "grid_afladning_pct_cum" / "total_afladning_pct_cum".
    "grid_afladning_pct_cum",
    "total_afladning_pct_cum",
]

PwM_BATTERY_DATA_SENSORS: list[SensorEntityDescription] = [
    # Old key="pv_battery_status" (sensor.pv_battery_status), from Node-RED.
    # Old name="Battery status"
    # Has a real state (min SoC in %), so it doesn't use DATA_SENSOR_STATE.
    SensorEntityDescription(
        key="battery_data",
        name="Battery data",
        icon="mdi:battery-lock",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
    ),
]

# Legacy entities replaced by this module, removed from the entity
# registry on setup -- see async_remove_legacy_battery_entities().
_LEGACY_UNIQUE_ID_SUFFIXES: list[str] = [
    # The "Forced charge" scaffold sensor -- now battery_data's
    # "forced_charge" attribute (v0.3.21).
    "pv_forced_charge",
]

# Node-RED fallbacks for inputs that are missing/unknown.
_DEFAULT_TARGET = 5.0
_DEFAULT_TARGET_END_TIME = "16:45"
_DEFAULT_PRICE = 1.5

# Charger state (sensor.py, PwM_CHARGER_SENSORS "state") that stops the
# house battery from discharging. Old: "Aktiv - manuel".
_CHARGER_STATE_ACTIVE_MANUAL = "Active - manual"
# Battery commands (select.py, "battery_command") that pause charging.
# Old: "Bloker ladning" / "Bloker batteri".
_COMMAND_BLOCK_CHARGING = "block_charging"
_COMMAND_BLOCK_BATTERY = "block_battery"
# Battery commands (select.py, "battery_command") that mean "actively
# charging from the grid right now" -- same set as
# data/battery_charge_level.py's own _ACTIVE_CHARGE_COMMANDS (duplicated
# rather than imported, to avoid a cross-module coupling between the two
# "Min_SOC"/"Bestem ladeeffekt" ports). Old: "Lad nu - 25/50/75/100%
# effekt". Used below to fix forced_charge (DIFF, Claus 2026-10-15).
_ACTIVE_CHARGE_COMMANDS = frozenset(
    {"charge_now_25", "charge_now_50", "charge_now_75", "charge_now_100"}
)
# Battery charge status (sensor.py, "battery_charge_status") meaning a grid
# charge is planned later today. Old: "Ja (i dag)".
_CHARGE_STATUS_YES_TODAY = "yes_today"

# Minimum SoC floor/ceiling, as in Node-RED.
_MIN_SOC_FLOOR = 5
_MIN_SOC_CEILING = 90

# A grid cell may only be discharged if the current price is at least this
# much (kr/kWh) above the cell's own purchase price. Old: NAT_PRIS_TILLAEG.
_NIGHT_PRICE_MARGIN = 0.30

# Node-RED's charge curve from 09:00 (or a later "restart" point, see
# _restart_plan()) to the target end time: 32 points, the share of
# (target - start SoC) the battery should have reached.
_CHARGE_CURVE: tuple[float, ...] = (
    0.05, 0.05, 0.05, 0.05,
    0.06, 0.07, 0.08, 0.09,
    0.10, 0.15, 0.18, 0.20,
    0.25, 0.28, 0.32, 0.38,
    0.43, 0.50, 0.58, 0.62,
    0.65, 0.68, 0.72, 0.75,
    0.81, 0.84, 0.88, 0.91,
    0.94, 0.96, 0.98, 1.00,
)
_CHARGE_CURVE_START_MINUTES = 9 * 60

# Triggers.
_RATE_LIMIT_SECONDS = 5
_TARGET_TRIGGER_START = (0, 1)    # 00:01
_TARGET_TRIGGER_END = (16, 59)    # 16:59
_PERIODIC_HOURS = list(range(6, 24))
_PERIODIC_MINUTES = [0, 15, 30, 45]


@callback
def async_remove_legacy_battery_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the entities this module replaces from the entity registry,
    so they don't linger as "unavailable" forever."""
    registry = er.async_get(hass)
    for suffix in _LEGACY_UNIQUE_ID_SUFFIXES:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{suffix}"
        )
        if entity_id:
            LOGGER.info("PwMngt: removing legacy battery entity %s", entity_id)
            registry.async_remove(entity_id)


# ---------------------------------------------------------------------------
# Pure calculations -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


@dataclass
class BatteryInputs:
    """Everything one run reads from Home Assistant, already resolved to
    plain values (Node-RED fallbacks applied by the caller)."""

    now: datetime  # local time
    battery_soc: float
    target: float
    target_end_time: str  # "HH:MM"
    electricity_price: float
    battery_command: str
    charge_status: str
    # From Battery charge status; None when it has no value (Node-RED: 999).
    avg_cheapest_hours: float | None
    # Today's grid-charged energy counter. Guaranteed not None by the time
    # this reaches calculate_battery_data() -- PwMngtBatteryDataSensor._run()
    # skips the whole run otherwise, same as Node-RED's top-of-function guard.
    grid_charged_day: float
    charger_states: list[str] = field(default_factory=list)


@dataclass
class BatteryLedger:
    """What carries over from one run to the next (restored across
    restarts, see PwMngtBatteryDataStoredData)."""

    # None = no valid previous run (first run ever, or nothing restored).
    soc_previous: float | None = None
    grid_charge_previous: float | None = None
    soc_grid: float = 0.0
    soc_pv: float = 0.0
    soc_cells: list[float] = field(default_factory=list)
    avg_cheapest_hours: float | None = None
    grid_afladning_pct_cum: float = 0.0
    total_afladning_pct_cum: float = 0.0
    # The "restart plan" (Node-RED: context 'plan_genstart'). None = no
    # plan recorded yet. plan_date is a "YYYY-MM-DD" local-date string.
    plan_date: str | None = None
    plan_start_minutes: float | None = None
    plan_start_soc: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "soc_previous": self.soc_previous,
            "grid_charge_previous": self.grid_charge_previous,
            "soc_grid": self.soc_grid,
            "soc_pv": self.soc_pv,
            "soc_cells": list(self.soc_cells),
            "avg_cheapest_hours": self.avg_cheapest_hours,
            "grid_afladning_pct_cum": self.grid_afladning_pct_cum,
            "total_afladning_pct_cum": self.total_afladning_pct_cum,
            "plan_date": self.plan_date,
            "plan_start_minutes": self.plan_start_minutes,
            "plan_start_soc": self.plan_start_soc,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "BatteryLedger":
        return cls(
            soc_previous=restored.get("soc_previous"),
            grid_charge_previous=restored.get("grid_charge_previous"),
            soc_grid=float(restored.get("soc_grid") or 0.0),
            soc_pv=float(restored.get("soc_pv") or 0.0),
            soc_cells=[float(v) for v in restored.get("soc_cells") or []],
            avg_cheapest_hours=restored.get("avg_cheapest_hours"),
            grid_afladning_pct_cum=float(restored.get("grid_afladning_pct_cum") or 0.0),
            total_afladning_pct_cum=float(restored.get("total_afladning_pct_cum") or 0.0),
            plan_date=restored.get("plan_date"),
            plan_start_minutes=restored.get("plan_start_minutes"),
            plan_start_soc=restored.get("plan_start_soc"),
        )


def _js_round(value: float) -> int:
    """Round half up, like JavaScript's Math.round (Python's round() rounds
    half to even)."""
    return math.floor(value + 0.5)


def _sort_cells(cells: list[float]) -> list[float]:
    """Most expensive grid cell first, PV cells (0) last. Node-RED
    treats any non-zero value as grid -- a grid charge at exactly 0 kr
    would count as PV, same here."""
    grid = sorted((v for v in cells if v != 0), reverse=True)
    pv = [v for v in cells if v == 0]
    return grid + pv


def _parse_hh_mm(value: str, fallback: str = _DEFAULT_TARGET_END_TIME) -> int:
    """Minutes after midnight for "HH:MM"."""
    try:
        hours, minutes = (int(part) for part in value.split(":"))
    except (AttributeError, ValueError):
        hours, minutes = (int(part) for part in fallback.split(":"))
    return hours * 60 + minutes


def wanted_charge_now(
    now_minutes: int,
    target: float,
    end_minutes: int,
    start_minutes: int = _CHARGE_CURVE_START_MINUTES,
    start_soc: float = _MIN_SOC_FLOOR,
) -> float:
    """The SoC the battery should have reached by now, following
    _CHARGE_CURVE from start_minutes/start_soc to the target end time.
    start_minutes/start_soc default to 09:00/SoC 5, but a "restart plan"
    (see _restart_plan()) can move them later in the day. Node-RED:
    calculateCharge()."""
    if now_minutes < start_minutes:
        return start_soc
    if now_minutes >= end_minutes:
        return target
    if start_soc >= target:
        return target

    progress = (now_minutes - start_minutes) / (end_minutes - start_minutes)
    scaled_index = progress * (len(_CHARGE_CURVE) - 1)
    index_low = math.floor(scaled_index)
    index_high = math.ceil(scaled_index)
    low = _CHARGE_CURVE[index_low]
    high = _CHARGE_CURVE[index_high]
    multiplier = low + (high - low) * (scaled_index - index_low)

    if start_minutes > _CHARGE_CURVE_START_MINUTES:
        # Restarting later than 09:00: re-normalize so the curve still
        # runs from 0 to 1 over [start_minutes, end_minutes], instead of
        # starting partway up the original 09:00 curve.
        multiplier = (multiplier - _CHARGE_CURVE[0]) / (1 - _CHARGE_CURVE[0])

    return (target - start_soc) * multiplier + start_soc


def _discharge_cells(
    cells: list[float],
    points_removed: int,
    price_mode: bool,
    target_mode: bool,
    price_floor: float,
) -> tuple[list[float], int, int]:
    """Remove up to `points_removed` cells one at a time from `cells`
    (most expensive grid first, PV last -- see _sort_cells), choosing
    which cell to remove each time depending on price_mode/target_mode.
    Returns (remaining cells, cells removed that were grid, cells removed
    that were PV). Node-RED: the removal loop inside "Min_SOC beregning".

    price_mode: cells priced above price_floor are protected -- use the
    cheapest *unprotected* grid cell first, then PV, and only once
    nothing else is left, a protected cell.
    target_mode: protect all grid cells until the target end time -- use
    PV first, otherwise the most expensive grid cell.
    neither: no protection -- most expensive grid cell first.
    """
    removed_grid = 0
    removed_pv = 0
    for _ in range(points_removed):
        if not cells:
            break
        if price_mode:
            protected_count = sum(1 for v in cells if v != 0 and v > price_floor)
            grid_count = sum(1 for v in cells if v != 0)
            if grid_count > protected_count:
                idx = protected_count
            elif cells[-1] == 0:
                idx = len(cells) - 1
            else:
                idx = 0
        elif target_mode:
            idx = len(cells) - 1 if cells[-1] == 0 else 0
        else:
            idx = 0
        cell = cells.pop(idx)
        if cell == 0:
            removed_pv += 1
        else:
            removed_grid += 1
    return cells, removed_grid, removed_pv


def _restart_plan(
    previous: BatteryLedger,
    today: str,
    blocked: bool,
    now_minutes: int,
    soc: float,
) -> tuple[str | None, float | None, float | None]:
    """The "restart plan" (Node-RED: context 'plan_genstart'): whenever
    the battery command blocks charging/the battery during 06:00-16:59,
    remember today's date and the current time/SoC, overwriting whatever
    was remembered before (even from earlier today). Returns the plan to
    carry forward (possibly unchanged)."""
    if blocked and 6 * 60 <= now_minutes <= 16 * 60 + 59:
        return today, float(now_minutes), soc
    return previous.plan_date, previous.plan_start_minutes, previous.plan_start_soc


def calculate_battery_data(
    inputs: BatteryInputs, previous: BatteryLedger
) -> tuple[int, BatteryLedger, str]:
    """One run of Node-RED's "Min_SOC beregning".

    Returns (min_soc, the new ledger, forced_charge "on"/"off"). Pure --
    testable on its own.
    """
    hour = inputs.now.hour
    # Node-RED rounds the minute to the nearest quarter (can give 60).
    minute = _js_round(inputs.now.minute / 15) * 15
    month = inputs.now.month  # 1-12 (Node-RED's getMonth() is 0-11)
    soc = inputs.battery_soc
    target = inputs.target
    price = inputs.electricity_price
    now_minutes = hour * 60 + minute

    has_previous = previous.soc_previous is not None
    soc_previous = previous.soc_previous if has_previous else _MIN_SOC_FLOOR
    soc_grid = previous.soc_grid
    soc_pv = previous.soc_pv
    cells = _sort_cells(list(previous.soc_cells))
    grid_afladning_pct_cum = previous.grid_afladning_pct_cum
    total_afladning_pct_cum = previous.total_afladning_pct_cum

    # ---- Grid counter (literal: no reset-handling, see module docstring) --
    grid_now = inputs.grid_charged_day
    grid_prev = previous.grid_charge_previous if previous.grid_charge_previous is not None else 0.0

    # ---- Time helpers -----------------------------------------------------
    end_minutes = _parse_hh_mm(inputs.target_end_time)
    before_target_end = now_minutes < end_minutes
    is_evening_free_window = 17 <= hour < 21  # Node-RED: er_17_21

    # Night price-protection window (21:00-05:59). Node-RED also has a
    # 17:00-20:59 branch guarded by FRI_17_21_UANSET_PRIS, but that
    # constant is hardcoded true there -- meaning 17-21 is always free --
    # so that branch can never fire and is left out here.
    price_period = hour >= 21 or hour <= 5
    price_floor = price - _NIGHT_PRICE_MARGIN

    vinter = month in (11, 12, 1)  # November, December, January
    # Protect grid-bought energy until the target end time, but only from
    # 06:00 and never during the free 17:00-20:59 window.
    target_protection_active = before_target_end and hour >= 6 and not is_evening_free_window

    # ---- Which discharge rule applies this run, based on the cells as
    # loaded (before this run's rise/fall -- rise and fall are mutually
    # exclusive, so this is also "as they are right now" either way). ----
    grid_cells_at_start = [v for v in cells if v != 0]
    target_mode = target_protection_active and bool(grid_cells_at_start)
    price_mode = price_period and any(v > price_floor for v in grid_cells_at_start)

    # ---- SoC went up: from grid or PV? (literal Node-RED semantics: a
    # grid counter that reads lower than last run blocks booking the rise
    # at all, see module docstring) -----------------------------------
    soc_rose = has_previous and soc > soc_previous and grid_now >= grid_prev
    rise_is_grid = False
    if soc_rose:
        if hour >= 23 or hour <= 4:
            rise_is_grid = True
        elif grid_now > grid_prev:
            rise_is_grid = True

    if soc_rose:
        points_added = soc - soc_previous
        if rise_is_grid:
            soc_grid += points_added
        else:
            soc_pv += points_added
        # Node-RED's "for (i = 0; i < points; i++)" -- a fractional
        # difference still adds a whole cell for the part.
        for _ in range(math.ceil(points_added)):
            cells.append(price if rise_is_grid else 0.0)
        cells = _sort_cells(cells)

    # ---- SoC went down -----------------------------------------------
    if has_previous and soc < soc_previous:
        difference = soc_previous - soc
        total_afladning_pct_cum += difference

        cells, removed_grid, removed_pv = _discharge_cells(
            cells, math.ceil(difference), price_mode, target_mode, price_floor
        )

        # The sums follow what was actually removed from the cell list,
        # so they can't drift out of sync with it.
        g = min(soc_grid, removed_grid)
        soc_grid -= g
        grid_afladning_pct_cum += g
        soc_pv -= min(soc_pv, removed_pv)

        # Any remainder that couldn't be found in the cell list (e.g.
        # missing cell data): fall back to the old sum-only logic.
        rest = difference - removed_grid - removed_pv
        if rest > 0:
            pv_first = price_mode or target_mode

            def take_pv() -> None:
                nonlocal soc_pv, rest
                taken = min(soc_pv, rest)
                soc_pv -= taken
                rest -= taken

            def take_grid() -> None:
                nonlocal soc_grid, grid_afladning_pct_cum, rest
                taken = min(soc_grid, rest)
                soc_grid -= taken
                grid_afladning_pct_cum += taken
                rest -= taken

            if pv_first:
                take_pv()
                take_grid()
            else:
                take_grid()
                take_pv()

    # Battery empty: start the ledger over (running totals untouched).
    if soc <= _MIN_SOC_FLOOR:
        soc_grid = 0.0
        soc_pv = 0.0
        cells = []

    # ---- Restart plan (see _restart_plan()) ---------------------------
    blocked = inputs.battery_command in (_COMMAND_BLOCK_CHARGING, _COMMAND_BLOCK_BATTERY)
    today = inputs.now.strftime("%Y-%m-%d")
    plan_date, plan_start_minutes, plan_start_soc = _restart_plan(
        previous, today, blocked, now_minutes, soc
    )
    plan_active = (
        plan_date == today
        and plan_start_minutes is not None
        and plan_start_minutes > _CHARGE_CURVE_START_MINUTES
    )
    curve_start_minutes = int(plan_start_minutes) if plan_active else _CHARGE_CURVE_START_MINUTES
    curve_start_soc = plan_start_soc if plan_active else _MIN_SOC_FLOOR

    # ---- Minimum SoC ----------------------------------------------------
    manual_charger_active = _CHARGER_STATE_ACTIVE_MANUAL in inputs.charger_states
    charging_blocked = inputs.battery_command == _COMMAND_BLOCK_CHARGING
    grid_charge_planned_today = inputs.charge_status == _CHARGE_STATUS_YES_TODAY

    min_soc: float = _MIN_SOC_FLOOR
    if not vinter:
        # February - October.
        wanted = wanted_charge_now(now_minutes, target, end_minutes, curve_start_minutes, curve_start_soc)

        # 06-08: use the battery in the morning.
        if 6 <= hour <= 8:
            min_soc = _MIN_SOC_FLOOR
        # Reached target: hold it (08-16).
        if soc >= target and 8 <= hour <= 16:
            min_soc = target
        # Not at target yet, but ahead of the curve: hold current SoC (09-16).
        if soc < target and soc >= wanted and 9 <= hour <= 16:
            min_soc = soc
        # Behind the curve, no grid charge planned today, charging not
        # blocked: hold at the curve (09-16).
        if (
            soc < target and soc < wanted and 9 <= hour <= 16
            and not grid_charge_planned_today and not charging_blocked
        ):
            min_soc = wanted
        # Behind the curve, grid charge planned today, not blocked: hold
        # current SoC (09-16).
        if (
            soc < target and soc < wanted and 9 <= hour <= 16
            and grid_charge_planned_today and not charging_blocked
        ):
            min_soc = soc
        # Charging blocked: hold current SoC.
        if charging_blocked:
            min_soc = soc
        # 17-20: allow discharge.
        if 17 <= hour <= 20:
            min_soc = _MIN_SOC_FLOOR
        # A charger charging manually must not drain the house battery.
        if manual_charger_active and min_soc <= soc:
            min_soc = soc
        min_soc = min(min_soc, _MIN_SOC_CEILING)
        min_soc = max(min_soc, _MIN_SOC_FLOOR)
    else:
        # November, December, January.
        min_soc = soc
        if hour >= 17:
            min_soc = _MIN_SOC_FLOOR
        # The night (00-06) is always released, regardless of target --
        # grid-bought power is protected by the price-protection block
        # below instead.
        if hour <= 6:
            min_soc = _MIN_SOC_FLOOR
        min_soc = min(min_soc, _MIN_SOC_CEILING)
        min_soc = max(min_soc, _MIN_SOC_FLOOR)
        min_soc = min(min_soc, target)
        if manual_charger_active and min_soc <= soc:
            min_soc = soc

    # ---- Protect grid-bought energy until the target end time -----------
    # Can only raise min_soc, and never above the target. Uses the cells
    # after this run's rise/fall.
    if target_protection_active:
        grid_cells = [v for v in cells if v != 0]
        if grid_cells:
            protected_floor = min(_MIN_SOC_FLOOR + len(grid_cells), target)
            min_soc = max(min_soc, protected_floor)

    # ---- Night price protection (21:00-05:59) ----------------------------
    # Only the grid cells priced above price_floor are protected. Can only
    # raise min_soc, and never above the current SoC (so this can never
    # by itself demand buying more power).
    if price_period:
        protected_cells = [v for v in cells if v != 0 and v > price_floor]
        if protected_cells:
            protected_floor = min(_MIN_SOC_FLOOR + len(protected_cells), soc)
            min_soc = max(min_soc, protected_floor)

    # In winter, min_soc may never be raised above the current SoC --
    # these two protections can only move it back down to SoC, never
    # trigger a forced charge.
    if vinter:
        min_soc = max(_MIN_SOC_FLOOR, min(min_soc, soc))

    # DIFF (Claus, 2026-10-15) -- see module docstring: an active
    # charge_now_* command counts as forced grid charging too, even when
    # min_soc > soc isn't itself true (most commonly while
    # grid_charge_planned_today already held min_soc at soc above).
    forced_charge = "on" if (
        inputs.battery_command in _ACTIVE_CHARGE_COMMANDS
        or (not vinter and min_soc > soc)
    ) else "off"
    min_soc_rounded = _js_round(min_soc)

    avg_cheapest_hours = (
        inputs.avg_cheapest_hours
        if inputs.avg_cheapest_hours is not None
        else previous.avg_cheapest_hours
    )

    ledger = BatteryLedger(
        soc_previous=soc,
        grid_charge_previous=grid_now,
        soc_grid=soc_grid,
        soc_pv=soc_pv,
        soc_cells=cells,
        avg_cheapest_hours=avg_cheapest_hours,
        grid_afladning_pct_cum=grid_afladning_pct_cum,
        total_afladning_pct_cum=total_afladning_pct_cum,
        plan_date=plan_date,
        plan_start_minutes=plan_start_minutes,
        plan_start_soc=plan_start_soc,
    )
    return min_soc_rounded, ledger, forced_charge


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


@dataclass
class PwMngtBatteryDataStoredData(restore_state.ExtraStoredData):
    """What PwMngtBatteryDataSensor restores across a restart: the ledger
    (BatteryLedger). Restored regardless of age -- unlike balance_data's
    samples it doesn't go stale: whatever happened while Home Assistant was
    down shows up as one SoC rise/fall on the first run."""

    ledger: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"ledger": self.ledger}

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtBatteryDataStoredData":
        return cls(ledger=dict(restored.get("ledger") or {}))


class PwMngtBatteryDataSensor(restore_state.RestoreEntity, SensorEntity):
    """The PV device's battery_data sensor: min SoC as its state, the
    ledger (PwM_BATTERY_DATA_ATTRIBUTES) as attributes. See the module
    docstring for what triggers a run."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False
        self._attr_extra_state_attributes = {
            key: None for key in PwM_BATTERY_DATA_ATTRIBUTES
        }

        self._entry = entry
        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._soc_entity_id = entity_map.get(ENTITY_KEY_BATTERY_SOC) or None
        self._price_entity_id = entity_map.get(ENTITY_KEY_SPOT_ELECTRICITY_PRICE) or None
        self._grid_charged_entity_id = (
            entity_map.get(ENTITY_KEY_BATTERY_GRID_CHARGED_DAY) or None
        )
        self._ledger = BatteryLedger()
        self._last_run: datetime | None = None
        self._pending_run: Any = None  # cancel callback of a scheduled run

        if not self._soc_entity_id:
            LOGGER.warning(
                "PwMngt: no Battery SoC entity configured (Options -> Solar "
                "PV Plant), '%s' will stay unavailable",
                self.entity_description.key,
            )
        if not self._grid_charged_entity_id:
            LOGGER.warning(
                "PwMngt: no 'Battery grid charged (day)' entity configured "
                "(Options -> Solar PV Plant) -- '%s' will stay unavailable, "
                "same as Node-RED's own guard on this input",
                self.entity_description.key,
            )

    @property
    def extra_restore_state_data(self) -> PwMngtBatteryDataStoredData:
        return PwMngtBatteryDataStoredData(ledger=self._ledger.as_dict())

    async def async_added_to_hass(self) -> None:
        """Restore the ledger, subscribe to the triggers, run once."""
        await super().async_added_to_hass()

        restored = await self.async_get_last_extra_data()
        if restored is not None:
            data = PwMngtBatteryDataStoredData.from_dict(restored.as_dict())
            self._ledger = BatteryLedger.from_dict(data.ledger)

        if not self._soc_entity_id:
            return

        # Any-time triggers.
        watched = [self._soc_entity_id]
        for entity_id in (
            pv_properties.battery_target_end_time_entity_id(self.hass, self._entry),
            pv_properties.battery_command_entity_id(self.hass, self._entry),
            *self._charger_state_entity_ids(),
        ):
            if entity_id:
                watched.append(entity_id)
        self.async_on_remove(
            event.async_track_state_change_event(
                self.hass, watched, self._handle_state_changed
            )
        )

        # Target: only 00:01-16:59 (Node-RED's time-range switch).
        target_entity_id = pv_properties.battery_nightly_target_entity_id(
            self.hass, self._entry
        )
        if target_entity_id:
            self.async_on_remove(
                event.async_track_state_change_event(
                    self.hass, [target_entity_id], self._handle_target_changed
                )
            )

        # Every 15 min, 06:00-23:45 (Node-RED cron "*/15 6-23 * * *").
        self.async_on_remove(
            event.async_track_time_change(
                self.hass,
                self._handle_trigger,
                hour=_PERIODIC_HOURS,
                minute=_PERIODIC_MINUTES,
                second=0,
            )
        )

        self._attr_available = True
        self._handle_trigger(None)

    async def async_will_remove_from_hass(self) -> None:
        if self._pending_run is not None:
            self._pending_run()
            self._pending_run = None
        await super().async_will_remove_from_hass()

    def _charger_state_entity_ids(self) -> list[str]:
        """Each charger's State sensor (sensor.py, PwM_CHARGER_SENSORS),
        looked up by unique_id."""
        registry = er.async_get(self.hass)
        entity_ids = []
        for charger in CHARGERS:
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{self._entry.entry_id}_{charger['id']}_state"
            )
            if entity_id:
                entity_ids.append(entity_id)
        return entity_ids

    @staticmethod
    def _state_value_changed(state_event) -> bool:
        """Only a change of the state itself counts, not an attribute-only
        update (Node-RED: "output only on state change")."""
        old = state_event.data.get("old_state")
        new = state_event.data.get("new_state")
        return old is None or new is None or old.state != new.state

    @callback
    def _handle_state_changed(self, state_event) -> None:
        if self._state_value_changed(state_event):
            self._handle_trigger(None)

    @callback
    def _handle_target_changed(self, state_event) -> None:
        if not self._state_value_changed(state_event):
            return
        now = dt_util.now()
        if _TARGET_TRIGGER_START <= (now.hour, now.minute) <= _TARGET_TRIGGER_END:
            self._handle_trigger(None)

    @callback
    def _handle_trigger(self, _event_or_time) -> None:
        """Run now, or -- within 5 s of the last run -- once when the 5 s
        are up (Node-RED "limit 1 msg/5s"). Triggers in between merge
        into that one run; it reads the latest values anyway."""
        if self._pending_run is not None:
            return
        now = dt_util.utcnow()
        if self._last_run is not None:
            wait = (self._last_run + timedelta(seconds=_RATE_LIMIT_SECONDS) - now).total_seconds()
            if wait > 0:
                self._pending_run = event.async_call_later(
                    self.hass, wait, self._handle_pending_run
                )
                return
        self._run()

    @callback
    def _handle_pending_run(self, _now) -> None:
        self._pending_run = None
        self._run()

    @callback
    def _run(self) -> None:
        """Read the inputs, calculate, write the result out."""
        self._last_run = dt_util.utcnow()

        soc = read_float_state(self.hass.states.get(self._soc_entity_id))
        if soc is None:
            # Node-RED: return null if sensor.batteri_soc is
            # unavailable/unknown/undefined. No fallback -- skip the run.
            return

        grid_charged_day = (
            read_float_state(self.hass.states.get(self._grid_charged_entity_id))
            if self._grid_charged_entity_id
            else None
        )
        if grid_charged_day is None:
            # Node-RED: return null if sensor.batteri_ladet_fra_grid_pr_dag
            # is unavailable/unknown/undefined. No fallback -- skip the run.
            return

        inputs = BatteryInputs(
            now=dt_util.now(),
            battery_soc=soc,
            target=self._read_float_pv(
                pv_properties.battery_nightly_target_entity_id, _DEFAULT_TARGET
            ),
            target_end_time=self._read_state_pv(
                pv_properties.battery_target_end_time_entity_id, _DEFAULT_TARGET_END_TIME
            ),
            electricity_price=self._read_float(self._price_entity_id, _DEFAULT_PRICE),
            battery_command=self._read_state_pv(
                pv_properties.battery_command_entity_id, ""
            ),
            charge_status=self._read_state_pv(
                pv_properties.battery_charge_status_entity_id, ""
            ),
            avg_cheapest_hours=self._read_avg_cheapest_hours(),
            grid_charged_day=grid_charged_day,
            charger_states=[
                state.state
                for entity_id in self._charger_state_entity_ids()
                if (state := self.hass.states.get(entity_id)) is not None
            ],
        )

        min_soc, ledger, forced_charge = calculate_battery_data(inputs, self._ledger)
        self._ledger = ledger

        self._attr_native_value = min_soc
        self._attr_extra_state_attributes = {
            "soc_grid": round(ledger.soc_grid, 2),
            "soc_pv": round(ledger.soc_pv, 2),
            "soc_cells": [round(v, 4) for v in ledger.soc_cells],
            "soc_count": len(ledger.soc_cells),
            "forced_charge": forced_charge,
            "avg_cheapest_hours": ledger.avg_cheapest_hours,
            "soc_previous": ledger.soc_previous,
            "grid_charge_previous": ledger.grid_charge_previous,
            "grid_afladning_pct_cum": round(ledger.grid_afladning_pct_cum, 2),
            "total_afladning_pct_cum": round(ledger.total_afladning_pct_cum, 2),
        }
        self._attr_available = True
        self.async_write_ha_state()

    # -- input readers (Node-RED fallback when missing/unknown) ----------

    def _read_float(self, entity_id: str | None, default: float) -> float:
        value = read_float_state(self.hass.states.get(entity_id)) if entity_id else None
        return default if value is None else value

    def _read_float_pv(self, lookup, default: float) -> float:
        return self._read_float(lookup(self.hass, self._entry), default)

    def _read_state_pv(self, lookup, default: str) -> str:
        entity_id = lookup(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state

    def _read_avg_cheapest_hours(self) -> float | None:
        entity_id = pv_properties.battery_charge_status_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        try:
            value = state.attributes.get("avg_cheapest_hours")
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
