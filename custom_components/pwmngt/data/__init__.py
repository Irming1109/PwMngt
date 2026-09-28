"""PwMngt "_data" sensors.

A "_data" sensor's value is maintained by an ongoing internal trigger
(a timer, later an event listener) rather than mirroring one external
entity -- see the naming-convention comment next to
PwM_BALANCE_DATA_ATTRIBUTES in sensor.py.

Each module in this package follows the same shape: a plain
"calculate_*" function that turns raw sample data into the computed
values (no Home Assistant state touched), plus a SensorEntity class that
wires a trigger to it and writes the result out.

State of a "_data" sensor
-------------------------
Some "_data" sensors have a natural single value to show as their state
(balance_data: the 30-second balance in W). Others only carry
attributes (consumption_data). Those must NOT be left at None: Home
Assistant would show "Unknown", which reads as missing data or an error
even though everything is fine. Set their state to DATA_SENSOR_STATE
instead -- a neutral, constant marker meaning "the values are in the
attributes". Use it for every current and future attribute-only
"_data" sensor.

Deliberately not "OK": the constant does not claim the values are
healthy (e.g. a history can still be empty), only that the sensor is
running and its data lives in the attributes. Before the entity is
added it is unavailable (_attr_available = False), not "Data".
"""

# Constant state for attribute-only "_data" sensors -- see above.
DATA_SENSOR_STATE = "Data"
