"""
The data model that represents a GivEnergy system.

From a modbus perspective, devices present themselves as collections
of 16-bit numbered registers. An instance of *Plant* is used to cache
the values of these registers for the various devices (inverter and
batteries) making up your system.

Then from the plant you can access an Inverter and an array of Battery
instances - these interpret the low-level modbus registers as higher-level
python datatypes.

Note that the model package provides read-only access to the state of
the system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import IntEnum

class DefaultUnknownIntEnum(IntEnum):
    """Enum that returns unknown instead of blowing up."""

    @classmethod
    def _missing_(cls, value):
        return cls.UNKNOWN  # type: ignore[attr-defined] # must be defined in subclasses because of Enum limits


@dataclass
class TimeSlot:
    """Dataclass to represent a time slot, with a start and end time."""

    # TODO perhaps just store as GE integers - (h * 100 + m) and
    # convert to time on demand (making start and end properties ?)
    start: time
    end: time

    @classmethod
    def from_components(
        cls, start_hour: int, start_minute: int, end_hour: int, end_minute: int
    ):
        """Shorthand for the individual datetime.time constructors."""
        return cls(time(start_hour, start_minute), time(end_hour, end_minute))

    @classmethod
    def from_repr(cls, start: int | str, end: int | str):
        """Converts from human-readable/ASCII representation: '0034' -> 00:34."""
        if isinstance(start, int):
            start = f"{start:04d}"
        start_hour = int(start[:-2])
        start_minute = int(start[-2:])
        if isinstance(end, int):
            end = f"{end:04d}"
        end_hour = int(end[:-2])
        end_minute = int(end[-2:])
        return cls(time(start_hour, start_minute), time(end_hour, end_minute))

    def __contains__(self, t: time|int) -> bool:
        """Implements 'in' operator.

        Parameter is either a time, or an integer in (hours * 100 + minute) format.
        """

        if self.start == self.end:
            return False

        if isinstance(t, int):
            t = time(t // 100, t % 100)
        elif not isinstance(t, time):
            # TODO throw an exception?  Return NotImplemented?
            return False

        # now "inside" depends on whether the timeslot
        # spans midnight

        if self.start < self.end:
            return self.start <= t < self.end
        else:
            return not (self.end <= t < self.start)
