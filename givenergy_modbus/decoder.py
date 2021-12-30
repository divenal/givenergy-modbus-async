#!/usr/bin/env python
from __future__ import annotations

import abc
from typing import Callable

from loguru import logger as _logger
from pymodbus.interfaces import IModbusDecoder
from pymodbus.pdu import ExceptionResponse, ModbusPDU

from . import pdu


class GivEnergyDecoder(IModbusDecoder, metaclass=abc.ABCMeta):
    """GivEnergy Modbus Decoder factory base class.

    This is to enable efficient decoding of unencapsulated messages (i.e. having the Modbus-specific framing
    stripped) and creating populated matching PDU DTO instances. Two factories are created, dealing with messages
    traveling in a particular direction (Request/Client vs. Response/Server) since implementations generally know
    what side of the conversation they'll be on. It does allow for more general ideas like being able to decode
    arbitrary streams of messages (i.e. captured from a network interface) where these classes may be intermixed.

    The Decoder's job is to do the bare minimum inspecting of the raw message to determine its type,
    instantiate a concrete PDU handler to decode it, and pass it on.
    """

    _function_table: list[Callable]  # contains all the decoder functions this factory will consider
    _lookup: dict[int, Callable]  # lookup table mapping function code to decoder type

    def __init__(self):
        """Constructor."""
        # build the lookup table at instantiation time
        self._lookup = {f.function_code: f for f in self._function_table}

    def lookupPduClass(self, fn_code: int) -> ModbusPDU | None:
        """Attempts to find the ModbusPDU handler class that can handle a given function code."""
        if fn_code in self._lookup:
            fn = self._lookup[fn_code]
            fn_name = str(fn).rsplit(".", maxsplit=1)[-1].rstrip("'>")
            _logger.info(f"Identified incoming PDU as function {fn_code}/{fn_name}")
            return fn()
        return None

    def decode(self, data: bytes) -> ModbusPDU | None:
        """Create an appropriate populated PDU message object from a valid Modbus message.

        Extracts the `function code` from the raw message and looks up the matching ModbusPDU handler class
        that claims that function. This handler is instantiated and passed the raw message, which then proceeds
        to decode its attributes from the bytestream.
        """
        if len(data) <= 19:
            _logger.error(f"PDU data is too short to find a valid function id: {len(data)} {data!r}")
            return None
        fn_code = data[19]
        if fn_code > 0x80:
            code = fn_code & 0x7F  # strip error portion
            return ExceptionResponse(code, pdu.ModbusExceptions.IllegalFunction)

        response = self.lookupPduClass(fn_code)
        if response:
            _logger.debug(f"About to decode data {data!r}")
            response.decode(data)
            return response

        _logger.error(f"No decoder for function code {fn_code}")
        return None


class GivEnergyClientDecoder(GivEnergyDecoder):
    """Factory class to decode GivEnergy Request PDU messages. Typically used by clients."""

    _function_table: list[Callable] = [
        pdu.ReadHoldingRegistersRequest,
        pdu.ReadInputRegistersRequest,
    ]


class GivEnergyServerDecoder(GivEnergyDecoder):
    """Factory class to decode GivEnergy Response PDU messages. Typically used in servers."""

    _function_table: list[Callable] = [
        pdu.ReadHoldingRegistersResponse,
        pdu.ReadInputRegistersResponse,
    ]