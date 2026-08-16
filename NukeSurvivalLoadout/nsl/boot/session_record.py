"""Session load recording for the NSL panel.

Loadout files import :func:`record_loaded` inside a try/except and fall
back to doing nothing when NSL is absent. The record only feeds the
panel's Loaded counter, so skipping it is always safe.
"""

from __future__ import annotations

import os

import nuke

from nsl.paths import canon_for_compare


def record_global_dir(path: str) -> None:
    """Record the resolved Global plugins dir for this session.

    Stamped at boot so the panel reads the value the head resolved. The
    head is Python, so a TD may compute the path at runtime.
    """
    nuke._nsl_global_plugins_dir = os.path.normpath(path)


def recorded_global_dir() -> "str | None":
    """The Global plugins dir recorded at boot, or ``None`` when absent."""
    recorded = getattr(nuke, "_nsl_global_plugins_dir", None)
    return recorded if isinstance(recorded, str) else None


def record_global_loadout_dir(path: str) -> None:
    """Record the resolved Global loadout dir for this session.

    Stamped at boot so the panel reads the value the head resolved. The
    read-only Global model then matches what boot actually loaded.
    """
    nuke._nsl_global_loadout_dir = os.path.normpath(path)


def recorded_global_loadout_dir() -> "str | None":
    """The Global loadout dir recorded at boot, or ``None`` when absent."""
    recorded = getattr(nuke, "_nsl_global_loadout_dir", None)
    return recorded if isinstance(recorded, str) else None


def record_loaded(name: str, path: str, gui: bool = False) -> None:
    """Append one plugin-load record to ``nuke._nsl_loaded_session``.

    Idempotent per path: a second call with the same normalized path is
    ignored, so the explicit-call pass and the folder sweep can both
    report the same plugin without double-counting.
    """
    rec = getattr(nuke, "_nsl_loaded_session", None)
    if rec is None:
        rec = nuke._nsl_loaded_session = []
    norm = os.path.normpath(path)
    # Dedup on the case-folded path. The stored path keeps its own case.
    key = canon_for_compare(norm)
    if any(canon_for_compare(item.get("path", "")) == key for item in rec):
        return
    rec.append({"name": name, "path": norm, "gui": bool(gui)})
