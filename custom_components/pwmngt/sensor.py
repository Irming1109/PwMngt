import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import slugify as util_slugify

from .api import PwMngtAPI
from .base import PwMngtSensorEntityDescription
from .const import DOMAIN, API_OBJ, CONF_ENTITY_MAP, ENTITY_KEY_BATTERY_SOC
from .devices import CHARGERS, charger_device_info, hub_device_info, pv_device_info

LOGGER = logging.getLogger(__name__)

PwM_SENSORS = [
    PwMngtSensorEntityDescription(
        key="hello_world_str",
        name="Hello world",
        entity_category=None,
        icon="mdi:flash",
        value_fn=lambda pwmngt: pwmngt.get_hello_world2(),
    ),
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):   
    """Setup sensors."""
    sensors = []

    for sensor in PwM_SENSORS:
        entity = PwMngtSensor(sensor, hass, entry)
        LOGGER.info("Added sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description in PwM_HUB_SENSORS:
        entity = PwMngtHubBalanceSensor(description, entry)
        LOGGER.info("Added hub sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description, entity_map_key in PwM_PV_MIRROR_SENSORS:
        entity = PwMngtPvMirrorSensor(description, entry, entity_map_key)
        LOGGER.info("Added PV mirror sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description, source_entity_id in PwM_HUB_MIRROR_SENSORS:
        entity = PwMngtHubMirrorSensor(description, entry, source_entity_id)
        LOGGER.info("Added hub mirror sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for charger in CHARGERS:
        for description in PwM_CHARGER_SENSORS:
            entity = PwMngtChargerSensor(description, entry, charger)
            LOGGER.info("Added charger sensor with entity_id '%s'", entity.entity_id)
            sensors.append(entity)

    async_add_entities(sensors)


class PwMngtSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, description: PwMngtSensorEntityDescription, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__()
        self.entity_description = description
        self._config = entry
        self._hass = hass
        self.api: PwMngtAPI = hass.data[DOMAIN][API_OBJ]

        self._attr_unique_id = util_slugify(
            f"{self.entity_description.key}_{self._config.entry_id}"
        )
        self._attr_should_poll = True

        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._config.entry_id)},
            "name": "PwM",
            "manufacturer": "Power Management",
        }

        self._attr_native_unit_of_measurement = (
            self.entity_description.unit_of_measurement
        )

        async_dispatcher_connect(
            self._hass,
            util_slugify(self.entity_description.update_signal),
            self.handle_update,
        )

    async def handle_attributes(self) -> None:
        """Handle attributes. No extra attributes yet -- placeholder for later."""

    async def handle_update(self) -> None:
        """Handle data update."""
        try:
            self._attr_native_value = self.entity_description.value_fn(
                self._hass.data[DOMAIN][API_OBJ]
            )

            LOGGER.info(
                "Setting value for '%s' to: %s",
                self.entity_id,
                self._attr_native_value,
            )
            await self.handle_attributes()
            self._attr_available = True
 
        except Exception as e:
            if self._attr_available:
                LOGGER.error("The PwMngt API made an invalid response: %s", e)
            self._attr_available = False

    async def async_added_to_hass(self):
        await self.handle_update()
        return await super().async_added_to_hass()

# ---------------------------------------------------------------------------
# Charger status sensors (read-only, scaffolding only).
#
# These have no value_fn / API backing yet -- they report None until real
# data (from Node-RED, Easee, or wherever "Ladeboks 1" ends up being read
# from) is wired up in a later step.
# ---------------------------------------------------------------------------

PwM_CHARGER_SENSORS: list[SensorEntityDescription] = [
    # Old key="ladeboks_1_ampere" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 ampere" (Ladeboks 2 ... equivalent for Charger2)
    SensorEntityDescription(
        key="current",
        name="Current",
        icon="mdi:current-ac",
        native_unit_of_measurement="A",
    ),
    # Old key="ladeboks_1_effekt" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 effekt" (Ladeboks 2 ... equivalent for Charger2)
    SensorEntityDescription(
        key="power",
        name="Power",
        icon="mdi:flash",
        native_unit_of_measurement="W",
    ),
    # Old key="ladeboks_1_forbrug" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 forbrug" (Ladeboks 2 ... equivalent for Charger2)
    SensorEntityDescription(
        key="consumption",
        name="Consumption",
        icon="mdi:lightning-bolt",
        native_unit_of_measurement="kWh",
    ),
    # Old key="ladeboks_1_km" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 km" (Ladeboks 2 ... equivalent for Charger2)
    SensorEntityDescription(
        key="km_charged",
        name="Km charged",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement="km",
    ),
    # Old key="ladeboks_1_mangler" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 mangler" (Ladeboks 2 ... equivalent for Charger2)
    SensorEntityDescription(
        key="remaining",
        name="Remaining",
        icon="mdi:progress-question",
    ),
    # Old key="ladeboks_1_faser_bestilt" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks_1_faser_bestilt" (Ladeboks 2 ... equivalent for Charger2)  # source was a Select entity in Node-RED; here it's an ENUM sensor
    SensorEntityDescription(
        key="ordered_phases",
        name="Ordered phases",
        icon="mdi:sine-wave",
        device_class=SensorDeviceClass.ENUM,
        options=["1", "3"],
    ),
    # Old key="ladeboks_1_tilstand" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 tilstand" (Ladeboks 2 ... equivalent for Charger2)  # source was a Select entity in Node-RED; here it's an ENUM sensor
    SensorEntityDescription(
        key="state",
        name="State",
        icon="mdi:information-outline",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "No car connected",
            "Inactive",
            "Paused",
            "Active - solar",
            "Active - manual",
            "No demand",
            "Unknown",
        ],
    ),
    # Old key="ladeboks_1_pv_ladning_aktiv" (ladeboks_2_... equivalent for Charger2)
    # Old name="Ladeboks 1 PV ladning aktiv" (Ladeboks 2 ... equivalent for Charger2)  # source was a Select entity in Node-RED; here it's an ENUM sensor
    SensorEntityDescription(
        key="surplus_charging_active",
        name="Surplus charging active",
        icon="mdi:solar-power",
        device_class=SensorDeviceClass.ENUM,
        options=["No", "Yes"],
    ),
]


