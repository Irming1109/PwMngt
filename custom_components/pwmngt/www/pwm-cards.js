/**
 * PwMngt custom Lovelace cards.
 *
 * Plain JavaScript custom elements (no build step) implementing Home
 * Assistant's "custom card" contract: setConfig(config), a hass setter,
 * and getCardSize(). Background on the pattern:
 * https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card
 *
 * Five card types are defined here:
 *   - pwm-hub-card:         the "PwM" hub device's charger/PV configuration
 *                           fields.
 *   - pwm-charger-card:     a charger device's configuration fields. Takes
 *                           a `charger` config key ("charger1" / "charger2",
 *                           or any future charger id from CHARGERS in
 *                           devices.py) so the same card type is reused for
 *                           every charger.
 *   - pwm-pv-card:          the "PwM PV" device's configuration fields.
 *   - pwm-electricity-card: the "PwM" hub device's electricity billing/
 *                           tariff configuration fields (a separate card
 *                           from pwm-hub-card, mirroring the "Strøm" vs.
 *                           "Ladestander konfiguration" split on the
 *                           Konfiguration dashboard, even though both sets
 *                           of entities live on the same hub device).
 *   - pwm-battery-card:     house battery control -- tonight's charge plan
 *                           and manual "charge now"/stop/postpone/auto
 *                           buttons. A port of Claus's Node-RED dashboard
 *                           card "Hus batteri styring" (issue #16); unlike
 *                           the config cards it is not field-driven.
 *
 * Each card's field list mirrors the matching Python entity descriptions
 * (select.py / number.py / text.py / sensor.py) as of this writing, in the
 * same order. This file does NOT discover which fields to show -- if a
 * config field is added, removed, reordered, or renamed in Python, update
 * the matching `fields` array below to match.
 *
 * Entities are never referenced by entity_id here. Each field names the
 * PwMngt device it lives on ("hub", "pv" or a charger id) plus its Python
 * description key, and pwmFindEntityId() resolves that through
 * hass.devices (the "pwmngt" device identifiers) and hass.entities
 * (platform "pwmngt" + translation_key, which every PwMngt entity sets to
 * its description key -- see use_description_key_as_translation_key() in
 * base.py). So the cards keep working when Home Assistant adds a "_2"
 * suffix, or the user renames a device or an entity -- e.g. in the "Name
 * and assign" dialog Home Assistant shows right after setup.
 *
 * The one bit of real behaviour these cards implement today: a charger's
 * "Charger identification (Easee serial number)" field is only shown
 * while that charger's own "type" select is set to "Easee" (see
 * `hideUnless` below) -- the same rule PwMngtText enforces server-side via
 * the entity registry's hidden_by, kept in sync here for any dashboard
 * that places these entities directly instead of using this card.
 */

const PWM_CARD_STYLE = `
  ha-card {
    padding: 16px;
  }
  .pwm-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--divider-color);
  }
  .pwm-row:last-child {
    border-bottom: none;
  }
  .pwm-row.pwm-hidden {
    display: none;
  }
  .pwm-row-label {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--primary-text-color);
  }
  .pwm-row-label ha-icon {
    color: var(--state-icon-color, var(--paper-item-icon-color));
    flex-shrink: 0;
  }
  .pwm-row-hint {
    display: block;
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .pwm-row-control select,
  .pwm-row-control input {
    font-family: inherit;
    font-size: 14px;
    padding: 6px 8px;
    border-radius: 4px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    min-width: 120px;
  }
  .pwm-row-control .pwm-row-value {
    font-size: 14px;
    color: var(--primary-text-color);
    min-width: 120px;
    text-align: right;
  }
  .pwm-warning {
    color: var(--error-color);
    font-size: 13px;
  }
`;

/**
 * Shared behaviour for all PwMngt config cards. A subclass sets
 * this.cardTitle and this.fields inside its own setConfig(), then calls
 * super.setConfig(config).
 *
 * Each entry in `fields` looks like:
 *   {
 *     device: "hub" | "pv" | "charger1" | ...,   // which PwMngt device
 *     key: "charger1_type",                      // Python description key
 *     label: "Charger1 type",
 *     icon: "mdi:ev-station",
 *     type: "select" | "number" | "text" | "sensor",   // also the domain
 *     hideUnless: { device: "hub", key: "...", equals: "Easee" },  // optional, a select
 *     hint: "Shown in small text under the label",  // optional
 *   }
 * "sensor" is read-only: it just displays the entity's current state (plus
 * its unit_of_measurement, if any), with no input control and no service
 * call -- for informational fields like a live battery percentage.
 */
