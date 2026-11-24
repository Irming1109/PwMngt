"""Repairs for a setup that silently stops working (issue #20).

PwMngt keeps calculating with whatever the wizard's fields point at --
a sensor that is gone, has been unavailable for long, or resets every
hour just gives wrong or frozen numbers, with nothing to see. On Claus's
install (2026-11-03/04) the four savings counters pointed at Kostal
sensors no longer connected, and two at hourly Utility Meters; the
savings stood still for weeks. This module checks, every
_CHECK_INTERVAL, the first time 10 minutes after setup:

- Every filled-in entity field (Solar PV Plant page, the chargers'
  consumption sources) -- not the seed fields, used once and cleared
  (issue #91): a Repair when its entity doesn't exist, or has been
  unavailable/unknown for _UNAVAILABLE_FOR (a Kostal can be unavailable
  for hours at night while the inverter sleeps).
- The five fields that must be a raw, ever-increasing counter
  (_RAW_COUNTER_KEYS): a Repair when one points at a Utility Meter
  helper -- PwMngt reads them right after its reset and books ~0.
- Strømligning's entities PwMngt reads without a wizard field
  (properties/hub_properties.py): spot price ex VAT and tomorrow's spot
  price ex VAT are required; the sale price for surplus production is
  recommended (the Repair can be ignored -- Kasper and Rene don't use it).
- The main fuse's phase sensors (issue #101): with a battery set up but
  none of the three picked, the grid charge ignores the main fuse and
  always charges at "Charge 75" (data/battery_charge_level.py) -- a
  Repair that can be ignored by those without a phase-current meter.
- Inverter control (issue #125): the inverter's fields for PwMngt's own
  control are filled in (the brand's writer is configured), but the
  switch "Inverter control" on PwM PV is off -- PwMngt then writes
  nothing to the inverter. Can be ignored by those who fill in the
  fields before they want PwMngt to take over; turning the switch on
  clears it at once (inverter/control.py).

Each Repair clears itself once the cause is gone. No Node-RED
counterpart -- new in PwMngt (Claus, 2026-11-16).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENTITY_MAP,
    DEPENDENCY_STROMLIGNING,
    DOMAIN,
    ENTITY_KEY_BATTERY_DISCHARGE_HOURLY,
    ENTITY_KEY_BATTERY_GRID_CHARGE_HOURLY,
    ENTITY_KEY_BATTERY_SOC,
    ENTITY_KEY_GRID_IMPORT_HOURLY,
    ENTITY_KEY_HOUSEHOLD_CONSUMPTION_HOURLY,
    ENTITY_KEY_MAIN_FUSE_LOAD_L1,
    ENTITY_KEY_MAIN_FUSE_LOAD_L2,
    ENTITY_KEY_MAIN_FUSE_LOAD_L3,
    ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL,
    ENTITY_KEY_SEED_BATTERY_GRID_CHARGE_SUM,
    ENTITY_KEY_SEED_BATTERY_GRID_DISCHARGE_SUM,
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL,
    ENTITY_KEY_SEED_PURCHASE_PRICE_REAL_WITHOUT_PV,
)
from .inverter.control import ISSUE_INVERTER_CONTROL_OFF, inverter_writer
from .properties import charger_properties, pv_properties

LOGGER = logging.getLogger(__name__)

# Also the first check's delay: Home Assistant needs a while after a
# restart before every integration's entities exist.
_CHECK_INTERVAL = timedelta(minutes=10)
_UNAVAILABLE_FOR = timedelta(hours=12)

_SEED_KEYS = frozenset(
    {
        ENTITY_KEY_SEED_PURCHASE_PRICE_REAL,
        ENTITY_KEY_SEED_PURCHASE_PRICE_REAL_WITHOUT_PV,
        ENTITY_KEY_SEED_BATTERY_GRID_CHARGE_SUM,
        ENTITY_KEY_SEED_BATTERY_GRID_DISCHARGE_SUM,
    }
)
# Read as counters that never reset -- an hourly Utility Meter resets just
# before PwMngt reads it at xx:01.
_RAW_COUNTER_KEYS = (
    ENTITY_KEY_PROPERTY_CONSUMPTION_TOTAL,
    ENTITY_KEY_HOUSEHOLD_CONSUMPTION_HOURLY,
    ENTITY_KEY_GRID_IMPORT_HOURLY,
    ENTITY_KEY_BATTERY_GRID_CHARGE_HOURLY,
    ENTITY_KEY_BATTERY_DISCHARGE_HOURLY,
)
_UTILITY_METER_PLATFORM = "utility_meter"

# Strømligning entities by (domain, translation_key) -- the same lookup as
# hub_properties._stromligning_state().
_STROMLIGNING_REQUIRED = (
    ("sensor", "spotprice_ex_vat"),
    ("binary_sensor", "tomorrow_spotprice_ex_vat"),
)
_STROMLIGNING_RECOMMENDED = (("sensor", "production_price_ex_vat"),)

# Repair translation keys (strings.json "issues").
ISSUE_FIELD_MISSING = "field_missing"
ISSUE_FIELD_UNAVAILABLE = "field_unavailable"
ISSUE_FIELD_UTILITY_METER = "field_utility_meter"
ISSUE_STROMLIGNING_REQUIRED = "stromligning_entity_required"
ISSUE_STROMLIGNING_RECOMMENDED = "stromligning_entity_recommended"
ISSUE_NO_FUSE_PHASES = "no_fuse_phases"

_FUSE_PHASE_KEYS = (
    ENTITY_KEY_MAIN_FUSE_LOAD_L1,
    ENTITY_KEY_MAIN_FUSE_LOAD_L2,
    ENTITY_KEY_MAIN_FUSE_LOAD_L3,
)

_STATES_UNAVAILABLE = ("unavailable", "unknown")


@dataclass(frozen=True)
class Field:
    """One filled-in wizard field: issue_key names its Repairs,
    translation_key its label in the wizard ("component.pwmngt.options....")."""

    issue_key: str
    translation_key: str
    entity_id: str


# ---------------------------------------------------------------------------
# Pure checks -- no Home Assistant state touched.
# ---------------------------------------------------------------------------


def field_problem(
    state: State | None,
    registered: bool,
    disabled: bool,
    no_state_since: datetime | None,
    now: datetime,
) -> str | None:
    """ISSUE_FIELD_MISSING when the entity is gone (no state and not in the
    entity registry) or disabled; ISSUE_FIELD_UNAVAILABLE when it has been
    unavailable/unknown -- or registered without a state, e.g. its
    integration not loaded, since no_state_since -- for _UNAVAILABLE_FOR;
    else None."""
    if disabled or (state is None and not registered):
        return ISSUE_FIELD_MISSING
    if state is None:
        since = no_state_since or now
    elif state.state in _STATES_UNAVAILABLE:
        since = state.last_changed
    else:
        return None
    return ISSUE_FIELD_UNAVAILABLE if now - since >= _UNAVAILABLE_FOR else None


# ---------------------------------------------------------------------------
# Home Assistant wiring.
# ---------------------------------------------------------------------------


@callback
def async_start_config_check(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Run the check every _CHECK_INTERVAL, the first time one interval
    after setup; stopped when the entry unloads."""
    checker = _ConfigCheck(hass, entry)

    async def _run(_now=None) -> None:
        await checker.async_run()

    entry.async_on_unload(async_track_time_interval(hass, _run, _CHECK_INTERVAL))


