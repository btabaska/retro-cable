"""The scheduling engine: a schedule is a pure function of (channel, simulated date, catalog
version, seed). Nothing here reads the wall clock; callers pass simulated times in."""

from retro_cable.scheduler.clock import BROADCAST_DAY_START, broadcast_day, simulated_now
from retro_cable.scheduler.engine import ChannelOffAir, Scheduler, Slot, SlotKind
from retro_cable.scheduler.library import Bumper, Library, Program, load_library

__all__ = [
    "BROADCAST_DAY_START",
    "Bumper",
    "ChannelOffAir",
    "Library",
    "Program",
    "Scheduler",
    "Slot",
    "SlotKind",
    "broadcast_day",
    "load_library",
    "simulated_now",
]
