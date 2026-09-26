"""Consumption averages: the PV device's household-consumption-average sensor.

See the package docstring in data/__init__.py for the general shape.
calculate_daytime_averages()/calculate_evening_average()/
calculate_full_day_average() below are the pure math, and
PwMngtConsumptionAveragesSensor is the Home Assistant wiring: once daily
(00:01, plus whenever select.pwm_pv_history_period_days changes) it reads
the just-completed calendar day's snapshots off
data/consumption_snapshots_data.py's PwMngtConsumptionSnapshotsSensor
(via properties/pv_properties.py), appends one entry to each of 3
rolling day-histories, and writes out the 8 averages those histories can
currently support.

Old Node-RED functions: "Beregn 8-16 & 6-9 & 17-06" (context list
"Dagtime_forbrug_liste"), "Beregn gennemsnitsforbrug 17-21" (context list
"Spidstimer_forbrug_liste"), "Beregn gennemsnitsforbrug døgn" (context
list "Døgn_forbrug_liste"). Node-RED updated these three at three
different times a day (16:01/21:01/00:01) so each figure appeared as
soon as that part of the day was over. PwMngt deliberately doesn't copy
that cadence -- see the "Trigger timing" note below -- all 3 histories
now advance together, once a day, from one complete calendar day's worth
of snapshots.
"""

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers import event, restore_state
from homeassistant.util import dt as dt_util

from ..devices import pv_device_info
from ..properties import pv_properties

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PV-device "consumption averages" sensor (read-only). Bundles 12
# household-consumption-average values as attributes on one diagnostic
# entity -- a "*_data" sensor, same convention as PwMngtBalanceDataSensor
# in data/balance_data.py.
#
# Only the first 8 have a real source and a calculate_* function below --
# they're the rolling N-day averages of data/consumption_snapshots_data.py's
# "property" category. The last 4 (pool_heating_consumption,
# pv_surplus_consumption, car_charger_consumption, heat_pump_consumption)
# stay None until Pool, PV Surplus, chargers and the ground-source heat
# pump exist as their own consumption_snapshots_data.py categories -- see
# that module's _CATEGORIES comment. Once one does, its average slots in
# here as a 4th rolling history the same way the first 3 do, not as a
# correction inside calculate_daytime_averages() -- see "daytime_average vs.
# daytime_average_adjusted" below for why the two aren't the same thing.
# ---------------------------------------------------------------------------

PwM_CONSUMPTION_AVERAGES_ATTRIBUTES: list[str] = [
    "daytime_average",
    "daytime_average_adjusted",
    "early_morning_average",
    "morning_average",
    "late_morning_average",
    "evening_average",
    "nighttime_average",
    "full_day_average",
    "pool_heating_consumption",
    "pv_surplus_consumption",
    "car_charger_consumption",
    "heat_pump_consumption",
]

PwM_CONSUMPTION_AVERAGES_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="consumption_averages_data",
        name="Consumption averages data",
        icon="mdi:chart-timeline-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]

# How many of the most recent daily entries a rolling history keeps, no
# matter what select.pwm_pv_history_period_days is currently set to --
# same "30" Node-RED capped each of its 3 context lists at.
_MAX_HISTORY_DAYS = 30


@dataclass
class PwMngtConsumptionAveragesStoredData(restore_state.ExtraStoredData):
    """What PwMngtConsumptionAveragesSensor restores across a Home
    Assistant restart: its 3 rolling day-histories (see the class
    docstring), so the averages don't rebuild from empty. Rides Home
    Assistant's restore-state cache via
    extra_restore_state_data/async_get_last_extra_data rather than
    extra_state_attributes, same reasoning as
    PwMngtBalanceStoredData in data/balance_data.py -- up to 30 small
    dicts per history would just clutter the entity (and get written to
    the recorder) for no benefit over the 8 averages already shown there.
    """

    daytime_history: list[dict[str, Any]]
    evening_history: list[dict[str, Any]]
    full_day_history: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "daytime_history": self.daytime_history,
            "evening_history": self.evening_history,
            "full_day_history": self.full_day_history,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtConsumptionAveragesStoredData":
        return cls(
            daytime_history=list(restored.get("daytime_history") or []),
            evening_history=list(restored.get("evening_history") or []),
            full_day_history=list(restored.get("full_day_history") or []),
        )


def _average(history: list[dict[str, Any]], field_name: str, period_days: int) -> float | None:
    """Mean of one field across the most recent min(period_days,
    len(history)) entries of history (newest-first) -- None if history is
    still empty. Same "shrink the period rather than divide by it
    anyway" rule Node-RED used (periode = liste.length when there isn't
    enough history yet).
    """
    if not history:
        return None
    window = history[: max(1, period_days)]
    return round(sum(entry[field_name] for entry in window) / len(window), 2)


