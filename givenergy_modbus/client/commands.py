"""High-level methods for interacting with a remote system.

NOTE: it is no longer intended that applications import this
directly. Instead, they should access commands via client.commands

These don't actually send requests to the inverter.
They simply prepare sequences of requests that need to be sent using
the client.
"""

import logging
from datetime import datetime
from textwrap import dedent
from typing import Any, Callable, Optional
from typing_extensions import deprecated  # type: ignore[attr-defined]

# TODO: move DynamicDoc somewhere more generic

from .client import Client
from ..model import TimeSlot
from ..model.inverter import (
    Inverter,
)
from ..model.register import DynamicDoc
from ..pdu import (
    ReadHoldingRegistersRequest,
    ReadInputRegistersRequest,
    TransparentRequest,
    WriteHoldingRegisterRequest,
)

_logger = logging.getLogger(__name__)


class Commands(metaclass=DynamicDoc):
    # pylint: disable=missing-class-docstring
    # The metaclass turns accesses to __doc__ into calls to _gendoc()

    _DOC = """High-level methods for interacting with a remote system."""

    def __init__(self, client: Client):
        self.client = client

    # Helper to look up an inverter holding register by name
    # and prepare a write request. Value range checking gets
    # done automatically.
    def write_named_register(self, name: str, value: int) -> TransparentRequest:
        """Prepare a request to write to a register."""
        idx = Inverter.lookup_writable_register(name, value)
        return WriteHoldingRegisterRequest(idx, value)

    # rather than writing lots of trivial setter methods,
    # this translates implicit commands into calls to helpers:
    #   commands.set_xyz(value) -> commands._set_helper('xyz', value)
    #   commands.reset_XXX_slot_N() -> commands._set_timeslot('XXX_slot_N', None)
    # If 'value' is a Timeslot, _set_helper(name) calls _set_timeslot(name)
    #
    # Invoking commands.xyz(value) is a two-step process:
    #  callable = getattr(commands, 'xyz')
    #  callable(value)
    # so __getattr__ returns a lambda that supplies the name and
    # takes the value as a parameter.

    def __getattr__(self, name: str) -> Callable:
        """Fabricate a set_xyz() or reset_x_slot_y() method."""
        if name.startswith("reset_") and "_slot" in name:
            if name[6:] in Inverter.REGISTER_LUT:
                return lambda: self._set_timeslot(name[6:], None)
        elif name.startswith("set_"):
            if name[4:] in Inverter.REGISTER_LUT:
                return lambda value: self._set_helper(name[4:], value)
        raise AttributeError(f"No {name} in {__name__}")

    # This is a generic register setter, usually invokved via __getattr__

    def _set_helper(self, name: str, value: Any) -> list[TransparentRequest]:
        """Helper for anonymous commands.

        Usually equivalent to [ write_named_register() ], unless value
        is a TimeSlot, in which case it calls _set_timeslot().
        """
        _logger.debug("commands._set_helper: %s %s", name, value)
        if isinstance(value, TimeSlot):
            return self._set_timeslot(name, value)
        # Otherwise just a single register access
        return [self.write_named_register(name, int(value))]

    # A helper to write a timeslot to a pair of adjacent time registers
    # Assumes the time registers are called {name}_start and {name}_end

    def _set_timeslot(
        self, name: str, value: TimeSlot | None
    ) -> list[TransparentRequest]:
        """Set a pair of start/end time slots.

        A value of None is interpreted to mean TimeSlot(0,0,0,0).
        """
        if value is None:
            start = 0
            end = 0
        else:
            start = 100 * value.start.hour + value.start.minute
            end = 100 * value.end.hour + value.end.minute
        return [
            self.write_named_register(name + "_start", start),
            self.write_named_register(name + "_end", end),
        ]

    def refresh_plant_data(
        self, complete: bool, number_batteries: int = 1, max_batteries: int = 5
    ) -> list[TransparentRequest]:
        """Refresh plant data."""
        requests: list[TransparentRequest] = [
            ReadInputRegistersRequest(
                base_register=0, register_count=60, slave_address=0x32
            ),
            ReadInputRegistersRequest(
                base_register=180, register_count=60, slave_address=0x32
            ),
        ]
        if complete:
            requests.append(
                ReadHoldingRegistersRequest(
                    base_register=0, register_count=60, slave_address=0x32
                )
            )
            requests.append(
                ReadHoldingRegistersRequest(
                    base_register=60, register_count=60, slave_address=0x32
                )
            )
            requests.append(
                ReadHoldingRegistersRequest(
                    base_register=120, register_count=60, slave_address=0x32
                )
            )
            requests.append(
                ReadInputRegistersRequest(
                    base_register=120, register_count=60, slave_address=0x32
                )
            )
            number_batteries = max_batteries
        for i in range(number_batteries):
            requests.append(
                ReadInputRegistersRequest(
                    base_register=60, register_count=60, slave_address=0x32 + i
                )
            )
        return requests

    def disable_charge_target(self) -> list[TransparentRequest]:
        """Removes SOC limit and target 100% charging."""
        return [
            self.write_named_register('enable_charge_target', False),
            self.write_named_register('charge_target_soc', 100),
        ]

    def set_charge_target(self, target_soc: int) -> list[TransparentRequest]:
        """Sets inverter to stop charging when SOC reaches the desired level. Also referred to as "winter mode"."""
        ret = self.set_enable_charge(True)
        if target_soc == 100:
            ret.extend(self.disable_charge_target())
        else:
            ret.append(
                self.write_named_register('enable_charge_target', True),
            )
            ret.append(
                self.write_named_register('charge_target_soc', target_soc),
            )
        return ret

    def set_inverter_reboot(self) -> list[TransparentRequest]:
        """Restart the inverter."""
        return [self.write_named_register('inverter_reboot', 100)]

    def set_calibrate_battery_soc(self) -> list[TransparentRequest]:
        """Set the inverter to recalibrate the battery state of charge estimation."""
        return [self.write_named_register('soc_force_adjust', 1)]

    @deprecated("use set_enable_charge(True) instead")
    def enable_charge(self) -> list[TransparentRequest]:
        """Enable the battery to charge, depending on the mode and slots set."""
        return self.set_enable_charge(True)

    @deprecated("use set_enable_charge(False) instead")
    def disable_charge(self) -> list[TransparentRequest]:
        """Prevent the battery from charging at all."""
        return self.set_enable_charge(False)

    @deprecated("use set_enable_discharge(True) instead")
    def enable_discharge(self) -> list[TransparentRequest]:
        """Enable the battery to discharge, depending on the mode and slots set."""
        return self.set_enable_discharge(True)

    @deprecated("use set_enable_discharge(False) instead")
    def disable_discharge(self) -> list[TransparentRequest]:
        """Prevent the battery from discharging at all."""
        return self.set_enable_discharge(False)

    def set_discharge_mode_max_power(self) -> list[TransparentRequest]:
        """Set the battery discharge mode to maximum power, exporting to the grid if it exceeds load demand."""
        return [self.write_named_register('battery_power_mode', 0)]

    def set_discharge_mode_to_match_demand(self) -> list[TransparentRequest]:
        """Set the battery discharge mode to match demand, avoiding exporting power to the grid."""
        return [self.write_named_register('battery_power_mode', 1)]

    @deprecated("Use set_battery_soc_reserve(val) instead")
    def set_shallow_charge(self, val: int) -> list[TransparentRequest]:
        """Set the minimum level of charge to maintain."""
        return self.set_battery_soc_reserve(val)

    # TODO: this needs a bit more finesse
    # client.exec() does everything in parallel, and therefore in random
    # order. Will take several elapsed seconds to send all the components.
    # If either new or target seconds is close to 60, then the minutes
    # may not end up set correctly.
    # Should probably accept dt of None to means "now", and then it can
    # do things in a suitable order to ensure that the target time is
    # properly synchronised (eg send seconds first, unless it's close
    # to 60, in which case maybe send year/month/day, then wait for seconds
    # to wrap, then send hour/min/sec

    def set_system_date_time(self, dt: datetime) -> list[TransparentRequest]:
        """Set the date & time of the inverter."""
        return [
            self.write_named_register("system_time_year", dt.year - 2000),
            self.write_named_register("system_time_month", dt.month),
            self.write_named_register("system_time_day", dt.day),
            self.write_named_register("system_time_hour", dt.hour),
            self.write_named_register("system_time_minute", dt.minute),
            self.write_named_register("system_time_second", dt.second),
        ]

    def set_mode_dynamic(self) -> list[TransparentRequest]:
        """Set system to Dynamic / Eco mode.

        This mode is designed to maximise use of solar generation. The battery will
        charge from excess solar generation to avoid exporting power, and discharge
        to meet load demand when solar power is insufficient to avoid importing power.
        This mode is useful if you want to maximise self-consumption of renewable
        generation and minimise the amount of energy drawn from the grid.
        """
        # r27=1 r110=4 r59=0
        return (
            self.set_discharge_mode_to_match_demand()
            + self.set_battery_soc_reserve(4)
            + self.set_enable_discharge(False)
        )

    def set_mode_storage(
        self,
        discharge_slot_1: TimeSlot = TimeSlot.from_repr(1600, 700),
        discharge_slot_2: Optional[TimeSlot] = None,
        discharge_for_export: bool = False,
    ) -> list[TransparentRequest]:
        """Set system to storage mode with specific discharge slots(s).

        This mode stores excess solar generation during the day and holds that energy
        ready for use later in the day. By default, the battery will start to discharge
        from 4pm-7am to cover energy demand during typical peak hours. This mode is
        particularly useful if you get charged more for your electricity at certain
        times to utilise the battery when it is most effective. If the second time slot
        isn't specified, it will be cleared.

        You can optionally also choose to export excess energy: instead of discharging
        to meet only your load demand, the battery will discharge at full power and any
        excess will be exported to the grid. This is useful if you have a variable
        export tariff (e.g. Agile export) and you want to target the peak times of
        day (e.g. 4pm-7pm) when it is most valuable to export energy.
        """
        if discharge_for_export:
            ret = self.set_discharge_mode_max_power()  # r27=0
        else:
            ret = self.set_discharge_mode_to_match_demand()  # r27=1
        ret.extend(self.set_battery_soc_reserve(100))  # r110=100
        ret.extend(self.set_enable_discharge(True))  # r59=1
        ret.extend(self.set_discharge_slot_1(discharge_slot_1))  # r56=1600, r57=700
        if discharge_slot_2:
            ret.extend(self.set_discharge_slot_2(discharge_slot_2))  # r56=1600, r57=700
        else:
            ret.extend(self.reset_discharge_slot_2())
        return ret

    # This is invoked when __doc__ is accessed.
    @classmethod
    def _gendoc(cls):
        """Construct a docstring from fixed prefix and register list."""

        doc = cls._DOC + dedent(
            """

        In addition to the explicitly defined methods, the following list was
        automatically generated from the register definition list. They are
        fabricated at runtime via ``__getattr__``.
        Note that the actual set of commands available depends on the inverter
        model.

        Because these attributes are not listed in ``__dict__`` they may not be
        visible to all python tools.
        Some appear multiple times as aliases.\n\n"""
        )

        for reg, defn in Inverter.REGISTER_LUT.items():
            if defn.valid is not None:
                doc += '* set_' + reg + "()\n"
                if '_slot_' in reg and reg.endswith('_end'):
                    doc += '* set_' + reg[:-4] + "()\n"
                    doc += '* reset_' + reg[:-4] + "()\n"
        return doc
