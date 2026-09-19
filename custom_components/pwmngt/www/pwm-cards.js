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
 * (select.py / number.py / text.py) as of this writing, in the same order.
 * This file does NOT discover fields automatically -- if a config field is
 * added, removed, reordered, or renamed in Python, update the matching
 * `fields` array below to match. Automatic discovery (via hass.devices /
 * hass.entities, keyed off the "pwmngt" device identifiers) is a
 * reasonable next step once these hand-written lists get annoying to
 * maintain.
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
 *     entityId: "select.pwm_charger1_type",
 *     label: "Charger1 type",
 *     icon: "mdi:ev-station",
 *     type: "select" | "number" | "text",
 *     hideUnless: { entityId: "...", equals: "Easee" },  // optional
 *   }
 */
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

    this._rows = {};
    for (const field of this.fields || []) {
      const row = this._buildRow(field);
      content.appendChild(row.rowEl);
      this._rows[field.entityId] = row;
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

    let controlEl;
    if (field.type === "select") {
      controlEl = document.createElement("select");
      controlEl.addEventListener("change", () => {
        this._hass.callService("select", "select_option", {
          entity_id: field.entityId,
          option: controlEl.value,
        });
      });
    } else if (field.type === "number") {
      controlEl = document.createElement("input");
      controlEl.type = "number";
      controlEl.addEventListener("change", () => {
        this._hass.callService("number", "set_value", {
          entity_id: field.entityId,
          value: Number(controlEl.value),
        });
      });
    } else {
      // "text"
      controlEl = document.createElement("input");
      controlEl.type = "text";
      controlEl.addEventListener("change", () => {
        this._hass.callService("text", "set_value", {
          entity_id: field.entityId,
          value: controlEl.value,
        });
      });
    }
    controlWrap.appendChild(controlEl);

    rowEl.append(labelEl, controlWrap);
    return { rowEl, controlEl };
  }

  _update() {
    for (const field of this.fields || []) {
      const row = this._rows[field.entityId];
      if (!row) continue;

      // Visibility, for fields that only apply given another entity's
      // current value (e.g. the Easee-only identification field).
      let visible = true;
      if (field.hideUnless) {
        const gate = this._hass.states[field.hideUnless.entityId];
        visible = !!gate && gate.state === field.hideUnless.equals;
      }
      row.rowEl.classList.toggle("pwm-hidden", !visible);

      const state = this._hass.states[field.entityId];
      if (!state) {
        continue; // Entity not available yet -- leave the control as-is.
      }

      const isBeingEdited = document.activeElement === row.controlEl;

      if (field.type === "select") {
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
    }
  }
}

/** Builds one charger-device field descriptor for a given charger id. */
function chargerField(charger, type, suffix, label, icon, extra) {
  return Object.assign(
    {
      entityId: `${type}.pwm_${charger}_${suffix}`,
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
        "driving_distance_in_km_per_kwh",
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
      chargerField(
        charger,
        "text",
        "charger_identification_easee_serial_number",
        "Charger identification (Easee serial number)",
        "mdi:identifier",
        { hideUnless: { entityId: `select.pwm_${charger}_type`, equals: "Easee" } }
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
 * hass.devices, matching on the "pwmngt" device identifier domain and a
 * "PwM Charger..." name) -- so adding a future charger in devices.py needs
 * no change here. Home Assistant wires this up automatically because of
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
    const chargerDevices = Object.values(this._hass.devices).filter(
      (d) =>
        (d.identifiers || []).some(([domain]) => domain === "pwmngt") &&
        d.name &&
        d.name.startsWith("PwM Charger")
    );
    const ids = chargerDevices
      .map((d) => (d.identifiers.find(([domain]) => domain === "pwmngt") || [])[1])
      .filter(Boolean)
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
      { entityId: "select.pwm_charger1_type", label: "Charger1 type", icon: "mdi:ev-station", type: "select" },
      { entityId: "select.pwm_charger2_type", label: "Charger2 type", icon: "mdi:ev-station", type: "select" },
      {
        entityId: "select.pwm_charger_priority",
        label: "Charger priority",
        icon: "mdi:sort-numeric-ascending",
        type: "select",
      },
      {
        entityId: "select.pwm_minimum_solar_power_to_charge",
        label: "Minimum solar power to charge (%)",
        icon: "mdi:solar-power",
        type: "select",
      },
      {
        entityId: "select.pwm_start_charging_at_battery_capacity",
        label: "Start charging at battery capacity (%)",
        icon: "mdi:battery-arrow-down",
        type: "select",
      },
      {
        entityId: "select.pwm_stop_charging_at_battery_capacity",
        label: "Stop charging at battery capacity (%)",
        icon: "mdi:battery-arrow-up",
        type: "select",
      },
      {
        entityId: "select.pwm_installation_max_load",
        label: "Installation max load",
        icon: "mdi:fuse",
        type: "select",
      },
      {
        entityId: "select.pwm_pv_control",
        label: "PV control",
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
        entityId: "select.pwm_billing_period",
        label: "Billing period",
        icon: "mdi:calendar-month",
        type: "select",
      },
      {
        entityId: "select.pwm_grid_company",
        label: "Grid company",
        icon: "mdi:transmission-tower",
        type: "select",
      },
      {
        entityId: "select.pwm_electricity_tax",
        label: "Electricity tax",
        icon: "mdi:receipt-text",
        type: "select",
      },
      {
        entityId: "select.pwm_electricity_pricing_model",
        label: "Electricity pricing model",
        icon: "mdi:cash-multiple",
        type: "select",
      },
      {
        entityId: "number.pwm_fixed_price_agreement",
        label: "Fixed price agreement (\u00f8re)",
        icon: "mdi:cash",
        type: "number",
      },
      {
        entityId: "number.pwm_spot_price_surcharge",
        label: "Spot price surcharge (\u00f8re)",
        icon: "mdi:cash-plus",
        type: "number",
      },
      {
        entityId: "select.pwm_tariff_fuse_size",
        label: "Tariff fuse size (Ampere)",
        icon: "mdi:fuse",
        type: "select",
      },
    ];
    super.setConfig(config || {});
  }
}
customElements.define("pwm-electricity-card", PwMElectricityCard);

class PwMPvCard extends PwMngtBaseCard {
  setConfig(config) {
    this.cardTitle = (config && config.title) || "Solar PV System";
    this.fields = [
      {
        entityId: "select.pwm_pv_battery_size_kwh",
        label: "Battery size (kWh)",
        icon: "mdi:home-battery",
        type: "select",
      },
      {
        entityId: "select.pwm_pv_history_period_days",
        label: "History period (days)",
        icon: "mdi:history",
        type: "select",
      },
      {
        entityId: "select.pwm_pv_solar_inverter_max_ac_output_kw",
        label: "Solar inverter max AC output (kW)",
        icon: "mdi:solar-power",
        type: "select",
      },
      {
        entityId: "select.pwm_pv_pv_battery_price_difference",
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
