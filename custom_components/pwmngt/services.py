import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .data.battery_charge_decision import (
    auto_detect_tomorrow_prices_entity_id,
    manual_night,
    signal_manual_charge_target,
)
from .properties import pv_properties
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

# Issue #105: the night's charge target -- 0 (or anything at/below the min
# SoC) means no grid charge tonight.
SERVICE_SET_NIGHT_CHARGE_TARGET = "set_night_charge_target"
SERVICE_RESET_NIGHT_CHARGE_TARGET = "reset_night_charge_target"
ATTR_TARGET = "target"

_SET_NIGHT_CHARGE_TARGET_SCHEMA = vol.Schema(
    {vol.Required(ATTR_TARGET): vol.All(vol.Coerce(float), vol.Range(min=0, max=100))}
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the pwmngt integration's services (actions). Called from
    async_setup_entry, so registering is skipped when already done."""
    if hass.services.has_service(DOMAIN, SERVICE_RESET_NIGHT_CHARGE_TARGET):
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

    def _send_manual_target(target: float | None) -> None:
        """Issue #105: to each entry's Battery charge status -- refused up
        front when there is no night plan to change (manual_night())."""
        now = dt_util.now()
        tomorrow_id = auto_detect_tomorrow_prices_entity_id(hass)
        tomorrow_state = hass.states.get(tomorrow_id) if tomorrow_id else None
        tomorrow_out = tomorrow_state is not None and tomorrow_state.state == "on"
        sent = False
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            status_id = pv_properties.battery_charge_status_entity_id(hass, entry)
            status = hass.states.get(status_id) if status_id else None
            if status is None or manual_night(
                status.state, dict(status.attributes), now, tomorrow_out
            ) is None:
                continue
            async_dispatcher_send(hass, signal_manual_charge_target(entry.entry_id), target)
            sent = True
        if not sent:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_night_plan"
            )

    async def _set_night_charge_target(call: ServiceCall) -> None:
        """Issue #105: the user's own charge target (%) for one night."""
        _send_manual_target(call.data[ATTR_TARGET])

    async def _reset_night_charge_target(call: ServiceCall) -> None:
        """Issue #105: back to the automation's own night plan."""
        _send_manual_target(None)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_NIGHT_CHARGE_TARGET,
        _set_night_charge_target,
        schema=_SET_NIGHT_CHARGE_TARGET_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESET_NIGHT_CHARGE_TARGET, _reset_night_charge_target
    )