class _ConfigCheck:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        # entity_id -> when it was first seen registered but without a state.
        self._no_state_since: dict[str, datetime] = {}

    async def async_run(self) -> None:
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "options", {DOMAIN}
        )
        raised: set[str] = set()
        registry = er.async_get(self.hass)
        now = dt_util.utcnow()
        for field in self._fields():
            label = translations.get(
                f"component.{DOMAIN}.options.{field.translation_key}", field.issue_key
            )
            entry = registry.async_get(field.entity_id)
            state = self.hass.states.get(field.entity_id)
            if state is None and entry is not None:
                self._no_state_since.setdefault(field.entity_id, now)
            else:
                self._no_state_since.pop(field.entity_id, None)
            problem = field_problem(
                state,
                entry is not None,
                entry is not None and entry.disabled_by is not None,
                self._no_state_since.get(field.entity_id),
                now,
            )
            if problem is None and field.issue_key in _RAW_COUNTER_KEYS and entry is not None:
                if entry.platform == _UTILITY_METER_PLATFORM:
                    problem = ISSUE_FIELD_UTILITY_METER
            if problem is None:
                continue
            issue_id = f"field_{field.issue_key}"
            raised.add(issue_id)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=problem,
                translation_placeholders={"field": label, "entity_id": field.entity_id},
            )
        # Also a Repair from before a restart, for a field fixed or emptied
        # since -- the issue registry keeps them.
        for domain, issue_id in list(ir.async_get(self.hass).issues):
            if domain == DOMAIN and issue_id.startswith("field_") and issue_id not in raised:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        self._check_stromligning(registry)
        self._check_fuse_phases()
        self._check_inverter_control()

    def _check_inverter_control(self) -> None:
        """Issue #125: the inverter's fields filled in, but the switch off."""
        entity_id = pv_properties.inverter_control_entity_id(self.hass, self._entry)
        state = self.hass.states.get(entity_id) if entity_id else None
        writer = inverter_writer(self.hass, self._entry)
        if state is not None and state.state == "off" and writer is not None and writer.is_configured():
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_INVERTER_CONTROL_OFF,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_INVERTER_CONTROL_OFF,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_INVERTER_CONTROL_OFF)

    def _check_fuse_phases(self) -> None:
        """Issue #101: a battery without any phase sensor picked."""
        entity_map = self._entry.options.get(CONF_ENTITY_MAP, {})

        def picked(key: str) -> bool:
            # Same test as _fields(): an empty field holds "" or no entity id.
            value = entity_map.get(key)
            return isinstance(value, str) and "." in value

        if picked(ENTITY_KEY_BATTERY_SOC) and not any(picked(key) for key in _FUSE_PHASE_KEYS):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_NO_FUSE_PHASES,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_NO_FUSE_PHASES,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_NO_FUSE_PHASES)

    def _fields(self) -> list[Field]:
        """Every filled-in entity field, the seed fields left out."""
        fields = [
            Field(key, f"step.solar_pv_plant.data.{key}", value)
            for key, value in self._entry.options.get(CONF_ENTITY_MAP, {}).items()
            if key not in _SEED_KEYS and isinstance(value, str) and "." in value
        ]
        fields += [
            Field(charger_id, f"step.ev_charging_consumption.data.{charger_id}", entity_id)
            for charger_id, entity_id in charger_properties.consumption_sources(self.hass, self._entry)
        ]
        return fields

    def _check_stromligning(self, registry: er.EntityRegistry) -> None:
        """Only while Strømligning is installed -- otherwise the
        missing_stromligning Repair (__init__.py) already says so."""
        installed = bool(self.hass.config_entries.async_entries(DEPENDENCY_STROMLIGNING))
        for keys, translation_key in (
            (_STROMLIGNING_REQUIRED, ISSUE_STROMLIGNING_REQUIRED),
            (_STROMLIGNING_RECOMMENDED, ISSUE_STROMLIGNING_RECOMMENDED),
        ):
            for domain, key in keys:
                issue_id = f"stromligning_{key}"
                enabled = [
                    entity
                    for entity in registry.entities.values()
                    if entity.platform == DEPENDENCY_STROMLIGNING
                    and entity.domain == domain
                    and entity.translation_key == key
                    and entity.disabled_by is None
                ]
                if not installed or len(enabled) == 1:
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                    continue
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=translation_key,
                    translation_placeholders={"key": key},
                )
