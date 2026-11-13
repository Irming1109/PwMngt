"""Battery data: the PV device's battery_data sensor -- the battery's
minimum SoC right now (its state), plus a running ledger of where the
battery's content came from (grid or PV, and at what price).

Port of Claus's Node-RED function "Min_SOC beregning" (tab "Batteri
styring NY"), which writes sensor.pv_battery_status. Checked against
Claus's export of 27-10-2026 (its "Min_SOC beregning backup" copy is not
part of this automation and is not ported). Only the calculation is ported: in Node-RED the state is
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
  choice local to this entity, not a behavioural difference.
- The per-percent-point cells are one list attribute ("soc_cells"),
  not 100 separate attributes soc_1..soc_100.

New in this export (not present in the previously-ported version):
- A per-cell price floor. Grid cells bought at a price more than
  NAT_PRIS_TILLAEG (0.30 kr/kWh) cheaper than *right now*'s price are
  protected from discharge at night (21:00-05:59); PV cells and grid
  cells within that margin are free to use. This replaces the old
  all-or-nothing "protect every grid cell at night" rule.
- A "restart plan": if the battery command blocks charging
  ("block_charging") during 06:00-16:59, that moment (today's date, time of day, SoC) is
  remembered. While that plan is still "active" (same calendar day,
  recorded after 09:00), the charge curve (wanted_charge_now) restarts
  from that point instead of from 09:00/minimum SoC -- so resuming after a
  daytime block doesn't demand catching up to where the curve "should"
  already be.
- The night-time (21:00-05:59) grid protection is capped at the current
  SoC rather than being able to push min_soc arbitrarily high.
- The target-end-time protection (from 06:00, never during 17:00-20:59)
  holds the battery at the level it has after a night charge: while there
  are grid cells, the floor is min(SoC, target), and it is remembered for
  the rest of the day so it never drops (Node-RED: context 'target_gulv',
  export 27-10-2026). It used to be 5 + the number of grid cells.
- forced_charge also reads "on" while battery_command is actively one of
  the charge_now_* options (select.py, Old: "Lad nu - 25/50/75/100%
  effekt"), on top of the min_soc > soc check. PwMngt had this first
  (Claus, 15-10-2026); Node-RED has it too since the 27-10-2026 export. It
  matters to data/balance_data.py's is_forced_charging(): without it the
  battery's charging draw was netted out of the grid balance during a
  charge_now_* command, showing a false surplus while buying from the grid.

Deliberate differences from Node-RED:
- DIFF (Claus, 19-10-2026): the min_soc ceiling is 95, not 90, and the
  target-end-time floor uses the same ceiling (Claus, 27-10-2026) -- see
  _MIN_SOC_CEILING.
- DIFF (Claus, 27-10-2026): Node-RED's latch "stop forcing once SoC
  reaches min_soc" (it reads last run's forced_charge back) is left out.
  It runs after the "Lad nu" rule there, so during a charge_now_* command
  forced_charge flips On/Off on every run. Here it stays "on" for as long
  as the command is active.
- DIFF (Claus, 27-10-2026): Node-RED's fixed 5 % ("battery empty") is
  the battery's minimum SoC from select.<pv>_battery_min_soc (default 5):
  the lowest min_soc, the charge curve's 09:00 start SoC, the base of the
  night price protection (minimum SoC + protected cells), the fallback
  target and the "battery empty" ledger reset. With a battery that stops
  at 10 %, SoC <= 5 never happens, so the ledger would otherwise never be
  reset. Only used for calculating; the inverter's own discharge limit is
  set in the inverter.
- DIFF (Claus, 29-10-2026): right after a start, a run is skipped until
  the minimum-SoC select -- and for the first 10 minutes Battery nightly
  target -- has a real value, instead of calculating with their
  fallbacks. Otherwise min_soc showed 5 % for a moment at a restart,
  which released data/battery_hold.py's hold. See _inputs_ready().

- DIFF (issue #33, Claus 2026-11-04): Node-RED's "Aktiv - manuel" charger
  rule (min_soc = SoC) also applies while any charger charges a car,
  any time of day, found from the chargers' own power sensors
  (properties/charger_properties.py) -- PwMngt's charger State sensors
  aren't set by anything yet. Not on a Growatt: there data/battery_hold.py
  lets the battery cover the house without the car instead. Only once the
  battery has discharged for 80 s in a row while the car charges (Claus
  2026-11-10): a car charged on PV surplus doesn't lock it; the lock then
  stays until the car stops (PV still charges the battery under it).

- DIFF (issue #59, Claus 2026-11-09): in today's daytime hours the night
  charge also covered (data/battery_charge_decision.py: day_hours,
  day_floor_soc), min_soc is that floor -- the battery covers the house
  down to what the evening needs less what tomorrow's PV surplus refills
  before it (issue #70), never below, and never above the SoC. The plan is remembered for the day once read
  (the decision moves on to tomorrow after 14:00). Not while a charger
  charges a car, a grid charge runs or charging is blocked. The target
  floor is let go in those hours, so it follows the battery down
  afterwards instead of demanding the energy back.
- DIFF (issue #61, Claus 2026-11-09): without such a plan for today (a
  manual charge, a plan from before #59, or simply more grid energy in
  the battery than planned), the same day plan is worked out from today:
  the hours 06-16 whose price is at least what the battery's grid energy
  cost (the grid cells' average price) plus the price difference, and
  an evening reserve for 17-21 plus the evening hours that price pays
  for (data/battery_charge_decision.py's evening_hourly_need() and
  worth_charging(), on today's prices and today's PV 17-21). Only while
  the battery holds grid energy; a plan from the night charge always
  wins.
- DIFF (issue #62, Claus 2026-11-09): outside 23:00-04:59 a SoC rise is
  also booked as grid while a grid charge runs (a charge_now_* command),
  or while the house buys at least half the battery's charge power from
  the grid (grid_feeds_battery()). Node-RED only looks at the "charged
  from grid" counter there, but a Kostal's lags up to an hour: Rene's
  charge 85 -> 100 % at 05:02-05:21 on 2026-11-09 was booked about half
  as PV, so that grid energy was used first as free PV and was missing
  from the battery's books.

- NEW (issue #64, Claus 2026-11-09): the battery sells in the night
  charge's sale hours (data/battery_charge_decision.py: sale_hours,
  sale_floors, remembered for the day like the day plan): min_soc is the
  hour's floor (never above the SoC, held exactly on a Kostal --
  kostal_lock()), and battery_command is set to discharge_now_100 while
  the SoC is above it -- back to "internal" when the floor or the end of
  the hour is reached. Only a discharge_now_100 this module set is taken
  back. Not while a car charges, a grid charge runs or the battery mode is
  manual. The inverter translators (Node-RED) carry it out. The night
  charge for a sale goes above battery_nightly_target (charge_target);
  that level is the target until the last sale hour (effective_target()).
  While selling, the export the battery delivers is metered every 30 s
  (sale_kwh per hour, read by data/savings_data.py at the hour's close).

- NEW (issue #71, Claus 2026-11-12): the PV energy above the evening
  reserve (17-21) is used in today's dearest known daytime hours when they
  beat the hours after 21:00 it would otherwise cover (plan_pv_release()):
  min_soc is the plan's floor in those hours (held exactly on a Kostal),
  never below the reserve. Planned at 06:00, when tomorrow's real prices
  arrive and when PV has added 1 kWh more -- not every run.

Triggers (same as Node-RED, see PwMngtBatteryDataSensor): Battery SoC
changes, Battery nightly target changes (only 00:01-16:59), target end
time / battery command / either charger's state changes, every 15 min
06:00-23:45, and once at startup. Runs at most once per 5 seconds (Node-RED
"limit 1 msg/5s"); triggers in between are merged into one run.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    DEFAULT_BATTERY_MIN_SOC,
    DOMAIN,
    ENTITY_KEY_BATTERY_GRID_CHARGED_DAY,
    ENTITY_KEY_BATTERY_POWER,
    ENTITY_KEY_BATTERY_SOC,
    ENTITY_KEY_GRID_POWER,
    INVERTER_TYPE_KOSTAL,
)
from ..devices import CHARGERS, pv_device_info
from ..helpers.state_helper import read_float_state
from ..properties import charger_properties, hub_properties, pv_properties
from ..select import signal_set_battery_command
from .battery_charge_decision import (
    auto_detect_tomorrow_prices_entity_id,
    evening_hourly_need,
    worth_charging,
)
from .solar_data import TARGET_MARGIN

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
    # <= the battery's minimum SoC). Old: "grid_afladning_pct_cum" /
    # "total_afladning_pct_cum".
    "grid_afladning_pct_cum",
    "total_afladning_pct_cum",
    # Issue #55: running total of percentage points charged from the grid
    # (never reset) -- savings_data books an hour's grid charge only if
    # this rose, see calculate_battery_grid_balance().
    "grid_opladning_pct_cum",
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

# Node-RED fallbacks for inputs that are missing/unknown. (A missing
# target falls back to the battery's minimum SoC, see _run().)
_DEFAULT_TARGET_END_TIME = "16:45"
_DEFAULT_PRICE = 1.5
# Issue #61: fallback_day_plan() -- the price difference while its select
# has no value (the charge decision's own fallback), and the hours it plans.
_DEFAULT_PRICE_DIFFERENCE = 1.2
_DAY_PLAN_FIRST_HOUR = 6
_DAY_PLAN_END_HOUR = 17  # 06-16.
_DAY_PLAN_EVENING_HOURS = range(17, 21)
# Issue #71: the hours after the evening the extra energy would cover (the
# night ones are tomorrow's), the least a daytime hour must save per kWh
# (below it, a switch isn't worth it -- an EEPROM write on a Growatt, a
# min_soc write on a Kostal), the least energy worth a release, and how
# much more PV must be there before the plan is made again.
_PV_RELEASE_LATE_HOURS = (21, 22, 23)
_PV_RELEASE_NIGHT_HOURS = range(0, 6)
_PV_RELEASE_MIN_GAIN = 0.10
_PV_RELEASE_MIN_KWH = 1.0
_PV_RELEASE_REPLAN_KWH = 1.0

# Charger state (sensor.py, PwM_CHARGER_SENSORS "state") that stops the
# house battery from discharging. Old: "Aktiv - manuel".
_CHARGER_STATE_ACTIVE_MANUAL = "Active - manual"
# Issue #33: how long a charger's power sensor may be unavailable/unknown
# while the last known "car charging" is kept -- a Wallbox drops out for a
# minute or two now and then, and each unlock/relock is an inverter write.
_CAR_POWER_UNAVAILABLE_HOLD = timedelta(minutes=10)
# Issue #33: how long a charger may read below CAR_CHARGING_MIN_W before
# the car counts as stopped -- a Wallbox pauses at 0 W for a minute or two
# mid-session (Kasper, 2026-11-07: 90 s), and each unlock/relock is an
# inverter write.
_CAR_PAUSE_HOLD = timedelta(minutes=3)
# Issue #33 (Claus 2026-11-10): a car charging locks the battery only once
# it has discharged (Battery power above _CAR_DISCHARGE_W) this long in a
# row -- a car charged on PV surplus doesn't need it, and the chargers'
# surplus control steps the car up 1-2 kW at a time, which the battery
# takes for a reading or two. The same 80 s as data/battery_hold.py.
_CAR_DISCHARGE_W = 100.0
_CAR_DISCHARGE_HOLD = timedelta(seconds=80)
# How often Battery power is looked at while a car charges unlocked.
_CAR_DISCHARGE_POLL_SECONDS = 20
# Battery command (select.py, "battery_command") that pauses charging.
# Old: "Bloker ladning". Node-RED also checked "Bloker batteri", which
# PwMngt doesn't offer (removed, issue #29).
_COMMAND_BLOCK_CHARGING = "block_charging"
# Battery commands (select.py, "battery_command") that mean "actively
# charging from the grid right now" -- same set as
# data/battery_charge_level.py's own _ACTIVE_CHARGE_COMMANDS (duplicated
# rather than imported, to avoid a cross-module coupling between the two
# "Min_SOC"/"Bestem ladeeffekt" ports). Old: "Lad nu - 25/50/75/100%
# effekt". Used below for forced_charge.
_ACTIVE_CHARGE_COMMANDS = frozenset(
    {"charge_now_25", "charge_now_50", "charge_now_75", "charge_now_100"}
)
# Battery charge status (sensor.py, "battery_charge_status") meaning a grid
# charge is planned later today. Old: "Ja (i dag)".
_CHARGE_STATUS_YES_TODAY = "yes_today"
# Issue #64: the command the battery sells with, and the one it goes back to.
COMMAND_SELL = "discharge_now_100"
# How often the sale meter reads the export while the battery sells.
_SALE_METER_INTERVAL = timedelta(seconds=30)
_COMMAND_INTERNAL = "internal"
# Issue #62: the share of the battery's charge power the house must buy
# from the grid for a rise to count as grid -- with PV short, the grid
# feeds the charge; with a PV surplus the house buys nothing.
_GRID_FEED_SHARE = 0.5

# Minimum SoC ceiling. (The floor is BatteryInputs.battery_min_soc.) The
# ceiling was 90 in Node-RED's original port; DIFF (Claus, 19-10-2026):
# raised to 95 so min_soc can lock all the way up to
# battery_nightly_target, which is itself capped at 95 (see
# data/solar_data.py's _TARGET_CEILING) -- a deliberate behaviour change,
# not a literal port anymore.
_MIN_SOC_CEILING = 95

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
# How long after a start a missing Battery nightly target means "not ready
# yet" rather than "use the fallback" -- see _inputs_ready().
_STARTUP_GRACE = timedelta(minutes=10)
# How soon a run skipped by _inputs_ready() is tried again.
_NOT_READY_RETRY_SECONDS = 10
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
    # Issue #33: a car is charging, on an inverter whose power PwMngt
    # can't steer (Kostal) -- hold the SoC, so the battery never covers
    # the car (any time of day; PV still charges the battery). A Growatt covers the house only instead
    # (data/battery_hold.py), so this stays False there.
    car_charging_lock: bool = False
    # Issue #59: today's daytime hours the night charge covered and the SoC
    # (%) kept for the evening in them -- from Battery charge status while
    # it is "yes_today". None = not known now.
    day_plan_hours: list[int] | None = None
    day_plan_reserve: float | None = None
    # Issue #61: False when the plan above was worked out from today
    # (fallback_day_plan()), which never replaces one from the night charge.
    day_plan_from_charge: bool = True
    # Issue #62: the house buys from the grid what the battery charges
    # right now (grid_feeds_battery()).
    grid_feeds_battery: bool = False
    # Issue #64: today's sale hours and the SoC (%) each stops at (same
    # order) -- from Battery charge status while it is "yes_today". None =
    # not known now.
    sale_hours: list[int] | None = None
    sale_floors: list[float] | None = None
    # The charge decision's charge_target, when it includes a sale.
    sale_target: float | None = None
    # Issue #71: prices and needs for plan_pv_release(); None = not known.
    pv_release: "PvReleaseInputs | None" = None
    # The SoC the battery is discharged to (pv_properties.battery_min_soc(),
    # Node-RED: a fixed 5) -- the lowest min_soc, the charge curve's 09:00
    # start, and "battery empty" for the ledger.
    battery_min_soc: float = DEFAULT_BATTERY_MIN_SOC


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
    # Issue #55: see PwM_BATTERY_DATA_ATTRIBUTES.
    grid_opladning_pct_cum: float = 0.0
    # The "restart plan" (Node-RED: context 'plan_genstart'). None = no
    # plan recorded yet. plan_date is a "YYYY-MM-DD" local-date string.
    plan_date: str | None = None
    plan_start_minutes: float | None = None
    plan_start_soc: float | None = None
    # The target-end-time floor (Node-RED: context 'target_gulv'), see
    # _target_floor(). None = no floor kept (protection not active).
    target_floor_date: str | None = None
    target_floor: float | None = None
    # Issue #45: the min_soc the last grid charge (charge_now_*) stopped at
    # -- a Kostal may hold exactly this (not a step of 10), see
    # kostal_min_soc_step(). Kept until the target protection ends.
    charge_lock_date: str | None = None
    charge_lock: float | None = None
    # Whether a charge_now_* command ran at the previous run -- the lock is
    # taken when it stops.
    charging_previous: bool = False
    # Issue #33: the min_soc when a charger started charging a car -- a
    # Kostal holds exactly this while the car charges, see
    # kostal_min_soc_step(). None = no car charging.
    car_lock: float | None = None
    # Issue #69: the SoC when the winter hold (07-16, outside the day plan
    # and the sale hours) started -- a Kostal holds exactly this instead of
    # the step of 10 below it, see kostal_min_soc_step(). None = no hold.
    hold_lock: float | None = None
    # Issue #35: the date a charge_now_* command ran before 06:00 -- "the
    # night charge has started", see calculate_battery_data().
    night_charge_date: str | None = None
    # Issue #59: today's day plan, remembered for the day (see
    # BatteryInputs.day_plan_hours).
    day_plan_date: str | None = None
    day_plan_hours: list[int] = field(default_factory=list)
    day_plan_reserve: float | None = None
    day_plan_from_charge: bool = False
    # Issue #67: the level the day plan lets the battery down to in this
    # hour, held exactly on a Kostal (kostal_lock()) -- the step of 10
    # below it would give the evening's reserve away. None outside them.
    plan_lock: float | None = None
    # Issue #64: today's sale plan, remembered for the day, whether the
    # battery sells right now (this module set discharge_now_100), and the
    # floor it sells down to (held exactly on a Kostal, kostal_lock()).
    sale_date: str | None = None
    sale_hours: list[int] = field(default_factory=list)
    sale_floors: list[float] = field(default_factory=list)
    selling: bool = False
    sale_lock: float | None = None
    # Tonight's charge target with the sale's extra charge (above the
    # battery_nightly_target) -- held as target until the last sale hour,
    # effective_target().
    sale_target: float | None = None
    # The sale hour whose sale has reached its floor -- it doesn't start
    # again that hour when the battery hold lifts the SoC a point (Claus
    # 2026-11-10: 08:08 floor reached, 08:40 a 26 s sale again).
    sale_done_hour: int | None = None
    # The kWh the battery sent to the grid while selling, per hour today
    # ("8": 9.4) -- metered here (add_sale_export()), since the hourly
    # counters can't tell it from the house's own use: on 2026-11-10 the
    # house bought 3 kWh in the sale hour, more than the 2.4 kWh discharge.
    sale_kwh: dict[str, float] = field(default_factory=dict)
    # Issue #71: today's plan for the PV energy above the evening reserve
    # (PvReleasePlan as a dict), remembered for the day.
    pv_release_date: str | None = None
    pv_release: dict[str, Any] | None = None

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
            "grid_opladning_pct_cum": self.grid_opladning_pct_cum,
            "plan_date": self.plan_date,
            "plan_start_minutes": self.plan_start_minutes,
            "plan_start_soc": self.plan_start_soc,
            "target_floor_date": self.target_floor_date,
            "target_floor": self.target_floor,
            "charge_lock_date": self.charge_lock_date,
            "charge_lock": self.charge_lock,
            "charging_previous": self.charging_previous,
            "car_lock": self.car_lock,
            "hold_lock": self.hold_lock,
            "night_charge_date": self.night_charge_date,
            "day_plan_date": self.day_plan_date,
            "day_plan_hours": list(self.day_plan_hours),
            "day_plan_reserve": self.day_plan_reserve,
            "day_plan_from_charge": self.day_plan_from_charge,
            "sale_date": self.sale_date,
            "sale_hours": list(self.sale_hours),
            "sale_floors": list(self.sale_floors),
            "selling": self.selling,
            "sale_lock": self.sale_lock,
            "plan_lock": self.plan_lock,
            "sale_target": self.sale_target,
            "sale_done_hour": self.sale_done_hour,
            "sale_kwh": dict(self.sale_kwh),
            "pv_release_date": self.pv_release_date,
            "pv_release": dict(self.pv_release) if self.pv_release else None,
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
            grid_opladning_pct_cum=float(restored.get("grid_opladning_pct_cum") or 0.0),
            plan_date=restored.get("plan_date"),
            plan_start_minutes=restored.get("plan_start_minutes"),
            plan_start_soc=restored.get("plan_start_soc"),
            target_floor_date=restored.get("target_floor_date"),
            target_floor=restored.get("target_floor"),
            charge_lock_date=restored.get("charge_lock_date"),
            charge_lock=restored.get("charge_lock"),
            charging_previous=bool(restored.get("charging_previous", False)),
            car_lock=restored.get("car_lock"),
            hold_lock=restored.get("hold_lock"),
            night_charge_date=restored.get("night_charge_date"),
            day_plan_date=restored.get("day_plan_date"),
            day_plan_hours=[int(h) for h in restored.get("day_plan_hours") or []],
            day_plan_reserve=restored.get("day_plan_reserve"),
            day_plan_from_charge=bool(restored.get("day_plan_from_charge")),
            sale_date=restored.get("sale_date"),
            sale_hours=[int(h) for h in restored.get("sale_hours") or []],
            sale_floors=[float(v) for v in restored.get("sale_floors") or []],
            selling=bool(restored.get("selling")),
            sale_lock=restored.get("sale_lock"),
            plan_lock=restored.get("plan_lock"),
            sale_target=restored.get("sale_target"),
            sale_done_hour=restored.get("sale_done_hour"),
            sale_kwh={str(k): float(v) for k, v in (restored.get("sale_kwh") or {}).items()},
            pv_release_date=restored.get("pv_release_date"),
            pv_release=restored.get("pv_release") if isinstance(restored.get("pv_release"), dict) else None,
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
    start_soc: float = DEFAULT_BATTERY_MIN_SOC,
) -> float:
    """The SoC the battery should have reached by now, following
    _CHARGE_CURVE from start_minutes/start_soc to the target end time.
    start_minutes/start_soc default to 09:00/SoC 5 (the caller passes the
    battery's minimum SoC), but a "restart plan"
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


def grid_feeds_battery(grid_power: float | None, battery_power: float | None) -> bool:
    """Issue #62: True while the battery charges and the house buys at
    least _GRID_FEED_SHARE of that charge power from the grid -- the
    charge comes from the grid, not PV. Powers in W, normalized (grid
    positive = buying, battery positive = discharging). False while
    either is unknown."""
    if grid_power is None or battery_power is None or battery_power >= 0:
        return False
    return grid_power >= -battery_power * _GRID_FEED_SHARE


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
    most expensive *unprotected* grid cell first, then PV, and only once
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
    hour: int,
    now_minutes: int,
    soc: float,
) -> tuple[str | None, float | None, float | None]:
    """The "restart plan" (Node-RED: context 'plan_genstart'): whenever
    the battery command blocks charging/the battery during 06:00-16:59,
    remember today's date and the current time/SoC, overwriting whatever
    was remembered before (even from earlier today). Returns the plan to
    carry forward (possibly unchanged).

    The window is checked on the real hour, not now_minutes: now_minutes
    uses the minute rounded to the nearest quarter, so 05:53 would
    otherwise count as 06:00 and 16:53 as 17:00 (Node-RED: klokken)."""
    if blocked and 6 <= hour <= 16:
        return today, float(now_minutes), soc
    return previous.plan_date, previous.plan_start_minutes, previous.plan_start_soc


