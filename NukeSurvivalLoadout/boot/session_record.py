"""Session load recording for the NSL panel.

Loadout files import :func:`record_loaded` (aliased to ``_nsl_record``)
inside a try/except and fall back to a no-op when NSL is absent. The
record feeds the panel's Loaded counter (read in ``ui/registry.py``);
it has no effect on what actually loads, so skipping it is always safe.
"""

from __future__ import annotations

import os

import nuke

from NukeSurvivalLoadout.paths import canon_for_compare


def record_global_dir(path: str) -> None:
    """Record the resolved Global plugins dir for this session.

    Stamped by ``boot.global_loader.nsl_load_global`` at boot so the
    panel reads the head's actual resolved value instead of re-deriving
    it (the head is Python; a TD may compute the path dynamically).
    """
    nuke._nsl_global_plugins_dir = os.path.normpath(path)


def recorded_global_dir() -> "str | None":
    """The Global plugins dir recorded at boot, or ``None`` when absent."""
    recorded = getattr(nuke, "_nsl_global_plugins_dir", None)
    return recorded if isinstance(recorded, str) else None


def record_global_loadout_dir(path: str) -> None:
    """Record the resolved Global loadout dir for this session.

    Stamped by ``boot.global_loader.nsl_load_global`` at boot so the
    panel reads the head's actual resolved value instead of re-deriving
    it (the head is Python; a TD may compute the path dynamically). The
    panel's read-only Global model and its ``has_loadout_copy`` case-A/B
    switch then match what boot actually loaded.
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
    # Dedup by case-folded identity (Windows/APFS are case-insensitive);
    # the stored path keeps its original case for display.
    key = canon_for_compare(norm)
    if any(canon_for_compare(item.get("path", "")) == key for item in rec):
        return
    rec.append({"name": name, "path": norm, "gui": bool(gui)})
