import logging
import voluptuous as vol

from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from homeassistant.data_entry_flow import SectionConfig, section
from homeassistant.helpers.event import async_call_later

from . import async_setup_entry, async_unload_entry
from .devices import CHARGERS
from .select import PwM_CONFIG_SELECTS
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
    ENTITY_KEY_SPOT_ELECTRICITY_PRICE,
    ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL,
    DEPENDENCY_SOLCAST_SOLAR,
    DEPENDENCY_STROMLIGNING,
    CHARGER_TYPE_INTEGRATION_DOMAINS,
    CHARGER_CONSUMPTION_SOURCE_TRANSLATION_KEYS,
    SEGMENT_EV_CHARGING,
    SEGMENT_POOL,
    SEGMENT_PV_SURPLUS,
)

# Solar PV Plant entity-map fields (page 2): (entity_map key, unit of
# measurement, required). Keep in sync with PwM_PV_MIRROR_SENSORS in
# sensor.py. Not required when not every install has the value (e.g.
# PV2/PV3 power for a second/third PV string).
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
    # Hub value (Stromligning spot price) -- lives here rather than a
    # dedicated page.
    (ENTITY_KEY_SPOT_ELECTRICITY_PRICE, "kr/kWh", True),
    # Consumption tracking -- required even though only the "property"
    # category (data/consumption_snapshots_data.py) exists so far (Kasper's call:
    # every install should have this wired up from the start, not just
    # the ones migrating this segment first). See that module's
    # docstring for why this must be a raw, ever-increasing counter
    # rather than a Daily Utility Meter helper.
    (ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL, "kWh", True),
]

# Unit of measurement for each field above, by entity_map key.
_FIELD_UNIT_BY_KEY: dict[str, str] = {
    key: unit for key, unit, _required in _SOLAR_PV_PLANT_FIELDS
}

# Visual grouping for the Solar PV Plant page -- cosmetic only (see
# `section()` in async_step_solar_pv_plant), doesn't change the entity
# map's shape or any field's required-ness. Each entry is (group_key,
# collapsed_by_default, [entity_map keys in this group]). Every key in
# _SOLAR_PV_PLANT_FIELDS must appear in exactly one group.
_SOLAR_PV_PLANT_GROUPS: list[tuple[str, bool, list[str]]] = [
    (
        "core",
        False,
        [
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
        ],
    ),
    (
        "solar_forecast",
        False,
        [
            ENTITY_KEY_PV_FORECAST_TODAY,
            ENTITY_KEY_PV_FORECAST_TOMORROW,
        ],
    ),
    (
        "stromligning",
        False,
        [
            ENTITY_KEY_SPOT_ELECTRICITY_PRICE,
        ],
    ),
    (
        "consumption",
        False,
        [
            ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL,
        ],
    ),
]

# Auto-detected default sources: entity_map key -> (integration domain,
# translation_key) for a field with one predictable real-world source, so
# it can be pre-filled instead of requiring a manual pick (see
# _auto_detect_entity_id). translation_key, not the display name, is
# matched -- stable across renames and locales. Solcast PV Forecast:
# sensor.solcast_pv_forecast_forecast_today/_tomorrow carry translation_key
# "total_kwh_forecast_today"/"total_kwh_forecast_tomorrow". Stromligning:
# sensor.stromligning_current_price_vat_2 carries translation_key
# "current_price_vat".
_AUTO_DETECT_SOURCES: dict[str, tuple[str, str]] = {
    ENTITY_KEY_PV_FORECAST_TODAY: (
        DEPENDENCY_SOLCAST_SOLAR,
        "total_kwh_forecast_today",
    ),
    ENTITY_KEY_PV_FORECAST_TOMORROW: (
        DEPENDENCY_SOLCAST_SOLAR,
        "total_kwh_forecast_tomorrow",
    ),
    ENTITY_KEY_SPOT_ELECTRICITY_PRICE: (
        DEPENDENCY_STROMLIGNING,
        "current_price_vat",
    ),
}

