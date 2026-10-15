"""Battery sell decision: writes select.battery_command directly to
block the battery from charging during the morning, so PV surplus gets
sold to the grid instead of going into the battery ("block_charging"),
and releases it again once that no longer makes sense ("internal").

Port of Claus's Node-RED function "Beregning ny" (tab "Aktivering /
deaktivering af salg af strøm"), plus the two small state-changed nodes
wired directly around it for the override boolean (see below). Ported
from the flow export Claus supplied 2026-10-15. Not an entity -- same
role as data/battery_charge_level.py: it only writes to battery_command
(select.py) and signals nothing else. Claus confirmed (2026-10-15) that
Node-RED's own version of this function never persisted any diagnostic
attributes either, so this port carries none.

Only the generic, installation-independent part of that flow is ported.
Four Lundekrog-specific pieces, agreed with Claus (2026-10-15), are
deliberately left out:
- The "museum" seasonal time-window variant (Node-RED: tidsvindueOK's
  `museum_totaleffekt !== null` branch, open May-September on weekdays,
  closed otherwise) -- tied to Claus's own museum tenant and its opening
  hours (his museum's meter sits electrically upstream of his smart
  meter, so PV can only reach it while the plant is actively "selling").
  This port always takes the "standard" branch Node-RED itself falls
  back to when museum_totaleffekt is unset -- exactly what every other
  installation already gets today.
- The "prioriter_stuehus" output (Node-RED's third function output,
  turning off input_boolean.prioriter_lejere when price is low and the
  battery is already blocked) -- not ported, for the same reason
  data/battery_charge_decision.py already excludes "prioriter_lejere"
  entirely: it's about Claus's own tenant-priority automation, not this
  decision.
- The "Tvangsladning aktiv" -> turn off prioriter_lejere node -- a rule
  about how Claus's own override boolean behaves, not about this sell
  decision. Stays in his own Node-RED, outside PwMngt.
- HOEJPRIS_GENVEJ and el_kobspris (the "high buy-price shortcut") --
  permanently disabled (HOEJPRIS_GENVEJ = false) in Claus's own export,
  and provably dead with it false: every branch behind it is
  unreachable, so (same policy data/battery_data.py's own docstring
  states for inputs that never affect the result) it isn't ported, and
  el_kobspris is never read. Revisit if Claus ever actually wants that
  toggle on.

Also, by Claus's own call (2026-10-15): both PRIS_GULV and
MIN_PRISFORSKEL are hard-coded (_PRICE_FLOOR/_MIN_PRICE_DIFFERENCE
below), not user-configurable selects -- same reasoning as the other
fixed constants below them (_SELL_WINDOW_END_HOUR, _CONSUMPTION_HOURS,
_SAFETY), none of which are configurable either. Node-RED's own version
of "Beregning ny" never persisted these as adjustable entities, so this
port doesn't introduce that either.

Generalized beyond Claus's own install: ENTITY_KEY_SELL_FORCE_OVERRIDE
(const.py) -- an optional boolean entity any installation's own
external automation can point PwMngt at to force selling mode right
now (Claus plugs his own input_boolean.prioriter_lejere into it;
Rene/Kasper leave it unmapped and get identical behaviour to not having
it at all). See const.py's own docstring for the override's exact
(one-way-only) semantics: it can only force the battery into selling
mode, never force it out of selling mode.

Node-RED's own trigger graph for "Beregning ny" (traced from the flow
export, not guessed):
- Every 30 min, 4:00-20:30 (inject "Kør hver halve time", crontab
  "*/30 4-20 * * *") -> a fixed 30 s delay (randomFirst/randomLast are
  inert under pauseType "delay", same as data/battery_charge_level.py's
  own identical note) -> run.
- PV forecast today changing, while local time is 05:00-21:00 (Node-RED:
  "Forventet produktion" state-changed -> time-range-switch "5-21") ->
  the same fixed 30 s delay -> run. DIFF: both of Node-RED's triggers
  above feed the very same delay node, so two near-simultaneous triggers
  there each get their own independent 30 s timer and both actually run
  the (deterministic, side-effect-only-via-dispatch) function again a
  moment apart. This port coalesces overlapping triggers into a single
  run instead (same "merge triggers in between into one run" policy
  data/battery_data.py's own docstring already applies elsewhere) --
  cheaper, and no different in effect since the computation is pure.
- The override boolean turning *off* (any state other than "on") -> run
  immediately, no delay (Node-RED: "Lejere prioriteres on/off" output 2)
  -- so a release isn't stuck waiting for the next scheduled tick.
- The override boolean turning *on* -> NOT a run of the full decision --
  Node-RED wires this straight to its own small "force block_charging
  unless already block_battery" check instead (see
  _handle_override_turned_on below), independent of whatever the price/
  forecast calculation would currently say.
- (Node-RED also has a bare inject with no crontab and once=false --
  never fires on its own, a manual debug button; not ported, same
  treatment other unported manual-only Node-RED nodes get elsewhere.)

Also a DIFF with no Node-RED equivalent at all: while select.<pv>_
battery_mode is "manual", this module does nothing, same as data/
battery_charge_level.py and data/battery_charge_decision.py already do
(Claus, 2026-10-14: "manual" means no automation should touch battery_
command, not just the charge/no-charge ones that request happened to be
about).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import event
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    DEPENDENCY_STROMLIGNING,
    ENTITY_KEY_BATTERY_SOC,
    ENTITY_KEY_PV_FORECAST_TODAY,
    ENTITY_KEY_SELL_FORCE_OVERRIDE,
    ENTITY_KEY_SPOT_ELECTRICITY_PRICE,
)
from ..helpers.state_helper import read_float_state
from ..properties import pv_properties
from ..select import signal_set_battery_command
from .solar_data import sum_pv_forecast

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants -- names/defaults match Claus's Node-RED settings block 1:1,
# except where his own 2026-10-15 call made one fixed instead of
# user-configurable (see module docstring).
# ---------------------------------------------------------------------------

_SELL_WINDOW_END_HOUR = 12      # Node-RED: SALGSVINDUE_SLUT.
_WINDOW_START_HOUR = 6          # Node-RED: iVinduet's own "hour >= 6".
_CONSUMPTION_HOURS = 8          # Node-RED: FORBRUG_TIMER (the 8-16 window).
_SAFETY = 0.6                   # Node-RED: SIKKERHED.
_PRICE_FLOOR = 0.15             # Node-RED: PRIS_GULV. Hard-coded (Claus, 2026-10-15).
_MIN_PRICE_DIFFERENCE = 0.30    # Node-RED: MIN_PRISFORSKEL. Hard-coded (Claus, 2026-10-15).
_BATTERY_MAX_CHARGE_FALLBACK = 3000.0  # W, before the 0.75 utilization.
_CHARGE_POWER_UTILIZATION = 0.75       # Node-RED comment: "max ladeeffekt sat til 75%".
_CHARGETIME_SAFETY_MARGIN = 1.10
_DEFAULT_TARGET_END_TIME = "16:45"      # Node-RED: batteri_target_sluttid || "16:45".

_COMMAND_INTERNAL = "internal"           # Old: "Intern".
_COMMAND_BLOCK_BATTERY = "block_battery"  # Old: "Bloker batteri".
_COMMAND_BLOCK_CHARGING = "block_charging"  # Old: "Bloker ladning".
_BLOCKED_COMMANDS = frozenset({_COMMAND_BLOCK_BATTERY, _COMMAND_BLOCK_CHARGING})
# battery_command options meaning tonight's grid charge
# (data/battery_charge_level.py) is actively running -- Node-RED's own
# top guard returns null outright while any of these is set.
_ACTIVE_CHARGE_COMMANDS = frozenset(
    {"charge_now_25", "charge_now_50", "charge_now_75", "charge_now_100"}
)

# Best-effort guess, NOT verified against a live Strømligning install --
# same caveat as data/battery_charge_decision.py's own identical note on
# _STROMLIGNING_TOMORROW_TRANSLATION_KEY. Modelled the same way that one
# was: the confirmed "current_price_vat" translation_key matches its
# entity_id's own tail 1:1, so sensor.stromligning_spotprice_ex_vat is
# assumed to carry translation_key "spotprice_ex_vat". Confirm against
# the entity registry on a real install and correct if wrong -- a wrong
# guess here is harmless, auto-detect just finds zero matches (see
# async_start's own warning).
_STROMLIGNING_SPOTPRICE_EX_VAT_TRANSLATION_KEY = "spotprice_ex_vat"

# Node-RED's fixed 30 s delay (both triggers feed the same delay node;
# see module docstring on why overlapping triggers are coalesced here).
_TRIGGER_DELAY_SECONDS = 30
# Node-RED: crontab "*/30 4-20 * * *" -- hours 4 through 20 inclusive, at
# :00 and :30.
_TRIGGER_HOURS = list(range(4, 21))
_TRIGGER_MINUTES = [0, 30]
# Node-RED: time-range-switch "5-21" gating the PV-forecast trigger.
_FORECAST_TRIGGER_WINDOW_START = (5, 0)
_FORECAST_TRIGGER_WINDOW_END = (21, 0)

# Price-list attribute names Node-RED tried, in order, before falling
# back to "the first list-shaped attribute that looks like one".
_PRICE_ATTR_CANDIDATES = ("prices", "raw_today", "forecast", "today")


def _auto_detect_spotprice_ex_vat_entity_id(hass: HomeAssistant) -> str | None:
    """Find the single Strømligning entity exposing the ex-VAT spot
    price (Node-RED: sensor.stromligning_spotprice_ex_vat, "spotpris").
    Only ever auto-detected -- there's no Options wizard field for this,
    see the module docstring. None unless exactly one match exists, same
    reasoning as data/battery_charge_decision.py's own
    _auto_detect_tomorrow_prices_entity_id."""
    registry = er.async_get(hass)
    matches = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DEPENDENCY_STROMLIGNING
        and entry.translation_key == _STROMLIGNING_SPOTPRICE_EX_VAT_TRANSLATION_KEY
    ]
    return matches[0] if len(matches) == 1 else None


def _parse_today_hourly_prices(attributes: dict[str, Any], today: str) -> dict[int, float]:
    """Node-RED's own flexible lookup on the "current_price_vat" entity's
    attributes: try a few known attribute names, else fall back to the
    first list-shaped attribute whose entries look like {"price": ...,
    "start": ...}. Entries are filtered to `today` (Node-RED:
    p.start.substring(0, 10) === idag) -- an attribute that also carries
    tomorrow's hours would otherwise double-book an hour number across
    two different days."""
    price_list = None
    for name in _PRICE_ATTR_CANDIDATES:
        value = attributes.get(name)
        if isinstance(value, list):
            price_list = value
            break
    if price_list is None:
        for value in attributes.values():
            if (
                isinstance(value, list)
                and value
                and isinstance(value[0], dict)
                and "price" in value[0]
                and "start" in value[0]
            ):
                price_list = value
                break
    if not isinstance(price_list, list):
        return {}

    prices: dict[int, float] = {}
    for item in price_list:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        if start is None:
            continue
        start_str = str(start)
        if start_str[:10] != today:
            continue
        try:
            hour = int(start_str[11:13])
            prices[hour] = float(item["price"])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    return prices


# ---------------------------------------------------------------------------
# Pure calculations -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


@dataclass
class BatterySellInputs:
    """Everything one run reads from Home Assistant, already resolved to
    plain values (Node-RED's own `|| default` fallback already applied
    by the caller for each field -- see the controller's _read_* methods
    for which default belongs to which field). battery_target is the one
    exception: None here means "not computed yet", a deliberate DIFF from
    Node-RED's literal `|| 0` -- see
    PwMngtBatterySellDecisionController._run for why."""

    battery_command: str
    hour: int
    today: str  # YYYY-MM-DD, local. Node-RED: idag.
    day_consumption_8_16: float
    battery_size: float
    battery_soc: float
    spotprice_ex_vat: float
    override_active: bool  # Node-RED: lejere == "on".
    battery_target_end_time: str  # "13:30" or "16:45".
    battery_max_charge_raw: float | None  # W, before the 0.75 utilization.
    battery_target: float  # % -- the caller already handled "not ready yet".
    today_hourly_forecast: list[dict[str, Any]]  # Solcast detailedHourly.
    today_prices_vat: dict[int, float]  # hour -> kr/kWh, already parsed.


def calculate_battery_sell_decision(inputs: BatterySellInputs) -> str | None:
    """One run of Node-RED's "Beregning ny", minus the Lundekrog-specific
    branches -- see the module docstring. Returns the battery_command
    option to apply ("block_charging"/"internal"), or None when Node-RED
    itself returns null and produces no output at all -- the caller must
    then leave battery_command exactly as it was."""

    if inputs.battery_command in _ACTIVE_CHARGE_COMMANDS:
        # Node-RED's own top guard.
        return None

    slut_hour = 14 if inputs.battery_target_end_time == "13:30" else 17

    max_charge_raw = inputs.battery_max_charge_raw
    if not max_charge_raw:  # None or 0 -- Node-RED: Number(parseFloat(...) || 3000).
        max_charge_raw = _BATTERY_MAX_CHARGE_FALLBACK
    battery_max_charge = max_charge_raw * _CHARGE_POWER_UTILIZATION

    battery_target_missing = inputs.battery_target - inputs.battery_soc
    battery_kwh_missing = max(0.0, (inputs.battery_size * battery_target_missing) / 100)
    chargetime_needed_missing = (
        math.ceil((battery_kwh_missing * 1000) / battery_max_charge * _CHARGETIME_SAFETY_MARGIN)
        if battery_max_charge
        else 0
    )

    forecast_ok = len(inputs.today_hourly_forecast) > 0

    def sum_period(key: str, start_hour: int, end_hour: int) -> float:
        if not forecast_ok:
            return 0.0
        return sum_pv_forecast(inputs.today_hourly_forecast, start_hour, end_hour, key)

    weighted_sum = 0.0
    weight = 0.0
    for hour in range(_SELL_WINDOW_END_HOUR, slut_hour):
        hour_weight = sum_period("pv_estimate", hour, hour + 1)
        price = inputs.today_prices_vat.get(hour)
        if price is not None and hour_weight > 0:
            weighted_sum += price * hour_weight
            weight += hour_weight
    afternoon_price = weighted_sum / weight if weight > 0 else None
    now_price = inputs.today_prices_vat.get(inputs.hour)

    price_difference = (
        now_price - afternoon_price
        if now_price is not None and afternoon_price is not None
        else None
    )
    price_attractive = (
        inputs.spotprice_ex_vat > _PRICE_FLOOR
        and price_difference is not None
        and price_difference >= _MIN_PRICE_DIFFERENCE
    )

    free_capacity = inputs.battery_size * (1 - inputs.battery_soc / 100)
    hours_after = max(0, slut_hour - _SELL_WINDOW_END_HOUR)
    prod_afternoon_median = sum_period("pv_estimate", _SELL_WINDOW_END_HOUR, slut_hour)
    prod_afternoon_p10 = sum_period("pv_estimate10", _SELL_WINDOW_END_HOUR, slut_hour)
    consumption_afternoon = (inputs.day_consumption_8_16 / _CONSUMPTION_HOURS) * hours_after
    net_median = prod_afternoon_median - consumption_afternoon
    prod_afternoon_safe = prod_afternoon_p10 + _SAFETY * (prod_afternoon_median - prod_afternoon_p10)
    net_safe = prod_afternoon_safe - consumption_afternoon

    # Krav 1: target nås selv i et dårligt scenarie og når at lade i tide.
    reaches_target = (
        forecast_ok
        and net_safe >= battery_kwh_missing
        and chargetime_needed_missing <= hours_after
    )
    # Krav 3+4: batteriet bliver alligevel fyldt helt i eftermiddag (median).
    fills_anyway = forecast_ok and net_median >= free_capacity
    surplus_ok = reaches_target and fills_anyway and free_capacity > 0

    # Node-RED's museum branch is never reached here -- always the
    # "standard" fallback (museum_totaleffekt === null). See module
    # docstring.
    in_window = _WINDOW_START_HOUR <= inputs.hour < _SELL_WINDOW_END_HOUR

    sell_basis = surplus_ok and in_window

    would_sell = False
    if sell_basis:
        if (
            inputs.spotprice_ex_vat <= _PRICE_FLOOR
            and inputs.battery_command in _BLOCKED_COMMANDS
        ):
            # Node-RED: this branch only ever set prioriter_stuehus here
            # (del 3, excluded) -- would_sell stays False.
            pass
        elif price_attractive:
            would_sell = True
    # else: Node-RED's own else-branch only had the excluded HOEJPRIS_
    # GENVEJ shortcut -- would_sell stays False.

    if would_sell and inputs.battery_command not in _BLOCKED_COMMANDS:
        return _COMMAND_BLOCK_CHARGING
    if (
        not would_sell
        and inputs.battery_command in _BLOCKED_COMMANDS
        and not inputs.override_active
        and inputs.hour >= _WINDOW_START_HOUR
    ):
        return _COMMAND_INTERNAL
    # Node-RED's third output (re-checking for an active "Lad nu" command
    # here) is dead code -- the top guard above already returns None
    # whenever battery_command is one of those, so it can never still be
    # one by this point. Not ported, see module docstring's trigger-graph
    # note.
    return None


# ---------------------------------------------------------------------------
# Home Assistant wiring. Not an Entity -- nothing displays this, it only
# ever writes to battery_command (select.py). Same role as
# PwMngtBatteryChargeLevelController in data/battery_charge_level.py:
# started/stopped from sensor.py's async_setup_entry via
# entry.async_on_unload().
# ---------------------------------------------------------------------------


class PwMngtBatterySellDecisionController:
    """Runs calculate_battery_sell_decision() on the Node-RED schedule
    and writes select.battery_command. See the module docstring for the
    exact trigger graph and the override boolean's two separate code
    paths."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._soc_entity_id = entity_map.get(ENTITY_KEY_BATTERY_SOC) or None
        self._forecast_today_entity_id = entity_map.get(ENTITY_KEY_PV_FORECAST_TODAY) or None
        self._price_entity_id = entity_map.get(ENTITY_KEY_SPOT_ELECTRICITY_PRICE) or None
        self._override_entity_id = entity_map.get(ENTITY_KEY_SELL_FORCE_OVERRIDE) or None
        # Resolved in async_start, once the entity registry is actually
        # populated -- see _auto_detect_spotprice_ex_vat_entity_id.
        self._spotprice_ex_vat_entity_id: str | None = None

        self._unsubscribers: list = []
        self._pending_trigger_run: Any = None

        if not self._soc_entity_id or not self._forecast_today_entity_id or not self._price_entity_id:
            LOGGER.warning(
                "PwMngt: Battery SoC, PV forecast today, or Spot "
                "electricity price not configured (Options -> Solar PV "
                "Plant) -- data/battery_sell_decision.py will never run"
            )

    @callback
    def async_start(self) -> None:
        """Subscribe to Node-RED's triggers (see module docstring)."""
        self._unsubscribers.append(
            event.async_track_time_change(
                self.hass,
                self._handle_periodic_trigger,
                hour=_TRIGGER_HOURS,
                minute=_TRIGGER_MINUTES,
                second=0,
            )
        )
        if self._forecast_today_entity_id:
            self._unsubscribers.append(
                event.async_track_state_change_event(
                    self.hass,
                    [self._forecast_today_entity_id],
                    self._handle_forecast_trigger,
                )
            )
        if self._override_entity_id:
            self._unsubscribers.append(
                event.async_track_state_change_event(
                    self.hass,
                    [self._override_entity_id],
                    self._handle_override_state_change,
                )
            )

        spotprice_entity_id = _auto_detect_spotprice_ex_vat_entity_id(self.hass)
        if not spotprice_entity_id:
            LOGGER.warning(
                "PwMngt: could not find the Strømligning ex-VAT spot "
                "price entity (looked for translation_key '%s') -- the "
                "sell decision will treat the spot price as 0, which "
                "keeps the price floor check from ever passing",
                _STROMLIGNING_SPOTPRICE_EX_VAT_TRANSLATION_KEY,
            )
        self._spotprice_ex_vat_entity_id = spotprice_entity_id

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
        if self._pending_trigger_run is not None:
            self._pending_trigger_run()
            self._pending_trigger_run = None

    # -- triggers ----------------------------------------------------------

    @callback
    def _handle_periodic_trigger(self, _now=None) -> None:
        self._schedule_delayed_run()

    @callback
    def _handle_forecast_trigger(self, _state_event) -> None:
        if not self._in_forecast_trigger_window():
            return
        self._schedule_delayed_run()

    def _in_forecast_trigger_window(self) -> bool:
        now = dt_util.now()
        return (
            _FORECAST_TRIGGER_WINDOW_START
            <= (now.hour, now.minute)
            <= _FORECAST_TRIGGER_WINDOW_END
        )

    def _schedule_delayed_run(self) -> None:
        # Coalesce overlapping triggers into one run -- see module
        # docstring.
        if self._pending_trigger_run is not None:
            self._pending_trigger_run()
        self._pending_trigger_run = event.async_call_later(
            self.hass, _TRIGGER_DELAY_SECONDS, self._handle_delayed_run
        )

    @callback
    def _handle_delayed_run(self, _now=None) -> None:
        self._pending_trigger_run = None
        self._run()

    @callback
    def _handle_override_state_change(self, state_event) -> None:
        old_state = state_event.data.get("old_state")
        new_state = state_event.data.get("new_state")
        was_on = old_state is not None and old_state.state == "on"
        is_on = new_state is not None and new_state.state == "on"
        if is_on and not was_on:
            self._handle_override_turned_on()
        elif was_on and not is_on:
            # Node-RED: "Lejere prioriteres on/off" output 2 -- re-run
            # immediately rather than waiting for the next scheduled
            # tick, so a release isn't stuck.
            self._run()

    @callback
    def _handle_override_turned_on(self) -> None:
        """Node-RED: "Lejere prioriteres on/off" output 1 -> "Batteri
        status" (get) -> "Aktivt?" (switch, payload != "Bloker batteri")
        -> "Batteri => Bloker ladning". Forces selling mode immediately,
        independent of the price/forecast calculation -- see const.py's
        own docstring on ENTITY_KEY_SELL_FORCE_OVERRIDE for why this is
        one-way-only."""
        if self._read_battery_command() == _COMMAND_BLOCK_BATTERY:
            # Node-RED's own switch excludes only this one state -- it
            # still re-sends "block_charging" even when already
            # "block_charging" (harmless).
            return
        self._send_battery_command(_COMMAND_BLOCK_CHARGING)

    # -- runs ----------------------------------------------------------------

    def _run(self) -> None:
        if pv_properties.is_battery_mode_manual(self.hass, self._entry):
            # Not part of Node-RED's own "Beregning ny" wiring -- see
            # module docstring's note on this deliberate addition.
            return

        battery_target = self._read_target()
        if battery_target is None:
            # Deliberate DIFF from Node-RED's own `parseFloat(...) || 0`
            # fallback -- same reasoning, and the same bug class, as
            # data/battery_charge_level.py's own _read_target() docstring
            # (fixed 2026-10-14): reading "not computed yet" as a literal
            # 0 right after a restart would wrongly conclude there's no
            # deficit left to protect, rather than waiting for a real
            # value.
            return

        hour = dt_util.now().hour
        today = dt_util.now().strftime("%Y-%m-%d")

        price_state = (
            self.hass.states.get(self._price_entity_id) if self._price_entity_id else None
        )
        today_prices = (
            _parse_today_hourly_prices(price_state.attributes, today)
            if price_state is not None
            else {}
        )

        forecast_state = (
            self.hass.states.get(self._forecast_today_entity_id)
            if self._forecast_today_entity_id
            else None
        )
        detailed_hourly = (
            forecast_state.attributes.get("detailedHourly") if forecast_state is not None else None
        )
        if not isinstance(detailed_hourly, list):
            detailed_hourly = []

        inputs = BatterySellInputs(
            battery_command=self._read_battery_command(),
            hour=hour,
            today=today,
            day_consumption_8_16=self._read_day_consumption(),
            battery_size=self._read_battery_size(),
            battery_soc=self._read_soc(),
            spotprice_ex_vat=self._read_spotprice_ex_vat(),
            override_active=self._read_override_active(),
            battery_target_end_time=self._read_target_end_time(),
            battery_max_charge_raw=self._read_battery_max_charge(),
            battery_target=battery_target,
            today_hourly_forecast=detailed_hourly,
            today_prices_vat=today_prices,
        )

        result = calculate_battery_sell_decision(inputs)
        if result is None:
            return
        self._send_battery_command(result)

    def _send_battery_command(self, option: str) -> None:
        async_dispatcher_send(
            self.hass, signal_set_battery_command(self._entry.entry_id), option
        )

    # -- input readers (Node-RED fallback when missing/unknown) ----------

    def _read_target(self) -> float | None:
        entity_id = pv_properties.battery_nightly_target_entity_id(self.hass, self._entry)
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None

    def _read_battery_command(self) -> str:
        entity_id = pv_properties.battery_command_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in ("unknown", "unavailable"):
            return _COMMAND_INTERNAL
        return state.state

    def _read_day_consumption(self) -> float:
        entity_id = pv_properties.consumption_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return 0.0
        try:
            value = state.attributes.get("daytime_average_adjusted")
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _read_battery_size(self) -> float:
        entity_id = pv_properties.battery_size_entity_id(self.hass, self._entry)
        value = read_float_state(self.hass.states.get(entity_id)) if entity_id else None
        return value or 0.0

    def _read_soc(self) -> float:
        value = (
            read_float_state(self.hass.states.get(self._soc_entity_id))
            if self._soc_entity_id
            else None
        )
        return value or 0.0

    def _read_spotprice_ex_vat(self) -> float:
        value = (
            read_float_state(self.hass.states.get(self._spotprice_ex_vat_entity_id))
            if self._spotprice_ex_vat_entity_id
            else None
        )
        return value or 0.0

    def _read_override_active(self) -> bool:
        if not self._override_entity_id:
            return False
        state = self.hass.states.get(self._override_entity_id)
        return state is not None and state.state == "on"

    def _read_target_end_time(self) -> str:
        entity_id = pv_properties.battery_target_end_time_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in ("unknown", "unavailable", ""):
            return _DEFAULT_TARGET_END_TIME
        return state.state

    def _read_battery_max_charge(self) -> float | None:
        entity_id = pv_properties.battery_max_charge_effect_entity_id(self.hass, self._entry)
        return read_float_state(self.hass.states.get(entity_id)) if entity_id else None
