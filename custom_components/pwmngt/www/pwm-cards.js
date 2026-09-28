/**
 * PwMngt custom Lovelace cards.
 *
 * Plain JavaScript custom elements (no build step) implementing Home
 * Assistant's "custom card" contract: setConfig(config), a hass setter,
 * and getCardSize(). Background on the pattern:
 * https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card
 *
 * Four card types are defined here:
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
        label: "Spot electricity price",
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
  }
);
