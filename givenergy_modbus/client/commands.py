"""High-level methods for interacting with a remote system.

Note that these don't actually send requests to the inverter.
They simply prepare lists of requests that need to be sent using
the client.
"""

# see end of file for code that dynamically appends to the docstring

from datetime import datetime
import logging
from textwrap import dedent
from typing import Optional, Any
from typing_extensions import deprecated  # type: ignore[attr-defined]

from ..model import TimeSlot
from ..model.inverter import (
    Inverter,
)
from ..pdu import (
    ReadHoldingRegistersRequest,
    ReadInputRegistersRequest,
    TransparentRequest,
    WriteHoldingRegisterRequest,
)

_logger = logging.getLogger(__name__)

# TODO: This list is deprecated. Use write_named_register() to find the
# register number from the master list in the inverter and perform
# validity checks.


class RegisterMap:
    """Mapping of holding register function to location."""

    ENABLE_CHARGE_TARGET = 20
    BATTERY_POWER_MODE = 27
    SOC_FORCE_ADJUST = 29
    CHARGE_SLOT_2_START = 31
    CHARGE_SLOT_2_END = 32
    SYSTEM_TIME_YEAR = 35
    SYSTEM_TIME_MONTH = 36
    SYSTEM_TIME_DAY = 37
    SYSTEM_TIME_HOUR = 38
    SYSTEM_TIME_MINUTE = 39
    SYSTEM_TIME_SECOND = 40
    DISCHARGE_SLOT_2_START = 44
    DISCHARGE_SLOT_2_END = 45
    ACTIVE_POWER_RATE = 50
    DISCHARGE_SLOT_1_START = 56
    DISCHARGE_SLOT_1_END = 57
    ENABLE_DISCHARGE = 59
    CHARGE_SLOT_1_START = 94
    CHARGE_SLOT_1_END = 95
    ENABLE_CHARGE = 96
    BATTERY_SOC_RESERVE = 110
    BATTERY_CHARGE_LIMIT = 111
    BATTERY_DISCHARGE_LIMIT = 112
    BATTERY_DISCHARGE_MIN_POWER_RESERVE = 114
    CHARGE_TARGET_SOC = 116
    REBOOT = 163
    BATTERY_PAUSE_MODE = 318


# Helper to look up an inverter holding register by name
# and prepare a write request. Value range checking gets
# done automatically.
def write_named_register(name: str, value: int) -> TransparentRequest:
    """Prepare a request to write to a register."""
    _logger.debug("commands.write_named_register %s = %d", str, value)
    idx = Inverter.lookup_writable_register(name, value)
    return WriteHoldingRegisterRequest(idx, value)


# rather than writing lots of trivial setter methods,
# this translates implicit commands into calls to helpers:
#   commands.set_xyz(value) -> _set_helper('set_xyz', value)
#   commands.reset_XXX_slot_N() -> _set_timeslot('XXX_slot_N', None)

# Invoking commands.xyz(value) is a two-step process:
#  callable = getattr(commands, 'xyz')
#  callable(value)
# so __getattr__ returns a lambda that supplies the name and
# takes the value as a parameter.


def __getattr__(name: str) -> callable:
    if name.startswith("reset_") and "_slot" in name:
        return lambda: _set_timeslot(name[6:], None)
    if name.startswith("set_"):
        return lambda value: _set_helper(name, value)
    raise AttributeError(f"No {name} in {__name__}")


# This is a generic register setter.
# Usually invokved via __getattr__
# TODO: strip out the leading "set_" before calling this ?


def _set_helper(name: str, value: Any) -> list[TransparentRequest]:
    """Helper for anonymous commands."""
    _logger.debug("commands._set_helper: %s %s", name, value)
    if not name.startswith("set_"):
        raise AttributeError
    name = name[4:]
    if isinstance(value, TimeSlot):
        return _set_timeslot(name, value)
    # Otherwise just a single register access
    return [write_named_register(name, int(value))]


# A helper to write a timeslot to a pair of adjacent time registers
# Assumes the time registers are called name_start and name_end


def _set_timeslot(
    name: str, value: Optional[TimeSlot] = None
) -> list[TransparentRequest]:
    """Set a pair of start/end time slots."""
    if value is None:
        start = 0
        end = 0
    else:
        start = 100 * value.start.hour + value.start.minute
        end = 100 * value.end.hour + value.end.minute
    return [
        write_named_register(name + "_start", start),
        write_named_register(name + "_end", end),
    ]


