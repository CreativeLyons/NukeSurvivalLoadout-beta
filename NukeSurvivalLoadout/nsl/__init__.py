"""NSL - Nuke Survival Loadout package root.

``compat`` is deliberately not imported here. It resolves PySide at
import time, and a headless boot must not pull in Qt.
"""

__version__ = "0.2.1"

from nsl import atomic_io, constants, log  # noqa: E402,F401

__all__ = ["__version__", "atomic_io", "constants", "log"]