const PWM_DOMAIN = "pwmngt";

/** A device's own "pwmngt" identifier (see devices.py), or undefined. */
function pwmIdentifier(device) {
  const pair = (device.identifiers || []).find(([domain]) => domain === PWM_DOMAIN);
  return pair ? pair[1] : undefined;
}

/**
 * The Home Assistant device id of one PwMngt device, found by its
 * identifiers (devices.py), never by its (user-renamable) name:
 *   "hub"      -> the PwMngt device with no via_device (hub_device_info())
 *   "pv"       -> identifier "<entry_id>_pv" (pv_device_info())
 *   "charger1" -> identifier "charger1" (charger_device_info())
 */
function pwmFindDeviceId(hass, deviceRef) {
  for (const device of Object.values(hass.devices || {})) {
    const identifier = pwmIdentifier(device);
    if (identifier === undefined) continue;
    const matches =
      deviceRef === "hub"
        ? !device.via_device_id
        : deviceRef === "pv"
          ? String(identifier).endsWith("_pv")
          : identifier === deviceRef;
    if (matches) return device.id;
  }
  return undefined;
}

/** entity_id of the PwMngt entity with this device/domain/description key. */
function pwmFindEntityId(hass, deviceRef, domain, key) {
  const deviceId = pwmFindDeviceId(hass, deviceRef);
  if (!deviceId) return undefined;
  const entity = Object.values(hass.entities || {}).find(
    (e) =>
      e.platform === PWM_DOMAIN &&
      e.device_id === deviceId &&
      e.translation_key === key &&
      e.entity_id.startsWith(`${domain}.`)
  );
  return entity ? entity.entity_id : undefined;
}

function pwmFieldDomain(field) {
  return field.type === "sensor" ? "sensor" : field.type;
}

class PwMngtBaseCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) {
      this._build();
      this._built = true;
    }
    this._update();
  }

  getCardSize() {
    return 1 + (this.fields ? this.fields.length : 1);
  }

  _build() {
    this.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = PWM_CARD_STYLE;

    const card = document.createElement("ha-card");
    card.header = this.cardTitle || "";

    const content = document.createElement("div");
    content.className = "card-content";

    this._rows = [];
    for (const field of this.fields || []) {
      const row = this._buildRow(field);
      content.appendChild(row.rowEl);
      this._rows.push(row);
    }

    card.appendChild(content);
    this.shadowRoot.append(style, card);
  }

  _buildRow(field) {
    const rowEl = document.createElement("div");
    rowEl.className = "pwm-row";

    const labelEl = document.createElement("div");
    labelEl.className = "pwm-row-label";
    if (field.icon) {
      const iconEl = document.createElement("ha-icon");
      iconEl.icon = field.icon;
      labelEl.appendChild(iconEl);
    }
    const labelText = document.createElement("span");
    labelText.textContent = field.label;
    if (field.hint) {
      const hintEl = document.createElement("span");
      hintEl.className = "pwm-row-hint";
      hintEl.textContent = field.hint;
      labelText.appendChild(hintEl);
    }
    labelEl.appendChild(labelText);

    const controlWrap = document.createElement("div");
    controlWrap.className = "pwm-row-control";

    // row.entityId/gateEntityId are filled in (and refreshed whenever the
    // registries change) by _resolveEntityIds(); the listeners below read
    // row.entityId at call time.
    const row = { rowEl, controlEl: null, entityId: undefined, gateEntityId: undefined };

    let controlEl;
    if (field.type === "sensor") {
      controlEl = document.createElement("span");
      controlEl.className = "pwm-row-value";
    } else if (field.type === "select") {
      controlEl = document.createElement("select");
      controlEl.addEventListener("change", () => {
        if (!row.entityId) return;
        this._hass.callService("select", "select_option", {
          entity_id: row.entityId,
          option: controlEl.value,
        });
      });
    } else if (field.type === "number") {
      controlEl = document.createElement("input");
      controlEl.type = "number";
      controlEl.addEventListener("change", () => {
        if (!row.entityId) return;
        this._hass.callService("number", "set_value", {
          entity_id: row.entityId,
          value: Number(controlEl.value),
        });
      });
    } else {
      // "text"
      controlEl = document.createElement("input");
      controlEl.type = "text";
      controlEl.addEventListener("change", () => {
        if (!row.entityId) return;
        this._hass.callService("text", "set_value", {
          entity_id: row.entityId,
          value: controlEl.value,
        });
      });
    }
    controlWrap.appendChild(controlEl);

    rowEl.append(labelEl, controlWrap);
    row.controlEl = controlEl;
    return row;
  }

  /**
   * Map every field to its current entity_id via pwmFindEntityId(). Only
   * redone when Home Assistant hands us a new entity or device registry
   * (hass.entities / hass.devices are replaced, not mutated, on change),
   * so a rename is picked up without scanning the registry on every
   * state update.
   */
  _resolveEntityIds() {
    if (
      this._resolvedEntities === this._hass.entities &&
      this._resolvedDevices === this._hass.devices
    ) {
      return;
    }
    this._resolvedEntities = this._hass.entities;
    this._resolvedDevices = this._hass.devices;
    (this.fields || []).forEach((field, index) => {
      const row = this._rows[index];
      row.entityId = pwmFindEntityId(this._hass, field.device, pwmFieldDomain(field), field.key);
      row.gateEntityId = field.hideUnless
        ? pwmFindEntityId(this._hass, field.hideUnless.device, "select", field.hideUnless.key)
        : undefined;
    });
  }

  _update() {
    this._resolveEntityIds();
    (this.fields || []).forEach((field, index) => {
      const row = this._rows[index];

      // Visibility, for fields that only apply given another entity's
      // current value (e.g. the Easee-only identification field).
      let visible = true;
      if (field.hideUnless) {
        const gate = row.gateEntityId ? this._hass.states[row.gateEntityId] : undefined;
        visible = !!gate && gate.state === field.hideUnless.equals;
      }
      row.rowEl.classList.toggle("pwm-hidden", !visible);

      const state = row.entityId ? this._hass.states[row.entityId] : undefined;
      if (!state) {
        return; // Entity not available yet -- leave the control as-is.
      }

      const isBeingEdited = document.activeElement === row.controlEl;

      if (field.type === "sensor") {
        const unit = state.attributes.unit_of_measurement;
        row.controlEl.textContent = unit ? `${state.state} ${unit}` : state.state;
      } else if (field.type === "select") {
        const options = state.attributes.options || [];
        const currentOptions = Array.from(row.controlEl.options).map((o) => o.value);
        if (currentOptions.join("|") !== options.join("|")) {
          row.controlEl.innerHTML = "";
          for (const option of options) {
            const optionEl = document.createElement("option");
            optionEl.value = option;
            optionEl.textContent = option;
            row.controlEl.appendChild(optionEl);
          }
        }
        if (!isBeingEdited) {
          row.controlEl.value = state.state;
        }
      } else {
        if (field.type === "number") {
          if (state.attributes.min !== undefined) row.controlEl.min = state.attributes.min;
          if (state.attributes.max !== undefined) row.controlEl.max = state.attributes.max;
          if (state.attributes.step !== undefined) row.controlEl.step = state.attributes.step;
        }
        if (!isBeingEdited) {
          row.controlEl.value = state.state;
        }
      }
    });
  }
}

/** Builds one charger-device field descriptor for a given charger id. */
function chargerField(charger, type, key, label, icon, extra) {
  return Object.assign(
    {
      device: charger,
      key,
      label,
      icon,
      type,
    },
    extra || {}
  );
}

class PwMChargerCard extends PwMngtBaseCard {
  setConfig(config) {
    if (!config || !config.charger) {
      throw new Error("pwm-charger-card: set 'charger' to e.g. 'charger1' or 'charger2'");
    }
    const charger = config.charger;
    const chargerNumber = charger.replace("charger", "");
    this.cardTitle = config.title || `Charger ${chargerNumber}`;

    this.fields = [
      chargerField(
        charger,
        "select",
        "driving_distance_km_per_kwh",
        "Driving distance in Km per KwH",
        "mdi:gauge"
      ),
      chargerField(charger, "select", "daily_charge_limit", "Daily charge limit", "mdi:battery-clock"),
      chargerField(charger, "select", "minimum_range", "Minimum range", "mdi:map-marker-distance"),
      chargerField(charger, "select", "charge_period", "Charge period", "mdi:calendar-clock"),
      chargerField(
        charger,
        "select",
        "defer_surplus_charging",
        "Defer surplus charging",
        "mdi:solar-power"
      ),
      chargerField(charger, "select", "charger_max_load", "Charger max load", "mdi:current-ac"),
      // Not charger-specific -- this is the shared PwM PV device's battery
      // SoC sensor (see PwM_PV_MIRROR_SENSORS in sensor.py), shown here
      // read-only since it's informational context for charging decisions,
      // same placement as "Batteri tilstand" on the old Ladeboks dashboard.
      {
        device: "pv",
        key: "battery_soc",
        label: "Battery SoC",
        icon: "mdi:battery",
        type: "sensor",
      },
      chargerField(
        charger,
        "text",
        "charger_identification",
        "Charger identification (Easee serial number)",
        "mdi:identifier",
        { hideUnless: { device: "hub", key: `${charger}_type`, equals: "Easee" } }
      ),
    ];
    super.setConfig(config);
  }