def calculate_daytime_averages(
    history: list[dict[str, Any]], period_days: int
) -> dict[str, float | None]:
    """Pure calculation: turn the daytime rolling history (see
    PwMngtConsumptionAveragesSensor._append_daily_entries) into the 6
    averages it can supply -- early/morning/late-morning splits, the
    8-16 daytime figure, its (currently unimplemented) adjusted variant,
    and the night+evening-tail figure. No Home Assistant state touched,
    just a plain list of dicts in -- testable on its own, same shape as
    calculate_balance() in data/balance_data.py.

    Old Node-RED context list: "Dagtime_forbrug_liste".
    """
    return {
        "early_morning_average": _average(history, "early_morning", period_days),
        "morning_average": _average(history, "morning", period_days),
        "late_morning_average": _average(history, "late_morning", period_days),
        "daytime_average": _average(history, "daytime", period_days),
        "daytime_average_adjusted": _average(history, "daytime_adjusted", period_days),
        "nighttime_average": _average(history, "nighttime", period_days),
    }


def calculate_evening_average(history: list[dict[str, Any]], period_days: int) -> float | None:
    """Pure calculation: rolling average of the 17-21 evening figure.

    Old Node-RED context list: "Spidstimer_forbrug_liste".
    """
    return _average(history, "evening", period_days)


def calculate_full_day_average(history: list[dict[str, Any]], period_days: int) -> float | None:
    """Pure calculation: rolling average of the full-day figure.

    Old Node-RED context list: "Døgn_forbrug_liste" -- Node-RED sourced
    each day's value from a separate mirrored entity
    (sensor.strom_forbrug_husstand_pr_dag's last_period attribute).
    PwMngt doesn't need a mirror for this: data/consumption_snapshots_data.py
    already captures the same since-local-midnight daily total as its
    "property_kl_23_59" attribute (read via
    properties/pv_properties.py's consumption_snapshot_kl(), see
    PwMngtConsumptionAveragesSensor._append_daily_entries), so that's
    used directly instead.
    """
    return _average(history, "full_day", period_days)


