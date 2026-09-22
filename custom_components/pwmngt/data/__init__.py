"""PwMngt "_data" sensors.

A "_data" sensor's value is maintained by an ongoing internal trigger
(a timer, later an event listener) rather than mirroring one external
entity -- see the naming-convention comment next to
PwM_BALANCE_DATA_ATTRIBUTES in sensor.py for the full explanation.

Each one gets its own module in this package, built the same way:
a small, plain "calculate_*" function that takes raw sample data in and
returns the computed values out (no Home Assistant state touched at all),
plus the SensorEntity class that wires a trigger to that function and
writes its result to the entity. That's the same "trigger -> function ->
outputs" shape as the Node-RED flows these sensors were ported from --
the calculate_* functions are the closest Python equivalent to a Node-RED
function node, and are the part most worth reading if you're coming from
that side rather than from Home Assistant/Python.
"""