  // Called by Home Assistant when this card type is picked from the "Add
  // Card" list without going into YAML mode -- gives it a working default
  // instead of erroring on a missing 'charger'.
  static getStubConfig() {
    return { charger: "charger1" };
  }

  // Called by Home Assistant to get a small visual editor for this card's
  // config, shown in the card's edit panel instead of raw YAML. See
  // PwMChargerCardEditor below.
  static getConfigElement() {
    return document.createElement("pwm-charger-card-editor");
  }
}
customElements.define("pwm-charger-card", PwMChargerCard);

/**
 * Visual editor for pwm-charger-card: a single "Charger" dropdown, listing
 * every charger device this PwMngt install currently has (discovered via
 * hass.devices, matching on a "pwmngt" device identifier of the form
 * "charger<N>" -- never the device name, which the user can rename) -- so
 * adding a future charger in devices.py needs no change here. Home Assistant wires this up automatically because of
 * PwMChargerCard.getConfigElement() above; it just needs to implement
 * setConfig()/set hass() and fire a "config-changed" event on changes.
 * See https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/#configuration-editor
 */
class PwMChargerCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _chargerOptions() {
    if (!this._hass) return ["charger1", "charger2"];
    const ids = Object.values(this._hass.devices || {})
      .map((d) => pwmIdentifier(d))
      .filter((identifier) => /^charger\d+$/.test(String(identifier)))
      .sort();
    return ids.length ? ids : ["charger1", "charger2"];
  }

  _render() {
    const options = this._chargerOptions();
    const currentValue = (this._config && this._config.charger) || options[0];

    if (!this._built) {
      this.innerHTML = `
        <div style="padding: 12px 16px;">
          <label style="display: block; font-size: 13px; font-weight: 500; margin-bottom: 4px; color: var(--primary-text-color);">
            Charger
          </label>
          <select style="padding: 6px 8px; min-width: 160px; font: inherit;"></select>
        </div>
      `;
      this._selectEl = this.querySelector("select");
      this._selectEl.addEventListener("change", () => {
        const newConfig = Object.assign({}, this._config, { charger: this._selectEl.value });
        this._config = newConfig;
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: newConfig },
            bubbles: true,
            composed: true,
          })
        );
      });
      this._built = true;
    }

    const currentOptionValues = Array.from(this._selectEl.options).map((o) => o.value);
    if (currentOptionValues.join("|") !== options.join("|")) {
      this._selectEl.innerHTML = "";
      for (const option of options) {
        const optionEl = document.createElement("option");
        optionEl.value = option;
        optionEl.textContent = option;
        this._selectEl.appendChild(optionEl);
      }
    }
    this._selectEl.value = currentValue;
  }
}
customElements.define("pwm-charger-card-editor", PwMChargerCardEditor);

class PwMHubCard extends PwMngtBaseCard {
  setConfig(config) {
    this.cardTitle = (config && config.title) || "Power Management Configuration";
    this.fields = [
      { device: "hub", key: "charger1_type", label: "Charger1 type", icon: "mdi:ev-station", type: "select" },
      { device: "hub", key: "charger2_type", label: "Charger2 type", icon: "mdi:ev-station", type: "select" },
      {
        device: "hub", key: "charger_priority",
        label: "Charger priority",
        icon: "mdi:sort-numeric-ascending",
        type: "select",
      },
      {
        device: "hub", key: "minimum_solar_power_to_charge",
        label: "Minimum solar power to charge (%)",
        icon: "mdi:solar-power",
        type: "select",
      },
      {
        device: "hub", key: "buffer_minimum_soc",
        label: "Buffer minimum SoC",
        icon: "mdi:battery-arrow-down",
        type: "select",
      },
      {
        device: "hub", key: "buffer_maximum_soc",
        label: "Buffer maximum SoC",
        icon: "mdi:battery-arrow-up",
        type: "select",
      },
      {
        device: "hub", key: "chargers_max_load_combined",
        label: "Chargers max load (Combined)",
        icon: "mdi:fuse",
        type: "select",
      },
      {
        device: "hub", key: "battery_reserve_car_charging",
        label: "Battery reserve car charging",
        icon: "mdi:solar-power-variant",
        type: "select",
      },
    ];
    super.setConfig(config || {});
  }
}
customElements.define("pwm-hub-card", PwMHubCard);

