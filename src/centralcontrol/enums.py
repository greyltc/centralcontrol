from enum import Enum, IntEnum, auto, unique

# TODO: instead of duplicating this in slotdb, figure out where it should live!


@unique
class Event(Enum):
    # the strings here pick out datadabse table names
    NONE = 0
    SS = "ss"
    LIGHT_SWEEP = "isweep"
    ELECTRIC_SWEEP = "sweep"
    MPPT = "mppt"


@unique
class DataTool(IntEnum):
    NONE = 0
    SMU = auto()


@unique
class DevType(IntEnum):
    NONE = 0
    SOLAR_CELL = auto()
    LED = auto()


@unique
class Fixed(IntEnum):
    NONE = 0
    CURRENT = auto()
    VOLTAGE = auto()
    RESISTANCE = auto()


@unique
class UserTypes(IntEnum):
    NONE = 0
    SUPERUSER = auto()
    SUPERVIEWER = auto()
    USER = auto()
    VISITOR = auto()
