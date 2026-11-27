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
      {
        device: "pv", key: "pv_battery_sell_price_difference",
        label: "PV battery sell price difference",
        icon: "mdi:cash-plus",
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
    // Issue #92: level is the charge power ("11.3 kW"), or "75% power"
    // while Battery max charge effect isn't known (see _chargeLevel()).
    charging: (level) => `Battery is charging at ${level}`,
    autoControls: "Tonight's charge is controlled by the automation",
    manualCharging: (level) => `Manual charge at ${level}`,
    manualRuns: (target) => `Runs until ${target}% SoC or until stopped`,
    reducedByFuse: (level) => `Reduced from ${level} because of the main fuse - goes up again when there is room`,
    pausedByFuse: (level) => `Manual charge at ${level} paused because of the main fuse`,
    kw: (value) => `${pwmKwText(value, ".")} kW`,
    pctPower: (pct) => `${pct}% power`,
    status: {
      yes_today: "Battery charges today in these hours",
      yes_tomorrow: "Battery charges tomorrow in these hours",
      no_price: "Battery is not charged tonight, no hour tomorrow is expensive enough",
      no_surplus: "Battery is not charged tonight, PV surplus tomorrow",
      no_charge_needed: "Battery is not charged tonight, no charge needed",
      no_deferred: "Charging postponed one day",
      no_manual: "Battery is not charged tonight - chosen manually",
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
    planEnded: (time, target, soc) =>
      `The charge plan ended at ${time} - the target of ${target}% wasn't reached${soc !== null ? ` (SoC ${soc}%)` : ""}`,
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
    chargeWithPvCovers: (pct, kwh, hours) =>
      `Charges to ${pct}% from the grid - together with tomorrow's PV (${kwh} surplus expected) it lasts until ${hours}`,
    dayHours: (kwh, hours, reserve, floor) =>
      floor < reserve
        ? `Of which about ${kwh} for the daytime hours ${hours} - down to ${floor}%, the sun refills ${reserve}% for the evening`
        : `Of which about ${kwh} for the daytime hours ${hours} - the battery keeps ${reserve}% for the evening`,
    dayPlanToday: (hours, reserve) =>
      `Today the battery covers the house at ${hours} - down to ${reserve}%, the rest is kept for the evening`,
    dayPlanFromToday: "Worked out from today's prices and what the battery's grid power cost",
    saleTonight: (kwh, hours, profit) =>
      `Plus about ${kwh} for selling at ${hours} - expected profit about ${profit} kr`,
    saleToday: (hours) => `Today the battery sells at ${hours}`,
    pvRelease: (hours, floor) => `Today the battery uses solar power at ${hours} - down to ${floor}%`,
    pvReleaseNote: (kwh, instead) => `About ${kwh} that would otherwise be used at ${instead}`,
    pvKept: (kwh) =>
      `Solar power above the evening reserve (about ${kwh}) is kept for later - today's remaining hours aren't dearer`,
    pvNight: (hours) => `${hours} tonight`,
    movedToToday: (today, from, when) => `Charges at ${today} today instead of ${from} ${when}`,
    tonight: "tonight",
    tomorrow: "tomorrow",
    and: " and ",
    sellingNow: "The battery is selling to the grid now",
    pvFills: (target) => `Tomorrow's PV is expected to fill it up to the target of ${target}%`,
    decimal: ".",
    notHigher: (price) =>
      `Not charged higher - only hours priced above ${price} kr/kWh (charge price + price difference) are worth charging for`,
    surplusCoversWorth: (hours, kwh) =>
      `Tomorrow's PV surplus (${kwh}, cautious estimate) covers ${hours}, the hours tonight's charge would cover`,
    manualTitle: "Manual battery control",
    // Issue #112: the title while the manual buttons are unlocked.
    manualActiveTitle: "Manual control active",
    // Issue #110: the title while the manual buttons are locked and the
    // automation runs.
    autoTitle: "Automatic operation active",
    stop: "Stop charging",
    postpone: "Postpone charging 1 day",
    cancelPostpone: "Cancel postponement",
    disableAuto: "Disable automatic charging",
    enableAuto: "Enable automatic charging",
    unlockHold: "🔒 Unlock manual control - hold 2 s",
    holdHint: "Hold the button for 2 seconds",
    // Issue #117: the always-shown buttons are held for 2 s too.
    holdLabel: (text) => `${text} - hold 2 s`,
    relock: "🔓 Lock",
    relockToAuto: "🔒 Lock and go back to automatic operation",
    // Issue #112: one "Charge now" with power and target SoC.
    chargeNowButton: "Charge now",
    chargePower: (level) => `Power: ${level}`,
    chargeUntil: (pct) => `Charge to: ${pct}%`,
    startCharge: "Start charging",
    soc: "Battery SoC",
    missing: "PwMngt battery entities not found - is the Solar PV Plant set up?",
    // Issue #105: the night's charge target, chosen by hand.
    manualChosen: (pct) => `Chosen manually: charges to ${pct}% tonight`,
    manualNone: "Chosen manually: no grid charge tonight",
    autoWould: (pct) =>
      pct > 0
        ? `The automation would charge to ${pct}% - it takes over again tomorrow`
        : "The automation wouldn't charge - it takes over again tomorrow",
    editTonight: "Choose tonight's charge",
    // Tomorrow is a dark day: the automation charges up to 100 %.
    darkDay: "Very little sun tomorrow - so up to 100% instead of 95%",
    editorNone: "No grid charge",
    editorAuto: (label) => `Automatic: ${label}`,
    quickNone: "No charge",
    quickAuto: (label) => `Automatic (${label})`,
    quickFull: (pct) => `Full (${pct}%)`,
    useTonight: "Save and use tonight",
    resetAuto: "Reset to automatic",
    close: "Cancel",
  },
  da: {
    title: "Hus batteri styring",
    manual: "Automatik og batterilås slået fra - kun manuel ladning",
    charging: (level) => `Batteri lader nu med ${level}`,
    autoControls: "Nattens ladning styres af automatikken",
    manualCharging: (level) => `Manuel ladning med ${level}`,
    manualRuns: (target) => `Kører til ${target} % SoC, eller til den afbrydes`,
    reducedByFuse: (level) => `Sænket fra ${level} pga. hovedsikring - går op igen, når der er plads`,
    pausedByFuse: (level) => `Manuel ladning med ${level} sat på pause pga. hovedsikring`,
    kw: (value) => `${pwmKwText(value, ",")} kW`,
    pctPower: (pct) => `${pct} % effekt`,
    status: {
      yes_today: "Batteri lades i dag i disse timer",
      yes_tomorrow: "Batteri lades i morgen i disse timer",
      no_price: "Batteri lades ikke i nat, ingen time i morgen er dyr nok",
      no_surplus: "Batteri lades ikke i nat, PV-overskud i morgen",
      no_charge_needed: "Batteri lades ikke i nat, intet ladebehov",
      no_deferred: "Ladning udskudt 1 dag",
      no_manual: "Batteri lades ikke i nat - valgt manuelt",
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
    planEnded: (time, target, soc) =>
      `Ladeplanen sluttede kl. ${time} - target ${target} % blev ikke nået${soc !== null ? ` (SoC ${soc} %)` : ""}`,
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
    chargeWithPvCovers: (pct, kwh, hours) =>
      `Lades til ${pct} % fra nettet - sammen med solen i morgen (forventet ${kwh} overskud) rækker det til kl. ${hours}`,
    dayHours: (kwh, hours, reserve, floor) =>
      floor < reserve
        ? `Heraf ca. ${kwh} til dagtimerne kl. ${hours} - ned til ${floor} %, solen fylder op til ${reserve} % til aftenen`
        : `Heraf ca. ${kwh} til dagtimerne kl. ${hours} - batteriet gemmer ${reserve} % til aftenen`,
    dayPlanToday: (hours, reserve) =>
      `I dag dækker batteriet huset kl. ${hours} - ned til ${reserve} %, resten gemmes til aftenen`,
    dayPlanFromToday: "Beregnet ud fra dagens priser og hvad netstrømmen i batteriet kostede",
    saleTonight: (kwh, hours, profit) =>
      `Plus ca. ${kwh} til salg kl. ${hours} - forventet gevinst ca. ${profit} kr`,
    saleToday: (hours) => `I dag sælger batteriet kl. ${hours}`,
    pvRelease: (hours, floor) => `I dag bruger batteriet solstrøm kl. ${hours} - ned til ${floor} %`,
    pvReleaseNote: (kwh, instead) => `Ca. ${kwh}, der ellers skulle bruges kl. ${instead}`,
    pvKept: (kwh) =>
      `Solstrøm over aftenreserven (ca. ${kwh}) gemmes til senere - dagens resterende timer er ikke dyrere`,
    pvNight: (hours) => `${hours} i nat`,
    movedToToday: (today, from, when) => `Lader kl. ${today} i dag i stedet for kl. ${from} ${when}`,
    tonight: "i nat",
    tomorrow: "i morgen",
    and: " og ",
    sellingNow: "Batteriet sælger til nettet nu",
    pvFills: (target) => `Solen i morgen forventes at fylde op til target ${target} %`,
    decimal: ",",
    notHigher: (price) =>
      `Lades ikke højere - kun timer med pris over ${price} kr/kWh (ladepris + prisforskel) er værd at lade til`,
    surplusCoversWorth: (hours, kwh) =>
      `Forventet sol-overskud i morgen (${kwh}, forsigtigt skøn) dækker kl. ${hours}, de timer natladningen skulle dække`,
    manualTitle: "Manuel betjening af batteri",
    // Issue #112: overskriften, mens de manuelle knapper er låst op.
    manualActiveTitle: "Manuel betjening aktiv",
    // Issue #110: overskriften, mens de manuelle knapper er låst og
    // automatikken kører.
    autoTitle: "Automatisk drift aktiv",
    stop: "Afbryd ladning",
    postpone: "Udskyd ladning 1 dag",
    cancelPostpone: "Annuller udskydning",
    disableAuto: "Deaktiver automatisk ladning",
    enableAuto: "Aktiver automatisk ladning",
    unlockHold: "🔒 Lås op for manuel betjening - hold 2 sek.",
    holdHint: "Hold knappen inde i 2 sekunder",
    // Issue #117: de altid synlige knapper holdes også i 2 sek.
    holdLabel: (text) => `${text} - hold 2 sek.`,
    relock: "🔓 Lås",
    relockToAuto: "🔒 Lås og gå tilbage til automatisk drift",
    // Issue #112: én "Lad nu" med effekt og target-SoC.
    chargeNowButton: "Lad nu",
    chargePower: (level) => `Effekt: ${level}`,
    chargeUntil: (pct) => `Lad til: ${pct} %`,
    startCharge: "Start ladning",
    soc: "Batteri SoC",
    missing: "PwMngt's batteri-entities blev ikke fundet - er Solar PV Plant sat op?",
    // Issue #105: nattens ladeprocent, valgt manuelt.
    manualChosen: (pct) => `Valgt manuelt: lades til ${pct} % i nat`,
    manualNone: "Valgt manuelt: ingen netladning i nat",
    autoWould: (pct) =>
      pct > 0
        ? `Automatikken ville lade til ${pct} % - den tager over igen i morgen`
        : "Automatikken ville ikke lade - den tager over igen i morgen",
    editTonight: "Vælg nattens ladning",
    // I morgen er en mørk dag: automatikken lader op til 100 %.
    darkDay: "Meget lidt sol i morgen - derfor op til 100 % i stedet for 95 %",
    editorNone: "Ingen netladning",
    editorAuto: (label) => `Automatik: ${label}`,
    quickNone: "Ingen ladning",
    quickAuto: (label) => `Automatik (${label})`,
    quickFull: (pct) => `Fuld (${pct} %)`,
    useTonight: "Gem og brug i nat",
    resetAuto: "Nulstil til automatik",
    close: "Afbryd",
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
  no_manual: "warn",
  not_calculated: "bad",
};

// Issue #105: the statuses of a decided coming night -- its charge target
// can be chosen by hand.
const PWM_BATTERY_COMING_NIGHT_STATUSES = [
  "yes_tomorrow",
  "no_price",
  "no_surplus",
  "no_charge_needed",
  "no_deferred",
  "no_manual",
];

/** The local date as YYYY-MM-DD (as battery_charge_decision.py writes it). */
function pwmLocalDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate()
  ).padStart(2, "0")}`;
}

// Issue #110: how long "Manual control" is held, and how long the
// guarded buttons stay visible.
const PWM_MANUAL_HOLD_MS = 2000;
const PWM_MANUAL_UNLOCK_MS = 60000;

/** Issue #92: kW with one decimal (none when whole), e.g. 11.3 / 15. */
function pwmKwText(kw, separator) {
  const rounded = Math.round(kw * 10) / 10;
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return text.replace(".", separator);
}

const PWM_BATTERY_CARD_STYLE = `
  ha-card {
    padding: 16px;
  }
  .pwm-group {
    background: var(--secondary-background-color);
    border: 1px solid var(--divider-color);
    border-radius: 12px;
    padding: 10px;
    margin-bottom: 10px;
  }
  .pwm-group:last-child,
  .pwm-group > :last-child {
    margin-bottom: 0;
  }
  .pwm-group .pwm-section-title {
    margin-top: 0;
  }
  /* The buttons sit on the group's own background -- set them apart. */
  .pwm-group .pwm-buttons button {
    background: color-mix(in srgb, var(--primary-text-color) 6%, var(--secondary-background-color));
  }
  .pwm-banner {
    padding: 12px;
    border-radius: 8px;
    margin-bottom: 8px;
    color: var(--text-primary-color, #fff);
    font-size: 17px;
    font-weight: 500;
    text-align: center;
  }
  .pwm-banner.ok { background: var(--success-color, #43a047); }
  .pwm-banner.bad { background: var(--error-color, #db4437); }
  .pwm-banner.warn { background: var(--warning-color, #ffa600); }
  .pwm-banner.info { background: var(--info-color, #039be5); }
  /* A banner's own explanation sits inside it, so it can't be read as
     belonging to the next banner (Claus, 2026-11-20). */
  .pwm-banner-note {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid color-mix(in srgb, currentColor 35%, transparent);
    font-size: 14px;
    font-weight: 400;
    opacity: 0.9;
  }
  .pwm-note {
    font-size: 15px;
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
  /* Percentages in banners and notes stand out (Claus, 2026-11-15). */
  .pwm-pct {
    font-weight: 700;
    font-size: 1.12em;
    /* "47 %" must not break between the number and the sign. */
    white-space: nowrap;
  }
  .pwm-note .pwm-pct {
    color: var(--primary-text-color);
  }
  /* Issue #105: the night's charge target opens its editor. */
  .pwm-pct-edit {
    cursor: pointer;
    text-decoration: underline dotted;
    text-underline-offset: 3px;
    white-space: nowrap;
  }
  .pwm-pct-edit ha-icon,
  .pwm-edit-line ha-icon {
    --mdc-icon-size: 16px;
    margin-left: 3px;
    vertical-align: -2px;
  }
  .pwm-edit-line {
    cursor: pointer;
  }
  .pwm-edit-line:hover {
    filter: brightness(1.08);
  }
  .pwm-edit-link {
    display: block;
    margin: 0 auto 8px;
    font: inherit;
    font-size: 14px;
    padding: 6px 12px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: transparent;
    color: var(--primary-color);
    cursor: pointer;
  }
  .pwm-editor {
    border-top: 1px solid var(--divider-color);
    margin-top: 8px;
    padding-top: 10px;
  }
  .pwm-editor + .pwm-buttons {
    margin-top: 8px;
  }
  .pwm-editor-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }
  .pwm-editor-row input[type="range"] {
    flex: 1;
    min-width: 0;
    accent-color: var(--primary-color);
  }
  .pwm-editor-row button {
    font: inherit;
    font-size: 18px;
    width: 40px;
    height: 40px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: color-mix(in srgb, var(--primary-text-color) 6%, var(--secondary-background-color));
    color: var(--primary-text-color);
    cursor: pointer;
  }
  .pwm-editor-value.pwm-editor-label {
    min-width: 140px;
    font-size: 16px;
  }
  .pwm-group .pwm-buttons button.active {
    border-color: var(--primary-color);
  }
  .pwm-editor-value {
    min-width: 76px;
    text-align: right;
    font-size: 20px;
    font-weight: 500;
    color: var(--primary-text-color);
  }
  .pwm-editor-quick {
    grid-template-columns: repeat(3, 1fr);
    margin-bottom: 8px;
  }
  .pwm-editor-actions {
    display: flex;
  }
  .pwm-editor-actions button {
    flex: 2;
  }
  .pwm-editor-actions button:last-child {
    flex: 1;
  }
  .pwm-group .pwm-buttons button.primary {
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
    border-color: var(--primary-color);
  }
  /* Issue #110: "Manual control" is held for 2 s to show the buttons. */
  .pwm-buttons .pwm-wide {
    grid-column: 1 / -1;
  }
  .pwm-buttons button.pwm-hold {
    position: relative;
    overflow: hidden;
    touch-action: pan-y;
    user-select: none;
    -webkit-user-select: none;
    -webkit-touch-callout: none;
  }
  .pwm-hold-fill {
    position: absolute;
    inset: 0 auto 0 0;
    width: 0;
    background: var(--primary-color);
    opacity: 0.3;
    pointer-events: none;
  }
  .pwm-hold.holding .pwm-hold-fill {
    width: 100%;
    transition: width ${PWM_MANUAL_HOLD_MS}ms linear;
  }
  .pwm-hold-label {
    position: relative;
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
 *   never read pv_battery_mode), so the banner says so.
 * - Issue #110: "Charge now", "Postpone" and "Disable automatic charging"
 *   are hidden until "Manual control" has been held for 2 s, and lock
 *   again after one use or 60 s. Stop / cancel postponement / enable
 *   automatic are always shown -- they lead back to normal operation.
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
    // Issue #105: leave the night editor alone while it is open -- a
    // re-render would reset the slider under the user's finger. Same for
    // the "Manual control" button while it is being held (#110).
    if (this._editing() || this._holding) return;
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
      size: pwmFindEntityId(this._hass, "pv", "select", "battery_size"),
      // Issue #92: what the charge_now_* percentages are a share of.
      maxCharge: pwmFindEntityId(this._hass, "pv", "select", "battery_max_charge_effect"),
      // Issues #59/#61: today's day plan (day_plan_* attributes).
      data: pwmFindEntityId(this._hass, "pv", "sensor", "battery_data"),
      // Issue #105: the night editor's lowest value (= no grid charge).
      minSoc: pwmFindEntityId(this._hass, "pv", "select", "battery_min_soc"),
    };
    this._signature = undefined;
  }

  _state(name) {
    const entityId = this._ids[name];
    return entityId ? this._hass.states[entityId] : undefined;
  }

  /**
   * Issue #92: a charge_now_* level as power -- pct of Battery max charge
   * effect (W) in kW, as battery_charge_level.py reads it; "75 % effekt"
   * while that isn't known. A percentage read as a SoC to charge to.
   */
  _chargeLevel(t, pct) {
    const state = this._state("maxCharge");
    const maxW = state ? Number(state.state) : NaN;
    if (!Number.isFinite(maxW) || maxW <= 0) return t.pctPower(pct);
    return t.kw((maxW * pct) / 100 / 1000);
  }

  /** Only re-render when something the card shows has changed. */
  _stateSignature() {
    const parts = ["command", "mode", "status", "soc", "target", "maxCharge", "minSoc"].map((name) => {
      const state = this._state(name);
      return state ? `${state.state}|${JSON.stringify(state.attributes)}` : "-";
    });
    // Only the day plan from battery_data -- its other attributes change
    // with every SoC point.
    const data = this._state("data");
    parts.push(
      data
        ? JSON.stringify([
            data.attributes.day_plan_hours,
            data.attributes.day_plan_reserve,
            data.attributes.day_plan_source,
            data.attributes.sale_hours,
            data.attributes.selling,
            // Issue #71.
            data.attributes.pv_release_hours,
            data.attributes.pv_release_floor,
            data.attributes.pv_release_extra_kwh,
            data.attributes.pv_release_with_tomorrow,
          ])
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

    // The card's four parts, each in its own rounded group: the status, the
    // night charge, today's plan and the manual control. Empty groups are
    // left out.
    const statusGroup = this._group();
    const nightGroup = this._group();
    const todayGroup = this._group();
    const manualGroup = this._group();

    // Issue #37: the charge watchdog -- the battery didn't follow a charge
    // command.
    const watchdog = status.attributes.charge_watchdog;
    if (watchdog && watchdog.failed) {
      statusGroup.appendChild(this._banner("bad", t.chargeNotResponding));
    } else if (watchdog && watchdog.retries > 0) {
      statusGroup.appendChild(this._note(t.chargeRetry(watchdog.retries, watchdog.max_retries)));
    }

    // Banners -- same order and conditions as the Node-RED card.
    let statusBanner = null;
    if (!isAuto) {
      statusGroup.appendChild(this._banner("bad", t.manual));
    } else if (chargingPct === null && this._planEnded(status) !== null) {
      // Issue #103: today's planned hours are over but the target wasn't
      // reached -- the plan stays "yes_today" until 16:55 (battery_data
      // still uses it for the day), so say it has ended.
      const end = this._planEnded(status);
      const target = Math.round(Number(status.attributes.charge_target));
      const socState = this._state("soc");
      const soc = socState && Number.isFinite(Number(socState.state)) ? Math.round(Number(socState.state)) : null;
      statusGroup.appendChild(
        this._banner("warn", t.planEnded(`${String(end).padStart(2, "0")}:00`, target, soc))
      );
    } else if ((chargingPct === null || !planned) && !this._manualNight(status)) {
      // Issue #105: a manual choice has its own banner below.
      const key =
        status.state === "not_calculated" && pwmBatteryRecheckHours()
          ? "not_calculated_recheck"
          : status.state;
      const text = t.status[key];
      if (text) {
        statusBanner = this._banner(PWM_BATTERY_STATUS_TONE[status.state], text);
        statusGroup.appendChild(statusBanner);
      }
    }
    // Issue #43: what the decision is based on, and how far tonight's
    // charge goes (a partial charge leaves the rest to tomorrow's PV).
    if (isAuto) {
      // Issue #105: the night's charge target can be chosen by hand --
      // tapping its percentage opens the editor, or, when the night isn't
      // charged, tapping the "not charged tonight" line.
      const editable = this._nightEditable(status);
      const manual = this._manualNight(status);
      let editAttached = false;
      if (manual) {
        const banner = this._banner(
          "warn",
          manual.target > manual.minSoc ? t.manualChosen(Math.round(manual.target)) : t.manualNone
        );
        if (editable) editAttached = this._attachEditChip(banner) || this._attachEditLine(banner);
        statusGroup.appendChild(banner);
        statusGroup.appendChild(this._note(t.autoWould(Math.round(manual.auto))));
      } else {
        this._decisionText(t, status).forEach((decision, index) => {
          const note = this._note(decision);
          // Only a planned charge's first line starts with the charge
          // target -- a "not charged" reason can name the nightly target.
          if (editable && planned && index === 0) editAttached = this._attachEditChip(note);
          statusGroup.appendChild(note);
        });
        // Claus 2026-11-20: before a dark day the plan may charge to 100 %.
        if (planned && Number(status.attributes.charge_ceiling) === 100 && Number(status.attributes.charge_target) > 95) {
          statusGroup.appendChild(this._note(t.darkDay));
        }
      }
      if (editable && !editAttached && statusBanner) {
        editAttached = this._attachEditLine(statusBanner);
      }
      if (editable && !editAttached && !(this._editor && this._editor.open)) {
        const link = this._button(t.editTonight, () => this._openEditor(status));
        link.className = "pwm-edit-link";
        statusGroup.appendChild(link);
      }
      if (editable && this._editor && this._editor.open) {
        statusGroup.appendChild(this._nightEditor(t, status, manual));
      }
    }

    if (manualPct !== null) {
      if (chargingPct === null) {
        statusGroup.appendChild(this._banner("warn", t.pausedByFuse(this._chargeLevel(t, manualPct))));
      } else {
        statusGroup.appendChild(this._banner("ok", t.manualCharging(this._chargeLevel(t, chargingPct))));
      }
      statusGroup.appendChild(
        this._note(chargingPct !== null && chargingPct < manualPct ? t.reducedByFuse(this._chargeLevel(t, manualPct)) : t.manualRuns(Math.round(this._manualChargeTarget())))
      );
    } else if (chargingPct !== null) {
      statusGroup.appendChild(this._banner("ok", t.charging(this._chargeLevel(t, chargingPct))));
      if (isAuto && status.state === "yes_today") {
        statusGroup.appendChild(this._note(t.autoControls));
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
        nightGroup,
        t,
        plannedHours,
        status.attributes.skipped_hours || [],
        status.attributes.extended_hours || [],
        null
      );
      // Issue #75: planned hours moved to today's cheaper hours before 17.
      const movedNote = this._movedToToday(t, status);
      if (movedNote) nightGroup.appendChild(this._note(movedNote));
    }

    // How tonight's plan ended, until a new decision is made (issue #24).
    const lastCharge = status.attributes.last_charge;
    if (isAuto && !planned && lastCharge && lastCharge.finished) {
      const finished = new Date(lastCharge.finished);
      const time = `${String(finished.getHours()).padStart(2, "0")}:${String(
        finished.getMinutes()).padStart(2, "0")}`;
      const target = Number.isFinite(Number(lastCharge.target)) && lastCharge.target !== null
        ? Math.round(Number(lastCharge.target)) : null;
      nightGroup.appendChild(
        lastCharge.target_reached
          ? this._banner("ok", t.lastChargeDone(time, target))
          : this._banner("warn", t.lastChargeStopped(time))
      );
      this._hourTiles(
        nightGroup,
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
      todayGroup.appendChild(
        this._banner(
          "ok",
          t.dayPlanToday(pwmHourRanges(dayPlan.hours), dayPlan.reserve),
          dayPlan.source === "today" ? t.dayPlanFromToday : null
        )
      );
    }
    // Issue #71: the PV energy above the evening reserve -- used in
    // today's dearest hours, or why it is kept.
    if (isAuto) {
      for (const node of this._pvReleaseToday(t)) todayGroup.appendChild(node);
    }
    // Issue #64: selling now, or today's sale hours while some are left.
    const sale = this._saleToday();
    if (isAuto && sale) {
      todayGroup.appendChild(
        this._banner("ok", sale.selling ? t.sellingNow : t.saleToday(pwmHourRanges(sale.hours)))
      );
    }

    // Manual control.
    const title = document.createElement("div");
    title.className = "pwm-section-title";
    // Issue #110: while locked with the automation running, say so.
    // Issue #112: unlocked, the manual control is active.
    title.textContent = this._isUnlocked() ? t.manualActiveTitle : isAuto ? t.autoTitle : t.manualTitle;
    manualGroup.appendChild(title);

    // Issue #110: the buttons that take over from the automation sit
    // behind a 2 s hold on "Manual control", so they can't be hit by
    // accident (e.g. while scrolling on a phone). The ones that lead back
    // to normal operation are always shown.
    const buttons = document.createElement("div");
    buttons.className = "pwm-buttons";
    const postponeButton = () =>
      this._hass.callService("button", "press", { entity_id: this._ids.postpone });
    const deferred = status.state === "no_deferred";
    // Issue #117: they are held for 2 s too, so a stray tap can't stop a
    // charge -- a short tap shows the hint, as on "Manual control".
    const held = (text, action) => this._holdButton(t.holdLabel(text), t.holdHint, action, false);
    if (chargingPct !== null || manualPct !== null) {
      buttons.appendChild(held(t.stop, () => this._setCommand("internal")));
    }
    if (this._ids.postpone && deferred) {
      buttons.appendChild(held(t.cancelPostpone, postponeButton));
    }
    if (!isAuto) {
      buttons.appendChild(held(t.enableAuto, () => this._setMode("auto")));
    }

    let relock = null;
    if (this._isUnlocked()) {
      // A button that locks again also closes the "Charge now" sliders --
      // the card doesn't redraw while they are open, so the lock would
      // otherwise not show until "Close".
      const guarded = (text, action) =>
        this._button(text, () => {
          action();
          this._chargeEditor = { open: false };
          this._lockManual();
        });
      // Issue #112: one "Charge now" -- it opens the power and target SoC
      // sliders below.
      const chargeNow = this._button(t.chargeNowButton, () => this._toggleChargeEditor(manualPct));
      if (this._chargeEditor && this._chargeEditor.open) chargeNow.classList.add("active");
      buttons.appendChild(chargeNow);
      if (this._ids.postpone && !deferred) {
        buttons.appendChild(guarded(t.postpone, postponeButton));
      }
      if (isAuto) {
        buttons.appendChild(guarded(t.disableAuto, () => this._setMode("manual")));
      }
      relock = guarded(isAuto ? t.relockToAuto : t.relock, () => {});
      relock.className = "pwm-wide";
    } else {
      buttons.appendChild(this._holdButton(t.unlockHold, t.holdHint, () => this._unlockManual()));
    }
    manualGroup.appendChild(buttons);
    // Issue #112: the "Charge now" sliders right under its button, above
    // the lock.
    if (relock && this._chargeEditor && this._chargeEditor.open) {
      manualGroup.appendChild(this._chargeNowEditor(t));
      const lockRow = document.createElement("div");
      lockRow.className = "pwm-buttons";
      lockRow.appendChild(relock);
      manualGroup.appendChild(lockRow);
    } else if (relock) {
      buttons.appendChild(relock);
    }

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
      manualGroup.appendChild(wrap);
    }

    for (const group of [statusGroup, nightGroup, todayGroup, manualGroup]) {
      if (group.childElementCount) content.appendChild(group);
    }
  }

  /**
   * Issue #105: whether the night's charge target can be chosen now --
   * mirrors manual_night() in data/battery_charge_decision.py: the running
   * night (before 06) while its plan stands or was chosen by hand, or the
   * coming night once it has been decided (tomorrow's prices are out after
   * ~13). The action itself refuses anything else.
   */
  _nightEditable(status) {
    const now = new Date();
    const hour = now.getHours();
    if (hour < 6) {
      return status.state === "yes_today" || status.attributes.manual_night === pwmLocalDate(now);
    }
    if (hour < 12) return false;
    return PWM_BATTERY_COMING_NIGHT_STATUSES.includes(status.state);
  }

  /**
   * Issue #105: {target, auto, minSoc} while the night's charge target is
   * chosen by hand, else null.
   */
  _manualNight(status) {
    const a = status.attributes;
    const target = Number(a.manual_target);
    if (a.manual_target === null || a.manual_target === undefined || !Number.isFinite(target) || !a.manual_night) {
      return null;
    }
    return { target, auto: Number(a.auto_charge_target) || 0, minSoc: this._minSoc() };
  }

  _minSoc() {
    const state = this._state("minSoc");
    const value = state ? Number(state.state) : NaN;
    return Number.isFinite(value) ? value : 0;
  }

  /** What the automation charges to tonight (%), 0 when it doesn't. */
  _autoTarget(status) {
    const manual = this._manualNight(status);
    if (manual) return manual.auto;
    const planned = status.state === "yes_today" || status.state === "yes_tomorrow";
    const target = Number(status.attributes.charge_target);
    return planned && Number.isFinite(target) ? target : 0;
  }

  /** Make the first percentage in el open the editor; false when it has none. */
  _attachEditChip(el) {
    const pct = el.querySelector(".pwm-pct");
    if (!pct) return false;
    pct.classList.add("pwm-pct-edit");
    pct.setAttribute("role", "button");
    pct.tabIndex = 0;
    const icon = document.createElement("ha-icon");
    icon.setAttribute("icon", "mdi:pencil");
    pct.appendChild(icon);
    this._makeEditTrigger(pct);
    return true;
  }

  /** Make the whole line el open the editor, with a pencil after its text. */
  _attachEditLine(el) {
    el.classList.add("pwm-edit-line");
    el.setAttribute("role", "button");
    el.tabIndex = 0;
    const icon = document.createElement("ha-icon");
    icon.setAttribute("icon", "mdi:pencil");
    el.appendChild(icon);
    this._makeEditTrigger(el);
    return true;
  }

  _makeEditTrigger(el) {
    const open = () => this._openEditor(this._state("status"));
    el.addEventListener("click", open);
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        open();
      }
    });
  }

  _openEditor(status) {
    if (this._editor && this._editor.open) {
      this._closeEditor();
      return;
    }
    const manual = this._manualNight(status);
    this._editor = { open: true, value: manual ? manual.target : this._autoTarget(status) };
    this._signature = undefined;
    this._render();
  }

  _closeEditor() {
    this._editor = { open: false, value: null };
    this._signature = undefined;
    this._render();
  }

  /**
   * Issue #105: slider and -/+ (5 % steps, min SoC to 100 %), shortcuts,
   * "Use tonight" and -- while chosen by hand -- "Reset to automatic".
   * At the min SoC (the slider's bottom) it means no grid charge.
   */
  _nightEditor(t, status, manual) {
    const minSoc = this._minSoc();
    const sliderMin = Math.floor(minSoc / 5) * 5;
    // A manual target may always go to 100 % (Claus 2026-11-20).
    const max = 100;
    const auto = this._autoTarget(status);
    // The automation's own value stays exact (54 %); the slider and -/+
    // move in whole 5 % steps from it.
    const clamp = (value) => Math.min(max, Math.max(sliderMin, Math.round(value)));
    const label = (value) => (value <= minSoc ? t.editorNone : `${value} %`);
    let value = clamp(this._editor.value ?? auto);

    const panel = document.createElement("div");
    panel.className = "pwm-editor";

    const row = document.createElement("div");
    row.className = "pwm-editor-row";
    const minus = this._button("−", () => update(Math.ceil(value / 5) * 5 - 5));
    minus.setAttribute("aria-label", "-5 %");
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = String(sliderMin);
    slider.max = String(max);
    slider.step = "5";
    slider.value = String(value);
    slider.addEventListener("input", () => update(Number(slider.value)));
    const plus = this._button("+", () => update(Math.floor(value / 5) * 5 + 5));
    plus.setAttribute("aria-label", "+5 %");
    const shown = document.createElement("div");
    shown.className = "pwm-editor-value";
    row.append(minus, slider, plus, shown);

    const autoNote = document.createElement("div");
    autoNote.className = "pwm-note";
    autoNote.textContent = auto > minSoc ? t.editorAuto(`${Math.round(auto)} %`) : t.editorAuto(t.editorNone);

    const update = (next) => {
      value = clamp(next);
      this._editor.value = value;
      slider.value = String(value);
      shown.textContent = label(value);
    };
    update(value);

    const quick = document.createElement("div");
    quick.className = "pwm-buttons pwm-editor-quick";
    quick.append(
      this._button(t.quickNone, () => update(sliderMin)),
      this._button(t.quickAuto(auto > minSoc ? `${Math.round(auto)} %` : t.editorNone), () =>
        update(auto > minSoc ? auto : sliderMin)
      ),
      this._button(t.quickFull(max), () => update(max))
    );

    const actions = document.createElement("div");
    actions.className = "pwm-buttons pwm-editor-actions";
    const use = this._button(t.useTonight, () =>
      this._callNightService("set_night_charge_target", { target: value <= minSoc ? 0 : value })
    );
    use.className = "primary";
    actions.appendChild(use);
    if (manual) {
      actions.appendChild(this._button(t.resetAuto, () => this._callNightService("reset_night_charge_target", {})));
    }
    actions.appendChild(this._button(t.close, () => this._closeEditor()));

    panel.append(row, autoNote, quick, actions);
    return panel;
  }

  /** Issue #105/#112: whether one of the card's editors is open. */
  _editing() {
    return Boolean((this._editor && this._editor.open) || (this._chargeEditor && this._chargeEditor.open));
  }

  /** Issue #112: the SoC (%) the running manual charge stops at. */
  _manualChargeTarget() {
    const command = this._state("command");
    const target = command ? Number(command.attributes.manual_charge_target) : NaN;
    return Number.isFinite(target) ? target : 100;
  }

  _toggleChargeEditor(manualPct) {
    if (this._chargeEditor && this._chargeEditor.open) {
      this._closeChargeEditor();
      return;
    }
    this._chargeEditor = {
      open: true,
      // Claus 2026-11-26: 75 % by default, not 100 %.
      power: manualPct !== null ? manualPct : 75,
      soc: manualPct !== null ? this._manualChargeTarget() : 100,
    };
    this._signature = undefined;
    this._render();
  }

  _closeChargeEditor() {
    this._chargeEditor = { open: false };
    this._rerender();
  }

  /**
   * Issue #112: "Charge now" -- power in 25 % steps of Battery max charge
   * effect (shown in kW) and the SoC it stops at, 1 % steps from the min
   * SoC to 100 % (default 100 %).
   */
  _chargeNowEditor(t) {
    const editor = this._chargeEditor;
    const minSoc = Math.max(1, Math.round(this._minSoc()));
    const panel = document.createElement("div");
    panel.className = "pwm-editor";

    const sliderRow = (min, max, step, value, onInput) => {
      const row = document.createElement("div");
      row.className = "pwm-editor-row";
      const slider = document.createElement("input");
      slider.type = "range";
      slider.min = String(min);
      slider.max = String(max);
      slider.step = String(step);
      slider.value = String(value);
      const shown = document.createElement("div");
      shown.className = "pwm-editor-value pwm-editor-label";
      slider.addEventListener("input", () => onInput(Number(slider.value), shown));
      row.append(slider, shown);
      onInput(value, shown);
      return row;
    };

    const power = sliderRow(25, 100, 25, editor.power, (value, shown) => {
      editor.power = value;
      shown.textContent = t.chargePower(this._chargeLevel(t, value));
    });
    editor.soc = Math.min(100, Math.max(minSoc, Math.round(editor.soc)));
    const soc = sliderRow(minSoc, 100, 1, editor.soc, (value, shown) => {
      editor.soc = value;
      shown.textContent = t.chargeUntil(value);
    });

    const actions = document.createElement("div");
    actions.className = "pwm-buttons pwm-editor-actions";
    const start = this._button(t.startCharge, () => {
      this._chargeEditor = { open: false };
      Promise.resolve(
        this._hass.callService("pwmngt", "charge_now", { power: editor.power, target_soc: editor.soc })
      ).catch((err) => window.alert((err && err.message) || String(err)));
      this._lockManual();
    });
    start.className = "primary";
    actions.append(start, this._button(t.close, () => this._closeChargeEditor()));

    panel.append(power, soc, actions);
    return panel;
  }

  _callNightService(service, data) {
    this._editor = { open: false, value: null };
    Promise.resolve(this._hass.callService("pwmngt", service, data)).catch((err) =>
      window.alert((err && err.message) || String(err))
    );
    this._signature = undefined;
    this._render();
  }

  _group() {
    const group = document.createElement("div");
    group.className = "pwm-group";
    return group;
  }

  _banner(tone, text, noteText = null) {
    const banner = document.createElement("div");
    banner.className = `pwm-banner ${tone || "info"}`;
    this._fillWithPercentages(banner, text);
    if (noteText) {
      const note = document.createElement("div");
      note.className = "pwm-banner-note";
      this._fillWithPercentages(note, noteText);
      banner.appendChild(note);
    }
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
        // Issue #50: the grid charge covers these hours -- together with
        // tomorrow's expected PV surplus when there is one (#43 takes the
        // surplus off the charge, so the grid share alone is not enough).
        const withPv = surplus !== null && surplus >= 0.05;
        lines.push(withPv ? t.chargeWithPvCovers(pct, kwh, hours) : t.chargeCovers(pct, hours));
        // Issue #59: and the expensive daytime hours, down to the evening
        // reserve less what the PV surplus refills before it (#70).
        const dayHours = Array.isArray(status.attributes.day_hours) ? status.attributes.day_hours : [];
        const dayKwh = number(status.attributes.day_kwh);
        const reserve = number(status.attributes.evening_reserve_soc);
        const floor = number(status.attributes.day_floor_soc) ?? reserve;
        if (dayHours.length && dayKwh !== null && reserve !== null) {
          lines.push(
            t.dayHours(t.kwh(dayKwh), pwmHourRanges(dayHours), Math.round(reserve), Math.round(floor))
          );
        }
        // Only when the grid charge plus the PV surplus actually reaches the
        // target -- the evening-only charge usually stops well below it.
        const sizeState = this._state("size");
        const size = sizeState ? number(sizeState.state) : null;
        if (target !== null && chargeTarget < target) {
          const pvPct = withPv && size ? (surplus / size) * 100 : 0;
          const breakEven = number(status.attributes.break_even_price);
          if (chargeTarget + pvPct >= target) {
            lines.push(t.pvFills(Math.round(target)));
          } else if (breakEven !== null) {
            // Why the battery is not charged further: only hours priced at
            // or above the break-even pay the charge price plus the price
            // difference (#50).
            lines.push(t.notHigher(breakEven.toFixed(2).replace(".", t.decimal)));
          }
        }
        this._pushSaleLine(t, status, lines);
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
      this._pushSaleLine(t, status, lines);
      return lines;
    }
    if (status.state === "no_surplus" && kwh !== null) {
      if (hours) return [t.surplusCoversWorth(hours, kwh)];
      if (target !== null) return [t.surplusCovers(Math.round(target), kwh)];
    }
    return [];
  }

  /**
   * Issue #103: the hour (0-23) today's charge plan ended at, while it is
   * still "yes_today" with a target -- or null while a planned hour is
   * still ahead or running. A night plan (hours 0-5) can be extended into
   * the night's unplanned hours (#22), so it counts as over only from 06.
   */
  _planEnded(status) {
    if (status.state !== "yes_today") return null;
    if (!Number.isFinite(Number(status.attributes.charge_target))) return null;
    const hours = (status.attributes.extended_hours || []).slice();
    for (let hour = 0; hour < 24; hour++) {
      if (status.attributes[`hour_${hour}`] === true) hours.push(hour);
    }
    if (!hours.length) return null;
    const end = Math.max(...hours) + 1;
    const now = new Date().getHours();
    if (now < end || (end <= 6 && now < 6)) return null;
    return end;
  }

  /**
   * Issue #75: "Charges at 14 today instead of 2 tonight" while one of
   * today's charge hours is still ahead (or running), else null.
   */
  _movedToToday(t, status) {
    const a = status.attributes;
    const today = Array.isArray(a.today_charge_hours) ? a.today_charge_hours : [];
    const from = Array.isArray(a.moved_from_hours) ? a.moved_from_hours : [];
    const now = new Date();
    const date = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
      now.getDate()).padStart(2, "0")}`;
    if (status.state !== "yes_tomorrow" || a.today_charge_date !== date || !from.length) return null;
    if (!today.some((hour) => hour >= now.getHours())) return null;
    const when = from.every((hour) => hour < 6) ? t.tonight : t.tomorrow;
    return t.movedToToday(pwmHourRanges(today), pwmHourRanges(from), when);
  }

  /** Issue #64: the night charge's sale hours, kWh and expected profit. */
  _pushSaleLine(t, status, lines) {
    const hours = Array.isArray(status.attributes.sale_hours) ? status.attributes.sale_hours : [];
    const kwh = Number(status.attributes.sale_kwh);
    const profit = Number(status.attributes.sale_profit);
    if (!hours.length || !Number.isFinite(kwh) || !Number.isFinite(profit)) return;
    lines.push(t.saleTonight(t.kwh(kwh), pwmHourRanges(hours), Math.round(profit)));
  }

  /**
   * Issue #64: battery_data's sales today -- {hours (all of today's --
   * issue #81: only the remaining ones read as if it starts later),
   * selling} -- or null once they are over.
   */
  _saleToday() {
    const data = this._state("data");
    if (!data) return null;
    const hours = Array.isArray(data.attributes.sale_hours) ? data.attributes.sale_hours : [];
    const now = new Date().getHours();
    const all = hours.map(Number);
    const selling = data.attributes.selling === true;
    if (!all.some((hour) => hour >= now) && !selling) return null;
    return { hours: all, selling };
  }

  /**
   * Issue #71: battery_data's plan for the PV energy above the evening
   * reserve, as banner/notes -- empty when there is nothing to say.
   */
  _pvReleaseToday(t) {
    const data = this._state("data");
    if (!data) return [];
    const a = data.attributes;
    const now = new Date().getHours();
    if (now >= 17) return [];
    const hours = (Array.isArray(a.pv_release_hours) ? a.pv_release_hours : []).map(Number);
    const nodes = [];
    // Issue #81: all of today's hours while some are left.
    if (hours.some((hour) => hour >= now)) {
      const instead = (Array.isArray(a.pv_release_instead_hours) ? a.pv_release_instead_hours : []).map(Number);
      const evening = instead.filter((hour) => hour >= 12);
      const night = instead.filter((hour) => hour < 12);
      const parts = [];
      if (evening.length) parts.push(pwmHourRanges(evening));
      if (night.length) parts.push(t.pvNight(pwmHourRanges(night)));
      nodes.push(
        this._banner(
          "ok",
          t.pvRelease(pwmHourRanges(hours), Math.round(Number(a.pv_release_floor))),
          t.pvReleaseNote(t.kwh(Number(a.pv_release_kwh) || 0), parts.join(t.and))
        )
      );
    } else if (Number(a.pv_release_extra_kwh) >= 1) {
      nodes.push(this._note(t.pvKept(t.kwh(Number(a.pv_release_extra_kwh)))));
    } else {
      return [];
    }
    return nodes;
  }

  /**
   * Issues #59/#61: battery_data's plan for today -- {hours (all of
   * today's -- issue #81: only the remaining ones read as if it starts
   * later), reserve, source} -- or null once they are over.
   */
  _dayPlanToday() {
    const data = this._state("data");
    if (!data) return null;
    const hours = Array.isArray(data.attributes.day_plan_hours) ? data.attributes.day_plan_hours : [];
    const reserve = Number(data.attributes.day_plan_reserve);
    const now = new Date().getHours();
    const all = hours.map(Number);
    if (!all.some((hour) => hour >= now) || !Number.isFinite(reserve)) return null;
    return { hours: all, reserve: Math.round(reserve), source: data.attributes.day_plan_source };
  }

  _note(text) {
    const note = document.createElement("div");
    note.className = "pwm-note";
    this._fillWithPercentages(note, text);
    return note;
  }

  /**
   * Put text into el with every percentage ("17 %", "59%") in its own
   * .pwm-pct span -- built from text nodes, never innerHTML, as the texts
   * carry values from Home Assistant states.
   */
  _fillWithPercentages(el, text) {
    el.textContent = "";
    String(text)
      .split(/(\d+(?:[.,]\d+)?\s?%)/)
      .forEach((part, index) => {
        if (!part) return;
        if (index % 2) {
          const pct = document.createElement("span");
          pct.className = "pwm-pct";
          pct.textContent = part;
          el.appendChild(pct);
        } else {
          el.appendChild(document.createTextNode(part));
        }
      });
  }

  _button(text, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", onClick);
    return button;
  }

  /**
   * Issue #110: a button that only fires after being held for
   * PWM_MANUAL_HOLD_MS. Letting go, moving the finger (the browser then
   * scrolls and cancels the pointer) or leaving the button aborts it.
   */
  _holdButton(text, hint, onHeld, wide = true) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = wide ? "pwm-hold pwm-wide" : "pwm-hold";
    const fill = document.createElement("span");
    fill.className = "pwm-hold-fill";
    const label = document.createElement("span");
    label.className = "pwm-hold-label";
    label.textContent = text;
    button.append(fill, label);

    let timer = null;
    let hintTimer = null;
    let startX = 0;
    let startY = 0;
    const cancel = () => {
      if (timer === null) return;
      clearTimeout(timer);
      timer = null;
      this._holding = false;
      button.classList.remove("holding");
    };
    // A short tap explains how the button works, then shows its label again.
    const release = () => {
      if (timer === null) return;
      cancel();
      label.textContent = hint;
      clearTimeout(hintTimer);
      hintTimer = setTimeout(() => (label.textContent = text), 3000);
    };
    const start = () => {
      if (timer !== null) return;
      // Keep HA's state updates from re-rendering the button mid-hold.
      this._holding = true;
      button.classList.add("holding");
      timer = setTimeout(() => {
        timer = null;
        this._holding = false;
        button.classList.remove("holding");
        onHeld();
      }, PWM_MANUAL_HOLD_MS);
    };
    button.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      startX = ev.clientX;
      startY = ev.clientY;
      start();
    });
    button.addEventListener("pointermove", (ev) => {
      if (Math.hypot(ev.clientX - startX, ev.clientY - startY) > 10) cancel();
    });
    button.addEventListener("pointerup", release);
    for (const name of ["pointercancel", "pointerleave"]) {
      button.addEventListener(name, cancel);
    }
    // A long press would otherwise open the phone's context menu.
    button.addEventListener("contextmenu", (ev) => ev.preventDefault());
    button.addEventListener("keydown", (ev) => {
      if ((ev.key === "Enter" || ev.key === " ") && !ev.repeat) {
        ev.preventDefault();
        start();
      }
    });
    button.addEventListener("keyup", release);
    button.addEventListener("blur", cancel);
    return button;
  }

  _isUnlocked() {
    return (this._unlockedUntil || 0) > Date.now();
  }

  /** Issue #110: show the guarded buttons; they lock again by themselves. */
  _unlockManual() {
    clearTimeout(this._relockTimer);
    this._unlockedUntil = Date.now() + PWM_MANUAL_UNLOCK_MS;
    this._relockTimer = setTimeout(() => this._lockManual(), PWM_MANUAL_UNLOCK_MS);
    this._rerender();
  }

  _lockManual() {
    clearTimeout(this._relockTimer);
    this._relockTimer = null;
    this._unlockedUntil = 0;
    this._rerender();
  }

  /** Render now, unless the night editor is open (it renders when closed). */
  _rerender() {
    this._signature = undefined;
    if (!this._hass || this._editing()) return;
    this._signature = this._stateSignature();
    this._render();
  }

  disconnectedCallback() {
    clearTimeout(this._relockTimer);
    this._relockTimer = null;
    this._unlockedUntil = 0;
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
    tips: {
      target: {
        title: "Target – tonight's goal",
        desc: "What the battery should hold at 17:00 to cover the evening and the night until 06:00 without buying power.",
        night: "Average use 17–06",
        pv: "Solar forecast 17–21 today",
        factor: "Battery factor (losses)",
        needed: "Needed from the battery",
        minimum: "Battery minimum SoC",
        result: "Target",
        ceiling: (pct) => `Capped at ${pct} %.`,
      },
      min: {
        title: "Min SoC – the lowest right now",
        desc: (pct) => `Right now the battery may discharge to ${pct} % at most. The limit moves hour by hour with the rule that wins.`,
        rule: "Rule now",
        held: (points, price) => `${points} % of the battery is grid power bought at ${price}.`,
      },
      pv: { title: "Charged from solar", desc: "The share of the battery (yellow in the ring) charged with solar power. Grey is what is below the counted share and has no known source." },
      grid: { title: "Charged from the grid", desc: "The share of the battery (blue in the ring) charged with bought grid power." },
      kwh: {
        title: "Usable energy",
        desc: (pct, size) => `What is in the battery above its minimum SoC of ${pct} % (battery ${size} kWh).`,
      },
      toFull: { title: "Time to full", desc: "When the battery is full at the current charging power." },
      toMin: { title: "Time to min SoC", desc: "When the battery reaches the min SoC at the current discharging power." },
      status: {
        title: "What PwMngt does",
        desc: "The command PwMngt gives the inverter right now (Battery command).",
        commands: {
          internal: "The inverter runs the battery itself – PwMngt does not step in.",
          block_charging: "Charging is blocked, e.g. while the battery sells to the grid.",
          hold_min_soc: "The battery is held at the min SoC and does not discharge further.",
          charge_now: "The battery charges from the grid at this % of its max charging power.",
          discharge_now: "The battery discharges to the grid at this % of its max power.",
        },
        hold: "After the dot: what the battery hold (Growatt) does, and its power in W.",
      },
      kr: (value) => `${value} kr.`,
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
    tips: {
      target: {
        title: "Target – nattens mål",
        desc: "Det, batteriet skal have kl. 17 for at dække aftenen og natten til kl. 06 uden at købe strøm.",
        night: "Gennemsnitsforbrug 17–06",
        pv: "Solprognose 17–21 i dag",
        factor: "Batterifaktor (tab)",
        needed: "Skal komme fra batteriet",
        minimum: "Batteriets minimum-SoC",
        result: "Target",
        ceiling: (pct) => `Begrænset til ${pct} %.`,
      },
      min: {
        title: "Min-SoC – det laveste lige nu",
        desc: (pct) => `Batteriet må lige nu højst aflade til ${pct} %. Grænsen flytter sig time for time efter den regel, der vinder.`,
        rule: "Regel nu",
        held: (points, price) => `${points} % af batteriet er netstrøm købt til ${price}.`,
      },
      pv: { title: "Ladet fra solceller", desc: "Den del af batteriet (gul i ringen), der er ladet med solstrøm. Gråt er den del under de talte andele, som der ikke kendes en kilde til." },
      grid: { title: "Ladet fra nettet", desc: "Den del af batteriet (blå i ringen), der er ladet med købt netstrøm." },
      kwh: {
        title: "Brugbar energi",
        desc: (pct, size) => `Det, der er i batteriet over dets minimum-SoC på ${pct} % (batteri på ${size} kWh).`,
      },
      toFull: { title: "Tid til fuldt", desc: "Hvornår batteriet er fuldt ved den nuværende ladeeffekt." },
      toMin: { title: "Tid til min-SoC", desc: "Hvornår batteriet når min-SoC ved den nuværende afladeeffekt." },
      status: {
        title: "Hvad PwMngt gør",
        desc: "Den kommando, PwMngt giver inverteren lige nu (Battery command).",
        commands: {
          internal: "Inverteren styrer selv batteriet – PwMngt griber ikke ind.",
          block_charging: "Ladning er blokeret, fx mens batteriet sælger til nettet.",
          hold_min_soc: "Batteriet holdes på min-SoC og aflader ikke længere ned.",
          charge_now: "Batteriet lades fra nettet med den % af sin max ladeeffekt.",
          discharge_now: "Batteriet aflader til nettet med den % af sin max effekt.",
        },
        hold: "Efter prikken: hvad batterilåsen (Growatt) gør, og dens effekt i W.",
      },
      kr: (value) => `${value} kr.`,
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
  .live { animation: pwm-pulse 1.8s ease-in-out infinite; }
  @keyframes pwm-pulse {
    0%, 100% { opacity: 0.35; filter: none; }
    50% { opacity: 1; filter: drop-shadow(0 0 4px currentColor) brightness(1.15); }
  }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; margin-top: 4px; }
  .chip { padding: 4px 10px; border-radius: 999px; font-size: 12px; white-space: nowrap;
    color: var(--primary-text-color); background: var(--secondary-background-color); }
  .chip.pv { background: rgba(255, 179, 0, 0.18); }
  .chip.grid { background: rgba(30, 136, 229, 0.18); }
  .chip.min { background: rgba(229, 57, 53, 0.15); }
  .chip.target { background: rgba(102, 187, 106, 0.18); }
  .status { margin-top: 8px; text-align: center; font-size: 12px; color: var(--secondary-text-color); }
  .pwm-warning { color: var(--error-color); font-size: 13px; }
  ha-card { position: relative; }
  .tip-target { cursor: help; }
  .tip { position: absolute; z-index: 5; box-sizing: border-box; width: max-content; max-width: min(280px, 92%);
    padding: 8px 10px; border-radius: 10px; font-size: 12px; line-height: 1.4; pointer-events: none;
    color: var(--primary-text-color); background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color); box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25); }
  .tip-title { font-weight: 600; margin-bottom: 2px; }
  .tip-desc { color: var(--secondary-text-color); margin-top: 2px; }
  .tip-rule { margin-top: 6px; }
  .tip table { border-collapse: collapse; margin-top: 6px; width: 100%; }
  .tip td { padding: 1px 0; }
  .tip td:last-child { text-align: right; padding-left: 12px; white-space: nowrap; }
`;

// The ring is 270 degrees, open at the bottom.
const PWM_RING_RADIUS = 80;
const PWM_RING_CIRCUMFERENCE = 2 * Math.PI * PWM_RING_RADIUS;
const PWM_RING_ARC = PWM_RING_CIRCUMFERENCE * 0.75;
// Below this (W) the battery counts as idle.
const PWM_RING_IDLE_W = 50;
// select.<pv>_battery_min_soc's default (const.DEFAULT_BATTERY_MIN_SOC).
const PWM_RING_DEFAULT_EMPTY_SOC = 5;

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
      this._listenForTips();
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

  disconnectedCallback() {
    this._hideTip();
  }

  _language() {
    const language = (this._hass.locale && this._hass.locale.language) || this._hass.language || "en";
    return String(language).split("-")[0];
  }

  _texts() {
    return PWM_RING_TEXTS[this._language()] || PWM_RING_TEXTS.en;
  }

  /**
   * Issue #136: an explanation of what is hovered (mouse) or tapped
   * (finger). Hover shows it while the pointer is there; a tap or click
   * pins it until the next tap, also outside the card.
   */
  _listenForTips() {
    const root = this.shadowRoot;
    const targetOf = (event) => event.target.closest && event.target.closest("[data-tip]");
    root.addEventListener("pointerover", (event) => {
      if (event.pointerType !== "mouse" || this._tipPinned) return;
      const target = targetOf(event);
      if (target) this._showTipFor(target, false);
    });
    root.addEventListener("pointerout", (event) => {
      if (event.pointerType !== "mouse" || this._tipPinned) return;
      const target = targetOf(event);
      if (target && !target.contains(event.relatedTarget)) this._hideTip();
    });
    root.addEventListener("click", (event) => {
      const target = targetOf(event);
      if (!target) {
        this._hideTip();
        return;
      }
      if (this._tipPinned && this._tipKey === target.dataset.tip) this._hideTip();
      else this._showTipFor(target, true);
    });
    this._outsideClick = (event) => {
      if (!event.composedPath().includes(this)) this._hideTip();
    };
  }

  /** The target's own place: the min and target marks have a chip too. */
  _showTipFor(target, pinned) {
    const key = target.dataset.tip;
    const index = [...this.shadowRoot.querySelectorAll(`[data-tip="${key}"]`)].indexOf(target);
    this._showTip(key, pinned, Math.max(index, 0));
  }

  _showTip(key, pinned, index = 0) {
    const card = this._card;
    const anchor = card && this.shadowRoot.querySelectorAll(`[data-tip="${key}"]`)[index];
    const html = anchor && this._tipHtml(key);
    if (!html) {
      this._hideTip();
      return;
    }
    this._tipKey = key;
    this._tipIndex = index;
    this._tipPinned = pinned;
    if (pinned) window.addEventListener("click", this._outsideClick, true);
    let tip = card.querySelector(".tip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "tip";
      card.appendChild(tip);
    }
    tip.innerHTML = html;
    // Below the hovered element, kept inside the card's width; above it
    // when there is no room below.
    const cardRect = card.getBoundingClientRect();
    const rect = anchor.getBoundingClientRect();
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    const left = rect.left + rect.width / 2 - cardRect.left - width / 2;
    tip.style.left = `${Math.max(4, Math.min(left, cardRect.width - width - 4))}px`;
    let top = rect.bottom - cardRect.top + 6;
    if (top + height > cardRect.height && rect.top - cardRect.top - height - 6 >= 0) {
      top = rect.top - cardRect.top - height - 6;
    }
    tip.style.top = `${top}px`;
  }

  _hideTip() {
    this._tipKey = undefined;
    this._tipPinned = false;
    if (this._outsideClick) window.removeEventListener("click", this._outsideClick, true);
    const tip = this._card && this._card.querySelector(".tip");
    if (tip) tip.remove();
  }

  /** The explanation's HTML -- only numbers and the card's own texts go in. */
  _tipHtml(key) {
    const t = this._texts().tips;
    const v = this._values || {};
    const head = (title, desc) => `<div class="tip-title">${title}</div><div class="tip-desc">${desc}</div>`;
    const row = (label, value) => `<tr><td>${label}</td><td>${value}</td></tr>`;
    const pct = (value) => `${Math.round(value)} %`;
    const kwh = (value) => `${pwmFormatDecimal(Number(value))} kWh`;
    switch (key) {
      case "target": {
        const basis = (this._state("target") || {}).attributes || {};
        let html = head(t.target.title, t.target.desc);
        if (Number.isFinite(Number(basis.night_consumption)) && Number(basis.battery_size) > 0) {
          const share = (Number(basis.target_kwh) / Number(basis.battery_size)) * 100;
          html +=
            `<table>` +
            row(t.target.night, kwh(basis.night_consumption)) +
            row(t.target.pv, `− ${kwh(basis.pv_17_21_today)}`) +
            row(t.target.factor, `× ${Number(basis.battery_factor).toFixed(2).replace(".", ",")}`) +
            row(t.target.needed, `${kwh(basis.target_kwh)} = ${pct(share)}`) +
            row(t.target.minimum, `+ ${pct(basis.battery_min_soc)}`) +
            row(`<b>${t.target.result}</b>`, `<b>${Number.isFinite(v.target) ? pct(v.target) : "–"}</b>`) +
            `</table>`;
          if (Number.isFinite(v.target) && v.target >= basis.target_ceiling) {
            html += `<div class="tip-desc">${t.target.ceiling(basis.target_ceiling)}</div>`;
          }
        }
        return html;
      }
      case "min": {
        if (!Number.isFinite(v.minSoc)) return "";
        let html = head(t.min.title, t.min.desc(Math.round(v.minSoc)));
        const entry = this._currentPlanEntry();
        if (entry) {
          const rules = (PWM_PLAN_TEXTS[this._language()] || PWM_PLAN_TEXTS.en).rules;
          const [name, desc] = rules[entry.rule] || [entry.rule, ""];
          html += `<div class="tip-rule">${t.min.rule}: <b>${name}</b></div><div class="tip-desc">${desc}</div>`;
          if (entry.rule === "night_protection" && entry.held_points !== undefined) {
            html += `<div class="tip-desc">${t.min.held(entry.held_points, this._kr(entry.cell_price_min))}</div>`;
          }
        }
        return html;
      }
      case "pv":
        return head(t.pv.title, t.pv.desc);
      case "grid":
        return head(t.grid.title, t.grid.desc);
      case "kwh":
        return Number.isFinite(v.size) ? head(t.kwh.title, t.kwh.desc(Math.round(v.empty), pwmFormatDecimal(v.size))) : "";
      case "toFull":
        return head(t.toFull.title, t.toFull.desc);
      case "toMin":
        return head(t.toMin.title, t.toMin.desc);
      case "status": {
        const command = this._state("command");
        if (!command) return "";
        const state = command.state.replace(/_\d+$/, "");
        let html = head(t.status.title, t.status.desc);
        const meaning = t.status.commands[state];
        if (meaning) html += `<div class="tip-desc">${meaning}</div>`;
        if (this._state("hold")) html += `<div class="tip-desc">${t.status.hold}</div>`;
        return html;
      }
      default:
        return "";
    }
  }

  /** The hour of Battery data's min_soc_plan (issue #122) that is now. */
  _currentPlanEntry() {
    const data = this._state("data");
    const plan = data && data.attributes.min_soc_plan;
    if (!Array.isArray(plan)) return null;
    const now = Date.now();
    return plan.find((entry) => {
      const start = new Date(entry.start).getTime();
      return start <= now && now < start + 3600000;
    }) || null;
  }

  _kr(value) {
    const text = Number(value).toLocaleString(this._language(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return this._texts().tips.kr(text);
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
      empty: pwmFindEntityId(this._hass, "pv", "select", "battery_min_soc"),
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
    // The SoC the battery can't be discharged below -- the kWh shown is
    // what's usable above it (same default as const.DEFAULT_BATTERY_MIN_SOC).
    const emptyRaw = this._number("empty");
    const empty = Number.isFinite(emptyRaw) ? emptyRaw : PWM_RING_DEFAULT_EMPTY_SOC;
    // Normalized by PwMngt (pv_properties.power_sign()): + = discharging.
    const power = this._number("power") || 0;

    card.appendChild(this._ring(t, soc, pv, grid, minSoc, target, size, power, empty));
    card.appendChild(this._chips(t, soc, pv, grid, minSoc, target, size, power));
    const status = this._statusText(t);
    if (status) {
      const line = document.createElement("div");
      line.className = "status tip-target";
      line.dataset.tip = "status";
      line.textContent = status;
      card.appendChild(line);
    }
    this._card = card;
    this._values = { soc, minSoc, target, size, empty };
    // A rebuild (new states) keeps an open explanation open.
    if (this._tipKey) this._showTip(this._tipKey, this._tipPinned, this._tipIndex);
  }

  _ring(t, soc, pv, grid, minSoc, target, size, power, empty) {
    const charging = power < -PWM_RING_IDLE_W;
    const discharging = power > PWM_RING_IDLE_W;
    const pvShare = Number.isFinite(pv) ? pv : 0;
    const gridShare = Number.isFinite(grid) ? grid : 0;
    // battery_data's floor below its counted cells has no source.
    const reserve = Math.max(0, soc - pvShare - gridShare);
    // Only charging pulses, and only the part being filled: grid power
    // while PwMngt's command is a grid charge (charge_now_X), else solar.
    const command = this._state("command");
    const gridCharging = charging && command && /^charge_now_/.test(command.state);
    const gridLive = gridCharging ? "live" : "";
    const pvLive = charging && !gridCharging ? "live" : "";
    const kw = pwmFormatKw(Math.abs(power));
    const powerColor = charging ? "#43a047" : discharging ? "#fb8c00" : "var(--secondary-text-color)";
    const powerText = charging ? t.charging(kw) : discharging ? t.discharging(kw) : t.idle;
    const usable = (Math.max(0, soc - empty) * size) / 100;
    const kwh = Number.isFinite(size) ? `${pwmFormatDecimal(usable)} kWh` : "";

    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <svg viewBox="0 0 200 175">
        ${pwmRingSegment(0, 100, "var(--divider-color)", "", "round")}
        ${pwmRingSegment(0, reserve, "#78909c")}
        ${pwmRingSegment(reserve, reserve + gridShare, "#1e88e5", gridLive)}
        ${pwmRingSegment(reserve + gridShare, soc, "#ffb300", pvLive)}
        ${pwmRingTick(minSoc, "#e53935", false, "min")}
        ${pwmRingTick(target, "#66bb6a", true, "target")}
        <text x="100" y="92" text-anchor="middle" font-size="40" font-weight="600"
          fill="var(--primary-text-color)">${Math.round(soc)}<tspan font-size="20">%</tspan></text>
        <text x="100" y="114" text-anchor="middle" font-size="12" fill="var(--secondary-text-color)"
          class="tip-target" data-tip="kwh">${kwh}</text>
        <text x="100" y="138" text-anchor="middle" font-size="14" font-weight="500" fill="${powerColor}">${powerText}</text>
        <text x="100" y="170" text-anchor="middle" font-size="13" fill="var(--secondary-text-color)">${this._config.name || t.title}</text>
      </svg>`;
    return wrap;
  }

  _chips(t, soc, pv, grid, minSoc, target, size, power) {
    const chips = document.createElement("div");
    chips.className = "chips";
    const add = (cls, text, tip) => {
      const chip = document.createElement("span");
      chip.className = `chip tip-target ${cls}`;
      chip.dataset.tip = tip;
      chip.textContent = text;
      chips.appendChild(chip);
    };
    if (Number.isFinite(pv)) add("pv", t.pv(pv), "pv");
    if (Number.isFinite(grid)) add("grid", t.grid(grid), "grid");
    if (Number.isFinite(minSoc)) add("min", t.min(minSoc), "min");
    if (Number.isFinite(target)) add("target", t.target(target), "target");
    if (Number.isFinite(size) && size > 0) {
      if (power < -PWM_RING_IDLE_W) {
        const time = pwmFormatHours((((100 - soc) / 100) * size * 1000) / -power);
        if (time) add("", t.toFull(time), "toFull");
      } else if (power > PWM_RING_IDLE_W && Number.isFinite(minSoc)) {
        const time = pwmFormatHours((((soc - minSoc) / 100) * size * 1000) / power);
        if (time) add("", t.toMin(time), "toMin");
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
function pwmRingSegment(from, to, color, cls, linecap = "butt", width = 14) {
  from = Math.max(0, from);
  to = Math.min(100, to);
  if (to <= from) return "";
  const length = (PWM_RING_ARC * (to - from)) / 100;
  const start = (PWM_RING_ARC * from) / 100;
  return `<circle cx="100" cy="100" r="${PWM_RING_RADIUS}" fill="none" stroke="${color}"
    stroke-width="${width}" stroke-linecap="${linecap}" class="${cls || ""}" style="color: ${color}"
    stroke-dasharray="${length} ${PWM_RING_CIRCUMFERENCE}" stroke-dashoffset="${-start}"
    transform="rotate(135 100 100)"/>`;
}

/** A mark across the ring at value % (min SoC, target). With `tip`, a wider
 * invisible line on top makes it easy to hit with a finger (issue #136). */
function pwmRingTick(value, color, dashed, tip) {
  if (!Number.isFinite(value)) return "";
  const angle = ((135 + (270 * Math.min(Math.max(value, 0), 100)) / 100) * Math.PI) / 180;
  const point = (radius) => [100 + radius * Math.cos(angle), 100 + radius * Math.sin(angle)];
  const [x1, y1] = point(66);
  const [x2, y2] = point(94);
  const mark = `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}"
    stroke-width="3" stroke-linecap="round"${dashed ? ' stroke-dasharray="3 3"' : ""}/>`;
  if (!tip) return mark;
  return `${mark}<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="transparent"
    stroke-width="16" pointer-events="stroke" class="tip-target" data-tip="${tip}"/>`;
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

/** Phase-load card texts (issue #99) -- same language pick as the ring. */
const PWM_PHASE_TEXTS = {
  en: {
    title: "Phase load",
    missing: "No phase loads picked in PwMngt's options (Solar PV Plant -> main fuse load L1-L3).",
    summary: (phase, amps) => `${phase} · ${amps} A`,
    over: (phase) => `${phase} over the limit`,
    ok: "Room on all phases",
    fuse: (amps) => `Fuse ${amps} A`,
    free: (amps) => `Free: ${amps} A`,
    charging: (kw) => `Battery charging at ${kw} kW`,
  },
  da: {
    title: "Fasebelastning",
    missing: "Ingen fasebelastning valgt i PwMngt's indstillinger (Solar PV Plant -> main fuse load L1-L3).",
    summary: (phase, amps) => `${phase} · ${amps} A`,
    over: (phase) => `${phase} over grænsen`,
    ok: "Plads på alle faser",
    fuse: (amps) => `Sikring ${amps} A`,
    free: (amps) => `Ledig: ${amps} A`,
    charging: (kw) => `Batteriet lader med ${kw} kW`,
  },
};

// Issue #99: one colour per phase -- red is kept for warnings.
const PWM_PHASES = [
  { key: "main_fuse_load_l1", name: "L1", color: "#ab47bc", text: "#ce93d8" },
  { key: "main_fuse_load_l2", name: "L2", color: "#43a047", text: "#81c784" },
  { key: "main_fuse_load_l3", name: "L3", color: "#1e88e5", text: "#64b5f6" },
];
// data/battery_charge_level.py's _TARIFF_FUSE_MARGIN: PwMngt's own limit.
const PWM_FUSE_MARGIN = 0.85;
// Each phase's share of the ring, and the gap between them (% of the arc).
const PWM_PHASE_SHARE = 100 / 3;
const PWM_PHASE_GAP = 1.6;

const PWM_PHASE_CARD_STYLE = `
  ha-card {
    padding: 12px 12px 16px;
    border-radius: var(--ha-card-border-radius, 18px);
  }
  svg { width: 100%; max-width: 260px; display: block; margin: auto; overflow: visible; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; margin-top: 4px; }
  .chip { padding: 4px 10px; border-radius: 999px; font-size: 12px; white-space: nowrap;
    color: var(--primary-text-color); background: var(--secondary-background-color); }
  .chip.warn { background: rgba(229, 57, 53, 0.18); color: var(--error-color, #e53935); }
  .status { margin-top: 8px; text-align: center; font-size: 12px; color: var(--secondary-text-color); }
  .pwm-warning { color: var(--secondary-text-color); font-size: 13px; padding: 8px; }
`;

/**
 * Phase load (issue #99): the main fuse's three phases on one ring, a third
 * each -- filled by the phase's current in % of the fuse, the most loaded
 * phase in the middle. A phase over PwMngt's own limit (fuse x
 * PWM_FUSE_MARGIN) gets a red stripe. Shown when the wizard's phase
 * fields are filled in (sensor.py's PwM_PV_PHASE_LOAD_SENSORS).
 */
class PwMPhaseLoadCard extends HTMLElement {
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
    return PWM_PHASE_TEXTS[String(language).split("-")[0]] || PWM_PHASE_TEXTS.en;
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
      fuse: pwmFindEntityId(this._hass, "hub", "select", "tariff_fuse_size"),
      command: pwmFindEntityId(this._hass, "pv", "select", "battery_command"),
      maxCharge: pwmFindEntityId(this._hass, "pv", "select", "battery_max_charge_effect"),
    };
    for (const phase of PWM_PHASES) {
      this._ids[phase.key] = pwmFindEntityId(this._hass, "pv", "sensor", phase.key);
    }
    this._signature = undefined;
  }

  _state(name) {
    const entityId = this._ids[name];
    return entityId ? this._hass.states[entityId] : undefined;
  }

  _number(name) {
    const state = this._state(name);
    if (!state) return NaN;
    return state.state === "" ? NaN : Number(state.state);
  }

  _stateSignature() {
    const parts = Object.keys(this._ids).map((name) => {
      const state = this._state(name);
      return state ? state.state : "-";
    });
    parts.push(this._texts().title);
    return parts.join("#");
  }

  _render() {
    const t = this._texts();
    const root = this.shadowRoot;
    root.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = PWM_PHASE_CARD_STYLE;
    const card = document.createElement("ha-card");
    if (this._config.title) card.header = this._config.title;
    root.append(style, card);

    const phases = PWM_PHASES.map((phase) => ({ ...phase, amps: this._number(phase.key) })).filter(
      (phase) => this._ids[phase.key]
    );
    if (!phases.length) {
      const warning = document.createElement("div");
      warning.className = "pwm-warning";
      warning.textContent = t.missing;
      card.appendChild(warning);
      return;
    }
    const fuse = this._number("fuse");
    const limit = Number.isFinite(fuse) ? fuse * PWM_FUSE_MARGIN : NaN;

    card.appendChild(this._ring(t, phases, fuse, limit));
    card.appendChild(this._chips(t, phases, fuse, limit));
    const status = this._chargingText(t);
    if (status) {
      const line = document.createElement("div");
      line.className = "status";
      line.textContent = status;
      card.appendChild(line);
    }
  }

  _ring(t, phases, fuse, limit) {
    const known = phases.filter((phase) => Number.isFinite(phase.amps));
    const top = known.reduce((best, phase) => (!best || phase.amps > best.amps ? phase : best), null);
    const pct = top && fuse > 0 ? Math.round((top.amps / fuse) * 100) : null;
    const over = known.filter((phase) => Number.isFinite(limit) && phase.amps > limit);
    // Label positions: the middle of each third of the ring.
    const labels = [
      { x: -6, y: 98, anchor: "end" },
      { x: 100, y: -22, anchor: "middle" },
      { x: 206, y: 98, anchor: "start" },
    ];

    let arcs = "";
    let texts = "";
    PWM_PHASES.forEach((phase, index) => {
      const shown = phases.find((p) => p.key === phase.key);
      const from = index * PWM_PHASE_SHARE;
      const width = PWM_PHASE_SHARE - PWM_PHASE_GAP;
      arcs += pwmRingSegment(from, from + width, "var(--divider-color)");
      if (!shown) return;
      const amps = shown.amps;
      const share = Number.isFinite(amps) && fuse > 0 ? Math.min(Math.max(amps / fuse, 0), 1) : 0;
      arcs += pwmRingSegment(from, from + width * share, phase.color);
      if (Number.isFinite(limit) && amps > limit) {
        arcs += pwmRingSegment(from, from + width * share, "#e53935", "", "butt", 5);
      }
      const label = labels[index];
      const value = Number.isFinite(amps) ? `${pwmFormatDecimal(amps)} A` : "–";
      texts += `<text x="${label.x}" y="${label.y}" text-anchor="${label.anchor}" font-size="13" font-weight="500" fill="${phase.text}">${value}</text>
        <text x="${label.x}" y="${label.y + 14}" text-anchor="${label.anchor}" font-size="11" fill="${phase.text}">${phase.name}</text>`;
    });

    const centre = top
      ? `<text x="100" y="96" text-anchor="middle" font-size="36" font-weight="600" fill="${top.text}">${pct ?? "–"}<tspan font-size="18">%</tspan></text>
         <text x="100" y="118" text-anchor="middle" font-size="12" fill="var(--secondary-text-color)">${t.summary(
           top.name,
           Math.round(top.amps)
         )}</text>
         <text x="100" y="142" text-anchor="middle" font-size="13" font-weight="500"
           fill="${over.length ? "var(--error-color, #e53935)" : "var(--primary-text-color)"}">${
           over.length ? t.over(over.map((phase) => phase.name).join(", ")) : t.ok
         }</text>`
      : "";
    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <svg viewBox="-24 -36 248 226">
        ${arcs}
        ${texts}
        ${centre}
        <text x="100" y="182" text-anchor="middle" font-size="13" fill="var(--secondary-text-color)">${this._config.name || t.title}</text>
      </svg>`;
    return wrap;
  }

  _chips(t, phases, fuse, limit) {
    const chips = document.createElement("div");
    chips.className = "chips";
    const add = (cls, text) => {
      const chip = document.createElement("span");
      chip.className = `chip ${cls}`;
      chip.textContent = text;
      chips.appendChild(chip);
    };
    if (Number.isFinite(fuse)) add("", t.fuse(Math.round(fuse)));
    const known = phases.filter((phase) => Number.isFinite(phase.amps));
    if (Number.isFinite(fuse) && known.length) {
      // Free current = the fuse minus the most loaded phase (not PwMngt's
      // 85 % limit) -- what is actually left before the fuse blows. Warns
      // once a phase is above PwMngt's limit.
      const highest = Math.max(...known.map((phase) => phase.amps));
      const free = Math.max(0, Math.floor(fuse - highest));
      const overLimit = Number.isFinite(limit) && highest > limit;
      add(free > 0 && !overLimit ? "" : "warn", t.free(free));
    }
    return chips;
  }

  /** "Battery charging at N kW" while a charge_now_* command runs. */
  _chargingText(t) {
    const command = this._state("command");
    const match = command ? /^charge_now_(\d+)$/.exec(command.state) : null;
    const maxW = this._number("maxCharge");
    if (!match || !Number.isFinite(maxW)) return "";
    return t.charging(pwmFormatKw((maxW * Number(match[1])) / 100));
  }
}
pwmDefine("pwm-phase-load-card", PwMPhaseLoadCard);

/* ---------------------------------------------------------------------------
 * PwM Power flow card (issue #109): the house in the middle, any number of
 * power entities around it (a ring, plus an optional left column for
 * PV/grid/battery) and animated flow dots on the lines between them.
 * Started as Claus's standalone "radial-power-card"; the type is new, so a
 * dashboard still using the old card keeps working side by side.
 * ------------------------------------------------------------------------- */

/** Power flow card texts -- same language pick as PWM_BATTERY_TEXTS. */
const PWM_FLOW_TEXTS = {
  en: {
    invalidConfig: "Invalid configuration",
    general: "General",
    center: "The house (middle)",
    devices: "Devices around the house",
    add: "+ Add device",
    newDevice: "New device",
    moveUp: "Move up",
    moveDown: "Move down",
    remove: "Remove",
    secondaryHint:
      'Extra lines can get a label in front (e.g. "in", "SoC") and a number of decimals in YAML: secondary: [{entity: ..., label: SoC, decimals: 0}]',
    placementRing: "Ring around the house",
    placementSide: "Column on the left (PV/grid/battery)",
    placementGrid: "By the grid, outside the house (e.g. a tenant)",
    flowOut: "Out of the house when positive (consumption, battery charging)",
    flowIn: "Into the house when positive (solar, grid import, battery discharging)",
    roleAuto: "Automatic (PwMngt's own sensors)",
    rolePv: "Solar (top)",
    roleGrid: "Grid (middle)",
    roleBattery: "Battery (bottom)",
    layoutColumn: "Column (solar/grid/battery on the left, others around the house)",
    layoutCross: "Cross (solar top, grid left, battery bottom, house right)",
    secondarySmall: "Small (as today)",
    directionSign: "Sign (-17 W)",
    directionArrow: "Arrow (← 17 W)",
    directionCard: "As the card",
    secondaryValue: "Like the power value",
    labels: {
      title: "Title",
      background: "Background (#000, theme, transparent)",
      show_date: "Show date",
      min_power: "Min. power for flow (W)",
      entity: "Power entity (W/kW)",
      name: "Name",
      icon: "Icon",
      color: "Colour (e.g. #ff5252)",
      flow: "Direction when positive",
      invert: "Invert sign",
      hide_name: "Hide name",
      secondary: "Extra lines (entities)",
      subtract: "Subtract from the house's value",
      placement: "Placement",
      hide_below: "Hide below (W)",
      role: "Role in the left column",
      center_size: "House size",
      node_size: "Circle size",
      text_size: "Text size (%)",
      name_on_arc: "Name on an arc above the circle",
      layout: "Layout",
      icon_size: "Icon size (%)",
      secondary_above: "Extra lines above the icon",
      flow_time: "Animation time (%) - higher is slower",
      grid_counted: "The grid circle also measures this device",
      header_size: "Header size (px) - like the other cards",
      secondary_size: "Extra lines' style",
      scale: "Scale (%)",
      direction_display: "Direction (sale/charging)",
    },
  },
  da: {
    invalidConfig: "Ugyldig konfiguration",
    general: "Generelt",
    center: "Huset (midten)",
    devices: "Enheder rundt om huset",
    add: "+ Tilføj enhed",
    newDevice: "Ny enhed",
    moveUp: "Flyt op",
    moveDown: "Flyt ned",
    remove: "Fjern",
    secondaryHint:
      'Ekstra linjer kan få en tekst foran (fx "in", "SoC") og et antal decimaler via YAML: secondary: [{entity: ..., label: SoC, decimals: 0}]',
    placementRing: "Ring om huset",
    placementSide: "Kolonne til venstre (PV/net/batteri)",
    placementGrid: "Ved nettet, uden om huset (fx lejemål)",
    flowOut: "Ud fra huset ved positiv værdi (forbrug, batteri der lader)",
    flowIn: "Ind i huset ved positiv værdi (sol, net-import, batteri der aflader)",
    roleAuto: "Automatisk (PwMngt's egne sensorer)",
    rolePv: "Solceller (øverst)",
    roleGrid: "Net (midten)",
    roleBattery: "Batteri (nederst)",
    layoutColumn: "Kolonne (sol/net/batteri til venstre, øvrige rundt om huset)",
    layoutCross: "Kryds (sol øverst, net til venstre, batteri nederst, huset til højre)",
    secondarySmall: "Lille (som i dag)",
    directionSign: "Fortegn (-17 W)",
    directionArrow: "Pil (← 17 W)",
    directionCard: "Som kortet",
    secondaryValue: "Som watt-tallet",
    labels: {
      title: "Titel",
      background: "Baggrund (#000, theme, transparent)",
      show_date: "Vis dato",
      min_power: "Min. effekt for flow (W)",
      entity: "Effekt-entitet (W/kW)",
      name: "Navn",
      icon: "Ikon",
      color: "Farve (fx #ff5252)",
      flow: "Retning ved positiv værdi",
      invert: "Vend fortegn",
      hide_name: "Skjul navn",
      secondary: "Ekstra linjer (entiteter)",
      subtract: "Træk fra husets visning",
      placement: "Placering",
      hide_below: "Skjul under (W)",
      role: "Rolle i venstre kolonne",
      center_size: "Husets størrelse",
      node_size: "Cirkelstørrelse",
      text_size: "Tekststørrelse (%)",
      name_on_arc: "Navn på en bue over cirklen",
      layout: "Layout",
      icon_size: "Ikonstørrelse (%)",
      secondary_above: "Ekstra linjer over ikonet",
      flow_time: "Animationstid (%) - højere er langsommere",
      grid_counted: "Net-cirklen måler også denne enhed",
      header_size: "Overskriftens størrelse (px) - som de andre kort",
      secondary_size: "Ekstra linjers stil",
      scale: "Skala (%)",
      direction_display: "Retning (salg/ladning)",
    },
  },
};

/** setConfig() runs before hass is set, so fall back to the page language. */
function pwmFlowTexts(hass) {
  const language =
    (hass && ((hass.locale && hass.locale.language) || hass.language)) ||
    document.documentElement.lang ||
    "en";
  return PWM_FLOW_TEXTS[String(language).split("-")[0]] || PWM_FLOW_TEXTS.en;
}

const PWM_FLOW_COLORS = ["#29b6f6", "#ff5252", "#ffd600", "#8c9eff", "#4f8cff", "#00e5ff",
  "#ffab00", "#00b8d4", "#7c83ff", "#9ccc65", "#ff7043", "#f06292"];

function pwmFlowToWatt(state) {
  if (!state) return null;
  const value = parseFloat(state.state);
  if (isNaN(value)) return null;
  const unit = String(state.attributes.unit_of_measurement || "W").trim();
  if (unit === "kW") return value * 1000;
  if (unit === "MW") return value * 1e6;
  return value;
}

function pwmFlowFormatPower(watts, language) {
  if (watts === null) return "—";
  // A non-breaking space keeps number and unit together (issue #106).
  if (Math.abs(watts) >= 1000) {
    return (watts / 1000).toLocaleString(language, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " kW";
  }
  return (Math.round(watts) || 0).toLocaleString(language) + " W";
}

function pwmFlowFormatState(hass, entityId, decimals) {
  const state = hass.states[entityId];
  if (!state) return "—";
  // Issue #118: a fixed number of decimals (e.g. SoC "97 %") -- only for a
  // numeric state; anything else (unavailable, text) is shown as HA does.
  const digits = decimals === undefined || decimals === null || decimals === "" ? NaN : Number(decimals);
  const value = Number(state.state);
  if (Number.isInteger(digits) && digits >= 0 && state.state !== "" && Number.isFinite(value)) {
    const language = (hass.locale && hass.locale.language) || hass.language || "en";
    const unit = state.attributes.unit_of_measurement;
    const text = value.toLocaleString(language, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    return text + (unit ? " " + unit : "");
  }
  if (typeof hass.formatEntityState === "function") return hass.formatEntityState(state);
  const unit = state.attributes.unit_of_measurement;
  return state.state + (unit ? " " + unit : "");
}

/** `secondary` may be one entity_id, a list of them, or {entity, label} objects. */
function pwmFlowSecondaryList(secondary) {
  if (!secondary) return [];
  return (Array.isArray(secondary) ? secondary : [secondary])
    .map((s) => (typeof s === "string" ? { entity: s } : s))
    .filter((s) => s && s.entity);
}

function pwmFlowSecondaryText(hass, secondary) {
  return pwmFlowSecondaryList(secondary)
    .map((s) => (s.label ? s.label + " " : "") + pwmFlowFormatState(hass, s.entity, s.decimals))
    .join("\n");
}

/** Drop empty values so the saved YAML only holds what the user chose. */
function pwmFlowClean(object) {
  const result = {};
  Object.keys(object || {}).forEach((key) => {
    const value = object[key];
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) return;
    result[key] = value;
  });
  return result;
}

/** JSON with sorted keys, to recognise the editor's own config-changed echo. */
function pwmFlowStableJson(object) {
  return JSON.stringify(object, (key, value) => (value && typeof value === "object" && !Array.isArray(value)
    ? Object.keys(value).sort().reduce((r, k) => { r[k] = value[k]; return r; }, {}) : value));
}

/** The entity picker only returns ids -- keep labels and decimals set in YAML. */
function pwmFlowMergeSecondary(ids, old) {
  const previous = pwmFlowSecondaryList(old);
  return (ids || []).map((id) => {
    const found = previous.find((x) => x.entity === id);
    if (!found || (!found.label && found.decimals === undefined)) return id;
    return { ...found, entity: id };
  });
}

// Issue #113: the left column's fixed roles -- solar on top, grid in the
// middle, battery at the bottom -- recognised by PwMngt's own sensors when
// the device has no "role" of its own.
const PWM_FLOW_ROLES = ["pv", "grid", "battery"];
const PWM_FLOW_ROLE_KEYS = { pv_total: "pv", grid_power: "grid", battery_power: "battery" };

/**
 * Issue #113: dot speed (seconds per dot spacing) -- a lot faster with more
 * power, so the biggest flow stands out: 100 W ~3.6 s, 1 kW 2 s, 4.5 kW
 * ~0.7 s, 10 kW ~0.35 s.
 */
function pwmFlowDuration(watts) {
  return Math.max(0.3, Math.min(4, 4 / (1 + Math.abs(watts) / 1000)));
}

/**
 * Issue #113: how PV, grid and battery power split between each other and
 * the house (all W, >= 0). pv = production; grid > 0 buying, < 0 selling;
 * battery > 0 discharging, < 0 charging. Selling comes from the PV first,
 * then the battery; charging from the PV first, then the grid; the rest
 * goes to the house.
 */
function pwmFlowSplit(pv, grid, battery) {
  const p = Math.max(pv || 0, 0);
  const gridIn = Math.max(grid || 0, 0);
  const gridOut = Math.max(-(grid || 0), 0);
  const batteryOut = Math.max(battery || 0, 0);
  const batteryIn = Math.max(-(battery || 0), 0);
  const pvToGrid = Math.min(p, gridOut);
  const batteryToGrid = Math.min(batteryOut, gridOut - pvToGrid);
  const pvToBattery = Math.min(p - pvToGrid, batteryIn);
  const gridToBattery = Math.min(gridIn, batteryIn - pvToBattery);
  return {
    pvToGrid,
    batteryToGrid,
    pvToBattery,
    gridToBattery,
    pvToHouse: Math.max(p - pvToGrid - pvToBattery, 0),
    gridToHouse: Math.max(gridIn - gridToBattery, 0),
    batteryToHouse: Math.max(batteryOut - batteryToGrid, 0),
  };
}

class PwMPowerFlowCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    // Issue #115: the shadow root's children are created once and never
    // replaced -- card-mod puts its own <card-mod> element in here, and a
    // rebuild only redraws the .stage, so outside styling survives it.
    this.shadowRoot.innerHTML = `
      <style>${PWM_FLOW_CARD_STYLE}</style>
      <ha-card>
        <div class="header"><span class="title"></span><span class="date"></span></div>
        <div class="stage"></div>
      </ha-card>`;
  }

  static getConfigElement() {
    return document.createElement("pwm-power-flow-card-editor");
  }

  /**
   * Pre-filled from PwMngt's own sensors, which are already normalised
   * (pv_properties.power_sign()): Grid power + = buying and Battery power
   * + = discharging, i.e. both flow into the house when positive -- the
   * left column's default direction. House power includes the chargers,
   * so they are subtracted from the middle to make the picture add up.
   */
  static getStubConfig(hass) {
    const find = (device, key) => pwmFindEntityId(hass, device, "sensor", key);
    const side = [
      { key: "pv_total", icon: "mdi:solar-power-variant", color: "#ffd600" },
      { key: "grid_power", icon: "mdi:transmission-tower", color: "#29b6f6" },
      { key: "battery_power", icon: "mdi:home-battery", color: "#9ccc65" },
    ]
      .map((d) => ({
        entity: find("pv", d.key),
        placement: "side",
        role: PWM_FLOW_ROLE_KEYS[d.key],
        icon: d.icon,
        color: d.color,
      }))
      .filter((d) => d.entity);
    const chargers = ["charger1", "charger2"]
      .map((charger) => find(charger, "power"))
      .filter(Boolean)
      .map((entity) => ({ entity, icon: "mdi:ev-station", subtract: true }));
    const house = find("pv", "house_power");
    if (house || side.length) {
      return { show_date: true, center: { entity: house || "" }, devices: [...side, ...chargers] };
    }
    // PwMngt's PV device is not set up: suggest any power sensors instead.
    const power = Object.keys(hass.states).filter(
      (e) => e.startsWith("sensor.") && hass.states[e].attributes.device_class === "power"
    );
    return {
      show_date: true,
      center: { entity: power[0] || "" },
      devices: power.slice(1, 7).map((entity) => ({ entity })),
    };
  }

  setConfig(config) {
    if (!config) throw new Error(pwmFlowTexts(this._hass).invalidConfig);
    this._config = {
      min_power: 5,
      ...config,
      center: { ...(config.center || {}) },
      devices: Array.isArray(config.devices) ? config.devices : [],
    };
    // Issue #115: the left column's roles are looked up in hass.entities, so
    // building before hass arrives would only mean a second build right after.
    if (this._hass) {
      this._build();
      this._update();
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._nodes) this._build();
    this._update();
  }

  getCardSize() {
    return 9;
  }

  /** Lays out the SVG lines and circles; `visible` hides devices below hide_below. */
  _build(visible) {
    const c = this._config;
    if (!c) return;
    visible = visible || c.devices.map(() => true);
    const roles = this._sideRoles();
    this._visibleKey = this._layoutKey(visible, roles);
    const side = [];
    const ring = [];
    const beside = [];
    c.devices.forEach((d, i) => {
      if (!visible[i]) return;
      if (d.placement === "side") side.push(i);
      else if (d.placement === "grid") beside.push(i);
      else ring.push(i);
    });
    const hasSide = side.length > 0;
    const NS = Math.max(12, Math.min(40, Number(c.node_size) || 26)); // wanted circle size
    const CD = Math.max(16, Math.min(40, Number(c.center_size) || 32)); // house diameter
    const CR = CD / 2;
    const TS = Math.max(50, Math.min(200, Number(c.text_size) || 100)) / 100; // text scale
    const IS = Math.max(50, Math.min(200, Number(c.icon_size) || 100)) / 100; // icon scale
    // Issue #114: a factor on the time a dot takes -- higher is slower.
    this._flowTime = Math.max(25, Math.min(400, Number(c.flow_time) || 100)) / 100;
    // A name on an arc above the circle needs room above the top circles (LH).
    const ARC = !!c.name_on_arc;
    const LH = ARC ? NS * 0.11 * TS + 1.5 : 0;
    const centerColor = c.center.color || "#29b6f6";

    // Issue #113: with solar, grid and battery recognised (each side device
    // has its own role) they keep fixed places and get lines between each other.
    const sideRoles = side.map((i) => roles[i]);
    const fixed = side.length > 0 && sideRoles.every(Boolean) && new Set(sideRoles).size === sideRoles.length;
    // Issue #119: one device by the grid, outside the house (e.g. a tenant),
    // sits to the right of the solar and hangs off the grid circle -- without
    // their fixed places (or for a second one) it joins the ring instead.
    const besideIndex = fixed && sideRoles.includes("grid") && sideRoles.includes("pv") ? beside[0] : undefined;
    beside.forEach((i) => { if (i !== besideIndex) ring.push(i); });
    ring.sort((a, b) => a - b);
    this._besideIndex = besideIndex;
    // Issue #114: the "cross" layout needs the fixed roles and room for at
    // most four other devices.
    const cross = c.layout === "cross" && fixed && ring.length <= 4;
    this._roleIndex = {};
    const geometry = [];
    const links = {};
    let VW, HX, HY;

    if (cross) {
      // Like the classic power flow card: grid to the left, solar above and
      // the battery below the next column, the house in the third, two
      // devices in the corners to its right and two more above/below it.
      side.forEach((i) => (this._roleIndex[roles[i]] = i));
      const D = Math.min(NS, (82 - LH) / 2);
      const R = D / 2;
      HY = 50 + LH / 2;
      // The other devices: room for them (and their names) above and below
      // the house, so a little smaller when the house is big.
      const RC = Math.max(4, Math.min(R, (HY - CR - 4 - 2 * LH) / 2 + 1, (96 - HY - CR - LH) / 2 + 1));
      const GAP = 1.45 * Math.max(D, R + CR); // between the columns' centres
      const GX = R + 1;
      const MX = GX + GAP;
      HX = MX + GAP;
      const RX = HX + GAP;
      VW = (ring.length ? RX + R : HX + CR) + 1;
      const top = { x: MX, y: 1 + LH + R, d: D };
      const bottom = { x: MX, y: 99 - R, d: D };
      const left = { x: GX, y: HY, d: D };
      const places = { pv: top, grid: left, battery: bottom };
      // A point on the house's left (-1) or right (+1) side, dy above (-) or below (+) its centre.
      const houseSide = (dir, dy) => [HX + dir * Math.sqrt(Math.max(CR * CR - dy * dy, 0)), HY + dy];
      side.forEach((i) => {
        const g = places[roles[i]];
        let p;
        // Drawn from the house outwards, like the column's lines.
        if (roles[i] === "grid") {
          p = `M${HX - CR} ${HY} L${g.x + R} ${g.y}`;
        } else {
          const up = roles[i] === "pv" ? -1 : 1;
          const [ex, ey] = houseSide(-1, up * CR * 0.4);
          const sx = g.x + R * 0.5, sy = g.y - up * R * 0.87;
          p = `M${ex} ${ey} Q${sx} ${ey} ${sx} ${sy}`;
        }
        geometry[i] = { ...g, p };
      });
      // Top right and bottom right first, as big as the others with a line
      // long enough for the dots (#114), then right above and below the house.
      const extras = [1 + LH + R, 99 - R].map((y) => {
        const dx = RX - HX, dy = y - HY, len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len;
        return { x: RX, y, d: D, p: `M${HX + ux * CR} ${HY + uy * CR} L${RX - ux * R} ${y - uy * R}` };
      });
      const topY = 1 + LH + RC, bottomY = 99 - RC;
      extras.push(
        { x: HX, y: topY, d: 2 * RC, p: `M${HX} ${HY - CR} L${HX} ${topY + RC}` },
        { x: HX, y: bottomY, d: 2 * RC, p: `M${HX} ${HY + CR} L${HX} ${bottomY - RC}` },
      );
      ring.forEach((i, k) => (geometry[i] = extras[k]));
      // The lines between solar, grid and battery, drawn from the first to the second.
      const has = (role) => this._roleIndex[role] !== undefined;
      if (has("pv") && has("grid")) {
        const sx = top.x - R * 0.5, sy = top.y + R * 0.87;
        const ex = left.x + R * 0.94, ey = left.y - R * 0.35;
        links.pv_grid = `M${sx} ${sy} Q${sx} ${ey} ${ex} ${ey}`;
      }
      if (has("grid") && has("battery")) {
        const sx = left.x + R * 0.94, sy = left.y + R * 0.35;
        const ex = bottom.x - R * 0.5, ey = bottom.y - R * 0.87;
        links.grid_battery = `M${sx} ${sy} Q${ex} ${sy} ${ex} ${ey}`;
      }
      if (has("pv") && has("battery")) {
        links.pv_battery = `M${top.x} ${top.y + R} L${bottom.x} ${bottom.y - R}`;
      }
    } else {
      VW = hasSide ? 140 : 100; // viewBox width (the height is always 100)
      HX = hasSide ? 86 : 50; // the house's centre
      HY = 50 + LH / 2; // moved down so the top circle's name fits above it

      // Ring: a full circle -- or a 240° arc to the right when there is a left column.
      const nr = ring.length;
      const span = hasSide ? (240 * Math.PI) / 180 : 2 * Math.PI;
      const ringStep = nr ? span / nr : 0;
      // The circles must fit vertically (0..100) and not touch the house.
      let ORBIT = 39;
      let D = nr ? Math.min(NS, 2 * ORBIT * Math.sin(Math.min(ringStep, Math.PI) / 2) * 0.9) : NS;
      ORBIT = Math.min(ORBIT, 49 - D / 2 - LH / 2);
      ORBIT = Math.max(ORBIT, CR + D / 2 + 4);
      D = Math.min(D, 2 * (49 - ORBIT - LH / 2));
      const R = D / 2;

      // Left column: solar on top, grid in the middle moved a little right,
      // battery at the bottom (#113).
      const ns = side.length;
      const slots = fixed ? PWM_FLOW_ROLES.length : ns;
      let sideTop = 18 + LH / 2;
      let sideStep = slots > 1 ? (82 - sideTop) / (slots - 1) : 0;
      let DS = slots > 1 ? Math.min(NS, sideStep * 0.9 - LH) : NS;
      if (slots > 1 && NS > DS) {
        // Issue #114: bigger circles than that spacing allows -- use the full
        // height, with just room for the names between them.
        DS = Math.min(NS, (98 - slots * LH - 2 * (slots - 1)) / slots);
        sideTop = 1 + LH + DS / 2;
        sideStep = DS + LH + 2;
      }
      const RS = DS / 2;
      const SX = RS + 1;
      const GRID_SHIFT = fixed ? DS * 0.45 : 0;

      ring.forEach((i, k) => {
        const angle = hasSide ? -span / 2 + (k + 0.5) * ringStep : -Math.PI / 2 + k * ringStep;
        const cos = Math.cos(angle);
        const sin = Math.sin(angle);
        geometry[i] = {
          x: HX + ORBIT * cos, y: HY + ORBIT * sin, d: D,
          p: `M${HX + CR * cos} ${HY + CR * sin} L${HX + (ORBIT - R) * cos} ${HY + (ORBIT - R) * sin}`,
        };
      });
      side.forEach((i, k) => {
        const slot = fixed ? PWM_FLOW_ROLES.indexOf(roles[i]) : k;
        const y = fixed || ns > 1 ? sideTop + slot * sideStep : 50;
        const x = fixed && roles[i] === "grid" ? SX + GRID_SHIFT : SX;
        if (fixed) this._roleIndex[roles[i]] = i;
        // Where the line meets the house's left side, spread by height.
        const ea = Math.PI - ((y - HY) / 50) * 0.75;
        const ex = HX + CR * Math.cos(ea);
        const ey = HY + CR * Math.sin(ea);
        const sx = x + RS;
        const dx = ex - sx;
        geometry[i] = {
          x, y, d: DS,
          // Drawn from the house outwards, the same direction as the ring's lines.
          p: `M${ex} ${ey} C${ex - dx * 0.45} ${ey} ${sx + dx * 0.55} ${y} ${sx} ${y}`,
        };
      });

      // Issue #113: the lines between solar, grid and battery (drawn from the
      // first to the second; dots run backwards for the other way).
      const at = (role) => geometry[this._roleIndex[role]];
      const edge = (from, to) => {
        const dx = to.x - from.x, dy = to.y - from.y, len = Math.hypot(dx, dy) || 1;
        return [from.x + (dx / len) * RS, from.y + (dy / len) * RS];
      };
      if (at("pv") && at("grid")) {
        const [ax, ay] = edge(at("pv"), at("grid")), [bx, by] = edge(at("grid"), at("pv"));
        links.pv_grid = `M${ax} ${ay} L${bx} ${by}`;
      }
      if (at("grid") && at("battery")) {
        const [ax, ay] = edge(at("grid"), at("battery")), [bx, by] = edge(at("battery"), at("grid"));
        links.grid_battery = `M${ax} ${ay} L${bx} ${by}`;
      }
      if (at("pv") && at("battery")) {
        // Down the left side, outside the grid circle (moved right for room).
        const pv = at("pv"), bat = at("battery");
        const ax = pv.x - RS * 0.8, ay = pv.y + RS * 0.6;
        const bx = bat.x - RS * 0.8, by = bat.y - RS * 0.6;
        links.pv_battery = `M${ax} ${ay} C${ax - 2} ${ay + 12} ${bx - 2} ${by - 12} ${bx} ${by}`;
      }
    }
    // Issue #119: the device by the grid sits to the right of the solar, with
    // a line from the grid circle.
    if (besideIndex !== undefined) {
      const sun = geometry[this._roleIndex.pv];
      const g = geometry[this._roleIndex.grid];
      const r = g.d / 2;
      if (cross) {
        // Top left, above the grid and beside the solar -- smaller if needed
        // to leave room for the grid's name.
        const rt = Math.max(4, Math.min(r, (g.y - r - 2 * LH - 3) / 2));
        const y = 1 + LH + rt;
        geometry[besideIndex] = {
          x: g.x, y, d: 2 * rt,
          p: `M${g.x + r * 0.71} ${g.y - r * 0.71} L${g.x + rt * 0.71} ${y + rt * 0.71}`,
        };
      } else {
        // A little smaller and higher than the solar, so the solar's line to
        // the house can pass below it (and above the grid's name).
        const rs = sun.d / 2;
        const rt = rs * 0.75;
        const x = sun.x + rs + 2.5 + rt;
        const y = sun.y - rs + rt;
        const dx = x - g.x, dy = y - g.y, len = Math.hypot(dx, dy) || 1;
        geometry[besideIndex] = {
          x, y, d: 2 * rt,
          p: `M${g.x + (dx / len) * r} ${g.y + (dy / len) * r} L${x - (dx / len) * rt} ${y - (dy / len) * rt}`,
        };
        // The solar's line to the house leaves 30° below its right side.
        const ea = Math.PI - ((sun.y - HY) / 50) * 0.75;
        const ex = HX + CR * Math.cos(ea), ey = HY + CR * Math.sin(ea);
        const px = sun.x + rs * 0.87, py = sun.y + rs * 0.5;
        sun.p = `M${ex} ${ey} C${ex - (ex - px) * 0.45} ${ey} ${px + (ex - px) * 0.4} ${py} ${px} ${py}`;
      }
    }
    // Issue #121: the way to the house at each circle, along its own line --
    // the line is drawn from the house outwards, so it is the end's tangent
    // (the last point minus the one before it), rounded to an arrow.
    this._toHouse = [];
    geometry.forEach((g, i) => {
      if (!g) return;
      const n = g.p.match(/-?\d+(\.\d+)?(e-?\d+)?/g).map(Number);
      const dx = n[n.length - 4] - n[n.length - 2];
      const dy = n[n.length - 3] - n[n.length - 1];
      this._toHouse[i] = Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
    });
    const pct = (x) => (x / VW) * 100;

    // The name follows the circle: a 270° arc from bottom-left over the top to
    // bottom-right, centred at the top, so long names still have room.
    const arcLabel = (id, x, y, d, scale) => {
      const size = d * scale * TS;
      const ra = d / 2 + 0.8;
      const a = (3 * Math.PI) / 4;
      const sx = x - ra * Math.sin(a), sy = y - ra * Math.cos(a);
      const ex = x + ra * Math.sin(a);
      return `<path id="arc-${id}" d="M${sx} ${sy} A${ra} ${ra} 0 1 1 ${ex} ${sy}" fill="none"/>
        <text class="arc-name" data-arc="${id}" font-size="${size}" text-anchor="middle"><textPath href="#arc-${id}" startOffset="50%"></textPath></text>`;
    };
    let labels = ARC ? arcLabel("c", HX, HY, CD, 0.10) : "";
    let svg = "";
    let nodes = "";
    c.devices.forEach((d, i) => {
      const g = geometry[i];
      if (!g) return; // hidden
      const color = d.color || PWM_FLOW_COLORS[i % PWM_FLOW_COLORS.length];
      svg += `<g class="line" data-i="${i}" style="--c:${color}"><path class="base" d="${g.p}"/><path class="dots" d="${g.p}"/></g>`;
      if (ARC) labels += arcLabel(i, g.x, g.y, g.d, 0.11);
      nodes += `<div class="node dev" data-i="${i}" style="left:${pct(g.x)}%;top:${g.y}%;width:${pct(g.d)}%;--c:${color}">
        <ha-icon></ha-icon><div class="val"><ha-icon class="dir"></ha-icon><span></span></div><div class="name"></div><div class="sec"></div></div>`;
    });

    // Faint in the first end's colour until power runs on it.
    const linkColor = (role) => {
      const i = this._roleIndex[role];
      return c.devices[i].color || PWM_FLOW_COLORS[i % PWM_FLOW_COLORS.length];
    };
    Object.keys(links).forEach((key) => {
      const color = linkColor(key.split("_")[0]);
      svg = `<g class="line link" data-link="${key}" style="--c:${color}"><path class="base" d="${links[key]}"/><path class="dots" d="${links[key]}"/></g>` + svg;
    });

    const stage = this.shadowRoot.querySelector(".stage");
    // Issue #120: a smaller drawing, centred -- the text follows its width.
    const SC = Math.max(50, Math.min(100, Number(c.scale) || 100));
    stage.style.cssText = `width:${SC}%;aspect-ratio:${VW}/100;max-width:${VW * 6.4}px;--k:${100 / VW};--ts:${TS};--is:${IS}`;
    // Issue #114: the extra lines (e.g. the SoC) above the icon instead of below the value.
    stage.classList.toggle("sec-above", !!c.secondary_above);
    // Issue #120: the extra lines in the same size and weight as the power value.
    stage.classList.toggle("sec-value", c.secondary_size === "value");
    // Issue #120: the header like the other cards' (normal weight) when a size is set.
    const header = this.shadowRoot.querySelector(".header");
    const headerSize = Number(c.header_size);
    header.style.fontSize = headerSize > 0 ? headerSize + "px" : "";
    header.style.fontWeight = headerSize > 0 ? "400" : "";
    stage.innerHTML = `
      <svg viewBox="0 0 ${VW} 100">${svg}<g class="labels">${labels}</g></svg>
      <div class="node center" style="left:${pct(HX)}%;top:${HY}%;width:${pct(CD)}%;--c:${centerColor}">
        <ha-icon></ha-icon><div class="val"><ha-icon class="dir"></ha-icon><span></span></div><div class="name"></div><div class="sec"></div>
      </div>
      ${nodes}`;

    const pick = (el, id) => ({
      el,
      arc: this.shadowRoot.querySelector(`.arc-name[data-arc="${id}"] textPath`),
      icon: el.querySelector("ha-icon"),
      val: el.querySelector(".val span"),
      dir: el.querySelector(".val .dir"),
      name: el.querySelector(".name"),
      sec: el.querySelector(".sec"),
    });
    const background = c.background || "#000";
    const card = this.shadowRoot.querySelector("ha-card");
    card.style.setProperty("--pwm-flow-card-bg",
      background === "theme" ? "var(--ha-card-background, var(--card-background-color))" : background);
    // On the theme's own (often light) background the white text would vanish
    // and the dark circles look heavy -- follow the theme's colours instead.
    card.classList.toggle("themed", background === "theme" || background === "transparent");
    this._nodes = [];
    this._lines = [];
    this.shadowRoot.querySelectorAll(".node.dev").forEach((el) => (this._nodes[+el.dataset.i] = pick(el, el.dataset.i)));
    this.shadowRoot.querySelectorAll(".line[data-i]").forEach((el) => (this._lines[+el.dataset.i] = el));
    this._links = {};
    this.shadowRoot.querySelectorAll(".line[data-link]").forEach((el) => (this._links[el.dataset.link] = el));
    this._linkDurations = {};
    this._center = pick(this.shadowRoot.querySelector(".node.center"), "c");
    this._durations = [];

    this._nodes.forEach((node, i) =>
      node && node.el.addEventListener("click", () => this._moreInfo(c.devices[i].entity))
    );
    this._center.el.addEventListener("click", () => this._moreInfo(c.center.entity));
  }

  /** Issue #113: each device's left-column role (pv/grid/battery) or null. */
  _sideRoles() {
    const entities = (this._hass && this._hass.entities) || {};
    return this._config.devices.map((d) => {
      if (d.placement !== "side") return null;
      if (PWM_FLOW_ROLES.includes(d.role)) return d.role;
      const entry = entities[d.entity];
      return entry && entry.platform === PWM_DOMAIN ? PWM_FLOW_ROLE_KEYS[entry.translation_key] || null : null;
    });
  }

  _layoutKey(visible, roles) {
    return visible.join() + "|" + roles.join();
  }

  /** Issue #113: start or speed up a line's dots -- only on a real change,
   * since resetting --dur restarts the animation. */
  _animate(line, cache, key, watts, inward) {
    line.classList.add("active");
    line.classList.toggle("in", inward);
    const duration = pwmFlowDuration(watts) * (this._flowTime || 1);
    if (!cache[key] || Math.abs(cache[key] - duration) / cache[key] > 0.12) {
      cache[key] = duration;
      line.style.setProperty("--dur", duration.toFixed(2) + "s");
    }
  }

  /**
   * Issue #121: with direction_display "arrow" the grid and the battery (and
   * any other device below zero) show an arrow towards the house when power
   * flows into it, away from it otherwise -- never for the solar, the device
   * by the grid or anything without power flowing.
   */
  _arrow(d, i, watts, min) {
    const mode = d.direction_display || this._config.direction_display || "sign";
    if (mode !== "arrow" || watts === null || watts === 0 || Math.abs(watts) < min) return null;
    if (i === this._besideIndex || !this._toHouse || !this._toHouse[i]) return null;
    const role = Object.keys(this._roleIndex || {}).find((r) => this._roleIndex[r] === i);
    if (role === "pv" || (role !== "grid" && role !== "battery" && watts > 0)) return null;
    const positiveIn = d.flow ? d.flow === "in" : d.placement === "side";
    const intoHouse = positiveIn ? watts > 0 : watts < 0;
    const opposite = { up: "down", down: "up", left: "right", right: "left" };
    return intoHouse ? this._toHouse[i] : opposite[this._toHouse[i]];
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    const event = new Event("hass-more-info", { bubbles: true, composed: true });
    event.detail = { entityId };
    this.dispatchEvent(event);
  }

  /** `arrow` (#121): "up"/"down"/"left"/"right" shows the power without its sign behind that arrow. */
  _fill(node, cfg, watts, language, defaultIcon, arrow) {
    const state = this._hass.states[cfg.entity];
    node.icon.setAttribute("icon", cfg.icon || (state && state.attributes.icon) || defaultIcon);
    node.val.textContent = pwmFlowFormatPower(arrow && watts !== null ? Math.abs(watts) : watts, language);
    if (arrow) node.dir.setAttribute("icon", "mdi:arrow-" + arrow);
    node.dir.style.display = arrow ? "" : "none";
    const name = cfg.hide_name ? "" : cfg.name || (state && state.attributes.friendly_name) || "";
    // With name_on_arc the name sits on the arc above the circle instead of inside it.
    if (node.arc) node.arc.textContent = name;
    node.name.textContent = node.arc ? "" : name;
    node.name.style.display = name && !node.arc ? "" : "none";
    const secondary = pwmFlowSecondaryText(this._hass, cfg.secondary);
    node.sec.textContent = secondary;
    node.sec.style.display = secondary ? "" : "none";
  }

  _devicePower(device) {
    let watts = pwmFlowToWatt(this._hass.states[device.entity]);
    if (watts !== null && device.invert) watts = -watts;
    return watts;
  }

  _update() {
    const h = this._hass;
    const c = this._config;
    if (!h || !c || !this._nodes) return;
    const language = (h.locale && h.locale.language) || h.language || "en";
    const min = Number(c.min_power) || 0;

    // Hide devices below their hide_below limit -- rebuild when that changes.
    const powers = c.devices.map((d) => this._devicePower(d));
    const visible = c.devices.map((d, i) => {
      const limit = Number(d.hide_below);
      if (!(limit > 0)) return true;
      return powers[i] !== null && Math.abs(powers[i]) >= limit;
    });
    if (this._layoutKey(visible, this._sideRoles()) !== this._visibleKey) this._build(visible);

    // Issue #113: with solar, grid and battery in fixed places, their power
    // is split between each other and the house (positive = into the house,
    // the left column's default direction).
    const role = (name) => (this._roleIndex ? this._roleIndex[name] : undefined);
    const inward = (i) => {
      if (i === undefined || powers[i] === null) return 0;
      return c.devices[i].flow === "out" ? -powers[i] : powers[i];
    };
    // Issue #119: the device by the grid (consumption > 0). When the grid
    // circle's meter counts it too, only the rest of the grid is the house's.
    const bi = this._besideIndex;
    const byGrid = bi !== undefined && powers[bi] !== null
      ? Math.max(0, c.devices[bi].flow === "in" ? -powers[bi] : powers[bi])
      : 0;
    const gridCounted = bi !== undefined && c.devices[bi].grid_counted !== false;
    const split = this._roleIndex && Object.keys(this._roleIndex).length
      ? pwmFlowSplit(inward(role("pv")), inward(role("grid")) - (gridCounted ? byGrid : 0), inward(role("battery")))
      : null;
    // What the house sells covers it first (solar, then battery); the rest comes from the grid.
    let byGridSource = "grid";
    if (split && bi !== undefined) {
      const fromPv = Math.min(split.pvToGrid, byGrid);
      const fromBattery = Math.min(split.batteryToGrid, byGrid - fromPv);
      const fromGrid = byGrid - fromPv - fromBattery;
      const most = Math.max(fromPv, fromBattery, fromGrid);
      byGridSource = most === fromPv ? "pv" : most === fromBattery ? "battery" : "grid";
    }
    const color = (i) => (i === undefined ? "#fff" : c.devices[i].color || PWM_FLOW_COLORS[i % PWM_FLOW_COLORS.length]);
    const houseShare = {};
    if (split) {
      if (role("pv") !== undefined) houseShare[role("pv")] = split.pvToHouse;
      if (role("grid") !== undefined) houseShare[role("grid")] = split.gridToHouse;
      if (role("battery") !== undefined) houseShare[role("battery")] = split.batteryToHouse;
    }

    const title = this.shadowRoot.querySelector(".title");
    const date = this.shadowRoot.querySelector(".date");
    title.textContent = c.title || "";
    let dateText = "";
    if (c.show_date) {
      dateText = new Date().toLocaleDateString(language, { weekday: "long", day: "numeric", month: "long" });
      dateText = dateText.charAt(0).toUpperCase() + dateText.slice(1);
    }
    date.textContent = dateText;
    title.style.display = c.title ? "" : "none";
    this.shadowRoot.querySelector(".header").style.display = c.title || dateText ? "" : "none";

    // The middle shows the house's power minus the devices marked "subtract" (display only).
    let centerWatts = pwmFlowToWatt(h.states[c.center.entity]);
    if (centerWatts !== null) {
      const subtracted = c.devices.reduce((sum, d, i) => sum + (d.subtract && powers[i] !== null ? powers[i] : 0), 0);
      if (subtracted) centerWatts = Math.max(0, centerWatts - subtracted);
    }
    this._fill(this._center, { ...c.center, hide_name: c.center.hide_name ?? !c.center.name }, centerWatts, language, "mdi:home");

    c.devices.forEach((d, i) => {
      if (!this._nodes[i]) return; // hidden
      const watts = powers[i];
      this._fill(this._nodes[i], d, watts, language, "mdi:flash", this._arrow(d, i, watts, min));

      const line = this._lines[i];
      const active = watts !== null && Math.abs(watts) >= min;
      this._nodes[i].el.classList.toggle("idle", !active);
      // Issue #113: solar/grid/battery's line to the house only carries the
      // house's share -- the rest runs on the lines between them.
      const share = houseShare[i];
      const amount = i === bi ? byGrid : share !== undefined ? share : watts;
      // Issue #119: the grid's line to the device by the grid takes the colour
      // of where most of its power comes from.
      if (i === bi) {
        const from = role(byGridSource);
        line.style.setProperty("--c", amount && from !== undefined ? color(from) : color(i));
      }
      if (amount === null || !(Math.abs(amount) >= min) || amount === 0) {
        line.classList.remove("active");
        return;
      }
      let towardsHouse = d.flow ? d.flow === "in" : d.placement === "side";
      if (share !== undefined) towardsHouse = true;
      else if (i === bi) towardsHouse = false; // drawn from the grid out to the device
      else if (watts < 0) towardsHouse = !towardsHouse;
      this._animate(line, this._durations, i, amount, towardsHouse);
    });

    // Issue #113: the lines between solar, grid and battery, in the colour
    // of where the power comes from.
    const flows = {
      pv_grid: split ? [split.pvToGrid, 0] : [0, 0],
      grid_battery: split ? [split.gridToBattery, split.batteryToGrid] : [0, 0],
      pv_battery: split ? [split.pvToBattery, 0] : [0, 0],
    };
    const ends = { pv_grid: ["pv", "grid"], grid_battery: ["grid", "battery"], pv_battery: ["pv", "battery"] };
    Object.keys(this._links || {}).forEach((key) => {
      const line = this._links[key];
      const [forward, backward] = flows[key];
      const watts = Math.max(forward, backward);
      if (!(watts >= min) || watts === 0) {
        line.classList.remove("active");
        return;
      }
      const reverse = backward > forward;
      line.style.setProperty("--c", color(role(ends[key][reverse ? 1 : 0])));
      this._animate(line, this._linkDurations, key, watts, reverse);
    });
  }
}

const PWM_FLOW_CARD_STYLE = `
  :host { --pwm-flow-bg: #000; --pwm-flow-fg: #fff; }
  ha-card { padding: 12px; overflow: hidden; background: var(--pwm-flow-card-bg, var(--pwm-flow-bg));
            font-family: var(--pwm-flow-font, Montserrat, var(--ha-font-family-body, Roboto), sans-serif); }
  .header { display:flex; flex-wrap:wrap; justify-content:space-between; gap:4px 12px; padding:0 4px 4px;
            font-size:1.25em; font-weight:500; color:var(--pwm-flow-fg); }
  .stage { position:relative; width:100%; margin:0 auto; container-type:inline-size; }
  svg { position:absolute; inset:0; width:100%; height:100%; overflow:visible; }
  .line path { fill:none; stroke:var(--c); }
  .line .base { stroke-width:.3; opacity:.3; transition:opacity .4s; }
  .line.active .base { opacity:.9; stroke-width:.4; }
  .line .dots { stroke-width:1.8; stroke-linecap:round; stroke-dasharray:0 5; opacity:0; }
  .line.active .dots { opacity:1; animation:pwm-flow var(--dur,1.5s) linear infinite; }
  .line.active.in .dots { animation-direction:reverse; }
  ha-card.themed { --pwm-flow-fg: var(--primary-text-color);
                   --pwm-flow-node-base: var(--ha-card-background, var(--card-background-color, #fff)); }
  ha-card.themed .node {
    background: radial-gradient(circle at 50% 40%, color-mix(in srgb, var(--c) 14%, var(--pwm-flow-node-base)) 0%,
                color-mix(in srgb, var(--c) 6%, var(--pwm-flow-node-base)) 75%); }
  .arc-name { fill:var(--pwm-flow-fg); font-weight:500; letter-spacing:.03em; }
  @keyframes pwm-flow { from { stroke-dashoffset:0; } to { stroke-dashoffset:-5; } }
  .node { position:absolute; transform:translate(-50%,-50%); aspect-ratio:1/1; border-radius:50%;
          box-sizing:border-box; border:calc(.45cqw * var(--k,1)) solid var(--c);
          background: radial-gradient(circle at 50% 40%, color-mix(in srgb, var(--c) 12%, #07090c) 0%,
                      color-mix(in srgb, var(--c) 5%, #030405) 75%);
          box-shadow: 0 0 calc(.9cqw * var(--k,1)) color-mix(in srgb, var(--c) 45%, transparent);
          display:flex; flex-direction:column; align-items:center; justify-content:center;
          text-align:center; padding:calc(1.6cqw * var(--k,1)); overflow:hidden; cursor:pointer;
          color:var(--pwm-flow-fg); -webkit-font-smoothing:antialiased; }
  /* Text scales with the circle's own size. */
  .node { container-type:inline-size; }
  .node ha-icon { --mdc-icon-size:calc(24cqw * var(--is,1)); color:var(--pwm-flow-fg); margin-bottom:2cqw; }
  /* --ts is the card's text_size option (1 = 100 %). */
  .val { font-weight:600; font-size:calc(16cqw * var(--ts,1)); line-height:1.15; white-space:nowrap; letter-spacing:.02em; }
  .val .dir { --mdc-icon-size:1em; width:1em; height:1em; margin-right:.12em; vertical-align:-.12em; }
  .name, .sec { font-size:calc(11cqw * var(--ts,1)); line-height:1.2; white-space:pre-line; }
  .name { font-weight:500; max-width:100%; overflow-wrap:anywhere; }
  .sec { font-weight:500; opacity:.92; }
  .center ha-icon { --mdc-icon-size:calc(24cqw * var(--is,1)); }
  .sec-above .sec { order:-1; margin-bottom:1cqw; }
  .sec-value .sec { font-weight:600; opacity:1; font-size:calc(16cqw * var(--ts,1)); line-height:1.15;
                    letter-spacing:.02em; white-space:pre; }
  .sec-value .center .sec { font-size:calc(14cqw * var(--ts,1)); }
  .center .val { font-size:calc(14cqw * var(--ts,1)); }
  .center .name, .center .sec { font-size:calc(10cqw * var(--ts,1)); }
`;

pwmDefine("pwm-power-flow-card", PwMPowerFlowCard);

/** Built per render: the layout dropdown follows the user's language. */
function pwmFlowGeneralSchema(t) {
  return [
    { type: "grid", name: "", schema: [
      { name: "title", selector: { text: {} } },
      { name: "background", selector: { text: {} } },
    ] },
    { name: "layout", selector: { select: { mode: "dropdown", options: [
      { value: "column", label: t.layoutColumn },
      { value: "cross", label: t.layoutCross },
    ] } } },
    { type: "grid", name: "", schema: [
      { name: "show_date", selector: { boolean: {} } },
      { name: "min_power", selector: { number: { min: 0, max: 1000, mode: "box", unit_of_measurement: "W" } } },
      { name: "node_size", selector: { number: { min: 12, max: 40, step: 1, mode: "slider" } } },
      { name: "center_size", selector: { number: { min: 16, max: 40, step: 1, mode: "slider" } } },
      { name: "text_size", selector: { number: { min: 50, max: 200, step: 5, mode: "slider" } } },
      { name: "icon_size", selector: { number: { min: 50, max: 200, step: 5, mode: "slider" } } },
      { name: "flow_time", selector: { number: { min: 25, max: 400, step: 5, mode: "slider" } } },
      { name: "name_on_arc", selector: { boolean: {} } },
      { name: "secondary_above", selector: { boolean: {} } },
      { name: "header_size", selector: { number: { min: 10, max: 40, mode: "box", unit_of_measurement: "px" } } },
      { name: "scale", selector: { number: { min: 50, max: 100, step: 1, mode: "slider" } } },
    ] },
    { name: "secondary_size", selector: { select: { mode: "dropdown", options: [
      { value: "small", label: t.secondarySmall },
      { value: "value", label: t.secondaryValue },
    ] } } },
    { name: "direction_display", selector: { select: { mode: "dropdown", options: [
      { value: "sign", label: t.directionSign },
      { value: "arrow", label: t.directionArrow },
    ] } } },
  ];
}

const PWM_FLOW_CENTER_SCHEMA = [
  { name: "entity", selector: { entity: {} } },
  { type: "grid", name: "", schema: [
    { name: "name", selector: { text: {} } },
    { name: "icon", selector: { icon: {} } },
    { name: "color", selector: { text: {} } },
  ] },
  { name: "secondary", selector: { entity: { multiple: true } } },
];

/** Built per render: the dropdown labels follow the user's language. */
function pwmFlowDeviceSchema(t) {
  return [
    { name: "entity", selector: { entity: {} } },
    { type: "grid", name: "", schema: [
      { name: "name", selector: { text: {} } },
      { name: "icon", selector: { icon: {} } },
      { name: "color", selector: { text: {} } },
      { name: "placement", selector: { select: { mode: "dropdown", options: [
        { value: "ring", label: t.placementRing },
        { value: "side", label: t.placementSide },
        { value: "grid", label: t.placementGrid },
      ] } } },
      { name: "flow", selector: { select: { mode: "dropdown", options: [
        { value: "out", label: t.flowOut },
        { value: "in", label: t.flowIn },
      ] } } },
      { name: "role", selector: { select: { mode: "dropdown", options: [
        { value: "auto", label: t.roleAuto },
        { value: "pv", label: t.rolePv },
        { value: "grid", label: t.roleGrid },
        { value: "battery", label: t.roleBattery },
      ] } } },
      { name: "invert", selector: { boolean: {} } },
      { name: "hide_name", selector: { boolean: {} } },
      { name: "subtract", selector: { boolean: {} } },
      { name: "grid_counted", selector: { boolean: {} } },
      { name: "direction_display", selector: { select: { mode: "dropdown", options: [
        { value: "card", label: t.directionCard },
        { value: "sign", label: t.directionSign },
        { value: "arrow", label: t.directionArrow },
      ] } } },
      { name: "hide_below", selector: { number: { min: 0, max: 100000, mode: "box", unit_of_measurement: "W" } } },
    ] },
    { name: "secondary", selector: { entity: { multiple: true } } },
  ];
}

class PwMPowerFlowCardEditor extends HTMLElement {
  setConfig(config) {
    const json = pwmFlowStableJson(config);
    if (json === this._lastJson) return; // our own change -- re-rendering would steal focus
    this._config = JSON.parse(JSON.stringify(config)); // our own, unfrozen copy
    this._config.center = this._config.center || {};
    this._config.devices = this._config.devices || [];
    this._lastJson = json;
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this.querySelectorAll("ha-form").forEach((form) => (form.hass = hass));
    if (first) this._render(); // the texts follow hass's language
  }

  connectedCallback() {
    this._ensureComponents().then(() => this._render());
  }

  /** ha-form is lazy-loaded by HA; loading a built-in card's editor pulls it in. */
  async _ensureComponents() {
    if (customElements.get("ha-form") && customElements.get("ha-selector")) return;
    try {
      const helpers = await window.loadCardHelpers();
      const card = await helpers.createCardElement({ type: "entities", entities: [] });
      if (card.constructor.getConfigElement) await card.constructor.getConfigElement();
    } catch (e) {
      // The editor still renders; HA loads ha-form itself on most pages.
    }
  }

  _fire() {
    // Always send a copy -- Home Assistant freezes the object it receives.
    const out = JSON.parse(JSON.stringify(this._config));
    this._lastJson = pwmFlowStableJson(out);
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail: { config: out }, bubbles: true, composed: true,
    }));
  }

  _form(container, schema, data, labels, onChange) {
    const form = document.createElement("ha-form");
    form.hass = this._hass;
    form.schema = schema;
    form.data = data;
    form.computeLabel = (s) => labels[s.name] || s.name;
    form.addEventListener("value-changed", (event) => {
      event.stopPropagation();
      form.data = event.detail.value;
      onChange(event.detail.value);
    });
    container.appendChild(form);
  }

  _render() {
    if (!this._config || !this.isConnected) return;
    const c = this._config;
    const t = pwmFlowTexts(this._hass);
    this.innerHTML = `
      <style>
        .pwm-flow-sect { margin-bottom: 18px; }
        .pwm-flow-sect h3 { margin: 0 0 8px; font-size: 1.05em; }
        .pwm-flow-dev { border: 1px solid var(--divider-color); border-radius: 10px; padding: 10px; margin-bottom: 10px; }
        .pwm-flow-dev-head { display:flex; align-items:center; gap:6px; margin-bottom:6px; font-weight:600; }
        .pwm-flow-dev-head span { flex:1; }
        .pwm-flow-btn { background:none; border:1px solid var(--divider-color); border-radius:6px;
                        color:var(--primary-text-color); cursor:pointer; padding:2px 8px; font-size:14px; }
        .pwm-flow-add { width:100%; padding:8px; background:var(--primary-color); color:var(--text-primary-color);
                        border:none; border-radius:8px; cursor:pointer; font-size:14px; }
        .pwm-flow-hint { font-size: 12px; opacity: .7; margin-top: 4px; }
      </style>
      <div class="pwm-flow-sect"><h3></h3><div id="g"></div></div>
      <div class="pwm-flow-sect"><h3></h3><div id="c"></div></div>
      <div class="pwm-flow-sect"><h3></h3><div id="d"></div>
        <button class="pwm-flow-add" id="add"></button>
        <div class="pwm-flow-hint"></div>
      </div>`;
    const headings = this.querySelectorAll("h3");
    headings[0].textContent = t.general;
    headings[1].textContent = t.center;
    headings[2].textContent = t.devices;
    this.querySelector("#add").textContent = t.add;
    this.querySelector(".pwm-flow-hint").textContent = t.secondaryHint;

    const generalKeys = ["title", "background", "layout", "show_date", "min_power", "node_size", "center_size",
      "text_size", "icon_size", "flow_time", "name_on_arc", "secondary_above", "header_size", "scale", "secondary_size", "direction_display"];
    this._form(this.querySelector("#g"), pwmFlowGeneralSchema(t),
      { title: c.title, background: c.background, layout: c.layout || "column",
        show_date: c.show_date, min_power: c.min_power,
        node_size: c.node_size || 26, center_size: c.center_size || 32,
        text_size: c.text_size || 100, icon_size: c.icon_size || 100, flow_time: c.flow_time || 100,
        name_on_arc: c.name_on_arc, secondary_above: c.secondary_above,
        header_size: c.header_size, scale: c.scale || 100, secondary_size: c.secondary_size || "small",
        direction_display: c.direction_display || "sign" },
      t.labels,
      (v) => {
        generalKeys.forEach((key) => {
          if (v[key] === undefined || v[key] === "") delete c[key]; else c[key] = v[key];
          // Leave the defaults out of the YAML.
          if (key === "scale" && Number(c.scale) >= 100) delete c.scale;
          if (key === "secondary_size" && c.secondary_size === "small") delete c.secondary_size;
          if (key === "direction_display" && c.direction_display === "sign") delete c.direction_display;
        });
        this._fire();
      });

    this._form(this.querySelector("#c"), PWM_FLOW_CENTER_SCHEMA,
      { ...c.center, secondary: pwmFlowSecondaryList(c.center.secondary).map((s) => s.entity) },
      t.labels,
      (v) => {
        c.center = pwmFlowClean({ ...v, secondary: pwmFlowMergeSecondary(v.secondary, c.center.secondary) });
        this._fire();
      });

    const list = this.querySelector("#d");
    const deviceSchema = pwmFlowDeviceSchema(t);
    c.devices.forEach((d, i) => {
      const box = document.createElement("div");
      box.className = "pwm-flow-dev";
      const title = () => {
        const state = this._hass && this._hass.states[d.entity];
        return `#${i + 1} ${d.name || (state ? state.attributes.friendly_name : d.entity || t.newDevice)}`;
      };
      box.innerHTML = `<div class="pwm-flow-dev-head"><span></span>
        <button class="pwm-flow-btn" data-a="up">▲</button>
        <button class="pwm-flow-btn" data-a="down">▼</button>
        <button class="pwm-flow-btn" data-a="del">✕</button></div>`;
      const buttonTitles = { up: t.moveUp, down: t.moveDown, del: t.remove };
      const head = box.querySelector("span");
      head.textContent = title();
      box.querySelectorAll("button").forEach((button) => {
        button.title = buttonTitles[button.dataset.a];
        button.addEventListener("click", () => {
          const action = button.dataset.a;
          if (action === "del") c.devices.splice(i, 1);
          if (action === "up" && i > 0) [c.devices[i - 1], c.devices[i]] = [c.devices[i], c.devices[i - 1]];
          if (action === "down" && i < c.devices.length - 1) [c.devices[i + 1], c.devices[i]] = [c.devices[i], c.devices[i + 1]];
          this._fire();
          this._render();
        });
      });
      list.appendChild(box);
      this._form(box, deviceSchema,
        { ...d, placement: d.placement || "ring", flow: d.flow || (d.placement === "side" ? "in" : "out"),
          role: d.role || "auto",
          grid_counted: d.grid_counted !== false,
          direction_display: d.direction_display || "card",
          secondary: pwmFlowSecondaryList(d.secondary).map((s) => s.entity) },
        t.labels,
        (v) => {
          const next = pwmFlowClean({ ...v, secondary: pwmFlowMergeSecondary(v.secondary, d.secondary) });
          const placementChanged = (v.placement || "ring") !== (d.placement || "ring");
          if (placementChanged) delete next.flow; // use the new placement's default direction
          // Leave defaults out of the YAML.
          if (next.placement === "ring") delete next.placement;
          if (next.role === "auto" || next.placement !== "side") delete next.role;
          if (next.placement !== "grid" || next.grid_counted !== false) delete next.grid_counted;
          if (next.direction_display === "card") delete next.direction_display;
          if (next.flow === (next.placement === "side" ? "in" : "out")) delete next.flow;
          ["invert", "hide_name", "subtract"].forEach((key) => { if (!next[key]) delete next[key]; });
          if (!(Number(next.hide_below) > 0)) delete next.hide_below;
          c.devices[i] = next;
          d = next;
          head.textContent = title();
          this._fire();
          if (placementChanged) this._render();
        });
    });

    this.querySelector("#add").addEventListener("click", () => {
      c.devices.push({});
      this._fire();
      this._render();
    });
  }
}
pwmDefine("pwm-power-flow-card-editor", PwMPowerFlowCardEditor);

/** Battery plan card texts (issue #122) -- same language pick as PWM_BATTERY_TEXTS. */
const PWM_PLAN_TEXTS = {
  en: {
    title: "Battery plan",
    soc: "SoC",
    minSoc: "Min SoC",
    planned: "Planned min SoC",
    target: "Target",
    usable: "May be used",
    held: "Kept (the rule's colour)",
    now: "now",
    today: "Today",
    tomorrow: "Tomorrow",
    waiting: ["Waiting for", "prices"],
    floor: (pct) => `Min SoC ${pct} %`,
    earlier: "Earlier today",
    priceAt: (hour) => `Price at ${hour}:00`,
    cellPrice: "Kept grid energy bought at",
    saving: "Saving by using it now",
    belowMargin: (margin) => `under the limit of ${margin}`,
    usedFrom: "Used when the hour's price is above",
    allFrom: (price) => `all of it above ${price}`,
    heldText: (points, price) =>
      `${points} % of the battery is grid energy bought at ${price}, and using it now saves too little, so it is kept for a dearer hour.`,
    kr: (value) => `${value} kr.`,
    planNote:
      "After now it is the plan, with the SoC as it is now: a car, a blocked charge and the SoC itself change it.",
    rules: {
      free: ["Free – the battery may be used", "The battery may be used all the way down to its minimum SoC."],
      hold: ["Held at its SoC (winter)", "In winter (Nov–Jan) the battery is held at its SoC 07–16, so the sun's energy is kept for the evening."],
      charge_curve: ["Follows the charge curve to the target", "09–16 the battery is held at least on the charge curve, so it reaches the target by the target end time."],
      target: ["Target reached – held", "The battery has reached its target and is held there until 17:00."],
      target_protection: ["The night charge is kept", "Energy from the night's grid charge isn't used before the target end time, so it lasts into the dear hours."],
      night_protection: ["Kept – too cheap to use now", "Grid energy in the battery is only used when the hour's price is at least 0.30 kr. above what it cost. Otherwise it is kept for a dearer hour."],
      day_plan: ["Day plan – dear hour", "Last night's charge also covers this dear hour, so the battery may be used now. The rest is kept for the evening."],
      pv_release: ["PV energy used in a dear hour", "Solar energy above the evening reserve is used in today's dearest hours instead of after 21:00."],
      sale: ["Selling from the battery", "The battery sells to the grid in a dear hour, down to the hour's floor."],
      grid_charge: ["Grid charge", "The battery charges from the grid in a cheap hour, up to tonight's charge level."],
      car: ["Car charging – battery held", "A car is charging, so the battery is held and doesn't cover the car."],
      blocked: ["Charging blocked", "Charging is blocked, and the battery is held at its SoC."],
      unknown: ["Waiting for tomorrow's prices", "Tomorrow's prices come around noon. Only then is the night planned."],
      none: ["No rule recorded", "PwMngt hasn't run in this hour since Home Assistant started."],
    },
    missing: "PwMngt's battery entities were not found – is Solar PV Plant set up?",
    noPlan: "Battery data has no plan yet – it comes with the next run.",
    editor: { title: "Title", header_size: "Header size (px) - like the other cards", height: "Height (%)", scale: "Scale (%)", text_size: "Text size (%)" },
  },
  da: {
    title: "Batteriplan",
    soc: "SoC",
    minSoc: "Min-SoC",
    planned: "Planlagt min-SoC",
    target: "Target",
    usable: "Må bruges",
    held: "Gemmes (regelens farve)",
    now: "nu",
    today: "I dag",
    tomorrow: "I morgen",
    waiting: ["Afventer", "priser"],
    floor: (pct) => `Min-SoC ${pct} %`,
    earlier: "Tidligere i dag",
    priceAt: (hour) => `Timepris kl. ${hour}`,
    cellPrice: "Gemt netstrøm købt til",
    saving: "Besparelse ved at bruge den nu",
    belowMargin: (margin) => `under grænsen på ${margin}`,
    usedFrom: "Bruges, når timeprisen er over",
    allFrom: (price) => `det hele over ${price}`,
    heldText: (points, price) =>
      `${points} % af batteriet er netstrøm, der blev købt til ${price}, og det sparer for lidt at bruge den nu, så den gemmes til en dyrere time.`,
    kr: (value) => `${value} kr.`,
    planNote:
      "Efter nu er det planen med SoC, som den er nu: en bil, blokeret ladning og selve SoC kan ændre den.",
    rules: {
      free: ["Fri – batteriet må bruges", "Batteriet må bruges helt ned til sin minimum-SoC."],
      hold: ["Holdes på sin SoC (vinter)", "Om vinteren (nov.–jan.) holdes batteriet på sin SoC kl. 07–16, så solens strøm gemmes til aftenen."],
      charge_curve: ["Følger ladekurven mod target", "Kl. 09–16 holdes batteriet mindst på ladekurven, så det når target ved target-sluttid."],
      target: ["Target nået – holdes", "Batteriet har nået target og holdes der til kl. 17."],
      target_protection: ["Natladningen holdes", "Strøm fra nattens netladning bruges ikke før target-sluttid, så den rækker til de dyre timer."],
      night_protection: ["Gemmes – for billig til at bruge nu", "Netstrøm i batteriet bruges kun, når timeprisen er mindst 0,30 kr. højere, end strømmen kostede. Ellers gemmes den til en dyrere time."],
      day_plan: ["Dagplan – dyr time", "Nattens ladning dækker også denne dyre time, så batteriet må bruges nu. Resten gemmes til aftenen."],
      pv_release: ["PV-strøm bruges i en dyr time", "Solstrøm over aftenreserven bruges i dagens dyreste timer i stedet for efter kl. 21."],
      sale: ["Salg fra batteriet", "Batteriet sælger til nettet i en dyr time ned til timens gulv."],
      grid_charge: ["Netladning", "Batteriet lades fra nettet i en billig time op til nattens ladeprocent."],
      car: ["Bil lader – batteriet holdes", "En bil lader, så batteriet holdes og ikke dækker bilen."],
      blocked: ["Ladning blokeret", "Ladning er blokeret, og batteriet holdes på sin SoC."],
      unknown: ["Afventer morgendagens priser", "Morgendagens priser kommer ved middagstid. Først da planlægges natten."],
      none: ["Ingen regel registreret", "PwMngt har ikke kørt i den time, siden Home Assistant startede."],
    },
    missing: "PwMngt's batteri-entities blev ikke fundet – er Solar PV Plant sat op?",
    noPlan: "Battery data har ingen plan endnu – den kommer ved næste kørsel.",
    editor: { title: "Titel", header_size: "Overskriftens størrelse (px) - som de andre kort", height: "Højde (%)", scale: "Skala (%)", text_size: "Tekststørrelse (%)" },
  },
};

// One colour per rule (battery_data.py's RULE_* keys); "unknown" is drawn
// hatched grey.
const PWM_PLAN_RULE_COLORS = {
  grid_charge: "#1e88e5",
  target_protection: "#2e7d32",
  night_protection: "#5c6bc0",
  day_plan: "#26a69a",
  pv_release: "#f9a825",
  sale: "#ab47bc",
  charge_curve: "#ffb300",
  target: "#66bb6a",
  hold: "#8d6e63",
  free: "#9e9e9e",
  car: "#ef6c00",
  blocked: "#e53935",
};
const PWM_PLAN_UNKNOWN_COLOR = "#9e9e9e";
const PWM_PLAN_USABLE_COLOR = "#1baf7a";

const PWM_PLAN_CARD_STYLE = `
  ha-card { padding: 0 0 12px; }
  .wrap { padding: 0 12px; position: relative; }
  .hit { fill: transparent; cursor: pointer; }
  svg { touch-action: pan-y; }
  .cursor { pointer-events: none; }
  .cursor line { stroke: var(--primary-text-color); stroke-opacity: 0.5; stroke-width: 1; }
  .cursor circle { stroke: var(--card-background-color, #fff); stroke-width: 1.5; }
  .dot-soc { fill: var(--primary-color); }
  .dot-min, .dot-planned { fill: var(--error-color, #e53935); }
  .dot-target { fill: var(--success-color, #43a047); }
  .values td i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
  .values .time { font-weight: 500; }
  .tip hr { border: none; border-top: 1px solid var(--divider-color); margin: 8px 0; }
  .hit:hover { fill: var(--primary-text-color); fill-opacity: 0.06; }
  .info.tip { position: absolute; z-index: 1; display: none; width: max-content; max-width: min(320px, 80%); margin: 0;
    pointer-events: none; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35); border: 1px solid var(--divider-color);
    background: var(--card-background-color, var(--secondary-background-color)); }
  svg { width: 100%; display: block; margin: 0 auto; overflow: visible; }
  .grid line { stroke: var(--divider-color); stroke-width: 1; }
  .axis text { fill: var(--secondary-text-color); font-size: calc(11px * var(--ts, 1)); }
  .soc { fill: none; stroke: var(--primary-color); stroke-width: 2; }
  .min { fill: none; stroke: var(--error-color, #e53935); stroke-width: 2; }
  .min.planned { stroke-dasharray: 5 4; }
  .target { stroke: var(--success-color, #43a047); stroke-width: 1.5; stroke-dasharray: 2 4; }
  .now { stroke: var(--primary-text-color); stroke-width: 1; stroke-dasharray: 3 3; }
  .now-label { fill: var(--primary-text-color); font-size: calc(11px * var(--ts, 1)); }
  .midnight { stroke: var(--secondary-text-color); stroke-opacity: 0.4; }
  .waiting { fill: var(--secondary-text-color); font-size: calc(11px * var(--ts, 1)); }
  .band { cursor: pointer; }
  .band.selected { stroke: var(--primary-text-color); stroke-width: 1.5; }
  .info { margin-top: 6px; padding: 10px 12px; border-radius: 8px; font-size: calc(13px * var(--ts, 1));
    background: var(--secondary-background-color); color: var(--primary-text-color); }
  .info .head { font-weight: 500; }
  .info .head i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }
  .info .desc { color: var(--secondary-text-color); margin-top: 4px; }
  .info table { margin-top: 6px; font-size: calc(12px * var(--ts, 1)); border-collapse: collapse; }
  .info td { padding: 2px 12px 2px 0; }
  .info td:first-child { color: var(--secondary-text-color); }
  .days { display: flex; gap: 6px; padding: 0 12px 8px; }
  .days button { font: inherit; font-size: calc(13px * var(--ts, 1)); padding: 3px 12px; border-radius: 999px;
    border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); cursor: pointer; }
  .days button.active { background: var(--primary-color); border-color: var(--primary-color);
    color: var(--text-primary-color, #fff); }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 8px; font-size: calc(12px * var(--ts, 1));
    color: var(--secondary-text-color); }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }
  .legend span.rule { cursor: pointer; }
  .legend i { display: inline-block; width: 12px; height: 10px; border-radius: 2px; }
  .legend i.line { height: 0; width: 14px; border-top: 2px solid; border-radius: 0; }
  .legend i.dash { height: 0; width: 14px; border-top: 2px dashed; border-radius: 0; }
  .note { margin-top: 6px; font-size: calc(11px * var(--ts, 1)); color: var(--secondary-text-color); }
  .pwm-warning { color: var(--error-color); font-size: calc(13px * var(--ts, 1)); padding: 0 16px; }
`;

// How often today's history is fetched again (ms).
const PWM_PLAN_HISTORY_REFRESH = 5 * 60 * 1000;
const PWM_PLAN_HOURS = 30; // "today": today 00:00 to tomorrow 06:00
const PWM_PLAN_TARGET_END_HOUR = 17; // the target rules end at 17:00 (battery_data.py)
const PWM_PLAN_DAY_HOURS = 24; // "tomorrow" (issue #123): tomorrow 00:00 to 24:00
const PWM_PLAN_PLOT_HEIGHT = 280; // SoC 0-100 % at height: 100, in viewBox units (600 wide)
const PWM_PLAN_HOUR_MS = 3600 * 1000;

/**
 * Battery plan (issue #122): battery_data's minimum-SoC rules hour by
 * hour, from midnight to 06:00 tomorrow -- the SoC and min SoC so far
 * (Recorder history), the planned min SoC from now (battery_data's
 * min_soc_plan, data/battery_plan.py), the target, the part of the battery
 * that may be used (green) and the part a rule keeps (hatched in the
 * rule's colour), and a strip with the rule of each hour. Tapping an hour
 * -- or a rule in the legend -- explains the rule. Finds its entities on
 * the PwM PV device, like the other PwM cards.
 */
class PwMBatteryPlanCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    // Like pwm-power-flow-card (issue #115): the shadow root's children are
    // created once and never replaced -- card-mod puts its own <card-mod>
    // element in here, and a rebuild only redraws .content.
    this.shadowRoot.innerHTML = `<style>${PWM_PLAN_CARD_STYLE}</style><ha-card><div class="content"></div></ha-card>`;
  }

  setConfig(config) {
    this._config = config || {};
    this._signature = undefined;
  }

  set hass(hass) {
    this._hass = hass;
    this._resolveEntityIds();
    this._maybeFetchHistory();
    const signature = this._stateSignature();
    if (signature === this._signature) return;
    this._signature = signature;
    this._render();
  }

  connectedCallback() {
    clearInterval(this._timer);
    this._timer = setInterval(() => {
      this._historyFetched = 0;
      if (this._hass) this._maybeFetchHistory();
    }, PWM_PLAN_HISTORY_REFRESH);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return 8;
  }

  /**
   * The visual editor: HA builds it from this schema (ha-form), so the
   * card needs no editor element of its own. HA passes no hass to
   * computeLabel, so the language is read from the page's own.
   */
  static getConfigForm() {
    const hass = (document.querySelector("home-assistant") || {}).hass || {};
    const language = String((hass.locale && hass.locale.language) || hass.language || "en").split("-")[0];
    const labels = (PWM_PLAN_TEXTS[language] || PWM_PLAN_TEXTS.en).editor;
    return {
      schema: [
        { type: "grid", name: "", schema: [
          { name: "title", selector: { text: {} } },
          { name: "header_size", selector: { number: { min: 10, max: 40, mode: "box", unit_of_measurement: "px" } } },
          { name: "height", selector: { number: { min: 50, max: 300, step: 5, mode: "slider" } } },
          { name: "scale", selector: { number: { min: 50, max: 100, step: 1, mode: "slider" } } },
          { name: "text_size", selector: { number: { min: 50, max: 200, step: 5, mode: "slider" } } },
        ] },
      ],
      computeLabel: (schema) => labels[schema.name] || schema.name,
    };
  }

  _language() {
    return String((this._hass.locale && this._hass.locale.language) || this._hass.language || "en");
  }

  _texts() {
    return PWM_PLAN_TEXTS[this._language().split("-")[0]] || PWM_PLAN_TEXTS.en;
  }

  _resolveEntityIds() {
    if (this._resolvedEntities === this._hass.entities && this._resolvedDevices === this._hass.devices) return;
    this._resolvedEntities = this._hass.entities;
    this._resolvedDevices = this._hass.devices;
    this._ids = {
      data: pwmFindEntityId(this._hass, "pv", "sensor", "battery_data"),
      soc: pwmFindEntityId(this._hass, "pv", "sensor", "battery_soc"),
      target: pwmFindEntityId(this._hass, "pv", "sensor", "battery_nightly_target"),
      empty: pwmFindEntityId(this._hass, "pv", "select", "battery_min_soc"),
    };
    this._historyFetched = 0;
    this._signature = undefined;
  }

  _state(name) {
    const entityId = this._ids[name];
    return entityId ? this._hass.states[entityId] : undefined;
  }

  _stateSignature() {
    const data = this._state("data");
    return [
      data ? `${data.state}|${JSON.stringify(data.attributes.min_soc_plan)}` : "-",
      ...["soc", "target", "empty"].map((name) => (this._state(name) ? this._state(name).state : "-")),
      this._historyVersion || 0,
      this._selected,
      this._day,
      this._language(),
      Math.floor(Date.now() / 60000),
    ].join("#");
  }

  /** Today's SoC and min SoC from the Recorder, at most every 5 min. */
  _maybeFetchHistory() {
    if (!this._ids.soc || !this._ids.data || this._fetching) return;
    if (this._historyFetched && Date.now() - this._historyFetched < PWM_PLAN_HISTORY_REFRESH) return;
    this._fetching = true;
    this._hass
      .callWS({
        type: "history/history_during_period",
        start_time: this._midnight().toISOString(),
        entity_ids: [this._ids.soc, this._ids.data],
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      })
      .then((result) => {
        const points = (entityId) =>
          (result[entityId] || [])
            .map((item) => ({ t: Number(item.lu) * 1000, v: Number(item.s) }))
            .filter((p) => Number.isFinite(p.v));
        this._history = { soc: points(this._ids.soc), min: points(this._ids.data) };
        this._historyVersion = (this._historyVersion || 0) + 1;
        this._signature = undefined;
        this._render();
      })
      .catch(() => {
        // No history (e.g. Recorder off): the card shows the plan alone.
      })
      .finally(() => {
        this._historyFetched = Date.now();
        this._fetching = false;
      });
  }

  _midnight() {
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    return midnight;
  }

  _kr(value) {
    const text = Number(value).toLocaleString(this._language(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return this._texts().kr(text);
  }

  _render() {
    const t = this._texts();
    const haCard = this.shadowRoot.querySelector("ha-card");
    haCard.header = this._config.title || t.title;
    // The header and text sizes as in pwm-power-flow-card (issue #120).
    const headerSize = Number(this._config.header_size);
    haCard.style.setProperty("--ha-card-header-font-size", headerSize > 0 ? `${headerSize}px` : "");
    const textSize = Math.max(50, Math.min(200, Number(this._config.text_size) || 100));
    haCard.style.setProperty("--ts", String(textSize / 100));
    const card = this.shadowRoot.querySelector(".content");
    card.innerHTML = "";

    const data = this._state("data");
    if (!data || !this._state("soc")) {
      card.insertAdjacentHTML("beforeend", `<div class="pwm-warning">${t.missing}</div>`);
      return;
    }
    const fullPlan = Array.isArray(data.attributes.min_soc_plan) ? data.attributes.min_soc_plan : null;
    if (!fullPlan || !fullPlan.length) {
      card.insertAdjacentHTML("beforeend", `<div class="pwm-warning">${t.noPlan}</div>`);
      return;
    }
    // Issue #123: tomorrow on its own once its plan is known (tomorrow's
    // prices, ~13:00) -- and back to today after midnight.
    const midnight = this._midnight();
    const today = midnight.getTime();
    // The plan's hours by their own start (local date and hour), not their
    // place in the list: Battery data only runs between 00:00 and 06:00 when
    // something changes, so just after midnight the plan may still be
    // yesterday's. An hour the plan doesn't have is "no rule" when it's
    // over, else "waiting for prices".
    const hourKey = (date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}-${date.getHours()}`;
    const byHour = new Map(fullPlan.map((entry) => [hourKey(new Date(entry.start)), entry]));
    const planFrom = (firstHour, count) =>
      Array.from({ length: count }, (_, i) => {
        const hour = firstHour + i;
        const time = new Date(midnight.getFullYear(), midnight.getMonth(), midnight.getDate() + Math.floor(hour / 24), hour % 24);
        const entry = byHour.get(hourKey(time));
        if (entry) return entry;
        return time.getTime() + PWM_PLAN_HOUR_MS <= Date.now()
          ? { rule: null, floor: null, planned: false }
          : { rule: "unknown", floor: null, planned: true };
      });
    const tomorrowPlan = planFrom(24, PWM_PLAN_DAY_HOURS);
    const hasTomorrow = tomorrowPlan.some((entry) => entry.rule !== "unknown");
    const showTomorrow = this._day === "tomorrow" && this._dayChosen === today && hasTomorrow;
    const hours = showTomorrow ? PWM_PLAN_DAY_HOURS : PWM_PLAN_HOURS;
    const plan = showTomorrow ? tomorrowPlan : planFrom(0, PWM_PLAN_HOURS);
    if (hasTomorrow) {
      const days = document.createElement("div");
      days.className = "days";
      days.innerHTML = [["today", t.today], ["tomorrow", t.tomorrow]]
        .map(([day, label]) => `<button data-day="${day}" class="${(day === "tomorrow") === showTomorrow ? "active" : ""}">${label}</button>`)
        .join("");
      days.querySelectorAll("button").forEach((button) => {
        button.addEventListener("click", () => {
          this._day = button.dataset.day;
          this._dayChosen = today;
          this._selected = undefined;
          this._signature = undefined;
          this._render();
        });
      });
      card.appendChild(days);
    }

    // Geometry: the plot, then the rule strip and the hour labels.
    // The drawing fills the card's width and keeps its proportions, so it
    // grows with the card (like pwm-power-flow-card); `height` (%) makes the
    // plot taller or lower, `scale` (%) a narrower drawing, centred.
    const W = 600;
    const left = 30;
    const right = W - 6;
    const top = 8;
    const heightPct = Math.max(50, Math.min(300, Number(this._config.height) || 100));
    const bottom = top + Math.round((PWM_PLAN_PLOT_HEIGHT * heightPct) / 100);
    const stripTop = bottom + 6;
    const stripH = 16;
    const H = stripTop + stripH + 18;
    const start = today + (showTomorrow ? 24 : 0) * PWM_PLAN_HOUR_MS;
    const span = hours * PWM_PLAN_HOUR_MS;
    const x = (time) => left + ((time - start) / span) * (right - left);
    const y = (pct) => bottom - (Math.max(0, Math.min(100, pct)) / 100) * (bottom - top);
    const now = Date.now();
    const hourW = (right - left) / hours;
    const emptyState = this._state("empty");
    const empty = emptyState && Number.isFinite(Number(emptyState.state)) ? Number(emptyState.state) : PWM_RING_DEFAULT_EMPTY_SOC;
    const socNow = Number(this._state("soc").state);
    const minNow = Number(data.state);

    // History up to now, the current values carried to now.
    const history = this._history || { soc: [], min: [] };
    // Tomorrow has no history.
    const withNow = (points, value) =>
      showTomorrow ? [] : Number.isFinite(value) ? [...points.filter((p) => p.t <= now), { t: now, v: value }] : points;
    const socPoints = withNow(history.soc, socNow);
    const minPoints = withNow(history.min, minNow);
    // The value a step series has at a time (the last point before it).
    const valueAt = (points, time) => {
      let value;
      for (const p of points) {
        if (p.t > time) break;
        value = p.v;
      }
      return value;
    };

    const parts = [];
    parts.push(
      `<defs>${[...Object.entries(PWM_PLAN_RULE_COLORS), ["unknown", PWM_PLAN_UNKNOWN_COLOR]]
        .map(
          ([rule, color]) =>
            `<pattern id="pwm-plan-${rule}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="${color}" stroke-width="2.5" opacity="0.55"/></pattern>`
        )
        .join("")}</defs>`
    );
    const grid = [0, 25, 50, 75, 100]
      .map((pct) => `<line x1="${left}" x2="${right}" y1="${y(pct)}" y2="${y(pct)}"/>`)
      .join("");
    parts.push(`<g class="grid">${grid}</g>`);

    // Per hour: the kept part (hatched, the rule's colour), the usable part
    // above it (green), and the strip. Past hours use the history; ahead of
    // now the SoC is the SoC now.
    const used = new Set();
    plan.forEach((entry, index) => {
      const hourStart = start + index * PWM_PLAN_HOUR_MS;
      const bx = left + index * hourW;
      const rule = entry.rule || "none";
      const unknown = rule === "unknown";
      const color = unknown ? PWM_PLAN_UNKNOWN_COLOR : PWM_PLAN_RULE_COLORS[rule];
      if (color) used.add(rule);
      if (unknown) {
        parts.push(`<rect x="${bx}" y="${top}" width="${hourW}" height="${bottom - top}" fill="url(#pwm-plan-unknown)"/>`);
      } else if (color) {
        const past = hourStart + PWM_PLAN_HOUR_MS <= now;
        const mid = Math.min(hourStart + PWM_PLAN_HOUR_MS / 2, now);
        // Ahead of now: the SoC the plan assumes for the hour (older plans
        // have none -- then the SoC now).
        const planSoc = entry.soc !== null && entry.soc !== undefined ? Number(entry.soc) : socNow;
        const soc = past || hourStart <= now ? valueAt(socPoints, mid) : planSoc;
        const floor = entry.floor !== null && entry.floor !== undefined ? Number(entry.floor) : valueAt(minPoints, mid);
        if (Number.isFinite(soc) && Number.isFinite(floor)) {
          const kept = Math.min(floor, soc);
          if (kept > empty) {
            parts.push(`<rect x="${bx}" y="${y(kept)}" width="${hourW}" height="${y(empty) - y(kept)}" fill="url(#pwm-plan-${rule})"/>`);
          }
          if (soc > floor) {
            parts.push(
              `<rect x="${bx}" y="${y(soc)}" width="${hourW}" height="${y(floor) - y(soc)}" fill="${PWM_PLAN_USABLE_COLOR}" opacity="${past ? 0.12 : 0.22}"/>`
            );
          }
        }
      }
      const selected = this._selected === index ? " selected" : "";
      const fill = color || "var(--divider-color)";
      parts.push(
        `<rect class="band${selected}" data-i="${index}" x="${bx + 0.5}" y="${stripTop}" width="${hourW - 1}" height="${stripH}" rx="2" fill="${unknown ? "url(#pwm-plan-unknown)" : fill}" opacity="${entry.planned ? 0.85 : 0.5}"/>`
      );
    });
    // "Waiting for prices" over the unknown hours.
    const unknownIndexes = plan.map((entry, index) => (entry.rule === "unknown" ? index : -1)).filter((i) => i >= 0);
    if (unknownIndexes.length) {
      const cx = left + ((unknownIndexes[0] + unknownIndexes[unknownIndexes.length - 1] + 1) / 2) * hourW;
      parts.push(
        `<text class="waiting" x="${cx}" y="${y(55)}" text-anchor="middle">${t.waiting[0]}</text>` +
          `<text class="waiting" x="${cx}" y="${y(55) + 14}" text-anchor="middle">${t.waiting[1]}</text>`
      );
    }

    const yLabels = [0, 50, 100]
      .map((pct) => `<text x="${left - 4}" y="${y(pct) + 4}" text-anchor="end">${pct}</text>`)
      .join("");
    const xLabels = Array.from({ length: hours / 3 + 1 }, (_, i) => i * 3)
      .map((h) => `<text x="${left + h * hourW}" y="${H - 2}" text-anchor="middle">${String(h % 24).padStart(2, "0")}</text>`)
      .join("");
    parts.push(`<g class="axis">${yLabels}${xLabels}</g>`);
    if (!showTomorrow) {
      parts.push(`<line class="midnight" x1="${left + 24 * hourW}" x2="${left + 24 * hourW}" y1="${top}" y2="${bottom}"/>`);
    }

    // Target: only today until 17:00 -- the rules that use it (charge
    // curve, target reached, the night charge kept) end there, and
    // tomorrow's target isn't worked out yet.
    const targetState = this._state("target");
    const target = targetState && !showTomorrow ? Number(targetState.state) : NaN;
    const targetEnd = today + PWM_PLAN_TARGET_END_HOUR * PWM_PLAN_HOUR_MS;
    if (Number.isFinite(target)) {
      parts.push(`<line class="target" x1="${left}" x2="${x(targetEnd)}" y1="${y(target)}" y2="${y(target)}"/>`);
    }

    // History: SoC as a line, min SoC as steps.
    if (socPoints.length > 1) {
      parts.push(
        `<path class="soc" d="${socPoints.map((p, i) => `${i ? "L" : "M"}${x(Math.max(p.t, start)).toFixed(1)},${y(p.v).toFixed(1)}`).join("")}"/>`
      );
    }
    const stepPath = (points) =>
      points
        .map((p, i) => {
          const px = x(Math.max(p.t, start)).toFixed(1);
          return i ? `H${px}V${y(p.v).toFixed(1)}` : `M${px},${y(p.v).toFixed(1)}`;
        })
        .join("");
    if (minPoints.length > 1) parts.push(`<path class="min" d="${stepPath(minPoints)}"/>`);

    // The plan from now: the planned floor per hour, dashed, up to the
    // first unknown hour.
    const planned = [];
    if (Number.isFinite(minNow) && !showTomorrow) planned.push({ t: now, v: minNow });
    let planEnd = start + span;
    for (let index = 0; index < plan.length; index++) {
      const entry = plan[index];
      const hourStart = start + index * PWM_PLAN_HOUR_MS;
      if (entry.rule === "unknown") {
        planEnd = Math.max(hourStart, now);
        break;
      }
      if (hourStart + PWM_PLAN_HOUR_MS <= now || entry.floor === null || entry.floor === undefined) continue;
      planned.push({ t: Math.max(hourStart, now), v: Number(entry.floor) });
    }
    if (planned.length) {
      planned.push({ t: planEnd, v: planned[planned.length - 1].v });
      parts.push(`<path class="min planned" d="${stepPath(planned)}"/>`);
    }

    // Now.
    if (!showTomorrow) {
      const nx = x(now);
      parts.push(`<line class="now" x1="${nx}" x2="${nx}" y1="${top}" y2="${stripTop + stripH}"/>`);
      parts.push(`<text class="now-label" x="${nx + 3}" y="${top + 10}">${t.now}</text>`);
    }

    // The pointer's time: a line with a dot on each curve (the tooltip
    // below has the values).
    parts.push(
      `<g class="cursor" style="display: none"><line x1="0" x2="0" y1="${top}" y2="${bottom}"/>` +
        ["soc", "min", "planned", "target"].map((name) => `<circle class="dot-${name}" r="4" cx="0" cy="0"/>`).join("") +
        `</g>`
    );
    // On top of everything: one column per hour, plot and strip, to tap or
    // hover (the tooltip below).
    plan.forEach((_entry, index) => {
      parts.push(`<rect class="hit" data-i="${index}" x="${left + index * hourW}" y="${top}" width="${hourW}" height="${stripTop + stripH - top}"/>`);
    });

    const wrap = document.createElement("div");
    wrap.className = "wrap";
    wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.title}">${parts.join("")}</svg>`;
    const scale = Math.max(50, Math.min(100, Number(this._config.scale) || 100));
    wrap.firstElementChild.style.width = `${scale}%`;
    card.appendChild(wrap);

    // The selected hour's rule (default: the current hour), or a rule
    // tapped in the legend.
    const currentIndex = Math.max(0, Math.floor((now - start) / PWM_PLAN_HOUR_MS));
    const info = document.createElement("div");
    info.className = "info";
    info.innerHTML =
      typeof this._selected === "string"
        ? this._ruleHtml(t, this._selected)
        : this._hourHtml(t, plan, this._selected !== undefined ? this._selected : currentIndex, start);
    wrap.appendChild(info);
    const select = (value) => {
      this._selected = this._selected === value ? undefined : value;
      this._signature = undefined;
      this._render();
    };
    // The pointer over the drawing (mouse, or a finger held on it): the
    // values of the curves at that time, and -- for a mouse -- what a tap
    // on that hour would show. Touch has no hover, so a tap still fills the
    // box.
    const tip = document.createElement("div");
    tip.className = "info tip";
    wrap.appendChild(tip);
    const svg = wrap.querySelector("svg");
    const cursor = svg.querySelector(".cursor");
    const placeTip = (event, touch) => {
      const box = wrap.getBoundingClientRect();
      const px = event.clientX - box.left;
      const py = event.clientY - box.top;
      // Beside the pointer (left of it near the right edge), above it --
      // below when there's no room, but never under a finger -- and always
      // inside the card.
      const tx = px + 14 + tip.offsetWidth > box.width ? px - 14 - tip.offsetWidth : px + 14;
      const above = py - tip.offsetHeight - (touch ? 40 : 10);
      tip.style.left = `${Math.max(0, Math.min(tx, box.width - tip.offsetWidth))}px`;
      tip.style.top = `${above >= 0 || touch ? Math.max(0, above) : py + 16}px`;
    };
    const nextHour = start + (currentIndex + 1) * PWM_PLAN_HOUR_MS;
    // Each curve's value at a time, as drawn: the history up to now, then
    // the plan (the SoC it assumes, the planned floor) until the first
    // hour waiting for prices; the target as drawn.
    const valuesAt = (time, index) => {
      const entry = plan[index] || {};
      const ahead = time > now;
      const planSoc = entry.soc !== null && entry.soc !== undefined ? Number(entry.soc) : socNow;
      return {
        soc: !ahead ? valueAt(socPoints, time) : entry.rule === "unknown" ? undefined : time < nextHour ? socNow : planSoc,
        min: ahead ? undefined : valueAt(minPoints, time),
        planned: ahead && planned.length && time < planEnd ? valueAt(planned, time) : undefined,
        target: Number.isFinite(target) && time < targetEnd ? target : undefined,
      };
    };
    const labels = { soc: t.soc, min: t.minSoc, planned: t.planned, target: t.target };
    const showAt = (event, touch) => {
      const point = svg.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      const sx = Math.max(left, Math.min(right, point.matrixTransform(svg.getScreenCTM().inverse()).x));
      const time = start + ((sx - left) / (right - left)) * span;
      const index = Math.min(plan.length - 1, Math.floor((time - start) / PWM_PLAN_HOUR_MS));
      const values = valuesAt(time, index);
      cursor.style.display = "";
      cursor.querySelector("line").setAttribute("x1", sx);
      cursor.querySelector("line").setAttribute("x2", sx);
      const rows = [];
      for (const [name, value] of Object.entries(values)) {
        const dot = cursor.querySelector(`.dot-${name}`);
        const shown = Number.isFinite(value);
        dot.style.display = shown ? "" : "none";
        if (!shown) continue;
        dot.setAttribute("cx", sx);
        dot.setAttribute("cy", y(value));
        const color = getComputedStyle(dot).fill;
        rows.push(`<tr><td><i style="background: ${color}"></i>${labels[name]}</td><td>${Math.round(value)} %</td></tr>`);
      }
      const clock = new Date(time).toLocaleTimeString(this._language(), { hour: "2-digit", minute: "2-digit" });
      const readout = `<div class="values"><div class="time">${clock}</div><table>${rows.join("")}</table></div>`;
      tip.innerHTML = touch ? readout : `${readout}<hr>${this._hourHtml(t, plan, index, start)}`;
      tip.style.display = "block";
      placeTip(event, touch);
    };
    const hide = () => {
      tip.style.display = "none";
      cursor.style.display = "none";
    };
    svg.addEventListener("pointermove", (event) => {
      if (event.pointerType === "mouse" || event.buttons) showAt(event, event.pointerType !== "mouse");
    });
    svg.addEventListener("pointerdown", (event) => {
      if (event.pointerType !== "mouse") showAt(event, true);
    });
    ["pointerleave", "pointerup", "pointercancel"].forEach((type) =>
      svg.addEventListener(type, (event) => {
        if (type === "pointerleave" || event.pointerType !== "mouse") hide();
      })
    );
    wrap.querySelectorAll(".hit").forEach((hit) => {
      hit.addEventListener("click", () => select(Number(hit.dataset.i)));
    });

    // Legend: the lines and areas, then the rules the plan shows (tap one
    // for what it means).
    const legend = document.createElement("div");
    legend.className = "legend";
    const ruleLegend = [...Object.keys(PWM_PLAN_RULE_COLORS), "unknown"]
      .filter((rule) => used.has(rule))
      .map((rule) => {
        const swatch =
          rule === "unknown"
            ? `background: repeating-linear-gradient(45deg, ${PWM_PLAN_UNKNOWN_COLOR} 0 2px, transparent 2px 5px)`
            : `background: ${PWM_PLAN_RULE_COLORS[rule]}`;
        return `<span class="rule" data-rule="${rule}"><i style="${swatch}"></i>${t.rules[rule][0]}</span>`;
      });
    legend.innerHTML = [
      `<span><i class="line" style="border-color: var(--primary-color)"></i>${t.soc}</span>`,
      `<span><i class="line" style="border-color: var(--error-color, #e53935)"></i>${t.minSoc}</span>`,
      `<span><i class="dash" style="border-color: var(--error-color, #e53935)"></i>${t.planned}</span>`,
      ...(Number.isFinite(target)
        ? [`<span><i class="dash" style="border-color: var(--success-color, #43a047)"></i>${t.target}</span>`]
        : []),
      `<span><i style="background: ${PWM_PLAN_USABLE_COLOR}; opacity: 0.35"></i>${t.usable}</span>`,
      `<span><i style="background: repeating-linear-gradient(45deg, var(--secondary-text-color) 0 2px, transparent 2px 5px)"></i>${t.held}</span>`,
      ...ruleLegend,
    ].join("");
    wrap.appendChild(legend);
    legend.querySelectorAll(".rule").forEach((item) => {
      item.addEventListener("click", () => select(item.dataset.rule));
    });
    const note = document.createElement("div");
    note.className = "note";
    note.textContent = t.planNote;
    wrap.appendChild(note);
  }

  _ruleHead(rule, label) {
    const color = rule === "unknown" ? PWM_PLAN_UNKNOWN_COLOR : PWM_PLAN_RULE_COLORS[rule] || "var(--divider-color)";
    return `<div class="head"><i style="background: ${color}"></i>${label}</div>`;
  }

  /** A rule tapped in the legend: its name and what it means. */
  _ruleHtml(t, rule) {
    const [name, desc] = t.rules[rule] || t.rules.none;
    return `${this._ruleHead(rule, name)}<div class="desc">${desc}</div>`;
  }

  /** "15–17 · Day plan ..." for the run of equal hours around index, what
   * the rule means, and -- for the night price protection -- the sums. */
  _hourHtml(t, plan, index, start) {
    const entry = plan[index];
    if (!entry) return "";
    const rule = entry.rule || "none";
    const same = (other) =>
      other && (other.rule || "none") === rule && other.floor === entry.floor && other.planned === entry.planned &&
      other.price === undefined && entry.price === undefined;
    let from = index;
    let to = index;
    while (same(plan[from - 1])) from--;
    while (same(plan[to + 1])) to++;
    const clock = (i) => String(i % 24).padStart(2, "0");
    const [name, desc] = t.rules[rule] || [rule, ""];
    const lines = [this._ruleHead(rule, `${clock(from)}–${clock(to + 1)} · ${name}`)];
    if (rule === "night_protection" && entry.price !== undefined) {
      lines.push(`<div class="desc">${t.heldText(entry.held_points, this._kr(entry.cell_price_min))} ${t.floor(entry.floor ?? "–")}</div>`);
      const saving = entry.price - entry.cell_price_min;
      const usedFrom =
        entry.cell_price_max > entry.cell_price_min
          ? `${this._kr(entry.cell_price_min + entry.margin)} (${t.allFrom(this._kr(entry.cell_price_max + entry.margin))})`
          : this._kr(entry.cell_price_min + entry.margin);
      lines.push(
        `<table>` +
          `<tr><td>${t.priceAt(clock(index))}</td><td>${this._kr(entry.price)}</td></tr>` +
          `<tr><td>${t.cellPrice}</td><td>${
            entry.cell_price_max > entry.cell_price_min
              ? `${this._kr(entry.cell_price_min)} – ${this._kr(entry.cell_price_max)}`
              : this._kr(entry.cell_price_min)
          }</td></tr>` +
          `<tr><td>${t.saving}</td><td>${this._kr(Math.max(saving, 0))} – ${t.belowMargin(this._kr(entry.margin))}</td></tr>` +
          `<tr><td>${t.usedFrom}</td><td>${usedFrom}</td></tr>` +
          `</table>`
      );
    } else {
      lines.push(`<div class="desc">${desc}</div>`);
      if (entry.floor !== null && entry.floor !== undefined) {
        lines.push(`<div class="desc">${t.floor(entry.floor)}</div>`);
      } else if (!entry.planned && start + (index + 1) * PWM_PLAN_HOUR_MS <= Date.now()) {
        lines.push(`<div class="desc">${t.earlier}</div>`);
      }
    }
    return lines.join("");
  }
}

pwmDefine("pwm-battery-plan-card", PwMBatteryPlanCard);

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
  },
  {
    type: "pwm-phase-load-card",
    name: "PwM Phase load",
    description: "The main fuse's three phase loads as a ring, with PwMngt's limit and free current.",
  },
  {
    type: "pwm-battery-plan-card",
    name: "PwM Battery plan",
    description: "The battery's min SoC rules hour by hour, today and tonight: SoC, min SoC, the plan, the target and what may be used.",
  },
  {
    type: "pwm-power-flow-card",
    name: "PwM Power flow",
    description: "The house in the middle with PV, grid, battery and other devices around it and animated power flow.",
    preview: true,
  }
];
// Skip the ones already listed -- this file may run twice (see pwmDefine()).
for (const card of PWM_CUSTOM_CARDS) {
  if (!window.customCards.some((existing) => existing.type === card.type)) {
    window.customCards.push(card);
  }
}
