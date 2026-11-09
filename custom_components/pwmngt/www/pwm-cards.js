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

// This copy's version, from the "?v=" the integration loads it with (see
// _async_register_frontend() in __init__.py); "0" when it has none.
const PWM_CARDS_VERSION = (() => {
  try {
    return new URL(import.meta.url).searchParams.get("v") || "0";
  } catch (error) {
    return "0";
  }
})();

/** Whether version a (e.g. "0.4.37") is newer than version b. */
function pwmIsNewer(a, b) {
  const pa = String(a).split(".").map(Number);
  const pb = String(b).split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] || 0;
    const y = pb[i] || 0;
    if (x !== y) return x > y;
  }
  return false;
}

/**
 * customElements.define(), but safe when this file runs more than once.
 * It is loaded both as an extra JS URL and as a dashboard resource (the
 * companion app only loads the latter), so in a browser it runs twice; a
 * second define() would throw and stop the rest of the file.
 *
 * Those two can be different versions: a cached Home Assistant page still
 * lists the previous release's URL, and whichever copy runs first owns the
 * element (issue #34 -- the battery card lost the night result that way).
 * So a newer copy moves its code onto the already registered class, the
 * whole prototype chain (PwMngtBaseCard too): the newest always wins.
 * Cards already on screen pick it up at their next render.
 */
