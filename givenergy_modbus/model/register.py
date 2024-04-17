from dataclasses import dataclass
import datetime
from json import JSONEncoder
from typing import TYPE_CHECKING, DefaultDict, Optional, Any, Callable, Union
import math

from givenergy_modbus.model import TimeSlot

# Registers are the fundamental entity on inverter and battery.
# They come in 'Holding' and 'Input' flavours - the former are
# read-write and the latter are read-only.

# The Register class is just used as a key to identify a register.
class Register:
    """Register base class."""

    TYPE_HOLDING = "HR"
    TYPE_INPUT = "IR"

    _type: str
    _idx: int

    def __init__(self, idx):
        self._idx = idx

    def __str__(self):
        return "%s_%d" % (self._type, int(self._idx))

    __repr__ = __str__

    def __eq__(self, other):
        return (
            isinstance(other, Register)
            and self._type == other._type
            and self._idx == other._idx
        )

    def __hash__(self):
        return hash((self._type, self._idx))


class HR(Register):
    """Holding Register."""

    _type = Register.TYPE_HOLDING


class IR(Register):
    """Input Register."""

    _type = Register.TYPE_INPUT

    

# A RegisterCache is a dictionary containing the most
# recent register values read from a modbus node.
# The Plant has one RegisterCache per node address.

class RegisterCache(DefaultDict[Register, int]):
    """Holds a cache of Registers populated after querying a device."""

    def __init__(self, registers: Optional[dict[Register, int]] = None) -> None:
        if registers is None:
            registers = {}
        super().__init__(lambda: 0, registers)

    def json(self) -> str:
        """Return JSON representation of the register cache, to mirror `from_json()`."""  # noqa: D402,D202,E501
        return json.dumps(self)

    @classmethod
    def from_json(cls, data: str) -> "RegisterCache":
        """Instantiate a RegisterCache from its JSON form."""

        def register_object_hook(object_dict: dict[str, int]) -> dict[Register, int]:
            """Rewrite the parsed object to have Register instances as keys instead of their (string) repr."""
            lookup = {"HR": HR, "IR": IR}
            ret = {}
            for k, v in object_dict.items():
                if k.find("(") > 0:
                    reg, idx = k.split("(", maxsplit=1)
                    idx = idx[:-1]
                elif k.find(":") > 0:
                    reg, idx = k.split(":", maxsplit=1)
                else:
                    raise ValueError(f"{k} is not a valid Register type")
                try:
                    ret[lookup[reg](int(idx))] = v
                except ValueError:
                    # unknown register, discard silently
                    continue
            return ret

        return cls(registers=(json.loads(data, object_hook=register_object_hook)))


class RegisterEncoder(JSONEncoder):
    """Custom JSONEncoder to work around Register behaviour.

    This is a workaround to force registers to render themselves as strings instead of
    relying on the internal identity by default.
    """

    def default(self, o: Any) -> str:
        """Custom JSON encoder to treat RegisterCaches specially."""
        if isinstance(o, Register):
            return f"{o._type}_{o._idx}"
        else:
            return super().default(o)



# Raw registers are simply unsigned 16-bit integers.
# The following classes are used to convert them to a more appropriate
# python data type

