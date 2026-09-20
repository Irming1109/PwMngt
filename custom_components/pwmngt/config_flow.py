import logging
import voluptuous as vol

from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from homeassistant.helpers.event import async_call_later

from . import async_setup_entry, async_unload_entry
from .const import (
    DOMAIN,
    CONF_DEFAULT_NAME,
    CONF_SEGMENTS,
    CONF_ENTITY_MAP,
    ENTITY_KEY_BATTERY_SOC,
    SEGMENT_EV_CHARGING,
    SEGMENT_POOL,
    SEGMENT_PV_SURPLUS,
)

LOGGER = logging.getLogger(__name__)

# Fixed visit order for the optional segment pages (3-5 of the wizard).
# "Power Management" isn't in here -- it's mandatory and always visited
# right after page 1, see PwMngtOptionsFlow.async_step_power_management.
_SEGMENT_STEP_ORDER = [SEGMENT_EV_CHARGING, SEGMENT_POOL, SEGMENT_PV_SURPLUS]


def _entities_by_unit(hass, unit: str) -> list[selector.SelectOptionDict]:
    """List sensor entities whose unit of measurement matches `unit`.

    Used to build a short, type-safe entity picker for the abstraction
    layer (e.g. only "%"-unit entities are offered for battery_soc)
    instead of a dropdown of every entity in the house.
    """
    options = []
    for state in sorted(hass.states.async_all("sensor"), key=lambda s: s.entity_id):
        if state.attributes.get("unit_of_measurement") == unit:
            friendly_name = state.attributes.get("friendly_name", state.entity_id)
            options.append(
                selector.SelectOptionDict(
                    value=state.entity_id,
                    label=f"{friendly_name} ({state.entity_id})",
                )
            )
    return options


