import logging
import voluptuous as vol

from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
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
    ENTITY_KEY_PV_FORECAST_TODAY,
    ENTITY_KEY_PV_FORECAST_TOMORROW,
    ENTITY_KEY_BATTERY_NIGHTLY_TARGET,
    ENTITY_KEY_PV_FORECAST_DAYTIME_TODAY,
    ENTITY_KEY_PV_FORECAST_DAYTIME_TOMORROW,
    ENTITY_KEY_PV_HISTORY_PERIOD_DAYS,
    ENTITY_KEY_SPOT_ELECTRICITY_PRICE,
    SEGMENT_EV_CHARGING,
    SEGMENT_POOL,
    SEGMENT_PV_SURPLUS,
)

# Solar PV Plant abstraction-layer fields (page 2), each paired with the
# unit of measurement its entity picker is filtered to. Keep this list and
# PwM_PV_MIRROR_SENSORS in sensor.py in sync -- this is what drives both
# the wizard page's fields and which of them get saved into the entity map.
# Third element: whether the field is required to advance the wizard. PV2/
# PV3 power and forecast are optional -- not every installation has a
# second/third PV string, and the field shouldn't block setup if so.
_SOLAR_PV_PLANT_FIELDS = [
    (ENTITY_KEY_BATTERY_SOC, "%", True),
    (ENTITY_KEY_BATTERY_PV_CHARGED, "kWh", True),
    (ENTITY_KEY_BATTERY_PV_DISCHARGED, "kWh", True),
    (ENTITY_KEY_BATTERY_POWER, "W", True),
    (ENTITY_KEY_PV1_POWER, "W", True),
    (ENTITY_KEY_PV2_POWER, "W", False),
    (ENTITY_KEY_PV3_POWER, "W", False),
    (ENTITY_KEY_PV_DIRECT_CONSUMPTION, "W", True),
    (ENTITY_KEY_PV_TOTAL_CONSUMPTION, "kWh", True),
    (ENTITY_KEY_GRID_POWER, "W", True),
    (ENTITY_KEY_PV_FORECAST_TODAY, "kWh", True),
    (ENTITY_KEY_PV_FORECAST_TOMORROW, "kWh", True),
    # Diagnostic values -- optional, since they depend on automations not
    # every install runs (see sensor.py's PwM_PV_MIRROR_SENSORS comment).
    (ENTITY_KEY_BATTERY_NIGHTLY_TARGET, "%", False),
    (ENTITY_KEY_PV_FORECAST_DAYTIME_TODAY, "kWh", False),
    (ENTITY_KEY_PV_FORECAST_DAYTIME_TOMORROW, "kWh", False),
    # Hub value, not really "Solar PV Plant" -- lives here anyway rather
    # than a dedicated page, same as ENTITY_KEY_PV_HISTORY_PERIOD_DAYS
    # above. Optional: without it the "Spot electricity price" sensor just
    # stays unavailable (and the missing-Stromligning Repair, see
    # __init__.py, already flags the underlying dependency), so this
    # shouldn't block finishing the wizard.
    (ENTITY_KEY_SPOT_ELECTRICITY_PRICE, "kr/kWh", False),
]

# Fields on the same page that pick a `select` entity rather than a
# `sensor` -- these need _select_entity_picker (domain-only filter), not
# _entity_picker (which filters by unit_of_measurement, meaningless for a
# select). Currently just the history-period selector: nothing consumes it
# yet (the consumption-averages calculation engine that will read it is a
# later release), but it's wired into Options now so it's ready when that
# lands. Each tuple is (entity_map_key, required).
_SOLAR_PV_PLANT_SELECT_FIELDS = [
    (ENTITY_KEY_PV_HISTORY_PERIOD_DAYS, False),
]

LOGGER = logging.getLogger(__name__)

