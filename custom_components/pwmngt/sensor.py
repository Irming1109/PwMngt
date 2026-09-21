import logging
from collections import deque
from datetime import timedelta
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify as util_slugify

from .api import PwMngtAPI
from .base import PwMngtSensorEntityDescription
from .const import (
    DOMAIN,
    API_OBJ,
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
)
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

    for description in PwM_BALANCE_DATA_SENSORS:
        entity = PwMngtBalanceDataSensor(description, entry)
        LOGGER.info("Added balance-data sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description, entity_map_key in PwM_PV_MIRROR_SENSORS:
        entity = PwMngtPvMirrorSensor(description, entry, entity_map_key)
        LOGGER.info("Added PV mirror sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description, entity_map_keys in PwM_PV_SUM_SENSORS:
        entity = PwMngtPvSumSensor(description, entry, entity_map_keys)
        LOGGER.info("Added PV sum sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description, entity_map_key, split_fn in PwM_PV_SPLIT_SENSORS:
        entity = PwMngtGridSplitSensor(description, entry, entity_map_key, split_fn)
        LOGGER.info("Added PV split sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description, source_entity_id in PwM_PV_INTEGRAL_SENSORS:
        entity = PwMngtEnergyIntegrationSensor(description, entry, source_entity_id)
        LOGGER.info("Added PV energy-integration sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description, entity_map_key in PwM_HUB_MIRROR_SENSORS:
        entity = PwMngtHubMirrorSensor(description, entry, entity_map_key)
        LOGGER.info("Added hub mirror sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description in PwM_PV_PLACEHOLDER_SENSORS:
        entity = PwMngtPlaceholderSensor(description, entry)
        LOGGER.info("Added PV placeholder sensor with entity_id '%s'", entity.entity_id)
        sensors.append(entity)

    for description in PwM_CONSUMPTION_DATA_SENSORS:
        entity = PwMngtConsumptionDataSensor(description, entry)
        LOGGER.info("Added PV consumption-data sensor with entity_id '%s'", entity.entity_id)
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
# Charger status sensors (read-only, scaffolding only). No value_fn / API
# backing yet -- each reports None until wired up.
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
# Hub-level "power balance" sensor. Bundles 7 net-power-balance values
# (averaged over different windows) as attributes on a single entity
# rather than 7 separate sensors -- none need their own History/Statistics
# graph, they're read programmatically.
#
# Naming convention: a "*_data" key/name marks a sensor whose value is
# maintained by an ongoing internal trigger (here, a 10-second timer)
# rather than mirroring one external entity. PwM_CONSUMPTION_DATA_SENSORS
# further down follows the same convention.
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
# "_corrected" is a placeholder equal to "balance_15_min" for now --
# mining correction isn't implemented yet. "_corrected_with_chargers" is
# genuinely computed from PwM Charger1/2's Power sensors, but reads the
# same as "balance_15_min" in practice until those sensors have live
# values (still scaffolding -- see PwM_CHARGER_SENSORS).
PwM_BALANCE_DATA_ATTRIBUTES: list[str] = [
    "balance_30_sec",
    "balance_1_min",
    "balance_5_min",
    "balance_15_min",
    "balance_15_min_corrected",
    "balance_15_min_corrected_with_chargers",
    "balance_30_min",
]

PwM_BALANCE_DATA_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="balance_data",
        name="Balance data",
        icon="mdi:scale-balance",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement="W",
    ),
]

# How often PwMngtBalanceDataSensor samples Grid+Battery power, and how many
# of those samples each rolling window covers (10 s/sample, so 3 = 30 sec,
# 180 = 30 min).
_BALANCE_SAMPLE_INTERVAL = timedelta(seconds=10)
_BALANCE_WINDOW_SAMPLES: dict[str, int] = {
    "balance_30_sec": 3,
    "balance_1_min": 6,
    "balance_5_min": 30,
    "balance_15_min": 90,
    "balance_30_min": 180,
}


class PwMngtBalanceDataSensor(SensorEntity):
    """A PwM hub sensor bundling the 7 balance values above as attributes.

    Every 10 seconds, samples Grid power plus Battery power (skipped
    while the Forced charge placeholder sensor -- see
    PwM_PV_PLACEHOLDER_SENSORS -- reports "on") into a rolling
    180-sample (30-minute) window, then averages the window's most recent
    N samples for each attribute in PwM_BALANCE_DATA_ATTRIBUTES and negates
    it, so a positive balance means surplus power. balance_15_min_corrected_
    with_chargers adds back the last 15 minutes' average combined charger
    power (PwM Charger1/2's Power sensors), so it reads what the balance
    would be without the EV chargers' own draw. Each average divides by
    its full window size even before 180 samples have accumulated (e.g.
    fresh after a Home Assistant restart), so early readings are pulled
    toward zero until the window fills up -- same behavior as the
    Node-RED "Balance" function this was ported from.
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
        self._attr_available = False
        self._attr_extra_state_attributes = {
            key: None for key in PwM_BALANCE_DATA_ATTRIBUTES
        }
        self._samples: deque[float] = deque(maxlen=180)
        self._charger_samples: deque[float] = deque(maxlen=180)
        # PwM Charger1/2's own Power sensors, resolved by unique_id (see
        # _read_charger_power_total) since they're not entity_map fields
        # either. Missing/not-yet-live chargers contribute 0.
        self._charger_unique_ids = [
            f"{entry.entry_id}_{charger['id']}_power" for charger in CHARGERS
        ]
        self._charger_entity_ids: list[str | None] = [None] * len(
            self._charger_unique_ids
        )

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._grid_entity_id = entity_map.get(ENTITY_KEY_GRID_POWER) or None
        self._battery_entity_id = entity_map.get(ENTITY_KEY_BATTERY_POWER) or None
        # Not an entity_map field -- PwMngt's own Forced charge placeholder
        # sensor (see PwM_PV_PLACEHOLDER_SENSORS), resolved by unique_id
        # since its entity_id isn't predictable ahead of registration.
        self._forced_charge_unique_id = f"{entry.entry_id}_pv_forced_charge"
        self._forced_charge_entity_id: str | None = None

        if not self._grid_entity_id or not self._battery_entity_id:
            LOGGER.warning(
                "PwMngt: no source entity configured for '%s' (set Grid "
                "and Battery power under Options -> Solar PV Plant), it "
                "will stay unavailable",
                self.entity_description.key,
            )

    async def async_added_to_hass(self) -> None:
        """Start sampling every 10 seconds, if Grid and Battery power are
        configured.
        """
        await super().async_added_to_hass()

        if not self._grid_entity_id or not self._battery_entity_id:
            return

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._sample, _BALANCE_SAMPLE_INTERVAL
            )
        )
        self._sample(None)

    @callback
    def _sample(self, _now) -> None:
        """Take one 10-second sample and recompute the rolling averages."""
        grid_power = self._read_float(self._grid_entity_id)
        battery_power = self._read_float(self._battery_entity_id)
        if grid_power is None or battery_power is None:
            return

        status = grid_power
        if not self._is_forced_charging():
            status += battery_power
        self._samples.append(status)
        self._charger_samples.append(self._read_charger_power_total())

        recent = list(self._samples)
        charger_recent = list(self._charger_samples)
        values = {
            key: round(-(sum(recent[-window:]) / window))
            for key, window in _BALANCE_WINDOW_SAMPLES.items()
        }
        values["balance_15_min_corrected"] = values["balance_15_min"]
        kor_bil_window = _BALANCE_WINDOW_SAMPLES["balance_15_min"]
        combined = sum(recent[-kor_bil_window:]) - sum(charger_recent[-kor_bil_window:])
        values["balance_15_min_corrected_with_chargers"] = round(
            -(combined / kor_bil_window)
        )

        self._attr_native_value = values["balance_30_sec"]
        self._attr_extra_state_attributes = values
        self._attr_available = True
        self.async_write_ha_state()

    def _read_charger_power_total(self) -> float:
        """Sum of all configured chargers' current Power reading.

        Resolved lazily by unique_id, since PwM Charger1/2's entity_ids
        aren't predictable ahead of registration. A charger with no live
        reading yet (still scaffolding -- see PwM_CHARGER_SENSORS)
        contributes 0 rather than blocking the whole calculation.
        """
        registry = er.async_get(self.hass)
        total = 0.0
        for index, unique_id in enumerate(self._charger_unique_ids):
            entity_id = self._charger_entity_ids[index]
            if not entity_id:
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
                self._charger_entity_ids[index] = entity_id
            if not entity_id:
                continue
            total += self._read_float(entity_id) or 0.0
        return total

    def _is_forced_charging(self) -> bool:
        """Whether PwMngt's own Forced charge placeholder currently
        reports "on". Always False until the logic that sets it (see
        PwM_PV_PLACEHOLDER_SENSORS) is built.
        """
        if not self._forced_charge_entity_id:
            self._forced_charge_entity_id = er.async_get(
                self.hass
            ).async_get_entity_id("sensor", DOMAIN, self._forced_charge_unique_id)
        if not self._forced_charge_entity_id:
            return False
        state = self.hass.states.get(self._forced_charge_entity_id)
        return state is not None and state.state == "on"

    def _read_float(self, entity_id: str) -> float | None:
        """Current state of `entity_id` as a float, or None if it's
        missing, unavailable, or not numeric.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# PV-device sensors that live-mirror another entity's state. The source
# entity is picked by the user in Options -> Solar PV Plant and stored in
# entry.options[CONF_ENTITY_MAP] keyed by ENTITY_KEY_*. The second tuple
# element below is that lookup key, resolved to an entity_id in
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
    # Old key="batteri_ladet" (sensor.batteri_ladet), from Node-RED.
    (
        SensorEntityDescription(
            key="battery_pv_charged",
            name="Battery PV charged",
            icon="mdi:battery-arrow-up-outline",
            device_class=SensorDeviceClass.ENERGY_STORAGE,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement="kWh",
        ),
        ENTITY_KEY_BATTERY_PV_CHARGED,
    ),
    # Old key="batteri_afladet" (sensor.batteri_afladet), from Node-RED.
    (
        SensorEntityDescription(
            key="battery_pv_discharged",
            name="Battery PV discharged",
            icon="mdi:battery-arrow-down-outline",
            device_class=SensorDeviceClass.ENERGY_STORAGE,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement="kWh",
        ),
        ENTITY_KEY_BATTERY_PV_DISCHARGED,
    ),
    # Old key="batteri effekt" (sensor.batteri_effekt), from Node-RED.
    (
        SensorEntityDescription(
            key="battery_power",
            name="Battery power",
            icon="mdi:battery-sync-outline",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="W",
        ),
        ENTITY_KEY_BATTERY_POWER,
    ),
    # Old key="PV1" (sensor.pv1), from Node-RED.
    (
        SensorEntityDescription(
            key="pv1_power",
            name="PV1 power",
            icon="mdi:solar-power",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="W",
        ),
        ENTITY_KEY_PV1_POWER,
    ),
    # Old key="PV2" (sensor.pv2), from Node-RED.
    (
        SensorEntityDescription(
            key="pv2_power",
            name="PV2 power",
            icon="mdi:solar-power",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="W",
        ),
        ENTITY_KEY_PV2_POWER,
    ),
    # Old key="PV3" (sensor.pv3), from Node-RED.
    (
        SensorEntityDescription(
            key="pv3_power",
            name="PV3 power",
            icon="mdi:solar-power",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="W",
        ),
        ENTITY_KEY_PV3_POWER,
    ),
    # Old key="pv forbrug direkte" (sensor.pv_forbrug_direkte), from Node-RED.
    (
        SensorEntityDescription(
            key="pv_direct_consumption",
            name="PV direct consumption",
            icon="mdi:home-import-outline",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="W",
        ),
        ENTITY_KEY_PV_DIRECT_CONSUMPTION,
    ),
    # Old key="pv_forbrug" (sensor.pv_forbrug), from Node-RED.
    (
        SensorEntityDescription(
            key="pv_total_consumption",
            name="PV total consumption",
            icon="mdi:counter",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement="kWh",
        ),
        ENTITY_KEY_PV_TOTAL_CONSUMPTION,
    ),
    # Old key="Grid" (sensor.grid), from Node-RED.
    (
        SensorEntityDescription(
            key="grid_power",
            name="Grid power",
            icon="mdi:transmission-tower",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="W",
        ),
        ENTITY_KEY_GRID_POWER,
    ),
    # A single whole-plant forecast entity, not one per PV string -- most
    # installs only have one (e.g. from a Forecast.Solar-style integration).
    (
        SensorEntityDescription(
            key="pv_forecast_today",
            name="PV forecast today",
            icon="mdi:weather-sunny",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="kWh",
        ),
        ENTITY_KEY_PV_FORECAST_TODAY,
    ),
    (
        SensorEntityDescription(
            key="pv_forecast_tomorrow",
            name="PV forecast tomorrow",
            icon="mdi:weather-sunny",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="kWh",
        ),
        ENTITY_KEY_PV_FORECAST_TOMORROW,
    ),
]


# ---------------------------------------------------------------------------
# PV-device sensors for values PwMngt computes internally itself, rather
# than mirror an external entity -- same scaffold-now-compute-later
# pattern as PwM_CONSUMPTION_DATA_SENSORS below. Each stays at
# native_value=None ("unknown") until that calculation lands.
# ---------------------------------------------------------------------------

PwM_PV_PLACEHOLDER_SENSORS: list[SensorEntityDescription] = [
    # Old key="batteri target" (sensor.batteri_target), from Node-RED.
    SensorEntityDescription(
        key="battery_nightly_target",
        name="Battery nightly target",
        icon="mdi:battery-clock-outline",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Old key="solproduktion 11_16" (sensor.solproduktion_11_16), from Node-RED.
    SensorEntityDescription(
        key="pv_forecast_daytime_today",
        name="PV forecast daytime today",
        icon="mdi:sun-clock-outline",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="kWh",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Old key="solproduktion_imorgen_11_16" (sensor.solproduktion_imorgen_11_16), from Node-RED.
    SensorEntityDescription(
        key="pv_forecast_daytime_tomorrow",
        name="PV forecast daytime tomorrow",
        icon="mdi:sun-clock-outline",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="kWh",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Old key="tvangslad_batteri" (input_boolean.tvangslad_batteri), from
    # Node-RED. Not user-mapped -- PwMngt will set this itself once the
    # battery forced-charge logic is built. PwMngtBalanceDataSensor reads
    # it to decide whether to skip Battery power in its calculation.
    SensorEntityDescription(
        key="forced_charge",
        name="Forced charge",
        icon="mdi:battery-lock",
        device_class=SensorDeviceClass.ENUM,
        options=["on", "off"],
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


class PwMngtPlaceholderSensor(SensorEntity):
    """A PV-device sensor for a value PwMngt computes internally itself
    (see PwM_PV_PLACEHOLDER_SENSORS). Stays at native_value=None until
    then, same pattern as PwMngtConsumptionDataSensor further down.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, description: SensorEntityDescription, entry: ConfigEntry) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None


# ---------------------------------------------------------------------------
# PV-device sensors computed as the live sum of several other configured
# entities' states, rather than a 1:1 mirror of a single one. Each tuple's
# second element is the ordered set of ENTITY_KEY_* to add together.
# ---------------------------------------------------------------------------

PwM_PV_SUM_SENSORS: list[tuple[SensorEntityDescription, tuple[str, ...]]] = [
    (
        SensorEntityDescription(
            key="pv_total",
            name="PV total",
            icon="mdi:solar-power-variant",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="W",
        ),
        (ENTITY_KEY_PV1_POWER, ENTITY_KEY_PV2_POWER, ENTITY_KEY_PV3_POWER),
    ),
]


# ---------------------------------------------------------------------------
# PV-device sensors computed as a live, sign-based split of a single other
# configured entity's state -- e.g. "Grid" power split into an import-only
# and an export-only power sensor. Each tuple is (description,
# entity_map_key of the single source, split_fn(value) -> value).
#
# Home Assistant's built-in "Integration - Riemann sum" helper does the
# W -> kWh integration over time, pointed at these two sensors.
# ---------------------------------------------------------------------------

PwM_PV_SPLIT_SENSORS: list[tuple[SensorEntityDescription, str, Callable[[float], float]]] = [
    # Old key="grid_kob" (sensor.grid_kob), Jinja template (positive split of "Grid").
    (
        SensorEntityDescription(
            key="grid_import_power",
            name="Grid import power",
            icon="mdi:transmission-tower-import",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="W",
        ),
        ENTITY_KEY_GRID_POWER,
        lambda value: value if value > 0 else 0.0,
    ),
    # Old key="grid_salg" (sensor.grid_salg), Jinja template (negative split of "Grid").
    (
        SensorEntityDescription(
            key="grid_export_power",
            name="Grid export power",
            icon="mdi:transmission-tower-export",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="W",
        ),
        ENTITY_KEY_GRID_POWER,
        lambda value: -value if value < 0 else 0.0,
    ),
]


# ---------------------------------------------------------------------------
# PV-device sensors that integrate one of the split power (W) sensors above
# over time into a cumulative energy (kWh) total -- a left-Riemann-sum
# approximation, same method as Home Assistant's built-in "Integration -
# Riemann sum" helper (`platform: integration`, method: left, unit_prefix:
# k, round: 2). Each tuple is (description, source_entity_id).
#
# source_entity_id is the PwMngt split sensor's own predictable entity_id
# (domain "sensor" + slug of its fixed name from PwM_PV_SPLIT_SENSORS
# above) -- not something the user maps.
# ---------------------------------------------------------------------------

PwM_PV_INTEGRAL_SENSORS: list[tuple[SensorEntityDescription, str]] = [
    # Old key="integration_grid_samlet_kob". Integrates grid_import_power.
    (
        SensorEntityDescription(
            key="energy_import",
            name="Energy import",
            icon="mdi:import",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement="kWh",
            suggested_display_precision=2,
        ),
        "sensor.grid_import_power",
    ),
    # Old key="integration_grid_samlet_salg". Integrates grid_export_power.
    (
        SensorEntityDescription(
            key="energy_export",
            name="Energy export",
            icon="mdi:export",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement="kWh",
            suggested_display_precision=2,
        ),
        "sensor.grid_export_power",
    ),
]


# ---------------------------------------------------------------------------
# Hub-device sensors that live-mirror another entity's state, picked by
# the user in Options -> Solar PV Plant. Same tuple shape and wiring as
# PwM_PV_MIRROR_SENSORS: second element is the ENTITY_KEY_* entity_map
# lookup key, resolved to an entity_id in PwMngtHubMirrorSensor.__init__.
# ---------------------------------------------------------------------------

PwM_HUB_MIRROR_SENSORS: list[tuple[SensorEntityDescription, str]] = [
    # Old key="el_kobspris_variabel"
    # Computed natively by the Stromligning integration.
    (
        SensorEntityDescription(
            key="spot_electricity_price",
            name="Spot electricity price",
            icon="mdi:cash",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="kr/kWh",
        ),
        ENTITY_KEY_SPOT_ELECTRICITY_PRICE,
    ),
]


# ---------------------------------------------------------------------------
# PV-device "consumption data" sensor (read-only, scaffolding only).
# Bundles 12 household-consumption-average values as attributes on a
# single diagnostic entity -- a "*_data" sensor, same naming convention
# and pattern as PwMngtBalanceDataSensor above.
# No calculation logic yet -- state and every attribute report None.
#
# Old Node-RED functions: "Beregn 8-16 & 6-9 & 17-06" / "Beregn
# gennemsnitsforbrug 17-21" / "Beregn gennemsnitsforbrug døgn".
# Recomputing natively needs a 30-day rolling history and time-based
# triggers (kl 16:01/21:01/00:01, plus select.pwm_pv_history_period_days
# changing).
# ---------------------------------------------------------------------------

PwM_CONSUMPTION_DATA_ATTRIBUTES: list[str] = [
    "daytime_average",
    "daytime_average_adjusted",
    "early_morning_average",
    "morning_average",
    "late_morning_average",
    "evening_average",
    "nighttime_average",
    "full_day_average",
    "pool_heating_consumption",
    "mining_consumption",
    "car_charger_consumption",
    "heat_pump_consumption",
]

PwM_CONSUMPTION_DATA_SENSORS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="consumption_data",
        name="Consumption data",
        icon="mdi:chart-timeline-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


class PwMngtConsumptionDataSensor(SensorEntity):
    """A scaffolded, read-only PwMngt PV-device sensor bundling the 12
    consumption-average values above as attributes. No live value yet --
    see the comment block above PwM_CONSUMPTION_DATA_ATTRIBUTES.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            key: None for key in PwM_CONSUMPTION_DATA_ATTRIBUTES
        }


# Attribute names Home Assistant itself derives from an entity's own
# properties (icon, unit_of_measurement, device_class, etc. -- set via
# each mirror's own SensorEntityDescription) rather than from
# extra_state_attributes. They still show up in a source entity's
# state.attributes dict, so they have to be filtered back out when
# copying "everything else" from the source -- otherwise the source's
# friendly_name/icon/unit would leak in and clash with the mirror's own.
_MIRROR_SKIP_ATTRIBUTES = {
    "assumed_state",
    "attribution",
    "device_class",
    "entity_picture",
    "friendly_name",
    "icon",
    "state_class",
    "supported_features",
    "unit_of_measurement",
}


class _PwMngtMirrorSensor(SensorEntity):
    """Shared base for PwMngt sensors that live-mirror another entity's
    state 1:1 -- including its extra attributes (e.g. an inverter's own
    diagnostic attributes on a power sensor), minus the handful of
    "standard" ones each mirror already defines itself (see
    _MIRROR_SKIP_ATTRIBUTES). A subclass sets self._source_entity_id in
    __init__ (None if there's nothing to mirror yet) before this class's
    async_added_to_hass runs.
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
        """Copy the source entity's state and extra attributes onto this
        entity."""
        if source_state is None or source_state.state in ("unknown", "unavailable"):
            self._attr_native_value = None
            self._attr_available = source_state is not None
            self._attr_extra_state_attributes = {}
        else:
            try:
                self._attr_native_value = float(source_state.state)
            except ValueError:
                self._attr_native_value = source_state.state
            self._attr_available = True
            self._attr_extra_state_attributes = {
                key: value
                for key, value in source_state.attributes.items()
                if key not in _MIRROR_SKIP_ATTRIBUTES
            }
        self.async_write_ha_state()


class PwMngtPvMirrorSensor(_PwMngtMirrorSensor):
    """A PwM PV-device sensor that live-mirrors another entity's state,
    where the source entity_id is picked by the user in Options -> Solar
    PV Plant (see PwM_PV_MIRROR_SENSORS above).
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
                "Options -> Solar PV Plant), it will stay unavailable",
                self.entity_description.key,
            )


class PwMngtHubMirrorSensor(_PwMngtMirrorSensor):
    """A PwM hub-device sensor that live-mirrors another entity's state,
    where the source entity_id is picked by the user in Options -> Solar
    PV Plant (see PwM_HUB_MIRROR_SENSORS above) -- same lookup pattern as
    PwMngtPvMirrorSensor, just on the Hub device instead of the PV device.
    """

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        entity_map_key: str,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._source_entity_id = entity_map.get(entity_map_key) or None
        if self._source_entity_id is None:
            # Not picked yet (e.g. an entry created before this field
            # existed, or the user hasn't set it) -- nothing to mirror
            # until it's set via the integration's options ("Configure" ->
            # Power Management). The missing-Stromligning Repair (see
            # __init__.py) already flags the likely underlying cause.
            LOGGER.warning(
                "PwMngt: no source entity configured for '%s' (set it under "
                "Options -> Solar PV Plant), it will stay unavailable",
                self.entity_description.key,
            )


class PwMngtPvSumSensor(SensorEntity):
    """A PwM PV-device sensor whose value is the live sum of several other
    configured entities' states (e.g. PV total = PV1 + PV2 + PV3 power),
    rather than a 1:1 mirror of a single entity.

    A component entity that's missing (not configured) or momentarily
    unavailable/unknown contributes 0 rather than making the whole sum
    unavailable -- a PV string legitimately reads 0 at night, and that
    shouldn't blank out the total.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        entity_map_keys: tuple[str, ...],
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._source_entity_ids = [
            entity_map[key] for key in entity_map_keys if entity_map.get(key)
        ]
        if not self._source_entity_ids:
            LOGGER.warning(
                "PwMngt: no source entities configured for '%s' (set them "
                "under Options -> Solar PV Plant), it will stay unavailable",
                self.entity_description.key,
            )

    async def async_added_to_hass(self) -> None:
        """Start summing the configured source entities' states, if any."""
        await super().async_added_to_hass()

        if not self._source_entity_ids:
            return

        @callback
        def _handle_source_update(event) -> None:  # pylint: disable=unused-argument
            self._recompute()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._source_entity_ids, _handle_source_update
            )
        )
        self._recompute()

    @callback
    def _recompute(self) -> None:
        """Re-sum the configured source entities' current states."""
        total = 0.0
        for entity_id in self._source_entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            try:
                total += float(state.state)
            except ValueError:
                continue
        self._attr_native_value = total
        self._attr_available = True
        self.async_write_ha_state()

class PwMngtGridSplitSensor(SensorEntity):
    """A PwM PV-device sensor whose value is a live, sign-based split of a
    single other configured entity's state (e.g. Grid import power = Grid
    power when positive, else 0; Grid export power = -Grid power when
    negative, else 0).

    Unlike PwMngtPvSumSensor, this stays unavailable whenever its single
    source is unavailable/unknown -- there's nothing meaningful to default
    to for a 1:1 derived value.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        entity_map_key: str,
        split_fn: Callable[[float], float],
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = None
        self._attr_available = False
        self._split_fn = split_fn

        entity_map = entry.options.get(CONF_ENTITY_MAP, {})
        self._source_entity_id = entity_map.get(entity_map_key) or None
        if not self._source_entity_id:
            LOGGER.warning(
                "PwMngt: no source entity configured for '%s' (set it under "
                "Options -> Solar PV Plant), it will stay unavailable",
                self.entity_description.key,
            )

    async def async_added_to_hass(self) -> None:
        """Start tracking the configured source entity's state, if any."""
        await super().async_added_to_hass()

        if not self._source_entity_id:
            return

        @callback
        def _handle_source_update(event) -> None:  # pylint: disable=unused-argument
            self._recompute()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], _handle_source_update
            )
        )
        self._recompute()

    @callback
    def _recompute(self) -> None:
        """Re-derive the split value from the source entity's current state."""
        state = self.hass.states.get(self._source_entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            self._attr_available = False
            self.async_write_ha_state()
            return
        try:
            value = float(state.state)
        except ValueError:
            self._attr_available = False
            self.async_write_ha_state()
            return
        self._attr_native_value = self._split_fn(value)
        self._attr_available = True
        self.async_write_ha_state()

class PwMngtEnergyIntegrationSensor(RestoreEntity, SensorEntity):
    """A PwM PV-device sensor that integrates a power (W) source sensor
    over time into a cumulative energy (kWh) total, using a left-Riemann-sum
    approximation (same method as Home Assistant's built-in "Integration -
    Riemann sum" helper: `platform: integration`, method: left,
    unit_prefix: k, round: 2).

    The accumulated total survives Home Assistant restarts via
    RestoreEntity; only the elapsed-time baseline resets on restart (the
    next source update after a restart just re-anchors the clock, it
    doesn't add a spurious chunk of energy for the downtime).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: SensorEntityDescription,
        entry: ConfigEntry,
        source_entity_id: str,
    ) -> None:
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_pv_{description.key}"
        self._attr_device_info = pv_device_info(entry)
        self._attr_native_value = 0.0
        self._attr_available = False
        self._source_entity_id = source_entity_id
        self._last_source_value: float | None = None
        self._last_update_time = None

    async def async_added_to_hass(self) -> None:
        """Restore the accumulated total, then start integrating."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError:
                self._attr_native_value = 0.0
        self._attr_available = True
        self.async_write_ha_state()

        @callback
        def _handle_source_update(event) -> None:
            self._integrate(event.data.get("new_state"))

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], _handle_source_update
            )
        )
        # Pick up whatever the source already reports right now (in case we
        # missed its own startup state-write due to add-order), so the
        # elapsed-time baseline is anchored immediately rather than waiting
        # for the source's next real update.
        self._integrate(self.hass.states.get(self._source_entity_id))

    @callback
    def _integrate(self, new_state) -> None:
        """Add the elapsed contribution of the source's *previous* value
        (left Riemann sum) since the last time we saw it change."""
        now = dt_util.utcnow()

        if new_state is None or new_state.state in ("unknown", "unavailable"):
            self._last_source_value = None
            self._last_update_time = now
            return

        try:
            value = float(new_state.state)
        except ValueError:
            self._last_source_value = None
            self._last_update_time = now
            return

        if self._last_source_value is not None and self._last_update_time is not None:
            elapsed_hours = (now - self._last_update_time).total_seconds() / 3600
            self._attr_native_value = round(
                (self._attr_native_value or 0.0)
                + (self._last_source_value * elapsed_hours) / 1000,
                2,
            )
            self._attr_available = True
            self.async_write_ha_state()

        self._last_source_value = value
        self._last_update_time = now