def refresh_plant_data(
    complete: bool, number_batteries: int = 1, max_batteries: int = 5
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


def disable_charge_target() -> list[TransparentRequest]:
    """Removes SOC limit and target 100% charging."""
    return [
        WriteHoldingRegisterRequest(RegisterMap.ENABLE_CHARGE_TARGET, False),
        WriteHoldingRegisterRequest(RegisterMap.CHARGE_TARGET_SOC, 100),
    ]


def set_charge_target(target_soc: int) -> list[TransparentRequest]:
    """Sets inverter to stop charging when SOC reaches the desired level. Also referred to as "winter mode"."""
    if not 4 <= target_soc <= 100:
        raise ValueError(f"Charge Target SOC ({target_soc}) must be in [4-100]%")
    ret = set_enable_charge(True)
    if target_soc == 100:
        ret.extend(disable_charge_target())
    else:
        ret.append(WriteHoldingRegisterRequest(RegisterMap.ENABLE_CHARGE_TARGET, True))
        ret.append(
            WriteHoldingRegisterRequest(RegisterMap.CHARGE_TARGET_SOC, target_soc)
        )
    return ret


def set_enable_charge(enabled: bool) -> list[TransparentRequest]:
    """Enable the battery to charge, depending on the mode and slots set."""
    return [WriteHoldingRegisterRequest(RegisterMap.ENABLE_CHARGE, enabled)]


def set_enable_discharge(enabled: bool) -> list[TransparentRequest]:
    """Enable the battery to discharge, depending on the mode and slots set."""
    return [WriteHoldingRegisterRequest(RegisterMap.ENABLE_DISCHARGE, enabled)]


def set_inverter_reboot() -> list[TransparentRequest]:
    """Restart the inverter."""
    return [WriteHoldingRegisterRequest(RegisterMap.REBOOT, 100)]


def set_calibrate_battery_soc() -> list[TransparentRequest]:
    """Set the inverter to recalibrate the battery state of charge estimation."""
    return [WriteHoldingRegisterRequest(RegisterMap.SOC_FORCE_ADJUST, 1)]


@deprecated("use set_enable_charge(True) instead")
def enable_charge() -> list[TransparentRequest]:
    """Enable the battery to charge, depending on the mode and slots set."""
    return set_enable_charge(True)


@deprecated("use set_enable_charge(False) instead")
def disable_charge() -> list[TransparentRequest]:
    """Prevent the battery from charging at all."""
    return set_enable_charge(False)


@deprecated("use set_enable_discharge(True) instead")
def enable_discharge() -> list[TransparentRequest]:
    """Enable the battery to discharge, depending on the mode and slots set."""
    return set_enable_discharge(True)


@deprecated("use set_enable_discharge(False) instead")
def disable_discharge() -> list[TransparentRequest]:
    """Prevent the battery from discharging at all."""
    return set_enable_discharge(False)


def set_discharge_mode_max_power() -> list[TransparentRequest]:
    """Set the battery discharge mode to maximum power, exporting to the grid if it exceeds load demand."""
    return [WriteHoldingRegisterRequest(RegisterMap.BATTERY_POWER_MODE, 0)]


def set_discharge_mode_to_match_demand() -> list[TransparentRequest]:
    """Set the battery discharge mode to match demand, avoiding exporting power to the grid."""
    return [WriteHoldingRegisterRequest(RegisterMap.BATTERY_POWER_MODE, 1)]


@deprecated("Use set_battery_soc_reserve(val) instead")
def set_shallow_charge(val: int) -> list[TransparentRequest]:
    """Set the minimum level of charge to maintain."""
    return set_battery_soc_reserve(val)


def set_battery_soc_reserve(val: int) -> list[TransparentRequest]:
    """Set the minimum level of charge to maintain."""
    # TODO what are valid values? 4-100?
    val = int(val)
    if not 4 <= val <= 100:
        raise ValueError(f"Minimum SOC / shallow charge ({val}) must be in [4-100]%")
    return [WriteHoldingRegisterRequest(RegisterMap.BATTERY_SOC_RESERVE, val)]


def set_battery_power_reserve(val: int) -> list[TransparentRequest]:
    """Set the battery power reserve to maintain."""
    # TODO what are valid values?
    val = int(val)
    if not 4 <= val <= 100:
        raise ValueError(f"Battery power reserve ({val}) must be in [4-100]%")
    return [
        WriteHoldingRegisterRequest(
            RegisterMap.BATTERY_DISCHARGE_MIN_POWER_RESERVE, val
        )
    ]


def set_system_date_time(dt: datetime) -> list[TransparentRequest]:
    """Set the date & time of the inverter."""
    return [
        WriteHoldingRegisterRequest(RegisterMap.SYSTEM_TIME_YEAR, dt.year - 2000),
        WriteHoldingRegisterRequest(RegisterMap.SYSTEM_TIME_MONTH, dt.month),
        WriteHoldingRegisterRequest(RegisterMap.SYSTEM_TIME_DAY, dt.day),
        WriteHoldingRegisterRequest(RegisterMap.SYSTEM_TIME_HOUR, dt.hour),
        WriteHoldingRegisterRequest(RegisterMap.SYSTEM_TIME_MINUTE, dt.minute),
        WriteHoldingRegisterRequest(RegisterMap.SYSTEM_TIME_SECOND, dt.second),
    ]


def set_mode_dynamic() -> list[TransparentRequest]:
    """Set system to Dynamic / Eco mode.

    This mode is designed to maximise use of solar generation. The battery will charge from excess solar
    generation to avoid exporting power, and discharge to meet load demand when solar power is insufficient to
    avoid importing power. This mode is useful if you want to maximise self-consumption of renewable generation
    and minimise the amount of energy drawn from the grid.
    """
    # r27=1 r110=4 r59=0
    return (
        set_discharge_mode_to_match_demand()
        + set_battery_soc_reserve(4)
        + set_enable_discharge(False)
    )


def set_mode_storage(
    discharge_slot_1: TimeSlot = TimeSlot.from_repr(1600, 700),
    discharge_slot_2: Optional[TimeSlot] = None,
    discharge_for_export: bool = False,
) -> list[TransparentRequest]:
    """Set system to storage mode with specific discharge slots(s).

    This mode stores excess solar generation during the day and holds that energy ready for use later in the day.
    By default, the battery will start to discharge from 4pm-7am to cover energy demand during typical peak
    hours. This mode is particularly useful if you get charged more for your electricity at certain times to
    utilise the battery when it is most effective. If the second time slot isn't specified, it will be cleared.

    You can optionally also choose to export excess energy: instead of discharging to meet only your load demand,
    the battery will discharge at full power and any excess will be exported to the grid. This is useful if you
    have a variable export tariff (e.g. Agile export) and you want to target the peak times of day (e.g. 4pm-7pm)
    when it is most valuable to export energy.
    """
    if discharge_for_export:
        ret = set_discharge_mode_max_power()  # r27=0
    else:
        ret = set_discharge_mode_to_match_demand()  # r27=1
    ret.extend(set_battery_soc_reserve(100))  # r110=100
    ret.extend(set_enable_discharge(True))  # r59=1
    ret.extend(_set_timeslot('discharge_slot_1', discharge_slot_1))
    ret.extend(_set_timeslot('discharge_slot_2', discharge_slot_2))
    return ret


# The following auto-generates for the docstring a list of implicit
# set_XXX commands made available via __getattr__()
# TODO Ideally this would happen lazily, only when __doc__ is accessed.
# But pydoc doesn't seem to use __getattr__ to access __doc__ ???

@staticmethod
def _gen_docstring():
    """Auto-generate the module docstring."""
    doc = dedent(
        """

        The following list of methods was automatically generated from
        the register definition list in Inverter. They are fabricated at runtime via ``__getattr__``.
        Note that the actual set of registers available depends on the inverter model - care
        should be taken to write only to registers that you know exist on the target system.
        Perhaps by doing a full refresh, and only writing registers which already have
        a value.
        It is quite possible that GivEnergy will repurpose register numbers to
        mean something quite different on different models / firmwares.\n\n"""
    )

    for reg, defn in Inverter.REGISTER_LUT.items():
        if defn.valid is not None:
            doc += "* set_" + reg + "(int)\n"
            if reg.endswith("_end") and defn.valid[1] == 2359:
                # we can support timeslot-pairs as a special case
                doc += "* set_" + reg[:-4] + "(TimeSlot)\n"
                doc += "* reset_" + reg[:-4] + "()\n"


    return doc

__doc__ += _gen_docstring()
