"""NSL - Nuke Survival Loadout package root.

Re-exports kept intentionally minimal. The ``compat`` submodule is NOT
auto-imported here because its top-level code resolves PySide at import
time; in headless boot NSL must not pull in Qt. UI code paths
import it explicitly via ``from nsl import compat``.
"""

__version__ = "0.1.0"

from nsl import atomic_io, constants, log  # noqa: E402,F401

__all__ = ["__version__", "atomic_io", "constants", "log"]
