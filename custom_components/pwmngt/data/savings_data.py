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

Entirely optional: every entity_map field this reads (ENTITY_KEY_PRICE_
VARIABLE/_WITHOUT_PV, the four hourly ENTITY_KEY_*_HOURLY counters) is
Claus's own sensor setup, not derivable from anything else PwMngt has.
Left unmapped, the affected running totals simply never update (see
_run()). The "real" price is ENTITY_KEY_SPOT_ELECTRICITY_PRICE (already
used elsewhere) -- Claus, 2026-10-15: "det er det samme reelt set" -- no
separate field for it -- read via hub_properties.electricity_price(),
which adds Claus's configured kr/kWh surcharge on top (added the same
day, see that function's own docstring): his supplier's markup isn't
something the configured price sensor itself knows about.

Seeding from Node-RED (ENTITY_KEY_SEED_*, const.py): on this module's very
first run ever (no restored PwMngt state yet), each of the 9 running
totals optionally starts from the current value of Claus's matching
Node-RED sensor instead of 0, so years of his own tracked savings aren't
silently lost when PwMngt takes over. Read exactly once; after that first
run, PwMngt's own RestoreEntity-persisted state is authoritative and these
seed fields are never consulted again. See _seed_value()/_run_first_ever().

Why all 9 running totals (not just the two headline ones) have to be
seeded together: own_production_savings and battery_charging_savings are
both a running *high-water mark* of (bigger sum - smaller sum) --
lifetime totals, not deltas. Seeding only the headline number while its
underlying sums restart at 0 would freeze it at the seeded value for
months, until the fresh sums grew large enough on their own to overtake
it (Claus, 2026-10-15 design discussion).

Deliberate difference from Node-RED (DIFF, Claus 2026-10-15): "Beregn
indkøbspris med og uden PV" tracks its own previous
grid_afladning_pct_cum/total_afladning_pct_cum with a plain `|| 0`
fallback, unlike "Beregn balance på ladning af batteri fra grid" in the
very same export, which guards the equivalent tracker against a missing
previous value (not just a previous value of 0) to avoid booking an
entire lifetime's grid-discharge percentage as "this hour"'s after a
Node-RED restart/deploy wipes local context. That guard is ported here for
*both* functions, matching the pattern Claus's own code already uses for
one of them -- not a new behaviour, just applied consistently. See
_calculate_purchase_prices()'s has_prev handling.

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
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import event
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from ..const import (
    CONF_ENTITY_MAP,
    DOMAIN,
    ENTITY_KEY_BATTERY_DISCHARGE_HOURLY,
    ENTITY_KEY_BATTERY_GRID_CHARGE_HOURLY,
    ENTITY_KEY_GRID_IMPORT_HOURLY,
    ENTITY_KEY_HOUSEHOLD_CONSUMPTION_HOURLY,
    ENTITY_KEY_PRICE_VARIABLE,
    ENTITY_KEY_PRICE_WITHOUT_PV,
    ENTITY_KEY_SEED_BATTERY_CHARGING_SAVINGS,
    ENTITY_KEY_SEED_BATTERY_GRID_CHARGE_SUM,
    ENTITY_KEY_SEED_BATTERY_GRID_DISCHARGE_SUM,
    ENTITY_KEY_SEED_OWN_PRODUCTION_SAVINGS,
    ENTITY_KEY_SEED_PRICE_AGREEMENT_SAVINGS,
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL,
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL_WITHOUT_PV,
    ENTITY_KEY_SEED_PURCHASE_PRICE_VARIABLE,
    ENTITY_KEY_SEED_PURCHASE_PRICE_VARIABLE_WITHOUT_PV,
)
from ..devices import hub_device_info
from ..helpers.state_helper import read_float_state
from ..properties import hub_properties, pv_properties

LOGGER = logging.getLogger(__name__)

# Node-RED's own `|| 2.3` fallback price (kr/kWh) when a price sensor is
# unavailable -- a deliberately generous guess so a missing reading never
# makes this run under-count what was spent.
_PRICE_FALLBACK = 2.3

PwM_SAVINGS_DATA_ATTRIBUTES: list[str] = [
    "purchase_price_real",
    "purchase_price_variable",
    "purchase_price_real_without_pv",
    "purchase_price_variable_without_pv",
    "price_agreement_savings",
    "battery_grid_charge_sum",
    "battery_grid_discharge_sum",
]

PwM_SAVINGS_DATA_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="savings_data",
        name="Savings data",
        icon="mdi:cash-sync",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="DKK",
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

