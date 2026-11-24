"""Switch entities for PwMngt.

- Inverter control (PwM PV, issue #124): lets PwMngt write battery_command
  and min_soc to the inverter itself -- see inverter/control.py. Off by
  default; until it is turned on, nothing is written to the inverter.
"""

from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .base import use_description_key_as_translation_key
from .inverter.control import PwMngtInverterControlSwitch

# New (issue #124) -- no Node-RED counterpart: in Node-RED the translator
# nodes were turned on and off by deploying/disabling them.
INVERTER_CONTROL_DESCRIPTION = SwitchEntityDescription(
    key="inverter_control",
    name="Inverter control",
    icon="mdi:solar-power-variant-outline",
    # No entity category: it is a control, not a setting, so it shows
    # under "Controls" on PwM PV (Rene's test, 2026-11-24).
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the PV device's switches."""
    entities = [PwMngtInverterControlSwitch(INVERTER_CONTROL_DESCRIPTION, entry)]
    use_description_key_as_translation_key(entities)
    async_add_entities(entities)