def _target_floor(
    previous: BatteryLedger,
    today: str,
    active: bool,
    has_grid_cells: bool,
    soc: float,
    target: float,
) -> tuple[str | None, float | None]:
    """The floor that protects grid-bought energy until the target end time
    (Node-RED: context 'target_gulv'). While the battery holds grid cells,
    the floor follows the SoC up (capped at the target and the min_soc
    ceiling), but it never drops for the rest of the day -- otherwise it
    would chase the SoC down as the battery discharges. Cleared whenever
    the protection isn't active. Returns (date, floor) to carry forward;
    (None, None) = no floor."""
    if not active:
        return None, None
    kept = (previous.target_floor or 0.0) if previous.target_floor_date == today else 0.0
    if not has_grid_cells and kept <= 0:
        # Node-RED keeps the floor object untouched here; it is 0/stale
        # either way, so it is cleared.
        return None, None
    new_floor = min(soc, target, _MIN_SOC_CEILING) if has_grid_cells else 0.0
    return today, max(kept, new_floor)


def fallback_day_plan(
    prices_today: dict[int, float],
    grid_cell_prices: list[float],
    price_difference: float,
    evening_need: dict[int, float],
    battery_size: float,
    battery_target: float,
    battery_min_soc: float,
) -> tuple[list[int], float] | None:
    """Issue #61: (today's daytime hours, evening reserve SoC %) worked out
    from today, for a battery holding grid energy without a plan from the
    night charge -- see the module docstring. None when nothing pays for
    it or something needed is missing."""
    if not grid_cell_prices or battery_size <= 0:
        return None
    if any(hour not in prices_today for hour in range(_DAY_PLAN_FIRST_HOUR, 24)):
        return None
    prices = [prices_today.get(hour, 0.0) for hour in range(24)]
    paid = sum(grid_cell_prices) / len(grid_cell_prices)
    break_even, worth, covered, covered_kwh = worth_charging(
        prices, paid, price_difference, evening_need
    )
    day_hours = [
        hour for hour in range(_DAY_PLAN_FIRST_HOUR, _DAY_PLAN_END_HOUR) if prices[hour] >= break_even
    ]
    if not day_hours:
        return None
    if not covered:
        covered_kwh = sum(evening_need[hour] for hour in _DAY_PLAN_EVENING_HOURS)
    reserve = min(
        battery_target,
        battery_min_soc + math.ceil(covered_kwh * TARGET_MARGIN / battery_size * 100),
    )
    return day_hours, float(reserve)