LOGGER = logging.getLogger(__name__)

# Fixed visit order for the optional segment pages (3-5). "Solar PV Plant"
# isn't here -- mandatory, see PwMngtOptionsFlow.async_step_solar_pv_plant.
_SEGMENT_STEP_ORDER = [SEGMENT_EV_CHARGING, SEGMENT_POOL, SEGMENT_PV_SURPLUS]

# Charger id -> its "<id>_type" select description (PwM_CONFIG_SELECTS in
# select.py), so the EV Charging page's type field can reuse the same
# options/default instead of duplicating them.
_CHARGER_TYPE_DESCRIPTIONS = {
    charger["id"]: next(
        d for d in PwM_CONFIG_SELECTS if d.key == f"{charger['id']}_type"
    )
    for charger in CHARGERS
}


def _entity_ids_by_unit(hass, unit: str) -> list[str]:
    """Sensor entity_ids with the given unit of measurement, excluding
    PwMngt's own sensors (mirroring one of PwMngt's own would be
    circular).
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
    """Entity picker restricted to sensors of the given unit."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            include_entities=_entity_ids_by_unit(hass, unit),
        )
    )


def _entity_ids_by_device_class(hass, device_class: str) -> list[str]:
    """Sensor entity_ids with the given device_class, excluding PwMngt's
    own sensors (mirroring one of PwMngt's own would be circular).

    Used instead of _entity_ids_by_unit for fields where the unit itself
    can vary by source integration (e.g. an energy counter reported in
    kWh by one Wallbox-style integration and Wh by another) -- matching
    on device_class covers both, a single fixed unit wouldn't.
    """
    registry = er.async_get(hass)
    entity_ids = []
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("device_class") != device_class:
            continue
        entry = registry.async_get(state.entity_id)
        if entry is not None and entry.platform == DOMAIN:
            continue
        entity_ids.append(state.entity_id)
    return sorted(entity_ids)