class PwMElectricityCard extends PwMngtBaseCard {
  setConfig(config) {
    this.cardTitle = (config && config.title) || "Electricity";
    this.fields = [
      {
        device: "hub", key: "billing_period",
        label: "Billing period",
        icon: "mdi:calendar-month",
        type: "select",
      },
      {
        device: "hub", key: "electricity_tax",
        label: "Electricity tax",
        icon: "mdi:receipt-text",
        type: "select",
      },
      // Note: "Electricity pricing model" / "Fixed price agreement" /
      // "Spot price surcharge" used to be here -- retired, since
      // the spot electricity price sensor below now covers this (mirrors
      // whatever product is configured in the Stromligning integration).
      {
        device: "hub", key: "tariff_fuse_size",
        label: "Tariff fuse size (Ampere)",
        icon: "mdi:fuse",
        type: "select",
      },
      // Read-only: live-mirrors sensor.stromligning_current_price_vat_2
      // (see PwM_HUB_MIRROR_SENSORS in sensor.py). Not editable.
      {
        device: "hub", key: "spot_electricity_price",
        label: "Electricity price (incl. VAT)",
        icon: "mdi:cash",
        type: "sensor",
      },
    ];
    super.setConfig(config || {});
  }
}
customElements.define("pwm-electricity-card", PwMElectricityCard);

class PwMPvCard extends PwMngtBaseCard {
  setConfig(config) {
    this.cardTitle = (config && config.title) || "Solar PV Plant";
    this.fields = [
      {
        device: "pv", key: "battery_size",
        label: "Battery size (kWh)",
        icon: "mdi:home-battery",
        type: "select",
      },
      {
        device: "pv", key: "battery_min_soc",
        label: "Battery minimum SoC (%)",
        hint: "Only used in PwMngt's calculations - does not change the battery's discharge limit in the inverter",
        icon: "mdi:battery-10",
        type: "select",
      },
      {
        device: "pv", key: "history_period_days",
        label: "History period (days)",
        icon: "mdi:history",
        type: "select",
      },
      {
        device: "pv", key: "solar_inverter_max_ac_kw",
        label: "Solar inverter max AC output (kW)",
        icon: "mdi:solar-power",
        type: "select",
      },
      {
        device: "pv", key: "pv_battery_price_difference",
        label: "PV battery price difference",
        icon: "mdi:currency-usd",
        type: "select",
      },
    ];
    super.setConfig(config || {});
  }
}
customElements.define("pwm-pv-card", PwMPvCard);

/**
 * Card texts. Entity states are shown through hass.formatEntityState()
 * (strings.json / translations/*.json), but these sentences are the card's
 * own -- the frontend only loads an integration's entity translations, so
 * they live here, picked by the user's HA language (English fallback).
 */
