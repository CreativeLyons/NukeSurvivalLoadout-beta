"""Path-identity helper - comparison-only canonicalization.

Windows (always) and default macOS APFS volumes are case-insensitive,
case-preserving: ``C:/Plugins`` and ``c:/plugins`` name the same folder.
``os.path.normpath`` unifies separators and dot segments but NOT case,
so equality/membership tests on normpath output treat one folder as two
whenever the case differs (drive-letter case from a file dialog vs. a
hand-typed path, ``nuke.pluginPath()`` echoes, shell completions).

:func:`canon_for_compare` layers ``normcase`` (lowercase + backslash
separators on Windows; identity on POSIX) on top of ``normpath``.

The result is for EQUALITY AND MEMBERSHIP ONLY - never store it, never
display it. Stored paths keep the user's original case (the filesystems
are case-preserving and so is NSL).
"""

from __future__ import annotations

import os
from typing import Union

__all__ = ["canon_for_compare"]

PathLike = Union[str, "os.PathLike[str]"]


def canon_for_compare(path: PathLike) -> str:
    """Canonical form of ``path`` for identity tests on case-insensitive
    filesystems. Comparison-only; see the module docstring."""
    return os.path.normcase(os.path.normpath(os.fspath(path)))
