import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN
from .data.savings_data import (
    ADJUSTABLE_SAVINGS_PERIODS,
    ADJUSTABLE_SAVINGS_TOTALS,
    signal_savings_adjust,
)

LOGGER = logging.getLogger(__name__)

SERVICE_ADJUST_SAVINGS = "adjust_savings"
ATTR_TOTAL = "total"
ATTR_AMOUNT = "amount"

_ADJUST_SAVINGS_SCHEMA = vol.Schema(
    {
        # Issue #53: the period totals too (today / this month / this year).
        vol.Required(ATTR_TOTAL): vol.In(
            list(ADJUSTABLE_SAVINGS_TOTALS) + list(ADJUSTABLE_SAVINGS_PERIODS)
        ),
        vol.Required(ATTR_AMOUNT): vol.Coerce(float),
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the pwmngt integration's services (actions). Called from
    async_setup_entry, so registering is skipped when already done."""
    if hass.services.has_service(DOMAIN, SERVICE_ADJUST_SAVINGS):
        return

    async def _adjust_savings(call: ServiceCall) -> None:
        """Issue #46: correct one of the savings running totals by an
        amount (DKK) -- e.g. what a wrongly mapped counter booked. Issue
        #53: or one of the period totals, e.g. to start "this year" from
        an existing utility meter."""
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            async_dispatcher_send(
                hass,
                signal_savings_adjust(entry.entry_id),
                call.data[ATTR_TOTAL],
                call.data[ATTR_AMOUNT],
            )

    hass.services.async_register(
        DOMAIN, SERVICE_ADJUST_SAVINGS, _adjust_savings, schema=_ADJUST_SAVINGS_SCHEMA
    )
