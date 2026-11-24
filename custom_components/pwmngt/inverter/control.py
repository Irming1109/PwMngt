"""Inverter control -- the switch that lets PwMngt write to the inverter
itself, and the one place that decides when it writes (issue #124).

No Node-RED port of its own: it takes over from Claus's translator nodes
(see inverter/writer.py), which react to every change of battery_command
and Battery data. Those translators are not run alongside it.

What it does while the switch is on:
- battery_command: written through the brand's writer at every change,
  and once at startup (and when the switch is turned on), so a relay or a
  charge is never left hanging after a restart.
- min_soc (the state of Battery data): only when it differs at least
  MIN_SOC_MIN_CHANGE points from what the inverter has, and at most once
  per MIN_SOC_WRITE_INTERVAL -- a later change waits for the interval and
  writes the newest value then. The inverter keeps min SoC in permanent
  memory (flash/EEPROM), so every write counts. At startup nothing is
  written when the inverter already has the value.
- Each write to the inverter gets an entry in Home Assistant's logbook,
  as the Node-RED translators did.

Turned off, the writer leaves the inverter in normal operation once
(InverterWriter.async_release()) and nothing more is written.

Which writer is used follows the wizard's inverter type (INVERTER_WRITERS);
a brand without one -- or whose fields aren't filled in -- writes nothing,
and the switch's "status" attribute says why.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.logbook import async_log_entry
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from ..const import DOMAIN, INVERTER_TYPE_KOSTAL
from ..devices import pv_device_info
from ..helpers.state_helper import read_float_state
from ..properties import pv_properties
from .kostal import KostalWriter
from .writer import InverterWriter

LOGGER = logging.getLogger(__name__)

# Inverter type (select.<pv>_inverter_type's option) -> its writer.
# Growatt follows in issue #126.
INVERTER_WRITERS: dict[str, type[InverterWriter]] = {
    INVERTER_TYPE_KOSTAL: KostalWriter,
}

# Issue #1's limits on min SoC writes (spares the inverter's flash/EEPROM).
MIN_SOC_WRITE_INTERVAL = timedelta(minutes=10)
MIN_SOC_MIN_CHANGE = 1.0

# The switch's "status" attribute (translated in strings.json under
# entity.switch.inverter_control.state_attributes.status).
STATUS_OFF = "off"
STATUS_ACTIVE = "active"
STATUS_NO_WRITER = "no_writer"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_WRITE_FAILED = "write_failed"

ATTR_STATUS = "status"
ATTR_LAST_COMMAND = "last_command"
ATTR_LAST_MIN_SOC = "last_min_soc"
ATTR_LAST_MIN_SOC_WRITE = "last_min_soc_write"

_INVALID_STATES = frozenset({"unknown", "unavailable", ""})


def should_write_min_soc(
    min_soc: float,
    inverter_min_soc: float | None,
    last_written: float | None,
) -> bool:
    """Whether min_soc differs enough to write: compared with what the
    inverter has, or -- when that can't be read -- with what was written
    last. Neither known: write, to be sure."""
    reference = inverter_min_soc if inverter_min_soc is not None else last_written
    return reference is None or abs(min_soc - reference) >= MIN_SOC_MIN_CHANGE


def next_min_soc_write(last_write: datetime | None, now: datetime) -> datetime | None:
    """When the next min SoC write may happen, or None when it may now."""
    if last_write is None or now - last_write >= MIN_SOC_WRITE_INTERVAL:
        return None
    return last_write + MIN_SOC_WRITE_INTERVAL


class PwMngtInverterControlSwitch(SwitchEntity, RestoreEntity):
    """Inverter control -- see the module docstring."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, description: SwitchEntityDescription, entry: ConfigEntry) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_is_on = False
        self._entry = entry
        self._status = STATUS_OFF
        # What was last written to the inverter (None = nothing since the
        # switch was turned on or Home Assistant started).
        self._last_command: str | None = None
        self._last_min_soc: float | None = None
        self._last_min_soc_write: datetime | None = None
        self._cancel_pending_min_soc = None
        # Writes are service calls; one sync at a time keeps them in order.
        self._lock = asyncio.Lock()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_STATUS: self._status,
            ATTR_LAST_COMMAND: self._last_command,
            ATTR_LAST_MIN_SOC: self._last_min_soc,
            ATTR_LAST_MIN_SOC_WRITE: (
                self._last_min_soc_write.isoformat() if self._last_min_soc_write else None
            ),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == STATE_ON
            # The interval holds across a restart too.
            last_write = last_state.attributes.get(ATTR_LAST_MIN_SOC_WRITE)
            self._last_min_soc_write = dt_util.parse_datetime(last_write) if last_write else None
        self._status = STATUS_ACTIVE if self._attr_is_on else STATUS_OFF

        trigger_entity_ids = [
            entity_id
            for entity_id in (
                pv_properties.battery_command_entity_id(self.hass, self._entry),
                pv_properties.battery_data_entity_id(self.hass, self._entry),
            )
            if entity_id
        ]
        if trigger_entity_ids:
            self.async_on_remove(
                event.async_track_state_change_event(self.hass, trigger_entity_ids, self._handle_change)
            )
        self.async_on_remove(self._cancel_pending)

        # The inverter's own entities may not be loaded before Home
        # Assistant has started.
        if self.hass.is_running:
            self.hass.async_create_task(self._async_sync(startup=True))
        else:
            # Removing a once-listener that has already fired makes Home
            # Assistant log an error, so it is only removed while waiting.
            cancel_started = None

            async def _sync_when_started(_event) -> None:
                nonlocal cancel_started
                cancel_started = None
                await self._async_sync(startup=True)

            cancel_started = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, _sync_when_started
            )

            @callback
            def _remove_started_listener() -> None:
                if cancel_started is not None:
                    cancel_started()

            self.async_on_remove(_remove_started_listener)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self._attr_is_on:
            return
        LOGGER.info("%s turned on", self.entity_id)
        self._attr_is_on = True
        self._last_command = None
        self._last_min_soc = None
        await self._async_sync(startup=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if not self._attr_is_on:
            return
        LOGGER.info("%s turned off", self.entity_id)
        async with self._lock:
            self._cancel_pending()
            writer = self._writer()
            if writer is not None:
                await self._async_write(writer.async_release(self._last_command))
            self._attr_is_on = False
            self._status = STATUS_OFF
            self._last_command = None
            self._last_min_soc = None
            self.async_write_ha_state()

    @callback
    def _handle_change(self, _event=None) -> None:
        if self._attr_is_on:
            self.hass.async_create_task(self._async_sync(startup=False))

    @callback
    def _handle_pending_min_soc(self, _now) -> None:
        self._cancel_pending_min_soc = None
        self._handle_change()

    @callback
    def _cancel_pending(self) -> None:
        if self._cancel_pending_min_soc is not None:
            self._cancel_pending_min_soc()
            self._cancel_pending_min_soc = None

    def _writer(self) -> InverterWriter | None:
        """The writer for the wizard's inverter type, or None (and the
        status saying why) when there is none or it isn't set up."""
        writer_class = INVERTER_WRITERS.get(pv_properties.inverter_type(self.hass, self._entry) or "")
        if writer_class is None:
            self._status = STATUS_NO_WRITER
            return None
        writer = writer_class(self.hass, self._entry)
        if not writer.is_configured():
            self._status = STATUS_NOT_CONFIGURED
            return None
        return writer

    async def _async_sync(self, startup: bool) -> None:
        """Bring the inverter in line with battery_command and min_soc."""
        async with self._lock:
            if not self._attr_is_on:
                return
            writer = self._writer()
            if writer is not None:
                self._status = STATUS_ACTIVE
                await self._async_sync_command(writer, startup)
                await self._async_sync_min_soc(writer)
            self.async_write_ha_state()

    async def _async_sync_command(self, writer: InverterWriter, startup: bool) -> None:
        entity_id = pv_properties.battery_command_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        command = state.state if state is not None else ""
        if command in _INVALID_STATES or (not startup and command == self._last_command):
            return
        previous = None if startup else self._last_command
        if await self._async_write(writer.async_write_command(command, previous, startup)):
            self._last_command = command

    async def _async_sync_min_soc(self, writer: InverterWriter) -> None:
        entity_id = pv_properties.battery_data_entity_id(self.hass, self._entry)
        min_soc = read_float_state(self.hass.states.get(entity_id) if entity_id else None)
        if min_soc is None:
            return
        if not should_write_min_soc(min_soc, writer.read_min_soc(), self._last_min_soc):
            self._last_min_soc = min_soc
            return
        now = dt_util.utcnow()
        allowed_at = next_min_soc_write(self._last_min_soc_write, now)
        if allowed_at is not None:
            if self._cancel_pending_min_soc is None:
                self._cancel_pending_min_soc = event.async_track_point_in_utc_time(
                    self.hass, self._handle_pending_min_soc, allowed_at
                )
            return
        if await self._async_write(writer.async_write_min_soc(min_soc)):
            self._last_min_soc = min_soc
            self._last_min_soc_write = now

    async def _async_write(self, write) -> bool:
        """Await one writer call and log it in the logbook. False when it
        failed -- the next change tries again."""
        try:
            description = await write
        except Exception:  # noqa: BLE001 -- a failing inverter must not stop PwMngt
            LOGGER.warning("%s: writing to the inverter failed", self.entity_id, exc_info=True)
            self._status = STATUS_WRITE_FAILED
            return False
        if description:
            LOGGER.info("%s: %s", self.entity_id, description)
            name = self.name if isinstance(self.name, str) else self.entity_id
            async_log_entry(self.hass, name, description, domain=DOMAIN, entity_id=self.entity_id)
        return True
