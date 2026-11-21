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
    manualRuns: "Runs until 100% SoC or until stopped",
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
    chargeNow: (level) => `Charge now ${level}`,
    stop: "Stop charging",
    postpone: "Postpone charging 1 day",
    cancelPostpone: "Cancel postponement",
    disableAuto: "Disable automatic charging",
    enableAuto: "Enable automatic charging",
    unlockHold: "🔒 Manual control - hold 2 s",
    holdHint: "Hold the button for 2 seconds",
    relock: "🔓 Lock",
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
    useTonight: "Use tonight",
    resetAuto: "Reset to automatic",
    close: "Close",
  },
  da: {
    title: "Hus batteri styring",
    manual: "Automatik og batterilås slået fra - kun manuel ladning",
    charging: (level) => `Batteri lader nu med ${level}`,
    autoControls: "Nattens ladning styres af automatikken",
    manualCharging: (level) => `Manuel ladning med ${level}`,
    manualRuns: "Kører til 100 % SoC, eller til den afbrydes",
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
    chargeNow: (level) => `Lad nu ${level}`,
    stop: "Afbryd ladning",
    postpone: "Udskyd ladning 1 dag",
    cancelPostpone: "Annuller udskydning",
    disableAuto: "Deaktiver automatisk ladning",
    enableAuto: "Aktiver automatisk ladning",
    unlockHold: "🔒 Manuel betjening - hold 2 sek.",
    holdHint: "Hold knappen inde i 2 sekunder",
    relock: "🔓 Lås",
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
    useTonight: "Brug i nat",
    resetAuto: "Nulstil til automatik",
    close: "Luk",
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

const PWM_CHARGE_LEVELS = [25, 50, 75, 100];
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
    if ((this._editor && this._editor.open) || this._holding) return;
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
        this._note(chargingPct !== null && chargingPct < manualPct ? t.reducedByFuse(this._chargeLevel(t, manualPct)) : t.manualRuns)
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
    title.textContent = t.manualTitle;
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
    if (chargingPct !== null || manualPct !== null) {
      buttons.appendChild(this._button(t.stop, () => this._setCommand("internal")));
    }
    if (this._ids.postpone && deferred) {
      buttons.appendChild(this._button(t.cancelPostpone, postponeButton));
    }
    if (!isAuto) {
      buttons.appendChild(this._button(t.enableAuto, () => this._setMode("auto")));
    }

    if (this._isUnlocked()) {
      const guarded = (text, action) =>
        this._button(text, () => {
          action();
          this._lockManual();
        });
      for (const pct of PWM_CHARGE_LEVELS) {
        if (pct === (manualPct !== null ? manualPct : chargingPct)) continue;
        buttons.appendChild(
          guarded(t.chargeNow(this._chargeLevel(t, pct)), () => this._setCommand(`charge_now_${pct}`))
        );
      }
      if (this._ids.postpone && !deferred) {
        buttons.appendChild(guarded(t.postpone, postponeButton));
      }
      if (isAuto) {
        buttons.appendChild(guarded(t.disableAuto, () => this._setMode("manual")));
      }
      const relock = this._button(t.relock, () => this._lockManual());
      relock.className = "pwm-wide";
      buttons.appendChild(relock);
    } else {
      buttons.appendChild(this._holdButton(t.unlockHold, t.holdHint, () => this._unlockManual()));
    }
    manualGroup.appendChild(buttons);

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
  _holdButton(text, hint, onHeld) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pwm-hold pwm-wide";
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
    if (!this._hass || (this._editor && this._editor.open)) return;
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
      line.className = "status";
      line.textContent = status;
      card.appendChild(line);
    }
  }

  _ring(t, soc, pv, grid, minSoc, target, size, power, empty) {
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
    const usable = (Math.max(0, soc - empty) * size) / 100;
    const kwh = Number.isFinite(size) ? `${pwmFormatDecimal(usable)} kWh` : "";

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
function pwmRingSegment(from, to, color, cls, linecap = "butt", width = 14) {
  from = Math.max(0, from);
  to = Math.min(100, to);
  if (to <= from) return "";
  const length = (PWM_RING_ARC * (to - from)) / 100;
  const start = (PWM_RING_ARC * from) / 100;
  return `<circle cx="100" cy="100" r="${PWM_RING_RADIUS}" fill="none" stroke="${color}"
    stroke-width="${width}" stroke-linecap="${linecap}" class="${cls || ""}"
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
      'Extra lines can get a label in front (e.g. "in", "SoC") in YAML: secondary: [{entity: ..., label: in}]',
    placementRing: "Ring around the house",
    placementSide: "Column on the left (PV/grid/battery)",
    flowOut: "Out of the house when positive (consumption, battery charging)",
    flowIn: "Into the house when positive (solar, grid import, battery discharging)",
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
      center_size: "House size",
      node_size: "Circle size",
      text_size: "Text size (%)",
      name_on_arc: "Name on an arc above the circle",
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
      'Ekstra linjer kan få en tekst foran (fx "in", "SoC") via YAML: secondary: [{entity: ..., label: in}]',
    placementRing: "Ring om huset",
    placementSide: "Kolonne til venstre (PV/net/batteri)",
    flowOut: "Ud fra huset ved positiv værdi (forbrug, batteri der lader)",
    flowIn: "Ind i huset ved positiv værdi (sol, net-import, batteri der aflader)",
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
      center_size: "Husets størrelse",
      node_size: "Cirkelstørrelse",
      text_size: "Tekststørrelse (%)",
      name_on_arc: "Navn på en bue over cirklen",
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

function pwmFlowFormatState(hass, entityId) {
  const state = hass.states[entityId];
  if (!state) return "—";
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
    .map((s) => (s.label ? s.label + " " : "") + pwmFlowFormatState(hass, s.entity))
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

/** The entity picker only returns ids -- keep labels set in YAML. */
function pwmFlowMergeSecondary(ids, old) {
  const previous = pwmFlowSecondaryList(old);
  return (ids || []).map((id) => {
    const found = previous.find((x) => x.entity === id);
    return found && found.label ? { entity: id, label: found.label } : id;
  });
}

class PwMPowerFlowCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
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
      .map((d) => ({ entity: find("pv", d.key), placement: "side", icon: d.icon, color: d.color }))
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
    this._build();
    if (this._hass) this._update();
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
    this._visibleKey = visible.join();
    const side = [];
    const ring = [];
    c.devices.forEach((d, i) => { if (visible[i]) (d.placement === "side" ? side : ring).push(i); });
    const hasSide = side.length > 0;
    const VW = hasSide ? 140 : 100; // viewBox width (the height is always 100)
    const HX = hasSide ? 86 : 50; // the house's centre
    const NS = Math.max(12, Math.min(34, Number(c.node_size) || 26)); // wanted circle size
    const CD = Math.max(16, Math.min(40, Number(c.center_size) || 32)); // house diameter
    const CR = CD / 2;
    const TS = Math.max(50, Math.min(200, Number(c.text_size) || 100)) / 100; // text scale
    // A name on an arc above the circle needs room above the top circles (LH).
    const ARC = !!c.name_on_arc;
    const LH = ARC ? NS * 0.11 * TS + 1.5 : 0;
    const HY = 50 + LH / 2; // moved down so the top circle's name fits above it
    const pct = (x) => (x / VW) * 100;
    const centerColor = c.center.color || "#29b6f6";

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

    // Left column
    const ns = side.length;
    const sideTop = 18 + LH / 2;
    const sideStep = ns > 1 ? (82 - sideTop) / (ns - 1) : 0;
    const DS = ns > 1 ? Math.min(NS, sideStep * 0.9 - LH) : NS;
    const RS = DS / 2;
    const SX = RS + 1;

    const geometry = [];
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
      const y = ns > 1 ? sideTop + k * sideStep : 50;
      // Where the line meets the house's left side, spread by height.
      const ea = Math.PI - ((y - HY) / 50) * 0.75;
      const ex = HX + CR * Math.cos(ea);
      const ey = HY + CR * Math.sin(ea);
      const sx = SX + RS;
      const dx = ex - sx;
      geometry[i] = {
        x: SX, y, d: DS,
        // Drawn from the house outwards, the same direction as the ring's lines.
        p: `M${ex} ${ey} C${ex - dx * 0.45} ${ey} ${sx + dx * 0.55} ${y} ${sx} ${y}`,
      };
    });

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
        <ha-icon></ha-icon><div class="val"></div><div class="name"></div><div class="sec"></div></div>`;
    });

    this.shadowRoot.innerHTML = `
      <style>${PWM_FLOW_CARD_STYLE}</style>
      <ha-card>
        <div class="header"><span class="title"></span><span class="date"></span></div>
        <div class="stage" style="aspect-ratio:${VW}/100;max-width:${VW * 6.4}px;--k:${100 / VW};--ts:${TS}">
          <svg viewBox="0 0 ${VW} 100">${svg}<g class="labels">${labels}</g></svg>
          <div class="node center" style="left:${pct(HX)}%;top:${HY}%;width:${pct(CD)}%;--c:${centerColor}">
            <ha-icon></ha-icon><div class="val"></div><div class="name"></div><div class="sec"></div>
          </div>
          ${nodes}
        </div>
      </ha-card>`;

    const pick = (el, id) => ({
      el,
      arc: this.shadowRoot.querySelector(`.arc-name[data-arc="${id}"] textPath`),
      icon: el.querySelector("ha-icon"),
      val: el.querySelector(".val"),
      name: el.querySelector(".name"),
      sec: el.querySelector(".sec"),
    });
    const background = c.background || "#000";
    this.shadowRoot.querySelector("ha-card").style.setProperty("--pwm-flow-card-bg",
      background === "theme" ? "var(--ha-card-background, var(--card-background-color))" : background);
    this._nodes = [];
    this._lines = [];
    this.shadowRoot.querySelectorAll(".node.dev").forEach((el) => (this._nodes[+el.dataset.i] = pick(el, el.dataset.i)));
    this.shadowRoot.querySelectorAll(".line").forEach((el) => (this._lines[+el.dataset.i] = el));
    this._center = pick(this.shadowRoot.querySelector(".node.center"), "c");
    this._durations = [];

    this._nodes.forEach((node, i) =>
      node && node.el.addEventListener("click", () => this._moreInfo(c.devices[i].entity))
    );
    this._center.el.addEventListener("click", () => this._moreInfo(c.center.entity));
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    const event = new Event("hass-more-info", { bubbles: true, composed: true });
    event.detail = { entityId };
    this.dispatchEvent(event);
  }

  _fill(node, cfg, watts, language, defaultIcon) {
    const state = this._hass.states[cfg.entity];
    node.icon.setAttribute("icon", cfg.icon || (state && state.attributes.icon) || defaultIcon);
    node.val.textContent = pwmFlowFormatPower(watts, language);
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
    if (visible.join() !== this._visibleKey) this._build(visible);

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
      this._fill(this._nodes[i], d, watts, language, "mdi:flash");

      const line = this._lines[i];
      const active = watts !== null && Math.abs(watts) >= min;
      let inward = d.flow ? d.flow === "in" : d.placement === "side";
      if (watts !== null && watts < 0) inward = !inward;
      line.classList.toggle("active", active);
      line.classList.toggle("in", inward);
      this._nodes[i].el.classList.toggle("idle", !active);

      if (active) {
        // More power = faster dots; only touch the animation on a real change,
        // since resetting --dur restarts it.
        const duration = Math.max(0.6, Math.min(3, 3 - Math.log10(Math.abs(watts)) * 0.6));
        if (!this._durations[i] || Math.abs(this._durations[i] - duration) > 0.15) {
          this._durations[i] = duration;
          line.style.setProperty("--dur", duration.toFixed(2) + "s");
        }
      }
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
  .node ha-icon { --mdc-icon-size:24cqw; color:var(--pwm-flow-fg); margin-bottom:2cqw; }
  /* --ts is the card's text_size option (1 = 100 %). */
  .val { font-weight:600; font-size:calc(16cqw * var(--ts,1)); line-height:1.15; white-space:nowrap; letter-spacing:.02em; }
  .name, .sec { font-size:calc(11cqw * var(--ts,1)); line-height:1.2; white-space:pre-line; }
  .name { font-weight:500; max-width:100%; overflow-wrap:anywhere; }
  .sec { font-weight:500; opacity:.92; }
  .center ha-icon { --mdc-icon-size:24cqw; }
  .center .val { font-size:calc(14cqw * var(--ts,1)); }
  .center .name, .center .sec { font-size:calc(10cqw * var(--ts,1)); }
`;

pwmDefine("pwm-power-flow-card", PwMPowerFlowCard);

const PWM_FLOW_GENERAL_SCHEMA = [
  { type: "grid", name: "", schema: [
    { name: "title", selector: { text: {} } },
    { name: "background", selector: { text: {} } },
  ] },
  { type: "grid", name: "", schema: [
    { name: "show_date", selector: { boolean: {} } },
    { name: "min_power", selector: { number: { min: 0, max: 1000, mode: "box", unit_of_measurement: "W" } } },
    { name: "node_size", selector: { number: { min: 12, max: 34, step: 1, mode: "slider" } } },
    { name: "center_size", selector: { number: { min: 16, max: 40, step: 1, mode: "slider" } } },
    { name: "text_size", selector: { number: { min: 50, max: 200, step: 5, mode: "slider" } } },
    { name: "name_on_arc", selector: { boolean: {} } },
  ] },
];

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
      ] } } },
      { name: "flow", selector: { select: { mode: "dropdown", options: [
        { value: "out", label: t.flowOut },
        { value: "in", label: t.flowIn },
      ] } } },
      { name: "invert", selector: { boolean: {} } },
      { name: "hide_name", selector: { boolean: {} } },
      { name: "subtract", selector: { boolean: {} } },
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

    const generalKeys = ["title", "background", "show_date", "min_power", "node_size", "center_size", "text_size", "name_on_arc"];
    this._form(this.querySelector("#g"), PWM_FLOW_GENERAL_SCHEMA,
      { title: c.title, background: c.background, show_date: c.show_date, min_power: c.min_power,
        node_size: c.node_size || 26, center_size: c.center_size || 32,
        text_size: c.text_size || 100, name_on_arc: c.name_on_arc },
      t.labels,
      (v) => {
        generalKeys.forEach((key) => {
          if (v[key] === undefined || v[key] === "") delete c[key]; else c[key] = v[key];
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
          secondary: pwmFlowSecondaryList(d.secondary).map((s) => s.entity) },
        t.labels,
        (v) => {
          const next = pwmFlowClean({ ...v, secondary: pwmFlowMergeSecondary(v.secondary, d.secondary) });
          const placementChanged = (v.placement || "ring") !== (d.placement || "ring");
          if (placementChanged) delete next.flow; // use the new placement's default direction
          // Leave defaults out of the YAML.
          if (next.placement === "ring") delete next.placement;
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