class Converter:
    """Type of data register represents."""

    @staticmethod
    def uint16(val: int) -> int:
        """Simply return the raw unsigned 16-bit integer register value."""
        if val is not None:
            return int(val)

    @staticmethod
    def int16(val: int) -> int:
        """Interpret as a 16-bit integer register value."""
        if val is not None:
            if val & (1 << (16 - 1)):
                val -= 1 << 16
            return val

    @staticmethod
    def duint8(val: int, *idx: int) -> int:
        """Split one register into two unsigned 8-bit ints and return the specified index."""
        if val is not None:
            vals = (val >> 8), (val & 0xFF)
            return vals[idx[0]]

    @staticmethod
    def uint32(high_val: int, low_val: int) -> int:
        """Combine two registers into an unsigned 32-bit int."""
        if high_val is not None and low_val is not None:
            return (high_val << 16) + low_val

    @staticmethod
    def timeslot(start_time: int, end_time: int) -> TimeSlot:
        """Interpret register as a time slot."""
        if start_time is not None and end_time is not None:
            return TimeSlot.from_repr(start_time, end_time)

    @staticmethod
    def bool(val: int) -> bool:
        """Interpret register as a bool."""
        if val is not None:
            return bool(val)
        return None

    @staticmethod
    def string(*vals: int) -> Optional[str]:
        """Represent one or more registers as a concatenated string."""
        if vals is not None and None not in vals:
            return (
                b"".join(v.to_bytes(2, byteorder="big") for v in vals)
                .decode(encoding="latin1")
                .replace("\x00", "")
                .upper()
            )
        return None

    @staticmethod
    def fstr(val, fmt) -> Optional[str]:
        """Render a value using a format string."""
        if val is not None:
            return f"{val:{fmt}}"
        return None

    @staticmethod
    def firmware_version(dsp_version: int, arm_version: int) -> Optional[str]:
        """Represent ARM & DSP firmware versions in the same format as the dashboard."""
        if dsp_version is not None and arm_version is not None:
            return f"D0.{dsp_version}-A0.{arm_version}"

    @staticmethod
    def inverter_max_power(device_type_code: str) -> Optional[int]:
        """Determine max inverter power from device_type_code."""
        dtc_to_power = {
            "2001": 5000,
            "2002": 4600,
            "2003": 3600,
            "3001": 3000,
            "3002": 3600,
            "4001": 6000,
            "4002": 8000,
            "4003": 10000,
            "4004": 11000,
            "8001": 6000,
        }
        return dtc_to_power.get(device_type_code)

    @staticmethod
    def hex(val: int, width: int = 4) -> str:
        """Represent a register value as a 4-character hex string."""
        if val is not None:
            return f"{val:0{width}x}"

    @staticmethod
    def milli(val: int) -> float:
        """Represent a register value as a float in 1/1000 units."""
        if val is not None:
            return val / 1000

    @staticmethod
    def centi(val: int) -> float:
        """Represent a register value as a float in 1/100 units."""
        if val is not None:
            return val / 100

    @staticmethod
    def deci(val: int) -> float:
        """Represent a register value as a float in 1/10 units."""
        if val is not None:
            return val / 10

    @staticmethod
    def datetime(year, month, day, hour, min, sec) -> Optional[datetime]:
        """Compose a datetime from 6 registers."""
        if None not in [year, month, day, hour, min, sec]:
            return datetime.datetime(year + 2000, month, day, hour, min, sec)
        return None


# This provides a recipe for converting one or more low-level registers into
# a human-readable format.
# The pre_conv is one of the Convert functions above, or None
# The post_conv can be one of the above, or an enum class
# In addition, the optional valid field can be used to store the range
# of valid values that can be written to a holding register. None means
# the register is not writable.
@dataclass(init=False)
class RegisterDefinition:
    """Specifies how to convert raw register values into their actual representation."""

    pre_conv: Union[Callable, tuple, None]
    post_conv: Union[Callable, tuple[Callable, Any], None]
    registers: tuple["Register"]
    valid: None

    def __init__(self, *args, valid = None):
        self.pre_conv = args[0]
        self.post_conv = args[1]
        self.registers = args[2:]  # type: ignore[assignment]
        self.valid = valid

    def __hash__(self):
        return hash(self.registers)


# This implements the recipe described in a RegisterDefintion to
# provide a value. It is the parent class of Inverter and Battery

class RegisterGetter:
    """Specifies how device attributes are derived from raw register values."""

    # these are provided by a subclass
    REGISTER_LUT: dict[str, RegisterDefinition]
    cache: RegisterCache

    def __init__(self, cache: RegisterCache):
        self.cache = cache

    # this implements the magic of providing attributes based
    # on the register lut
    def __getattr__(self, name:str):
        return self.get(name)

    # or you can just use inverter.get('name')
    def get(self, key: str, default: Any = None) -> Any:
        """Return a named register's value, after pre- and post-conversion."""
        try:
            r = self.REGISTER_LUT[key]
        except KeyError:
            return default
        regs = [self.cache[r] for r in r.registers]

        if None in regs:
            return None

        if r.pre_conv:
            if isinstance(r.pre_conv, tuple):
                args = regs + list(r.pre_conv[1:])
                val = r.pre_conv[0](*args)
            else:
                val = r.pre_conv(*regs)
        else:
            val = regs

        if r.post_conv:
            if isinstance(r.post_conv, tuple):
                return r.post_conv[0](val, *r.post_conv[1:])
            else:
                if not isinstance(r.post_conv, Callable):
                    pass
                return r.post_conv(val)
        return val
