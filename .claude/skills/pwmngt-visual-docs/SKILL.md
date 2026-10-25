---
name: pwmngt-visual-docs
description: Use when Kasper or Claus asks to create or update the interactive "visual documentation" drill-down HTML page for PwMngt (docs/visual-documentation.html).
---

# PwMngt visual documentation

**Manually triggered only** - never part of `pwmngt-deploy`; docs update only when someone asks.

## Deliverable
1. `docs/visual-documentation.html` in the repo - one self-contained HTML file. Edit it in place.
2. Kasper's published copy: republish the same file to the existing artifact `https://claude.ai/artifact/1vHFVv6LYbsJhKBKetHivL` (same URL, so links keep working) when Kasper asks for it. Update the URL here if it changes.
3. Commit only that file (`git add docs/visual-documentation.html`, message like "Opdater visuel dokumentation"), after `git pull --autostash`, and push when asked - never `git add -A`.

## Source of truth — always read live, never from memory

Before rebuilding any level of the page, read fresh from the repo:
`manifest.json` (version), `devices.py` (device topology), `const.py`
(SEGMENT_* constants), `config_flow.py` (step order/docstrings), and the
actual current contents of every file in `data/`, `properties/`, and
`helpers/`. Don't extrapolate from an earlier session's summary or from how
a file "sounds like" it should behave — grep and read the real code every
time, including on an incremental update (Kasper has caught real drift this
way more than once, see "Device ownership" below).

Kasper also checks the *live HA device registry itself* (Settings →
Devices) against the docs, not just the source code — he has caught
misgrouping this way too. Treat what he reports seeing in HA as an
authoritative signal, on the same footing as a `_device_info()` grep.

If the repo can't be read, don't guess at file-level facts (triggers,
attributes, wrapped entities, methods) — tell Kasper plainly and wait for
the connection, rather than fabricating detail that looks authoritative but
isn't. Frontpage-level facts already verified earlier in the same session
(segment list, mandatory/optional status) are lower-risk to reuse with a
clear caveat, but file-level internals are not.

## Terminology

- **Device** (HA term): an actual Home Assistant device entry, built by one
  of `devices.py`'s `*_device_info()` functions — currently `hub_device_info`
  ("PwM"), `pv_device_info` ("PwM PV"), `charger_device_info` ("PwM
  Charger1"/"PwM Charger2"). 4 devices exist today; PV and both chargers
  nest under the Hub via `via_device`.
- **Segment** (internal term): an options-wizard page (`async_step_*`) —
  Solar PV Plant, EV Charging, Pool, PV Surplus — plus a 5th "Hub" card for
  general configuration that isn't tied to any one wizard page.

**These are not 1:1.** A segment can map to 0, 1, or 2 devices (EV Charging
→ up to 2 charger devices; Pool/PV Surplus → 0 devices today).

**The Hub card is NOT a free conceptual bucket for "cross-cutting"
files — it follows the same literal-device rule as every other segment.**
An earlier version of this skill allowed grouping a file under Hub because
its *design intent* was cross-cutting (e.g. a category list meant to grow
across future segments), even when its `_attr_device_info` literally
resolved to the PV device. Kasper corrected this twice (2026-09-24): once
for `pv_properties.py` (renamed from `hub_properties.py`), and again after
checking HA's own device registry and finding `consumption_snapshots_data.py`
and `consumption_averages_data.py` listed under "PwM PV", not "PwM" — both
were moved from the Hub box to the Solar PV Plant box as a result. **The
rule now has no exceptions: a file's box is decided purely by its literal
device, full stop.** Only `balance_data.py` genuinely belongs under Hub
(it calls `hub_device_info()`). If a file's design intent is genuinely
cross-segment but it currently lives on one specific device, say so in its
`note` — but file it under that device's segment, not Hub.

## Device ownership — the rule that must never drift

This part has caused real mismatches more than once (a data-layer file
grouped under Hub whose entity actually attaches to the PV device; an
inconsistency where one sibling file got a device note and a near-identical
one didn't; and grouping overridden by "but it's designed to be
cross-cutting" reasoning instead of the literal device). To stop that
recurring:

1. **For every `data/` file** (a `SensorEntity` with `self._attr_device_info
   = ...`): grep the literal `_device_info(` call in that file. That call
   — not which segment folder the file conceptually feels like it
   belongs to, not what its category list is *designed to grow into*, not
   what Kasper called it in conversation — is the file's `deviceKey`, and
   its `deviceKey` decides which segment/Hub box it's filed under. No
   exceptions, ever, even for a file that's explicitly cross-segment by
   design (put that design intent in its `note` instead).
2. **Only show a device chip when the file's device is a genuine mismatch**
   against the segment/Hub box it's filed under (`deviceChip()` returns
   `''` when the device matches — no chip at all — and only renders when it
   differs). Kasper flagged (2026-09-24) that `balance_data.py` under Hub
   was showing a redundant "PwM (Hub)" chip even though Hub genuinely is its
   device — a chip that never disagrees with the box it's sitting in isn't
   telling the reader anything, it's noise. So: no chip for the common case
   of a file living on its segment's own device; a chip (dashed/amber
   styled, in both the level-1 file-list row and the level-2 file-detail
   header) only for the deliberately-flagged exception where a file's real
   device differs from its segment's.