def _energy_entity_picker(hass) -> selector.EntitySelector:
    """Entity picker restricted to sensors with device_class "energy" --
    used for each charger's consumption source entity (e.g. a Wallbox
    portal's "Added energy" sensor)."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            include_entities=_entity_ids_by_device_class(hass, "energy"),
        )
    )


def _auto_detect_entity_id(hass, platform: str, translation_key: str) -> str | None:
    """Find the single entity_id from `platform` whose translation_key
    matches, for pre-filling a field (see _AUTO_DETECT_SOURCES).

    Returns None unless exactly one match exists -- e.g. two Solcast
    installs for two rooftops would otherwise risk picking the wrong one.
    """
    registry = er.async_get(hass)
    matches = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == platform and entry.translation_key == translation_key
    ]
    return matches[0] if len(matches) == 1 else None


def _installed_charger_type_options(hass, options: list[str], current_value: str) -> list[str]:
    """Narrow a charger "type" select's static options (description.options
    in select.py's PwM_CONFIG_SELECTS) down to what the EV Charging wizard
    page should actually offer to pick from.

    "Not installed" (and anything else not in
    CHARGER_TYPE_INTEGRATION_DOMAINS) always stays. A brand name like
    "Wallbox"/"Easee" only stays if that brand's own Home Assistant
    integration is actually configured here -- same
    hass.config_entries.async_entries() check _async_check_dependencies()
    in __init__.py uses for Solcast/Strømligning, just without raising a
    Repair for it (neither charger brand is required -- EV charging is an
    opt-in segment, and a user with neither installed just sees "Not
    installed").

    `current_value` is always kept too, even if its integration isn't
    installed (e.g. it was just uninstalled) -- otherwise the field's own
    default would point at an option no longer in its list.

    The select entity itself (select.<id>_type) is untouched by this --
    it keeps offering the full option list regardless; only what the
    wizard page presents is narrowed.
    """
    return [
        option
        for option in options
        if option == current_value
        or option not in CHARGER_TYPE_INTEGRATION_DOMAINS
        or hass.config_entries.async_entries(CHARGER_TYPE_INTEGRATION_DOMAINS[option])
    ]


def _auto_detect_charger_consumption_entity(
    hass, charger_type: str, device_id: str
) -> str | None:
    """Find one charger's own "added energy" / "session energy" sensor,
    for pre-filling "consumption_source_entity" once both this charger's
    brand (its "type" field) and its physical device (its
    "charging_device_id" field) are already known -- see
    CHARGER_CONSUMPTION_SOURCE_TRANSLATION_KEYS in const.py for the
    (platform, translation_key) each brand matches on, and why.

    Deliberately scoped to one already-known device_id rather than
    searching the whole registry like _auto_detect_entity_id does for the
    Solar PV Plant page -- a house with two Wallbox chargers would
    otherwise have two "added_energy" sensors with no way to tell them
    apart by translation_key alone. Narrowing to this charger's own
    device_id first is what makes a plain translation_key match safe here.

    Returns None if the brand isn't one CHARGER_CONSUMPTION_SOURCE_TRANSLATION_KEYS
    recognizes (e.g. "Not installed"), no device is picked yet, or the
    device doesn't have exactly one matching sensor.
    """
    if not device_id or charger_type not in CHARGER_CONSUMPTION_SOURCE_TRANSLATION_KEYS:
        return None
    platform, translation_key = CHARGER_CONSUMPTION_SOURCE_TRANSLATION_KEYS[charger_type]
    registry = er.async_get(hass)
    matches = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.device_id == device_id
        and entry.platform == platform
        and entry.translation_key == translation_key
    ]
    return matches[0] if len(matches) == 1 else None


class PwMngtOptionsFlow(config_entries.OptionsFlow):
    """PwMngt options flow -- a multi-page wizard.

    self.config_entry comes from Home Assistant's OptionsFlow base class
    -- do not set it manually in __init__, that causes a 500 error on
    newer HA versions.

    Page 1 (async_step_init): which optional segments are enabled. No
    "name" field -- that's set once at initial setup
    (PwMngtConfigFlow.async_step_user). "Solar PV Plant" is mandatory, not
    a toggle; EV Charging / Pool / PV Surplus are the opt-in segments.
    Page 2 (async_step_solar_pv_plant): the Solar PV Plant entity map.
    Always visited.
    Pages 3-5 (async_step_ev_charging / _pool / _pv_surplus): one per
    optional segment enabled on page 1. Scaffolding -- no fields yet.

    Each page's picks are saved into the config entry's options as soon as
    it's submitted (see _save_progress), not only at the end of the
    wizard.
    """

    def __init__(self) -> None:
        super().__init__()
        # Collected across pages, written out as the final options dict
        # once the wizard reaches the end (see _finish and _save_progress).
        self._data: dict[str, Any] = {}
        # Optional segment steps still left to show, in fixed order.
        self._pending_segments: list[str] = []

    def _save_progress(self) -> None:
        """Persist what's been collected so far into the config entry's
        options immediately, not only once the wizard finishes -- so
        leaving it partway through doesn't lose progress. A field not yet
        touched this session falls back to the existing options.
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
                # "Solar PV Plant" isn't a choice here -- mandatory,
                # configured on page 2. Helper text for each toggle is in
                # strings.json -> options.step.init.data_description.
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
        """Page 2: Solar PV Plant entity map (mandatory page; not every
        field is required, see _SOLAR_PV_PLANT_FIELDS).

        Each field is an entity picker filtered to the value's unit of
        measurement. Helper text is in strings.json's data_description.

        A few fields (PV forecast today/tomorrow, spot electricity price)
        get pre-filled automatically if their one known source integration
        is installed and unambiguous -- see _AUTO_DETECT_SOURCES. The user
        can still pick something else; an existing pick is never
        overwritten.

        Fields are visually split into collapsible sections (see
        _SOLAR_PV_PLANT_GROUPS) via Home Assistant's `section()` form
        helper -- display-only, doesn't change the entity map. Each
        section's picks come back as a nested dict under the group's key,
        flattened at the top of the submit-handling below.

        Known limitation: a field that already holds a value and gets
        cleared via the picker's own "x" makes Home Assistant's
        EntitySelector reject the resulting "" before this function even
        runs. Not yet hit in practice (PV2/PV3 are the only optional
        fields, and nobody without a second/third string would have set
        one).
        """
        errors: dict[str, str] = {}
        values = dict(self.config_entry.options.get(CONF_ENTITY_MAP, {}))

        # Pre-fill fields the user hasn't picked yet (see
        # _AUTO_DETECT_SOURCES) -- never overrides an existing pick.
        for key, (platform, translation_key) in _AUTO_DETECT_SOURCES.items():
            if not values.get(key):
                detected = _auto_detect_entity_id(self.hass, platform, translation_key)
                if detected:
                    values[key] = detected

        if user_input is not None:
            # Flatten each section's nested dict (keyed by group_key) back
            # into one flat dict.
            flat_input: dict[str, Any] = {}
            for group_key, _collapsed, _field_keys in _SOLAR_PV_PLANT_GROUPS:
                flat_input.update(user_input.get(group_key) or {})

            values.update(flat_input)
            errors = {
                key: "required"
                for key, _unit, required in _SOLAR_PV_PLANT_FIELDS
                if required and not flat_input.get(key)
            }
            if errors:
                # Home Assistant doesn't render a field-specific error for a
                # field inside a section, so "base" (a banner at the top
                # of the page) is set too -- the only visible signal the
                # user gets. No field in the collapsed section is
                # required, so the missing one is always already visible.
                errors["base"] = "required_in_section"
            else:
                entity_map = dict(self.config_entry.options.get(CONF_ENTITY_MAP, {}))
                for key, _unit, _required in _SOLAR_PV_PLANT_FIELDS:
                    entity_map[key] = flat_input.get(key) or None
                self._data[CONF_ENTITY_MAP] = entity_map
                self._save_progress()
                return await self._advance()

        # No `default=` at all when there's no value yet -- NOT default=""
        # or default=None. Home Assistant's EntitySelector rejects "" as
        # invalid, so the only way to keep a field skippable is to leave
        # it out of the schema entirely until it has a value; the frontend
        # then omits an untouched field from the submitted data.
        schema_dict: dict[Any, Any] = {}
        for group_key, collapsed, field_keys in _SOLAR_PV_PLANT_GROUPS:
            group_schema_dict: dict[Any, Any] = {}
            for key in field_keys:
                marker = (
                    vol.Optional(key, default=values[key])
                    if values.get(key)
                    else vol.Optional(key)
                )
                group_schema_dict[marker] = _entity_picker(self.hass, _FIELD_UNIT_BY_KEY[key])
            schema_dict[vol.Required(group_key)] = section(
                vol.Schema(group_schema_dict), SectionConfig(collapsed=collapsed)
            )
        schema = vol.Schema(schema_dict)

        return self.async_show_form(
            step_id="solar_pv_plant", data_schema=schema, errors=errors
        )

    async def async_step_ev_charging(self, user_input: Any | None = None):
        """Page 3: EV Charging -- per charger, interleaved as (type,
        charging device, consumption source entity), i.e. Charger1 type,
        Charger1 device, Charger1 consumption source, Charger2 type,
        Charger2 device, Charger2 consumption source.

        All three fields are convenience mirrors of a persistent entity
        that stays the actual source of truth -- type mirrors
        select.<id>_type (PwM_CONFIG_SELECTS in select.py), device and
        consumption source mirror text.<id>_charging_device_id and
        text.<id>_consumption_source_entity (both PwM_CHARGER_CONFIG_TEXTS
        in text.py). Submitting here writes through to all three via a
        service call; none of them is stored in the config entry's
        options.

        All three fields are always shown, for every charger, regardless
        of its type -- tried hiding the consumption field while a charger
        is "Not installed" first, but Home Assistant's options-flow
        pages are static per render (no field can react to another
        field's still-being-edited value, and nothing can trigger a
        submit on the user's behalf), so it either stayed stuck showing
        the wrong thing until a second Submit, or never visibly reacted
        at all if you only changed the dropdown without submitting.
        Kasper's call: always-visible is simpler, and matches how the
        text entity already behaves everywhere else (it exists and
        stays visible under its own Configuration tab no matter the
        charger's type) -- filling it in for a "Not installed" charger
        just has no effect yet.

        The type field's own dropdown is narrowed to the brand(s)
        actually installed in this Home Assistant (plus "Not installed"
        and whatever the charger is currently set to) -- see
        _installed_charger_type_options(). The underlying select entity
        (select.<id>_type) keeps its full static option list regardless;
        only what this wizard page offers to pick is narrowed, so a
        user without the Wallbox or Easee integration can't pick a brand
        he doesn't have.

        The device field, right below type, is a DeviceSelector filtered
        to devices from either brand's own integration (see
        CHARGER_TYPE_INTEGRATION_DOMAINS in const.py) -- the user picks
        by device name, same as anywhere else in Home Assistant, but the
        value actually stored (in text.<id>_charging_device_id) is that
        device's registry ID, not its name -- see that entity's own
        description in text.py for why. This is what a later step needs
        to find this charger's own entities and call services on them;
        nothing reads it yet.

        The consumption field is a searchable entity picker (like the
        Solar PV Plant page's fields), restricted to sensors with
        device_class "energy" -- it used to be a plain text box the user
        had to type an entity_id into by hand. Same known limitation as
        the Solar PV Plant page (and now the device field too): no
        default= at all when there's no value yet (EntitySelector/
        DeviceSelector reject "" as invalid), so an untouched/cleared
        field is simply left out of the schema instead.

        If the consumption field is still empty, it's auto-detected from
        this charger's already-saved type + device (see
        _auto_detect_charger_consumption_entity), same idea as
        _AUTO_DETECT_SOURCES on the Solar PV Plant page -- never
        overrides an existing pick. "Already-saved" matters: since this
        page is static per render, picking a device and hitting Submit
        only pre-fills consumption on the *next* time this page opens,
        not within that same submission -- same static-per-render
        limitation the always-visible-fields decision above is about.
        """
        if user_input is not None:
            for charger in CHARGERS:
                type_entity_id = self._charger_type_entity_id(charger)
                if type_entity_id is not None:
                    await self.hass.services.async_call(
                        "select",
                        "select_option",
                        {
                            "entity_id": type_entity_id,
                            "option": user_input[f"{charger['id']}_type"],
                        },
                        blocking=True,
                    )
                device_text_entity_id = self._charger_text_entity_id(
                    charger, "charging_device_id"
                )
                if device_text_entity_id is not None:
                    await self.hass.services.async_call(
                        "text",
                        "set_value",
                        {
                            "entity_id": device_text_entity_id,
                            "value": user_input.get(f"{charger['id']}_device", ""),
                        },
                        blocking=True,
                    )
                text_entity_id = self._charger_text_entity_id(
                    charger, "consumption_source_entity"
                )
                if text_entity_id is not None:
                    await self.hass.services.async_call(
                        "text",
                        "set_value",
                        {
                            "entity_id": text_entity_id,
                            "value": user_input.get(charger["id"], ""),
                        },
                        blocking=True,
                    )
            return await self._advance()

        schema_dict: dict[Any, Any] = {}
        for charger in CHARGERS:
            description = _CHARGER_TYPE_DESCRIPTIONS[charger["id"]]
            current_type_value = self._charger_type_current_value(charger)
            schema_dict[
                vol.Optional(
                    f"{charger['id']}_type",
                    default=current_type_value,
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_installed_charger_type_options(
                        self.hass, description.options, current_type_value
                    ),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

            current_device_value = self._charger_text_current_value(
                charger, "charging_device_id"
            )
            device_marker = (
                vol.Optional(f"{charger['id']}_device", default=current_device_value)
                if current_device_value
                else vol.Optional(f"{charger['id']}_device")
            )
            schema_dict[device_marker] = selector.DeviceSelector(
                selector.DeviceSelectorConfig(
                    filter=[
                        {"integration": domain}
                        for domain in CHARGER_TYPE_INTEGRATION_DOMAINS.values()
                    ]
                )
            )

            current_consumption_value = self._charger_text_current_value(
                charger, "consumption_source_entity"
            )
            if not current_consumption_value:
                current_consumption_value = (
                    _auto_detect_charger_consumption_entity(
                        self.hass, current_type_value, current_device_value
                    )
                    or ""
                )
            consumption_marker = (
                vol.Optional(charger["id"], default=current_consumption_value)
                if current_consumption_value
                else vol.Optional(charger["id"])
            )
            schema_dict[consumption_marker] = _energy_entity_picker(self.hass)

        return self.async_show_form(
            step_id="ev_charging", data_schema=vol.Schema(schema_dict)
        )

    def _charger_type_entity_id(self, charger: dict) -> str | None:
        """entity_id of this charger's "<id>_type" select entity -- a
        hub-level PwM_CONFIG_SELECTS entity in select.py, despite being
        keyed per-charger (see _CHARGER_TYPE_DESCRIPTIONS above)."""
        registry = er.async_get(self.hass)
        return registry.async_get_entity_id(
            "select", DOMAIN, f"{self.config_entry.entry_id}_{charger['id']}_type"
        )

    def _charger_type_current_value(self, charger: dict) -> str:
        """Current value of that select entity, for pre-filling the
        field -- the description's own default if it doesn't exist yet
        or has no value."""
        entity_id = self._charger_type_entity_id(charger)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in ("unknown", "unavailable"):
            return _CHARGER_TYPE_DESCRIPTIONS[charger["id"]].default_option
        return state.state

    def _charger_text_entity_id(self, charger: dict, key: str) -> str | None:
        """entity_id of one of this charger's plain text.py fields --
        "charging_device_id" or "consumption_source_entity"
        (PwM_CHARGER_CONFIG_TEXTS in text.py) -- or None if the text
        platform hasn't registered it yet."""
        registry = er.async_get(self.hass)
        unique_id = f"{self.config_entry.entry_id}_{charger['id']}_{key}"
        return registry.async_get_entity_id("text", DOMAIN, unique_id)

    def _charger_text_current_value(self, charger: dict, key: str) -> str:
        """Current value of that text entity, for pre-filling the field
        -- "" if it doesn't exist yet or has no value."""
        entity_id = self._charger_text_entity_id(charger, key)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in ("unknown", "unavailable"):
            return ""
        return state.state

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
    """PwMngt's config flow.

    manifest.json's "single_config_entry" keeps Home Assistant from
    letting a second entry be added at all ("Add Entry" is disabled
    once one exists) -- the architecture assumes exactly one PwMngt
    instance (one Hub device, one PV Plant, etc.). Revisit this if a
    second instance ever makes sense -- e.g. a separate "simulator"
    instance for demos/debugging, which has been floated as an idea.
    """

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> PwMngtOptionsFlow:
        """Make the options flow (the "Configure" button) reachable."""
        return PwMngtOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Handle a config flow for PwMngt.

        Kept minimal on purpose -- just the name. Everything else is
        configured afterwards through Options ("Configure"), see
        PwMngtOptionsFlow above.
        """
        errors = {}

        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={"name": user_input[CONF_NAME]},
                options=user_input,
                description=f"Power Management - {user_input[CONF_NAME]}",
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=CONF_DEFAULT_NAME): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