@dataclass
class PvReleaseInputs:
    """Issue #71: what plan_pv_release() needs besides the ledger -- read
    by PwMngtBatteryDataSensor._read_pv_release()."""

    prices_today: dict[int, float]
    # Tomorrow's real prices; empty while only Strømligning's forecast is
    # out (before ~13:00) -- those hours then don't count.
    prices_tomorrow: dict[int, float]
    # kWh the battery covers per hour 17-23 (evening_hourly_need()).
    evening_need: dict[int, float]
    # kWh per hour 00-05 the night after.
    night_need_per_hour: float
    # The house's average kWh per daytime hour (06-16).
    day_need: dict[int, float]
    battery_size: float


@dataclass
class PvReleasePlan:
    """Issue #71: today's plan for the PV energy above the evening reserve."""

    # The daytime hours the battery covers the house in, down to `floor`.
    hours: list[int]
    floor: float
    # The kWh planned for them, and the later hours (time order; 0-5 = the
    # night after) they would otherwise have covered.
    kwh: float
    instead_hours: list[int]
    gain: float
    # The evening reserve (SoC %) and the PV energy above it at planning.
    reserve: float
    extra_kwh: float
    # Whether the night's real prices were known.
    with_tomorrow: bool
    # True when the battery would reach into the night, whose prices were
    # not known yet -- compared with the evening hours instead.
    night_unknown: bool