const PWM_BATTERY_TEXTS = {
  en: {
    title: "House battery control",
    manual: "Automation and battery hold are off - manual charging only",
    charging: (pct) => `Battery is charging at ${pct}% power`,
    autoControls:
      "The automation controls tonight's charge - a manual choice can be overridden at the next hour or fuse check",
    status: {
      yes_today: "Battery charges today in these hours",
      yes_tomorrow: "Battery charges tomorrow in these hours",
      no_price: "Battery is not charged tonight, price difference too small",
      no_surplus: "Battery is not charged tonight, PV surplus tomorrow",
      no_charge_needed: "Battery is not charged tonight, no charge needed",
      no_deferred: "Charging postponed one day",
      not_calculated: "Waiting for electricity prices (after 14)",
      not_calculated_recheck: "Not calculated yet - calculated at the next full hour",
    },
    manualTitle: "Manual battery control",
    chargeNow: (pct) => `Charge now ${pct}%`,
    stop: "Stop charging",
    postpone: "Postpone charging 1 day",
    cancelPostpone: "Cancel postponement",
    disableAuto: "Disable automatic charging",
    enableAuto: "Enable automatic charging",
    confirmDisable:
      "This also turns off the battery hold (min. SoC), so the battery can discharge freely. Continue?",
    soc: "Battery SoC",
    missing: "PwMngt battery entities not found - is the Solar PV Plant set up?",
  },
  da: {
    title: "Hus batteri styring",
    manual: "Automatik og batterilås slået fra - kun manuel ladning",
    charging: (pct) => `Batteri lader nu med ${pct} % effekt`,
    autoControls:
      "Automatikken styrer nattens ladning - et manuelt valg kan blive overskrevet ved næste timeskift eller sikringstjek",
    status: {
      yes_today: "Batteri lades i dag i disse timer",
      yes_tomorrow: "Batteri lades i morgen i disse timer",
      no_price: "Batteri lades ikke i nat, prisforskel for lav",
      no_surplus: "Batteri lades ikke i nat, PV-overskud i morgen",
      no_charge_needed: "Batteri lades ikke i nat, intet ladebehov",
      no_deferred: "Ladning udskudt 1 dag",
      not_calculated: "Afventer elpriser (efter 14)",
      not_calculated_recheck: "Ikke beregnet endnu - beregnes ved næste hele time",
    },
    manualTitle: "Manuel betjening af batteri",
    chargeNow: (pct) => `Lad nu ${pct} %`,
    stop: "Afbryd ladning",
    postpone: "Udskyd ladning 1 dag",
    cancelPostpone: "Annuller udskydning",
    disableAuto: "Deaktiver automatisk ladning",
    enableAuto: "Aktiver automatisk ladning",
    confirmDisable:
      "Det slår også batterilåsen (min. SoC) fra, så batteriet kan aflade frit. Fortsæt?",
    soc: "Batteri SoC",
    missing: "PwMngt's batteri-entities blev ikke fundet - er Solar PV Plant sat op?",
  },
};

// True 14:00-22:59: "not_calculated" then only waits for the hourly
// recheck (data/battery_charge_decision.py, on the hour 14:00-23:30), not
// for tomorrow's prices.
function pwmBatteryRecheckHours() {
  const hour = new Date().getHours();
  return hour >= 14 && hour < 23;
}

// Banner colour per battery_charge_status (data/battery_charge_decision.py).
const PWM_BATTERY_STATUS_TONE = {
  yes_today: "ok",
  yes_tomorrow: "ok",
  no_price: "bad",
  no_surplus: "bad",
  no_charge_needed: "info",
  no_deferred: "warn",
  not_calculated: "bad",
};

const PWM_CHARGE_LEVELS = [25, 50, 75, 100];

const PWM_BATTERY_CARD_STYLE = `
  ha-card {
    padding: 16px;
  }
  .pwm-banner {
    padding: 12px;
    border-radius: 8px;
    margin-bottom: 8px;
    color: var(--text-primary-color, #fff);
    font-weight: 500;
    text-align: center;
  }
  .pwm-banner.ok { background: var(--success-color, #43a047); }
  .pwm-banner.bad { background: var(--error-color, #db4437); }
  .pwm-banner.warn { background: var(--warning-color, #ffa600); }
  .pwm-banner.info { background: var(--info-color, #039be5); }
  .pwm-note {
    font-size: 12px;
    color: var(--secondary-text-color);
    margin: -2px 0 8px;
    text-align: center;
  }
  .pwm-hours {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 6px;
    margin-bottom: 12px;
  }
  .pwm-hour {
    padding: 10px 0;
    border-radius: 8px;
    text-align: center;
    font-size: 13px;
    background: var(--success-color, #43a047);
    color: var(--text-primary-color, #fff);
  }
  .pwm-section-title {
    font-size: 16px;
    font-weight: 500;
    margin: 12px 0 8px;
    color: var(--primary-text-color);
  }
  .pwm-buttons {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
  }
  .pwm-buttons button {
    font: inherit;
    font-size: 14px;
    padding: 12px 8px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
    cursor: pointer;
  }
  .pwm-buttons button:hover {
    border-color: var(--primary-color);
  }
  .pwm-soc {
    margin-top: 12px;
  }
  .pwm-soc-label {
    display: flex;
    justify-content: space-between;
    font-size: 14px;
    margin-bottom: 4px;
    color: var(--primary-text-color);
  }
  .pwm-soc-bar {
    height: 10px;
    border-radius: 5px;
    background: var(--divider-color);
    overflow: hidden;
  }
  .pwm-soc-fill {
    height: 100%;
    background: var(--success-color, #43a047);
  }
  .pwm-warning {
    color: var(--error-color);
    font-size: 13px;
  }
`;

