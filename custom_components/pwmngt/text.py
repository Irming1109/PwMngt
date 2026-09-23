"""Text entities for PwMngt chargers.

Scaffolding only: setting the value stores it locally and nothing
downstream reacts to it yet. Real behaviour comes in a later step.

One exception is visibility: a text entity whose description sets
visible_when_charger_type (see PwMngtTextEntityDescription in base.py) is
automatically hidden from the default UI unless the matching charger's
"<id>_type" select (from PwM_CONFIG_SELECTS in select.py) currently equals
that option -- e.g. "charger_identification" only makes sense for an Easee
charger, so it's hidden while a charger is set to "Wallbox" or
"Not installed". This is enforced via the entity registry's hidden_by, not
by removing the entity, so it still shows up (with a "show hidden
entities" toggle) if Kasper wants to look at it regardless.
"""

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .base import PwMngtTextEntityDescription
from .const import DOMAIN
from .devices import CHARGERS, charger_device_info, hub_device_info

LOGGER = logging.getLogger(__name__)

# Shown once, under the main "PwM" hub device's "Configuration" tab --
# not tied to a specific charger.
PwM_CONFIG_TEXTS: list[PwMngtTextEntityDescription] = [
]

# Shown under each charger device's "Configuration" tab.
PwM_CHARGER_CONFIG_TEXTS: list[PwMngtTextEntityDescription] = [
    # Old key="ladeboks_1_easee_sn" ("ladeboks_2_easee_sn")
    # Old name="Ladeboks_1_Easee_SN" (Ladeboks_2_Easee_SN)
    PwMngtTextEntityDescription(
        key="charger_identification",
        name="Charger identification (Easee serial number)",
        icon="mdi:identifier",
        entity_category=EntityCategory.CONFIG,
        default_value="",
        visible_when_charger_type="Easee",
    ),
    # New (no Node-RED equivalent by this name -- Claus's flow read a
    # fixed entity_id, sensor.wallbox_portal_added_energy, hardcoded in
    # his function node). The entity that reports this charger's own
    # added-energy counter, read by PwMngtChargerConsumptionSensor
    # (data/consumption_charger_data.py) to compute its since-midnight
    # consumption. Deliberately a plain typed entity_id, not part of
    # entry.options[CONF_ENTITY_MAP] like every other PwMngt source --
    # CONF_ENTITY_MAP fields are all picked via _entity_picker()'s
    # EntitySelector in the Options wizard, but this needs to exist as
    # its own persistent, always-visible entity (Kasper's call) so it can
    # also be pre-filled/edited from the EV Charging wizard page without
    # a second, disconnected source of truth. No visible_when_charger_type
    # -- unlike charger_identification, this stays visible under the
    # charger's own Configuration tab regardless of charger type; only
    # the Options wizard's own field hides/shows based on charger type.
    PwMngtTextEntityDescription(
        key="consumption_source_entity",
        name="Consumption source entity",
        icon="mdi:ev-station",
        entity_category=EntityCategory.CONFIG,
        default_value="",
    ),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up text entities for the hub device and each configured charger."""
    entities = []

    for description in PwM_CONFIG_TEXTS:
        entities.append(PwMngtText(description, entry))

    for charger in CHARGERS:
        for description in PwM_CHARGER_CONFIG_TEXTS:
            entities.append(PwMngtText(description, entry, charger))

    async_add_entities(entities)


class PwMngtText(TextEntity):
    """A scaffolded PwMngt text entity. No live behaviour yet."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: PwMngtTextEntityDescription,
        entry: ConfigEntry,
        charger: dict | None = None,
    ) -> None:
        self.entity_description = description
        self._entry = entry
        self._charger = charger
        if charger is None:
            # Hub-level entity: belongs to the general "PwM" device.
            self._attr_unique_id = f"{entry.entry_id}_{description.key}"
            self._attr_device_info = hub_device_info(entry)
        else:
            self._attr_unique_id = f"{entry.entry_id}_{charger['id']}_{description.key}"
            self._attr_device_info = charger_device_info(entry, charger)
        self._attr_native_value = description.default_value

    async def async_set_value(self, value: str) -> None:
        """Store the value locally. Functionality is not implemented yet."""
        LOGGER.info(
            "%s set to '%s' (no functionality implemented yet)", self.entity_id, value
        )
        self._attr_native_value = value
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Start tracking the matching charger-type select, if any."""
        await super().async_added_to_hass()
        self._start_visibility_tracking()

    def _start_visibility_tracking(self) -> None:
        """Hide this entity unless its charger's type select matches.

        Only applies to per-charger entities whose description sets
        visible_when_charger_type (see base.py). Hides/shows the entity via
        the entity registry's hidden_by so it reacts live to the select
        changing, without needing a reload -- and never overrides a
        hidden_by the user set manually themselves.
        """
        wanted_option = getattr(self.entity_description, "visible_when_charger_type", None)
        if wanted_option is None or self._charger is None:
            return

        registry = er.async_get(self.hass)
        type_unique_id = f"{self._entry.entry_id}_{self._charger['id']}_type"

        @callback
        def _apply(current_option: str | None) -> None:
            target_hidden = None if current_option == wanted_option else er.RegistryEntryHider.INTEGRATION
            reg_entry = registry.async_get(self.entity_id)
            if reg_entry is None:
                return
            # Never fight a hidden_by the user set from the UI themselves.
            if reg_entry.hidden_by not in (None, er.RegistryEntryHider.INTEGRATION):
                return
            if reg_entry.hidden_by != target_hidden:
                registry.async_update_entity(self.entity_id, hidden_by=target_hidden)

        def _subscribe(type_entity_id: str) -> None:
            state = self.hass.states.get(type_entity_id)
            _apply(state.state if state else None)

            @callback
            def _on_change(event) -> None:
                new_state = event.data.get("new_state")
                _apply(new_state.state if new_state else None)

            self.async_on_remove(
                async_track_state_change_event(self.hass, [type_entity_id], _on_change)
            )

        type_entity_id = registry.async_get_entity_id("select", DOMAIN, type_unique_id)
        if type_entity_id:
            _subscribe(type_entity_id)
            return

        # First-ever setup: the charger-type select's platform may not have
        # finished registering it yet. Retry shortly -- after that it will
        # always resolve immediately, since registry entries persist across
        # restarts.
        @callback
        def _retry(_now) -> None:
            retried_entity_id = registry.async_get_entity_id("select", DOMAIN, type_unique_id)
            if retried_entity_id:
                _subscribe(retried_entity_id)
            else:
                LOGGER.warning(
                    "Could not find the charger-type select (%s) to drive "
                    "visibility of %s; leaving it hidden",
                    type_unique_id,
                    self.entity_id,
                )
                _apply(None)

        self.async_on_remove(async_call_later(self.hass, 2, _retry))
