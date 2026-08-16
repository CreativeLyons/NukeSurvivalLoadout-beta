"""Path-identity helper - comparison-only canonicalization.

Windows and default macOS APFS treat ``C:/Plugins`` and ``c:/plugins`` as
one folder, but ``os.path.normpath`` does not fold case. Equality and
membership tests on its output therefore see one folder as two.

:func:`canon_for_compare` returns a hashable key that is case-folded only
on case-insensitive volumes. Use it for comparison only. Never store it
and never display it, because stored paths keep the user's own case.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Dict, Optional, Union

__all__ = ["canon_for_compare"]

PathLike = Union[str, "os.PathLike[str]"]

# ``os.pathconf_names`` misses this on some macOS builds, but
# ``os.pathconf`` still accepts the raw selector number on Darwin.
_DARWIN_PC_CASE_SENSITIVE = 11

# Keyed by device id (``os.stat().st_dev``), which identifies a mounted
# volume, so each volume is probed at most once per process.
_volume_case_insensitive: Dict[int, bool] = {}

# Fallback when the volume cannot be probed. Default macOS APFS folds
# case and default Linux does not. Windows never reaches this code.
_DEFAULT_CASE_INSENSITIVE = sys.platform == "darwin"


def _nearest_existing(path: str) -> Optional[str]:
    """Return ``path`` if it exists, else its nearest existing ancestor.

    A new folder lands on the same volume as its parent, so the ancestor
    is a valid stand-in for the case probe. ``None`` if nothing exists.
    """
    p = os.path.abspath(path)
    while True:
        if os.path.exists(p):
            return p
        parent = os.path.dirname(p)
        if parent == p:  # reached the root
            return None
        p = parent


def _probe_pathconf(existing: str) -> Optional[bool]:
    """Ask the OS whether ``existing``'s volume is case-insensitive.

    ``None`` when the platform has no answer. ``PC_CASE_SENSITIVE`` reports
    1 for a case-sensitive volume, so insensitive is ``== 0``.
    """
    selector = os.pathconf_names.get("PC_CASE_SENSITIVE")
    if selector is None and sys.platform == "darwin":
        selector = _DARWIN_PC_CASE_SENSITIVE
    if selector is None:
        return None
    try:
        return os.pathconf(existing, selector) == 0
    except (OSError, ValueError):
        return None


def _probe_empirical(existing: str) -> Optional[bool]:
    """Empirically test case-insensitivity by flipping a temp file's case.

    The same inode under the flipped name means the volume folds case.
    ``None`` when the test cannot run, such as a read-only directory.
    """
    probe_dir = existing if os.path.isdir(existing) else os.path.dirname(existing)
    if not probe_dir or not os.path.isdir(probe_dir):
        return None
    real = None
    try:
        fd, real = tempfile.mkstemp(prefix="NSLcaseAa", dir=probe_dir)
        os.close(fd)
        head, base = os.path.split(real)
        flipped = os.path.join(head, base.swapcase())
        if flipped == real:  # nothing cased to flip -> inconclusive
            return None
        try:
            return os.path.samestat(os.stat(real), os.stat(flipped))
        except FileNotFoundError:
            return False  # flipped name does not exist -> case-sensitive
    except OSError:
        return None
    finally:
        if real is not None:
            try:
                os.unlink(real)
            except OSError:
                pass


def _volume_is_case_insensitive(path: str) -> bool:
    """Whether ``path``'s volume folds case, cached per volume by device id.

    Never raises. Any probe failure falls back to the platform default.
    """
    try:
        existing = _nearest_existing(path)
        if existing is None:
            return _DEFAULT_CASE_INSENSITIVE

        try:
            dev = os.stat(existing).st_dev
        except OSError:
            dev = None

        if dev is not None and dev in _volume_case_insensitive:
            return _volume_case_insensitive[dev]

        result = _probe_pathconf(existing)
        if result is None:
            result = _probe_empirical(existing)
        if result is None:
            result = _DEFAULT_CASE_INSENSITIVE

        if dev is not None:
            _volume_case_insensitive[dev] = result
        return result
    except Exception:  # noqa: BLE001 - a compare helper must never crash a loop
        return _DEFAULT_CASE_INSENSITIVE


def canon_for_compare(path: PathLike) -> str:
    """Hashable form of ``path`` for identity tests. Comparison only, see
    the module docstring."""
    normalized = os.path.normpath(os.fspath(path))
    if os.name == "nt":
        # normcase already lowercases and swaps separators on Windows.
        return os.path.normcase(normalized)
    # ``normcase`` is the identity function on POSIX, so fold case here.
    if _volume_is_case_insensitive(normalized):
        return normalized.casefold()
    return normalized
