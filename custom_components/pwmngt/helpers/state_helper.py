"""Helpers for reading Home Assistant State objects safely."""

from homeassistant.core import State


def read_float_state(state: State | None) -> float | None:
    """The numeric value of a Home Assistant state, or None if it's
    missing, unknown/unavailable, or not a valid number.

    Takes the State object itself rather than an entity_id, so it works
    equally well for a state you already have in hand (from an event, or
    RestoreEntity.async_get_last_state()) and one you look up yourself
    (state = hass.states.get(entity_id)).
    """
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None
