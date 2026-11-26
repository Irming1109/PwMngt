"""Base class for the brand writers that carry PwMngt's battery control to
an inverter (issue #124).

No Node-RED port of its own: in Node-RED each brand has a translator
function node that reads PwMngt's select.pwm_pv_battery_command and
sensor.pwm_pv_battery_data and writes to the inverter (the Growatt
translator in issue #126, the Kostal translator in issue #1, 04-11-2026).
A brand writer (inverter/kostal.py, inverter/growatt.py) is the PwMngt
version of such a node; when it is called is decided in one place,
inverter/control.py.

A writer only translates. It may keep state between calls (one instance
lives as long as the switch), and when the inverter makes it wait -- e.g.
Growatt's 30 s lockout per register -- it asks control for a new sync
later (schedule_sync()) rather than writing on its own.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

# The command that leaves the inverter in normal operation -- also what
# a writer is given when "Inverter control" is turned off.
COMMAND_INTERNAL = "internal"


class InverterWriter:
    """Writes battery_command (and min_soc, where supported) to one brand
    of inverter.

    Subclasses override what their inverter supports; the defaults do
    nothing.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        # Set by inverter/control.py: runs a new sync (follow-up included).
        self.request_sync: Callable[[], None] | None = None
        self._cancel_scheduled_sync: Callable[[], None] | None = None

    def is_configured(self) -> bool:
        """True when the wizard has the fields this writer needs. While
        False, inverter/control.py writes nothing."""
        return False

    def trigger_entity_ids(self) -> list[str]:
        """Entities besides battery_command and Battery data whose changes
        should run async_follow() (e.g. Growatt's Battery hold power)."""
        return []

    async def async_write_command(
        self, command: str, previous: str | None, startup: bool
    ) -> str | None:
        """Carry a changed battery_command to the inverter.

        previous is the command last written -- also across a restart (then
        startup is True and previous may equal command), and None right
        after "Inverter control" was turned on. A writer whose commands cost
        an EEPROM write can skip one the inverter already runs.

        Returns a short description of what was written, for the logbook
        (None = nothing was written).
        """
        return None

    async def async_follow(self, command: str) -> str | None:
        """Called on every sync while battery_command is unchanged: a
        writer can finish a write it had to postpone, or follow a value
        that changes under the same command. Returns a description for the
        logbook, or None."""
        return None

    def supports_min_soc(self) -> bool:
        """Whether this writer writes min_soc at all."""
        return False

    def read_min_soc(self) -> float | None:
        """The min SoC the inverter has now, or None when it can't be
        read -- then inverter/control.py writes it to be sure."""
        return None

    async def async_write_min_soc(self, min_soc: float) -> str | None:
        """Write min_soc (%) to the inverter. Returns a short description
        for the logbook (None = nothing was written)."""
        return None

    async def async_release(self, previous: str | None) -> str | None:
        """Leave the inverter in normal operation -- called once when
        "Inverter control" is turned off. By default the same as writing
        "internal"."""
        return await self.async_write_command(COMMAND_INTERNAL, previous, startup=False)

    @callback
    def schedule_sync(self, seconds: float) -> None:
        """Ask for a new sync in `seconds` (replaces an earlier request)."""
        self.cancel_scheduled_sync()

        @callback
        def _sync(_now) -> None:
            self._cancel_scheduled_sync = None
            if self.request_sync is not None:
                self.request_sync()

        self._cancel_scheduled_sync = async_call_later(self.hass, seconds, _sync)

    @callback
    def stop(self) -> None:
        """The writer is done -- "Inverter control" turned off or the
        writer replaced. A writer clears what it keeps going (timers, its
        own Repairs)."""
        self.cancel_scheduled_sync()

    @callback
    def cancel_scheduled_sync(self) -> None:
        if self._cancel_scheduled_sync is not None:
            self._cancel_scheduled_sync()
            self._cancel_scheduled_sync = None
