---
name: pwmngt-nodered-reference
description: Use whenever a PwMngt question needs Claus's or Kasper's Node-RED automations (how the old automation decides X, where a value is used, porting a function) - always read the newest dated exported flow JSON, never live Node-RED.
---

# PwMngt Node-RED reference

The source of truth for what the original automations do is the **exported flow JSON** ("Export > all flows" in Node-RED) - never live Node-RED.

## Where the files are
- Kasper's machine: `C:\Temp\CurrentHA\NodeRed\` (flows) and `C:\Temp\CurrentHA\homeassistant\` (`configuration.yaml`, `template_sensors/`, `entities/` - check these for template sensors that are neither PwMngt nor Node-RED).
- Claus's machine: see his `CLAUDE.local.md`. If the folder is missing, say so and ask for the path/export - never fall back to browsing Node-RED.
- Files are named `All tabs - all flows <Person> <DD-MM-YYYY>.json`. Re-list the folder each time; new exports arrive.

## Rules
- **Use the newest file per person by the date in the filename** - parse it (`datetime.strptime(d, "%d-%m-%Y")`), not mtime and not a string sort.
- **Claus's newest export is the reference** for logic; Kasper's may differ.
- Open older files only to compare ("what changed since last export"). Name the dated file a finding came from.
- Why not live: Claus's Node-RED can't be reached at all, and clicking around Kasper's live editor is slow and risks editing a running automation. The editor's search box also missed matches inside function bodies - full-text search of the JSON is more thorough.

## How to search
The export is a flat JSON array of nodes: `id`, `type`, `name`, `z` (tab/subflow id), `d` (node disabled). Tabs/subflows are nodes with `type: "tab"`/`"subflow"` and a `disabled` flag. Dead copies exist (Claus's disabled tab "Skraldespand", nodes named "backup"/"ny udgave") - check before treating a match as live.

```python
import json, re
data = json.load(open(newest_claus_path, encoding="utf-8"))
tabs = {n["id"]: n for n in data if n.get("type") in ("tab", "subflow")}
for n in data:
    if n.get("type") == "function" and re.search(r"keyword", n.get("func") or "", re.I):
        tab = tabs.get(n.get("z"), {})
        print(n.get("name"), "| tab:", tab.get("label") or tab.get("name"), "| tab disabled:", tab.get("disabled"), "| node disabled:", n.get("d"))
```

- Search the `func` body text, not only names. Function code reads state as `ha.homeAssistant.states["sensor.xxx"]`, so grepping the raw entity_id is the most reliable.
- `ha-entity-config` nodes define the entity an `ha-sensor`/`ha-entity` node writes; match their `id` against other nodes' `entityConfig` to tell writers from readers.
- Resolve `z` all the way to a tab/subflow label - Claus's and Kasper's layouts differ (e.g. Kasper's "Strøm beregninger" sits in subflow "Sol prognose i dag").
- Run Python with `-I` when reading these files.
