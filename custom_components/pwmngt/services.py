import logging

from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the pwmngt integration's services.

    No services are registered yet -- called from __init__.py so real
    ones have a place to go once there's actual functionality to expose.
    """