class PwMngtChargerSensor(SensorEntity):
    """A scaffolded, read-only PwMngt charger sensor. No live value yet."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        charger: dict,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{charger['id']}_{description.key}"
        self._attr_device_info = charger_device_info(entry, charger)
        self._attr_native_value = None


# ---------------------------------------------------------------------------
# Hub-level "power balance" sensor (read-only, scaffolding only).
#
# The live dashboard/Node-RED side has 7 sibling sensors, all reporting a
# net power balance (W) averaged/corrected over different windows. None of
# the 7 need their own History/Statistics graph in Home Assistant -- the
# only reason to use separate entities would be per-value graphing, and the
# stated use here is purely programmatic (Node-RED / PwMngt's own internal
# logic reading all 7 current numbers from one place). So instead of 7
# entities, this bundles all 7 as attributes on a single entity.
#
# Old key="balance_30_sek" (sensor.balance_30_sek) -> attribute "balance_30_sec"
# Old key="balance_1_min" (sensor.balance_1_min) -> attribute "balance_1_min"
# Old key="balance_5_min" (sensor.balance_5_min) -> attribute "balance_5_min"
# Old key="balance_15_min" (sensor.balance_15_min) -> attribute "balance_15_min"
# Old key="balance_15_min_korrigeret" (sensor.balance_15_min_korrigeret)
#   -> attribute "balance_15_min_corrected"
# Old key="balance_15_min_korrigeret_m_ladere"
#   (sensor.balance_15_min_korrigeret_m_ladere)
#   -> attribute "balance_15_min_corrected_with_chargers"
# Old key="balance_30_min" (sensor.balance_30_min) -> attribute "balance_30_min"
#
# No value_fn / real calculation wired up yet -- the state and every
# attribute report None until PwMngt computes them itself in a later step.
PwM_HUB_BALANCE_ATTRIBUTES: list[str] = [
    "balance_30_sec",
    "balance_1_min",
    "balance_5_min",
    "balance_15_min",
    "balance_15_min_corrected",
    "balance_15_min_corrected_with_chargers",
    "balance_30_min",
]

PwM_HUB_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="power_balance",
        name="Power balance",
        icon="mdi:scale-balance",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement="W",
    ),
]


class PwMngtHubBalanceSensor(SensorEntity):
    """A scaffolded, read-only PwMngt hub sensor bundling the 7 balance
    values above as attributes. No live value yet -- see the comment block
    above PwM_HUB_BALANCE_ATTRIBUTES.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device_info(entry)
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            key: None for key in PwM_HUB_BALANCE_ATTRIBUTES
        }


