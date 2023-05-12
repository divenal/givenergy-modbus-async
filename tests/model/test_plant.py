import json
from datetime import datetime
from typing import Any, Optional

import pytest

from givenergy_modbus.exceptions import ExceptionBase
from givenergy_modbus.model import TimeSlot
from givenergy_modbus.model.battery import Battery, BatteryUsbDevice
from givenergy_modbus.model.inverter import (
    BatteryCalibrationStage,
    BatteryPowerMode,
    BatteryType,
    Inverter,
    InverterStatus,
    InverterUsbDevice,
    MeterType,
    Model,
)
from givenergy_modbus.model.plant import Plant
from givenergy_modbus.model.register import HR, IR, Register
from givenergy_modbus.model.register_cache import RegisterCache
from givenergy_modbus.pdu import (
    ClientIncomingMessage,
    HeartbeatRequest,
    NullResponse,
    ReadInputRegistersResponse,
    ReadRegistersResponse,
    WriteHoldingRegisterResponse,
)
from tests.conftest import CLIENT_MESSAGES, PduTestCaseSig


@pytest.fixture
def plant():
    """Yield a Plant."""
    yield Plant()


def test_instantiation():
    assert (plant := Plant()).dict() == {
        'data_adapter_serial_number': '',
        'inverter_serial_number': '',
        'register_caches': {0x32: {}},
    }
    assert plant.json() == (
        '{"register_caches": {"50": {}}, "inverter_serial_number": "", "data_adapter_serial_number": ""}'
    )

    rc = RegisterCache(registers={HR(1): 2})
    assert Plant(inverter_serial_number='AB1234', register_caches={0x30: rc}).dict() == {
        'data_adapter_serial_number': '',
        'inverter_serial_number': 'AB1234',
        'register_caches': {0x30: rc},
    }
    with pytest.raises(TypeError, match='keys must be str, int, float, bool or None, not HR'):
        assert Plant(data_adapter_serial_number='ZX9876', register_caches={0x30: rc}).json() == ''


def test_plant(
    plant: Plant,
    register_cache_inverter_daytime_discharging_with_solar_generation,
    register_cache_battery_daytime_discharging,
):
    """Ensure we can instantiate a Plant from existing DTOs."""
    assert plant.dict() == {
        'data_adapter_serial_number': '',
        'inverter_serial_number': '',
        'register_caches': {0x32: {}},
    }

    # inject register values
    plant.register_caches[0x32].update(register_cache_inverter_daytime_discharging_with_solar_generation)
    plant.register_caches[0x32].update(register_cache_battery_daytime_discharging)

    assert plant.dict() == {
        'data_adapter_serial_number': '',
        'inverter_serial_number': '',
        'register_caches': plant.register_caches,
    }
    with pytest.raises(TypeError, match='keys must be str, int, float, bool or None, not HR'):
        assert len(plant.json()) > 5000

    i = Inverter.from_orm(register_cache_inverter_daytime_discharging_with_solar_generation)
    assert i.serial_number == 'SA1234G567'
    b = Battery.from_orm(register_cache_battery_daytime_discharging)
    assert b.serial_number == 'BG1234G567'

    assert isinstance(plant.inverter, Inverter)
    assert plant.inverter == i
    assert plant.number_batteries == 1
    assert isinstance(plant.batteries[0], Battery)
    assert plant.batteries[0] == b

    # assert Plant(**plant.dict()) == plant
    # assert Plant.from_registers(plant) == plant


