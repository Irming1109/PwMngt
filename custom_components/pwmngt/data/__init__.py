"""PwMngt "_data" sensors.

A "_data" sensor's value is maintained by an ongoing internal trigger
(a timer, later an event listener) rather than mirroring one external
entity -- see the naming-convention comment next to
PwM_BALANCE_DATA_ATTRIBUTES in sensor.py.

Each module in this package follows the same shape: a plain
"calculate_*" function that turns raw sample data into the computed
values (no Home Assistant state touched), plus a SensorEntity class that
wires a trigger to it and writes the result out.
"""
