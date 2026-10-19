"""Savings data: running totals (DKK) of money saved from having PV/
battery, plus the hourly purchase-price tracking that feeds them.

Port of three of Claus's Node-RED functions (tab "Ha data - opsætning"):
"gem sidste times priser" (unnamed node, inferred from its code -- shifts
"this hour"'s prices into "last hour" so the two functions below always
price a hour's energy at the rate that was actually in effect during it),
"Beregn indkøbspris med og uden PV", and "Beregn balance på ladning af
batteri fra grid". Only these three -- "Beregn forbrugspris museum &
andre lejere", "Beregn forbrugspris jordvarme" and "Beregn tabt fortjeneste
på PV mining" are Lundekrog-specific (museum tenant, geothermal heat pump,
mining rig) and stay in Claus's own Node-RED, same call as
prioriter_lejere and the sell-override boolean before it (Claus,
2026-10-15).

Entirely optional: every entity_map field this reads (ENTITY_KEY_
ELECTRICITY_TAX_FULL_RATE, the four hourly ENTITY_KEY_*_HOURLY counters)
is Claus's own sensor setup, not derivable from anything else PwMngt has.
Left unmapped, the affected running totals simply never update (see
_handle_accumulate()). The "real" price is ENTITY_KEY_SPOT_ELECTRICITY_PRICE
(already used elsewhere) -- Claus, 2026-10-15: "det er det samme reelt
set" -- no separate field for it -- read via hub_properties.
electricity_price(), which adds Claus's configured kr/kWh surcharge on
top (added the same day, see that function's own docstring): his
supplier's markup isn't something the configured price sensor itself
knows about.

Reworked (Claus, 2026-10-16): "uden PV" no longer means "read a different
price sensor". The underlying commodity/spot price is the SAME with or
without PV -- the market price doesn't know or care how much solar you
have. The only real difference is electricity TAX (elafgift): without
PV, every kWh of household_consumption would have been bought from the
grid, so the household would cross Denmark's tax threshold faster than
it actually does (PV covers part of consumption, so actual grid_import is
lower). So purchase_price_real_without_pv is now: household_consumption
priced at the same real/surcharge-inclusive rate as purchase_price_real,
PLUS the electricity tax that hypothetical all-grid consumption would
have incurred, split across the full/reduced tax tiers using a running
monthly-or-quarterly (per select.<hub>_billing_period) cumulative of
household_consumption -- see hub_properties.billing_period_threshold_kwh()/
electricity_tax_reduced_enabled() and _tax_period_id()/
calculate_purchase_prices() below. The full tax rate comes from Claus's
own sensor (ENTITY_KEY_ELECTRICITY_TAX_FULL_RATE, Strømligning); the
reduced rate is hardcoded (_REDUCED_ELECTRICITY_TAX_RATE) -- Claus says
it's always exactly that figure, not something to configure per install.
No tax tier is currently in effect in Denmark (everyone pays the reduced
rate regardless of volume as of 2026), but Claus expects the full-rate
tier to return 2028-01-01, hence building this now rather than when it
actually starts mattering.

Reworked again (Claus, 2026-10-16, same day): the four ENTITY_KEY_*_
HOURLY fields no longer point at a Home Assistant Utility Meter helper
read through its "last_period" attribute -- they now point straight at
the underlying raw, ever-increasing lifetime counter, and this module
derives each hour's own delta itself (see read_counter_delta()/
_read_counter()). Point: a Utility Meter helper is one more thing Claus
has to rebuild by hand whenever he swaps hardware (e.g. a new PV
inverter) that changes which entity the lifetime counter lives on.
Reading the raw counter directly needs its own reset/entity-swap
handling in return, which read_counter_delta() provides -- mirroring
data/consumption_data.py's own accumulate() helper (same-entity meter
reset) and its source_signature rebuild-on-swap logic (entity_map
repoint), the two places that pattern was already solved in this
codebase.

Dropped (Claus, 2026-10-15, same day): the "variable" purchase price and
price_agreement_savings (what a fixed-price agreement would have cost vs.
the variable price). Claus's own el_kobspris_variabel sensor turned out
to just be the same number as the real/surcharge-inclusive price above,
not a genuinely separate fixed-price-agreement figure -- so the
comparison it fed had nothing real to compare against. Revisit if Claus
ever wires up an actual fixed-price-agreement sensor; own_production_
savings and battery_charging_savings never depended on this at all, so
dropping it doesn't touch either.

Seeding from Node-RED (ENTITY_KEY_SEED_*, const.py): each of the 4 seeded
running totals (purchase_price_real, purchase_price_real_without_pv,
battery_grid_charge_sum, battery_grid_discharge_sum) optionally starts
from the current value of Claus's matching Node-RED sensor instead of 0,
so years of his own tracked savings aren't silently lost when PwMngt
takes over. own_production_savings/battery_charging_savings have no seed
field of their own -- they're derived from the 4 above instead (see next
paragraph).

Reworked (Claus, 2026-10-16, yet again the same day): _seed_ledger() used
to run only on this module's very first run ever (no restored PwMngt
state yet) -- the idea being that once PwMngt's own RestoreEntity-
persisted state exists, it's authoritative and the seed fields should
never be consulted again. In practice this backfired: the entity's first
run is whenever it's first *added*, which can happen before Claus has
even filled in the seed entity_map fields in the wizard -- that first,
seed-less run permanently "used up" the one-time seed opportunity, so
filling the seed fields in afterwards and re-running the wizard had no
effect at all, silently. Fixed by instead running _seed_ledger() on
*every* async_added_to_hass, with each field only ever pulled UP, never
down (see _seed_ledger()'s own docstring) -- so it's safe to re-run as
often as the wizard is, and a late or corrected seed sensor still takes
effect instead of being permanently locked out.

Why all 4 base sums (not just the two headline ones) have to be seeded
together: own_production_savings and battery_charging_savings are both a
running *high-water mark* of (bigger sum - smaller sum) -- lifetime
totals, not deltas. Seeding only the headline number while its underlying
sums restart at 0 would freeze it at the seeded value for months, until
the fresh sums grew large enough on their own to overtake it (Claus,
2026-10-15 design discussion).

Not ported (confirmed dead in the exported source): "Beregn indkøbspris
med og uden PV" reads select.pv_batteri_stromkilde into `stromkilde`, and
a whole soc_state/context-restore block (`saved`/`ha_attrs`/`har_saved`/
`src`/`har_gyldig_tidligere_tilstand`) -- neither is referenced anywhere
else in the function body.

Triggers: every hour, on the hour (00:00-23:00), all day. Two separately-
delayed runs off that same trigger, exactly matching Claus's own delay
nodes (randomFirst/randomLast on Node-RED's "delay" pauseType are inert,
same as already noted in data/battery_charge_level.py's docstring -- not
ported): +60 s shifts the prices (must run before the functions below, so
they see the hour that just ended's price, not whatever the price sensors
already moved on to); +75 s runs both accumulation functions, reading
whatever the shift step produced 15 seconds earlier.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er, event
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    DOMAIN,
    ENTITY_KEY_BATTERY_DISCHARGE_HOURLY,
    ENTITY_KEY_BATTERY_GRID_CHARGE_HOURLY,
    ENTITY_KEY_ELECTRICITY_TAX_FULL_RATE,
    ENTITY_KEY_GRID_IMPORT_HOURLY,
    ENTITY_KEY_HOUSEHOLD_CONSUMPTION_HOURLY,
    ENTITY_KEY_SEED_BATTERY_GRID_CHARGE_SUM,
    ENTITY_KEY_SEED_BATTERY_GRID_DISCHARGE_SUM,
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL,
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL_WITHOUT_PV,
)
from ..devices import hub_device_info
from ..helpers.state_helper import read_float_state
from ..properties import hub_properties, pv_properties
from . import DATA_SENSOR_STATE

LOGGER = logging.getLogger(__name__)

# Node-RED's own `|| 2.3` fallback price (kr/kWh), carried over only to
# migrate an old restored value of the pre-2026-10-16 dict shape (see
# _price_from_restored() below) where the "real" key is missing --
# deliberately generous, so a migrated reading never under-counts what
# was spent. Not used anywhere else; the live calculation has its own
# "price unavailable -> skip this run" handling instead.
_PRICE_FALLBACK = 2.3

# The reduced electricity-tax rate (kr/kWh, incl. VAT) that applies above
# the full-rate threshold -- hardcoded, not user-configurable: Claus says
# it's always exactly this figure (2026-10-16), unlike the full rate
# (ENTITY_KEY_ELECTRICITY_TAX_FULL_RATE), which does change and has no
# fixed value to hardcode.
_REDUCED_ELECTRICITY_TAX_RATE = 0.01

PwM_SAVINGS_DATA_ATTRIBUTES: list[str] = [
    "purchase_price_real",
    "purchase_price_real_without_pv",
    "battery_grid_charge_sum",
    "battery_grid_discharge_sum",
    "own_production_savings",
    "battery_charging_savings",
]

PwM_SAVINGS_DATA_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="savings_data",
        name="Savings data",
        icon="mdi:cash-sync",
        # Diagnostic, like consumption_data.py/solar_data.py's own
        # attribute-only "_data" sensors this was modelled on (Claus,
        # 19-10-2026: it was the odd one out, showing up under "Sensors"
        # instead of "Diagnostic" on the device page).
        entity_category=EntityCategory.DIAGNOSTIC,
        # No device_class/native_unit_of_measurement (Claus, 2026-10-17,
        # fixing a bug from the very same day): a device_class forces HA
        # to require a NUMERIC state -- that's exactly why setting this
        # sensor's state to DATA_SENSOR_STATE ("Data", a plain string)
        # crashed entity setup ("has device class 'monetary' ... thus
        # indicating it has a numeric value; however, it has the non-
        # numeric value: 'Data'"). consumption_data.py/solar_data.py's
        # own attribute-only "_data" sensors, which this was modelled on,
        # have no device_class/unit either -- that's *why* "Data" works
        # for them, not a detail that happened to not matter.
    ),
]

# The two headline sensors -- real entities with their own History/
# Statistics graph and Energy-dashboard compatibility (device_class
# monetary + state_class total_increasing), unlike PwM_SAVINGS_DATA_
# SENSORS above. Old: sensor.besparelse_egenproduktion /
# sensor.PV_batteri_ladning_besparelse.
PwM_SAVINGS_HEADLINE_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="own_production_savings",
        name="Own production savings",
        icon="mdi:solar-power-variant",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="DKK",
    ),
    SensorEntityDescription(
        key="battery_charging_savings",
        name="Battery charging savings",
        icon="mdi:battery-heart-variant",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="DKK",
    ),
]

# The electricity price itself never comes from a plain entity_map lookup
# any more -- see _read_real_price()'s use of hub_properties.
# electricity_price() (Claus, 2026-10-15: his own supplier surcharge has
# to be added on top of ENTITY_KEY_SPOT_ELECTRICITY_PRICE's raw value
# wherever money is calculated from it). It's also now the only price:
# "without_pv" no longer has its own price sensor (see module docstring,
# 2026-10-16) -- only the electricity-tax full rate still comes from an
# entity_map field.
_TAX_FULL_RATE_KEY = ENTITY_KEY_ELECTRICITY_TAX_FULL_RATE

# entity_map key -> dotted ledger field, for the high-water-mark seed
# (see module docstring's "Reworked yet again" paragraph). Built in
# __init__, applied in _seed_ledger() against SavingsLedger's actual
# attribute names. own_production_savings/battery_charging_savings
# have no entry here any more (Claus, 2026-10-16) -- _seed_ledger()
# derives both from the four sums below instead of seeding them
# separately.
_SEED_SOURCE_KEYS: dict[str, str] = {
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL: "purchase.purchase_price_real",
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL_WITHOUT_PV: "purchase.purchase_price_real_without_pv",
    ENTITY_KEY_SEED_BATTERY_GRID_CHARGE_SUM: "battery.battery_grid_charge_sum",
    ENTITY_KEY_SEED_BATTERY_GRID_DISCHARGE_SUM: "battery.battery_grid_discharge_sum",
}

_PRICE_SHIFT_DELAY_SECONDS = 60
_ACCUMULATE_DELAY_SECONDS = 75


def signal_savings_update(entry_id: str) -> str:
    """Dispatcher signal PwMngtSavingsDataSensor sends after every +75 s
    run -- the two headline sensors listen and update themselves. Payload:
    (own_production_savings, battery_charging_savings)."""
    return f"{DOMAIN}_{entry_id}_savings_update"


# ---------------------------------------------------------------------------
# Pure calculations -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


@dataclass
class PurchasePriceState:
    purchase_price_real: float = 0.0
    purchase_price_real_without_pv: float = 0.0
    own_production_savings: float = 0.0
    # Running cumulative of household_consumption (kWh) for the current
    # tax period (month or quarter -- see hub_properties.
    # billing_period_threshold_kwh()), used to find where in the full/
    # reduced tax tiers the hypothetical "all bought from grid" scenario
    # currently sits. Reset to 0 whenever tax_period_id changes (new
    # month/quarter started) -- see calculate_purchase_prices().
    tax_period_id: str | None = None
    tax_period_consumption_cum: float = 0.0


@dataclass
class BatteryGridBalanceState:
    battery_grid_charge_sum: float = 0.0
    battery_grid_discharge_sum: float = 0.0
    battery_charging_savings: float = 0.0
    grid_discharge_pct_prev: float | None = None


@dataclass
class HourlyCounterState:
    """Bookkeeping for one raw, ever-increasing lifetime counter (one of
    the four ENTITY_KEY_*_HOURLY fields) -- what read_counter_delta()
    needs to tell a normal hour's rise from a counter reset or an
    entity_map repoint (Claus, 2026-10-16, see that function)."""

    last_entity_id: str | None = None
    last_raw_value: float | None = None


@dataclass
class HourlyCountersState:
    household_consumption: HourlyCounterState = field(default_factory=HourlyCounterState)
    grid_import: HourlyCounterState = field(default_factory=HourlyCounterState)
    battery_grid_charge: HourlyCounterState = field(default_factory=HourlyCounterState)
    battery_discharge: HourlyCounterState = field(default_factory=HourlyCounterState)


@dataclass
class SavingsLedger:
    purchase: PurchasePriceState = field(default_factory=PurchasePriceState)
    battery: BatteryGridBalanceState = field(default_factory=BatteryGridBalanceState)
    counters: HourlyCountersState = field(default_factory=HourlyCountersState)
    # Plain floats, not PriceSnapshot, since 2026-10-16: there's only one
    # price left to delay by an hour (see module docstring) -- the
    # electricity tax split happens in calculate_purchase_prices() itself,
    # using that same hour's household_consumption, not a price read a
    # tier ahead of time.
    last_hour_price: float | None = None
    this_hour_price: float | None = None


def read_counter_delta(
    state: HourlyCounterState, entity_id: str | None, raw_value: float | None
) -> tuple[float | None, HourlyCounterState]:
    """This hour's delta (kWh) for one raw, ever-increasing lifetime
    counter, tolerating both a meter/firmware reset and an entity_map
    repoint (Claus, 2026-10-16): replacing hardware (e.g. a PV inverter)
    can make its lifetime counter jump to a much lower value, or make
    Claus repoint the wizard field at a brand new entity_id whose own
    counter starts low/at 0 -- either way, naively doing new - old would
    book a huge negative number into a running total that must never
    decrease. Pure -- testable on its own.

    - raw_value is None (entity unavailable/unmapped): nothing to read
      this hour -- (None, state unchanged), same as a missing reading
      anywhere else in this module.
    - No previous reading yet, or entity_id differs from last time (first
      run ever, or a repoint): nothing safe to diff against -- this
      hour's delta is 0, and raw_value becomes the new baseline. Costs
      one hour's data right at the swap, which is the safe trade-off:
      the alternative is guessing whether the new entity's current value
      is "since the reset" or "since always".
    - raw_value dropped below last_raw_value on the SAME entity: a
      meter/firmware reset. Mirrors data/consumption_data.py's own
      accumulate() helper -- the new, lower reading is itself this
      period's accumulated amount since the reset, not negative
      consumption, so it becomes the delta directly.
    - Otherwise: the normal case, delta = raw_value - last_raw_value.
    """
    if raw_value is None:
        return None, state
    if state.last_raw_value is None or state.last_entity_id != entity_id:
        return 0.0, HourlyCounterState(last_entity_id=entity_id, last_raw_value=raw_value)
    if raw_value < state.last_raw_value:
        delta = raw_value
    else:
        delta = raw_value - state.last_raw_value
    return delta, HourlyCounterState(last_entity_id=entity_id, last_raw_value=raw_value)


def shift_prices(ledger: SavingsLedger, new_price: float) -> SavingsLedger:
    """One run of Node-RED's "gem sidste times priser": "this hour"
    becomes "last hour", and new_price becomes the new "this hour".
    last_hour_price stays None only on the very first shift ever --
    matching Node-RED's own global.get() returning undefined before its
    first run."""
    ledger.last_hour_price = ledger.this_hour_price
    ledger.this_hour_price = new_price
    return ledger


def tax_period_id(now, billing_period_is_quarter: bool) -> str:
    """A string identifying the current tax period (calendar month or
    quarter, per select.<hub>_billing_period) -- changes exactly when
    calculate_purchase_prices() should reset tax_period_consumption_cum
    back to 0. "now" is a tz-aware datetime (dt_util.now()); kept as a
    plain argument (not read from HA here) so this stays pure/testable."""
    if billing_period_is_quarter:
        quarter = (now.month - 1) // 3 + 1
        return f"{now.year}-Q{quarter}"
    return f"{now.year}-{now.month:02d}"


def calculate_purchase_prices(
    state: PurchasePriceState,
    last_hour_real_price: float,
    household_consumption: float,
    grid_import: float,
    tax_full_rate: float,
    tax_reduced_enabled: bool,
    tax_threshold_kwh: float,
    current_tax_period_id: str,
) -> PurchasePriceState:
    """One run of Node-RED's "Beregn indkøbspris med og uden PV". Pure --
    testable on its own. See module docstring for 2026-10-16's rework of
    purchase_price_real_without_pv (same commodity price as
    purchase_price_real, plus the electricity tax that hypothetical
    all-grid consumption would have incurred, split across the full/
    reduced tax tiers).

    DIFF from both Node-RED and this module's own first port (Claus,
    2026-10-16): purchase_price_real is now exactly grid_import * price,
    nothing added for battery_discharge. grid_import (his sensor.
    strom_indkob) is confirmed to already count ALL electricity bought
    from the grid, including whatever went into charging the battery --
    so a battery_discharge-weighted top-up on top of that was double-
    counting money already counted once, at the hour it was actually
    bought, and counting it again (at a different hour's price, no less)
    when it was later discharged from the battery. The money actually
    spent charging the battery from grid, and the arbitrage value of
    discharging it later, are already tracked correctly and separately in
    BatteryGridBalanceState/calculate_battery_grid_balance() below --
    that's a different question (battery arbitrage savings) from this
    one (total real spend vs. the hypothetical no-PV spend)."""
    # Tax period rolled over (new month/quarter) since the last run --
    # start this period's cumulative back at 0.
    period_consumption_before = (
        state.tax_period_consumption_cum if state.tax_period_id == current_tax_period_id else 0.0
    )

    if tax_reduced_enabled:
        kwh_at_full_rate = max(
            0.0, min(household_consumption, tax_threshold_kwh - period_consumption_before)
        )
    else:
        # select.<hub>_electricity_tax is "Full" -- no reduced tier at
        # all, every kWh taxed at the full rate.
        kwh_at_full_rate = household_consumption
    kwh_at_reduced_rate = household_consumption - kwh_at_full_rate

    tax_cost = kwh_at_full_rate * tax_full_rate + kwh_at_reduced_rate * _REDUCED_ELECTRICITY_TAX_RATE

    purchase_price_real_without_pv = round(
        state.purchase_price_real_without_pv
        + household_consumption * last_hour_real_price
        + tax_cost,
        2,
    )
    purchase_price_real = round(
        state.purchase_price_real + grid_import * last_hour_real_price, 2
    )

    own_production_savings = state.own_production_savings
    if (purchase_price_real_without_pv - purchase_price_real) > own_production_savings:
        own_production_savings = round(purchase_price_real_without_pv - purchase_price_real, 2)

    return PurchasePriceState(
        purchase_price_real=purchase_price_real,
        purchase_price_real_without_pv=purchase_price_real_without_pv,
        own_production_savings=own_production_savings,
        tax_period_id=current_tax_period_id,
        tax_period_consumption_cum=period_consumption_before + household_consumption,
    )


def calculate_battery_grid_balance(
    state: BatteryGridBalanceState,
    last_hour_real_price: float,
    battery_grid_charge: float,
    battery_discharge: float,
    grid_discharge_pct_cum: float,
    battery_size: float,
) -> BatteryGridBalanceState:
    """One run of Node-RED's "Beregn balance på ladning af batteri fra
    grid". Pure -- testable on its own.

    TODO (Claus, 2026-10-16): battery_discharge (ENTITY_KEY_BATTERY_
    DISCHARGE_HOURLY, "Battery discharge to house") is, as a matter of
    fact about the underlying sensor, ALL discharge from the battery --
    there is no way to tell from it alone whether a given kWh went to
    the house or was sold to the grid. Right now that's harmless, since
    Claus has no grid-export logic yet, so 100% of discharge really does
    go to the house. It stops being harmless the day he builds battery-
    to-grid selling (when the price is good): this function would keep
    valuing the whole discharge at last_hour_real_price (the PURCHASE
    price, i.e. "what we avoided buying"), which is wrong for any kWh
    that was actually sold -- that portion only earned the sale price,
    not the (usually much higher, tax-inclusive) purchase price, and
    crediting it as a purchase-avoidance savings would overstate
    battery_charging_savings.

    Fix, once that feature exists: it's the export logic itself that
    knows how many kWh it commanded sold this hour (the raw counter
    can't be fixed to tell the two apart) -- have it report that figure
    separately, subtract it from battery_discharge here before running
    today's buy-price valuation on the remainder, and value the
    subtracted (exported) kWh on its own, at the real sale price, into
    its own sum -- NOT battery_charging_savings."""
    kwh_per_percent_point = battery_size / 100 if battery_size else 0.0

    grid_discharge_pct_this_hour = 0.0
    if state.grid_discharge_pct_prev is not None:
        grid_discharge_pct_this_hour = max(
            0.0, grid_discharge_pct_cum - state.grid_discharge_pct_prev
        )

    battery_discharge_from_grid = min(
        grid_discharge_pct_this_hour * kwh_per_percent_point, battery_discharge
    )

    battery_grid_charge_sum = round(
        state.battery_grid_charge_sum + battery_grid_charge * last_hour_real_price, 2
    )
    battery_grid_discharge_sum = round(
        state.battery_grid_discharge_sum + battery_discharge_from_grid * last_hour_real_price,
        2,
    )

    battery_charging_savings = state.battery_charging_savings
    if (battery_grid_discharge_sum - battery_grid_charge_sum) > battery_charging_savings:
        battery_charging_savings = round(battery_grid_discharge_sum - battery_grid_charge_sum, 2)

    return BatteryGridBalanceState(
        battery_grid_charge_sum=battery_grid_charge_sum,
        battery_grid_discharge_sum=battery_grid_discharge_sum,
        battery_charging_savings=battery_charging_savings,
        grid_discharge_pct_prev=grid_discharge_pct_cum,
    )


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


def _counter_from_restored(raw: dict[str, Any] | None) -> HourlyCounterState:
    """Builds one HourlyCounterState from restored state. Explicit
    field-by-field reads, not HourlyCounterState(**raw), so an unexpected
    key (e.g. from a future version of this module) is ignored instead of
    crashing restore."""
    if raw is None:
        return HourlyCounterState()
    return HourlyCounterState(
        last_entity_id=raw.get("last_entity_id"),
        last_raw_value=raw.get("last_raw_value"),
    )


def _price_from_restored(raw: Any) -> float | None:
    """Reads last_hour_price/this_hour_price back from restored state.
    Tolerates the dict shape ({"real": ..., "without_pv": ...}) a version
    of this module from before 2026-10-16 may have saved -- only "real"
    survives (see module docstring on why "without_pv" dropped its own
    price). A plain float is the current/new shape."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw.get("real", _PRICE_FALLBACK)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@dataclass
class PwMngtSavingsStoredData(ExtraStoredData):
    """Everything PwMngtSavingsDataSensor restores across a restart --
    the full ledger. Unlike data/balance_data.py's sample history, this is
    never discarded for being "too old": a money total has no shelf life.
    """

    ledger: SavingsLedger

    def as_dict(self) -> dict[str, Any]:
        purchase = self.ledger.purchase
        battery = self.ledger.battery
        counters = self.ledger.counters
        return {
            "purchase": {
                "purchase_price_real": purchase.purchase_price_real,
                "purchase_price_real_without_pv": purchase.purchase_price_real_without_pv,
                "own_production_savings": purchase.own_production_savings,
                "tax_period_id": purchase.tax_period_id,
                "tax_period_consumption_cum": purchase.tax_period_consumption_cum,
            },
            "battery": {
                "battery_grid_charge_sum": battery.battery_grid_charge_sum,
                "battery_grid_discharge_sum": battery.battery_grid_discharge_sum,
                "battery_charging_savings": battery.battery_charging_savings,
                "grid_discharge_pct_prev": battery.grid_discharge_pct_prev,
            },
            "counters": {
                "household_consumption": vars(counters.household_consumption),
                "grid_import": vars(counters.grid_import),
                "battery_grid_charge": vars(counters.battery_grid_charge),
                "battery_discharge": vars(counters.battery_discharge),
            },
            "last_hour_price": self.ledger.last_hour_price,
            "this_hour_price": self.ledger.this_hour_price,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtSavingsStoredData":
        purchase_raw = restored.get("purchase") or {}
        battery_raw = restored.get("battery") or {}
        counters_raw = restored.get("counters") or {}
        last_hour_raw = restored.get("last_hour_price")
        this_hour_raw = restored.get("this_hour_price")
        return cls(
            ledger=SavingsLedger(
                purchase=PurchasePriceState(
                    purchase_price_real=purchase_raw.get("purchase_price_real", 0.0),
                    purchase_price_real_without_pv=purchase_raw.get(
                        "purchase_price_real_without_pv", 0.0
                    ),
                    own_production_savings=purchase_raw.get("own_production_savings", 0.0),
                    tax_period_id=purchase_raw.get("tax_period_id"),
                    tax_period_consumption_cum=purchase_raw.get(
                        "tax_period_consumption_cum", 0.0
                    ),
                ),
                battery=BatteryGridBalanceState(
                    battery_grid_charge_sum=battery_raw.get("battery_grid_charge_sum", 0.0),
                    battery_grid_discharge_sum=battery_raw.get(
                        "battery_grid_discharge_sum", 0.0
                    ),
                    battery_charging_savings=battery_raw.get("battery_charging_savings", 0.0),
                    grid_discharge_pct_prev=battery_raw.get("grid_discharge_pct_prev"),
                ),
                counters=HourlyCountersState(
                    household_consumption=_counter_from_restored(
                        counters_raw.get("household_consumption")
                    ),
                    grid_import=_counter_from_restored(counters_raw.get("grid_import")),
                    battery_grid_charge=_counter_from_restored(
                        counters_raw.get("battery_grid_charge")
                    ),
                    battery_discharge=_counter_from_restored(
                        counters_raw.get("battery_discharge")
                    ),
                ),
                last_hour_price=_price_from_restored(last_hour_raw),
                this_hour_price=_price_from_restored(this_hour_raw),
            )
        )


class PwMngtSavingsDataSensor(RestoreEntity, SensorEntity):
    """The Hub's savings_data sensor -- owns the whole ledger, runs the
    hourly price-shift and both accumulation functions, and dispatches the
    two headline numbers out to their own sensors (see
    signal_savings_update()). See the module docstring for the full
    trigger graph and the seeding mechanism."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device_info(entry)
        self._attr_native_value = DATA_SENSOR_STATE
        self._attr_extra_state_attributes = {
            key: None for key in PwM_SAVINGS_DATA_ATTRIBUTES
        }
        self._entry = entry
        self._ledger = SavingsLedger()
        self._pending_shift: Any = None
        self._pending_accumulate: Any = None

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        # "real" is read via hub_properties.electricity_price() instead
        # (see _TAX_FULL_RATE_KEY's comment). _tax_full_rate_entity_id is
        # the only price-ish entity_map field left for this sensor.
        self._tax_full_rate_entity_id = entity_map.get(_TAX_FULL_RATE_KEY) or None
        self._household_consumption_entity_id = (
            entity_map.get(ENTITY_KEY_HOUSEHOLD_CONSUMPTION_HOURLY) or None
        )
        self._grid_import_entity_id = entity_map.get(ENTITY_KEY_GRID_IMPORT_HOURLY) or None
        self._battery_grid_charge_entity_id = (
            entity_map.get(ENTITY_KEY_BATTERY_GRID_CHARGE_HOURLY) or None
        )
        self._battery_discharge_entity_id = (
            entity_map.get(ENTITY_KEY_BATTERY_DISCHARGE_HOURLY) or None
        )
        self._seed_entity_ids = {
            field_path: entity_map.get(key) or None
            for key, field_path in _SEED_SOURCE_KEYS.items()
        }

    @property
    def extra_restore_state_data(self) -> PwMngtSavingsStoredData:
        return PwMngtSavingsStoredData(ledger=self._ledger)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        restored = await self.async_get_last_extra_data()
        if restored is not None:
            self._ledger = PwMngtSavingsStoredData.from_dict(restored.as_dict()).ledger
        self._seed_ledger()

        self.async_on_remove(
            event.async_track_time_change(
                self.hass, self._handle_hourly_trigger, minute=0, second=0
            )
        )
        self._write_state()
        self._dispatch_headline_values()

    async def async_will_remove_from_hass(self) -> None:
        if self._pending_shift is not None:
            self._pending_shift()
            self._pending_shift = None
        if self._pending_accumulate is not None:
            self._pending_accumulate()
            self._pending_accumulate = None
        await super().async_will_remove_from_hass()

    def _seed_ledger(self) -> None:
        """High-water-mark seed, re-applied on every async_added_to_hass
        (not just the first run ever -- Claus, 2026-10-16, see module
        docstring's "Reworked yet again" paragraph). Each of the 4 seed
        fields only ever pulls its ledger field UP, never down: if the
        seed sensor's current value is lower than what the ledger
        already holds (the normal case once PwMngt has been tracking for
        a while, or after options were reloaded for an unrelated reason),
        it's silently ignored. If it's higher -- a genuine first-time
        seed, or Claus fixing a wrongly-pointed historical sensor and
        re-running the wizard -- it's taken. Either way, running this on
        every reload instead of gating it on "no restored state yet"
        means pointing the wizard's seed fields at a historical sensor
        always has an effect, instead of silently doing nothing once the
        entity has ever been restored once before (the bug this fixed)."""
        for field_path, entity_id in self._seed_entity_ids.items():
            value = self._seed_value(entity_id)
            if value is None:
                continue
            owner_name, attr_name = field_path.split(".", 1)
            owner = getattr(self._ledger, owner_name)
            current = getattr(owner, attr_name)
            if current is not None and value <= current:
                continue
            setattr(owner, attr_name, value)
            LOGGER.info(
                "PwMngt: seeded savings_data's '%s' from '%s' (%.2f DKK)",
                field_path,
                entity_id,
                value,
            )

        # own_production_savings/battery_charging_savings have no seed
        # field of their own (Claus, 2026-10-16) -- derive both from
        # whatever the four sums above now hold, same formula
        # calculate_purchase_prices()/calculate_battery_grid_balance() use
        # every hour. Same high-water-mark rule as above: only raised,
        # never lowered, including against their own already-stored
        # value -- this runs on every reload now, not just once, so it
        # must not undo an hourly high-water mark set since the last
        # reload just because this run's raw diff happens to be smaller.
        purchase = self._ledger.purchase
        derived_production_savings = max(
            0.0, purchase.purchase_price_real_without_pv - purchase.purchase_price_real
        )
        if derived_production_savings > purchase.own_production_savings:
            purchase.own_production_savings = round(derived_production_savings, 2)

        battery = self._ledger.battery
        derived_charging_savings = max(
            0.0, battery.battery_grid_discharge_sum - battery.battery_grid_charge_sum
        )
        if derived_charging_savings > battery.battery_charging_savings:
            battery.battery_charging_savings = round(derived_charging_savings, 2)

    def _seed_value(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        return read_float_state(self.hass.states.get(entity_id))

    # -- triggers ----------------------------------------------------------

    @callback
    def _handle_hourly_trigger(self, _now=None) -> None:
        self._pending_shift = event.async_call_later(
            self.hass, _PRICE_SHIFT_DELAY_SECONDS, self._handle_price_shift
        )
        self._pending_accumulate = event.async_call_later(
            self.hass, _ACCUMULATE_DELAY_SECONDS, self._handle_accumulate
        )

    @callback
    def _handle_price_shift(self, _now=None) -> None:
        self._pending_shift = None
        new_price = self._read_real_price()
        if new_price is None:
            return
        self._ledger = shift_prices(self._ledger, new_price)
        self._write_state()

    @callback
    def _handle_accumulate(self, _now=None) -> None:
        self._pending_accumulate = None
        last_hour_real_price = self._ledger.last_hour_price
        if last_hour_real_price is None:
            # No shift has ever completed yet (fresh install, or this is
            # the very first hourly cycle since startup) -- nothing to
            # price this hour's energy at. Node-RED: global.get() returns
            # undefined here too before its own first run.
            return

        counters = self._ledger.counters
        household_consumption, counters.household_consumption = self._read_counter(
            self._household_consumption_entity_id, counters.household_consumption
        )
        grid_import, counters.grid_import = self._read_counter(
            self._grid_import_entity_id, counters.grid_import
        )
        battery_grid_charge, counters.battery_grid_charge = self._read_counter(
            self._battery_grid_charge_entity_id, counters.battery_grid_charge
        )
        battery_discharge, counters.battery_discharge = self._read_counter(
            self._battery_discharge_entity_id, counters.battery_discharge
        )
        grid_discharge_pct_cum, _total_discharge_pct_cum = self._read_discharge_pct_cum()
        battery_size = self._read_battery_size()

        if household_consumption is not None and grid_import is not None:
            tax_full_rate = self._read_tax_full_rate()
            tax_reduced_enabled = hub_properties.electricity_tax_reduced_enabled(
                self.hass, self._entry
            )
            tax_threshold_kwh = hub_properties.billing_period_threshold_kwh(
                self.hass, self._entry
            )
            current_tax_period_id = self._current_tax_period_id()
            self._ledger.purchase = calculate_purchase_prices(
                self._ledger.purchase,
                last_hour_real_price,
                household_consumption,
                grid_import,
                tax_full_rate,
                tax_reduced_enabled,
                tax_threshold_kwh,
                current_tax_period_id,
            )
        else:
            LOGGER.debug(
                "PwMngt: savings_data skipping this hour's purchase-price "
                "run -- a required hourly reading is missing"
            )

        if battery_grid_charge is not None and battery_discharge is not None:
            self._ledger.battery = calculate_battery_grid_balance(
                self._ledger.battery,
                last_hour_real_price,
                battery_grid_charge,
                battery_discharge,
                grid_discharge_pct_cum,
                battery_size,
            )
        else:
            LOGGER.debug(
                "PwMngt: savings_data skipping this hour's battery-grid-"
                "balance run -- a required hourly reading is missing"
            )

        self._write_state()
        self._dispatch_headline_values()

    # -- reads ---------------------------------------------------------------

    def _read_real_price(self) -> float | None:
        real = hub_properties.electricity_price(self.hass, self._entry)
        return round(real, 2) if real is not None else None

    def _read_tax_full_rate(self) -> float:
        """The full electricity-tax rate (kr/kWh), from Claus's own
        entity_map field -- falls back to the reduced rate when
        unmapped/unavailable, same "don't invent a tax rate higher than
        reality" reasoning as elsewhere, and matching today's actual law
        (no full-rate tier currently in effect -- see module docstring)."""
        value = self._read_price(self._tax_full_rate_entity_id)
        return value if value is not None else _REDUCED_ELECTRICITY_TAX_RATE

    def _current_tax_period_id(self) -> str:
        is_quarter = hub_properties.billing_period_is_quarter(self.hass, self._entry)
        return tax_period_id(dt_util.now(), is_quarter)

    def _read_price(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        value = read_float_state(self.hass.states.get(entity_id))
        return round(value, 2) if value is not None else None

    def _read_counter(
        self, entity_id: str | None, state: HourlyCounterState
    ) -> tuple[float | None, HourlyCounterState]:
        """This hour's delta (kWh) for one of the four ENTITY_KEY_*_HOURLY
        fields, read from the entity's live .state -- a raw, ever-
        increasing lifetime counter (Claus's own Utility Meter helpers
        were dropped 2026-10-16 in favour of this, so a hardware swap,
        e.g. a new PV inverter, doesn't need a new helper set up too).
        See read_counter_delta() for the reset/entity-swap handling."""
        raw_value = read_float_state(self.hass.states.get(entity_id)) if entity_id else None
        return read_counter_delta(state, entity_id, raw_value)

    def _read_discharge_pct_cum(self) -> tuple[float, float]:
        entity_id = pv_properties.battery_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return 0.0, 0.0
        grid = _read_float_attribute(state, "grid_afladning_pct_cum") or 0.0
        total = _read_float_attribute(state, "total_afladning_pct_cum") or 0.0
        return grid, total

    def _read_battery_size(self) -> float:
        entity_id = pv_properties.battery_size_entity_id(self.hass, self._entry)
        value = read_float_state(self.hass.states.get(entity_id)) if entity_id else None
        return value if value is not None else 0.0

    # -- output ----------------------------------------------------------

    def _write_state(self) -> None:
        purchase = self._ledger.purchase
        battery = self._ledger.battery
        # Attribute-only "_data" sensor (Claus, 2026-10-16): no single
        # number here is more "the" state than another -- purchase_price_
        # real was being shown as native_value before, but that's just
        # one of several running totals, picked somewhat arbitrarily, not
        # a natural single value the way balance_data's 30-second balance
        # is. Matches consumption_data.py/solar_data.py's own pattern
        # instead: state is the constant DATA_SENSOR_STATE marker, the
        # real values all live in the attributes below.
        self._attr_native_value = DATA_SENSOR_STATE
        self._attr_extra_state_attributes = {
            "purchase_price_real": purchase.purchase_price_real,
            "purchase_price_real_without_pv": purchase.purchase_price_real_without_pv,
            "battery_grid_charge_sum": battery.battery_grid_charge_sum,
            "battery_grid_discharge_sum": battery.battery_grid_discharge_sum,
            # Added (Claus, 2026-10-16) so PwMngtSavingsHeadlineSensor can
            # read the live value straight off this sibling entity's own
            # state at startup, instead of relying only on the one-shot
            # dispatcher signal below, which it's too late to catch (see
            # that class's own async_added_to_hass()).
            "own_production_savings": purchase.own_production_savings,
            "battery_charging_savings": battery.battery_charging_savings,
        }
        self.async_write_ha_state()

    def _dispatch_headline_values(self) -> None:
        async_dispatcher_send(
            self.hass,
            signal_savings_update(self._entry.entry_id),
            self._ledger.purchase.own_production_savings,
            self._ledger.battery.battery_charging_savings,
        )


def _read_float_attribute(state: State, attribute: str) -> float | None:
    value = state.attributes.get(attribute)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _savings_data_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Entity id of the "Savings data" sensor (PwM_SAVINGS_DATA_SENSORS'
    one entry, key "savings_data") for this config entry -- same entity-
    registry-by-unique_id lookup as hub_properties._hub_entity_id, kept
    local here since it's only ever needed by PwMngtSavingsHeadlineSensor
    below."""
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_savings_data"
    )


class PwMngtSavingsHeadlineSensor(RestoreEntity, SensorEntity):
    """Either of the two standalone, graphable savings sensors (own-
    production or battery-charging) -- see PwM_SAVINGS_HEADLINE_SENSORS.
    Restores its own last shown value directly (not via PwMngtSavingsData
    Sensor's ledger) so it has something to show immediately after a
    restart, then updates whenever signal_savings_update() fires."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        value_index: int,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device_info(entry)
        self._attr_native_value = 0.0
        self._entry = entry
        # 0 = own_production_savings, 1 = battery_charging_savings -- see
        # signal_savings_update()'s payload order.
        self._value_index = value_index

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            value = read_float_state(last_state)
            if value is not None:
                self._attr_native_value = value

        # Claus, 2026-10-16: don't rely solely on the dispatcher signal
        # for this entity's value right after a restart. PwMngtSavings
        # DataSensor (sibling entity, same config entry) is added to hass
        # before these headline sensors (see sensor.py's async_setup_
        # entry), so its own async_added_to_hass() -- which re-seeds the
        # ledger and sends the one-shot startup dispatch -- has already
        # run and fired *before* async_dispatcher_connect() below even
        # subscribes, every single restart, so that one dispatch is
        # always missed. Read the live value straight off the sibling's
        # own state instead: it's already there by now, since entities
        # added in the same async_add_entities() call have their
        # async_added_to_hass() awaited one at a time, in order. Only
        # overrides the possibly-stale restored value above if the
        # sibling and its attribute are actually there.
        live_value = self._read_live_value()
        if live_value is not None:
            self._attr_native_value = live_value

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_savings_update(self._entry.entry_id),
                self._handle_update,
            )
        )
        self.async_write_ha_state()

    def _read_live_value(self) -> float | None:
        entity_id = _savings_data_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        attribute = ("own_production_savings", "battery_charging_savings")[self._value_index]
        return _read_float_attribute(state, attribute)

    @callback
    def _handle_update(self, own_production_savings: float, battery_charging_savings: float) -> None:
        value = (own_production_savings, battery_charging_savings)[self._value_index]
        self._attr_native_value = value
        self.async_write_ha_state()