# ---------------------------------------------------------------------------
# PV-device sensors that live-mirror another entity's state (not
# scaffolding -- these have a real value from day one, no "later step").
#
# The source entity is picked by the user in Options -> Power Management
# (page 2 of the wizard, see PwMngtOptionsFlow in config_flow.py) and
# stored in entry.options[CONF_ENTITY_MAP] keyed by ENTITY_KEY_*, rather
# than assumed from an "inverter name" naming convention -- entity names
# aren't reliable across different users' Home Assistant setups, so PwMngt
# no longer guesses them. The second tuple element below is that
# ENTITY_KEY_* lookup key, resolved to an actual entity_id per-entry in
# PwMngtPvMirrorSensor.__init__.
# ---------------------------------------------------------------------------

PwM_PV_MIRROR_SENSORS: list[tuple[SensorEntityDescription, str]] = [
    # Old key="batteri_soc" (sensor.batteri_soc)
    # NOTE: sensor.batteri_soc is itself just a read-only mirror of the
    # inverter's own battery SoC entity.
    (
        SensorEntityDescription(
            key="battery_soc",
            name="Battery SoC",
            icon="mdi:battery",
            device_class=SensorDeviceClass.BATTERY,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="%",
        ),
        ENTITY_KEY_BATTERY_SOC,
    ),
]


# ---------------------------------------------------------------------------
# Hub-device sensors that live-mirror a fixed external entity (not
# scaffolding -- real values from day one). Unlike PwM_PV_MIRROR_SENSORS,
# the source entity_id here doesn't depend on any PwMngt configuration --
# it's owned entirely by another integration.
# ---------------------------------------------------------------------------

PwM_HUB_MIRROR_SENSORS: list[tuple[SensorEntityDescription, str]] = [
    # Old key="el_kobspris_variabel" (sensor.el_kobspris_variabel)
    # Was a template that hand-combined the Energi Data Service spot price
    # with tariffs looked up from its "tariffs" attribute (nordpool_spotpris
    # was also referenced but never actually used in the result). The
    # Stromligning integration -- configured with the real EnergiFyn
    # product ("Strom til kostpris") -- computes this exact figure natively
    # (verified live: its 5 component sensors sum to the exact current
    # price), so this just mirrors it instead of recalculating it.
    (
        SensorEntityDescription(
            key="spot_electricity_price",
            name="Spot electricity price",
            icon="mdi:cash",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="kr/kWh",
        ),
        "sensor.stromligning_current_price_vat_2",
    ),
]


class _PwMngtMirrorSensor(SensorEntity):
    """Shared base for PwMngt sensors that live-mirror another entity's
    state 1:1. A subclass sets self._source_entity_id in __init__ (None if
    there's nothing to mirror yet) before this class's async_added_to_hass
    runs.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    async def async_added_to_hass(self) -> None:
        """Start mirroring the source entity's state, if one is configured."""
        await super().async_added_to_hass()

        if self._source_entity_id is None:
            return

        @callback
        def _handle_source_update(event) -> None:
            self._apply_source_state(event.data.get("new_state"))

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], _handle_source_update
            )
        )
        self._apply_source_state(self.hass.states.get(self._source_entity_id))

    @callback
    def _apply_source_state(self, source_state) -> None:
        """Copy the source entity's state onto this entity."""
        if source_state is None or source_state.state in ("unknown", "unavailable"):
            self._attr_native_value = None
            self._attr_available = source_state is not None
        else:
            try:
                self._attr_native_value = float(source_state.state)
            except ValueError:
                self._attr_native_value = source_state.state
            self._attr_available = True
        self.async_write_ha_state()


class PwMngtPvMirrorSensor(_PwMngtMirrorSensor):
    """A PwM PV-device sensor that live-mirrors another entity's state,
    where the source entity_id depends on the configured inverter name.
    """

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        entity_map_key: str,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._source_entity_id = entity_map.get(entity_map_key) or None
        if self._source_entity_id is None:
            # Not picked yet (e.g. an entry created before this field
            # existed, or the user hasn't set it) -- nothing to mirror
            # until it's set via the integration's options ("Configure" ->
            # Power Management).
            LOGGER.warning(
                "PwMngt: no source entity configured for '%s' (set it under "
                "Options -> Power Management), it will stay unavailable",
                self.entity_description.key,
            )


class PwMngtHubMirrorSensor(_PwMngtMirrorSensor):
    """A PwM hub-device sensor that live-mirrors a fixed external entity's
    state -- for values sourced entirely from another integration (e.g.
    Stromligning), where PwMngt itself has nothing per-install to
    configure, unlike PwMngtPvMirrorSensor's inverter-name-based lookup.
    """

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        source_entity_id: str,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False
        self._source_entity_id = source_entity_id
