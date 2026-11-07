"""Battery hold: keeps a Growatt battery at battery_data's minimum SoC by
switching battery_command to "hold_min_soc" and publishing the power the
inverter should deliver to the house meanwhile ("Battery hold power").

Port of Claus's Node-RED function '"Lås" batteri - growatt' (tab
"Batteri styring NY", export 28-10-2026), written for his Growatt WIT,
which can't hold a minimum SoC on its own. The hold engages at once when
SoC is below the limit, and at the limit only once the battery has kept
discharging for a while; it releases again when SoC is HYSTERESIS points
above the limit. Meanwhile the inverter runs normal Load First.

PwMngt never talks to the inverter itself (Claus, 2026-10-28): where
Node-RED called growatt_modbus.set_battery_mode and number.set_value on
the remote power, this module only writes its own entities. Claus's own
Node-RED translates them, the same way his "Lad nu - Growatt" translates
the charge_now_* commands:
- battery_command changes TO "hold_min_soc" or "block_charging":
  set_battery_mode "discharge".
- battery_command changes FROM those two to anything else:
  set_battery_mode "release" and remote power 0. Between the two: nothing.
- Battery hold power changes to a number while battery_command is one of
  them: remote power = -round(W / W per Growatt-%). Battery hold power
  has its target already when either starts (issue #31) -- written before
  the command, so the translator starts with it rather than at 1 %.

"Bloker ladning" (issue #28, new in PwMngt -- Claus's Kostal blocks
charging through relays, a Growatt has no such input, and its VPP Charge
Cutoff SOC only accepts 70-100 %): while battery_command is
"block_charging", Battery hold power is max(house, PV - margin) -- PV
covers the house, the surplus is exported, the battery doesn't charge
(beyond a little, issue #30: the margin covers the inverter's DC/AC loss,
which would otherwise come out of the battery -- it starts at 8 % of PV,
at least 300 W, and is corrected from Battery power towards a ~100 W
charge, half the error per minute; only the power target moves, so no
EEPROM writes are added), and a house above PV is
covered by the battery as usual. At or below battery_data's minimum SoC
it is PV - margin alone: the battery idles. Same deadband and cooldown
as the hold, so a sudden PV drop can let the battery export a little for
up to the cooldown before the power follows.

Car charging (issue #33, new in PwMngt -- the battery must never cover a
car charge, any time of day, Claus 2026-11-04): while a charger charges a
car (charger_properties.car_charging()) and SoC is above the limit, the
hold is on with Battery hold power = max(House Consumption minus the car,
PV minus the loss margin) -- PV goes to the house and the car, the battery
covers at most the house's own deficit, the grid the rest. Behind today's
charge curve (battery_data's behind_charge_curve) the PV surplus charges
the battery first: the power is the house without the car. At or below
the limit the normal rules apply. The minimum switch time also
applies here, so a car charge costs at most the hold's usual two EEPROM
writes; following the car's power only moves the remote power. A charger
without a power sensor: the battery idles (0 W). A Kostal can't be steered
like this -- data/battery_data.py holds its SoC instead.

Only created on an install with growatt_modbus' "House Consumption" sensor
(battery_hold_supported()) -- an inverter that holds its minimum SoC
itself (e.g. Kostal) gets battery_data written to it instead (issue #1).

Node-RED sensor -> PwMngt:

| Node-RED                                   | PwMngt                                           |
|--------------------------------------------|--------------------------------------------------|
| sensor.pv_battery_status (state, the limit) | battery_data (data/battery_data.py)              |
| sensor.batteri_soc                         | Battery SoC field                                |
| sensor.growatt_solar_solar_total_power     | pv_total (PV1 + PV2 + PV3)                       |
| sensor.growatt_load_house_consumption      | growatt_modbus "House Consumption" (auto-detected) |
| sensor.batteri_effekt                      | Battery power field, via pv_properties.power_sign() |
| select.pv_batteri_styring                  | battery_command (read; "hold_min_soc"/"internal" written) |
| output 1, growatt_modbus.set_battery_mode  | battery_command -> Claus's Node-RED translator   |
| output 2, number.set_value (remote power)  | battery_hold_power (W) -> Claus's Node-RED translator |
| node.status text                           | the "status" attribute                           |

Deliberate differences from Node-RED (agreed with Claus, issue #12):
- The minimum time between switches (10 min) also applies when the hold
  starts, not only when it releases. Node-RED could start and stop every
  quarter of an hour when the limit hovers around 10 % (20 % in winter).
- battery_command itself says whether the hold is active, instead of a
  remembered flag: the hold can't get stuck when another command (or the
  user) takes over -- the translator releases as soon as battery_command
  leaves "hold_min_soc", whoever changes it. Node-RED paused only for
  "Lad nu"; here every other command pauses it ("charge_now_*",
  "block_charging", "discharge_now_*" all outrank the hold), and the hold only
  ever writes battery_command while it is "internal" or "hold_min_soc".
- Paused while battery_mode is "manual", like the other battery_command
  writers (battery_charge_level.py, battery_sell_decision.py). Unlike
  them it releases its own "hold_min_soc" once when manual starts
  (Claus, 2026-11-01): left alone, the paused hold published 0 W, so the
  translator held the battery hard until auto was back.
- Power in W rather than Growatt-%: W per % depends on the inverter
  model, so the conversion lives in the translator. The deadbands are
  Node-RED's 2 / 1 Growatt-% at its 150 W per %.
- Winter (November-January) is read in Home Assistant's own time zone
  rather than a fixed Europe/Copenhagen.

New logic with no Node-RED counterpart (Claus, issue #14):
- The wait at the limit is 3 minutes, but 6 with 3 or more big PV shifts
  in the last hour (solar_data's pv_big_shifts_last_60min) -- on a
  cloudy day a discharge is likely a passing cloud, and every hold costs
  two EEPROM writes.
- A wait starts when the battery discharges while SoC is AT the limit.
  During it, SoC dropping below the limit doesn't hold at once (Growatt
  reports whole percent, so 11.4 % can read 10 % at the first dip) --
  only 2 or more points below does.
- If the discharge stops during the wait with SoC below the limit, there
  is no hold: the inverter charges by itself. It holds once the
  discharge starts again (or SoC is 2 points below). SoC below the limit
  without a wait before it (e.g. the limit stepping up at 09:00) still
  holds at once, as in Node-RED.
- The worst case is 2 points below the limit, whatever the battery size
  -- a small battery reaches it within a few minutes, so its wait is
  shorter in practice.

Night standby (issue #48, Claus 2026-11-07): asked for 0 W in a hard
hold with no PV, the WIT takes its own consumption (~275 W) from the
battery -- 0.9 kWh of the grid charge in one night. A positive remote
power makes it import (tested: +1 % = ~175 W more from the grid), so the
hold publishes a negative power for what PV doesn't cover of the
inverter's standby, in whole 150 W steps rounded up (a little charge
rather than a discharge). The standby is learned from Battery power
while PV is ~0 and the last write has settled. Only the power moves, so
no EEPROM writes are added.

Triggers (as Node-RED): every minute, and whenever PV total, battery_data
or battery_command changes -- plus battery_mode, for the manual pause.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import deque
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import event
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    ENTITY_KEY_BATTERY_POWER,
    ENTITY_KEY_BATTERY_SOC,
    GROWATT_MODBUS_HOUSE_CONSUMPTION,
    GROWATT_MODBUS_PLATFORM,
)
from ..devices import pv_device_info
from ..helpers.entity_registry_helper import find_entity_by_name
from ..helpers.state_helper import read_float_state
from ..properties import charger_properties, pv_properties
from ..select import signal_set_battery_command
from .solar_data import KEY_PV_BIG_SHIFTS_LAST_60MIN

LOGGER = logging.getLogger(__name__)

COMMAND_HOLD = "hold_min_soc"  # New in PwMngt -- no Node-RED option.
COMMAND_BLOCK_CHARGING = "block_charging"  # Old: "Bloker ladning".
_COMMAND_INTERNAL = "internal"  # Old: "Intern".
# The commands Battery hold power carries a power for -- the translator
# runs both through the inverter's remote control (issue #28).
_POWER_COMMANDS = frozenset({COMMAND_HOLD, COMMAND_BLOCK_CHARGING})

# Node-RED's settings, same names translated.
_FLOOR_LIMIT = 10  # BUND: Growatt's lowest cutoff -- a limit at or below means no hold.
_WINTER_MIN_LIMIT = 20  # VINTER_MIN: Nov-Jan, hold only from a limit of 20 %.
_WINTER_MONTHS = (11, 12, 1)
_MARGIN_W = 300  # MARGIN_W: safety margin at the limit (clouds).
# Issue #30: under "Bloker ladning" the margin also covers the inverter's
# DC/AC loss, which grows with PV -- measured 2026-11-04 on Claus's WIT:
# ~3 % at 5.9 kW, ~4.6 % at 6.5 kW, ~7.8 % at 8.7 kW. The margin starts at
# this share of PV (at least _MARGIN_W) and is then corrected from the
# battery's own power -- see _block_margin().
_BLOCK_LOSS_SHARE = 0.08
# The correction aims for a small charge (W, negative = charging) and
# leaves it alone in this band -- the hold's own deadbands, so it doesn't
# add power writes for noise.
_BLOCK_AIM_W = -100.0
_BLOCK_BAND_W = (-300.0, 50.0)
# Only correct when the last power write has had time to show in Battery
# power (Growatt: ~20 s updates, up to ~45 s after a command), and at most
# this often. Half the error per step, so the slow loop doesn't swing.
_BLOCK_SETTLE_SECONDS = 60
_BLOCK_GAIN = 0.5
# The correction's range, as a share of PV on top of _BLOCK_LOSS_SHARE.
_BLOCK_CORRECTION_RANGE = (-_BLOCK_LOSS_SHARE, 0.07)
_LOW_PV_W = 1000  # LAV_PV_W: below this, a hard hold at 0 W (never discharges).
# Issue #48: the inverter's own consumption covered from the grid in a
# hard hold -- start value, range and the translator's 150 W per Growatt-%.
_STANDBY_START_W = 275.0
_STANDBY_RANGE_W = (0.0, 600.0)
_STANDBY_STEP_W = 150.0
# Learned only with PV below this (night: Battery power is the standby
# minus the import) and after the last write has settled.
_STANDBY_LEARN_PV_W = 30.0
_STANDBY_LEARN_GAIN = 0.5
_FULL_SOC = 98  # FULD_SOC: above this, PV surplus may go to the grid.
_COOLDOWN_SECONDS = 30  # COOLDOWN_MS: the growatt_modbus lock per register.
_DEADBAND_UP_W = 300  # DEADBAND_OP: 2 Growatt-% at 150 W per %.
_DEADBAND_DOWN_W = 150  # DEADBAND_NED: 1 % -- quicker, to avoid discharging.
_HYSTERESIS = 5  # HYSTERESE: points above the limit before releasing.
_MIN_SWITCH_SECONDS = 10 * 60  # MIN_SKIFT_MS: spares the inverter's EEPROM.
_DISCHARGE_W = 100  # AFLAD_W: discharging above this counts as "drawing".
_DISCHARGE_SECONDS = 3 * 60  # AFLAD_TID_MS: at the limit, this long in a row.
# New (issue #14): with this many big PV shifts in the last hour (clouds),
# a discharge at the limit is likely a passing cloud -- wait longer.
_UNSTABLE_PV_SHIFTS = 3
_DISCHARGE_SECONDS_UNSTABLE = 6 * 60
# New (issue #14): while waiting, SoC this many points below the limit
# locks at once after all.
_WAIT_MAX_POINTS_BELOW = 2

_RUN_INTERVAL_SECONDS = 60  # Node-RED inject "1 Minut".
# Issue #42: the house power is the median of this many House Consumption
# readings. growatt_modbus derives it from the inverter's own output and
# the grid, read at different moments: right after the hold power steps,
# it reads 0 or the whole output for one update. Followed as is, the hold
# power jumps with it, which makes the next reading jump -- it swung
# between 0 and 6 kW every ~30 s. A median of 3 drops those single
# readings and follows a real change after two updates.
_HOUSE_MEDIAN_READINGS = 3

# The "status" attribute (Node-RED's node.status text). Translated in
# strings.json under entity.sensor.battery_hold_power.state_attributes.
STATUS_INVALID_INPUT = "invalid_input"
STATUS_PAUSED_MANUAL = "paused_manual"
STATUS_PAUSED_OTHER_COMMAND = "paused_other_command"
STATUS_RELEASED_NO_LIMIT = "released_no_limit"
STATUS_RELEASED_WINTER = "released_winter"
STATUS_WAITING_DISCHARGE = "waiting_discharge"
STATUS_WAITING_BELOW_LIMIT = "waiting_below_limit"
STATUS_BELOW_LIMIT_NOT_DISCHARGING = "below_limit_not_discharging"
STATUS_AT_LIMIT = "at_limit"
STATUS_WAITING_MIN_SWITCH_TIME = "waiting_min_switch_time"
STATUS_HOLD_STARTED = "hold_started"
STATUS_RELEASED = "released"
STATUS_ABOVE_LIMIT = "above_limit"
STATUS_CATCHING_UP = "catching_up"
STATUS_HARD_HOLD_LOW_PV = "hard_hold_low_pv"
STATUS_HOLDING_AT_LIMIT = "holding_at_limit"
STATUS_NORMAL_OPERATION = "normal_operation"
STATUS_BLOCKING_CHARGE = "blocking_charge"
STATUS_BLOCKING_CHARGE_AT_LIMIT = "blocking_charge_at_limit"
# Issue #48: the hold statuses at 0 W, where the grid covers the
# inverter's standby (_standby_import()).
_STANDBY_STATUSES = frozenset({STATUS_HARD_HOLD_LOW_PV, STATUS_CATCHING_UP})
STATUS_COVERING_HOUSE_CAR_CHARGING = "covering_house_car_charging"
STATUS_CAR_CHARGING_PV_TO_CAR = "car_charging_pv_to_car"
STATUS_CAR_CHARGING_PV_TO_BATTERY = "car_charging_pv_to_battery"

PwM_BATTERY_HOLD_SENSORS: list[SensorEntityDescription] = [
    # New in PwMngt -- Node-RED wrote this straight to
    # number.growatt_battery_remote_charge_and_discharge_power.
    SensorEntityDescription(
        key="battery_hold_power",
        name="Battery hold power",
        icon="mdi:battery-lock",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="W",
    ),
]


def battery_hold_supported(hass: HomeAssistant) -> bool:
    """Whether this install gets the hold: a Growatt read through
    growatt_modbus, recognized by its "House Consumption" sensor (the
    hold needs it). Looked up by (platform, name), never by entity_id."""
    return find_entity_by_name(hass, GROWATT_MODBUS_PLATFORM, GROWATT_MODBUS_HOUSE_CONSUMPTION) is not None


# ---------------------------------------------------------------------------
# Pure calculation -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


@dataclass
class BatteryHoldState:
    """What the hold remembers between runs (Node-RED: context
    "laas_state"). Times are epoch seconds; 0 = never."""

    # Whether battery_command was "hold_min_soc" after the last run -- to
    # notice someone else setting or leaving it in between.
    holding: bool = False
    # The power last published (W). None = the hold has just started and
    # the first write is still due (Node-RED: sidst_pct = -1).
    power_w: float | None = 0.0
    last_write: float = 0.0
    last_switch: float = 0.0
    discharge_start: float = 0.0
    # The current discharge began while SoC was AT the limit -- a dip below
    # it doesn't lock before the wait is over (issue #14).
    waiting: bool = False
    # A wait ended without a hold (the discharge stopped) with SoC below
    # the limit: no hold unless the discharge starts again (issue #14).
    held_off_below: bool = False
    # Whether battery_command was "block_charging" after the last run
    # (issue #28) -- to force the first power write when it starts.
    blocking: bool = False
    # Issue #30: the learned correction of the block's loss margin (share
    # of PV), kept between blocks, and when it was last changed.
    block_correction: float = 0.0
    last_block_correction: float = 0.0
    # Issue #48: the inverter's learned standby consumption (W), and when
    # it was last learned.
    standby_w: float = _STANDBY_START_W
    last_standby_learn: float = 0.0


@dataclass
class BatteryHoldInputs:
    limit: float | None  # battery_data's min SoC (%).
    soc: float | None
    pv_power: float | None
    house_power: float | None
    battery_power: float | None  # Normalized: positive = discharging.
    battery_command: str | None
    battery_mode_manual: bool
    now: float  # Epoch seconds.
    winter: bool
    pv_big_shifts: int = 0  # solar_data's pv_big_shifts_last_60min.
    # Issue #33: a car is charging, and with how much (W; None = unknown,
    # the charger has no power sensor).
    car_charging: bool = False
    car_power_w: float | None = None
    # battery_data's behind_charge_curve: the PV surplus goes to the
    # battery first while a car charges.
    behind_charge_curve: bool = False


@dataclass
class BatteryHoldResult:
    state: BatteryHoldState
    command: str | None  # battery_command to write, None = leave it.
    status: str
    active: bool
    target_power_w: float | None  # Before deadband/cooldown.


def _block_margin(st: BatteryHoldState, inputs: BatteryHoldInputs, feedback: bool) -> float:
    """The loss margin (W) under "Bloker ladning" (issue #30): the larger of
    _MARGIN_W and (_BLOCK_LOSS_SHARE + the learned correction) of PV.

    With feedback, once the last power write has settled, the correction
    moves half-way towards a small charge (_BLOCK_AIM_W) when Battery power
    is outside _BLOCK_BAND_W: discharging means the margin is too small,
    charging more than ~300 W that it is too large. It only changes the
    power target, so it never adds an EEPROM write (only set_battery_mode
    is stored) and the deadbands still limit the power writes."""
    pv_power = inputs.pv_power or 0.0
    now = inputs.now
    if (
        feedback
        and pv_power > 0
        and inputs.battery_power is not None
        and now - st.last_write >= _BLOCK_SETTLE_SECONDS
        and now - st.last_block_correction >= _BLOCK_SETTLE_SECONDS
        and not _BLOCK_BAND_W[0] <= inputs.battery_power <= _BLOCK_BAND_W[1]
    ):
        step_w = (inputs.battery_power - _BLOCK_AIM_W) * _BLOCK_GAIN
        low, high = _BLOCK_CORRECTION_RANGE
        st.block_correction = min(max(st.block_correction + step_w / pv_power, low), high)
        st.last_block_correction = now
    return max(_MARGIN_W, pv_power * (_BLOCK_LOSS_SHARE + st.block_correction))


def _publish_power(st: BatteryHoldState, target: float, now: float) -> float:
    """Move st.power_w (the published power) towards target, behind the
    deadband and the cooldown. Returns the rounded target."""
    target = float(round(target))
    # Deadband: quick when the output goes DOWN (towards 0), slower UP.
    if st.power_w is None:
        due = True
    else:
        difference = target - st.power_w
        due = abs(difference) >= (_DEADBAND_DOWN_W if difference < 0 else _DEADBAND_UP_W)
    if due and now - st.last_write >= _COOLDOWN_SECONDS:
        st.power_w = target
        st.last_write = now
    return target


def _start_power(st: BatteryHoldState, target: float, now: float) -> float:
    """Publish target at once when the hold or the block starts (issue
    #31): the start writes the inverter's remote power anyway, so the lock
    starts now -- later changes wait out the cooldown as usual."""
    target = float(round(target))
    st.power_w = target
    st.last_write = now
    return target


def _car_target(st: BatteryHoldState, inputs: BatteryHoldInputs, starting: bool) -> tuple[float, str]:
    """Issue #33: the power (W) and status while a car charges -- the
    battery never covers the car (Claus, 2026-11-04).

    House Consumption includes the charger, so the house alone is house
    minus the car (unknown car power: 0, the battery idles). PV goes to
    the house and the car first: the inverter puts out all of it (minus
    the DC/AC loss margin of "Bloker ladning", issue #30), the battery
    covers at most the house's own deficit, the grid the rest of the car.
    Behind today's charge curve the surplus charges the battery instead,
    so the evening target is still reached, and the grid covers the car."""
    house_power, pv_power = inputs.house_power, inputs.pv_power
    house_only = (
        max(0.0, house_power - inputs.car_power_w) if inputs.car_power_w is not None else 0.0
    )
    if pv_power <= 0:
        return house_only, STATUS_COVERING_HOUSE_CAR_CHARGING
    if inputs.behind_charge_curve:
        return house_only, STATUS_CAR_CHARGING_PV_TO_BATTERY
    # Battery power only says something about the loss while PV alone sets
    # the target (see _block_margin()).
    feedback = pv_power - _MARGIN_W > house_only and not starting
    pv_out = max(0.0, pv_power - _block_margin(st, inputs, feedback))
    return max(house_only, pv_out), STATUS_CAR_CHARGING_PV_TO_CAR


def _standby_import(st: BatteryHoldState, inputs: BatteryHoldInputs, learn: bool) -> float:
    """Issue #48: the power (W, negative = import) that covers what PV
    doesn't of the inverter's standby in a hard hold -- whole 150 W steps,
    rounded up. With learn, the standby is first corrected half-way
    towards Battery power minus what was last published (night only)."""
    now = inputs.now
    if (
        learn
        and st.power_w is not None
        and (inputs.pv_power or 0.0) < _STANDBY_LEARN_PV_W
        and inputs.battery_power is not None
        and now - st.last_write >= _BLOCK_SETTLE_SECONDS
        and now - st.last_standby_learn >= _BLOCK_SETTLE_SECONDS
    ):
        # Battery power = standby - import, and import = -power_w.
        measured = inputs.battery_power - st.power_w
        low, high = _STANDBY_RANGE_W
        st.standby_w = min(max(st.standby_w + (measured - st.standby_w) * _STANDBY_LEARN_GAIN, low), high)
        st.last_standby_learn = now
    missing = st.standby_w - (inputs.pv_power or 0.0)
    if missing <= 0:
        return 0.0
    return -math.ceil(missing / _STANDBY_STEP_W) * _STANDBY_STEP_W


def _hold_target(soc: float, limit: float, pv_power: float, house_power: float) -> tuple[float, str]:
    """The power (W) and status while the hold is active."""
    if soc > limit:
        # Within the hysteresis: the battery may discharge down to the limit.
        target = max(house_power, pv_power) if soc >= _FULL_SOC else house_power
        return target, STATUS_ABOVE_LIMIT
    if soc < limit:
        # Below the limit: all PV into the battery, the grid covers the house.
        return 0.0, STATUS_CATCHING_UP
    if pv_power < _LOW_PV_W:
        # At the limit with little PV: hard hold.
        return 0.0, STATUS_HARD_HOLD_LOW_PV
    # At the limit: PV covers the house as far as it reaches, minus a margin.
    return max(0.0, min(house_power, pv_power - _MARGIN_W)), STATUS_HOLDING_AT_LIMIT


def calculate_battery_hold(state: BatteryHoldState, inputs: BatteryHoldInputs) -> BatteryHoldResult:
    """One run of '"Lås" batteri - growatt' -- see the module docstring
    for what differs from Node-RED."""
    numbers = (inputs.limit, inputs.soc, inputs.pv_power, inputs.house_power, inputs.battery_power)
    if any(value is None for value in numbers) or not inputs.battery_command:
        # Node-RED: "Ugyldigt input - intet ændret".
        return BatteryHoldResult(
            state, None, STATUS_INVALID_INPUT, inputs.battery_command in _POWER_COMMANDS, None
        )

    limit, soc, pv_power, house_power = inputs.limit, inputs.soc, inputs.pv_power, inputs.house_power
    now = inputs.now
    st = replace(state)

    if inputs.battery_command == COMMAND_BLOCK_CHARGING:
        # Issue #28: "Bloker ladning" through the same remote control as
        # the hold -- a command like "Lad nu", so it applies in manual
        # mode too. The hold's own bookkeeping starts over afterwards.
        st.holding = False
        st.discharge_start = 0.0
        st.waiting = False
        st.held_off_below = False
        st.last_switch = 0.0  # May hold at once when the block ends.
        starting = not st.blocking
        st.blocking = True
        # PV total is the strings' DC power, the remote power the inverter's
        # AC output: asked for exactly PV, it takes its conversion loss
        # from the battery (~200 W at 5.9 kW, ~375 W at 8.7 kW, Claus
        # 2026-11-04, issue #30). The margin covers it: the battery may then
        # charge a little, but never exports.
        # Battery power only says something about the loss while the
        # target is PV alone -- with a house above PV the battery is meant
        # to discharge.
        feedback = (soc <= limit or house_power <= pv_power) and not starting
        pv_out = max(0.0, pv_power - _block_margin(st, inputs, feedback))
        if soc <= limit:
            # At or below the minimum SoC: PV out only, the battery idles.
            target, status = pv_out, STATUS_BLOCKING_CHARGE_AT_LIMIT
        else:
            # PV covers the house, the surplus is exported; a house above
            # PV is covered by the battery as usual.
            target, status = max(house_power, pv_out), STATUS_BLOCKING_CHARGE
        publish = _start_power if starting else _publish_power
        return BatteryHoldResult(st, None, status, True, publish(st, target, now))
    if st.blocking:
        st.blocking = False
        st.power_w = 0.0

    # How long has the battery been discharging in a row?
    discharging = inputs.battery_power > _DISCHARGE_W
    if discharging:
        if not st.discharge_start:
            st.discharge_start = now
    else:
        st.discharge_start = 0.0
        if st.waiting and soc < limit:
            # The wait ended without a hold (e.g. the cloud passed) --
            # don't hold for the dip it left behind (issue #14, rule 5).
            st.held_off_below = True
        st.waiting = False
    if soc >= limit:
        st.held_off_below = False
    discharge_seconds = now - st.discharge_start if st.discharge_start else 0.0
    wait_seconds = (
        _DISCHARGE_SECONDS_UNSTABLE
        if inputs.pv_big_shifts >= _UNSTABLE_PV_SHIFTS
        else _DISCHARGE_SECONDS
    )

    # Paused while battery_command belongs to someone else. Forgets its
    # state, so it starts cleanly afterwards (Node-RED: tvangslad).
    paused_status = None
    if inputs.battery_mode_manual:
        paused_status = STATUS_PAUSED_MANUAL
    elif inputs.battery_command not in (_COMMAND_INTERNAL, COMMAND_HOLD):
        paused_status = STATUS_PAUSED_OTHER_COMMAND
    if paused_status is not None:
        st.power_w = 0.0
        st.last_switch = 0.0  # May hold at once when the other command ends.
        st.discharge_start = 0.0
        st.waiting = False
        st.held_off_below = False
        if inputs.battery_mode_manual and inputs.battery_command == COMMAND_HOLD:
            # Manual means the inverter's own control plus the user's
            # buttons -- release our own hold rather than freeze it at
            # 0 W, which would block discharging until auto is back.
            st.holding = False
            return BatteryHoldResult(st, _COMMAND_INTERNAL, paused_status, False, None)
        st.holding = inputs.battery_command == COMMAND_HOLD
        return BatteryHoldResult(
            st, None, paused_status, inputs.battery_command == COMMAND_HOLD, None
        )

    active = inputs.battery_command == COMMAND_HOLD
    # Set by someone else (e.g. by hand): its target at once, like our own start.
    started_by_other = active and not st.holding
    if not active:
        st.power_w = 0.0

    # Should the hold be active? Only once SoC has reached the limit --
    # release again when PV has charged it a bit above.
    switch_ok = now - st.last_switch >= _MIN_SWITCH_SECONDS
    should_hold = active
    status = None
    # Issue #33: a car charging above the limit -- hold, so the battery
    # never covers the car (_car_target()). At or below the limit
    # the normal rules apply. Starting and stopping keep the minimum switch
    # time: they are the hold's only EEPROM writes.
    car_cover = inputs.car_charging and soc > limit

    if car_cover:
        if active:
            should_hold = True
        elif switch_ok:
            should_hold = True
        else:
            status = STATUS_WAITING_MIN_SWITCH_TIME
    elif (limit < _WINTER_MIN_LIMIT) if inputs.winter else (limit <= _FLOOR_LIMIT):
        # No (relevant) limit: always normal operation, at once.
        should_hold = False
        status = (
            STATUS_RELEASED_WINTER
            if inputs.winter and limit > _FLOOR_LIMIT
            else STATUS_RELEASED_NO_LIMIT
        )
    elif not active and soc <= limit:
        if soc == limit and discharging:
            st.waiting = True
        if soc <= limit - _WAIT_MAX_POINTS_BELOW:
            # Far below: hold at once, waiting or not.
            hold_now = True
        elif soc == limit or st.waiting:
            # At the limit, or dipped below it during the wait: hold only
            # once the battery has kept discharging long enough --
            # otherwise the inverter manages by itself (issue #14: longer
            # on a cloudy day, and a dip during the wait doesn't count).
            hold_now = discharge_seconds >= wait_seconds
            if not hold_now:
                if soc < limit:
                    status = STATUS_WAITING_BELOW_LIMIT
                else:
                    status = STATUS_WAITING_DISCHARGE if discharging else STATUS_AT_LIMIT
        elif st.held_off_below and not discharging:
            # A wait ended below the limit without discharging since:
            # the inverter charges by itself (issue #14, rule 5).
            hold_now = False
            status = STATUS_BELOW_LIMIT_NOT_DISCHARGING
        else:
            # Below the limit: hold at once (all PV into the battery to
            # catch up).
            hold_now = True
        if hold_now:
            if switch_ok:
                should_hold = True
            else:
                status = STATUS_WAITING_MIN_SWITCH_TIME
    elif active and soc >= min(limit + _HYSTERESIS, _FULL_SOC):
        if switch_ok:
            should_hold = False
        else:
            status = STATUS_WAITING_MIN_SWITCH_TIME

    if should_hold and not active:
        st.waiting = False
        st.held_off_below = False
        st.holding = True
        st.last_switch = now
        if car_cover:
            target, _ = _car_target(st, inputs, starting=True)
        else:
            target, hold_status = _hold_target(soc, limit, pv_power, house_power)
            if hold_status in _STANDBY_STATUSES:
                target = _standby_import(st, inputs, learn=False)
        return BatteryHoldResult(
            st, COMMAND_HOLD, STATUS_HOLD_STARTED, True, _start_power(st, target, now)
        )
    if not should_hold and active:
        st.holding = False
        st.power_w = 0.0
        st.last_write = now
        st.last_switch = now
        return BatteryHoldResult(st, _COMMAND_INTERNAL, status or STATUS_RELEASED, False, None)

    st.holding = active
    if not active:
        return BatteryHoldResult(st, None, status or STATUS_NORMAL_OPERATION, active, None)

    # The power while the hold is active.
    if car_cover:
        target, status = _car_target(st, inputs, starting=started_by_other)
    else:
        target, hold_status = _hold_target(soc, limit, pv_power, house_power)
        if hold_status in _STANDBY_STATUSES:
            target = _standby_import(st, inputs, learn=not started_by_other)
        if soc > limit:
            status = status or hold_status
        else:
            status = hold_status
    publish = _start_power if started_by_other else _publish_power
    return BatteryHoldResult(st, None, status, active, publish(st, target, now))


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


@dataclass
class PwMngtBatteryHoldStoredData(ExtraStoredData):
    """BatteryHoldState across restarts (Node-RED keeps its context in a
    file for the same reason: the minimum switch time and cooldown must
    survive a restart)."""

    state: BatteryHoldState

    def as_dict(self) -> dict[str, Any]:
        return vars(self.state).copy()

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtBatteryHoldStoredData":
        power_w = restored.get("power_w", 0.0)
        return cls(
            BatteryHoldState(
                holding=bool(restored.get("holding", False)),
                power_w=float(power_w) if power_w is not None else None,
                last_write=float(restored.get("last_write") or 0.0),
                last_switch=float(restored.get("last_switch") or 0.0),
                discharge_start=float(restored.get("discharge_start") or 0.0),
                waiting=bool(restored.get("waiting", False)),
                held_off_below=bool(restored.get("held_off_below", False)),
                blocking=bool(restored.get("blocking", False)),
                block_correction=float(restored.get("block_correction") or 0.0),
                last_block_correction=float(restored.get("last_block_correction") or 0.0),
                standby_w=float(restored.get("standby_w") or _STANDBY_START_W),
                last_standby_learn=float(restored.get("last_standby_learn") or 0.0),
            )
        )


class PwMngtBatteryHoldSensor(RestoreEntity, SensorEntity):
    """Battery hold power -- runs calculate_battery_hold() and writes
    battery_command. See the module docstring."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Changes with every PV update -- not worth a row in the Recorder each time.
    _unrecorded_attributes = frozenset({"target_power"})

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry, hass: HomeAssistant) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = 0.0
        self._attr_extra_state_attributes = {}
        self._entry = entry
        self._state = BatteryHoldState()

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._soc_entity_id = entity_map.get(ENTITY_KEY_BATTERY_SOC) or None
        self._battery_power_entity_id = entity_map.get(ENTITY_KEY_BATTERY_POWER) or None
        self._house_entity_id = find_entity_by_name(
            hass, GROWATT_MODBUS_PLATFORM, GROWATT_MODBUS_HOUSE_CONSUMPTION
        )
        self._house_readings: deque[float] = deque(maxlen=_HOUSE_MEDIAN_READINGS)

    @property
    def extra_restore_state_data(self) -> PwMngtBatteryHoldStoredData:
        return PwMngtBatteryHoldStoredData(self._state)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        restored = await self.async_get_last_extra_data()
        if restored is not None:
            self._state = PwMngtBatteryHoldStoredData.from_dict(restored.as_dict()).state

        self.async_on_remove(
            event.async_track_time_interval(
                self.hass, self._handle_trigger, timedelta(seconds=_RUN_INTERVAL_SECONDS)
            )
        )
        trigger_entity_ids = [
            entity_id
            for entity_id in (
                pv_properties.pv_total_entity_id(self.hass, self._entry),
                pv_properties.battery_data_entity_id(self.hass, self._entry),
                pv_properties.battery_command_entity_id(self.hass, self._entry),
                pv_properties.battery_mode_entity_id(self.hass, self._entry),
            )
            if entity_id
        ]
        if trigger_entity_ids:
            self.async_on_remove(
                event.async_track_state_change_event(self.hass, trigger_entity_ids, self._handle_trigger)
            )
        # Issue #33: follow the car's charging power.
        self.async_on_remove(
            charger_properties.async_track_car_charging(self.hass, self._entry, self._handle_trigger)
        )
        # Issue #42: collect House Consumption readings for the median --
        # no run of its own, the hold keeps its own triggers.
        if self._house_entity_id:
            self._add_house_reading()
            self.async_on_remove(
                event.async_track_state_change_event(
                    self.hass, [self._house_entity_id], self._handle_house_changed
                )
            )
        self._run()

    @callback
    def _handle_house_changed(self, _event) -> None:
        self._add_house_reading()

    def _add_house_reading(self) -> None:
        value = self._read(self._house_entity_id)
        if value is not None:
            self._house_readings.append(value)

    def _house_power(self) -> float | None:
        """Issue #42: the median of the last House Consumption readings,
        or None while there is none."""
        return statistics.median(self._house_readings) if self._house_readings else None

    @callback
    def _handle_trigger(self, _event_or_now=None) -> None:
        self._run()

    @callback
    def _run(self) -> None:
        result = calculate_battery_hold(self._state, self._read_inputs())
        self._state = result.state

        native_value = result.state.power_w if result.active else 0.0
        attributes = {
            "active": result.active,
            "status": result.status,
            "target_power": result.target_power_w,
        }
        if native_value != self._attr_native_value or attributes != self._attr_extra_state_attributes:
            self._attr_native_value = native_value
            self._attr_extra_state_attributes = attributes
            self.async_write_ha_state()

        # Last: the select's state change re-triggers this sensor at once.
        if result.command is not None:
            async_dispatcher_send(
                self.hass, signal_set_battery_command(self._entry.entry_id), result.command
            )

    def _read_inputs(self) -> BatteryHoldInputs:
        battery_power = self._read(self._battery_power_entity_id)
        if battery_power is not None:
            battery_power *= pv_properties.power_sign(self._entry, ENTITY_KEY_BATTERY_POWER)
        command_entity_id = pv_properties.battery_command_entity_id(self.hass, self._entry)
        command_state = self.hass.states.get(command_entity_id) if command_entity_id else None
        return BatteryHoldInputs(
            limit=self._read(pv_properties.battery_data_entity_id(self.hass, self._entry)),
            soc=self._read(self._soc_entity_id),
            pv_power=self._read(pv_properties.pv_total_entity_id(self.hass, self._entry)),
            house_power=self._house_power(),
            battery_power=battery_power,
            battery_command=(
                command_state.state
                if command_state is not None and command_state.state not in ("unknown", "unavailable")
                else None
            ),
            battery_mode_manual=pv_properties.is_battery_mode_manual(self.hass, self._entry),
            now=dt_util.utcnow().timestamp(),
            winter=dt_util.now().month in _WINTER_MONTHS,
            pv_big_shifts=self._read_pv_big_shifts(),
            **self._read_car_charging(),
        )

    def _read_car_charging(self) -> dict[str, Any]:
        """Issue #33: car_charging/car_power_w/behind_charge_curve for
        BatteryHoldInputs."""
        car = charger_properties.car_charging(self.hass, self._entry)
        entity_id = pv_properties.battery_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        return {
            "car_charging": car.charging,
            "car_power_w": car.power_w,
            "behind_charge_curve": bool(state and state.attributes.get("behind_charge_curve")),
        }

    def _read_pv_big_shifts(self) -> int:
        """solar_data's pv_big_shifts_last_60min -- 0 (the normal wait)
        while it isn't there."""
        entity_id = pv_properties.solar_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        try:
            return int(state.attributes.get(KEY_PV_BIG_SHIFTS_LAST_60MIN) or 0) if state else 0
        except (TypeError, ValueError):
            return 0

    def _read(self, entity_id: str | None) -> float | None:
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None