def evening_reserve_soc(
    evening_need: dict[int, float], battery_min_soc: float, battery_size: float
) -> float | None:
    """Issues #71/#73: the SoC (%) the battery keeps for the evening 17-21
    -- the floor plus the evening's need with TARGET_MARGIN, at most
    _MIN_SOC_CEILING. One formula for #71's plan and battery_hold's winter
    limit, so the hold never lets go of the reserve over a rounding
    difference. None when the battery size or the evening need is
    missing."""
    if battery_size <= 0 or not evening_need:
        return None
    evening_kwh = sum(evening_need.get(h, 0.0) for h in _DAY_PLAN_EVENING_HOURS)
    return min(
        float(_MIN_SOC_CEILING),
        battery_min_soc + math.ceil(evening_kwh * TARGET_MARGIN / battery_size * 100),
    )


def plan_pv_release(
    soc: float,
    soc_pv: float,
    battery_min_soc: float,
    hour: int,
    excluded_hours: set[int],
    data: PvReleaseInputs,
) -> PvReleasePlan | None:
    """Issue #71 (Claus 2026-11-12): use the PV energy above the evening
    reserve in today's dearest known hours instead of saving it for after
    21:00. The battery empties in time order after 17:00, so energy used
    now is missing at the END of what it would have covered -- the last
    hours reached, latest first. A daytime hour gets it while its price
    beats that hour's by at least _PV_RELEASE_MIN_GAIN; the dearest
    daytime hours go first. Only known prices: a night hour without
    tomorrow's real price is skipped, so the comparison is with the evening
    hours 21-23, which are dearer as a rule. The PV energy is free, so no
    charge price or margin -- it just has to save the dearest purchase.
    The daytime need is the house's average without PV (it can only make
    the plan pick fewer, dearer hours); the floor keeps the reserve either
    way. None when something needed is missing."""
    size = data.battery_size
    reserve = evening_reserve_soc(data.evening_need, battery_min_soc, size)
    if reserve is None:
        return None
    above_kwh = max(soc - reserve, 0.0) / 100 * size
    extra_kwh = min(above_kwh, max(soc_pv, 0.0) / 100 * size)

    # The later hours the energy above the reserve reaches, in time order.
    later: list[tuple[int, float, float | None]] = []
    left = above_kwh
    for later_hour in _PV_RELEASE_LATE_HOURS:
        need = data.evening_need.get(later_hour, 0.0)
        if left <= 0 or need <= 0:
            continue
        later.append((later_hour, min(need, left), data.prices_today.get(later_hour)))
        left -= need
    if data.night_need_per_hour > 0:
        for night_hour in _PV_RELEASE_NIGHT_HOURS:
            if left <= 0:
                break
            later.append(
                (night_hour, min(data.night_need_per_hour, left), data.prices_tomorrow.get(night_hour))
            )
            left -= data.night_need_per_hour
    night_unknown = any(price is None for h, _kwh, price in later if h < _DAY_PLAN_FIRST_HOUR)
    # Latest first, known prices only.
    displaced = [[h, kwh, price] for h, kwh, price in reversed(later) if price is not None]

    candidates = sorted(
        (
            (data.prices_today[h], h)
            for h in range(max(hour, _DAY_PLAN_FIRST_HOUR), _DAY_PLAN_END_HOUR)
            if h not in excluded_hours and h in data.prices_today and data.day_need.get(h, 0.0) > 0
        ),
        reverse=True,
    )
    remaining = extra_kwh
    planned: dict[int, float] = {}
    instead: set[int] = set()
    gain = 0.0
    stop = False
    for price, day_hour in candidates:
        room = data.day_need[day_hour]
        while remaining > 0 and room > 0 and displaced:
            later_hour, later_kwh, later_price = displaced[0]
            if price - later_price < _PV_RELEASE_MIN_GAIN:
                # The dearest daytime hour left can't beat it -- nor can
                # any cheaper one.
                stop = True
                break
            amount = min(remaining, room, later_kwh)
            planned[day_hour] = planned.get(day_hour, 0.0) + amount
            instead.add(later_hour)
            gain += amount * (price - later_price)
            remaining -= amount
            room -= amount
            displaced[0][1] -= amount
            if displaced[0][1] <= 1e-9:
                displaced.pop(0)
        if stop or remaining <= 0 or not displaced:
            break

    kwh = sum(planned.values())
    hours = sorted(planned)
    if kwh < _PV_RELEASE_MIN_KWH:
        hours, kwh, gain, instead = [], 0.0, 0.0, set()
    floor = max(reserve, math.ceil(soc - kwh / size * 100)) if hours else reserve
    order = [*_PV_RELEASE_LATE_HOURS, *_PV_RELEASE_NIGHT_HOURS]
    return PvReleasePlan(
        hours=hours,
        floor=float(floor),
        kwh=round(kwh, 2),
        instead_hours=[h for h in order if h in instead],
        gain=round(gain, 2),
        reserve=float(reserve),
        extra_kwh=round(extra_kwh, 2),
        with_tomorrow=bool(data.prices_tomorrow),
        night_unknown=night_unknown,
    )


def kostal_min_soc_step(
    min_soc: float, target: float, battery_min_soc: float, lock: float | None = None
) -> int:
    """Issue #1 (Claus, 2026-11-05): a Kostal stores Battery:MinSoc in its
    permanent memory, so the minimum SoC is published in steps of 10 --
    the highest step the calculated min_soc has reached, never above it:
    it follows the SoC up 10, 20, 30 ... (Node-RED's translator wrote every
    point, ~70 writes in one night). The target itself is published once
    reached, also when it isn't a multiple of 10 (one extra write), and
    never below the battery's own minimum. Done here, not in a translator,
    because PwMngt will write the inverter itself later on.

    Issue #45 (Claus, 2026-11-06): after a partial night charge (#43) to,
    say, 47 %, the step down to 40 let the battery discharge 7 points of
    the grid charge before 06:00. So the level a grid charge stopped at
    (charge_lock, see BatteryLedger) is held exactly -- one write off the
    steps per charge, also when the plan has a second block later in the
    day -- until the steps pass it or the protection ends; never above the
    calculated min_soc (the winter rule keeps that at or below the SoC).

    Issue #33 (Claus, 2026-11-07): the same for a car charging -- the lock
    sets min_soc to the SoC (16), the step rounded it to 10, and the
    battery covered the car from 16 to 10 % (Kasper, 2026-11-07). So the
    SoC at the start of the car charge (car_lock) is held exactly too.
    lock is the higher of charge_lock and car_lock."""
    if min_soc >= target:
        return int(round(min_soc))
    stepped = max(math.floor(min_soc / 10) * 10, battery_min_soc)
    if lock is not None:
        stepped = max(stepped, min(lock, min_soc))
    return int(round(stepped))


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def effective_target(target: float, ledger: "BatteryLedger", now: datetime) -> float:
    """Issue #64 (Claus 2026-11-10): the night charge for a sale goes above
    battery_nightly_target -- that higher level is the target (the cap on
    min_soc, the protection of grid power, the Kostal steps) from the
    charge until the last sale hour, so the extra charge isn't held back
    or used by the house before it is sold."""
    if (
        ledger.sale_target is None
        or ledger.sale_date != now.date().isoformat()
        or not ledger.sale_hours
        or now.hour > max(ledger.sale_hours)
    ):
        return target
    return max(target, ledger.sale_target)


def sale_export_w(grid_power: float | None, battery_power: float | None) -> float:
    """Issue #64: the battery's power going out to the grid (W) -- the
    export (grid_power negative), but never more than the battery delivers,
    so PV going out at the same time isn't counted as sold."""
    if grid_power is None or battery_power is None:
        return 0.0
    return max(0.0, min(-grid_power, battery_power))


def add_sale_export(
    sale_kwh: dict[str, float], hour: int, export_w: float, seconds: float
) -> dict[str, float]:
    """Issue #64: sale_kwh with export_w for `seconds` added to `hour`."""
    if export_w <= 0 or seconds <= 0:
        return sale_kwh
    key = str(hour)
    result = dict(sale_kwh)
    # Unrounded: rounding every 30 s sample adds up (round for display).
    result[key] = result.get(key, 0.0) + export_w * seconds / 3_600_000
    return result


def kostal_lock(ledger: "BatteryLedger") -> float | None:
    """The level kostal_min_soc_step() holds exactly: the higher of the
    charge lock (#45), the car lock (#33) and the hold lock (#69), or
    None -- issue #64: while
    the battery sells, its floor alone; issue #67: in a day-plan hour, the
    level the plan lets it down to."""
    if ledger.selling and ledger.sale_lock is not None:
        return ledger.sale_lock
    if ledger.plan_lock is not None:
        return ledger.plan_lock
    locks = [
        v for v in (ledger.charge_lock, ledger.car_lock, ledger.hold_lock) if v is not None
    ]
    return max(locks) if locks else None


