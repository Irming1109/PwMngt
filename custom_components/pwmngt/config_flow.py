import logging
import voluptuous as vol

from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_NAME

from homeassistant.helpers.event import async_call_later

from . import async_setup_entry, async_unload_entry
from .const import DOMAIN, CONF_DEFAULT_NAME

LOGGER = logging.getLogger(__name__)

class PwMngtOptionsFlow(config_entries.OptionsFlow):
    """PwMngt options flow handler."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize PwMngt options flow."""
        self.config_entry = config_entry
        self._errors = {}

    async def _do_update(
        self, *args, **kwargs  # pylint: disable=unused-argument
    ) -> None:
        """Update after settings change."""
        await async_unload_entry(self.hass, self.config_entry)
        await async_setup_entry(self.hass, self.config_entry)

    async def async_step_init(self, user_input: Any | None = None):
        """Handle the initial options flow step."""

        errors = {}
        if user_input is not None and "base" not in errors:
            
            async_call_later(self.hass, 2, self._do_update)
            return self.async_create_entry(
                title=self.config_entry.data.get(CONF_NAME),
                data=user_input,
                description=f"Power Management - {self.config_entry.data.get(CONF_NAME)}",
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=CONF_DEFAULT_NAME): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

class PwMngtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    async def async_step_user(self, user_input=None):

        """Handle a config flow for PwMngt."""
        errors = {}

        if user_input is not None:
            # Create a new entry with what, user has entered
                       
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={"name": user_input[CONF_NAME]},
                options=user_input,
                description=f"Power Management - {user_input[CONF_NAME]}",
            )
            
        # Defines needed inputs from user (example: sensor_name)
        schema = vol.Schema(
            {
                #vol.Required("sensor_name", default="Min Sensor"): str,
                vol.Required(CONF_NAME, default=CONF_DEFAULT_NAME): str,
            }
        )

        # Shows formula for user
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
