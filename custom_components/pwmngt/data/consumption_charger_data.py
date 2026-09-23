"""Charger consumption: since-local-midnight consumption for a single
charger device, tracked from a raw source that resets itself whenever a
new charging session starts.

See the package docstring in data/__init__.py for the general shape. A
charger's own added-energy counter (e.g. a Wallbox portal's
"added_energy" sensor) resets to a low value at the start of every
charging session -- possibly several times a day -- so a simple
raw-minus-midnight-baseline snapshot (as data/consumption_snapshots_data.py
uses for its non-resetting sources) would go negative the moment a
session resets it.

Instead, PwMngtChargerConsumptionSensor keeps a running total updated on
every state change of the source entity: accumulate() below tells a
genuine reset (the source dropped -- you can't charge a negative
amount) apart from an ordinary rise, and folds each reading in
accordingly.

The source entity_id is not part of entry.options[CONF_ENTITY_MAP] like
every other PwMngt source -- see the "consumption_source_entity" text
entity in text.py (PwM_CHARGER_CONFIG_TEXTS) for why.

Whenever there's no same-day running total to resume from -- a source
configured for the first time, a fresh install, or a restored total
from a previous day (Home Assistant was down across local midnight) --
_backfill_from_history() reconstructs it by replaying every recorded
state change since local midnight through accumulate(), instead of
starting blank at 0.0.
"""

import logging
from collections.abc import Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..devices import charger_device_info
from ..helpers.history_helper import fetch_state_changes_since
from ..helpers.state_helper import read_float_state

LOGGER = logging.getLogger(__name__)

# Only these two snapshots are needed -- unlike
# data/consumption_snapshots_data.py's property tracking (8 times/day),
# only kl 8 and 16 feed the household daytime-consumption correction.
# Add more here if a future use needs them; nothing else assumes exactly
# two.
_SAMPLE_TIMES: list[tuple[str, int, int]] = [
    ("8", 8, 0),
    ("16", 16, 0),
]


def accumulate(total_so_far: float, last_raw: float | None, new_raw: float) -> float:
    """Pure calculation: fold one new raw reading into a running
    since-midnight total, treating a decrease as a session reset rather
    than negative consumption.

    - last_raw is None (first-ever reading, or just after a source
      change): nothing to add yet -- this reading becomes the reference
      point the next call compares against.
    - new_raw >= last_raw: ordinary rise (or no change) -- add the
      difference.
    - new_raw < last_raw: the source reset (a new session started). The
      old session's consumption was already folded in while it was
      rising, so the new, lower reading is itself the new session's
      consumption so far -- add it on top rather than subtracting a
      negative delta.
    """
    if last_raw is None:
        return total_so_far
    if new_raw >= last_raw:
        return round(total_so_far + (new_raw - last_raw), 3)
    return round(total_so_far + new_raw, 3)


