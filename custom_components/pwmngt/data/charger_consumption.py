"""Charger consumption: since-local-midnight consumption for a single
charger device, tracked from a raw source that resets itself whenever a
new charging session starts.

See the package docstring in data/__init__.py for the general shape.
This is deliberately NOT built the same way as data/consumption_status.py:
that module assumes its source is a genuine, never-resetting lifetime
counter (see its own docstring), and computes "since midnight" as a
simple raw-minus-baseline snapshot taken at a handful of fixed times a
day. A charger's own added-energy counter (e.g. a Wallbox portal's
"added_energy" sensor) resets to a low value every time a new charging
session starts -- possibly several times a day -- so a baseline captured
once at midnight would go stale (and the raw-minus-baseline subtraction
would go negative) the moment the first session of the day resets it.

Instead, PwMngtChargerConsumptionSensor keeps a running total that's
updated on every state change of the source entity, using accumulate()
below to tell a genuine reset (the source dropped -- you can't charge a
negative amount, so a drop always means a new session started, never
negative consumption) apart from an ordinary rise. This is the same
"decrease means reset" handling Home Assistant's own state_class
"total_increasing" sensors (and its utility_meter/statistics machinery)
already build in -- we just have to do it ourselves here, since nothing
built into Home Assistant exposes that running total as an entity for us
directly against an arbitrary source.

The source entity_id itself is not part of entry.options[CONF_ENTITY_MAP]
like every other PwMngt source -- see the "consumption_source_entity"
text entity in text.py (PwM_CHARGER_CONFIG_TEXTS) for why.
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

from ..const import DOMAIN
from ..devices import charger_device_info
from ..helpers.state_helper import read_float_state

LOGGER = logging.getLogger(__name__)

# Only these two snapshots are needed -- unlike data/consumption_status.py's
# property tracking (8 fixed times/day), Claus's Node-RED only ever reads
# the charger-consumption equivalent (Forbrug_billadere) at kl 8 and 16,
# for the household daytime-consumption correction. Add more here if a
# future use needs them; nothing else about this module assumes exactly
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
      change): nothing to add yet -- this reading only becomes the new
      reference point the next call compares against.
    - new_raw >= last_raw: ordinary rise (or no change) -- add the
      difference.
    - new_raw < last_raw: the source reset (a new charging session
      started partway through the day). The old session's remaining
      consumption was already folded in while it was rising, so the new,
      lower reading is itself the new session's consumption so far --
      add it on top rather than subtracting a negative delta.
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
    correction (data/consumption.py's planned car_charger_consumption --
    see total_charger_consumption() below).

    Tracks its source indirectly: it resolves this charger's own
    "consumption_source_entity" text entity (PwM_CHARGER_CONFIG_TEXTS in
    text.py) via the entity registry, reads whichever entity_id is
    currently typed into it, and tracks that entity's state changes --
    switching cleanly to a new source if the text entity's value changes,
    and staying unavailable (like data/consumption_status.py does for an
    unconfigured category) while it's empty.
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

        self._attr_native_value = self._total_since_midnight
        self._attr_extra_state_attributes["_last_raw"] = self._last_raw

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
        self._attr_native_value = 0.0
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
        re-subscribing automatically if the user changes it later. Same
        registry-lookup-with-retry shape as PwMngtText's
        _start_visibility_tracking in text.py, since the text platform
        may not have finished registering yet on a first-ever setup."""
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
        or None/"" while it's unconfigured. Resets _last_raw so the next
        reading is treated as a fresh reference point, not a jump from
        whatever the previous source's last value was."""
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

        # Prime _last_raw with whatever the source already reads right
        # now, so the very next change event gives a correct delta
        # instead of being silently swallowed as a "first-ever reading".
        raw = read_float_state(self.hass.states.get(entity_id))
        if raw is not None:
            self._last_raw = raw
            self._attr_extra_state_attributes["_last_raw"] = raw

        self._attr_available = True
        self.async_write_ha_state()


def total_charger_consumption(hass, entry: ConfigEntry, chargers: list[dict], time_key: str) -> float:
    """Sum the kl_<time_key> snapshot across every configured charger
    (0 for one whose "consumption_source_entity" is empty/unset) -- the
    native replacement for Claus's combined Forbrug_billadere at that
    same time of day. Only used inside a calculation (see
    data/consumption.py's planned car_charger_consumption) -- never
    presented as its own entity, per Kasper's call (neither his nor
    Claus's Node-RED presents the summed value anywhere either, only
    consumes it internally).
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