/**
 * House battery control -- a port of Claus's Node-RED dashboard card "Hus
 * batteri styring" / "Manuel betjening af batteri" (issue #16):
 *
 * | Node-RED                                  | PwMngt (device "pv")            |
 * |-------------------------------------------|---------------------------------|
 * | select.pv_batteri_styring                 | select battery_command          |
 * | select.pv_battery_mode (Auto/Manuel)      | select battery_mode             |
 * | sensor.pv_battery_charge (+ hour0..23)    | sensor battery_charge_status (+ hour_0..23) |
 * | button.pv_battery_udskyd                  | button postpone_battery_charge  |
 * | sensor.batteri_soc                        | sensor battery_soc              |
 *
 * Differences from the Node-RED card, agreed with Claus 2026-11-01:
 * - "Stop charging" is only offered while a charge_now_* command is
 *   active. Under hold_min_soc/block_charging the hold or the sell decision
 *   would just set its own command again.
 * - battery_mode "manual" also pauses the battery hold in PwMngt (Node-RED
 *   never read pv_battery_mode), so the banner says so and disabling asks
 *   for confirmation.
 * - While tonight's charge is planned ("yes_today") the card says that the
 *   automation can override a manual choice -- data/battery_charge_level.py
 *   re-decides every hour and on the fuse check, same as Node-RED.
 * - Shows the two statuses the Node-RED card had no banner for
 *   (no_charge_needed, no_deferred).
 */
class PwMBatteryCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._signature = undefined;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._resolveEntityIds();
    const signature = this._stateSignature();
    if (signature === this._signature) return;
    this._signature = signature;
    this._render();
  }

  getCardSize() {
    return 8;
  }

  _texts() {
    const language = (this._hass.locale && this._hass.locale.language) || this._hass.language || "en";
    return PWM_BATTERY_TEXTS[String(language).split("-")[0]] || PWM_BATTERY_TEXTS.en;
  }

  /** Same registry-change caching as PwMngtBaseCard._resolveEntityIds(). */
  _resolveEntityIds() {
    if (
      this._resolvedEntities === this._hass.entities &&
      this._resolvedDevices === this._hass.devices
    ) {
      return;
    }
    this._resolvedEntities = this._hass.entities;
    this._resolvedDevices = this._hass.devices;
    this._ids = {
      command: pwmFindEntityId(this._hass, "pv", "select", "battery_command"),
      mode: pwmFindEntityId(this._hass, "pv", "select", "battery_mode"),
      status: pwmFindEntityId(this._hass, "pv", "sensor", "battery_charge_status"),
      postpone: pwmFindEntityId(this._hass, "pv", "button", "postpone_battery_charge"),
      soc: pwmFindEntityId(this._hass, "pv", "sensor", "battery_soc"),
    };
    this._signature = undefined;
  }

  _state(name) {
    const entityId = this._ids[name];
    return entityId ? this._hass.states[entityId] : undefined;
  }

  /** Only re-render when something the card shows has changed. */
  _stateSignature() {
    const parts = ["command", "mode", "status", "soc"].map((name) => {
      const state = this._state(name);
      return state ? `${state.state}|${JSON.stringify(state.attributes)}` : "-";
    });
    parts.push(this._texts().title, pwmBatteryRecheckHours());
    return parts.join("#");
  }

  _render() {
    const t = this._texts();
    const command = this._state("command");
    const mode = this._state("mode");
    const status = this._state("status");

    const root = this.shadowRoot;
    root.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = PWM_BATTERY_CARD_STYLE;
    const card = document.createElement("ha-card");
    card.header = this._config.title || t.title;
    const content = document.createElement("div");
    content.className = "card-content";
    card.appendChild(content);
    root.append(style, card);

    if (!command || !mode || !status) {
      const warning = document.createElement("div");
      warning.className = "pwm-warning";
      warning.textContent = t.missing;
      content.appendChild(warning);
      return;
    }

    const isAuto = mode.state === "auto";
    const chargeMatch = /^charge_now_(\d+)$/.exec(command.state);
    const chargingPct = chargeMatch ? Number(chargeMatch[1]) : null;
    const planned = status.state === "yes_today" || status.state === "yes_tomorrow";

    // Banners -- same order and conditions as the Node-RED card.
    if (!isAuto) {
      content.appendChild(this._banner("bad", t.manual));
    } else if (chargingPct === null || !planned) {
      const key =
        status.state === "not_calculated" && pwmBatteryRecheckHours()
          ? "not_calculated_recheck"
          : status.state;
      const text = t.status[key];
      if (text) {
        content.appendChild(this._banner(PWM_BATTERY_STATUS_TONE[status.state], text));
      }
    }
    if (chargingPct !== null) {
      content.appendChild(this._banner("ok", t.charging(chargingPct)));
      if (isAuto && status.state === "yes_today") {
        const note = document.createElement("div");
        note.className = "pwm-note";
        note.textContent = t.autoControls;
        content.appendChild(note);
      }
    }

    // Tonight's cheap hours (battery_charge_status' hour_N attributes).
    if (isAuto && planned) {
      const hours = document.createElement("div");
      hours.className = "pwm-hours";
      for (let hour = 0; hour < 24; hour++) {
        if (status.attributes[`hour_${hour}`] !== true) continue;
        const tile = document.createElement("div");
        tile.className = "pwm-hour";
        tile.textContent = `${hour}-${hour + 1}`;
        hours.appendChild(tile);
      }
      if (hours.childElementCount) content.appendChild(hours);
    }

    // Manual control.
    const title = document.createElement("div");
    title.className = "pwm-section-title";
    title.textContent = t.manualTitle;
    content.appendChild(title);

    const buttons = document.createElement("div");
    buttons.className = "pwm-buttons";
    for (const pct of PWM_CHARGE_LEVELS) {
      if (pct === chargingPct) continue;
      buttons.appendChild(this._button(t.chargeNow(pct), () => this._setCommand(`charge_now_${pct}`)));
    }
    if (chargingPct !== null) {
      buttons.appendChild(this._button(t.stop, () => this._setCommand("internal")));
    }
    if (this._ids.postpone) {
      const deferred = status.state === "no_deferred";
      buttons.appendChild(
        this._button(deferred ? t.cancelPostpone : t.postpone, () =>
          this._hass.callService("button", "press", { entity_id: this._ids.postpone })
        )
      );
    }
    if (isAuto) {
      buttons.appendChild(
        this._button(t.disableAuto, () => {
          if (window.confirm(t.confirmDisable)) this._setMode("manual");
        })
      );
    } else {
      buttons.appendChild(this._button(t.enableAuto, () => this._setMode("auto")));
    }
    content.appendChild(buttons);

    // SoC while charging (Node-RED: the gauge under "Lad nu").
    const soc = this._state("soc");
    const socValue = soc ? parseFloat(soc.state) : NaN;
    if (chargingPct !== null && Number.isFinite(socValue)) {
      const wrap = document.createElement("div");
      wrap.className = "pwm-soc";
      const label = document.createElement("div");
      label.className = "pwm-soc-label";
      const name = document.createElement("span");
      name.textContent = t.soc;
      const value = document.createElement("span");
      value.textContent = `${Math.round(socValue)} %`;
      label.append(name, value);
      const bar = document.createElement("div");
      bar.className = "pwm-soc-bar";
      const fill = document.createElement("div");
      fill.className = "pwm-soc-fill";
      fill.style.width = `${Math.min(Math.max(socValue, 0), 100)}%`;
      bar.appendChild(fill);
      wrap.append(label, bar);
      content.appendChild(wrap);
    }
  }

  _banner(tone, text) {
    const banner = document.createElement("div");
    banner.className = `pwm-banner ${tone || "info"}`;
    banner.textContent = text;
    return banner;
  }

  _button(text, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", onClick);
    return button;
  }

  _setCommand(option) {
    this._hass.callService("select", "select_option", { entity_id: this._ids.command, option });
  }

  _setMode(option) {
    this._hass.callService("select", "select_option", { entity_id: this._ids.mode, option });
  }
}
customElements.define("pwm-battery-card", PwMBatteryCard);

// Register with Home Assistant's card picker so all three card types show
// up there with a name/description, instead of only being addable by
// typing their `type:` manually in YAML.
window.customCards = window.customCards || [];
window.customCards.push(
  {
    type: "pwm-hub-card",
    name: "PwM Hub",
    description: "PwM hub device configuration fields.",
  },
  {
    type: "pwm-charger-card",
    name: "PwM Charger",
    description: "A PwM charger device's configuration fields (set 'charger' to e.g. charger1 or charger2).",
  },
  {
    type: "pwm-pv-card",
    name: "PwM PV",
    description: "PwM PV system device configuration fields.",
  },
  {
    type: "pwm-electricity-card",
    name: "PwM Electricity",
    description: "PwM hub electricity billing/tariff configuration fields.",
  },
  {
    type: "pwm-battery-card",
    name: "PwM Battery",
    description: "House battery control: tonight's charge plan and manual charge/stop/postpone.",
  }
);