# entity_map key -> which PriceSnapshot field it fills, for the +60 s shift
# step. "real" isn't here -- it doesn't come from a plain entity_map
# lookup any more, see _read_price_snapshot()'s use of
# hub_properties.electricity_price() (Claus, 2026-10-15: his own supplier
# surcharge has to be added on top of ENTITY_KEY_SPOT_ELECTRICITY_PRICE's
# raw value wherever money is calculated from it).
_PRICE_SOURCE_KEYS = {
    "variable": ENTITY_KEY_PRICE_VARIABLE,
    "without_pv": ENTITY_KEY_PRICE_WITHOUT_PV,
}

# entity_map key -> dotted ledger field, for the one-time seed (see module
# docstring). Checked in __init__/_run_first_ever() against SavingsLedger's
# actual attribute names.
_SEED_SOURCE_KEYS: dict[str, str] = {
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL: "purchase.purchase_price_real",
    ENTITY_KEY_SEED_PURCHASE_PRICE_VARIABLE: "purchase.purchase_price_variable",
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL_WITHOUT_PV: "purchase.purchase_price_real_without_pv",
    ENTITY_KEY_SEED_PURCHASE_PRICE_VARIABLE_WITHOUT_PV: "purchase.purchase_price_variable_without_pv",
    ENTITY_KEY_SEED_PRICE_AGREEMENT_SAVINGS: "purchase.price_agreement_savings",
    ENTITY_KEY_SEED_OWN_PRODUCTION_SAVINGS: "purchase.own_production_savings",
    ENTITY_KEY_SEED_BATTERY_GRID_CHARGE_SUM: "battery.battery_grid_charge_sum",
    ENTITY_KEY_SEED_BATTERY_GRID_DISCHARGE_SUM: "battery.battery_grid_discharge_sum",
    ENTITY_KEY_SEED_BATTERY_CHARGING_SAVINGS: "battery.battery_charging_savings",
}

_HOURLY_TRIGGER_HOURS = list(range(24))
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
class PriceSnapshot:
    real: float
    variable: float
    without_pv: float


@dataclass
class PurchasePriceState:
    purchase_price_real: float = 0.0
    purchase_price_variable: float = 0.0
    purchase_price_real_without_pv: float = 0.0
    purchase_price_variable_without_pv: float = 0.0
    own_production_savings: float = 0.0
    price_agreement_savings: float = 0.0
    grid_discharge_pct_prev: float | None = None
    total_discharge_pct_prev: float | None = None


@dataclass
class BatteryGridBalanceState:
    battery_grid_charge_sum: float = 0.0
    battery_grid_discharge_sum: float = 0.0
    battery_charging_savings: float = 0.0
    grid_discharge_pct_prev: float | None = None


@dataclass
class SavingsLedger:
    purchase: PurchasePriceState = field(default_factory=PurchasePriceState)
    battery: BatteryGridBalanceState = field(default_factory=BatteryGridBalanceState)
    last_hour_price: PriceSnapshot | None = None
    this_hour_price: PriceSnapshot | None = None


def shift_prices(ledger: SavingsLedger, new_price: PriceSnapshot) -> SavingsLedger:
    """One run of Node-RED's "gem sidste times priser": "this hour"
    becomes "last hour", and new_price becomes the new "this hour".
    last_hour_price stays None only on the very first shift ever --
    matching Node-RED's own global.get() returning undefined before its
    first run."""
    ledger.last_hour_price = ledger.this_hour_price
    ledger.this_hour_price = new_price
    return ledger


