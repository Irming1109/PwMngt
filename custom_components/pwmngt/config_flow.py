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
    ENTITY_KEY_BATTERY_PV_CHARGED,
    ENTITY_KEY_BATTERY_PV_DISCHARGED,
    ENTITY_KEY_BATTERY_POWER,
    ENTITY_KEY_PV1_POWER,
    ENTITY_KEY_PV2_POWER,
    ENTITY_KEY_PV3_POWER,
    ENTITY_KEY_PV_DIRECT_CONSUMPTION,
    ENTITY_KEY_PV_TOTAL_CONSUMPTION,
    ENTITY_KEY_GRID_POWER,
    ENTITY_KEY_PV1_FORECAST_TODAY,
    ENTITY_KEY_PV2_FORECAST_TODAY,
    ENTITY_KEY_PV3_FORECAST_TODAY,
    SEGMENT_EV_CHARGING,
    SEGMENT_POOL,
    SEGMENT_PV_SURPLUS,
)

# Solar PV Plant abstraction-layer fields (page 2), each paired with the
# unit of measurement its entity picker is filtered to. Keep this list and
# PwM_PV_MIRROR_SENSORS in sensor.py in sync -- this is what drives both
# the wizard page's fields and which of them get saved into the entity map.
_SOLAR_PV_PLANT_FIELDS = [
    (ENTITY_KEY_BATTERY_SOC, "%"),
    (ENTITY_KEY_BATTERY_PV_CHARGED, "kWh"),
    (ENTITY_KEY_BATTERY_PV_DISCHARGED, "kWh"),
    (ENTITY_KEY_BATTERY_POWER, "W"),
    (ENTITY_KEY_PV1_POWER, "W"),
    (ENTITY_KEY_PV2_POWER, "W"),
    (ENTITY_KEY_PV3_POWER, "W"),
    (ENTITY_KEY_PV_DIRECT_CONSUMPTION, "W"),
    (ENTITY_KEY_PV_TOTAL_CONSUMPTION, "kWh"),
    (ENTITY_KEY_GRID_POWER, "W"),
    (ENTITY_KEY_PV1_FORECAST_TODAY, "kWh"),
    (ENTITY_KEY_PV2_FORECAST_TODAY, "kWh"),
    (ENTITY_KEY_PV3_FORECAST_TODAY, "kWh"),
]

LOGGER = logging.getLogger(__name__)

# Fixed visit order for the optional segment pages (3-5 of the wizard).
# "Solar PV Plant" isn't in here -- it's mandatory and always visited
# right after page 1, see PwMngtOptionsFlow.async_step_solar_pv_plant.
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

    Page 1 (async_step_init): which optional segments are enabled. No
    "name" field here -- the entry's name is set once at initial setup
    (PwMngtConfigFlow.async_step_user) and isn't re-asked in Options.
    "Solar PV Plant" itself is mandatory and not a toggle -- the EV
    Charging / Pool / PV Surplus rows are the actual opt-in segments, each
    with its own short helper text (see strings.json's data_description).
    Page 2 (async_step_solar_pv_plant): the Solar PV Plant abstraction
    layer. Always visited. Only battery_soc is implemented so far; more
    fields will be added here as PwMngt grows its entity map.
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
        """Page 1: which optional segments are enabled."""

        errors = {}
        if user_input is not None and "base" not in errors:
            selected_segments = [
                segment
                for segment in _SEGMENT_STEP_ORDER
                if user_input.get(segment)
            ]
            self._data[CONF_SEGMENTS] = selected_segments
            self._pending_segments = list(selected_segments)
            return await self.async_step_solar_pv_plant()

        current_segments = self.config_entry.options.get(CONF_SEGMENTS, [])
        schema = vol.Schema(
            {
                # "Solar PV Plant" itself isn't a choice here -- it's
                # mandatory and always configured on page 2. These three
                # are the only optional segments on top of it, each with
                # its own helper text under the toggle (see strings.json
                # -> options.step.init.data_description).
                vol.Optional(
                    SEGMENT_EV_CHARGING,
                    default=SEGMENT_EV_CHARGING in current_segments,
                ): selector.BooleanSelector(),
                vol.Optional(
                    SEGMENT_POOL,
                    default=SEGMENT_POOL in current_segments,
                ): selector.BooleanSelector(),
                vol.Optional(
                    SEGMENT_PV_SURPLUS,
                    default=SEGMENT_PV_SURPLUS in current_segments,
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_solar_pv_plant(self, user_input: Any | None = None):
        """Page 2: Solar PV Plant abstraction layer (mandatory).

        Each field is an entity picker filtered to the unit of measurement
        that value should have (see _SOLAR_PV_PLANT_FIELDS), so the
        dropdown is short and only offers entities that could actually be
        the right one -- replaces the old fixed "inverter name"
        naming-convention assumption. The helper text under each field
        (strings.json -> data_description) tells the user what to look
        for. More fields land here as PwMngt's entity map grows (see
        ENTITY_KEY_* in const.py).
        """
        errors = {}
        if user_input is not None:
            entity_map = dict(self.config_entry.options.get(CONF_ENTITY_MAP, {}))
            for key, _unit in _SOLAR_PV_PLANT_FIELDS:
                entity_map[key] = user_input.get(key)
            self._data[CONF_ENTITY_MAP] = entity_map
            return await self._advance()

        current_entity_map = self.config_entry.options.get(CONF_ENTITY_MAP, {})
        schema = vol.Schema(
            {
                vol.Optional(
                    key, default=current_entity_map.get(key)
                ): _entity_picker(self.hass, unit)
                for key, unit in _SOLAR_PV_PLANT_FIELDS
            }
        )

        return self.async_show_form(
            step_id="solar_pv_plant", data_schema=schema, errors=errors
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
        title = self.config_entry.data.get(CONF_NAME, self.config_entry.title)
        data = {
            CONF_SEGMENTS: self._data.get(CONF_SEGMENTS, []),
            CONF_ENTITY_MAP: self._data.get(CONF_ENTITY_MAP, {}),
        }
        return self.async_create_entry(
            title=title,
            data=data,
            description=f"Power Management - {title}",
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
