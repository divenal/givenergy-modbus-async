import logging
from typing import Any

from givenergy_modbus.model import GivEnergyBaseModel
from givenergy_modbus.model.battery import Battery
from givenergy_modbus.model.inverter import Inverter
from givenergy_modbus.model.register import HR, IR, RegisterCache

from givenergy_modbus.pdu import (
    ClientIncomingMessage,
    NullResponse,
    ReadHoldingRegistersResponse,
    ReadInputRegistersResponse,
    TransparentResponse,
    WriteHoldingRegisterResponse,
)

_logger = logging.getLogger(__name__)


class Plant(GivEnergyBaseModel):
    """Representation of a complete GivEnergy plant.

    A plant comprises an inverter and some batteries. Each has
    its own slave address. For each encountered slave address,
    the plant maintains a cache of (raw) register values.

    The register values are scaled and converted as required
    when viewed through an Inverter or Battery instance.
    """

    # One per slave address.
    # The inverter can appear at multiple addresses - all are mapped to 0x32
    # Batteries are at addresses 0x32, 0x33, 0x34, ...
    # (The inverter and the first battery share a register cache.)
    register_caches: dict[int, RegisterCache] = {}

    # These hold the largest (base) registers numbers we've seen in any responses.
    # (Registers are accessed in blocks of 60, so 120 means we've seen 120 to 179.)
    # They are inialised with the minimum count that all systems have.
    # Input registers 0-59 and 180+ are inverter,  60-179 are battery.
    max_holding_reg: int = 120
    max_input_reg: int = 0
    max_battery_reg: int = 60

    inverter_serial_number: str = ""                # cached from incoming responses
    data_adapter_serial_number: str = ""            # cached from incoming responses
    number_batteries: int = 0                       # number of known batteries

    class Config:  # noqa: D106
        allow_mutation = True
        frozen = False

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if not self.register_caches:
            self.register_caches = {0x32: RegisterCache()}

    # These can be overriden by application sublass to receive (raw) updates
    # Note that execute in the context of the reader thread, and so MUST NOT
    # do any significant work. Also, raising exceptions seems problematic
    # in asyncio code - seems to end up hanging the reader thread.
    def holding_register_updated(self, reg, value):
        pass

    def holding_registers_updated(self, base, count):
        pass

    def input_registers_updated(self, base, count):
        pass

    def battery_registers_updated(self, idx, base, count):
        pass

    # internal wrappers to update state before calling the above
    def _holding_registers_updated(self, pdu):
        base = pdu.base_register
        if self.max_holding_reg < base:
            _logger.info("updating max holding reg to %d", base)
            self.max_holding_reg = base
        self.holding_registers_updated(base, len(pdu.register_values))

    def _input_registers_updated(self, pdu):
        base = pdu.base_register
        address = pdu.slave_address
        if base < 60 or base >= 180:
            # an inverter register
            assert address <= 0x32
            if self.max_input_reg < base:
                _logger.info("updating max input reg to %d", base)
                self.max_input_reg = base
            self.input_registers_updated(base, len(pdu.register_values))
            return
        
        if address < 0x32:
            return  # no valid battery with a smaller address

        if base == 60 and address >= 0x32 + self.number_batteries:
            # a new battery ?  Look to see if it's valid
            if self.battery_is_valid(self.register_caches[address]):
                _logger.info("new battery detected at address 0x%x", address)
                self.number_batteries = address - 0x31
            else:
                return

        if self.max_battery_reg < base:
            _logger.info("updating max battery reg to %d", base)
            self.max_battery_reg = base
        self.battery_registers_updated(pdu.slave_address - 0x32, base, len(pdu.register_values))

    # Decide whether a battery seems valid
    def battery_is_valid(self, cache):
        # serial number is in IR(110) .. IR(114) and should be ascii digits
        return cache[IR(110)] >= 0x30 and cache[IR(114)] >= 0x30

    # entry point for processing an incoming message
    def update(self, pdu: ClientIncomingMessage):
        """Update the Plant state from a PDU message."""
        if not isinstance(pdu, TransparentResponse):
            _logger.debug(f"Ignoring non-Transparent response {pdu}")
            return
        if isinstance(pdu, NullResponse):
            _logger.debug(f"Ignoring Null response {pdu}")
            return
        if pdu.error:
            _logger.debug(f"Ignoring error response {pdu}")
            return
        _logger.debug(f"Handling {pdu}")

        slave_address = pdu.slave_address
        if slave_address >= 0x32:
            # keep as-is
            pass
        elif isinstance(pdu, ReadInputRegistersResponse) and pdu.base_register == 60 and pdu.base_register < 180:
            # input registers 60-179 are for a battery.
            # any such result for a slave address < 0x32 will just result in invalid values.
            # We don't want to map to 0x32, since that would overwrite the real battery #0 registers.
            # So just leave it as it is.
            pass
        elif pdu.slave_address in (0x11, 0x30):
            # rewrite cloud and mobile app responses to "normal" inverter address
            slave_address = 0x32

        if slave_address not in self.register_caches:
            _logger.debug(
                f"First time encountering slave address 0x{slave_address:02x}"
            )
            self.register_caches[slave_address] = RegisterCache()

        self.inverter_serial_number = pdu.inverter_serial_number
        self.data_adapter_serial_number = pdu.data_adapter_serial_number

        cache = self.register_caches[slave_address]
        if isinstance(pdu, ReadInputRegistersResponse):
            for i, v in enumerate(pdu.register_values, start=pdu.base_register):
                cache[IR(i)] = v
            if slave_address >= 0x32:
                self._input_registers_updated(pdu)
        elif isinstance(pdu, ReadHoldingRegistersResponse):
            for i, v in enumerate(pdu.register_values, start=pdu.base_register):
                cache[HR(i)] = v
            if slave_address >= 0x32:
                self._holding_registers_updated(pdu)
        elif isinstance(pdu, WriteHoldingRegisterResponse):
            if pdu.register == 0:
                _logger.warning(f"Ignoring, likely corrupt: {pdu}")
            else:
                cache[HR(pdu.register)] = pdu.value
                if slave_address >= 0x32:
                    self.holding_register_updated(pdu.register, pdu.value)

    def detect_batteries(self) -> None:
        """Determine the number of batteries based on whether the register data is valid.

        Since we attempt to decode register data in the process, it's possible for an
        exception to be raised.
        """
        i = 0
        for i in range(6):
            try:
                # TODO must not use assert for required runtime tests
                assert Battery.from_orm(self.register_caches[i + 0x32]).is_valid()
            except (KeyError, AssertionError):
                break
        self.number_batteries = i
        _logger.info("Found %d batteries", i)

    @property
    def inverter(self) -> Inverter:
        """Return Inverter model for the Plant."""
        return Inverter(self.register_caches[0x32])

    @property
    def batteries(self) -> list[Battery]:
        """Return Battery models for the Plant."""
        return [
            Battery(self.register_caches[i + 0x32])
            for i in range(self.number_batteries)
        ]
