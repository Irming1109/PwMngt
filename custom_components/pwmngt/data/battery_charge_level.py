"""Battery charge level: writes select.battery_command directly while
tonight's grid charge (data/battery_charge_decision.py's
battery_charge_status == "yes_today") is running.

Port of Claus's Node-RED function "Bestem ladeeffekt" (tab "Batteri
styring NY"), plus the small "SOC >= target" function and the "Lader
vi?"/"Intern" reset nodes wired around both of them. Confirmed with
Claus (2026-10-14) that writing battery_command here still never talks
to the inverter -- it's the same internal select entity
data/battery_data.py already only *reads* (its "block_charging" check);
nothing in this codebase sends anything to the inverter itself.

select.battery_mode (select.py, Old: select.pv_battery_mode -- declared
in Node-RED but never actually read anywhere there) gates everything
below: while it's "manual", _run_charge_level() and
_maybe_target_reached() both do nothing at all (Claus, 2026-10-14) --
battery_command is then only ever whatever's set by hand.

Node-RED's own trigger graph for "Bestem ladeeffekt" (traced from the
flow export, not guessed):
- Hourly, every hour on the hour, all day (inject "Time", crontab
  "0 0-23 * * *") -> a fixed 30 s delay -> read battery_charge_status
  ("Status") -> only if it's "yes_today" ("Ja (i dag)") -> another fixed
  30 s delay -> run. (The companion "not_calculated"/"yes_today" branch
  there instead re-runs "Skal batteri lades" itself within 14:00-23:30 --
  that's a separate hourly chain on "Skal batteri lades"' own tab, ported
  in data/battery_charge_decision.py's own hourly nudge, not this one.)
- "Hovedsikring overvågning": any of the three main-fuse-load sensors
  (ENTITY_KEY_MAIN_FUSE_LOAD_L1/L2/L3) reaching >= 20 A, while
  battery_command is already one of the charge_now_* options AND
  battery_charge_status is exactly "yes_today" -- rate-limited to at
  most once a minute (Node-RED: delay node, pauseType "rate", drop
  excess messages; ported here as a trailing-edge coalesce, the same
  rate-limit style already used by data/battery_data.py, rather than a
  literal drop).

Each run (_run()) picks one of four outcomes, same ladder as Node-RED's
"Bestem ladeeffekt":
- An abort ("return null" in Node-RED: invalid tariff fuse size,
  charge_wish isn't "yes_today", or target already reached) writes
  nothing at all -- battery_command is left exactly as it was.
- "Charge 75"/"Charge 50"/"Charge 25" (enough amp headroom left, and the
  current hour is one of battery_charge_status's own cheap hours) write
  battery_command unconditionally -- Node-RED's switch wires these three
  straight to their ha-select nodes, no further gate.
- "Intern" (not enough headroom, or the current hour isn't a cheap one)
  only gets written if battery_command IS currently one of the
  charge_now_* options -- i.e. only to actually stop an in-progress
  charge; if we're already not charging, nothing happens (Node-RED: the
  "Lader vi ?" api-current-state node's halt_if sends to output 1,
  which wires to the "Intern" ha-select, exactly when its condition --
  battery_command includes one of the "Lad nu" states -- is TRUE;
  confirmed against zachowj's Current State node docs, which is the
  opposite of what an earlier version of this module assumed).

The "SOC >= target" gate (Node-RED's own small function, node "SOC >=
target", behind trigger-state "SOC når Target value": batteri_soc >=
batteri_target while sensor.pv_battery_charge is "Ja (i dag)") always
resets battery_charge_status back to "not_calculated" -- that part
sends data/battery_charge_decision.py's signal_battery_charge_target_
reached(), unconditionally. It then separately runs through the exact
same "Lader vi ?" gate described above before writing "internal" to
battery_command -- so it only writes "internal" (actually stopping the
charge) when a charge_now_* command is active; otherwise nothing more
happens.

Deliberate difference from Node-RED (issue #15, Claus 2026-11-01): while
a charge_now_* command is active, the battery's own charge power (the
Battery power field) is taken out of the measured fuse loads before the
new level is added. Node-RED counted a running charge twice, so with a
15 kW Growatt "Charge 75" flipped to "Charge 50" and back at ~30 A on a
50 A fuse -- every flip a new inverter command.

Deliberate difference from Node-RED (issue #15, Claus 2026-11-03): a
lower level forced by the fuse (a step down during a cheap hour) is
applied at once, but going up again waits until 10 minutes after the
last such step down. A load spike of a few seconds, or a house load
right at the limit, otherwise flipped 75 -> 50 -> 75 within one minute
(two inverter commands, seen twice on Claus's 2026-11-02 night). A
recheck is scheduled for when the wait ends, so going up doesn't depend
on the fuse trigger (only fires at >= 20 A) or the next hourly run.

Deliberate difference from Node-RED (issue #22, Claus 2026-11-03): "Skal
batteri lades" plans its hours as if the battery were empty, so there
are nearly always more cheap night hours than needed, and the charge ran
in the first of them -- the target was reached early and the battery
then sat locked for the rest of the night. Now, at the start of a
planned night hour (0-5) before the charge has started, the hour is
skipped (the battery keeps discharging) when the planned night hours
after it are enough to reach the target from the current SoC, counted
at a safe power: the lower of 50 % of the max charge power and what the
fuse allows right now (the charge itself still runs at "Charge 75" when
the fuse allows it). A charge that has started runs on. Safety net: if
the target isn't reached when the last planned night hour has passed,
charging goes on in the following hours, until 06:00 at the latest.
Planned day hours are left as they are.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import event
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from ..const import CONF_ENTITY_MAP, ENTITY_KEY_BATTERY_POWER, ENTITY_KEY_BATTERY_SOC
from ..const import (
    ENTITY_KEY_MAIN_FUSE_LOAD_L1,
    ENTITY_KEY_MAIN_FUSE_LOAD_L2,
    ENTITY_KEY_MAIN_FUSE_LOAD_L3,
)
from ..helpers.state_helper import read_float_state
from ..properties import hub_properties, pv_properties
from ..select import signal_set_battery_command
from .battery_charge_decision import signal_battery_charge_target_reached

LOGGER = logging.getLogger(__name__)

# Node-RED fallbacks for inputs that are missing/unknown.
_TARIFF_FUSE_MARGIN = 0.85  # Node-RED: tarif_sikring * 0.85.
_BATTERY_MAX_CHARGE_FALLBACK = 3000.0  # W
_BATTERY_SOC_FALLBACK = 5.0  # %, Node-RED: parseFloat(...) || 5.
# The "SOC når Target value" trigger-state's own first constraint: the
# raw SoC reading must itself be >= 5 before the function even runs, on
# top of (and independent from) _BATTERY_SOC_FALLBACK above. Happens to
# be the same number in Claus's flow, but a separate knob.
_SOC_TRIGGER_FLOOR = 5.0
_FUSE_OVERLOAD_THRESHOLD = 20.0  # A

# battery_charge_status (data/battery_charge_decision.py) value that
# means tonight's grid charge is wanted. Old: "Ja (i dag)".
_STATUS_YES_TODAY = "yes_today"

# battery_command (select.py) options that mean "actively charging from
# the grid right now" -- the "Lader vi ?" gate's own check. Old: "Lad nu
# - 25/50/75/100% effekt".
_ACTIVE_CHARGE_COMMANDS = frozenset(
    {"charge_now_25", "charge_now_50", "charge_now_75", "charge_now_100"}
)
_COMMAND_INTERNAL = "internal"  # Old: "Intern".

# Hourly trigger: two chained fixed 30 s delays (Node-RED: two "delay"
# nodes, pauseType "delay", timeout 30 -- the randomFirst/randomLast
# fields on them are inert, Node-RED only applies those under pauseType
# "random", not "delay").
_HOURLY_FIRST_DELAY_SECONDS = 30
_HOURLY_SECOND_DELAY_SECONDS = 30

# Fuse-overload trigger: at most 1 run/minute (Node-RED: delay node,
# pauseType "rate", rate 1/minute, drop=true).
_FUSE_RATE_LIMIT_SECONDS = 60

# DIFF (issue #15, Claus 2026-11-03): after the fuse has forced a lower
# level, wait this long before going up again -- see module docstring.
_STEP_UP_HOLD_SECONDS = 10 * 60

# DIFF (issue #22, Claus 2026-11-03): the night plan -- see module
# docstring. Night = hours 0-5; the safety net stops at 06:00.
_NIGHT_END_HOUR = 6
# Share of the max charge power the skip decision counts on, at most --
# one fuse step below the normal "Charge 75".
_SAFE_CHARGE_SHARE = 0.50
# Same margin data/battery_charge_decision.py plans its hours with.
_CHARGETIME_SAFETY_MARGIN = 1.10
_NIGHT_SKIP = "skip"
_NIGHT_EXTEND = "extend"

# Charge levels in order, for "is this a step up or down?". Anything not
# listed (internal, hold_min_soc, block_*) counts as not charging.
_CHARGE_LEVEL_RANK = {
    "charge_now_25": 1,
    "charge_now_50": 2,
    "charge_now_75": 3,
    "charge_now_100": 4,
}


# ---------------------------------------------------------------------------
# Pure calculations -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


@dataclass
class BatteryChargeLevelInputs:
    """Everything one run reads from Home Assistant, already resolved to
    plain values (Node-RED fallbacks applied by the caller)."""

    charge_wish: str
    # select.tariff_fuse_size's raw value (A), before the 0.85 margin.
    # None if missing/invalid -- Node-RED's own NaN, caught below.
    tariff_fuse_size_raw: float | None
    fuse_load_l1: float
    fuse_load_l2: float
    fuse_load_l3: float
    battery_max_charge_raw: float | None
    battery_soc: float | None
    battery_target: float
    # battery_charge_status's own hour_<current hour> attribute (Node-
    # RED: battery_charge[`hour${current_hour}`] === true).
    hour_is_cheap: bool
    # W the battery is charging with right now while a charge_now_*
    # command is active, else 0. DIFF (issue #15): already part of the
    # measured fuse loads, so it is taken out before adding the new level.
    battery_charge_power: float = 0.0
    # DIFF (issue #22): what the night plan needs. current_hour None turns
    # it off (plain Node-RED behaviour).
    current_hour: int | None = None
    # Every hour_<N> attribute of battery_charge_status that is true.
    cheap_hours: frozenset[int] = frozenset()
    battery_size_kwh: float | None = None
    # A charge_now_* command is active right now.
    charging_now: bool = False


def _amps_per_phase(power_w: float) -> float:
    """Amps per phase for a power spread evenly over three phases at 230 V
    (Node-RED's "(effekt / 3) / 230")."""
    return (power_w / 3) / 230


def _amps_allowed(load: float, amps_needed_75: float, tariff_fuse_size: float) -> float:
    if load + amps_needed_75 > tariff_fuse_size:
        allowed = tariff_fuse_size - load
        allowed = min(allowed, amps_needed_75)
        allowed = max(allowed, 0.0)
        return math.floor(allowed)
    return amps_needed_75


def _charge_hours_needed(
    battery_soc: float, battery_target: float, battery_size_kwh: float, power_w: float
) -> float:
    """Whole hours to charge from battery_soc up to battery_target at
    power_w, with the same 10 % margin "Skal batteri lades" plans with.
    math.inf when power_w is 0 (the fuse allows nothing right now)."""
    missing_kwh = max(battery_target - battery_soc, 0.0) / 100 * battery_size_kwh
    if missing_kwh <= 0:
        return 0
    if power_w <= 0:
        return math.inf
    return math.ceil(missing_kwh * 1000 * _CHARGETIME_SAFETY_MARGIN / power_w)


def _night_plan(
    inputs: BatteryChargeLevelInputs, battery_soc: float, safe_power_w: float
) -> str | None:
    """Issue #22 (see module docstring): _NIGHT_SKIP to leave this planned
    night hour out because the planned night hours after it are enough,
    _NIGHT_EXTEND to keep charging in an unplanned night hour after the
    last planned one, or None for the plain Node-RED rule."""
    hour = inputs.current_hour
    if hour is None or hour >= _NIGHT_END_HOUR or not inputs.battery_size_kwh:
        return None
    planned_night = [h for h in inputs.cheap_hours if h < _NIGHT_END_HOUR]
    if not planned_night:
        return None
    if inputs.hour_is_cheap:
        if inputs.charging_now:
            # Once started, a charge runs on -- skipping now would only
            # add a stop and a start (two inverter commands).
            return None
        hours_left_after = sum(1 for h in planned_night if h > hour)
        needed = _charge_hours_needed(
            battery_soc, inputs.battery_target, inputs.battery_size_kwh, safe_power_w
        )
        return _NIGHT_SKIP if hours_left_after >= needed else None
    if max(planned_night) < hour:
        return _NIGHT_EXTEND
    return None


def calculate_battery_charge_level(inputs: BatteryChargeLevelInputs) -> str | None:
    """One run of Node-RED's "Bestem ladeeffekt". Returns the
    battery_command option to apply ("charge_now_75"/"_50"/"_25", or
    "internal"), or None when Node-RED itself returns null and produces
    no output at all -- the caller must then leave battery_command
    exactly as it was.
    """
    tariff_fuse_size = (
        inputs.tariff_fuse_size_raw * _TARIFF_FUSE_MARGIN
        if inputs.tariff_fuse_size_raw is not None
        else None
    )
    if tariff_fuse_size is None:
        # Node-RED: node.warn(...) + return null.
        return None

    battery_max_charge_full = (
        inputs.battery_max_charge_raw
        if inputs.battery_max_charge_raw is not None
        else _BATTERY_MAX_CHARGE_FALLBACK
    )
    amps_needed_full = _amps_per_phase(battery_max_charge_full)

    amps_needed_75 = amps_needed_full * 0.75
    amps_needed_50 = amps_needed_full * 0.50
    amps_needed_25 = amps_needed_full * 0.25

    if inputs.charge_wish != _STATUS_YES_TODAY:
        return None

    # DIFF (issue #15): the measured loads already include the battery's
    # own charge current. Node-RED added the new level on top of it, so a
    # running "Charge 75" counted itself twice and flipped to "Charge 50"
    # and back on every fuse check (unnoticed with a 6 kW Kostal, ~6.5 A
    # per phase at 75 %; a 15 kW Growatt draws ~16 A).
    own_amps = _amps_per_phase(max(inputs.battery_charge_power, 0.0))
    amps_allowed = min(
        _amps_allowed(max(load - own_amps, 0.0), amps_needed_75, tariff_fuse_size)
        for load in (inputs.fuse_load_l1, inputs.fuse_load_l2, inputs.fuse_load_l3)
    )

    battery_soc = (
        inputs.battery_soc if inputs.battery_soc is not None else _BATTERY_SOC_FALLBACK
    )
    if battery_soc >= inputs.battery_target:
        return None

    hour_is_cheap = inputs.hour_is_cheap
    night_plan = _night_plan(
        inputs,
        battery_soc,
        safe_power_w=min(
            battery_max_charge_full * _SAFE_CHARGE_SHARE,
            amps_allowed * 3 * 230,
        ),
    )
    if night_plan == _NIGHT_SKIP:
        return _COMMAND_INTERNAL
    if night_plan == _NIGHT_EXTEND:
        hour_is_cheap = True

    if not hour_is_cheap:
        return _COMMAND_INTERNAL

    if amps_allowed >= amps_needed_75:
        return "charge_now_75"
    if amps_allowed >= amps_needed_50:
        return "charge_now_50"
    if amps_allowed >= amps_needed_25:
        return "charge_now_25"
    return _COMMAND_INTERNAL


def battery_target_reached(
    battery_soc: float | None, battery_target: float, charge_wish: str
) -> bool:
    """Node-RED's trigger-state "SOC når Target value" constraints plus
    its function "SOC >= target": true only while charge_wish is exactly
    "yes_today" (Old: "Ja (i dag)") and the SoC has reached target --
    AND the trigger-state's own first constraint, a floor of its own on
    the raw SoC reading (new_state.state >= 5) *before* the function
    even runs, independent of the function's internal `|| 5` fallback
    below."""
    soc = battery_soc if battery_soc is not None else _BATTERY_SOC_FALLBACK
    return (
        charge_wish == _STATUS_YES_TODAY
        and soc >= _SOC_TRIGGER_FLOOR
        and soc >= battery_target
    )


def currently_charging(battery_command: str | None) -> bool:
    """Node-RED's "Lader vi ?" api-current-state halt_if check: true
    while battery_command is one of the charge_now_* options -- this is
    when "internal" actually gets written (see module docstring)."""
    return battery_command in _ACTIVE_CHARGE_COMMANDS


# ---------------------------------------------------------------------------
# Home Assistant wiring. Not an Entity -- nothing displays this, it only
# ever writes to battery_command (select.py) and signals
# data/battery_charge_decision.py. Same role as PwMngtSolarDataCoordinator
# in data/solar_data.py: started/stopped from sensor.py's
# async_setup_entry via entry.async_on_unload().
# ---------------------------------------------------------------------------


class PwMngtBatteryChargeLevelController:
    """Runs calculate_battery_charge_level() on the Node-RED schedule and
    writes select.battery_command. See the module docstring for the
    exact trigger graph and the "Lader vi ?" gate's quirk."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._soc_entity_id = entity_map.get(ENTITY_KEY_BATTERY_SOC) or None
        self._battery_power_entity_id = entity_map.get(ENTITY_KEY_BATTERY_POWER) or None
        # Fixed L1/L2/L3 order -- each may be None ("Not available" in
        # the wizard), read as 0 (see const.py's ENTITY_KEY_MAIN_FUSE_
        # LOAD_L1/L2/L3 docstring).
        self._fuse_entity_ids_by_phase: list[str | None] = [
            entity_map.get(ENTITY_KEY_MAIN_FUSE_LOAD_L1) or None,
            entity_map.get(ENTITY_KEY_MAIN_FUSE_LOAD_L2) or None,
            entity_map.get(ENTITY_KEY_MAIN_FUSE_LOAD_L3) or None,
        ]
        self._fuse_entity_ids = [
            entity_id for entity_id in self._fuse_entity_ids_by_phase if entity_id
        ]
        self._unsubscribers: list = []
        self._pending_hourly_run: Any = None
        self._pending_fuse_run: Any = None
        self._last_fuse_run = None
        self._last_step_down = None
        self._pending_step_up_recheck: Any = None

        if not self._soc_entity_id:
            LOGGER.warning(
                "PwMngt: no Battery SoC entity configured (Options -> "
                "Solar PV Plant) -- battery_command will never be set by "
                "data/battery_charge_level.py"
            )
        if not self._fuse_entity_ids:
            LOGGER.info(
                "PwMngt: no main-fuse-load entities configured (Options "
                "-> Solar PV Plant) -- the fuse-overload trigger for "
                "data/battery_charge_level.py is disabled; the hourly "
                "trigger still runs"
            )

    @callback
    def async_start(self) -> None:
        """Subscribe to the three Node-RED triggers."""
        self._unsubscribers.append(
            event.async_track_time_change(
                self.hass, self._handle_hourly_trigger, minute=0, second=0
            )
        )
        if self._fuse_entity_ids:
            self._unsubscribers.append(
                event.async_track_state_change_event(
                    self.hass, self._fuse_entity_ids, self._handle_fuse_trigger
                )
            )
        if self._soc_entity_id:
            self._unsubscribers.append(
                event.async_track_state_change_event(
                    self.hass, [self._soc_entity_id], self._handle_soc_trigger
                )
            )

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
        if self._pending_hourly_run is not None:
            self._pending_hourly_run()
            self._pending_hourly_run = None
        if self._pending_fuse_run is not None:
            self._pending_fuse_run()
            self._pending_fuse_run = None
        if self._pending_step_up_recheck is not None:
            self._pending_step_up_recheck()
            self._pending_step_up_recheck = None

    # -- triggers ----------------------------------------------------------

    @callback
    def _handle_hourly_trigger(self, _now) -> None:
        # Node-RED: "Time" inject -> fixed 30 s delay -> THEN read
        # battery_charge_status ("Status") -- the status read happens
        # after this first delay, not before it.
        self._pending_hourly_run = event.async_call_later(
            self.hass, _HOURLY_FIRST_DELAY_SECONDS, self._handle_hourly_first_delay
        )

    @callback
    def _handle_hourly_first_delay(self, _now) -> None:
        if self._read_charge_status() != _STATUS_YES_TODAY:
            # Node-RED: the companion branch here re-runs "Skal batteri
            # lades" itself within 14:00-23:30 -- deliberately left
            # unported, see data/battery_charge_decision.py.
            self._pending_hourly_run = None
            return
        self._pending_hourly_run = event.async_call_later(
            self.hass, _HOURLY_SECOND_DELAY_SECONDS, self._handle_hourly_second_delay
        )

    @callback
    def _handle_hourly_second_delay(self, _now) -> None:
        self._pending_hourly_run = None
        self._run_charge_level()

    @callback
    def _handle_fuse_trigger(self, state_event) -> None:
        new_state = state_event.data.get("new_state")
        load = read_float_state(new_state)
        if load is None or load < _FUSE_OVERLOAD_THRESHOLD:
            return
        if not currently_charging(self._read_battery_command()):
            return
        if self._read_charge_status() != _STATUS_YES_TODAY:
            return
        self._schedule_rate_limited_run()

    def _schedule_rate_limited_run(self) -> None:
        if self._pending_fuse_run is not None:
            return
        now = dt_util.utcnow()
        if self._last_fuse_run is not None:
            wait = (
                self._last_fuse_run + timedelta(seconds=_FUSE_RATE_LIMIT_SECONDS) - now
            ).total_seconds()
            if wait > 0:
                self._pending_fuse_run = event.async_call_later(
                    self.hass, wait, self._handle_pending_fuse_run
                )
                return
        self._last_fuse_run = now
        self._run_charge_level()

    @callback
    def _handle_pending_fuse_run(self, _now) -> None:
        self._pending_fuse_run = None
        self._last_fuse_run = dt_util.utcnow()
        self._run_charge_level()

    @callback
    def _handle_soc_trigger(self, state_event) -> None:
        old_state = state_event.data.get("old_state")
        new_state = state_event.data.get("new_state")
        if old_state is not None and new_state is not None and old_state.state == new_state.state:
            return
        self._maybe_target_reached()

    # -- runs ----------------------------------------------------------------

    def _run_charge_level(self) -> None:
        if pv_properties.is_battery_mode_manual(self.hass, self._entry):
            # select.battery_mode (select.py) is "manual" -- see module
            # docstring. battery_command is left exactly as it is.
            return
        battery_target = self._read_target()
        if battery_target is None:
            # Not ready yet -- see _read_target()'s own docstring. Leave
            # battery_command as it is and wait for the next trigger.
            return
        inputs = BatteryChargeLevelInputs(
            charge_wish=self._read_charge_status(),
            tariff_fuse_size_raw=self._read_tariff_fuse_size(),
            fuse_load_l1=self._read_fuse_load(0),
            fuse_load_l2=self._read_fuse_load(1),
            fuse_load_l3=self._read_fuse_load(2),
            battery_max_charge_raw=self._read_battery_max_charge(),
            battery_soc=self._read_soc(),
            battery_target=battery_target,
            hour_is_cheap=self._read_hour_is_cheap(),
            battery_charge_power=self._read_grid_charge_power(),
            current_hour=dt_util.now().hour,
            cheap_hours=self._read_cheap_hours(),
            battery_size_kwh=self._read_battery_size(),
            charging_now=currently_charging(self._read_battery_command()),
        )
        result = calculate_battery_charge_level(inputs)
        if result is None:
            return
        if inputs.hour_is_cheap and not self._step_allowed(result):
            return
        self._apply_result(result)

    def _step_allowed(self, result: str) -> bool:
        """Issue #15's step-up wait (see module docstring): records a step
        down during a cheap hour, and holds back a step up until
        _STEP_UP_HOLD_SECONDS after the last one."""
        current_rank = _CHARGE_LEVEL_RANK.get(self._read_battery_command() or "", 0)
        result_rank = _CHARGE_LEVEL_RANK.get(result, 0)
        now = dt_util.utcnow()
        if result_rank < current_rank:
            self._last_step_down = now
            self._schedule_step_up_recheck(_STEP_UP_HOLD_SECONDS)
            return True
        if result_rank > current_rank and self._last_step_down is not None:
            remaining = (
                self._last_step_down + timedelta(seconds=_STEP_UP_HOLD_SECONDS) - now
            ).total_seconds()
            if remaining > 0:
                LOGGER.debug(
                    "PwMngt: holding back %s for %.0f s after a fuse step down",
                    result,
                    remaining,
                )
                self._schedule_step_up_recheck(remaining)
                return False
        return True

    def _schedule_step_up_recheck(self, delay_seconds: float) -> None:
        if self._pending_step_up_recheck is not None:
            self._pending_step_up_recheck()
        self._pending_step_up_recheck = event.async_call_later(
            self.hass, delay_seconds + 1, self._handle_step_up_recheck
        )

    @callback
    def _handle_step_up_recheck(self, _now) -> None:
        self._pending_step_up_recheck = None
        self._run_charge_level()

    def _maybe_target_reached(self) -> None:
        if pv_properties.is_battery_mode_manual(self.hass, self._entry):
            return
        battery_target = self._read_target()
        if battery_target is None:
            # Not ready yet -- see _read_target()'s own docstring. Don't
            # treat an unready value as "target reached".
            return
        if not battery_target_reached(
            self._read_soc(), battery_target, self._read_charge_status()
        ):
            return
        async_dispatcher_send(
            self.hass, signal_battery_charge_target_reached(self._entry.entry_id)
        )
        self._apply_result(_COMMAND_INTERNAL)

    def _apply_result(self, result: str) -> None:
        if result == _COMMAND_INTERNAL and not currently_charging(self._read_battery_command()):
            # Node-RED's "Lader vi ?" gate: only actually stop a charge
            # that's active -- see module docstring.
            return
        self._send_battery_command(result)

    def _send_battery_command(self, option: str) -> None:
        async_dispatcher_send(
            self.hass, signal_set_battery_command(self._entry.entry_id), option
        )

    # -- input readers (Node-RED fallback when missing/unknown) ------------

    def _read_charge_status(self) -> str:
        entity_id = pv_properties.battery_charge_status_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in ("unknown", "unavailable"):
            return ""
        return state.state

    def _read_hour_is_cheap(self) -> bool:
        entity_id = pv_properties.battery_charge_status_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return False
        current_hour = dt_util.now().hour
        return state.attributes.get(f"hour_{current_hour}") is True

    def _read_cheap_hours(self) -> frozenset[int]:
        entity_id = pv_properties.battery_charge_status_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return frozenset()
        return frozenset(
            hour for hour in range(24) if state.attributes.get(f"hour_{hour}") is True
        )

    def _read_battery_size(self) -> float | None:
        entity_id = pv_properties.battery_size_entity_id(self.hass, self._entry)
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None

    def _read_battery_command(self) -> str | None:
        entity_id = pv_properties.battery_command_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        return state.state if state is not None else None

    def _read_tariff_fuse_size(self) -> float | None:
        entity_id = hub_properties.tariff_fuse_size_entity_id(self.hass, self._entry)
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None

    def _read_fuse_load(self, phase_index: int) -> float:
        entity_id = self._fuse_entity_ids_by_phase[phase_index]
        value = read_float_state(self.hass.states.get(entity_id)) if entity_id else None
        return value if value is not None else 0.0

    def _read_grid_charge_power(self) -> float:
        """W the battery is charging with while a charge_now_* command is
        active -- that power comes from the grid and is already in the
        measured fuse loads. 0 otherwise (a PV charge doesn't load the main
        fuse) and while Battery power is missing."""
        if not currently_charging(self._read_battery_command()):
            return 0.0
        power = (
            read_float_state(self.hass.states.get(self._battery_power_entity_id))
            if self._battery_power_entity_id
            else None
        )
        if power is None:
            return 0.0
        # Normalized: positive = discharging, so charging is negative.
        power *= pv_properties.power_sign(self._entry, ENTITY_KEY_BATTERY_POWER)
        return max(-power, 0.0)

    def _read_battery_max_charge(self) -> float | None:
        entity_id = pv_properties.battery_max_charge_effect_entity_id(self.hass, self._entry)
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None

    def _read_soc(self) -> float | None:
        return read_float_state(self.hass.states.get(self._soc_entity_id)) if self._soc_entity_id else None

    def _read_target(self) -> float | None:
        """None while battery_nightly_target hasn't computed a value yet
        (typically: solar_data's own coordinator hasn't run once since a
        restart). DIFF (Claus, 2026-10-15): same restart-bug class already
        fixed in battery_charge_decision.py's own _read_target() (0.3.30)
        -- Node-RED's own fallback here was `|| 0`, but a 0 reads as "SoC
        already >= target", so right after a restart the very next SoC
        update could make _maybe_target_reached() fire immediately,
        telling battery_charge_decision.py the charge target was reached
        before it had even been computed -- wrongly resetting a correctly
        -restored "yes_today" straight back to "not_calculated". Both
        _run_charge_level() and _maybe_target_reached() below now skip
        the run entirely while this is None, instead of computing from a
        0 that only means "not ready"."""
        entity_id = pv_properties.battery_nightly_target_entity_id(self.hass, self._entry)
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None