# Fixed visit order for the optional segment pages (3-5 of the wizard).
# "Solar PV Plant" isn't in here -- it's mandatory and always visited
# right after page 1, see PwMngtOptionsFlow.async_step_solar_pv_plant.
_SEGMENT_STEP_ORDER = [SEGMENT_EV_CHARGING, SEGMENT_POOL, SEGMENT_PV_SURPLUS]


def _entity_ids_by_unit(hass, unit: str) -> list[str]:
    """List sensor entity_ids whose unit of measurement matches `unit`,
    excluding PwMngt's own sensors -- mirroring one of PwMngt's own
    entities would be circular and is never what the user wants here.
    """
    registry = er.async_get(hass)
    entity_ids = []
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("unit_of_measurement") != unit:
            continue
        entry = registry.async_get(state.entity_id)
        if entry is not None and entry.platform == DOMAIN:
            continue
        entity_ids.append(state.entity_id)
    return sorted(entity_ids)


def _entity_picker(hass, unit: str) -> selector.EntitySelector:
    """Build an entity picker restricted to sensors of the given unit.

    Uses Home Assistant's native entity selector (via `include_entities`)
    rather than a hand-rolled dropdown -- it already provides type-to-
    filter search and an alphabetically sorted list for free, and treats a
    cleared/empty selection as valid, which is what lets a field be left
    blank when it's optional (see _SOLAR_PV_PLANT_FIELDS).
    """
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            include_entities=_entity_ids_by_unit(hass, unit),
        )
    )


def _entity_ids_by_domain(hass, domain: str) -> list[str]:
    """List entity_ids in the given domain, excluding PwMngt's own.

    Sibling to _entity_ids_by_unit, for entities that don't carry a
    meaningful unit_of_measurement to filter on (e.g. `select` entities).
    """
    registry = er.async_get(hass)
    entity_ids = []
    for state in hass.states.async_all(domain):
        entry = registry.async_get(state.entity_id)
        if entry is not None and entry.platform == DOMAIN:
            continue
        entity_ids.append(state.entity_id)
    return sorted(entity_ids)


