![Current Release](https://img.shields.io/github/release/Irming1109/PwMngt/all.svg?style=plastic)
![Github All Releases](https://img.shields.io/github/downloads/Irming1109/PwMngt/total.svg?style=plastic)
<!--![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=plastic)-->

# Power Management (PwMngt)

A Home Assistant custom integration for monitoring and managing home power: a Solar PV Plant (production, battery, consumption and spot price) and, as it's built out, EV charger control, a pool segment, and PV-surplus automation. It's a native re-implementation of automations originally built in Node-RED.

Not suitable for general usage yet -- the Solar PV Plant side is live, the rest is still under active development.

## Table of Content

[**Installation**](#installation)

[**Setup**](#setup)

[**Status**](#status)

## Installation

### Add custom repository to HACS:

*   See [this link](https://www.hacs.xyz/docs/faq/custom_repositories/) for how to add a custom repository to HACS.
*   Add `https://github.com/Irming1109/PwMngt` as a custom repository of type Integration.
*   Search for and install the "Power Management" integration.
*   Restart Home Assistant.

### Manual installation:

*   Download the latest release.
*   Unpack the release and copy the `custom_components/pwmngt` directory into the `custom_components` directory of your Home Assistant installation.
*   Restart Home Assistant.

## Setup

Go to Home Assistant > Settings > Devices & services > Add integration, and add "Power Management" _(if it doesn't show, try CTRL+F5 to force a refresh of the page)_.

The setup wizard first asks you to map PwMngt's Solar PV Plant fields (battery, PV production, grid, spot price, etc.) to your own existing entities -- this segment is mandatory. You can then opt in to the EV Charging, Pool and PV Surplus segments, which are still being built out (see Status below).

The same wizard is reachable afterwards from the integration's "Configure" option, to change entity mappings or segments later.

## Status

Live and working:

*   The Hub and Solar PV Plant sensors (PV production, battery, grid, spot price, and the derived power-balance sensor).
*   Since-local-midnight consumption tracking (property and per-charger), including backfilling today's readings from Home Assistant history on first setup.

Still scaffolding (entities exist so the dashboard cards have something to bind to, but nothing behind them does anything yet):

*   EV charger control (buttons, numbers, selects for each charger).
*   The Pool and PV Surplus segments.
