"""Path-identity helper - comparison-only canonicalization.

Windows (always) and default macOS APFS volumes are case-insensitive,
case-preserving: ``C:/Plugins`` and ``c:/plugins`` name the same folder.
``os.path.normpath`` unifies separators and dot segments but NOT case,
so equality/membership tests on normpath output treat one folder as two
whenever the case differs (drive-letter case from a file dialog vs. a
hand-typed path, ``nuke.pluginPath()`` echoes, shell completions).

:func:`canon_for_compare` returns a single hashable string key suitable
for dict keys, set membership, and equality. It case-folds that key only
when the path lives on a case-insensitive volume:

* Windows: ``os.path.normcase`` already lowercases (and swaps separators),
  so it is used directly.
* POSIX: ``os.path.normcase`` is the identity function, so it would treat
  ``/p/Foo`` and ``/p/foo`` as two folders even on default (case-insensitive)
  APFS. To match the documented contract we probe the path's volume:
  - case-insensitive volume (default macOS APFS) -> ``str.casefold`` the
    normpath result so case-only variants collapse to one key;
  - case-sensitive volume (case-sensitive APFS, default Linux) -> identity,
    because there ``Foo`` and ``foo`` really are two distinct folders.

The probe result is cached per volume (by device id) so hot dedup loops do
not stat on every call. The probe order is: named ``PC_CASE_SENSITIVE``
pathconf when available, then the raw macOS ``_PC_CASE_SENSITIVE`` selector,
then an empirical dual-name test (create a temp file, stat its case-flipped
name, compare inodes). For a not-yet-created path we walk up to the nearest
existing ancestor, since the new folder will live on the same volume as its
parent. Any failure falls back conservatively (case-fold on macOS, identity
elsewhere) and never raises - a comparison helper must not crash a dedup loop.

The result is for EQUALITY AND MEMBERSHIP ONLY - never store it, never
display it. Stored paths keep the user's original case (the filesystems
are case-preserving and so is NSL).
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Dict, Optional, Union

__all__ = ["canon_for_compare"]

PathLike = Union[str, "os.PathLike[str]"]

# macOS ``_PC_CASE_SENSITIVE`` pathconf selector. Python's portable
# ``os.pathconf_names`` does not expose it on every build, but the raw int is
# still accepted by ``os.pathconf`` on Darwin, so we keep it as a fallback.
_DARWIN_PC_CASE_SENSITIVE = 11

# Per-volume cache of "is this volume case-insensitive?" keyed by device id
# (``os.stat().st_dev``). A device id uniquely and stably identifies a mounted
# volume, so we probe each volume at most once per process.
_volume_case_insensitive: Dict[int, bool] = {}

# Conservative default when the volume cannot be probed at all: default macOS
# APFS is case-insensitive, so case-fold there; Linux's default is
# case-sensitive, so keep identity. (Windows never reaches this code.)
_DEFAULT_CASE_INSENSITIVE = sys.platform == "darwin"


def _nearest_existing(path: str) -> Optional[str]:
    """Return ``path`` if it exists, else its nearest existing ancestor.

    A not-yet-created folder lives on the same volume as its parent, so the
    nearest existing ancestor is a valid stand-in for the case probe. Returns
    ``None`` only if nothing on the chain (including the root) resolves.
    """
    p = os.path.abspath(path)
    while True:
        if os.path.exists(p):
            return p
        parent = os.path.dirname(p)
        if parent == p:  # reached the root without finding anything
            return None
        p = parent


def _probe_pathconf(existing: str) -> Optional[bool]:
    """Ask the OS whether ``existing``'s volume is case-insensitive.

    Returns ``True`` (insensitive) / ``False`` (sensitive), or ``None`` when
    the platform offers no ``PC_CASE_SENSITIVE`` answer. ``PC_CASE_SENSITIVE``
    reports 1 for a case-sensitive volume, so insensitive is ``== 0``.
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

    Create a uniquely named temp file in ``existing`` (a directory), then stat
    the same name with its basename case swapped. If the flipped name resolves
    to the same inode the volume folds case; if it is absent the volume is
    case-sensitive. Returns ``None`` when the test cannot be run (not a
    writable directory, no cased characters to flip, etc.).
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

    Robust by construction: any probe failure falls back to the conservative
    platform default, and the function never raises.
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
    """Canonical, hashable form of ``path`` for identity tests on
    case-insensitive filesystems. Comparison-only; see the module docstring."""
    normalized = os.path.normpath(os.fspath(path))
    if os.name == "nt":
        # normcase already lowercases and swaps separators on Windows.
        return os.path.normcase(normalized)
    # POSIX: normcase is identity, so fold case ourselves only when the
    # path's volume is case-insensitive (default macOS APFS).
    if _volume_is_case_insensitive(normalized):
        return normalized.casefold()
    return normalized