3. **For `properties/` files** (no `_attr_device_info` of their own — they
   just read entity-registry/state): figure out which device the entity or
   entities they read actually live on (trace the unique_id / entity lookup
   back to the sensor definition, e.g. which `*_SCAFFOLD_SENSORS` list it's
   in and what device_info that list's sensor class uses), and file the
   properties file under THAT device's segment too — same no-exceptions
   rule as data files.
   - **Worked example**: `hub_properties.py` was named and filed as if it
     were about the Hub, but its one function (`is_forced_charging()`)
     reads a sensor filed under `PwM_PV_SCAFFOLD_SENSORS` in sensor.py —
     genuinely a PV-device sensor. Once confirmed, Claude flagged this to
     Kasper plainly (didn't rename anything unasked); Kasper agreed and
     asked for the rename. Claude renamed the file to `pv_properties.py`,
     updated its docstring and every import/call site
     (`properties/__init__.py`, `data/balance_data.py`), verified no
     leftover references, and moved its documentation entry from the Hub
     box to the Solar PV Plant box.
4. **When a file's grouping needs correcting** (Kasper reports a mismatch,
   either from reading code or from HA's live device registry): move the
   file's entire content-model entry to the correct segment's array,
   rewrite its `note` to drop any "grouped under X because..." rationale
   that no longer applies, and update the losing segment's `description`
   text too if it referenced that file's function (e.g. Hub's description
   used to mention "consumption snapshots" — removed once that file moved
   out).
5. **No dedicated Hub device-registry diagram.** An earlier version of this
   page had a "Home Assistant device registry" box on the Hub view — a
   literal box-inside-a-box showing PwM containing PwM PV/Charger1/Charger2.
   Kasper had Claude build two different versions of this (a tree, then the
   containment box) and ultimately asked for the whole thing removed
   (2026-09-24) because it visually disturbed the page ("forstyrrer
   billedet"). **Do not rebuild any version of this diagram unless Kasper
   explicitly asks for one again** — the device-vs-segment relationship is
   still explained in prose in the Terminology section and via the per-file
   device chips (now shown only for genuine mismatches, see point 2 above);
   that's considered sufficient.

## Frontpage (level 0)

Heading "Home Assistant Integration Power Management"; a live version chip
read from `manifest.json`. One card per segment (Solar PV Plant, EV
Charging, Pool, PV Surplus) plus a 5th Hub card. Each card shows title,
badge, and a short description.

- **Hub badge text is just "PwM"** (plus the pulsing active-dot) — not
  "PwM · Always active". Kasper simplified this (2026-09-24): the dot
  already signals "live", the text only needs to name the device.
- **Card alignment fix — the real root cause.** The 4 segment cards are
  `<button>` elements laid out in a single-row CSS Grid
  (`grid-template-columns: repeat(4, 1fr)`), each stretched to the same
  height by the grid's default `align-items: stretch`. Kasper reported
  (twice, 2026-09-24) that the icon/badge row at the top of each card
  (`.card-top`) wasn't level across cards. The first attempted fix
  (`min-height` + centered `align-items` on `.card-top`) did NOT work —
  confirmed both by Kasper's screenshots and by Playwright measurement.
  Root cause, found via Playwright (swap-testing which part of a card's
  content the offset followed — it tracked `.card-desc`'s text, not the
  icon or badge): Chromium's `<button>` element retains legacy internal
  layout behavior that vertically centers a button's rendered content when
  the button is stretched taller than its own content needs (e.g. by the
  grid row's `align-items: stretch`) — `display: block` on `.card-base`
  does NOT fully opt out of this quirk in Chrome, even though it's the
  spec-correct value. Cards whose description wrapped to fewer lines (a
  shorter natural content height) got their whole content block nudged
  down inside the stretched button, while the card with the longest
  description (whose natural height already filled the stretched height,
  leaving no slack to center) stayed put — producing the exact "only some
  cards are off" pattern Kasper saw, correlated with description length,
  not with icon or badge. **The actual fix: `.card-base` uses
  `display: grid` instead of `display: block`.** A single-column grid
  behaves like block for these stacked children but fully opts the button
  out of Chromium's centering quirk. Confirmed via Playwright
  (`getBoundingClientRect()` on `.card-top` across all 4 cards, before and
  after) that this makes every card's `.card-top` land at an identical
  offset. **Don't revert `.card-base` back to `display: block` without
  re-testing this** — it looks like a harmless equivalent swap but isn't,
  given this Chromium behavior.

Every clickable/hoverable element needs a clear, consistent visual signal
(hover lift, hint text, icon motion) so it's obvious it's interactive.

## Drill-down level 1 (segment/Hub detail)

The segment header (top of the level-1 page) shows an icon, the title, a
badge, and a description.

- **Icon**: reuses the exact same icon markup/color class used for that
  segment/device on the frontpage overview card (`.card-icon icon-<key>` +
  the same inline SVG from the shared `ICON` object) — do not invent a
  separate icon for this view. History: Kasper first asked to remove the
  per-segment icon from this header (2026-09-24) as redundant ("it's
  obviously given which segment we're on"); shortly after, in the same day,
  he asked to bring it back specifically by reusing the icons already built
  for the frontpage/Hub cards ("De Ikoner du har oprette for hver device
  (og for Hub'en), må du gerne genbruge når man klikker sig ind på hubben
  eller devicet") rather than removing it outright. Net effect: the icon IS
  shown here again, reused rather than newly designed.
- **Device-name tag(s)**: a compact device-name tag (small uppercase pill,
  styled like the Mandatory/Optional badge) sits inline next to that badge.
  Skipped for Hub (its badge already says "PwM"). Three cases:
  - A segment with exactly one real device (Solar PV Plant → "PWM PV"):
    one tag, solid color, sourced from `DEVICE_META`/`SEGMENT_HOME_DEVICE`
    (the literal-device-grounded pair).
  - EV Charging maps to **2** distinct real devices (PwM Charger1, PwM
    Charger2), so it shows **2 separate tags** — "PWM CHARGER1" and
    "PWM CHARGER2" — not one combined "PwM Charger1/2" label. Kasper
    corrected this (2026-09-24): the combined label reads as one thing when
    it's actually two real devices. `DEVICE_META.charger`'s combined label
    is still used elsewhere (the generic per-file device chip, since a
    `consumption_charger_data.py`-type file doesn't distinguish which of
    the two chargers it's instantiated for) — only this header tag splits
    it, handled as a special case in `segmentDeviceTag()`.
  - Pool and PV Surplus have no real HA device yet, but Kasper explicitly
    asked for tags there too (2026-09-24, after Claude initially flagged
    the literal-device tension and asked): "PWM POOL" and
    "PWM PV SURPLUS". **These are handled through a separate
    `SEGMENT_PLANNED_LABEL` map, deliberately kept OUT of
    `SEGMENT_HOME_DEVICE`/`DEVICE_META`** (which must stay grounded in
    devices.py's literal `*_device_info()` functions, since that pair also
    drives the cross-device mismatch check in `deviceChip()`). The planned
    tag renders with a visibly different style (`.badge-device.dev-planned`
    — dashed border, muted color, not the solid per-device color) and a
    tooltip explaining it's a planned name, not a registered device — so it
    can't be mistaken for a claim that the device exists. If a segment like
    this gets a real device later, move its entry from
    `SEGMENT_PLANNED_LABEL` to `SEGMENT_HOME_DEVICE`/`DEVICE_META` instead
    of just changing the label text, so it starts participating in the
    cross-device check too.

Below the header, clicking a card shows 3 boxes matching PwMngt's folder
layout 1:1 — Data layer (`data/`), Properties/Wrapper layer
(`properties/`), Helper layer (`helpers/`) — each listing only the files
whose literal device matches that segment/Hub (see "Device ownership"
above — no cross-cutting exceptions). Show empty boxes plainly ("No files
here yet."), don't hide them. There is no device-registry diagram on this
page — see point 5 under "Device ownership" above.

## Drill-down level 2 (file detail)

Clicking a filename opens a file-detail view. The file header shows the
type badge, filename, and (if genuinely cross-device) the device chip; it
also shows "Implements `<ClassName>`" for data and helper files, but
**not for properties files** (see below).

- **Data file**: triggers (time/interval/entity, color-coded by kind), then
  "Attributes calculated" as a **list** (`.wraps-list`/`.wraps-item`,
  reusing the same component the properties layer uses for "Entities
  wrapped" — see below) — each attribute shows its name, then an
  **example value tag**, then a **data type tag**, in that order (Kasper
  corrected the order, 2026-09-24: name → example → type reads more
  naturally than name → type → example). Both tags are terse — the type
  tag is just the type itself (`float (kWh)`, `int (W)`, `str (ISO date)`,
  `ENUM`), never a clause explaining what the value means or how it's
  computed (that belongs in the file's `note`, not the type tag; Kasper
  trimmed things like "float — last raw source reading, used to compute
  the next delta" down to plain `float`, and "float (kWh) — running
  since-midnight total (the sensor's own state, not an extra attribute)"
  down to plain `float (kWh)`). Sourced from actually reading the
  sensor's code (the calculation function, the `SensorEntityDescription`,
  what `_attr_extra_state_attributes` actually holds), never guessed.
  Internal/bookkeeping attributes (leading underscore, e.g.
  `_midnight_baseline_property`, `_last_raw`) get the `.wraps-item.internal`
  treatment — dimmed text, dashed/faint type+example tags — same
  de-emphasis Kasper originally asked for on the old flat-chip layout,
  just carried over to the list. The type tag uses the data layer's own
  color (`.wraps-chip.type.data-type`) so it's visually distinct from the
  properties layer's purple type tag. Show the device chip in the header
  only if it's a genuine cross-device mismatch (see "Device ownership"
  point 2) — most files show no chip here since they live on their
  segment's own device.
- **Properties/Wrapper file**: the entities it wraps/reads, each listed
  with (a) just the entity reference itself (e.g. `sensor.<hub>_pv_forced_charge`,
  code-styled) — **not a prose description of it** (Kasper had Claude trim
  "PwMngt's own "Forced charge" scaffold sensor (...)" down to just the
  bare entity reference, 2026-09-24: keep this view terse, the entity
  reference is enough), (b) an example value, then (c) a data type tag —
  same name → example → type order and same terseness rule as data files
  above. E.g. `is_forced_charging()` wrapping an ENUM sensor state: example
  `On`, type `ENUM` — NOT the fuller "ENUM sensor state → wrapped as
  Python bool" / `"on" → True / "off" or unregistered → False` that an
  earlier round had; Kasper wants the glanceable version, the fuller
  mapping logic is what the code itself is for. Show the "reads a sensor
  on" chip only for a genuine cross-device mismatch, same rule as data
  files. **Do not show "Implements `<cls>`" in
  the file header for a properties file** (Kasper corrected this,
  2026-09-24: a wrapper file's whole point is to hold *several* wrapped
  entities/functions, so naming just one function there — e.g.
  `is_forced_charging()` — reads as if that's the file's only job; drop
  the `cls` field from a properties file's content-model entry entirely
  rather than just hiding it, so it isn't there to accidentally render
  again later). **Do not render the `note` block for properties files** —
  Kasper asked for source-code-derived commentary to stay out of this view
  specifically (data/helpers views still show notes).
- **Helper file**: its static methods, each with signature + description.

**Notes must not carry migration/history commentary.** A file's `note`
should describe the file's current behavior/design intent, never *how it
got there* — no "moved here from Hub because...", no "Kasper spotted
this in HA's device registry...". This was already the rule in "Device
ownership" point 4 ("rewrite its `note` to drop any 'grouped under X
because...' rationale") but wasn't fully carried out the first time:
`consumption_snapshots_data.py` and `consumption_averages_data.py` still
had their full "moved here from Hub (2026-09-24) — Kasper spotted in HA's
own device registry..." sentences in their notes months (well, one round)
after the actual move. Kasper flagged this explicitly (2026-09-24): "a
comment from the code (or wherever it now comes from) like [that] should
never be shown." Fixed by rewriting both notes down to only their
forward-looking/design content (category list still designed to grow;
pure-scaffolding status) — apply the same trim any time a note is
inherited across a re-grouping.

## Visual style

Kasper's stated preference (2026-09-24): make icons and color usage bigger
and bolder, with "lir og gøjl" (extra visual flourish/liveliness) in the
CSS — not the more minimal/restrained treatment used in the very first
draft. In practice this means: larger icon badges with per-segment/device
tinted backgrounds (not flat grey for everything, on the frontpage cards),
a bit of springy hover motion on icons and cards (scale/rotate), a subtle
gradient accent that appears on card hover, and a gentle pulse on the Hub's
active-dot indicator. Keep `prefers-reduced-motion` respected (disable both
transitions and animations under that media query). Keep the overall
typography system (Sora / Public Sans / IBM Plex Mono) and the light/dark
token structure from the original build.

Note the tension with the later (also 2026-09-24) round of feedback: more
flourish on the frontpage cards is still wanted, but a literal diagram
(the device-registry box) was judged as visual clutter and removed, while
the per-segment icon on the level-1 header was removed and then explicitly
brought back (see "Drill-down level 1" above) — so "remove visual clutter"
doesn't automatically mean "never show an icon there again"; when Kasper
reverses a prior request, treat the newest instruction as current and
update this file accordingly rather than assuming the older note still
holds. Read the overall guidance as "flourish in service of clarity, not
decoration for its own sake" — when in doubt about whether a new visual
element helps or clutters, it's reasonable to ask Kasper before building a
large new diagram from scratch, even though small style tweaks (colors,
motion, spacing) don't need to be checked first.

## Debugging layout bugs - measure, don't guess
When an alignment/spacing bug isn't obvious from the CSS, open the file in the built-in browser (`preview_start` with a `file:///` URL or a quick static server) and measure with `getBoundingClientRect()`/`getComputedStyle()` via `javascript_tool` across the elements that look different. If computed styles match but positions differ, swap content between the elements one piece at a time and re-measure - the discrepancy follows the responsible piece (that's how the `.card-base` button-centering bug was found). Test a fix by injecting CSS in the page before editing the file.

## Navigation

Single-page, in-page JS show/hide (no reloads), hash-based deep-linking
(`#segment-key`, underscores as hyphens, bare token only — no query
strings, per Artifact tool rules). Breadcrumb trail at every non-overview
level.

## Status / open questions

All 3 drill-down levels are built as of 2026-09-24, along with the
cross-device chip system (shown only for genuine mismatches) and the
stricter literal-device-only grouping rule (no "cross-cutting" exceptions).
The Hub device-registry diagram was built, then removed the same day —
don't reintroduce it without being asked (see "Device ownership" point 5).
The frontpage card-alignment bug (root cause: Chromium's `<button>`
centering quirk under CSS Grid stretch) was fixed the same day via
`display: grid` on `.card-base` — see "Frontpage (level 0)" above. The EV
Charging header tag was split into two ("PWM CHARGER1"/"PWM CHARGER2"), and
Pool/PV Surplus gained "planned" device-name tags (see "Drill-down level 1"
above for the `SEGMENT_PLANNED_LABEL` mechanism). The level-2 file-detail
view was also reworked: data-layer attributes and properties-layer wrapped
entities both show as name → example → type (in that order), with both
tags kept terse (bare type, no explanatory clause); notes no longer carry
migration-history commentary; and properties files drop their misleading
"Implements <fn>()" header line. Current file grouping as of that date:
Hub → `balance_data.py` only. Solar PV Plant →
`consumption_snapshots_data.py`, `consumption_averages_data.py`,
`pv_properties.py`. EV Charging → `consumption_charger_data.py`. Pool / PV
Surplus → no files yet. This will drift as the integration grows — always
re-verify via grep/live-HA rather than trusting this list. Keep updating
this SKILL.md via improvement proposals whenever the design or integration
structure changes meaningfully enough to change how future rebuilds should
work.