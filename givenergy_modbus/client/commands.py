"""High-level methods for interacting with a remote system."""

from typing import Optional

from arrow import Arrow
from typing_extensions import deprecated  # type: ignore[attr-defined]

from givenergy_modbus.model import TimeSlot
from givenergy_modbus.model.inverter import (
    Inverter,
    BatteryPauseMode
)

from givenergy_modbus.pdu import (
    ReadHoldingRegistersRequest,
    ReadInputRegistersRequest,
    TransparentRequest,
    WriteHoldingRegisterRequest,
)


# update interesting registers.
# By default, it will fetch everything available,
# but if you only want a specific category, you can limit the
# set of registers in various categories.  Eg if you only want
# input registers, pass max_holding as -1. (They specify the
# base register, so 0 and 59 are equivalent.)
# TODO I think this really belongs in the client, rather than here ..?
def refresh_plant_data(
        max_holding: int,
        max_input: int,
        max_battery_input,
        num_batteries
) -> list[TransparentRequest]:
    """Refresh plant data."""

    requests: list[TransparentRequest] = []

    for base in [0, 180]:
        if base <= max_input:
            requests.append(ReadInputRegistersRequest(base_register=base, register_count=60, slave_address=0x32))

    for base in [0, 60, 120, 180, 300 ]:
        if base <= max_holding:
            requests.append(ReadHoldingRegistersRequest(base_register=base, register_count=60, slave_address=0x32))

    for batt in range(num_batteries):
        for base in [60, 120]:
            if base <= max_battery_input:
                requests.append(ReadInputRegistersRequest(base_register=base, register_count=60, slave_address=0x32 + batt))

    return requests


# Helper to look up an inverter holding register by name
# and prepare a write request. Value range checking gets
# done automatically.
def write_named_register(name: str, value: int) -> TransparentRequest:
    """Prepare a request to write to a register."""
    idx = Inverter.lookup_writable_register(name, value)
    return WriteHoldingRegisterRequest(idx, value)

def disable_charge_target() -> list[TransparentRequest]:
    """Removes AC SOC limit and target 100% charging."""
    return [
        write_named_register('enable_charge_target', 0),
        write_named_register('charge_target_soc', 100)
    ]


def set_charge_target(target_soc: int) -> list[TransparentRequest]:
    """Sets inverter to stop charging when SOC reaches the desired level. Also referred to as "winter mode"."""
    if not 4 <= target_soc <= 100:
        raise ValueError(f"Charge Target SOC ({target_soc}) must be in [4-100]%")
    ret = set_enable_charge(True)
    if target_soc == 100:
        ret.extend(disable_charge_target())
    else:
        ret.append(write_named_register('enable_charge_target', True))
        ret.append(
            write_named_register('charge_target_soc', target_soc)
        )
    return ret

def set_charge_target_only(target_soc: int) -> list[TransparentRequest]:
    """Sets inverter to stop charging when SOC reaches the desired level on AC Charge."""
    target_soc = int(target_soc)
    return [write_named_register('charge_target_soc', target_soc)]

def set_enable_charge(enabled: bool) -> list[TransparentRequest]:
    """Enable the battery to charge, depending on the mode and slots set."""
    return [write_named_register('enable_charge', enabled)]


def set_enable_discharge(enabled: bool) -> list[TransparentRequest]:
    """Enable the battery to discharge, depending on the mode and slots set."""
    return [write_named_register('enable_discharge', enabled)]


def set_inverter_reboot() -> list[TransparentRequest]:
    """Restart the inverter."""
    return [write_named_register('xxx', 100)]


def set_calibrate_battery_soc() -> list[TransparentRequest]:
    """Set the inverter to recalibrate the battery state of charge estimation."""
    return [write_named_register('xxx', 1)]


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
    return [write_named_register('battery_power_mode', 0)]


def set_discharge_mode_to_match_demand() -> list[TransparentRequest]:
    """Set the battery discharge mode to match demand, avoiding exporting power to the grid."""
    return [write_named_register('battery_power_mode', 1)]
 

@deprecated("Use set_battery_soc_reserve(val) instead")
def set_shallow_charge(val: int) -> list[TransparentRequest]:
    """Set the minimum level of charge to maintain."""
    return set_battery_soc_reserve(val)


def set_battery_soc_reserve(val: int) -> list[TransparentRequest]:
    """Set the minimum level of charge to maintain."""
    return [write_named_register('battery_soc_reserve', val)]


def set_battery_charge_limit(val: int) -> list[TransparentRequest]:
    """Set the battery charge power limit as percentage. 50% (2.6 kW) is the maximum for most inverters."""
    return [write_named_register('battery_charge_limit', val)]