@pytest.mark.parametrize(PduTestCaseSig, CLIENT_MESSAGES)
async def test_update(
    plant: Plant,
    str_repr: str,
    pdu_class: type[ClientIncomingMessage],
    constructor_kwargs: dict[str, Any],
    mbap_header: bytes,
    inner_frame: bytes,
    ex: Optional[ExceptionBase],
):
    """Ensure we can update a Plant from PDU Response messages."""
    pdu: ClientIncomingMessage = pdu_class(**constructor_kwargs)
    assert plant.register_caches == {0x32: {}}
    orig_plant_dict = plant.dict()
    assert orig_plant_dict == {
        'register_caches': {0x32: {}},
        'inverter_serial_number': '',
        'data_adapter_serial_number': '',
    }
    assert plant.json() == json.dumps(orig_plant_dict)

    plant.update(pdu)

    d = plant.dict()
    # with pytest.raises(TypeError, match='keys must be str, int, float, bool or None, not HR'):
    #     plant.json()
    assert d.keys() == {'register_caches', 'inverter_serial_number', 'data_adapter_serial_number'}

    expected_caches_keys = {0x32}
    if isinstance(pdu, (ReadRegistersResponse, WriteHoldingRegisterResponse)):
        expected_caches_keys.add(pdu.slave_address)
    assert set(d['register_caches'].keys()) == expected_caches_keys

    if isinstance(pdu, ReadRegistersResponse):
        assert d != orig_plant_dict
        register_type: type[Register]
        if isinstance(pdu, ReadInputRegistersResponse):
            register_type = IR
        else:
            register_type = HR
        assert len(plant.register_caches[pdu.slave_address]) > 30
        assert plant.register_caches[pdu.slave_address] == {
            register_type(k): v for k, v in enumerate(pdu.register_values, start=pdu.base_register)
        }
        assert d['register_caches'][pdu.slave_address] == {
            register_type(k): v for k, v in enumerate(pdu.register_values, start=pdu.base_register)
        }
        # assert len(j) > 1400
    elif isinstance(pdu, WriteHoldingRegisterResponse):
        assert d != orig_plant_dict
        assert d['register_caches'][pdu.slave_address] == {HR(pdu.register): pdu.value}
        # assert j == ''.join(
        #     [
        #         '{"register_caches": {"',
        #         str(pdu.slave_address),
        #         '": {"HR(',
        #         str(pdu.register),
        #         ')": ',
        #         str(pdu.value),
        #         '}}, ' '"inverter_serial_number": "SA1234G567", ' '"data_adapter_serial_number": "WF1234G567"}',
        #     ]
        # )
    elif isinstance(pdu, (NullResponse, HeartbeatRequest)):
        assert d['register_caches'] == {k: {} for k in expected_caches_keys}
        # assert j == json.dumps(
        #     {
        #         'register_caches': {k: {} for k in expected_caches_keys},
        #         'inverter_serial_number': '',
        #         'data_adapter_serial_number': '',
        #     }
        # )
    else:  # unknown message
        assert False