def calculate_battery_data(
    inputs: BatteryInputs, previous: BatteryLedger
) -> tuple[int, BatteryLedger, str, bool]:
    """One run of Node-RED's "Min_SOC beregning".

    Returns (min_soc, the new ledger, forced_charge "on"/"off", and whether
    the SoC is behind today's charge curve -- issue #33: a car charging
    then leaves the PV surplus to the battery). Pure -- testable on its
    own.
    """
    # DIFF: Node-RED's fixed 5 everywhere below.
    empty_soc = inputs.battery_min_soc
    hour = inputs.now.hour
    # Node-RED rounds the minute to the nearest quarter (can give 60).
    minute = _js_round(inputs.now.minute / 15) * 15
    month = inputs.now.month  # 1-12 (Node-RED's getMonth() is 0-11)
    soc = inputs.battery_soc

    # Issue #64: today's sale plan, remembered for the day -- read first,
    # since a sale's extra charge raises the target (effective_target()).
    today = inputs.now.date().isoformat()
    sale_date = previous.sale_date
    sale_hours = list(previous.sale_hours)
    sale_floors = list(previous.sale_floors)
    sale_target = previous.sale_target
    if sale_date != today:
        sale_date, sale_hours, sale_floors, sale_target = None, [], [], None
    if (
        inputs.sale_hours is not None
        and inputs.sale_floors is not None
        and len(inputs.sale_hours) == len(inputs.sale_floors)
    ):
        sale_date = today
        sale_hours = [int(h) for h in inputs.sale_hours]
        sale_floors = [float(v) for v in inputs.sale_floors]
        sale_target = inputs.sale_target if sale_hours else None
    target = effective_target(
        inputs.target,
        BatteryLedger(
            sale_date=sale_date, sale_hours=sale_hours, sale_target=sale_target
        ),
        inputs.now,
    )
    price = inputs.electricity_price
    now_minutes = hour * 60 + minute

    has_previous = previous.soc_previous is not None
    soc_previous = previous.soc_previous if has_previous else empty_soc
    soc_grid = previous.soc_grid
    soc_pv = previous.soc_pv
    cells = _sort_cells(list(previous.soc_cells))
    grid_afladning_pct_cum = previous.grid_afladning_pct_cum
    total_afladning_pct_cum = previous.total_afladning_pct_cum
    grid_opladning_pct_cum = previous.grid_opladning_pct_cum

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
    # DIFF (issue #35, Claus 2026-11-05): also from the moment tonight's
    # grid charge has started -- Node-RED's 06:00 left the battery free
    # between the end of the charge and 06:00, and the night price
    # protection only keeps grid cells: on 2026-11-05 the target (43 %) was
    # reached at 03:45 and the battery ran down to 36 % on its PV cells
    # before 06:00. Before the charge starts the battery stays free (the
    # night plan, issue #22, skips early hours precisely for that).
    # "Started" = a charge_now_* command has run since midnight, not the
    # grid counter: the inverter resets that up to a minute after midnight,
    # so yesterday's kWh locked the battery at 00:00 on 2026-11-06 (Kasper).
    today = inputs.now.strftime("%Y-%m-%d")
    charging = inputs.battery_command in _ACTIVE_CHARGE_COMMANDS
    night_charge_date = previous.night_charge_date
    if hour < 6 and charging:
        night_charge_date = today
    night_charge_started = hour < 6 and night_charge_date == today
    target_protection_active = (
        before_target_end and (hour >= 6 or night_charge_started) and not is_evening_free_window
    )

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
        elif inputs.battery_command in _ACTIVE_CHARGE_COMMANDS or inputs.grid_feeds_battery:
            # Issue #62: the counter can lag the charge (see module docstring).
            rise_is_grid = True

    if soc_rose:
        points_added = soc - soc_previous
        if rise_is_grid:
            soc_grid += points_added
            grid_opladning_pct_cum += points_added
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
    if soc <= empty_soc:
        soc_grid = 0.0
        soc_pv = 0.0
        cells = []

    # ---- Restart plan (see _restart_plan()) ---------------------------
    blocked = inputs.battery_command == _COMMAND_BLOCK_CHARGING
    plan_date, plan_start_minutes, plan_start_soc = _restart_plan(
        previous, today, blocked, hour, now_minutes, soc
    )
    plan_active = (
        plan_date == today
        and plan_start_minutes is not None
        and plan_start_minutes > _CHARGE_CURVE_START_MINUTES
    )
    curve_start_minutes = int(plan_start_minutes) if plan_active else _CHARGE_CURVE_START_MINUTES
    curve_start_soc = plan_start_soc if plan_active else empty_soc

    # ---- Minimum SoC ----------------------------------------------------
    manual_charger_active = (
        _CHARGER_STATE_ACTIVE_MANUAL in inputs.charger_states or inputs.car_charging_lock
    )
    charging_blocked = inputs.battery_command == _COMMAND_BLOCK_CHARGING
    grid_charge_planned_today = inputs.charge_status == _CHARGE_STATUS_YES_TODAY

    min_soc: float = empty_soc
    behind_charge_curve = False
    if not vinter:
        # February - October.
        wanted = wanted_charge_now(now_minutes, target, end_minutes, curve_start_minutes, curve_start_soc)
        behind_charge_curve = soc < target and soc < wanted and 9 <= hour <= 16 and before_target_end

        # 06-08: use the battery in the morning.
        if 6 <= hour <= 8:
            min_soc = empty_soc
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
            min_soc = empty_soc
        # A charger charging manually must not drain the house battery.
        if manual_charger_active and min_soc <= soc:
            min_soc = soc
        min_soc = min(min_soc, _MIN_SOC_CEILING)
        min_soc = max(min_soc, empty_soc)
    else:
        # November, December, January.
        min_soc = soc
        if hour >= 17:
            min_soc = empty_soc
        # The night (00-06) is always released, regardless of target --
        # grid-bought power is protected by the price-protection block
        # below instead.
        if hour <= 6:
            min_soc = empty_soc
        min_soc = min(min_soc, _MIN_SOC_CEILING)
        min_soc = max(min_soc, empty_soc)
        min_soc = min(min_soc, target)
        if manual_charger_active and min_soc <= soc:
            min_soc = soc

    # ---- Protect grid-bought energy until the target end time -----------
    # Holds the battery at the level it has after a night charge (see
    # _target_floor()). Can only raise min_soc. Uses the cells after this
    # run's rise/fall.
    target_floor_date, target_floor = _target_floor(
        previous,
        today,
        target_protection_active,
        any(v != 0 for v in cells),
        soc,
        target,
    )
    if target_floor is not None:
        min_soc = max(min_soc, target_floor)

    # ---- Night price protection (21:00-05:59) ----------------------------
    # Only the grid cells priced above price_floor are protected. Can only
    # raise min_soc, and never above the current SoC (so this can never
    # by itself demand buying more power).
    if price_period:
        protected_cells = [v for v in cells if v != 0 and v > price_floor]
        if protected_cells:
            protected_floor = min(empty_soc + len(protected_cells), soc)
            min_soc = max(min_soc, protected_floor)

    # In winter, min_soc may never be raised above the current SoC --
    # these two protections can only move it back down to SoC, never
    # trigger a forced charge.
    if vinter:
        min_soc = max(empty_soc, min(min_soc, soc))

    # ---- Daytime hours the night charge covered (issue #59) -------------
    day_plan_date = previous.day_plan_date
    day_plan_hours = list(previous.day_plan_hours)
    day_plan_reserve = previous.day_plan_reserve
    day_plan_from_charge = previous.day_plan_from_charge
    if day_plan_date != today:
        day_plan_date, day_plan_hours, day_plan_reserve, day_plan_from_charge = None, [], None, False
    # Issue #61: a plan worked out from today never replaces the night
    # charge's own.
    if (
        inputs.day_plan_hours is not None
        and inputs.day_plan_reserve is not None
        and (inputs.day_plan_from_charge or not day_plan_from_charge)
    ):
        day_plan_date, day_plan_hours, day_plan_reserve, day_plan_from_charge = (
            today,
            [int(h) for h in inputs.day_plan_hours],
            float(inputs.day_plan_reserve),
            inputs.day_plan_from_charge,
        )
    # True when the day plan or a sale lets the battery below the SoC on
    # purpose -- it ends the charge lock below (issue #67).
    released = False
    plan_lock = None
    if (
        day_plan_reserve is not None
        and hour in day_plan_hours
        and not manual_charger_active
        and not charging
        and not charging_blocked
    ):
        min_soc = max(empty_soc, min(day_plan_reserve, soc))
        target_floor_date, target_floor = None, None
        released = True
        plan_lock = min_soc

    # ---- PV energy above the evening reserve (issue #71) ----------------
    # Planned at few moments, not every run (Claus 2026-11-12): the first
    # run of the day from 06:00, when tomorrow's real prices arrive, and
    # when PV has added _PV_RELEASE_REPLAN_KWH more to share out. Hours of
    # the day plan and the sale keep their own rules.
    pv_release_date = previous.pv_release_date
    pv_release = previous.pv_release if pv_release_date == today else None
    if pv_release is None:
        pv_release_date = None
    if inputs.pv_release is not None and _DAY_PLAN_FIRST_HOUR <= hour < _DAY_PLAN_END_HOUR:
        excluded = set(day_plan_hours) | set(sale_hours)
        fresh = plan_pv_release(
            soc,
            sum(1 for v in cells if v == 0),
            empty_soc,
            hour,
            excluded,
            inputs.pv_release,
        )
        if fresh is not None and (
            pv_release is None
            or (fresh.with_tomorrow and not pv_release.get("with_tomorrow"))
            or fresh.extra_kwh >= (pv_release.get("extra_kwh") or 0.0) + _PV_RELEASE_REPLAN_KWH
        ):
            pv_release_date, pv_release = today, asdict(fresh)
    if (
        pv_release is not None
        and hour in (pv_release.get("hours") or [])
        and hour not in day_plan_hours
        and hour not in sale_hours
        and not manual_charger_active
        and not charging
        and not charging_blocked
    ):
        min_soc = max(empty_soc, min(float(pv_release["floor"]), soc))
        target_floor_date, target_floor = None, None
        released = True
        plan_lock = min_soc

    # ---- Sale hours (issue #64) ------------------------------------------
    selling = False
    sale_lock = None
    sale_done_hour = previous.sale_done_hour if previous.sale_date == today else None
    sale_kwh = dict(previous.sale_kwh) if previous.sale_date == today else {}
    if hour in sale_hours and not manual_charger_active and not charging:
        floor = max(empty_soc, sale_floors[sale_hours.index(hour)])
        min_soc = max(empty_soc, min(floor, soc))
        target_floor_date, target_floor = None, None
        released = True
        selling = soc > floor and sale_done_hour != hour
        if previous.selling and not selling:
            sale_done_hour = hour
        sale_lock = floor if selling else None

    # An active charge_now_* command counts as forced grid charging too,
    # even when min_soc > soc isn't itself true (most commonly while
    # grid_charge_planned_today already held min_soc at soc above).
    # Node-RED's "stop forcing once SoC reaches min_soc" latch is left out
    # on purpose -- see module docstring.
    forced_charge = "on" if (
        inputs.battery_command in _ACTIVE_CHARGE_COMMANDS
        or (not vinter and min_soc > soc)
    ) else "off"
    min_soc_rounded = _js_round(min_soc)

    # ---- Charge lock (issue #45, see kostal_min_soc_step()) ---------------
    # Taken when a charge_now_* command stops while the protection after
    # the grid charge holds -- the level that part of the charge reached.
    # A later block (say 02-04, then 11-12) takes it again at its own end.
    # Cleared when the protection ends -- and issue #67 (Kasper
    # 2026-11-10): when the day plan or a sale lets the battery below it.
    # The lock has done its job then; kept, it passed every point of the
    # SoC below it through (min(lock, min_soc) in kostal_min_soc_step()):
    # 82, 83, 84 ... one EEPROM write each, after a 93 % charge and a day
    # plan down to 35. Not cleared when min_soc merely dips below it -- a
    # point of noise would step the battery down to the 10 below (#45).
    charge_lock_date, charge_lock = previous.charge_lock_date, previous.charge_lock
    if not target_protection_active or charge_lock_date != today:
        charge_lock_date, charge_lock = None, None
    if released and charge_lock is not None and min_soc_rounded < charge_lock:
        charge_lock_date, charge_lock = None, None
    if target_protection_active and previous.charging_previous and not charging:
        charge_lock_date, charge_lock = today, float(min_soc_rounded)

    # ---- Car lock (issue #33, see kostal_min_soc_step()) ----------------
    # Taken once when a charger starts charging a car (min_soc is the SoC
    # then), kept while it charges, cleared when it stops.
    car_lock = None
    if manual_charger_active:
        car_lock = (
            previous.car_lock if previous.car_lock is not None else float(min_soc_rounded)
        )

    # ---- Hold lock (issue #69, see kostal_min_soc_step()) ---------------
    # Kasper 2026-11-11: when the day plan's first block ended at 10:00,
    # the winter hold set min_soc to the SoC (78), the step rounded it to
    # 70, and the battery covered the house with what the plan kept for
    # 13-16. A write is due anyway when the hold starts, so the SoC then is
    # held exactly (Claus). Kept while the hold lasts -- PV lifting the SoC
    # doesn't move it (no extra writes) -- cleared when the battery is let
    # go (day plan, sale, 17:00, the night) or charges.
    hold_lock = None
    if (
        vinter
        and 7 <= hour <= 16
        and not released
        and not charging
        and min_soc_rounded >= _js_round(soc)
    ):
        hold_lock = (
            previous.hold_lock if previous.hold_lock is not None else float(min_soc_rounded)
        )

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
        grid_opladning_pct_cum=grid_opladning_pct_cum,
        plan_date=plan_date,
        plan_start_minutes=plan_start_minutes,
        plan_start_soc=plan_start_soc,
        target_floor_date=target_floor_date,
        target_floor=target_floor,
        charge_lock_date=charge_lock_date,
        charge_lock=charge_lock,
        charging_previous=charging,
        night_charge_date=night_charge_date,
        car_lock=car_lock,
        hold_lock=hold_lock,
        day_plan_date=day_plan_date,
        day_plan_hours=day_plan_hours,
        day_plan_reserve=day_plan_reserve,
        day_plan_from_charge=day_plan_from_charge,
        sale_date=sale_date,
        sale_hours=sale_hours,
        sale_floors=sale_floors,
        selling=selling,
        sale_lock=sale_lock,
        plan_lock=plan_lock,
        sale_target=sale_target,
        sale_done_hour=sale_done_hour,
        sale_kwh=sale_kwh,
        pv_release_date=pv_release_date,
        pv_release=pv_release,
    )
    return min_soc_rounded, ledger, forced_charge, behind_charge_curve


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
        self._grid_charged_entity_id = (
            entity_map.get(ENTITY_KEY_BATTERY_GRID_CHARGED_DAY) or None
        )
        self._ledger = BatteryLedger()
        self._added_at = dt_util.utcnow()
        self._last_run: datetime | None = None
        self._pending_run: Any = None  # cancel callback of a scheduled run
        self._last_car_charging_lock = False  # issue #33
        # When the chargers stopped reading "charging" while the lock was
        # kept (issue #33), or None; and the cancel callback of the re-check
        # when that grace runs out.
        self._car_stopped_since: datetime | None = None
        self._car_discharge_since: datetime | None = None
        self._car_recheck: Any = None
        # Issue #64: the sale meter -- the last sample (time, W) and the
        # cancel callback of its timer, which only runs while selling.
        self._sale_sample: tuple[datetime, float] | None = None
        self._sale_meter: Any = None
        # Issue #71: Strømligning's "tomorrow" sensor, found on first use.
        self._tomorrow_prices_entity_id: str | None = None

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

        self._added_at = dt_util.utcnow()

        # Any-time triggers.
        watched = [self._soc_entity_id]
        for entity_id in (
            # Not a Node-RED trigger (its 5 % was fixed): a changed minimum
            # SoC, or the select becoming ready after a start, re-runs at once.
            pv_properties.battery_min_soc_entity_id(self.hass, self._entry),
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
        # Issue #33: re-run when a car starts or stops charging -- the
        # chargers' power changes all the time, so only the crossing counts.
        self.async_on_remove(
            charger_properties.async_track_car_charging(
                self.hass, self._entry, self._handle_car_charging_changed
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
        self._cancel_car_recheck()
        self._stop_sale_meter()
        if self._pending_run is not None:
            self._pending_run()
            self._pending_run = None
        await super().async_will_remove_from_hass()

    def _read_power(self, entity_map_key: str) -> float | None:
        """A power field from the wizard's entity map (W), normalized with
        the wizard's "invert sign" (pv_properties.power_sign()); None
        when unmapped or unknown."""
        entity_id = self._entry.options.get(CONF_ENTITY_MAP, {}).get(entity_map_key)
        power = read_float_state(self.hass.states.get(entity_id)) if entity_id else None
        if power is None:
            return None
        return power * pv_properties.power_sign(self._entry, entity_map_key)

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
    def _handle_car_charging_changed(self, _state_event) -> None:
        previous = self._last_car_charging_lock
        if self._update_car_charging_lock() != previous:
            self._handle_trigger(None)

    def _update_car_charging_lock(self) -> bool:
        self._last_car_charging_lock = self._car_charging_lock()
        return self._last_car_charging_lock

    def _car_charging_lock(self) -> bool:
        """Issue #33 -- see BatteryInputs.car_charging_lock. Once locked,
        a car only counts as stopped when the chargers haven't read
        "charging" for _CAR_PAUSE_HOLD -- or _CAR_POWER_UNAVAILABLE_HOLD
        while a power sensor is unavailable/unknown -- so a short pause
        doesn't unlock and relock the inverter. A re-check is scheduled for
        when that grace runs out."""
        # Imported here: battery_hold imports solar_data, which battery_data
        # mustn't pull in at module load.
        from .battery_hold import battery_hold_supported

        if battery_hold_supported(self.hass):
            return False
        car = charger_properties.car_charging(self.hass, self._entry)
        now = dt_util.utcnow()
        if not self._last_car_charging_lock:
            return self._car_discharge_lock(car.charging, now)
        if car.charging:
            self._car_stopped_since = None
            self._cancel_car_recheck()
            return True
        if self._car_stopped_since is None:
            self._car_stopped_since = now
        hold = _CAR_POWER_UNAVAILABLE_HOLD if car.power_unavailable else _CAR_PAUSE_HOLD
        remaining = (self._car_stopped_since + hold - now).total_seconds()
        if remaining > 0:
            self._cancel_car_recheck()
            self._car_recheck = event.async_call_later(
                self.hass, remaining, self._handle_car_recheck
            )
            return True
        self._car_stopped_since = None
        return False

    def _car_discharge_lock(self, car_charging: bool, now: datetime) -> bool:
        """Issue #33 (Claus 2026-11-10): whether a car charging, not yet
        locked, locks now -- once the battery has discharged for
        _CAR_DISCHARGE_HOLD in a row. Looked at again every
        _CAR_DISCHARGE_POLL_SECONDS while the car charges."""
        self._car_stopped_since = None
        self._cancel_car_recheck()
        power = self._read_power(ENTITY_KEY_BATTERY_POWER) if car_charging else None
        if power is None or power <= _CAR_DISCHARGE_W:
            self._car_discharge_since = None
        elif self._car_discharge_since is None:
            self._car_discharge_since = now
        if (
            self._car_discharge_since is not None
            and now - self._car_discharge_since >= _CAR_DISCHARGE_HOLD
        ):
            self._car_discharge_since = None
            return True
        if car_charging:
            self._car_recheck = event.async_call_later(
                self.hass, _CAR_DISCHARGE_POLL_SECONDS, self._handle_car_recheck
            )
        return False

    @callback
    def _handle_car_recheck(self, _now) -> None:
        self._car_recheck = None
        self._handle_car_charging_changed(None)

    def _cancel_car_recheck(self) -> None:
        if self._car_recheck is not None:
            self._car_recheck()
            self._car_recheck = None

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
        self._sample_sale()

        soc = read_float_state(self.hass.states.get(self._soc_entity_id))
        if soc is None:
            # Node-RED: return null if sensor.batteri_soc is
            # unavailable/unknown/undefined. No fallback -- skip the run.
            self._retry_soon(startup_only=True)
            return

        grid_charged_day = (
            read_float_state(self.hass.states.get(self._grid_charged_entity_id))
            if self._grid_charged_entity_id
            else None
        )
        if grid_charged_day is None:
            # Node-RED: return null if sensor.batteri_ladet_fra_grid_pr_dag
            # is unavailable/unknown/undefined. No fallback -- skip the run.
            if self._grid_charged_entity_id:
                self._retry_soon(startup_only=True)
            return

        if not self._inputs_ready():
            self._retry_soon()
            return

        battery_min_soc = pv_properties.battery_min_soc(self.hass, self._entry)
        pv_release_inputs = self._read_pv_release()
        # Issue #73: battery_hold's winter limit in daytime.
        evening_reserve = (
            evening_reserve_soc(
                pv_release_inputs.evening_need, battery_min_soc, pv_release_inputs.battery_size
            )
            if pv_release_inputs is not None
            else None
        )
        inputs = BatteryInputs(
            now=dt_util.now(),
            battery_soc=soc,
            # Node-RED's fallback target is its fixed 5, i.e. "empty".
            target=self._read_float_pv(
                pv_properties.battery_nightly_target_entity_id, battery_min_soc
            ),
            target_end_time=self._read_state_pv(
                pv_properties.battery_target_end_time_entity_id, _DEFAULT_TARGET_END_TIME
            ),
            # Via hub_properties.electricity_price(), not a direct entity
            # read (Claus, 2026-10-15): adds his configured surcharge on
            # top of the raw Strømligning value -- this feeds price_floor
            # below, so an un-added surcharge would make the night
            # price-protection window compare against a systematically
            # too-low price.
            electricity_price=hub_properties.electricity_price(self.hass, self._entry)
            or _DEFAULT_PRICE,
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
            battery_min_soc=battery_min_soc,
            car_charging_lock=self._update_car_charging_lock(),
            grid_feeds_battery=grid_feeds_battery(
                self._read_power(ENTITY_KEY_GRID_POWER),
                self._read_power(ENTITY_KEY_BATTERY_POWER),
            ),
            **self._read_day_plan(),
            **self._read_sale_plan(),
            pv_release=pv_release_inputs,
        )

        was_selling = self._ledger.selling
        min_soc, ledger, forced_charge, behind_charge_curve = calculate_battery_data(
            inputs, self._ledger
        )
        if pv_properties.is_battery_mode_manual(self.hass, self._entry):
            ledger.selling = False
        self._ledger = ledger

        # Issue #1: a Kostal gets the minimum SoC in steps of 10 -- see
        # kostal_min_soc_step().
        calculated_min_soc = min_soc
        if pv_properties.inverter_type(self.hass, self._entry) == INVERTER_TYPE_KOSTAL:
            min_soc = kostal_min_soc_step(
                min_soc,
                effective_target(inputs.target, ledger, inputs.now),
                inputs.battery_min_soc,
                kostal_lock(ledger),
            )
        self._attr_native_value = min_soc
        self._attr_extra_state_attributes = {
            # Before the Kostal steps (issue #1); the same as the state on
            # other inverters.
            "min_soc_calculated": calculated_min_soc,
            # Issue #45: the level the last grid charge stopped at, or None.
            "charge_lock": ledger.charge_lock,
            # Issue #33: the SoC a car charge started at, or None.
            "car_lock": ledger.car_lock,
            # Issue #69: the SoC the winter hold started at, or None.
            "hold_lock": ledger.hold_lock,
            "soc_grid": round(ledger.soc_grid, 2),
            "soc_pv": round(ledger.soc_pv, 2),
            "soc_cells": [round(v, 4) for v in ledger.soc_cells],
            "soc_count": len(ledger.soc_cells),
            "forced_charge": forced_charge,
            # Issue #33 -- read by data/battery_hold.py.
            "behind_charge_curve": behind_charge_curve,
            "avg_cheapest_hours": ledger.avg_cheapest_hours,
            "soc_previous": ledger.soc_previous,
            "grid_charge_previous": ledger.grid_charge_previous,
            "grid_afladning_pct_cum": round(ledger.grid_afladning_pct_cum, 2),
            "total_afladning_pct_cum": round(ledger.total_afladning_pct_cum, 2),
            "grid_opladning_pct_cum": round(ledger.grid_opladning_pct_cum, 2),
            # Issues #59/#61: today's daytime hours the battery may cover,
            # the SoC it keeps for the evening in them, and whether the
            # plan came from the night charge ("charge") or from today
            # ("today").
            "day_plan_hours": list(ledger.day_plan_hours),
            "day_plan_reserve": ledger.day_plan_reserve,
            "day_plan_source": (
                None
                if ledger.day_plan_date is None
                else "charge" if ledger.day_plan_from_charge else "today"
            ),
            # Issue #64: today's sale hours, the SoC each stops at, and
            # whether the battery sells right now.
            "sale_hours": list(ledger.sale_hours),
            "sale_floors": list(ledger.sale_floors),
            "selling": ledger.selling,
            "sale_kwh": {hour: round(kwh, 3) for hour, kwh in ledger.sale_kwh.items()},
            # Issue #73: the SoC kept for the evening 17-21 (floor + need),
            # battery_hold's winter limit 06-17. None while unknown.
            "evening_reserve": evening_reserve,
            # Issue #71: today's plan for the PV energy above the evening
            # reserve -- the daytime hours it is used in and the floor, the
            # kWh, the later hours it would otherwise have covered (time
            # order, 0-5 = the night after), the expected gain (kr), the
            # reserve and the PV energy above it, and whether the night's
            # prices were known / the battery would reach an unknown night.
            **{
                f"pv_release_{key}": (ledger.pv_release or {}).get(key)
                for key in (
                    "hours", "floor", "kwh", "instead_hours", "gain",
                    "reserve", "extra_kwh", "with_tomorrow", "night_unknown",
                )
            },
        }
        self._attr_available = True
        self.async_write_ha_state()

        if ledger.selling:
            self._start_sale_meter()
        else:
            self._stop_sale_meter()

        # Last: the select's state change re-triggers this sensor.
        self._apply_sale_command(
            inputs.battery_command, was_selling, ledger.selling, inputs.battery_soc, min_soc
        )

    def _apply_sale_command(
        self, command: str, was_selling: bool, selling: bool, soc: float, min_soc: float
    ) -> None:
        """Issue #64: discharge_now_100 while the battery sells, "internal"
        again once it stops -- or straight to the Growatt hold when it would
        take over anyway (battery_hold.stop_command()). Only a
        discharge_now_100 set here is taken back, and a grid charge or a
        manual command is never overruled."""
        if selling and command != COMMAND_SELL and command not in _ACTIVE_CHARGE_COMMANDS:
            option = COMMAND_SELL
        elif not selling and was_selling and command == COMMAND_SELL:
            # Imported here: battery_hold imports solar_data (see above).
            from .battery_hold import stop_command

            option = stop_command(self.hass, self._entry, soc, min_soc)
        else:
            return
        LOGGER.info("PwMngt: battery sale -> battery_command %s", option)
        async_dispatcher_send(self.hass, signal_set_battery_command(self._entry.entry_id), option)

    # -- sale meter (issue #64) -------------------------------------------

    def _sample_sale(self) -> None:
        """Adds the export since the last sample to the ledger's sale_kwh
        (the power read then, held until now), and takes a new sample while
        the battery sells."""
        now = dt_util.now()
        if self._sale_sample is not None and self._ledger.selling:
            started, export_w = self._sale_sample
            self._ledger.sale_kwh = add_sale_export(
                self._ledger.sale_kwh,
                started.hour,
                export_w,
                (now - started).total_seconds(),
            )
        self._sale_sample = (
            (
                now,
                sale_export_w(
                    self._read_power(ENTITY_KEY_GRID_POWER),
                    self._read_power(ENTITY_KEY_BATTERY_POWER),
                ),
            )
            if self._ledger.selling
            else None
        )

    def _start_sale_meter(self) -> None:
        if self._sale_sample is None:
            self._sample_sale()
        if self._sale_meter is None:
            self._sale_meter = event.async_track_time_interval(
                self.hass, self._handle_sale_meter, _SALE_METER_INTERVAL
            )

    def _stop_sale_meter(self) -> None:
        self._sale_sample = None
        if self._sale_meter is not None:
            self._sale_meter()
            self._sale_meter = None

    @callback
    def _handle_sale_meter(self, _now) -> None:
        self._sample_sale()

    def _read_sale_plan(self) -> dict[str, Any]:
        """Issue #64: sale_hours/sale_floors for BatteryInputs, from Battery
        charge status while it is "yes_today"."""
        entity_id = pv_properties.battery_charge_status_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state != _CHARGE_STATUS_YES_TODAY:
            return {}
        hours = state.attributes.get("sale_hours")
        floors = state.attributes.get("sale_floors")
        if not isinstance(hours, list) or not isinstance(floors, list):
            return {}
        try:
            return {
                "sale_hours": [int(hour) for hour in hours],
                "sale_floors": [float(floor) for floor in floors],
                "sale_target": _float_or_none(state.attributes.get("charge_target")),
            }
        except (TypeError, ValueError):
            return {}

    def _read_day_plan(self) -> dict[str, Any]:
        """Issue #59: day_plan_hours/day_plan_reserve for BatteryInputs,
        from Battery charge status while it is "yes_today" -- issue #61:
        otherwise worked out from today (fallback_day_plan())."""
        entity_id = pv_properties.battery_charge_status_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is not None and state.state == _CHARGE_STATUS_YES_TODAY:
            hours = state.attributes.get("day_hours")
            # Issue #70: the floor less the PV refill; a plan from before
            # it has only the evening reserve.
            reserve = state.attributes.get("day_floor_soc")
            if reserve is None:
                reserve = state.attributes.get("evening_reserve_soc")
            if isinstance(hours, list) and reserve is not None:
                try:
                    return {
                        "day_plan_hours": [int(hour) for hour in hours],
                        "day_plan_reserve": float(reserve),
                        "day_plan_from_charge": True,
                    }
                except (TypeError, ValueError):
                    pass
        return self._fallback_day_plan()

    def _fallback_day_plan(self) -> dict[str, Any]:
        """Issue #61: see fallback_day_plan()."""
        grid_cells = [price for price in self._ledger.soc_cells if price != 0]
        if not grid_cells:
            return {}
        battery_size = self._read_float_pv(pv_properties.battery_size_entity_id, 0.0)
        battery_min_soc = pv_properties.battery_min_soc(self.hass, self._entry)
        battery_target = self._read_float_pv(
            pv_properties.battery_nightly_target_entity_id, battery_min_soc
        )
        consumption = self._attributes_of(pv_properties.consumption_data_entity_id)
        solar = self._attributes_of(pv_properties.solar_data_entity_id)
        evening_need = evening_hourly_need(
            night_consumption=_float_or_none(
                consumption.get("nighttime_average_adjusted")
            ) or _float_or_none(consumption.get("nighttime_average")),
            night_consumption_raw=_float_or_none(consumption.get("nighttime_average")),
            evening_consumption=_float_or_none(consumption.get("evening_average")),
            late_evening_consumption=_float_or_none(
                consumption.get("late_evening_average_adjusted")
            ) or _float_or_none(consumption.get("late_evening_average")),
            pv_17_21=_float_or_none(solar.get("pv_forecast_17_21_today")),
            battery_target=battery_target,
            battery_min_soc=battery_min_soc,
            battery_size=battery_size,
        )
        plan = fallback_day_plan(
            hub_properties.electricity_prices_today(
                self.hass, self._entry, dt_util.now().strftime("%Y-%m-%d")
            ),
            grid_cells,
            self._read_float_pv(
                pv_properties.battery_price_difference_entity_id, _DEFAULT_PRICE_DIFFERENCE
            ),
            evening_need,
            battery_size,
            battery_target,
            battery_min_soc,
        )
        if plan is None:
            return {}
        hours, reserve = plan
        return {"day_plan_hours": hours, "day_plan_reserve": reserve, "day_plan_from_charge": False}

    def _read_pv_release(self) -> PvReleaseInputs | None:
        """Issue #71: the inputs of plan_pv_release(), or None while the
        battery size or today's prices are missing."""
        battery_size = self._read_float_pv(pv_properties.battery_size_entity_id, 0.0)
        now = dt_util.now()
        prices_today = hub_properties.electricity_prices_today(
            self.hass, self._entry, now.strftime("%Y-%m-%d")
        )
        if not battery_size or not prices_today:
            return None
        if self._tomorrow_prices_entity_id is None:
            self._tomorrow_prices_entity_id = auto_detect_tomorrow_prices_entity_id(self.hass)
        battery_min_soc = pv_properties.battery_min_soc(self.hass, self._entry)
        consumption = self._attributes_of(pv_properties.consumption_data_entity_id)
        solar = self._attributes_of(pv_properties.solar_data_entity_id)
        night = _float_or_none(consumption.get("nighttime_average_adjusted")) or _float_or_none(
            consumption.get("nighttime_average")
        )
        evening_need = evening_hourly_need(
            night_consumption=night,
            night_consumption_raw=_float_or_none(consumption.get("nighttime_average")),
            evening_consumption=_float_or_none(consumption.get("evening_average")),
            late_evening_consumption=_float_or_none(
                consumption.get("late_evening_average_adjusted")
            ) or _float_or_none(consumption.get("late_evening_average")),
            pv_17_21=_float_or_none(solar.get("pv_forecast_17_21_today")),
            battery_target=self._read_float_pv(
                pv_properties.battery_nightly_target_entity_id, battery_min_soc
            ),
            battery_min_soc=battery_min_soc,
            battery_size=battery_size,
        )
        # consumption_data's nighttime average covers 17-24 and 00-06: what
        # 17-23 doesn't use is the night after midnight.
        night_per_hour = (
            max(night - sum(evening_need.values()), 0.0) / len(_PV_RELEASE_NIGHT_HOURS)
            if night
            else 0.0
        )
        day_need: dict[int, float] = {}
        hourly = consumption.get("day_hourly_average_adjusted")
        if isinstance(hourly, list):
            for index, value in enumerate(hourly):
                kwh = _float_or_none(value)
                if kwh is not None:
                    day_need[_DAY_PLAN_FIRST_HOUR + index] = kwh
        return PvReleaseInputs(
            prices_today=prices_today,
            prices_tomorrow=hub_properties.electricity_prices_tomorrow(
                self.hass, self._entry, self._tomorrow_prices_entity_id
            ),
            evening_need=evening_need,
            night_need_per_hour=night_per_hour,
            day_need=day_need,
            battery_size=battery_size,
        )

    def _attributes_of(self, lookup) -> dict[str, Any]:
        entity_id = lookup(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        return dict(state.attributes) if state is not None else {}

    def _retry_soon(self, startup_only: bool = False) -> None:
        """Run again in _NOT_READY_RETRY_SECONDS instead of waiting for the
        next trigger -- at night, with SoC standing still, that could be
        the next quarter of an hour, with battery_data "unknown" and the
        hold without a limit all the while (issue #25). Other triggers in
        between merge into that run, as with the rate limit.
        startup_only: an input Node-RED also just waits for (SoC, grid
        charged today) -- retried only during _STARTUP_GRACE, when it's
        most likely just not loaded yet, not for as long as it's down."""
        if self._pending_run is not None:
            return
        if startup_only and dt_util.utcnow() - self._added_at >= _STARTUP_GRACE:
            return
        self._pending_run = event.async_call_later(
            self.hass, _NOT_READY_RETRY_SECONDS, self._handle_pending_run
        )

    def _inputs_ready(self) -> bool:
        """False right after a start, while the minimum-SoC select -- or,
        for the first _STARTUP_GRACE, Battery nightly target -- has no real
        value yet. Their fallbacks would publish a false min_soc for a
        moment (5 % at a restart, Claus 2026-10-29), which released
        data/battery_hold.py's hold at once. Skipping keeps the state
        "unknown" until a retry (_retry_soon()) finds them ready. After the grace period a missing target falls back as in
        Node-RED (e.g. no Solcast at all)."""
        min_soc_entity_id = pv_properties.battery_min_soc_entity_id(self.hass, self._entry)
        if self._read_float(min_soc_entity_id, None) is None:
            return False
        target_entity_id = pv_properties.battery_nightly_target_entity_id(self.hass, self._entry)
        if self._read_float(target_entity_id, None) is None:
            return dt_util.utcnow() - self._added_at >= _STARTUP_GRACE
        return True

    # -- input readers (Node-RED fallback when missing/unknown) ----------

    def _read_float(self, entity_id: str | None, default: float | None) -> float | None:
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