def set_battery_discharge_limit(val: int) -> list[TransparentRequest]:
    """Set the battery discharge power limit as percentage. 50% (2.6 kW) is the maximum for most inverters."""
    return [write_named_register('battery_discharge_limit', val)]


def set_battery_power_reserve(val: int) -> list[TransparentRequest]:
    """Set the battery power reserve to maintain."""
    # TODO what are valid values?
    val = int(val)
    if not 4 <= val <= 100:
        raise ValueError(f"Battery power reserve ({val}) must be in [4-100]%")
    return [
        write_named_register('battery_discharge_min_power_reserve', val)
    ]


def set_battery_pause_mode(val: BatteryPauseMode) -> list[TransparentRequest]:
    """Set the battery pause mode."""
    return [write_named_register('battery_pause_mode', val)]


def _set_charge_slot(
    discharge: bool, idx: int, slot: Optional[TimeSlot]
) -> list[TransparentRequest]:
    hr_start, hr_end = (
        getattr(RegisterMap, f'{"DIS" if discharge else ""}CHARGE_SLOT_{idx}_START'),
        getattr(RegisterMap, f'{"DIS" if discharge else ""}CHARGE_SLOT_{idx}_END'),
    )
    if slot:
        return [
            write_named_register(hr_start, int(slot.start.strftime("%H%M"))),
            write_named_register(hr_end, int(slot.end.strftime("%H%M"))),
        ]
    else:
        return [
            write_named_register(hr_start, 0),
            write_named_register(hr_end, 0),
        ]


def set_charge_slot_1(timeslot: TimeSlot) -> list[TransparentRequest]:
    """Set first charge slot start & end times."""
    return _set_charge_slot(False, 1, timeslot)


def reset_charge_slot_1() -> list[TransparentRequest]:
    """Reset first charge slot to zero/disabled."""
    return _set_charge_slot(False, 1, None)


def set_charge_slot_2(timeslot: TimeSlot) -> list[TransparentRequest]:
    """Set second charge slot start & end times."""
    return _set_charge_slot(False, 2, timeslot)


def reset_charge_slot_2() -> list[TransparentRequest]:
    """Reset second charge slot to zero/disabled."""
    return _set_charge_slot(False, 2, None)


def set_discharge_slot_1(timeslot: TimeSlot) -> list[TransparentRequest]:
    """Set first discharge slot start & end times."""
    return _set_charge_slot(True, 1, timeslot)


def reset_discharge_slot_1() -> list[TransparentRequest]:
    """Reset first discharge slot to zero/disabled."""
    return _set_charge_slot(True, 1, None)


def set_discharge_slot_2(timeslot: TimeSlot) -> list[TransparentRequest]:
    """Set second discharge slot start & end times."""
    return _set_charge_slot(True, 2, timeslot)


def reset_discharge_slot_2() -> list[TransparentRequest]:
    """Reset second discharge slot to zero/disabled."""
    return _set_charge_slot(True, 2, None)


def set_system_date_time(dt: Arrow) -> list[TransparentRequest]:
    """Set the date & time of the inverter."""
    return [
        write_named_register('RegisterMap.SYSTEM_TIME_YEAR', dt.year - 2000),
        write_named_register('RegisterMap.SYSTEM_TIME_MONTH', dt.month),
        write_named_register('RegisterMap.SYSTEM_TIME_DAY', dt.day),
        write_named_register('RegisterMap.SYSTEM_TIME_HOUR', dt.hour),
        write_named_register('RegisterMap.SYSTEM_TIME_MINUTE', dt.minute),
        write_named_register('RegisterMap.SYSTEM_TIME_SECOND', dt.second),
    ]


def set_mode_dynamic() -> list[TransparentRequest]:
    """Set system to Dynamic / Eco mode.

    This mode is designed to maximise use of solar generation. The battery will charge from excess solar
    generation to avoid exporting power, and discharge to meet load demand when solar power is insufficient to
    avoid importing power. This mode is useful if you want to maximise self-consumption of renewable generation
    and minimise the amount of energy drawn from the grid.
    """
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
        ret = set_discharge_mode_max_power()
    else:
        ret = set_discharge_mode_to_match_demand()
    ret.extend(set_battery_soc_reserve(100))
    ret.extend(set_enable_discharge(True))
    ret.extend(set_discharge_slot_1(discharge_slot_1))
    if discharge_slot_2:
        ret.extend(set_discharge_slot_2(discharge_slot_2))
    else:
        ret.extend(reset_discharge_slot_2())
    return ret
