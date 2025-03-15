from homeassistant import config_entries
from .const import DOMAIN

class PwmngtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Pwmngt integration."""

    async def async_step_user(self, user_input=None):
        """Trin til at konfigurere integrationen via UI."""
        if user_input is not None:
            return self.async_create_entry(title="Pwmngt Sensor", data={})

        return self.async_show_form(step_id="user")
