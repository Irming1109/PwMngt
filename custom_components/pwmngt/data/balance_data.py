"""Balance data: the PwM hub's live power-balance sensor.

See the package docstring in data/__init__.py for the general shape.
calculate_balance() below is the pure math, and PwMngtBalanceDataSensor
is the Home Assistant wiring: a 10-second trigger that feeds it samples
and writes out what it returns.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_ENTITY_MAP,
    DOMAIN,
    ENTITY_KEY_BATTERY_POWER,
    ENTITY_KEY_GRID_POWER,
    RESTORE_MAX_AGE,
)
from ..devices import CHARGERS, hub_device_info
from ..helpers.state_helper import read_float_state
from ..properties import hub_properties

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hub-level "power balance" sensor. Bundles 7 net-power-balance values
# (different averaging windows) as attributes on one entity instead of 7
# separate sensors -- none need their own History/Statistics graph.
#
# Naming convention: a "*_data" key/name means the value comes from an
# ongoing internal trigger (here, a 10-second timer) rather than
# mirroring one external entity -- same convention as
# PwM_CONSUMPTION_AVERAGES_SENSORS in data/consumption_averages_data.py.
#
# Old key="balance_30_sek" (sensor.balance_30_sek) -> attribute "balance_30_sec"
# Old key="balance_1_min" (sensor.balance_1_min) -> attribute "balance_1_min"
# Old key="balance_5_min" (sensor.balance_5_min) -> attribute "balance_5_min"
# Old key="balance_15_min" (sensor.balance_15_min) -> attribute "balance_15_min"
# Old key="balance_15_min_korrigeret" (sensor.balance_15_min_korrigeret)
#   -> attribute "balance_15_min_corrected"
# Old key="balance_15_min_korrigeret_m_ladere"
#   (sensor.balance_15_min_korrigeret_m_ladere)
#   -> attribute "balance_15_min_corrected_with_chargers"
# Old key="balance_30_min" (sensor.balance_30_min) -> attribute "balance_30_min"
#
# "_corrected" is currently just "balance_15_min" (mining correction not
# implemented yet). "_corrected_with_chargers" is genuinely computed from
# PwM Charger1/2's Power sensors, but reads the same until those sensors
# have live values (still scaffolding -- see PwM_CHARGER_SENSORS in
# sensor.py).
# ---------------------------------------------------------------------------
PwM_BALANCE_DATA_ATTRIBUTES: list[str] = [
    "balance_30_sec",
    "balance_1_min",
    "balance_5_min",
    "balance_15_min",
    "balance_15_min_corrected",
    "balance_15_min_corrected_with_chargers",
    "balance_30_min",
]

PwM_BALANCE_DATA_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="balance_data",
        name="Balance data",
        icon="mdi:scale-balance",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement="W",
    ),
]

# How often PwMngtBalanceDataSensor samples Grid+Battery power, and how many
# of those samples each rolling window covers (10 s/sample, so 3 = 30 sec,
# 180 = 30 min).
_BALANCE_SAMPLE_INTERVAL = timedelta(seconds=10)
_BALANCE_WINDOW_SAMPLES: dict[str, int] = {
    "balance_30_sec": 3,
    "balance_1_min": 6,
    "balance_5_min": 30,
    "balance_15_min": 90,
    "balance_30_min": 180,
}


@dataclass
class PwMngtBalanceStoredData(ExtraStoredData):
    """What PwMngtBalanceDataSensor restores across a Home Assistant
    restart: its raw sample history, so the rolling averages don't
    rebuild from empty (see RESTORE_MAX_AGE for the cutoff). Rides Home
    Assistant's restore-state cache via
    extra_restore_state_data/async_get_last_extra_data rather than
    extra_state_attributes, since up to 180 raw numbers per list would
    just clutter the entity (and get written to the recorder) for no
    benefit.
    """

    samples: list[float]
    charger_samples: list[float]
    last_sample_time: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "charger_samples": self.charger_samples,
            "last_sample_time": self.last_sample_time,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "PwMngtBalanceStoredData":
        return cls(
            samples=list(restored.get("samples") or []),
            charger_samples=list(restored.get("charger_samples") or []),
            last_sample_time=restored.get("last_sample_time"),
        )


def calculate_balance(
    status_samples: list[float],
    charger_samples: list[float],
) -> dict[str, float]:
    """Pure calculation: turn raw samples into the 7 balance attributes.

    No Home Assistant state touched -- give it two plain lists (most
    recent sample last, up to 180 of them each) and it returns a plain
    dict of the 7 PwM_BALANCE_DATA_ATTRIBUTES values, testable on its
    own.

    Each average divides by its full window size even before enough
    samples have accumulated, so early readings are pulled toward zero
    until the window fills -- in practice this only shows on a fresh
    install, or after a gap long enough that PwMngtBalanceDataSensor
    discards its restored history (see RESTORE_MAX_AGE); a normal
    restart resumes from the restored sample history instead.
    """
    values = {
        key: round(-(sum(status_samples[-window:]) / window))
        for key, window in _BALANCE_WINDOW_SAMPLES.items()
    }
    values["balance_15_min_corrected"] = values["balance_15_min"]

    corrected_window_samples = _BALANCE_WINDOW_SAMPLES["balance_15_min"]
    combined = sum(status_samples[-corrected_window_samples:]) - sum(
        charger_samples[-corrected_window_samples:]
    )
    values["balance_15_min_corrected_with_chargers"] = round(
        -(combined / corrected_window_samples)
    )

    return values


class PwMngtBalanceDataSensor(RestoreEntity, SensorEntity):
    """A PwM hub sensor bundling the 7 balance values above as attributes.

    Every 10 seconds, a timer trigger (_sample) reads Grid power plus
    Battery power (skipped while hub_properties.is_forced_charging()
    reports True), extends this entity's rolling sample history, then
    hands it to calculate_balance() for the math and writes the result
    out.

    The rolling sample history survives a Home Assistant restart -- see
    PwMngtBalanceStoredData and RESTORE_MAX_AGE -- so the longer windows
    (5/15/30 min) don't read artificially low for up to half an hour
    after every restart while they refill from empty.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False
        self._attr_extra_state_attributes = {
            key: None for key in PwM_BALANCE_DATA_ATTRIBUTES
        }
        self._samples: deque[float] = deque(maxlen=180)
        self._charger_samples: deque[float] = deque(maxlen=180)
        self._last_sample_time: datetime | None = None
        # PwM Charger1/2's own Power sensors, resolved by unique_id (see
        # _read_charger_power_total) since they're not entity_map fields
        # either. Missing/not-yet-live chargers contribute 0.
        self._charger_unique_ids = [
            f"{entry.entry_id}_{charger['id']}_power" for charger in CHARGERS
        ]
        self._charger_entity_ids: list[str | None] = [None] * len(
            self._charger_unique_ids
        )

        self._entry = entry
        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._grid_entity_id = entity_map.get(ENTITY_KEY_GRID_POWER) or None
        self._battery_entity_id = entity_map.get(ENTITY_KEY_BATTERY_POWER) or None

        if not self._grid_entity_id or not self._battery_entity_id:
            LOGGER.warning(
                "PwMngt: no source entity configured for '%s' (set Grid "
                "and Battery power under Options -> Solar PV Plant), it "
                "will stay unavailable",
                self.entity_description.key,
            )

    @property
    def extra_restore_state_data(self) -> PwMngtBalanceStoredData:
        """What to hand to Home Assistant's restore-state cache --
        see PwMngtBalanceStoredData."""
        return PwMngtBalanceStoredData(
            samples=list(self._samples),
            charger_samples=list(self._charger_samples),
            last_sample_time=(
                self._last_sample_time.isoformat()
                if self._last_sample_time
                else None
            ),
        )

    async def async_added_to_hass(self) -> None:
        """Restore the sample history from before a restart (if recent
        enough, see RESTORE_MAX_AGE), then start sampling every 10
        seconds, if Grid and Battery power are configured.
        """
        await super().async_added_to_hass()

        restored = await self.async_get_last_extra_data()
        if restored is not None:
            self._restore_samples(PwMngtBalanceStoredData.from_dict(restored.as_dict()))

        if not self._grid_entity_id or not self._battery_entity_id:
            return

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._sample, _BALANCE_SAMPLE_INTERVAL
            )
        )
        self._sample(None)

    def _restore_samples(self, data: PwMngtBalanceStoredData) -> None:
        """Repopulate the rolling sample history from a previous run's
        PwMngtBalanceStoredData, unless it's too old to trust (see
        RESTORE_MAX_AGE) -- e.g. a long power outage, not just a normal
        restart.
        """
        if data.last_sample_time is None:
            return

        last_sample_time = dt_util.parse_datetime(data.last_sample_time)
        if last_sample_time is None:
            return

        age = dt_util.utcnow() - last_sample_time
        if age > RESTORE_MAX_AGE:
            LOGGER.info(
                "PwMngt: balance sample history is %s old, too stale to "
                "restore -- starting fresh",
                age,
            )
            return

        self._samples.extend(data.samples[-180:])
        self._charger_samples.extend(data.charger_samples[-180:])
        self._last_sample_time = last_sample_time
        LOGGER.info(
            "PwMngt: restored %d balance sample(s) from before restart (%s old)",
            len(self._samples),
            age,
        )

    @callback
    def _sample(self, _now) -> None:
        """Trigger callback: read the latest raw readings, extend the
        rolling sample history, then hand it all to calculate_balance()
        for the math and write its result out.
        """
        grid_power = read_float_state(self.hass.states.get(self._grid_entity_id))
        battery_power = read_float_state(
            self.hass.states.get(self._battery_entity_id)
        )
        if grid_power is None or battery_power is None:
            return

        status = grid_power
        if not hub_properties.is_forced_charging(self.hass, self._entry):
            status += battery_power
        self._samples.append(status)
        self._charger_samples.append(self._read_charger_power_total())
        self._last_sample_time = dt_util.utcnow()

        values = calculate_balance(list(self._samples), list(self._charger_samples))

        self._attr_native_value = values["balance_30_sec"]
        self._attr_extra_state_attributes = values
        self._attr_available = True
        self.async_write_ha_state()

    def _read_charger_power_total(self) -> float:
        """Sum of all configured chargers' current Power reading.

        Resolved lazily by unique_id, since PwM Charger1/2's entity_ids
        aren't predictable ahead of registration. A charger with no live
        reading yet (still scaffolding -- see PwM_CHARGER_SENSORS in
        sensor.py) contributes 0 rather than blocking the whole
        calculation.
        """
        registry = er.async_get(self.hass)
        total = 0.0
        for index, unique_id in enumerate(self._charger_unique_ids):
            entity_id = self._charger_entity_ids[index]
            if not entity_id:
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
                self._charger_entity_ids[index] = entity_id
            if not entity_id:
                continue
            total += read_float_state(self.hass.states.get(entity_id)) or 0.0
        return total