def _select_entity_picker(hass) -> selector.EntitySelector:
    """Build an entity picker restricted to `select` domain entities."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            include_entities=_entity_ids_by_domain(hass, "select"),
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

    Each page's picks are saved into the config entry's options as soon as
    that page is submitted (see _save_progress), not only once the whole
    wizard reaches the end -- so leaving the wizard partway through (e.g.
    to go set up a source integration first) doesn't lose what was already
    filled in.
    """

    def __init__(self) -> None:
        super().__init__()
        # Collected across pages, written out as the final options dict
        # once the wizard reaches the end (see _finish). Also written out
        # incrementally after each page -- see _save_progress.
        self._data: dict[str, Any] = {}
        # Optional segment steps still left to show, in fixed order,
        # populated from the page-1 selection.
        self._pending_segments: list[str] = []

    def _save_progress(self) -> None:
        """Persist whatever's been collected so far into the config
        entry's options immediately, not just once the whole wizard is
        completed.

        Home Assistant's options flows normally only write anything out
        once, right at the end (see _finish's async_create_entry) -- if
        the user closes/cancels partway through, nothing is saved at all.
        That's fine for a short flow, but PwMngt's wizard can legitimately
        need a mid-flow detour (e.g. going to set up an integration a
        mirror source depends on before picking it here), so each page's
        picks are written out as soon as that page is submitted. A field
        not yet touched this session falls back to what's already in
        self.config_entry.options, so this never wipes out an earlier
        page's (or an earlier wizard run's) saved values.
        """
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={
                CONF_SEGMENTS: self._data.get(
                    CONF_SEGMENTS,
                    list(self.config_entry.options.get(CONF_SEGMENTS, [])),
                ),
                CONF_ENTITY_MAP: self._data.get(
                    CONF_ENTITY_MAP,
                    dict(self.config_entry.options.get(CONF_ENTITY_MAP, {})),
                ),
            },
        )

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
            self._save_progress()
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
        """Page 2: Solar PV Plant abstraction layer (mandatory page, but
        not every field on it is mandatory -- see _SOLAR_PV_PLANT_FIELDS).

        Each field is an entity picker filtered to the unit of measurement
        that value should have (see _SOLAR_PV_PLANT_FIELDS), so the
        dropdown is short and only offers entities that could actually be
        the right one -- replaces the old fixed "inverter name"
        naming-convention assumption. The helper text under each field
        (strings.json -> data_description) tells the user what to look
        for. More fields land here as PwMngt's entity map grows (see
        ENTITY_KEY_* in const.py).

        A field can be left untouched/blank -- see the schema-building
        comment below for why that only works when it has no `default=` at
        all. Whether blank is actually *allowed* for a given field is
        enforced here instead, per _SOLAR_PV_PLANT_FIELDS' "required" flag,
        so a field like pv2_power can be skipped (not everyone has a second
        PV string) while e.g. battery_soc still can't be.

        Known limitation: this covers a field that was never touched. If a
        field already holds a value (so it does get a `default=`) and the
        user actively clears it via the picker's own "x", Home Assistant's
        EntitySelector still rejects the resulting "" at the schema-
        validation layer, before this function even runs -- same
        underlying issue, just for the "clear an existing pick" path
        rather than the "never picked anything" path this fixes. Not yet
        hit in practice (PV2/PV3 are the only optional fields, and nobody
        without a second/third string would have set one to begin with),
        so left as a follow-up rather than guessed at now.
        """
        errors: dict[str, str] = {}
        values = dict(self.config_entry.options.get(CONF_ENTITY_MAP, {}))

        if user_input is not None:
            values.update(user_input)
            errors = {
                key: "required"
                for key, _unit, required in _SOLAR_PV_PLANT_FIELDS
                if required and not user_input.get(key)
            }
            errors.update(
                {
                    key: "required"
                    for key, required in _SOLAR_PV_PLANT_SELECT_FIELDS
                    if required and not user_input.get(key)
                }
            )
            if not errors:
                entity_map = dict(self.config_entry.options.get(CONF_ENTITY_MAP, {}))
                for key, _unit, _required in _SOLAR_PV_PLANT_FIELDS:
                    entity_map[key] = user_input.get(key) or None
                for key, _required in _SOLAR_PV_PLANT_SELECT_FIELDS:
                    entity_map[key] = user_input.get(key) or None
                self._data[CONF_ENTITY_MAP] = entity_map
                self._save_progress()
                return await self._advance()

        # No `default=` at all when there's no value yet -- NOT default=""
        # or default=None. Home Assistant's EntitySelector validates
        # whatever value actually gets submitted (cv.entity_id_or_uuid),
        # and it rejects "" outright ("Entity  is neither a valid entity ID
        # nor a valid UUID") -- there's no such thing as a "blank but
        # valid" entity value as far as that selector is concerned. A
        # `vol.Optional(key)` with no default is different: Home
        # Assistant's frontend (compute-initial-ha-form-data.ts) leaves the
        # key out of the submitted data entirely when the user never
        # touches it, so voluptuous never runs the selector's validator on
        # it at all -- which is what actually makes a field skippable.
        schema_dict: dict[Any, Any] = {}
        for key, unit, _required in _SOLAR_PV_PLANT_FIELDS:
            marker = (
                vol.Optional(key, default=values[key])
                if values.get(key)
                else vol.Optional(key)
            )
            schema_dict[marker] = _entity_picker(self.hass, unit)
        for key, _required in _SOLAR_PV_PLANT_SELECT_FIELDS:
            marker = (
                vol.Optional(key, default=values[key])
                if values.get(key)
                else vol.Optional(key)
            )
            schema_dict[marker] = _select_entity_picker(self.hass)
        schema = vol.Schema(schema_dict)

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