class PwMngtConsumptionAveragesSensor(restore_state.RestoreEntity, SensorEntity):
    """A PwM PV-device sensor bundling the 12 consumption-average values
    (see PwM_CONSUMPTION_AVERAGES_ATTRIBUTES) as attributes. The first 8
    come from 3 rolling day-histories (see PwMngtConsumptionAveragesStoredData),
    each capped at _MAX_HISTORY_DAYS entries and averaged over
    select.pwm_pv_history_period_days (7/14/21/30) -- or however many
    days are actually available yet, whichever is smaller.

    Once a day (00:01, once that day's own consumption_snapshots_data.py
    attributes are all in for the day that just ended) a new entry is
    added to all 3 histories together and the 8 averages are
    recalculated. They're also recalculated (without adding a new day)
    whenever select.pwm_pv_history_period_days changes -- same
    msg.topic == "Historik" branch Node-RED had in each of its 3
    functions.

    Trigger timing, and why it differs from Node-RED: Node-RED advanced
    its 3 context lists at 3 different times (16:01/21:01/00:01) so each
    figure went stale for as little of the day as possible. Its
    "Dagtime_forbrug_liste" list actually bundled the daytime figures
    *and* the night+evening-tail figure ("dag_spids_nat") into one daily
    entry despite computing them 16:01 (daytime) vs. implicitly at the
    next run's 00:01-ish reset cycle (night) -- relying on global
    variables not being reset in between. That's a real, working
    property of Node-RED's specific reset ordering, not a design worth
    reproducing: it means "today's" daytime figure and "today's" night
    figure in the same entry didn't actually always describe the same
    calendar day. PwMngt reads a clean, already-midnight-baselined full
    day's worth of snapshots at once, so there's no reason to split the
    update across the day -- all 3 histories advance together once
    that day's snapshots are complete.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False
        self._attr_extra_state_attributes = {
            key: None for key in PwM_CONSUMPTION_AVERAGES_ATTRIBUTES
        }

        self._entry = entry
        self._daytime_history: list[dict[str, Any]] = []
        self._evening_history: list[dict[str, Any]] = []
        self._full_day_history: list[dict[str, Any]] = []

    @property
    def extra_restore_state_data(self) -> PwMngtConsumptionAveragesStoredData:
        """What to hand to Home Assistant's restore-state cache -- see
        PwMngtConsumptionAveragesStoredData."""
        return PwMngtConsumptionAveragesStoredData(
            daytime_history=self._daytime_history,
            evening_history=self._evening_history,
            full_day_history=self._full_day_history,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the 3 rolling histories from before a restart, then
        start the once-daily append and the history-period-change
        listener."""
        await super().async_added_to_hass()

        restored = await self.async_get_last_extra_data()
        if restored is not None:
            data = PwMngtConsumptionAveragesStoredData.from_dict(restored.as_dict())
            self._daytime_history = data.daytime_history
            self._evening_history = data.evening_history
            self._full_day_history = data.full_day_history

        self.async_on_remove(
            event.async_track_time_change(
                self.hass, self._handle_new_day, hour=0, minute=1, second=0
            )
        )

        history_period_entity_id = pv_properties.history_period_days_entity_id(
            self.hass, self._entry
        )
        if history_period_entity_id:
            self.async_on_remove(
                event.async_track_state_change_event(
                    self.hass,
                    [history_period_entity_id],
                    self._handle_history_period_changed,
                )
            )

        self._recompute()
        self._attr_available = True
        self.async_write_ha_state()

    @callback
    def _handle_history_period_changed(self, _event) -> None:
        """select.pwm_pv_history_period_days changed: recompute the 8
        averages over the new period without touching the histories
        themselves -- same as Node-RED's msg.topic == "Historik" branch."""
        self._recompute()
        self.async_write_ha_state()

    @callback
    def _handle_new_day(self, _now) -> None:
        """00:01 alarm: the calendar day that just ended has a complete
        set of data/consumption_snapshots_data.py "property_kl_*"
        attributes -- read them, append one entry to each of the 3
        rolling histories, then recompute the 8 averages."""
        snapshot_attributes = self._read_snapshot_attributes()
        if snapshot_attributes is None:
            return

        self._append_daily_entries(snapshot_attributes)
        self._recompute()
        self.async_write_ha_state()

    def _read_snapshot_attributes(self) -> dict[str, float] | None:
        """The just-completed day's "property" category snapshots off
        data/consumption_snapshots_data.py, via
        properties/pv_properties.py's consumption_snapshot_kl() -- None
        if any of the 8 needed samples isn't available yet (that sensor
        not registered yet, a fresh install, or Home Assistant restarted
        partway through the day)."""
        needed = ("6", "7", "8", "9", "16", "17", "21", "23_59")
        values: dict[str, float] = {}
        for time_key in needed:
            value = pv_properties.consumption_snapshot_kl(
                self.hass, self._entry, "property", time_key
            )
            if value is None:
                LOGGER.debug(
                    "PwMngt: property_kl_%s not available yet, skipping "
                    "today's consumption-average entry",
                    time_key,
                )
                return None
            values[time_key] = value
        return values

    def _append_daily_entries(self, kl: dict[str, float]) -> None:
        """Turn one day's property_kl_* snapshots into one entry per
        rolling history (newest first, capped at _MAX_HISTORY_DAYS) --
        the same shape calculate_daytime_averages()/
        calculate_evening_average()/calculate_full_day_average() read
        back."""
        today = dt_util.now().date().isoformat()

        daytime = round(kl["16"] - kl["8"], 2)
        # daytime_adjusted has nothing to subtract yet -- see the
        # PwM_CONSUMPTION_AVERAGES_ATTRIBUTES comment above. Same
        # "equals the uncorrected value until the real logic exists"
        # placeholder as balance_15_min_corrected in data/balance_data.py.
        daytime_adjusted = daytime
        nighttime = round((kl["23_59"] - kl["17"]) + kl["6"], 2)

        self._daytime_history.insert(
            0,
            {
                "date": today,
                "early_morning": round(kl["7"] - kl["6"], 2),
                "morning": round(kl["8"] - kl["7"], 2),
                "late_morning": round(kl["9"] - kl["8"], 2),
                "daytime": daytime,
                "daytime_adjusted": daytime_adjusted,
                "nighttime": nighttime,
            },
        )
        self._daytime_history = self._daytime_history[:_MAX_HISTORY_DAYS]

        self._evening_history.insert(
            0, {"date": today, "evening": round(kl["21"] - kl["17"], 2)}
        )
        self._evening_history = self._evening_history[:_MAX_HISTORY_DAYS]

        self._full_day_history.insert(0, {"date": today, "full_day": kl["23_59"]})
        self._full_day_history = self._full_day_history[:_MAX_HISTORY_DAYS]

    def _recompute(self) -> None:
        """Recalculate the 8 real averages from the 3 rolling histories
        over the currently-selected period, and write them into this
        entity's attributes. The remaining 4 (pool/PV Surplus/charger/
        heat pump) stay whatever they already were -- None until those
        segments exist."""
        period_days = pv_properties.history_period_days(self.hass, self._entry)
        self._attr_extra_state_attributes.update(
            calculate_daytime_averages(self._daytime_history, period_days)
        )
        self._attr_extra_state_attributes["evening_average"] = calculate_evening_average(
            self._evening_history, period_days
        )
        self._attr_extra_state_attributes["full_day_average"] = calculate_full_day_average(
            self._full_day_history, period_days
        )
