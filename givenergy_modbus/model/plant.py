import logging
from typing import Any

from givenergy_modbus.model import GivEnergyBaseModel
from givenergy_modbus.model.battery import Battery
from givenergy_modbus.model.inverter import Inverter
from givenergy_modbus.model.register import HR, IR
from givenergy_modbus.model.register_cache import (
    RegisterCache,
)
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

    # This is the largest holding register number we've seen in any response.
    max_holding_reg: int = 
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

    # can be overriden by application sublass to receive (raw) updates
    def holding_register_updated(self, reg, value):
        pass
    
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
        elif isinstance(pdu, ReadInputRegistersResponse) and pdu.base_register == 60:
            # leave it alone - input registers starting at 60 are for
            # the battery, but asking for those registers at an arbitrary address
            # will return all zeros. Mapping slave_address to 0x32 would result in
            # overwriting the real battery #0 registers
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
        elif isinstance(pdu, ReadHoldingRegistersResponse):
            for i, v in enumerate(pdu.register_values, start=pdu.base_register):
                cache[HR(i)] = v
        elif isinstance(pdu, WriteHoldingRegisterResponse):
            if pdu.register == 0:
                _logger.warning(f"Ignoring, likely corrupt: {pdu}")
            else:
                cache[HR(pdu.register)] = pdu.value
                self.holding_register_updated(pdu.register, pdu.value)

    def detect_batteries(self) -> None:
        """Determine the number of batteries based on whether the register data is valid.

        Since we attempt to decode register data in the process, it's possible for an
        exception to be raised.
        """
        i = 0
        for i in range(6):
            try:
                assert Battery.from_orm(self.register_caches[i + 0x32]).is_valid()
            except (KeyError, AssertionError):
                break
        self.number_batteries = i

    @property
    def inverter(self) -> Inverter:
        """Return Inverter model for the Plant."""
        return Inverter.from_orm(self.register_caches[0x32])

    @property
    def batteries(self) -> list[Battery]:
        """Return Battery models for the Plant."""
        return [
            Battery.from_orm(self.register_caches[i + 0x32])
            for i in range(self.number_batteries)
        ]
