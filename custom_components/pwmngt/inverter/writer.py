"""Base class for the brand writers that carry PwMngt's battery control to
an inverter (issue #124).

No Node-RED port of its own: in Node-RED each brand has a translator
function node that reads PwMngt's select.pwm_pv_battery_command and
sensor.pwm_pv_battery_data and writes to the inverter ("Oversætter til
Growatt" and "Lad nu - Growatt" in tab "Batteri styring NY"; the Kostal
translator in issue #1, 04-11-2026). A brand writer (issue #125 Kostal,
issue #126 Growatt) is the PwMngt version of such a node; when and how
often it is called is decided in one place, inverter/control.py.

A writer only translates -- it never decides on its own when to write.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

# The command that leaves the inverter in normal operation -- also what
# a writer is given when "Inverter control" is turned off.
COMMAND_INTERNAL = "internal"


class InverterWriter:
    """Writes battery_command and min_soc to one brand of inverter.

    Subclasses override what their inverter supports; the defaults do
    nothing, so a brand without e.g. a min SoC setting just leaves
    async_write_min_soc() alone.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

    def is_configured(self) -> bool:
        """True when the wizard has the fields this writer needs. While
        False, inverter/control.py writes nothing."""
        return False

    async def async_write_command(
        self, command: str, previous: str | None, startup: bool
    ) -> str | None:
        """Carry battery_command to the inverter.

        previous is the command last written (None at startup or right
        after "Inverter control" was turned on), so a writer can e.g. end
        a charge when it is left. startup is True when this is the sync
        after a restart or after turning control on -- a writer whose
        command costs an EEPROM write may skip it when nothing is running.

        Returns a short description of what was written, for the logbook
        (None = nothing was written).
        """
        return None

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