def test_from_actual():
    """Ensure we can instantiate a plant from actual register values."""
    register_caches = {
        50: RegisterCache(
            registers={
                HR(0): 8193,
                HR(1): 3,
                HR(2): 2098,
                HR(3): 513,
                HR(4): 0,
                HR(5): 50000,
                HR(6): 3600,
                HR(7): 1,
                HR(8): 16967,
                HR(9): 12594,
                HR(10): 13108,
                HR(11): 18229,
                HR(12): 13879,
                HR(13): 21313,
                HR(14): 12594,
                HR(15): 13108,
                HR(16): 18229,
                HR(17): 13879,
                HR(18): 3005,
                HR(19): 449,
                HR(20): 0,
                HR(21): 449,
                HR(22): 2,
                HR(23): 0,
                HR(24): 32768,
                HR(25): 30235,
                HR(26): 6000,
                HR(27): 1,
                HR(28): 0,
                HR(29): 0,
                HR(30): 17,
                HR(31): 0,
                HR(32): 4,
                HR(33): 7,
                HR(34): 140,
                HR(35): 22,
                HR(36): 4,
                HR(37): 27,
                HR(38): 23,
                HR(39): 29,
                HR(40): 18,
                HR(41): 1,
                HR(42): 2,
                HR(43): 0,
                HR(44): 0,
                HR(45): 0,
                HR(46): 101,
                HR(47): 1,
                HR(48): 0,
                HR(49): 0,
                HR(50): 100,
                HR(51): 0,
                HR(52): 0,
                HR(53): 1,
                HR(54): 1,
                HR(55): 160,
                HR(56): 0,
                HR(57): 0,
                HR(58): 1,
                HR(59): 0,
                HR(60): 1500,
                HR(61): 30,
                HR(62): 30,
                HR(63): 1840,
                HR(64): 2740,
                HR(65): 4700,
                HR(66): 5198,
                HR(67): 126,
                HR(68): 27,
                HR(69): 24,
                HR(70): 28,
                HR(71): 1840,
                HR(72): 2620,
                HR(73): 4745,
                HR(74): 5200,
                HR(75): 126,
                HR(76): 52,
                HR(77): 1,
                HR(78): 28,
                HR(79): 1755,
                HR(80): 2837,
                HR(81): 4700,
                HR(82): 5200,
                HR(83): 2740,
                HR(84): 0,
                HR(85): 0,
                HR(86): 0,
                HR(87): 0,
                HR(88): 0,
                HR(89): 0,
                HR(90): 0,
                HR(91): 0,
                HR(92): 0,
                HR(93): 0,
                HR(94): 30,
                HR(95): 430,
                HR(96): 1,
                HR(97): 4320,
                HR(98): 5850,
                HR(99): 0,
                HR(100): 0,
                HR(101): 0,
                HR(102): 0,
                HR(103): 0,
                HR(104): 0,
                HR(105): 0,
                HR(106): 0,
                HR(107): 0,
                HR(108): 6,
                HR(109): 1,
                HR(110): 4,
                HR(111): 50,
                HR(112): 50,
                HR(113): 0,
                HR(114): 4,
                HR(115): 0,
                HR(116): 100,
                HR(117): 0,
                HR(118): 0,
                HR(119): 0,
                HR(120): 0,
                HR(121): 0,
                HR(122): 0,
                HR(123): 24,
                HR(124): 0,
                HR(125): 1,
                HR(126): 0,
                HR(127): 0,
                HR(128): 1,
                HR(129): 1,
                HR(130): 255,
                HR(131): 20000,
                HR(132): 255,
                HR(133): 20000,
                HR(134): 255,
                HR(135): 20000,
                HR(136): 255,
                HR(137): 20000,
                HR(138): 2484,
                HR(139): 2530,
                HR(140): 2116,
                HR(141): 2070,
                HR(142): 20,
                HR(143): 5,
                HR(144): 2415,
                HR(145): 2300,
                HR(146): 0,
                HR(147): 0,
                HR(148): 0,
                HR(149): 0,
                HR(150): 0,
                HR(151): 0,
                HR(152): 0,
                HR(153): 0,
                HR(154): 0,
                HR(155): 0,
                HR(156): 0,
                HR(157): 0,
                HR(158): 0,
                HR(159): 0,
                HR(160): 0,
                HR(161): 0,
                HR(162): 0,
                HR(163): 0,
                HR(164): 0,
                HR(165): 0,
                HR(166): 0,
                HR(167): 0,
                HR(168): 0,
                HR(169): 0,
                HR(170): 0,
                HR(171): 0,
                HR(172): 0,
                HR(173): 0,
                HR(174): 0,
                HR(175): 0,
                HR(176): 0,
                HR(177): 0,
                HR(178): 0,
                HR(179): 0,
                IR(120): 0,
                IR(121): 0,
                IR(122): 0,
                IR(123): 0,
                IR(124): 0,
                IR(125): 0,
                IR(126): 0,
                IR(127): 0,
                IR(128): 0,
                IR(129): 0,
                IR(130): 0,
                IR(131): 0,
                IR(132): 0,
                IR(133): 0,
                IR(134): 0,
                IR(135): 0,
                IR(136): 0,
                IR(137): 0,
                IR(138): 0,
                IR(139): 0,
                IR(140): 0,
                IR(141): 0,
                IR(142): 0,
                IR(143): 0,
                IR(144): 0,
                IR(145): 0,
                IR(146): 0,
                IR(147): 0,
                IR(148): 0,
                IR(149): 0,
                IR(150): 0,
                IR(151): 0,
                IR(152): 0,
                IR(153): 0,
                IR(154): 0,
                IR(155): 0,
                IR(156): 0,
                IR(157): 0,
                IR(158): 0,
                IR(159): 0,
                IR(160): 0,
                IR(161): 0,
                IR(162): 0,
                IR(163): 0,
                IR(164): 0,
                IR(165): 0,
                IR(166): 0,
                IR(167): 0,
                IR(168): 0,
                IR(169): 0,
                IR(170): 0,
                IR(171): 0,
                IR(172): 0,
                IR(173): 0,
                IR(174): 0,
                IR(175): 0,
                IR(176): 0,
                IR(177): 0,
                IR(178): 0,
                IR(179): 0,
                IR(0): 1,
                IR(1): 180,
                IR(2): 178,
                IR(3): 3885,
                IR(4): 0,
                IR(5): 2404,
                IR(6): 0,
                IR(7): 18527,
                IR(8): 0,
                IR(9): 0,
                IR(10): 41,
                IR(11): 0,
                IR(12): 11929,
                IR(13): 5006,
                IR(14): 5,
                IR(15): 2760,
                IR(16): 8160,
                IR(17): 195,
                IR(18): 0,
                IR(19): 159,
                IR(20): 0,
                IR(21): 0,
                IR(22): 1639,
                IR(23): 0,
                IR(24): 921,
                IR(25): 24,
                IR(26): 123,
                IR(27): 0,
                IR(28): 7211,
                IR(29): 0,
                IR(30): 65497,
                IR(31): 0,
                IR(32): 0,
                IR(33): 19783,
                IR(34): 0,
                IR(35): 43,
                IR(36): 57,
                IR(37): 59,
                IR(38): 0,
                IR(39): 0,
                IR(40): 0,
                IR(41): 322,
                IR(42): 745,
                IR(43): 654,
                IR(44): 380,
                IR(45): 0,
                IR(46): 16987,
                IR(47): 0,
                IR(48): 2754,
                IR(49): 1,
                IR(50): 5128,
                IR(51): 2165,
                IR(52): 1075,
                IR(53): 2390,
                IR(54): 5004,
                IR(55): 318,
                IR(56): 190,
                IR(57): 0,
                IR(58): 266,
                IR(59): 57,
                IR(180): 9061,
                IR(181): 9466,
                IR(182): 59,
                IR(183): 57,
                IR(184): 0,
                IR(185): 0,
                IR(186): 0,
                IR(187): 0,
                IR(188): 0,
                IR(189): 0,
                IR(190): 0,
                IR(191): 0,
                IR(192): 0,
                IR(193): 0,
                IR(194): 0,
                IR(195): 0,
                IR(196): 0,
                IR(197): 0,
                IR(198): 0,
                IR(199): 0,
                IR(200): 0,
                IR(201): 0,
                IR(202): 0,
                IR(203): 0,
                IR(204): 0,
                IR(205): 0,
                IR(206): 0,
                IR(207): 0,
                IR(208): 0,
                IR(209): 0,
                IR(210): 0,
                IR(211): 0,
                IR(212): 0,
                IR(213): 0,
                IR(214): 0,
                IR(215): 0,
                IR(216): 0,
                IR(217): 0,
                IR(218): 0,
                IR(219): 0,
                IR(220): 0,
                IR(221): 0,
                IR(222): 0,
                IR(223): 0,
                IR(224): 0,
                IR(225): 0,
                IR(226): 0,
                IR(227): 300,
                IR(228): 0,
                IR(229): 0,
                IR(230): 0,
                IR(231): 0,
                IR(232): 0,
                IR(233): 0,
                IR(234): 0,
                IR(235): 0,
                IR(236): 0,
                IR(237): 0,
                IR(238): 0,
                IR(239): 0,
                IR(60): 3221,
                IR(61): 3224,
                IR(62): 3219,
                IR(63): 3217,
                IR(64): 3217,
                IR(65): 3216,
                IR(66): 3221,
                IR(67): 3217,
                IR(68): 3225,
                IR(69): 3222,
                IR(70): 3221,
                IR(71): 3221,
                IR(72): 3222,
                IR(73): 3229,
                IR(74): 3227,
                IR(75): 3225,
                IR(76): 199,
                IR(77): 186,
                IR(78): 191,
                IR(79): 182,
                IR(80): 51555,
                IR(81): 255,
                IR(82): 0,
                IR(83): 51548,
                IR(84): 0,
                IR(85): 19202,
                IR(86): 0,
                IR(87): 16000,
                IR(88): 0,
                IR(89): 11071,
                IR(90): 0,
                IR(91): 3600,
                IR(92): 256,
                IR(93): 0,
                IR(94): 0,
                IR(95): 0,
                IR(96): 116,
                IR(97): 16,
                IR(98): 3005,
                IR(99): 0,
                IR(100): 58,
                IR(101): 0,
                IR(102): 16000,
                IR(103): 199,
                IR(104): 186,
                IR(105): 0,
                IR(106): 0,
                IR(107): 0,
                IR(108): 0,
                IR(109): 0,
                IR(110): 16967,
                IR(111): 12594,
                IR(112): 13108,
                IR(113): 18229,
                IR(114): 13879,
                IR(115): 8,
                IR(116): 0,
                IR(117): 0,
                IR(118): 0,
                IR(119): 0,
            }
        ),
        51: RegisterCache(
            registers={
                IR(60): 0,
                IR(61): 0,
                IR(62): 0,
                IR(63): 0,
                IR(64): 0,
                IR(65): 0,
                IR(66): 0,
                IR(67): 0,
                IR(68): 0,
                IR(69): 0,
                IR(70): 0,
                IR(71): 0,
                IR(72): 0,
                IR(73): 0,
                IR(74): 0,
                IR(75): 0,
                IR(76): 45,
                IR(77): 0,
                IR(78): 0,
                IR(79): 0,
                IR(80): 0,
                IR(81): 256,
                IR(82): 0,
                IR(83): 0,
                IR(84): 0,
                IR(85): 0,
                IR(86): 0,
                IR(87): 0,
                IR(88): 0,
                IR(89): 0,
                IR(90): 0,
                IR(91): 0,
                IR(92): 0,
                IR(93): 0,
                IR(94): 0,
                IR(95): 0,
                IR(96): 0,
                IR(97): 0,
                IR(98): 0,
                IR(99): 0,
                IR(100): 0,
                IR(101): 0,
                IR(102): 0,
                IR(103): 0,
                IR(104): 0,
                IR(105): 0,
                IR(106): 0,
                IR(107): 0,
                IR(108): 0,
                IR(109): 0,
                IR(110): 0,
                IR(111): 0,
                IR(112): 0,
                IR(113): 0,
                IR(114): 0,
                IR(115): 0,
                IR(116): 0,
                IR(117): 0,
                IR(118): 0,
                IR(119): 0,
            }
        ),
        52: RegisterCache(
            registers={
                IR(60): 0,
                IR(61): 0,
                IR(62): 0,
                IR(63): 0,
                IR(64): 0,
                IR(65): 0,
                IR(66): 0,
                IR(67): 0,
                IR(68): 0,
                IR(69): 0,
                IR(70): 0,
                IR(71): 0,
                IR(72): 0,
                IR(73): 0,
                IR(74): 0,
                IR(75): 0,
                IR(76): 0,
                IR(77): 0,
                IR(78): 0,
                IR(79): 0,
                IR(80): 0,
                IR(81): 0,
                IR(82): 0,
                IR(83): 0,
                IR(84): 0,
                IR(85): 0,
                IR(86): 0,
                IR(87): 0,
                IR(88): 0,
                IR(89): 0,
                IR(90): 0,
                IR(91): 0,
                IR(92): 0,
                IR(93): 0,
                IR(94): 0,
                IR(95): 0,
                IR(96): 0,
                IR(97): 0,
                IR(98): 0,
                IR(99): 0,
                IR(100): 0,
                IR(101): 0,
                IR(102): 0,
                IR(103): 0,
                IR(104): 0,
                IR(105): 0,
                IR(106): 0,
                IR(107): 0,
                IR(108): 0,
                IR(109): 0,
                IR(110): 0,
                IR(111): 0,
                IR(112): 0,
                IR(113): 0,
                IR(114): 0,
                IR(115): 0,
                IR(116): 0,
                IR(117): 0,
                IR(118): 0,
                IR(119): 0,
            }
        ),
        53: RegisterCache(
            registers={
                IR(60): 0,
                IR(61): 0,
                IR(62): 0,
                IR(63): 0,
                IR(64): 0,
                IR(65): 0,
                IR(66): 0,
                IR(67): 0,
                IR(68): 0,
                IR(69): 0,
                IR(70): 0,
                IR(71): 0,
                IR(72): 0,
                IR(73): 0,
                IR(74): 0,
                IR(75): 0,
                IR(76): 0,
                IR(77): 0,
                IR(78): 0,
                IR(79): 0,
                IR(80): 0,
                IR(81): 0,
                IR(82): 0,
                IR(83): 0,
                IR(84): 0,
                IR(85): 0,
                IR(86): 0,
                IR(87): 0,
                IR(88): 0,
                IR(89): 0,
                IR(90): 0,
                IR(91): 0,
                IR(92): 0,
                IR(93): 0,
                IR(94): 0,
                IR(95): 0,
                IR(96): 0,
                IR(97): 0,
                IR(98): 0,
                IR(99): 0,
                IR(100): 0,
                IR(101): 0,
                IR(102): 0,
                IR(103): 0,
                IR(104): 0,
                IR(105): 0,
                IR(106): 0,
                IR(107): 0,
                IR(108): 0,
                IR(109): 0,
                IR(110): 0,
                IR(111): 0,
                IR(112): 0,
                IR(113): 0,
                IR(114): 0,
                IR(115): 0,
                IR(116): 0,
                IR(117): 0,
                IR(118): 0,
                IR(119): 0,
            }
        ),
        54: RegisterCache(
            registers={
                IR(60): 0,
                IR(61): 0,
                IR(62): 0,
                IR(63): 0,
                IR(64): 0,
                IR(65): 0,
                IR(66): 0,
                IR(67): 0,
                IR(68): 0,
                IR(69): 0,
                IR(70): 0,
                IR(71): 0,
                IR(72): 0,
                IR(73): 0,
                IR(74): 0,
                IR(75): 0,
                IR(76): 0,
                IR(77): 0,
                IR(78): 0,
                IR(79): 0,
                IR(80): 0,
                IR(81): 0,
                IR(82): 0,
                IR(83): 0,
                IR(84): 0,
                IR(85): 0,
                IR(86): 0,
                IR(87): 0,
                IR(88): 0,
                IR(89): 0,
                IR(90): 0,
                IR(91): 0,
                IR(92): 0,
                IR(93): 0,
                IR(94): 0,
                IR(95): 0,
                IR(96): 0,
                IR(97): 0,
                IR(98): 0,
                IR(99): 0,
                IR(100): 0,
                IR(101): 0,
                IR(102): 0,
                IR(103): 0,
                IR(104): 0,
                IR(105): 0,
                IR(106): 0,
                IR(107): 0,
                IR(108): 0,
                IR(109): 0,
                IR(110): 0,
                IR(111): 0,
                IR(112): 0,
                IR(113): 0,
                IR(114): 0,
                IR(115): 0,
                IR(116): 0,
                IR(117): 0,
                IR(118): 0,
                IR(119): 0,
            }
        ),
        55: RegisterCache(
            registers={
                IR(60): 0,
                IR(61): 0,
                IR(62): 0,
                IR(63): 0,
                IR(64): 0,
                IR(65): 0,
                IR(66): 0,
                IR(67): 0,
                IR(68): 0,
                IR(69): 0,
                IR(70): 0,
                IR(71): 0,
                IR(72): 0,
                IR(73): 0,
                IR(74): 0,
                IR(75): 0,
                IR(76): 0,
                IR(77): 0,
                IR(78): 0,
                IR(79): 0,
                IR(80): 0,
                IR(81): 0,
                IR(82): 0,
                IR(83): 0,
                IR(84): 0,
                IR(85): 0,
                IR(86): 0,
                IR(87): 0,
                IR(88): 0,
                IR(89): 0,
                IR(90): 0,
                IR(91): 0,
                IR(92): 0,
                IR(93): 0,
                IR(94): 0,
                IR(95): 0,
                IR(96): 0,
                IR(97): 0,
                IR(98): 0,
                IR(99): 0,
                IR(100): 0,
                IR(101): 0,
                IR(102): 0,
                IR(103): 0,
                IR(104): 0,
                IR(105): 0,
                IR(106): 0,
                IR(107): 0,
                IR(108): 0,
                IR(109): 0,
                IR(110): 0,
                IR(111): 0,
                IR(112): 0,
                IR(113): 0,
                IR(114): 0,
                IR(115): 0,
                IR(116): 0,
                IR(117): 0,
                IR(118): 0,
                IR(119): 0,
            }
        ),
    }

    p = Plant(register_caches=register_caches)
    i = p.inverter
    assert i.dict() == {
        # 'battery_charge_limit': 50,
        # 'battery_discharge_limit': 50,
        # 'battery_discharge_min_power_reserve': 4,
        # 'battery_high_voltage_protection_limit': 58.5,
        # 'battery_low_force_charge_time': 6,
        # 'battery_low_voltage_protection_limit': 43.2,
        # 'battery_percent': 57,
        # 'battery_soc_reserve': 4,
        # 'battery_voltage_adjust': 0,
        # 'charge_slot_1': TimeSlot.from_repr(30, 430),
        # 'charge_soc_stop_1': 0,
        # 'charge_soc_stop_2': 0,
        # 'charge_status': 5,
        # 'charge_target_soc': 100,
        # 'charger_warning_code': 0,
        # 'cmd_bms_flash_update': False,
        # 'dci_1_i': 0.0,
        # 'dci_1_time': 0,
        # 'dci_2_i': 0.0,
        # 'dci_2_time': 0,
        # 'dci_fault_value': 0.0,
        # 'debug_inverter': 0,
        # 'discharge_soc_stop_1': 0,
        # 'discharge_soc_stop_2': 0,
        # 'e_battery_charge_day': 5.7,
        # 'e_battery_charge_day_2': 5.7,
        # 'e_battery_charge_today_3': 0.0,
        # 'e_battery_charge_total': 946.6,
        # 'e_battery_charge_total_2': 0.0,
        # 'e_battery_discharge_day': 5.9,
        # 'e_battery_discharge_day_2': 5.9,
        # 'e_battery_discharge_today_3': 0.0,
        # 'e_battery_discharge_total': 906.1,
        # 'e_battery_discharge_total_2': 0.0,
        # 'e_battery_throughput_total': 1852.7,
        # 'e_discharge_year': 0.0,
        # 'e_grid_in_day': 12.3,
        # 'e_grid_in_total': 1978.3,
        # 'e_grid_out_day': 2.4,
        # 'e_grid_out_total': 163.9,
        # 'e_inverter_export_total': 0.0,
        # 'e_inverter_in_day': 4.3,
        # 'e_inverter_in_total': 721.1,
        # 'e_inverter_out_day': 38.0,
        # 'e_inverter_out_total': 1698.7,
        # 'e_pv1_day': 19.5,
        # 'e_pv2_day': 15.9,
        # 'e_pv_day': 35.4,
        # 'e_pv_total': 1192.9,
        # 'e_solar_diverter': 0.0,
        # 'enable_above_6kw_system': False,
        # 'enable_battery_cable_impedance_alarm': False,
        # 'enable_battery_on_pv_or_grid': False,
        # 'enable_bms_read': True,
        # 'enable_buzzer': False,
        # 'enable_charge': True,
        # 'enable_frequency_derating': True,
        # 'enable_g100_limit_switch': False,
        # 'enable_low_voltage_fault_ride_through': False,
        # 'enable_spi': True,
        # 'enable_standard_self_consumption_logic': False,
        # 'enable_ups_mode': False,
        # 'f_ac1': 50.06,
        # 'f_ac_fault_value': 0.0,
        # 'f_ac_high_c': 52.0,
        # 'f_ac_high_in': 52.0,
        # 'f_ac_high_in_time': 28,
        # 'f_ac_high_out': 51.98,
        # 'f_ac_high_out_time': 28,
        # 'f_ac_low_c': 47.0,
        # 'f_ac_low_in': 47.45,
        # 'f_ac_low_in_time': 1,
        # 'f_ac_low_out': 47.0,
        # 'f_ac_low_out_time': 24,
        # 'f_eps_backup': 50.04,
        # 'fault_code': 0,
        # 'frequency_load_limit_rate': 24,
        # 'gfci_1_i': 0.0,
        # 'gfci_1_time': 0,
        # 'gfci_2_i': 0.0,
        # 'gfci_2_time': 0,
        # 'gfci_fault_value': 0.0,
        # 'grid_power_adjust': 0,
        # 'grid_r_voltage_adjust': 0,
        # 'grid_s_voltage_adjust': 0,
        # 'grid_t_voltage_adjust': 0,
        # 'i_ac1': 0.41,
        # 'i_battery': 21.65,
        # 'i_grid_port': 2.66,
        # 'i_pv1': 0.0,
        # 'i_pv2': 0.0,
        # 'inverter_countdown': 0,
        # 'inverter_reboot': 0,
        # 'inverter_status': 1,
        # 'island_check_continue': 0,
        # 'iso1': 0,
        # 'iso2': 0,
        # 'iso_fault_value': 0.0,
        # 'local_command_test': False,
        # 'p_battery': 1075,
        # 'p_eps_backup': 0,
        # 'p_grid_apparent': 654,
        # 'p_grid_out': -39,
        # 'p_inverter_out': 921,
        # 'p_load_demand': 745,
        # 'p_pv': 0,
        # 'p_pv1': 0,
        # 'p_pv2': 0,
        # 'pf_cmd_memory_state': True,
        # 'pf_inverter_out': -0.184,
        # 'pf_limit_lp1_lp': 255,
        # 'pf_limit_lp1_pf': 1.0,
        # 'pf_limit_lp2_lp': 255,
        # 'pf_limit_lp2_pf': 1.0,
        # 'pf_limit_lp3_lp': 255,
        # 'pf_limit_lp3_pf': 1.0,
        # 'pf_limit_lp4_lp': 255,
        # 'pf_limit_lp4_pf': 1.0,
        # 'power_factor_function_model': PowerFactorFunctionModel.PF_1,
        # 'pv1_power_adjust': 0,
        # 'pv1_voltage_adjust': 0,
        # 'pv2_power_adjust': 0,
        # 'pv2_voltage_adjust': 0,
        # 'pv_power_setting': 0.0,
        # 'pv_start_voltage': 150.0,
        # 'real_v_f_value': 0.0,
        # 'remote_bms_restart': False,
        # 'restart_delay_time': 30,
        # 'safety_time_limit': 0.0,
        # 'safety_v_f_limit': 0.0,
        # 'start_countdown_timer': 30,
        # 'start_system_auto_test': False,
        # 'system_mode': 1,
        # 'temp_battery': 19.0,
        # 'temp_charger': 31.8,
        # 'temp_fault_value': 0.0,
        # 'temp_inverter_heatsink': 32.2,
        # 'test_treat_time': 0,
        # 'test_treat_value': 0.0,
        # 'test_value': 0.0,
        # 'threephase_abc': 0,
        # 'threephase_balance_1': 0,
        # 'threephase_balance_2': 0,
        # 'threephase_balance_3': 0,
        # 'threephase_balance_mode': 0,
        # 'user_code': 7,
        # 'v_10_min_protection': 274.0,
        # 'v_ac1': 240.4,
        # 'v_ac_fault_value': 0.0,
        # 'v_ac_high_c': 283.7,
        # 'v_ac_high_in': 262.0,
        # 'v_ac_high_in_time': 52,
        # 'v_ac_high_out': 274.0,
        # 'v_ac_high_out_time': 27,
        # 'v_ac_low_c': 175.5,
        # 'v_ac_low_in': 184.0,
        # 'v_ac_low_in_time': 126,
        # 'v_ac_low_out': 184.0,
        # 'v_ac_low_out_time': 126,
        # 'v_battery': 51.28,
        # 'v_eps_backup': 239.0,
        # 'v_highbrigh_bus': 2760,
        # 'v_n_bus': 0.0,
        # 'v_p_bus': 388.5,
        # 'v_pv1': 18.0,
        # 'v_pv2': 17.8,
        # 'v_pv_fault_value': 0.0,
        # 'work_time_total': 2754,
        'active_power_rate': 100,
        'arm_firmware_version': 449,
        'battery_calibration_stage': BatteryCalibrationStage.OFF,
        'battery_capacity': 160,
        'battery_power_mode': BatteryPowerMode.SELF_CONSUMPTION,
        'battery_type': BatteryType.LITHIUM,
        'bms_firmware_version': 101,
        'charge_slot_2': TimeSlot.from_repr(0, 4),
        'charge_soc': 0,
        'device_type_code': '2001',
        'discharge_slot_1': TimeSlot.from_repr(0, 0),
        'discharge_slot_2': TimeSlot.from_repr(0, 0),
        'discharge_soc': 0,
        'dsp_firmware_version': 449,
        'enable_60hz_freq_mode': False,
        'enable_ammeter': True,
        'enable_auto_judge_battery_type': True,
        'enable_charge_target': False,
        'enable_discharge': False,
        'enable_drm_rj45_port': True,
        'enable_inverter': True,
        'enable_inverter_auto_restart': False,
        'enable_reversed_115_meter': False,
        'enable_reversed_418_meter': False,
        'enable_reversed_ct_clamp': True,
        'firmware_version': 'D0.449-A0.449',
        'first_battery_bms_firmware_version': 3005,
        'first_battery_serial_number': 'BG1234G567',
        'grid_port_max_power_output': 6000,
        'meter_type': MeterType.EM115,
        'modbus_address': 0x11,
        'modbus_version': '1.40',
        'model': Model.HYBRID,
        'module': '00030832',
        'num_mppt': 2,
        'num_phases': 1,
        'power_factor': 0,
        'reactive_power_rate': 0,
        'select_arm_chip': False,
        'serial_number': 'SA1234G567',
        'status': InverterStatus.NORMAL,
        'system_time': datetime(2022, 4, 27, 23, 29, 18),
        'usb_device_inserted': InverterUsbDevice.DISK,
        'user_code': 7,
        'variable_address': 32768,
        'variable_value': 30235,
    }

    assert p.number_batteries == 1
    b = p.batteries[0]
    assert b.dict() == {
        'bms_firmware_version': 3005,
        'cap_design': 160.0,
        'cap_design2': 160.0,
        'cap_calibrated': 192.02,
        'num_cells': 16,
        'num_cycles': 116,
        'cap_remaining': 110.71,
        'serial_number': 'BG1234G567',
        'soc': 58,
        'status_1': 0,
        'status_2': 0,
        'status_3': 14,
        'status_4': 16,
        'status_5': 1,
        'status_6': 0,
        'status_7': 0,
        't_bms_mosfet': 25.5,
        't_cells_01_04': 19.9,
        't_cells_05_08': 18.6,
        't_cells_09_12': 19.1,
        't_cells_13_16': 18.2,
        't_max': 19.9,
        't_min': 18.6,
        'usb_device_inserted': BatteryUsbDevice.DISK,
        'v_cell_01': 3.221,
        'v_cell_02': 3.224,
        'v_cell_03': 3.219,
        'v_cell_04': 3.217,
        'v_cell_05': 3.217,
        'v_cell_06': 3.216,
        'v_cell_07': 3.221,
        'v_cell_08': 3.217,
        'v_cell_09': 3.225,
        'v_cell_10': 3.222,
        'v_cell_11': 3.221,
        'v_cell_12': 3.221,
        'v_cell_13': 3.222,
        'v_cell_14': 3.229,
        'v_cell_15': 3.227,
        'v_cell_16': 3.225,
        'v_cells_sum': 51.555,
        'v_out': 51.548,
        'warning_1': 0,
        'warning_2': 0,
    }