def _entity_picker(hass, unit: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=_entities_by_unit(hass, unit),
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class PwMngtOptionsFlow(config_entries.OptionsFlow):
    """PwMngt options flow handler -- a multi-page wizard.

    self.config_entry is provided by Home Assistant's config entries manager
    itself (it's a property on the base OptionsFlow class) -- do not set it
    manually in __init__ here, that is no longer allowed by newer HA
    versions and causes the options flow to fail with a 500 error.

    Page 1 (async_step_init): name + which optional segments are enabled.
    "Power Management" itself is mandatory and not a toggle -- it's just
    shown as a heading -- the EV Charging / Pool / PV Surplus rows are the
    actual opt-in segments.
    Page 2 (async_step_power_management): the Power Management
    abstraction layer. Always visited. Only battery_soc is implemented so
    far; more fields will be added here as PwMngt grows its entity map.
    Pages 3-5 (async_step_ev_charging / _pool / _pv_surplus): one per
    optional segment, visited only if that segment was enabled on page 1.
    They're scaffolding for now -- no fields yet, just a page that exists
    in the wizard so the structure is in place before the content is.
    """

    def __init__(self) -> None:
        super().__init__()
        # Collected across pages, written out as the final options dict
        # once the wizard reaches the end (see _finish).
        self._data: dict[str, Any] = {}
        # Optional segment steps still left to show, in fixed order,
        # populated from the page-1 selection.
        self._pending_segments: list[str] = []

    async def _do_update(
        self, *args, **kwargs  # pylint: disable=unused-argument
    ) -> None:
        """Update after settings change."""
        await async_unload_entry(self.hass, self.config_entry)
        await async_setup_entry(self.hass, self.config_entry)

    async def async_step_init(self, user_input: Any | None = None):
        """Page 1: name + segment selection."""

        errors = {}
        if user_input is not None and "base" not in errors:
            self._data[CONF_NAME] = user_input[CONF_NAME]
            selected_segments = user_input.get(CONF_SEGMENTS, [])
            self._data[CONF_SEGMENTS] = selected_segments
            self._pending_segments = [
                segment
                for segment in _SEGMENT_STEP_ORDER
                if segment in selected_segments
            ]
            return await self.async_step_power_management()

        current_segments = self.config_entry.options.get(CONF_SEGMENTS, [])
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NAME,
                    default=self.config_entry.options.get(
                        CONF_NAME,
                        self.config_entry.data.get(CONF_NAME, CONF_DEFAULT_NAME),
                    ),
                ): str,
                # "Power Management" itself isn't a choice here -- it's
                # mandatory and always configured on page 2. This list is
                # only the optional segments on top of it.
                vol.Optional(CONF_SEGMENTS, default=current_segments): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=SEGMENT_EV_CHARGING, label="EV Charging"
                            ),
                            selector.SelectOptionDict(
                                value=SEGMENT_POOL, label="Pool"
                            ),
                            selector.SelectOptionDict(
                                value=SEGMENT_PV_SURPLUS, label="PV Surplus"
                            ),
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_power_management(self, user_input: Any | None = None):
        """Page 2: Power Management abstraction layer (mandatory).

        Only battery_soc is wired up so far -- more fields land here as
        PwMngt's entity map grows (see ENTITY_KEY_* in const.py).
        """
        errors = {}
        if user_input is not None:
            entity_map = dict(self.config_entry.options.get(CONF_ENTITY_MAP, {}))
            entity_map[ENTITY_KEY_BATTERY_SOC] = user_input.get(ENTITY_KEY_BATTERY_SOC)
            self._data[CONF_ENTITY_MAP] = entity_map
            return await self._advance()

        current_entity_map = self.config_entry.options.get(CONF_ENTITY_MAP, {})
        schema = vol.Schema(
            {
                # Filtered to "%"-unit sensors, so the list is short and
                # only contains entities that could actually be a battery
                # state of charge -- replaces the old fixed "inverter
                # name" naming-convention assumption.
                vol.Optional(
                    ENTITY_KEY_BATTERY_SOC,
                    default=current_entity_map.get(ENTITY_KEY_BATTERY_SOC),
                ): _entity_picker(self.hass, "%"),
            }
        )

        return self.async_show_form(
            step_id="power_management", data_schema=schema, errors=errors
        )

    async def async_step_ev_charging(self, user_input: Any | None = None):
        """Page 3: EV Charging abstraction layer (scaffolding for now)."""
        return await self._scaffold_step("ev_charging", user_input)

    async def async_step_pool(self, user_input: Any | None = None):
        """Page 4: Pool abstraction layer (scaffolding for now)."""
        return await self._scaffold_step("pool", user_input)

    async def async_step_pv_surplus(self, user_input: Any | None = None):
        """Page 5: PV Surplus abstraction layer (scaffolding for now)."""
        return await self._scaffold_step("pv_surplus", user_input)

    async def _scaffold_step(self, step_id: str, user_input: Any | None):
        """A segment page that exists in the wizard but has no fields to
        configure yet. Shows an empty form (just a Submit button) so the
        page is in place -- fill in real fields here once that segment's
        abstraction layer is designed.
        """
        if user_input is not None:
            return await self._advance()
        return self.async_show_form(step_id=step_id, data_schema=vol.Schema({}))

    async def _advance(self):
        """Move to the next enabled segment's page, or finish the wizard."""
        while self._pending_segments:
            segment = self._pending_segments.pop(0)
            if segment == SEGMENT_EV_CHARGING:
                return await self.async_step_ev_charging()
            if segment == SEGMENT_POOL:
                return await self.async_step_pool()
            if segment == SEGMENT_PV_SURPLUS:
                return await self.async_step_pv_surplus()
        return self._finish()

    def _finish(self):
        """Write out the collected options and trigger a reload."""
        async_call_later(self.hass, 2, self._do_update)
        data = {
            CONF_NAME: self._data.get(
                CONF_NAME, self.config_entry.options.get(CONF_NAME)
            ),
            CONF_SEGMENTS: self._data.get(CONF_SEGMENTS, []),
            CONF_ENTITY_MAP: self._data.get(CONF_ENTITY_MAP, {}),
        }
        return self.async_create_entry(
            title=data[CONF_NAME],
            data=data,
            description=f"Power Management - {data[CONF_NAME]}",
        )


class PwMngtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> PwMngtOptionsFlow:
        """Make the options flow (the "Configure" button) reachable.

        Without this, PwMngtOptionsFlow above is defined but never used --
        Home Assistant has no other way to discover it.
        """
        return PwMngtOptionsFlow()

    async def async_step_user(self, user_input=None):

        """Handle a config flow for PwMngt.

        Kept minimal on purpose -- just the name. Everything else
        (segments, the entity abstraction layer) is configured afterwards
        through Options ("Configure"), see PwMngtOptionsFlow above.
        """
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
