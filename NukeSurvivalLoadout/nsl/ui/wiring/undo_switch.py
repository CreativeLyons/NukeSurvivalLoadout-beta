"""Cross-Loadout undo stack switching.

Each Loadout has its own undo stack, kept for the session only. This
module refreshes the toolbar's Undo / Redo enabled state when the
active Loadout changes. The clicks themselves are wired in
:mod:`nsl.ui.wiring.events`.

Only read ``can_undo`` and ``can_redo`` here. A switch must never
change any stack.
"""

from __future__ import annotations

from typing import Optional

from nsl.constants import RESERVED_LOADOUT_STEM
from nsl.domain.undo_stack import UndoStack, UndoStackRegistry


__all__ = ["wire_undo_switch"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _registry(panel):
    """Return ``panel.registry``, or raise when none is attached."""
    reg = getattr(panel, "registry", None)
    if reg is None:
        raise AttributeError(
            "panel.registry is None - attach a Registry "
            "(build_registry_for_panel) before wiring signals."
        )
    return reg


def _stem_from_dropdown(name: str) -> str:
    """Return the Loadout stem for a dropdown row name.

    Row names are already bare stems, so this returns ``name`` as is.
    It stays as the one place to change if that ever differs.
    """
    return name


def _is_global(stem: str) -> bool:
    """Return True when ``stem`` is the reserved Global Loadout.

    An empty string counts as Global, because that is how
    :mod:`nsl.domain.loadout_ops` marks it.
    """
    return not stem or stem == RESERVED_LOADOUT_STEM


def _active_stack_for(registry, stem: str) -> Optional[UndoStack]:
    """Return the undo stack for ``stem``, creating it when needed.

    ``None`` when the panel holds a placeholder instead of a real
    :class:`UndoStackRegistry`, or when Global is active. Global has no
    file on disk, so it has no stack.
    """
    if not isinstance(registry.undo_stacks, UndoStackRegistry):
        return None
    if _is_global(stem):
        return None
    return registry.undo_stacks.for_loadout(stem)


def _refresh_toolbar(panel, stem: str) -> None:
    """Update the toolbar's Undo / Redo enabled state for ``stem``.

    ``stem`` is the now-active Loadout, or ``""`` for Global.
    """
    toolbar = getattr(panel, "top_toolbar", None)
    if toolbar is None:
        return
    registry = _registry(panel)
    stack = _active_stack_for(registry, stem)
    if stack is None:
        # Global has no stack.
        toolbar.set_undo_available(False)
        toolbar.set_redo_available(False)
        return
    toolbar.set_undo_available(stack.can_undo)
    toolbar.set_redo_available(stack.can_redo)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def wire_undo_switch(panel) -> None:
    """Connect Loadout-switch events to the toolbar Undo / Redo state.

    ``panel.registry`` must already be attached.
    """
    strip = getattr(panel, "loadout_strip", None)
    if strip is None:
        return

    def _on_loadout_selected(dropdown_name: str) -> None:
        stem = _stem_from_dropdown(dropdown_name)
        _refresh_toolbar(panel, stem)

    strip.loadout_selected.connect(_on_loadout_selected)

    # Sync once now, so the buttons match the active Loadout before any
    # switch. A missing registry is fine, the toolbar starts disabled.
    if getattr(panel, "registry", None) is None:
        return
    registry = _registry(panel)
    current = getattr(registry.state, "active", "") or ""
    _refresh_toolbar(panel, current)
