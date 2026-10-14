"""Port of Node-RED's "Antal store skift i PV produktion den seneste time"
function node (tab "Ha data - opsætning"). Counts large jumps
(>= _BIG_SHIFT_THRESHOLD_W) in sensor.pv_total within the last 60
minutes, with a reset after a sustained near-zero-production stretch
("night").

    Node-RED sensor              PwMngt
    sensor.udsving_60min         solar_data: pv_big_shifts_last_60min

A "shift" is a fall of at least 1800 W confirmed over (at least) two
consecutive samples below the previous plateau, followed later by a rise
of at least 1800 W back up -- e.g. clouds passing over, not just normal
minute-to-minute noise. Only completed (fall-then-rise) shifts count;
one still waiting for its rise isn't counted yet.

Node-RED's trigger: a state-changed listener on sensor.pv_total, gated to
only fire while production is > 0, rate-limited to roughly once every 10
seconds (plus 1-5s jitter) with excess messages dropped in between.
PwMngt instead samples pv_total on a plain 10-second timer (see
PwMngtSolarDataCoordinator._sample_pv_shift in data/solar_data.py) -- the
same cadence, but it also runs at exactly 0 W, which Node-RED's ">0" gate
never did. DIFF: this means the 30-minute night-reset timeout can
actually fire from a flat 0 W reading, not just a lingering 1-99 W one --
a strict improvement, not a behavior PwMngt depends on elsewhere.

DIFF: the shift history (PvShiftState) isn't persisted across a Home
Assistant restart, unlike data/balance_data.py's rolling samples --
pv_big_shifts_last_60min just starts back at 0 and rebuilds over the
next hour. It's a diagnostic count, not an input to any charge decision,
so that's an acceptable loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

_BIG_SHIFT_THRESHOLD_W = 1800.0
_NIGHT_PRODUCTION_THRESHOLD_W = 100.0
_NIGHT_RESET_TIMEOUT = timedelta(minutes=30)
_SHIFT_RECORD_MAX_AGE = timedelta(minutes=60)
_FALLS_BEFORE_SHIFT = 2  # Node-RED: fallCount >= 2


@dataclass
class _ShiftRecord:
    timestamp: datetime
    before_fall: float
    after_fall: float
    after_rise: float | None = None


@dataclass
class PvShiftState:
    """Everything step_pv_shift_detection() carries from one sample to the
    next -- the Node-RED function node's context.get/set variables,
    bundled into one value instead of free-floating flow-context keys."""

    last_production: float | None = None
    shift_records: list[_ShiftRecord] = field(default_factory=list)
    in_low_production: bool = False
    fall_start_production: float | None = None
    fall_count: int = 0
    current_shift: _ShiftRecord | None = None
    low_production_start_time: datetime | None = None


def step_pv_shift_detection(
    state: PvShiftState, current_production: float, now: datetime
) -> tuple[PvShiftState, int]:
    """One sample tick: feed it the latest PV total power and the current
    time, get back the updated state plus the number of completed big
    shifts still within the last 60 minutes (Node-RED's
    payload.bigShiftsLast60Min)."""
    last_production = (
        state.last_production
        if state.last_production is not None
        else current_production
    )
    shift_records = list(state.shift_records)
    in_low_production = state.in_low_production
    fall_start_production = state.fall_start_production
    fall_count = state.fall_count
    current_shift = state.current_shift
    low_production_start_time = state.low_production_start_time

    delta = current_production - last_production

    # Night handling: a sustained low-production stretch resets
    # everything, so a new day doesn't inherit yesterday's half-finished
    # shift or stale records.
    if current_production <= _NIGHT_PRODUCTION_THRESHOLD_W:
        if low_production_start_time is None:
            low_production_start_time = now
        elif now - low_production_start_time > _NIGHT_RESET_TIMEOUT:
            in_low_production = False
            fall_count = 0
            fall_start_production = None
            shift_records = []
            current_shift = None
            low_production_start_time = None
    else:
        low_production_start_time = None

    if not in_low_production:
        if fall_start_production is None:
            fall_start_production = last_production

        if (fall_start_production - current_production) >= _BIG_SHIFT_THRESHOLD_W:
            fall_count += 1
            if fall_count >= _FALLS_BEFORE_SHIFT:
                current_shift = _ShiftRecord(
                    timestamp=now,
                    before_fall=fall_start_production,
                    after_fall=current_production,
                )
                in_low_production = True
                fall_count = 0
                fall_start_production = None
        else:
            fall_count = 0
            fall_start_production = current_production
    else:
        # Waiting for the rise back up that completes the shift.
        if delta >= _BIG_SHIFT_THRESHOLD_W:
            if current_shift is not None:
                current_shift.after_rise = current_production
                shift_records.append(current_shift)
                current_shift = None
            in_low_production = False

    shift_records = [
        record
        for record in shift_records
        if now - record.timestamp <= _SHIFT_RECORD_MAX_AGE
    ]

    new_state = PvShiftState(
        last_production=current_production,
        shift_records=shift_records,
        in_low_production=in_low_production,
        fall_start_production=fall_start_production,
        fall_count=fall_count,
        current_shift=current_shift,
        low_production_start_time=low_production_start_time,
    )
    return new_state, len(shift_records)
