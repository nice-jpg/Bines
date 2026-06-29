"""Adapters that expose Bines task runners as mind_controller slaves."""

from .raw_run_adapter import RawRunSlave, slave

__all__ = ["RawRunSlave", "slave"]