function pwmDefine(name, elementClass) {
  const existing = customElements.get(name);
  if (!existing) {
    elementClass.pwmVersion = PWM_CARDS_VERSION;
    customElements.define(name, elementClass);
    return;
  }
  if (!pwmIsNewer(PWM_CARDS_VERSION, existing.pwmVersion || "0")) return;
  let source = elementClass.prototype;
  let target = existing.prototype;
  while (source && target && source !== HTMLElement.prototype && target !== HTMLElement.prototype) {
    for (const key of Object.getOwnPropertyNames(source)) {
      if (key === "constructor") continue;
      Object.defineProperty(target, key, Object.getOwnPropertyDescriptor(source, key));
    }
    source = Object.getPrototypeOf(source);
    target = Object.getPrototypeOf(target);
  }
  existing.pwmVersion = PWM_CARDS_VERSION;
}

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
pwmDefine("pwm-charger-card", PwMChargerCard);

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
pwmDefine("pwm-charger-card-editor", PwMChargerCardEditor);

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
pwmDefine("pwm-hub-card", PwMHubCard);

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
pwmDefine("pwm-electricity-card", PwMElectricityCard);

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
pwmDefine("pwm-pv-card", PwMPvCard);

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
    autoControls: "Tonight's charge is controlled by the automation",
    manualCharging: (pct) => `Manual charge at ${pct}% power`,
    manualRuns: "Runs until 100% SoC or until stopped",
    reducedByFuse: (pct) => `Reduced from ${pct}% because of the main fuse - goes up again when there is room`,
    pausedByFuse: (pct) => `Manual charge ${pct}% paused because of the main fuse`,
    status: {
      yes_today: "Battery charges today in these hours",
      yes_tomorrow: "Battery charges tomorrow in these hours",
      no_price: "Battery is not charged tonight, no hour tomorrow is expensive enough",
      no_surplus: "Battery is not charged tonight, PV surplus tomorrow",
      no_charge_needed: "Battery is not charged tonight, no charge needed",
      no_deferred: "Charging postponed one day",
      not_calculated: "Waiting for electricity prices (after 14)",
      not_calculated_recheck: "Not calculated yet - calculated at the next full hour",
    },
    skippedHours: "Red = skipped by the automation - the later hours are enough to reach the target",
    extendedHours: "Dashed = charged after the plan, the target wasn't reached in the planned hours",
    unusedHours: "Grey = not used, the target was already reached",
    chargeNotResponding: "The battery doesn't respond to the charge command - check the inverter",
    chargeRetry: (n, max) => `The battery didn't charge - command sent again (attempt ${n} of ${max})`,
    lastChargeDone: (time, target) =>
      `Tonight's charge finished at ${time}${target !== null ? ` - target ${target}% reached` : ""}`,
    lastChargeStopped: (time) => `Tonight's charge stopped at ${time} - the target wasn't reached`,
    kwh: (value) => `${value.toFixed(1)} kWh`,
    partialCharge: (pct, target, kwh) =>
      `Charges to ${pct}% from the grid - tomorrow's PV (${kwh} surplus expected) fills it up to the target of ${target}%`,
    fullCharge: (pct, kwh) =>
      `Charges all the way to ${pct}% from the grid - tomorrow's PV surplus is only ${kwh}`,
    fullChargeNoSurplus: (pct) => `Charges all the way to ${pct}% from the grid - no PV surplus expected tomorrow`,
    chargeTo: (pct) => `Charges to ${pct}% from the grid`,
    surplusCovers: (target, kwh) =>
      `Tomorrow's PV surplus (${kwh}, cautious estimate) covers the target of ${target}%`,
    chargeCovers: (pct, hours) => `Charges to ${pct}% from the grid - enough for ${hours}`,
    dayHours: (kwh, hours, reserve) =>
      `Of which about ${kwh} for the daytime hours ${hours} - the battery keeps ${reserve}% for the evening`,
    dayPlanToday: (hours, reserve) =>
      `Today the battery covers the house at ${hours} - down to ${reserve}%, the rest is kept for the evening`,
    dayPlanFromToday: "Worked out from today's prices and what the battery's grid power cost",
    pvFills: (kwh, target) => `Tomorrow's PV (${kwh} surplus expected) fills it up towards the target of ${target}%`,
    surplusCoversWorth: (hours, kwh) =>
      `Tomorrow's PV surplus (${kwh}, cautious estimate) covers ${hours}, the hours tonight's charge would cover`,
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
    autoControls: "Nattens ladning styres af automatikken",
    manualCharging: (pct) => `Manuel ladning med ${pct} % effekt`,
    manualRuns: "Kører til 100 % SoC, eller til den afbrydes",
    reducedByFuse: (pct) => `Sænket fra ${pct} % pga. hovedsikring - går op igen, når der er plads`,
    pausedByFuse: (pct) => `Manuel ladning ${pct} % sat på pause pga. hovedsikring`,
    status: {
      yes_today: "Batteri lades i dag i disse timer",
      yes_tomorrow: "Batteri lades i morgen i disse timer",
      no_price: "Batteri lades ikke i nat, ingen time i morgen er dyr nok",
      no_surplus: "Batteri lades ikke i nat, PV-overskud i morgen",
      no_charge_needed: "Batteri lades ikke i nat, intet ladebehov",
      no_deferred: "Ladning udskudt 1 dag",
      not_calculated: "Afventer elpriser (efter 14)",
      not_calculated_recheck: "Ikke beregnet endnu - beregnes ved næste hele time",
    },
    skippedHours: "Rød = sprunget over af automatikken - de senere timer er nok til at nå target",
    extendedHours: "Stiplet = ladet efter planen, target blev ikke nået i de planlagte timer",
    unusedHours: "Grå = ikke brugt, target var allerede nået",
    chargeNotResponding: "Batteriet reagerer ikke på ladekommandoen - tjek inverteren",
    chargeRetry: (n, max) => `Batteriet ladede ikke - kommandoen sendt igen (forsøg ${n} af ${max})`,
    lastChargeDone: (time, target) =>
      `Nattens ladning færdig kl. ${time}${target !== null ? ` - target ${target} % nået` : ""}`,
    lastChargeStopped: (time) => `Nattens ladning stoppet kl. ${time} - target blev ikke nået`,
    kwh: (value) => `${value.toFixed(1).replace(".", ",")} kWh`,
    partialCharge: (pct, target, kwh) =>
      `Lades til ${pct} % fra nettet - solen i morgen (forventet ${kwh} overskud) fylder op til target ${target} %`,
    fullCharge: (pct, kwh) =>
      `Lades helt til ${pct} % fra nettet - forventet sol-overskud i morgen kun ${kwh}`,
    fullChargeNoSurplus: (pct) => `Lades helt til ${pct} % fra nettet - intet sol-overskud forventet i morgen`,
    chargeTo: (pct) => `Lades til ${pct} % fra nettet`,
    surplusCovers: (target, kwh) =>
      `Forventet sol-overskud i morgen (${kwh}, forsigtigt skøn) dækker target ${target} %`,
    chargeCovers: (pct, hours) => `Lades til ${pct} % fra nettet - nok til kl. ${hours}`,
    dayHours: (kwh, hours, reserve) =>
      `Heraf ca. ${kwh} til dagtimerne kl. ${hours} - batteriet gemmer ${reserve} % til aftenen`,
    dayPlanToday: (hours, reserve) =>
      `I dag dækker batteriet huset kl. ${hours} - ned til ${reserve} %, resten gemmes til aftenen`,
    dayPlanFromToday: "Beregnet ud fra dagens priser og hvad netstrømmen i batteriet kostede",
    pvFills: (kwh, target) => `Solen i morgen (forventet ${kwh} overskud) fylder op mod target ${target} %`,
    surplusCoversWorth: (hours, kwh) =>
      `Forventet sol-overskud i morgen (${kwh}, forsigtigt skøn) dækker kl. ${hours}, de timer natladningen skulle dække`,
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
  .pwm-hour.skipped {
    background: var(--error-color, #db4437);
  }
  .pwm-hour.unused {
    background: var(--disabled-color, #9e9e9e);
  }
  .pwm-hour.extended {
    outline: 2px dashed var(--text-primary-color, #fff);
    outline-offset: -4px;
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
 * - A "Charge now" from the card is a manual charge (issue #17): the
 *   automation can't override it, only the main fuse can lower or pause it
 *   (battery_command's manual_charge attribute holds the chosen level), and
 *   the card says so. It runs until 100 % SoC or "Stop charging".
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
      target: pwmFindEntityId(this._hass, "pv", "sensor", "battery_nightly_target"),
      // Issues #59/#61: today's day plan (day_plan_* attributes).
      data: pwmFindEntityId(this._hass, "pv", "sensor", "battery_data"),
    };
    this._signature = undefined;
  }

  _state(name) {
    const entityId = this._ids[name];
    return entityId ? this._hass.states[entityId] : undefined;
  }

  /** Only re-render when something the card shows has changed. */
  _stateSignature() {
    const parts = ["command", "mode", "status", "soc", "target"].map((name) => {
      const state = this._state(name);
      return state ? `${state.state}|${JSON.stringify(state.attributes)}` : "-";
    });
    // Only the day plan from battery_data -- its other attributes change
    // with every SoC point.
    const data = this._state("data");
    parts.push(
      data
        ? JSON.stringify([data.attributes.day_plan_hours, data.attributes.day_plan_reserve, data.attributes.day_plan_source])
        : "-"
    );
    parts.push(this._texts().title, pwmBatteryRecheckHours(), new Date().getHours());
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
    const manualMatch = /^charge_now_(\d+)$/.exec(command.attributes.manual_charge || "");
    const manualPct = manualMatch ? Number(manualMatch[1]) : null;
    const planned = status.state === "yes_today" || status.state === "yes_tomorrow";

    // Issue #37: the charge watchdog -- the battery didn't follow a charge
    // command.
    const watchdog = status.attributes.charge_watchdog;
    if (watchdog && watchdog.failed) {
      content.appendChild(this._banner("bad", t.chargeNotResponding));
    } else if (watchdog && watchdog.retries > 0) {
      content.appendChild(this._note(t.chargeRetry(watchdog.retries, watchdog.max_retries)));
    }

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
    // Issue #43: what the decision is based on, and how far tonight's
    // charge goes (a partial charge leaves the rest to tomorrow's PV).
    if (isAuto) {
      for (const decision of this._decisionText(t, status)) {
        content.appendChild(this._note(decision));
      }
    }

    if (manualPct !== null) {
      if (chargingPct === null) {
        content.appendChild(this._banner("warn", t.pausedByFuse(manualPct)));
      } else {
        content.appendChild(this._banner("ok", t.manualCharging(chargingPct)));
      }
      content.appendChild(
        this._note(chargingPct !== null && chargingPct < manualPct ? t.reducedByFuse(manualPct) : t.manualRuns)
      );
    } else if (chargingPct !== null) {
      content.appendChild(this._banner("ok", t.charging(chargingPct)));
      if (isAuto && status.state === "yes_today") {
        content.appendChild(this._note(t.autoControls));
      }
    }

    // Tonight's cheap hours (battery_charge_status' hour_N attributes),
    // plus the night plan's marks (issue #24): a skipped planned hour is
    // red, an unplanned hour charged in after the plan is added.
    if (isAuto && planned) {
      const plannedHours = [];
      for (let hour = 0; hour < 24; hour++) {
        if (status.attributes[`hour_${hour}`] === true) plannedHours.push(hour);
      }
      this._hourTiles(
        content,
        t,
        plannedHours,
        status.attributes.skipped_hours || [],
        status.attributes.extended_hours || [],
        null
      );
    }

    // How tonight's plan ended, until a new decision is made (issue #24).
    const lastCharge = status.attributes.last_charge;
    if (isAuto && !planned && lastCharge && lastCharge.finished) {
      const finished = new Date(lastCharge.finished);
      const time = `${String(finished.getHours()).padStart(2, "0")}:${String(
        finished.getMinutes()).padStart(2, "0")}`;
      const target = Number.isFinite(Number(lastCharge.target)) && lastCharge.target !== null
        ? Math.round(Number(lastCharge.target)) : null;
      content.appendChild(
        lastCharge.target_reached
          ? this._banner("ok", t.lastChargeDone(time, target))
          : this._banner("warn", t.lastChargeStopped(time))
      );
      this._hourTiles(
        content,
        t,
        lastCharge.hours || [],
        lastCharge.skipped_hours || [],
        lastCharge.extended_hours || [],
        lastCharge.target_reached ? finished.getHours() : null
      );
    }

    // Issues #59/#61: today's day plan, while hours of it are still ahead.
    const dayPlan = this._dayPlanToday();
    if (isAuto && dayPlan) {
      content.appendChild(this._banner("ok", t.dayPlanToday(pwmHourRanges(dayPlan.hours), dayPlan.reserve)));
      if (dayPlan.source === "today") content.appendChild(this._note(t.dayPlanFromToday));
    }

    // Manual control.
    const title = document.createElement("div");
    title.className = "pwm-section-title";
    title.textContent = t.manualTitle;
    content.appendChild(title);

    const buttons = document.createElement("div");
    buttons.className = "pwm-buttons";
    for (const pct of PWM_CHARGE_LEVELS) {
      if (pct === (manualPct !== null ? manualPct : chargingPct)) continue;
      buttons.appendChild(this._button(t.chargeNow(pct), () => this._setCommand(`charge_now_${pct}`)));
    }
    if (chargingPct !== null || manualPct !== null) {
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
    if ((chargingPct !== null || manualPct !== null) && Number.isFinite(socValue)) {
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

  /**
   * The plan's hour tiles: green planned, red skipped by the night plan
   * (issue #22), dashed when charged after the plan, grey when the target
   * was already reached before the hour (unusedAfter = the hour it was).
   */
  _hourTiles(content, t, plannedHours, skipped, extended, unusedAfter) {
    const hours = document.createElement("div");
    hours.className = "pwm-hours";
    let anyUnused = false;
    for (let hour = 0; hour < 24; hour++) {
      const isPlanned = plannedHours.includes(hour);
      if (!isPlanned && !extended.includes(hour)) continue;
      const unused =
        isPlanned && unusedAfter !== null && hour > unusedAfter && hour < 17 && !skipped.includes(hour);
      anyUnused = anyUnused || unused;
      const tile = document.createElement("div");
      tile.className = `pwm-hour${skipped.includes(hour) ? " skipped" : ""}${
        !isPlanned ? " extended" : ""}${unused ? " unused" : ""}`;
      tile.textContent = `${hour}-${hour + 1}`;
      hours.appendChild(tile);
    }
    if (!hours.childElementCount) return;
    content.appendChild(hours);
    if (skipped.length) content.appendChild(this._note(t.skippedHours));
    if (extended.length) content.appendChild(this._note(t.extendedHours));
    if (anyUnused) content.appendChild(this._note(t.unusedHours));
  }

  /**
   * Issue #43: the charge decision in words -- the charge target tonight
   * (partial or full) against the nightly target, and tomorrow's cautious
   * PV surplus it is based on. Issue #50: and the evening hours worth
   * charging for. Empty when there is nothing to say.
   */
  _decisionText(t, status) {
    const number = (value) =>
      value === null || value === undefined || !Number.isFinite(Number(value)) ? null : Number(value);
    const surplus = number(status.attributes.expected_pv_surplus);
    const chargeTarget = number(status.attributes.charge_target);
    const covered = Array.isArray(status.attributes.covered_hours) ? status.attributes.covered_hours : null;
    const hours = covered && covered.length ? pwmHourRanges(covered) : null;
    const targetState = this._state("target");
    const target = targetState ? number(targetState.state) : null;
    const kwh = surplus === null ? null : t.kwh(Math.max(surplus, 0));
    if (status.state === "yes_today" || status.state === "yes_tomorrow") {
      if (chargeTarget === null) return [];
      const pct = Math.round(chargeTarget);
      const lines = [];
      if (hours) {
        // Issue #50: the charge covers these hours; PV (if any) tops up.
        lines.push(t.chargeCovers(pct, hours));
        // Issue #59: and the expensive daytime hours, down to the evening
        // reserve.
        const dayHours = Array.isArray(status.attributes.day_hours) ? status.attributes.day_hours : [];
        const dayKwh = number(status.attributes.day_kwh);
        const reserve = number(status.attributes.evening_reserve_soc);
        if (dayHours.length && dayKwh !== null && reserve !== null) {
          lines.push(t.dayHours(t.kwh(dayKwh), pwmHourRanges(dayHours), Math.round(reserve)));
        }
        if (surplus !== null && surplus >= 0.05 && target !== null && chargeTarget < target) {
          lines.push(t.pvFills(kwh, Math.round(target)));
        }
        return lines;
      }
      if (target !== null && chargeTarget < target && kwh !== null) {
        lines.push(t.partialCharge(pct, Math.round(target), kwh));
      } else {
        // Below 0.05 kWh the surplus reads "0.0 kWh" -- say "none" instead.
        if (kwh === null) lines.push(t.chargeTo(pct));
        else if (surplus < 0.05) lines.push(t.fullChargeNoSurplus(pct));
        else lines.push(t.fullCharge(pct, kwh));
      }
      return lines;
    }
    if (status.state === "no_surplus" && kwh !== null) {
      if (hours) return [t.surplusCoversWorth(hours, kwh)];
      if (target !== null) return [t.surplusCovers(Math.round(target), kwh)];
    }
    return [];
  }

  /**
   * Issues #59/#61: battery_data's plan for today -- {hours (the ones still
   * ahead), reserve, source} -- or null.
   */
  _dayPlanToday() {
    const data = this._state("data");
    if (!data) return null;
    const hours = Array.isArray(data.attributes.day_plan_hours) ? data.attributes.day_plan_hours : [];
    const reserve = Number(data.attributes.day_plan_reserve);
    const now = new Date().getHours();
    const ahead = hours.map(Number).filter((hour) => hour >= now);
    if (!ahead.length || !Number.isFinite(reserve)) return null;
    return { hours: ahead, reserve: Math.round(reserve), source: data.attributes.day_plan_source };
  }

  _note(text) {
    const note = document.createElement("div");
    note.className = "pwm-note";
    note.textContent = text;
    return note;
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
pwmDefine("pwm-battery-card", PwMBatteryCard);

/**
 * Issue #50: hours as clock ranges -- [17, 18, 19, 20, 22] -> "17-21, 22-23".
 */
function pwmHourRanges(hours) {
  const sorted = [...hours].map(Number).sort((a, b) => a - b);
  const ranges = [];
  for (const hour of sorted) {
    const last = ranges[ranges.length - 1];
    if (last && last[1] === hour) last[1] = hour + 1;
    else ranges.push([hour, hour + 1]);
  }
  return ranges.map(([from, to]) => `${from}-${to}`).join(", ");
}

/** Battery ring card texts -- same language pick as PWM_BATTERY_TEXTS. */
const PWM_RING_TEXTS = {
  en: {
    title: "Battery",
    charging: (kw) => `▲ ${kw} kW charging`,
    discharging: (kw) => `▼ ${kw} kW discharging`,
    idle: "idle",
    pv: (pct) => `☀ PV ${pct}%`,
    grid: (pct) => `⚡ Grid ${pct}%`,
    min: (pct) => `🔒 Min ${pct}%`,
    target: (pct) => `🎯 Target ${pct}%`,
    toFull: (time) => `⏱ full in ${time}`,
    toMin: (time) => `⏱ min in ${time}`,
    command: {
      internal: "Internal control",
      block_charging: "Charging blocked",
      hold_min_soc: "Battery hold active",
      charge: (pct) => `Charging ${pct}%`,
      manual: " (manual)",
      manualPaused: "Manual charge - paused (main fuse)",
      discharge: (pct) => `Discharging ${pct}%`,
    },
    hold: {
      holding_at_limit: "held at min",
      hard_hold_low_pv: "held (low PV)",
      above_limit: "above the limit",
      waiting_discharge: "waiting",
      at_limit: "at the limit",
      catching_up: "below the limit",
      normal_operation: "normal operation",
      blocking_charge: "charging blocked",
      blocking_charge_at_limit: "blocked at min",
      paused_other_command: "paused",
      released_no_limit: "released",
      covering_house_car_charging: "covers the house, the car from the grid",
      car_charging_pv_to_car: "PV to the house and car, battery idle",
      car_charging_pv_to_battery: "PV to the battery, the car from the grid",
    },
    missing: "PwMngt battery entities not found - is the Solar PV Plant set up?",
  },
  da: {
    title: "Batteri",
    charging: (kw) => `▲ ${kw} kW lader`,
    discharging: (kw) => `▼ ${kw} kW aflader`,
    idle: "hviler",
    pv: (pct) => `☀ PV ${pct} %`,
    grid: (pct) => `⚡ Net ${pct} %`,
    min: (pct) => `🔒 Min ${pct} %`,
    target: (pct) => `🎯 Target ${pct} %`,
    toFull: (time) => `⏱ fuld om ${time}`,
    toMin: (time) => `⏱ min om ${time}`,
    command: {
      internal: "Intern styring",
      block_charging: "Ladning blokeret",
      hold_min_soc: "Batterilås aktiv",
      charge: (pct) => `Lader ${pct} %`,
      manual: " (manuel)",
      manualPaused: "Manuel ladning - pause (hovedsikring)",
      discharge: (pct) => `Aflader ${pct} %`,
    },
    hold: {
      holding_at_limit: "låst ved min",
      hard_hold_low_pv: "låst (lav PV)",
      above_limit: "over grænsen",
      waiting_discharge: "afventer",
      at_limit: "ved grænsen",
      catching_up: "under grænsen",
      normal_operation: "normal drift",
      blocking_charge: "ladning blokeret",
      blocking_charge_at_limit: "blokeret ved min",
      paused_other_command: "pause",
      released_no_limit: "frigivet",
      covering_house_car_charging: "dækker huset, bilen fra nettet",
      car_charging_pv_to_car: "PV til hus og bil, batteriet hviler",
      car_charging_pv_to_battery: "PV til batteriet, bilen fra nettet",
    },
    missing: "PwMngt's batteri-entities blev ikke fundet - er Solar PV Plant sat op?",
  },
};

const PWM_RING_CARD_STYLE = `
  ha-card {
    padding: 12px 12px 16px;
    border-radius: var(--ha-card-border-radius, 18px);
    background:
      radial-gradient(circle at 50% 35%, rgba(255, 180, 0, 0.10), transparent 60%),
      var(--ha-card-background, var(--card-background-color));
  }
  svg { width: 100%; max-width: 260px; display: block; margin: auto; }
  .live { animation: pwm-pulse 2.4s ease-in-out infinite; }
  @keyframes pwm-pulse { 0%, 100% { opacity: 0.75; } 50% { opacity: 1; } }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; margin-top: 4px; }
  .chip { padding: 4px 10px; border-radius: 999px; font-size: 12px; white-space: nowrap;
    color: var(--primary-text-color); background: var(--secondary-background-color); }
  .chip.pv { background: rgba(255, 179, 0, 0.18); }
  .chip.grid { background: rgba(30, 136, 229, 0.18); }
  .chip.min { background: rgba(229, 57, 53, 0.15); }
  .chip.target { background: rgba(102, 187, 106, 0.18); }
  .status { margin-top: 8px; text-align: center; font-size: 12px; color: var(--secondary-text-color); }
  .pwm-warning { color: var(--error-color); font-size: 13px; }
`;

// The ring is 270 degrees, open at the bottom.
const PWM_RING_RADIUS = 80;
const PWM_RING_CIRCUMFERENCE = 2 * Math.PI * PWM_RING_RADIUS;
const PWM_RING_ARC = PWM_RING_CIRCUMFERENCE * 0.75;
// Below this (W) the battery counts as idle.
const PWM_RING_IDLE_W = 50;

/**
 * Battery ring (issue #32): SoC split into the share charged from PV and
 * from the grid (battery_data's soc_pv/soc_grid), the minimum SoC and
 * tonight's target as marks, the battery's power, and what PwMngt is
 * doing with the battery. Finds its entities itself on the PwM PV device,
 * like the other PwM cards -- no configuration needed.
 */
class PwMBatteryRingCard extends HTMLElement {
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
    return 5;
  }

  _texts() {
    const language = (this._hass.locale && this._hass.locale.language) || this._hass.language || "en";
    return PWM_RING_TEXTS[String(language).split("-")[0]] || PWM_RING_TEXTS.en;
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
      soc: pwmFindEntityId(this._hass, "pv", "sensor", "battery_soc"),
      power: pwmFindEntityId(this._hass, "pv", "sensor", "battery_power"),
      data: pwmFindEntityId(this._hass, "pv", "sensor", "battery_data"),
      target: pwmFindEntityId(this._hass, "pv", "sensor", "battery_nightly_target"),
      size: pwmFindEntityId(this._hass, "pv", "select", "battery_size"),
      command: pwmFindEntityId(this._hass, "pv", "select", "battery_command"),
      hold: pwmFindEntityId(this._hass, "pv", "sensor", "battery_hold_power"),
    };
    this._signature = undefined;
  }

  _state(name) {
    const entityId = this._ids[name];
    return entityId ? this._hass.states[entityId] : undefined;
  }

  /** A state (or one attribute of it) as a number, NaN when missing. */
  _number(name, attribute) {
    const state = this._state(name);
    if (!state) return NaN;
    const raw = attribute ? state.attributes[attribute] : state.state;
    return raw === null || raw === undefined || raw === "" ? NaN : Number(raw);
  }

  _stateSignature() {
    const parts = Object.keys(this._ids).map((name) => {
      const state = this._state(name);
      return state ? `${state.state}|${JSON.stringify(state.attributes)}` : "-";
    });
    parts.push(this._texts().title);
    return parts.join("#");
  }

  _render() {
    const t = this._texts();
    const root = this.shadowRoot;
    root.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = PWM_RING_CARD_STYLE;
    const card = document.createElement("ha-card");
    if (this._config.title) card.header = this._config.title;
    root.append(style, card);

    const socState = this._state("soc");
    if (!socState) {
      const warning = document.createElement("div");
      warning.className = "pwm-warning";
      warning.textContent = t.missing;
      card.appendChild(warning);
      return;
    }

    const soc = Math.max(0, Math.min(100, Number(socState.state) || 0));
    const pv = this._number("data", "soc_pv");
    const grid = this._number("data", "soc_grid");
    const minSoc = this._number("data");
    const target = this._number("target");
    const size = this._number("size");
    // Normalized by PwMngt (pv_properties.power_sign()): + = discharging.
    const power = this._number("power") || 0;

    card.appendChild(this._ring(t, soc, pv, grid, minSoc, target, size, power));
    card.appendChild(this._chips(t, soc, pv, grid, minSoc, target, size, power));
    const status = this._statusText(t);
    if (status) {
      const line = document.createElement("div");
      line.className = "status";
      line.textContent = status;
      card.appendChild(line);
    }
  }

  _ring(t, soc, pv, grid, minSoc, target, size, power) {
    const charging = power < -PWM_RING_IDLE_W;
    const discharging = power > PWM_RING_IDLE_W;
    const pvShare = Number.isFinite(pv) ? pv : 0;
    const gridShare = Number.isFinite(grid) ? grid : 0;
    // battery_data's floor below its counted cells has no source.
    const reserve = Math.max(0, soc - pvShare - gridShare);
    const live = charging || discharging ? "live" : "";
    const kw = pwmFormatKw(Math.abs(power));
    const powerColor = charging ? "#43a047" : discharging ? "#fb8c00" : "var(--secondary-text-color)";
    const powerText = charging ? t.charging(kw) : discharging ? t.discharging(kw) : t.idle;
    const kwh = Number.isFinite(size) ? `${pwmFormatDecimal((soc * size) / 100)} kWh` : "";

    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <svg viewBox="0 0 200 175">
        ${pwmRingSegment(0, 100, "var(--divider-color)", "", "round")}
        ${pwmRingSegment(0, reserve, "#78909c")}
        ${pwmRingSegment(reserve, reserve + gridShare, "#1e88e5", live)}
        ${pwmRingSegment(reserve + gridShare, soc, "#ffb300", live)}
        ${pwmRingTick(minSoc, "#e53935", false)}
        ${pwmRingTick(target, "#66bb6a", true)}
        <text x="100" y="92" text-anchor="middle" font-size="40" font-weight="600"
          fill="var(--primary-text-color)">${Math.round(soc)}<tspan font-size="20">%</tspan></text>
        <text x="100" y="114" text-anchor="middle" font-size="12" fill="var(--secondary-text-color)">${kwh}</text>
        <text x="100" y="138" text-anchor="middle" font-size="14" font-weight="500" fill="${powerColor}">${powerText}</text>
        <text x="100" y="170" text-anchor="middle" font-size="13" fill="var(--secondary-text-color)">${this._config.name || t.title}</text>
      </svg>`;
    return wrap;
  }

  _chips(t, soc, pv, grid, minSoc, target, size, power) {
    const chips = document.createElement("div");
    chips.className = "chips";
    const add = (cls, text) => {
      const chip = document.createElement("span");
      chip.className = `chip ${cls}`;
      chip.textContent = text;
      chips.appendChild(chip);
    };
    if (Number.isFinite(pv)) add("pv", t.pv(pv));
    if (Number.isFinite(grid)) add("grid", t.grid(grid));
    if (Number.isFinite(minSoc)) add("min", t.min(minSoc));
    if (Number.isFinite(target)) add("target", t.target(target));
    if (Number.isFinite(size) && size > 0) {
      if (power < -PWM_RING_IDLE_W) {
        const time = pwmFormatHours((((100 - soc) / 100) * size * 1000) / -power);
        if (time) add("", t.toFull(time));
      } else if (power > PWM_RING_IDLE_W && Number.isFinite(minSoc)) {
        const time = pwmFormatHours((((soc - minSoc) / 100) * size * 1000) / power);
        if (time) add("", t.toMin(time));
      }
    }
    return chips;
  }

  _statusText(t) {
    const command = this._state("command");
    if (!command) return "";
    const manual = command.attributes.manual_charge;
    const charge = /^charge_now_(\d+)$/.exec(command.state);
    const discharge = /^discharge_now_(\d+)$/.exec(command.state);
    let text;
    if (charge) text = t.command.charge(charge[1]) + (manual ? t.command.manual : "");
    else if (manual) text = t.command.manualPaused;
    else if (discharge) text = t.command.discharge(discharge[1]);
    else text = t.command[command.state] || command.state;

    // Battery hold power exists on a Growatt only (data/battery_hold.py).
    const hold = this._state("hold");
    if (hold) {
      const holdText = t.hold[hold.attributes.status];
      if (holdText) text += ` · ${holdText}`;
      const watts = Number(hold.state);
      if (Number.isFinite(watts) && watts > 0) text += ` · ${Math.round(watts)} W`;
    }
    return text;
  }
}
pwmDefine("pwm-battery-ring-card", PwMBatteryRingCard);

/** One coloured stretch of the battery ring, from..to in % of SoC. Butt
 * ends by default, so neighbouring stretches meet without overlapping. */
function pwmRingSegment(from, to, color, cls, linecap = "butt") {
  from = Math.max(0, from);
  to = Math.min(100, to);
  if (to <= from) return "";
  const length = (PWM_RING_ARC * (to - from)) / 100;
  const start = (PWM_RING_ARC * from) / 100;
  return `<circle cx="100" cy="100" r="${PWM_RING_RADIUS}" fill="none" stroke="${color}"
    stroke-width="14" stroke-linecap="${linecap}" class="${cls || ""}"
    stroke-dasharray="${length} ${PWM_RING_CIRCUMFERENCE}" stroke-dashoffset="${-start}"
    transform="rotate(135 100 100)"/>`;
}

/** A mark across the ring at value % (min SoC, target). */
function pwmRingTick(value, color, dashed) {
  if (!Number.isFinite(value)) return "";
  const angle = ((135 + (270 * Math.min(Math.max(value, 0), 100)) / 100) * Math.PI) / 180;
  const point = (radius) => [100 + radius * Math.cos(angle), 100 + radius * Math.sin(angle)];
  const [x1, y1] = point(66);
  const [x2, y2] = point(94);
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}"
    stroke-width="3" stroke-linecap="round"${dashed ? ' stroke-dasharray="3 3"' : ""}/>`;
}

/** 1 decimal with a decimal comma, as the rest of the cards' Danish texts. */
function pwmFormatDecimal(value) {
  return value.toFixed(1).replace(".", ",");
}

function pwmFormatKw(watts) {
  return pwmFormatDecimal(watts / 1000);
}

/** Hours as h:mm, or null when it isn't a useful estimate (0 or > 48 h). */
function pwmFormatHours(hours) {
  if (!Number.isFinite(hours) || hours <= 0 || hours > 48) return null;
  const minutes = Math.round(hours * 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

// Register with Home Assistant's card picker so all three card types show
// up there with a name/description, instead of only being addable by
// typing their `type:` manually in YAML.
window.customCards = window.customCards || [];
const PWM_CUSTOM_CARDS = [
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
  },
  {
    type: "pwm-battery-ring-card",
    name: "PwM Battery ring",
    description: "Battery SoC as a ring: PV/grid share, min SoC, target, power and status.",
  }
];
// Skip the ones already listed -- this file may run twice (see pwmDefine()).
for (const card of PWM_CUSTOM_CARDS) {
  if (!window.customCards.some((existing) => existing.type === card.type)) {
    window.customCards.push(card);
  }
}