class PwMngtChargerConsumptionSensor(RestoreEntity, SensorEntity):
    """A charger device's "Consumption" sensor (see PwM_CHARGER_SENSORS'
    "consumption" key in sensor.py) -- native value is the running
    since-local-midnight kWh total from accumulate() above, with kl_8/
    kl_16 snapshot attributes for the future household-consumption
    correction (data/consumption_averages_data.py's planned
    car_charger_consumption -- see total_charger_consumption() below).

    Tracks its source indirectly: resolves this charger's own
    "consumption_source_entity" text entity (PwM_CHARGER_CONFIG_TEXTS in
    text.py) via the entity registry, tracks whichever entity_id is
    currently typed into it, switching cleanly to a new source if it
    changes, and staying unavailable while it's empty.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        charger: dict,
    ) -> None:
        self.entity_description = description
        self._entry = entry
        self._charger = charger
        self._attr_unique_id = f"{entry.entry_id}_{charger['id']}_{description.key}"
        self._attr_device_info = charger_device_info(entry, charger)
        self._attr_native_value = None
        self._attr_available = False
        self._attr_extra_state_attributes = {
            f"kl_{time_key}": None for time_key, _, _ in _SAMPLE_TIMES
        }

        self._source_entity_id: str | None = None
        self._last_raw: float | None = None
        self._total_since_midnight: float = 0.0
        # ISO date _total_since_midnight is valid for -- None until a
        # restore or a history backfill establishes one for today; see
        # _bind_source()/_backfill_from_history() and _handle_midnight().
        self._total_date: str | None = None
        self._unsub_source: Callable[[], None] | None = None

    @property
    def _source_text_unique_id(self) -> str:
        return (
            f"{self._entry.entry_id}_{self._charger['id']}_"
            "consumption_source_entity"
        )

    async def async_added_to_hass(self) -> None:
        """Restore the running total and today's snapshots (if any),
        then start tracking the local-midnight/sample alarms and this
        charger's configured source entity."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            None,
            "unknown",
            "unavailable",
        ):
            try:
                self._total_since_midnight = float(last_state.state)
            except ValueError:
                self._total_since_midnight = 0.0
            for time_key, _, _ in _SAMPLE_TIMES:
                attr_key = f"kl_{time_key}"
                if attr_key in last_state.attributes:
                    self._attr_extra_state_attributes[attr_key] = (
                        last_state.attributes[attr_key]
                    )
            raw = last_state.attributes.get("_last_raw")
            if raw is not None:
                try:
                    self._last_raw = float(raw)
                except (TypeError, ValueError):
                    self._last_raw = None
            self._total_date = last_state.attributes.get("_total_date")

        self._attr_native_value = self._total_since_midnight
        self._attr_extra_state_attributes["_last_raw"] = self._last_raw
        self._attr_extra_state_attributes["_total_date"] = self._total_date

        self.async_on_remove(
            async_track_time_change(
                self.hass, self._handle_midnight, hour=0, minute=0, second=0
            )
        )
        for time_key, hour, minute in _SAMPLE_TIMES:
            self.async_on_remove(
                async_track_time_change(
                    self.hass,
                    self._make_sample_handler(time_key),
                    hour=hour,
                    minute=minute,
                    second=0,
                )
            )

        self._start_source_tracking()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_source is not None:
            self._unsub_source()
            self._unsub_source = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_midnight(self, _now) -> None:
        """Local-midnight alarm: start a fresh since-midnight total.
        Keeps _last_raw as-is (a charging session may be running right
        through midnight) so the next reading is still a correct delta
        rather than being treated as the first-ever reading."""
        self._total_since_midnight = 0.0
        self._total_date = dt_util.now().date().isoformat()
        self._attr_native_value = 0.0
        self._attr_extra_state_attributes["_total_date"] = self._total_date
        self.async_write_ha_state()

    def _make_sample_handler(self, time_key: str):
        @callback
        def _handler(_now) -> None:
            self._attr_extra_state_attributes[f"kl_{time_key}"] = (
                self._total_since_midnight
            )
            self.async_write_ha_state()

        return _handler

    def _start_source_tracking(self) -> None:
        """Resolve this charger's "consumption_source_entity" text
        entity, then track whichever raw entity_id it currently holds --
        re-subscribing if the user changes it later. Same
        registry-lookup-with-retry shape as PwMngtText's
        _start_visibility_tracking in text.py, since the text platform
        may not have finished registering yet on first-ever setup."""
        registry = er.async_get(self.hass)

        def _subscribe_to_text(text_entity_id: str) -> None:
            @callback
            def _on_text_change(event) -> None:
                new_state = event.data.get("new_state")
                self._set_source(new_state.state if new_state else None)

            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [text_entity_id], _on_text_change
                )
            )
            state = self.hass.states.get(text_entity_id)
            self._set_source(state.state if state else None)

        text_entity_id = registry.async_get_entity_id(
            "text", DOMAIN, self._source_text_unique_id
        )
        if text_entity_id:
            _subscribe_to_text(text_entity_id)
            return

        @callback
        def _retry(_now) -> None:
            retried = registry.async_get_entity_id(
                "text", DOMAIN, self._source_text_unique_id
            )
            if retried:
                _subscribe_to_text(retried)
            else:
                LOGGER.warning(
                    "Could not find the consumption-source text entity "
                    "(%s) for %s; it will stay unavailable",
                    self._source_text_unique_id,
                    self.entity_id,
                )
                self._attr_available = True
                self.async_write_ha_state()

        self.async_on_remove(async_call_later(self.hass, 2, _retry))

    @callback
    def _set_source(self, entity_id: str | None) -> None:
        """(Re-)point tracking at entity_id -- the entity_id currently
        typed into this charger's consumption_source_entity text field,
        or None/"" while unconfigured. Resets _last_raw so the next
        reading is a fresh reference point. Binding to a resolved
        entity_id happens in _bind_source() instead, since backfilling
        from history needs to await a Recorder query and this callback
        can't."""
        entity_id = entity_id or None
        if entity_id == self._source_entity_id:
            return

        if self._unsub_source is not None:
            self._unsub_source()
            self._unsub_source = None

        self._source_entity_id = entity_id
        self._last_raw = None
        self._attr_extra_state_attributes["_last_raw"] = None

        if entity_id is None:
            self._attr_available = True
            self.async_write_ha_state()
            return

        self.hass.async_create_task(self._bind_source(entity_id))

    async def _bind_source(self, entity_id: str) -> None:
        """Finish pointing tracking at entity_id: backfill today's
        running total from Recorder history first if we don't already
        have one for today (a fresh entity, a source configured for the
        first time, or a restored total that's from a previous day), then
        prime _last_raw from the source's current value and start
        tracking its live state changes."""
        if self._source_entity_id != entity_id:
            return  # source changed again before this task got to run

        today = dt_util.now().date().isoformat()
        if self._total_date != today:
            await self._backfill_from_history(entity_id, today)

        if self._source_entity_id != entity_id:
            return  # source changed again while backfill was running

        @callback
        def _on_source_change(event) -> None:
            new_state = event.data.get("new_state")
            raw = read_float_state(new_state)
            if raw is None:
                return
            self._total_since_midnight = accumulate(
                self._total_since_midnight, self._last_raw, raw
            )
            self._last_raw = raw
            self._attr_native_value = self._total_since_midnight
            self._attr_extra_state_attributes["_last_raw"] = raw
            self._attr_available = True
            self.async_write_ha_state()

        self._unsub_source = async_track_state_change_event(
            self.hass, [entity_id], _on_source_change
        )

        # Prime _last_raw from the source's current value if backfill
        # above didn't already set one, so the next change event gives a
        # correct delta instead of being read as a "first-ever reading".
        if self._last_raw is None:
            raw = read_float_state(self.hass.states.get(entity_id))
            if raw is not None:
                self._last_raw = raw
                self._attr_extra_state_attributes["_last_raw"] = raw

        self._attr_available = True
        self.async_write_ha_state()

    async def _backfill_from_history(self, entity_id: str, today: str) -> None:
        """Reconstruct today's running since-midnight total (and any
        already-passed kl_8/kl_16 snapshots) from Recorder history, for
        when there's no same-day total to resume from -- a source
        configured for the first time, a fresh install, or a restored
        total from a previous day. Replays every recorded change since
        local midnight through accumulate(), in order, so a reset
        partway through today is still handled correctly. Leaves
        everything at its default (0.0, no snapshots) if Recorder has
        nothing to offer.
        """
        local_midnight = dt_util.start_of_local_day()
        changes = await fetch_state_changes_since(
            self.hass, entity_id, dt_util.as_utc(local_midnight)
        )
        if not changes:
            return

        sample_times = [
            (time_key, local_midnight.replace(hour=hour, minute=minute))
            for time_key, hour, minute in _SAMPLE_TIMES
        ]
        now = dt_util.now()
        total = 0.0
        last_raw: float | None = None
        sample_idx = 0

        for state in changes:
            changed_at = dt_util.as_local(state.last_changed)
            while (
                sample_idx < len(sample_times)
                and sample_times[sample_idx][1] <= changed_at
            ):
                time_key, _ = sample_times[sample_idx]
                self._attr_extra_state_attributes[f"kl_{time_key}"] = round(total, 3)
                sample_idx += 1

            raw = read_float_state(state)
            if raw is not None:
                total = accumulate(total, last_raw, raw)
                last_raw = raw

        while sample_idx < len(sample_times) and sample_times[sample_idx][1] <= now:
            time_key, _ = sample_times[sample_idx]
            self._attr_extra_state_attributes[f"kl_{time_key}"] = round(total, 3)
            sample_idx += 1

        self._total_since_midnight = total
        self._last_raw = last_raw
        self._total_date = today
        self._attr_extra_state_attributes["_total_date"] = today
        self._attr_extra_state_attributes["_last_raw"] = last_raw
        self._attr_native_value = total

        LOGGER.info(
            "PwMngt: backfilled %s's since-midnight consumption from "
            "history (%.3f kWh so far today)",
            entity_id,
            total,
        )


def total_charger_consumption(hass, entry: ConfigEntry, chargers: list[dict], time_key: str) -> float:
    """Sum the kl_<time_key> snapshot across every configured charger (0
    for one whose "consumption_source_entity" is empty/unset) -- the
    native replacement for Claus's combined Forbrug_billadere at that
    time of day. Only used inside a calculation (see
    data/consumption_averages_data.py's planned car_charger_consumption)
    -- never presented as its own entity; neither Kasper's nor Claus's
    Node-RED exposes the summed value either.
    """
    registry = er.async_get(hass)
    total = 0.0
    for charger in chargers:
        unique_id = f"{entry.entry_id}_{charger['id']}_consumption"
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            continue
        value = state.attributes.get(f"kl_{time_key}")
        if isinstance(value, (int, float)):
            total += value
    return round(total, 3)
