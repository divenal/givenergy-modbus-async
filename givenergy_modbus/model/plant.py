import logging
from typing import Optional, Iterator, Sequence

from .battery import Battery
from .inverter import Inverter
from .register import Register, HR, IR
from .register_cache import RegisterCache
from ..pdu import (
    BasePDU,
    NullResponse,
    ReadHoldingRegistersResponse,
    ReadInputRegistersResponse,
    TransparentResponse,
    WriteHoldingRegisterResponse,
)

_logger = logging.getLogger(__name__)


class Plant:
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
    # TODO: I think it might be better to map inverter to 0x31 ?
    register_caches: dict[int, RegisterCache]

    inverter_serial_number: str = ''  # cached from incoming responses
    data_adapter_serial_number: str = ''  # cached from incoming responses
    number_batteries: int

    # the full set of register base numbers we have seen
    # used when a plant refresh is requested.
    # Can be optionally supplied when plant is instantiated,
    # else is discovered as updates arrive.
    # TODO: currently we don't add an entry when we see a
    # WriteHoldingRegisterResponse for a register that's not
    # in a block we're already aware of.
    registers: set[Register]

    def __init__(
        self,
        num_batteries=0,
        registers: Optional[set[Register]] = None,
    ) -> None:
        """Initialise a plant instance.

        Normally, registers would be left unset, and a detection
        performed during startup to probe for available registers and
        batteries. But if an application knows which subset of registers
        they wish to use, it may specify it.

        num_batteries can be supplied if known in advance, otherwise it would
        normally be probed.

        Note that register updates are sent to all connected clients, so even if
        an application initialises these, additional registers and batteries may
        appear if other applications probe for them.
        """

        if registers is None:
            # TODO this is the initial set which should be available
            # on all devices. The plan is that different inverter
            # families (AIO, 3-phase, ...) will propose additional
            # register subsets to be probed, once the model has been
            # determined.
            registers = {HR(0), HR(60), HR(120), HR(180), IR(0), IR(180)}

        self.registers = registers
        self.register_caches = {0x32: RegisterCache()}
        self.number_batteries = num_batteries

    # Note that these execute in the context of the reader thread, and so MUST NOT
    # do any significant work. Also, raising exceptions seems problematic
    # in asyncio code - seems to end up hanging the reader thread.

    def registers_updated(self, base: Register, count: int, values: Sequence) -> None:
        """Can be overridden by subclass to receive Read*RegisterResponse notifications."""

    def register_written(self, base: Register, value: int) -> None:
        """Can be overridden by subclass to receive WriteHoldingRegisterResponse notifications."""

    def battery_updated(self, number: int, values: Sequence) -> None:
        """Can be overridden by subclass to receive update notifications."""

    # Decide whether a battery seems valid
    # We probably ought to make a Battery instance and ask for the serial number
    # from that, but...
    def battery_is_valid(self, cache):
        # serial number is in IR(110) .. IR(114) and should be ascii digits
        return cache[IR(110)] >= 0x30 and cache[IR(114)] >= 0x30

    def _process_read_registers_response(
        self, address: int, pdu: TransparentResponse
    ) -> None:
        """Process a ReadRegistersResponse message."""

        # copy the values into the register cache
        cache = self.register_caches[address]
        cache.update(pdu.enumerate())

        basereg = pdu.register_class(pdu.base_register)
        if basereg == IR(60):
            # A battery. Perhaps it's new.
            if address - 0x32 >= self.number_batteries:
                # But first check that it's valid - reads from non-existent
                # battery addresses just return a page of zeros.
                if self.battery_is_valid(cache):
                    _logger.info("new battery detected at address 0x%x", address)
                    self.number_batteries = address - 0x31
            self.battery_updated(address - 0x32, pdu.register_values)
        else:
            # inverter registers.
            if int(basereg) % 60 == 0:
                # ReadRegisters typically operate on blocks of 60 at a time.
                # However, when using the cloud API to read a register, it
                # seems to do a ReadHoldingRegisterRequest of that single register,
                # so we only record it if it's a multiple of 60.
                self.registers.add(basereg)
            self.registers_updated(basereg, pdu.register_count, pdu.register_values)

    def _process_write_holding_register_response(
        self, address: int, pdu: WriteHoldingRegisterResponse
    ) -> None:
        """Process a WriteHoldingRegisterResponse message."""
        if pdu.register == 0:
            _logger.warning("Ignoring, likely corrupt: %s", pdu)
        else:
            reg = HR(pdu.register)
            self.register_caches[address][reg] = pdu.value
            self.register_written(reg, pdu.value)

    # entry point for processing an incoming message

    def update(self, pdu: BasePDU) -> None:
        """Update the Plant state from a PDU message."""
        if not isinstance(pdu, TransparentResponse):
            _logger.debug(f"Ignoring non-Transparent response {pdu}")
            return
        if isinstance(pdu, NullResponse):
            _logger.debug("Ignoring Null response %s", pdu)
            return
        if pdu.error:
            _logger.debug("Ignoring error response %s", pdu)
            return
        _logger.debug("Handling %s", pdu)

        self.inverter_serial_number = pdu.inverter_serial_number
        self.data_adapter_serial_number = pdu.data_adapter_serial_number

        address = pdu.slave_address

        if address >= 0x32:
            pass
        elif isinstance(pdu, ReadInputRegistersResponse) and pdu.base_register == 60:
            # This is a battery register, but from a bogus address.
            # Don't map to 0x32, since that would overwrite battery #0 with rubbish.
            _logger.info("Ignoring input registers at 60 from 0x%02x", address)
            return
        elif address in (0x11, 0x30, 0x31):
            # map these cloud and mobile app responses to our model inverter address
            address = 0x32
        else:
            # may as well just discard it rather than putting it in an inaccessible cache slot
            return

        if address not in self.register_caches:
            _logger.info("First time encountering address 0x%02x", address)
            self.register_caches[address] = RegisterCache()

        try:
            # add the registers into the model
            # because this can call out to user-supplied hooks, be
            # prepared for spurious exceptions
            if isinstance(
                pdu, (ReadInputRegistersResponse, ReadHoldingRegistersResponse)
            ):
                self._process_read_registers_response(address, pdu)
            elif isinstance(pdu, WriteHoldingRegisterResponse):
                self._process_write_holding_register_response(address, pdu)
        except Exception as ex:
            print(ex)

    def refresh(
        self,
        full_refresh: bool = True,
        registers: Optional[set[Register]] = None,
        max_batteries=-1,
    ) -> Iterator[tuple[int, Register]]:
        """Returns a sequence of (address, base_register) pairs that should be fetched.

        full_refresh controls whether holding registers are included or not.
        If a register set is supplied, that takes precendence over the set
        of registers the plant is aware of. (But it will still be filtered
        by full_refresh.)

        """

        if registers is None:
            registers = self.registers
        if max_batteries < 0:
            max_batteries = self.number_batteries

        for reg in registers:
            if full_refresh or isinstance(reg, IR):
                yield (0x32, reg)

        for idx in range(max_batteries):
            yield (0x32 + idx, IR(60))

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
