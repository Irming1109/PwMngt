"""Read-only property accessors, grouped one file per device/segment (e.g.
hub_properties.py for the PwM hub device).

Each function hides the entity-registry lookup and state parsing behind a
single, safe call -- callers get a plain answer (a bool, a number, ...)
without repeating "is the entity even registered yet" / "does it have a
real value yet" checks themselves. These are read-only accessors for
values a scaffold/data sensor already owns and computes (see the data/
and the scaffold-sensor conventions in sensor.py) -- not a new source of
state, just a friendlier way for other Python code in this integration to
read it. Home Assistant dashboards and automations still read the
underlying entity directly; this package is for use from Python code
only.
"""