def calculate_purchase_prices(
    state: PurchasePriceState,
    last_hour_price: PriceSnapshot,
    household_consumption: float,
    grid_import: float,
    battery_discharge: float,
    grid_discharge_pct_cum: float,
    total_discharge_pct_cum: float,
) -> PurchasePriceState:
    """One run of Node-RED's "Beregn indkøbspris med og uden PV". Pure --
    testable on its own. See module docstring for the has_prev DIFF."""
    has_prev = state.grid_discharge_pct_prev is not None
    grid_discharge_pct_delta = 0.0
    total_discharge_pct_delta = 0.0
    if has_prev:
        grid_discharge_pct_delta = grid_discharge_pct_cum - (state.grid_discharge_pct_prev or 0.0)
        total_discharge_pct_delta = total_discharge_pct_cum - (state.total_discharge_pct_prev or 0.0)

    grid_share_of_discharge = 0.0
    if total_discharge_pct_delta > 0:
        grid_share_of_discharge = grid_discharge_pct_delta / total_discharge_pct_delta

    grid_import_total = grid_import + battery_discharge * grid_share_of_discharge

    purchase_price_real_without_pv = round(
        state.purchase_price_real_without_pv + household_consumption * last_hour_price.without_pv,
        2,
    )
    # Deliberately household_consumption x the *variable* price (not
    # without_pv) -- literal to Node-RED: "what would full consumption
    # have cost at variable price, with no PV at all".
    purchase_price_variable_without_pv = round(
        state.purchase_price_variable_without_pv + household_consumption * last_hour_price.variable,
        2,
    )
    purchase_price_real = round(
        state.purchase_price_real + grid_import_total * last_hour_price.real, 2
    )
    purchase_price_variable = round(
        state.purchase_price_variable + grid_import_total * last_hour_price.variable, 2
    )

    own_production_savings = state.own_production_savings
    if (purchase_price_real_without_pv - purchase_price_real) > own_production_savings:
        own_production_savings = round(purchase_price_real_without_pv - purchase_price_real, 2)

    price_agreement_savings = round(purchase_price_real - purchase_price_variable, 2)

    return PurchasePriceState(
        purchase_price_real=purchase_price_real,
        purchase_price_variable=purchase_price_variable,
        purchase_price_real_without_pv=purchase_price_real_without_pv,
        purchase_price_variable_without_pv=purchase_price_variable_without_pv,
        own_production_savings=own_production_savings,
        price_agreement_savings=price_agreement_savings,
        grid_discharge_pct_prev=grid_discharge_pct_cum,
        total_discharge_pct_prev=total_discharge_pct_cum,
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
    grid". Pure -- testable on its own."""
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
        return {
            "purchase": {
                "purchase_price_real": purchase.purchase_price_real,
                "purchase_price_variable": purchase.purchase_price_variable,
                "purchase_price_real_without_pv": purchase.purchase_price_real_without_pv,
                "purchase_price_variable_without_pv": purchase.purchase_price_variable_without_pv,
                "own_production_savings": purchase.own_production_savings,
                "price_agreement_savings": purchase.price_agreement_savings,
                "grid_discharge_pct_prev": purchase.grid_discharge_pct_prev,
                "total_discharge_pct_prev": purchase.total_discharge_pct_prev,
            },
            "battery": {
                "battery_grid_charge_sum": battery.battery_grid_charge_sum,
                "battery_grid_discharge_sum": battery.battery_grid_discharge_sum,
                "battery_charging_savings": battery.battery_charging_savings,
                "grid_discharge_pct_prev": battery.grid_discharge_pct_prev,
            },
            "last_hour_price": (
                None
                if self.ledger.last_hour_price is None
                else vars(self.ledger.last_hour_price)
            ),
            "this_hour_price": (
                None
                if self.ledger.this_hour_price is None
                else vars(self.ledger.this_hour_price)
            ),
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtSavingsStoredData":
        purchase_raw = restored.get("purchase") or {}
        battery_raw = restored.get("battery") or {}
        last_hour_raw = restored.get("last_hour_price")
        this_hour_raw = restored.get("this_hour_price")
        return cls(
            ledger=SavingsLedger(
                purchase=PurchasePriceState(
                    purchase_price_real=purchase_raw.get("purchase_price_real", 0.0),
                    purchase_price_variable=purchase_raw.get("purchase_price_variable", 0.0),
                    purchase_price_real_without_pv=purchase_raw.get(
                        "purchase_price_real_without_pv", 0.0
                    ),
                    purchase_price_variable_without_pv=purchase_raw.get(
                        "purchase_price_variable_without_pv", 0.0
                    ),
                    own_production_savings=purchase_raw.get("own_production_savings", 0.0),
                    price_agreement_savings=purchase_raw.get("price_agreement_savings", 0.0),
                    grid_discharge_pct_prev=purchase_raw.get("grid_discharge_pct_prev"),
                    total_discharge_pct_prev=purchase_raw.get("total_discharge_pct_prev"),
                ),
                battery=BatteryGridBalanceState(
                    battery_grid_charge_sum=battery_raw.get("battery_grid_charge_sum", 0.0),
                    battery_grid_discharge_sum=battery_raw.get(
                        "battery_grid_discharge_sum", 0.0
                    ),
                    battery_charging_savings=battery_raw.get("battery_charging_savings", 0.0),
                    grid_discharge_pct_prev=battery_raw.get("grid_discharge_pct_prev"),
                ),
                last_hour_price=(
                    None if last_hour_raw is None else PriceSnapshot(**last_hour_raw)
                ),
                this_hour_price=(
                    None if this_hour_raw is None else PriceSnapshot(**this_hour_raw)
                ),
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
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            key: None for key in PwM_SAVINGS_DATA_ATTRIBUTES
        }
        self._entry = entry
        self._ledger = SavingsLedger()
        self._pending_shift: Any = None
        self._pending_accumulate: Any = None

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        # "real" is read via hub_properties.electricity_price() instead
        # (see _PRICE_SOURCE_KEYS' comment) -- not in this dict.
        self._price_entity_ids = {
            field_name: entity_map.get(key) or None
            for field_name, key in _PRICE_SOURCE_KEYS.items()
        }
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
        else:
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
        """First run ever (no restored state) -- see module docstring."""
        for field_path, entity_id in self._seed_entity_ids.items():
            value = self._seed_value(entity_id)
            if value is None:
                continue
            owner_name, attr_name = field_path.split(".", 1)
            owner = getattr(self._ledger, owner_name)
            setattr(owner, attr_name, value)
            LOGGER.info(
                "PwMngt: seeded savings_data's '%s' from '%s' (%.2f DKK)",
                field_path,
                entity_id,
                value,
            )

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
        new_price = self._read_price_snapshot()
        if new_price is None:
            return
        self._ledger = shift_prices(self._ledger, new_price)
        self._write_state()

    @callback
    def _handle_accumulate(self, _now=None) -> None:
        self._pending_accumulate = None
        last_hour_price = self._ledger.last_hour_price
        if last_hour_price is None:
            # No shift has ever completed yet (fresh install, or this is
            # the very first hourly cycle since startup) -- nothing to
            # price this hour's energy at. Node-RED: global.get() returns
            # undefined here too before its own first run.
            return

        household_consumption = self._read_hourly(self._household_consumption_entity_id)
        grid_import = self._read_hourly(self._grid_import_entity_id)
        battery_grid_charge = self._read_hourly(self._battery_grid_charge_entity_id)
        battery_discharge = self._read_hourly(self._battery_discharge_entity_id)
        grid_discharge_pct_cum, total_discharge_pct_cum = self._read_discharge_pct_cum()
        battery_size = self._read_battery_size()

        if household_consumption is not None and grid_import is not None and battery_discharge is not None:
            self._ledger.purchase = calculate_purchase_prices(
                self._ledger.purchase,
                last_hour_price,
                household_consumption,
                grid_import,
                battery_discharge,
                grid_discharge_pct_cum,
                total_discharge_pct_cum,
            )
        else:
            LOGGER.debug(
                "PwMngt: savings_data skipping this hour's purchase-price "
                "run -- a required hourly reading is missing"
            )

        if battery_grid_charge is not None and battery_discharge is not None:
            self._ledger.battery = calculate_battery_grid_balance(
                self._ledger.battery,
                last_hour_price.real,
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

    def _read_price_snapshot(self) -> PriceSnapshot | None:
        real = hub_properties.electricity_price(self.hass, self._entry)
        real = round(real, 2) if real is not None else None
        variable = self._read_price(self._price_entity_ids["variable"])
        without_pv = self._read_price(self._price_entity_ids["without_pv"])
        if real is None and variable is None and without_pv is None:
            # None of the three price sources are configured at all --
            # don't bother shifting an all-fallback snapshot in.
            return None
        return PriceSnapshot(
            real=real if real is not None else _PRICE_FALLBACK,
            variable=variable if variable is not None else _PRICE_FALLBACK,
            without_pv=without_pv if without_pv is not None else _PRICE_FALLBACK,
        )

    def _read_price(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        value = read_float_state(self.hass.states.get(entity_id))
        return round(value, 2) if value is not None else None

    def _read_hourly(self, entity_id: str | None) -> float | None:
        """The entity's "last_period" attribute (previous full hour's
        total), not its live .state -- see const.py's own docstring on
        the ENTITY_KEY_*_HOURLY fields."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return _read_float_attribute(state, "last_period")

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
        self._attr_native_value = purchase.price_agreement_savings
        self._attr_extra_state_attributes = {
            "purchase_price_real": purchase.purchase_price_real,
            "purchase_price_variable": purchase.purchase_price_variable,
            "purchase_price_real_without_pv": purchase.purchase_price_real_without_pv,
            "purchase_price_variable_without_pv": purchase.purchase_price_variable_without_pv,
            "price_agreement_savings": purchase.price_agreement_savings,
            "battery_grid_charge_sum": battery.battery_grid_charge_sum,
            "battery_grid_discharge_sum": battery.battery_grid_discharge_sum,
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

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_savings_update(self._entry.entry_id),
                self._handle_update,
            )
        )
        self.async_write_ha_state()

    @callback
    def _handle_update(self, own_production_savings: float, battery_charging_savings: float) -> None:
        value = (own_production_savings, battery_charging_savings)[self._value_index]
        self._attr_native_value = value
        self.async_write_ha_state()
