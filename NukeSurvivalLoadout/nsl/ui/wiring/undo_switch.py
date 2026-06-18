"""Cross-Loadout undo stack switching.

NSL maintains a separate undo history per Loadout. Switching the Active
Loadout preserves each Loadout's undo stack, and undo history is
per-session only (all stacks clear when Nuke closes).

Single public helper: :func:`wire_undo_switch`. This module keeps the
top-toolbar's Undo / Redo buttons bound to the **active** Loadout's
:class:`nsl.domain.undo_stack.UndoStack`. The actual undo / redo
*invocations* are routed by :mod:`nsl.ui.wiring.events`, which
resolves the active stack dynamically from
``registry.state.active`` at signal time. Because that
resolution is dynamic, the runtime "swap" of which stack the buttons act
on happens automatically when the active Loadout changes.

What this module adds is the toolbar refresh: whenever
``panel.loadout_strip.loadout_selected(str)`` fires, it recomputes the
Undo / Redo button *availability* (enabled state) from the now-active
stack's ``can_undo`` / ``can_redo``.

Peer-stack read-only invariant: switching from A to B must not pop, push,
or rewind any stack other than the now-active one. This module upholds
that by only reading ``can_undo`` / ``can_redo`` (pure boolean reads) and
never calling ``undo`` / ``redo`` / ``push`` / ``clear``. The registry
owns all stacks; we never reach into stack internals.
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
    """Return ``panel.registry``; raise a friendly error when absent.

    Mirrors the helper in :mod:`nsl.ui.wiring.events` so a misconfigured
    panel surfaces a clear AttributeError rather than a cryptic slot
    failure.
    """
    reg = getattr(panel, "registry", None)
    if reg is None:
        raise AttributeError(
            "panel.registry is None - attach a Registry "
            "(build_registry_for_panel) before wiring signals."
        )
    return reg


def _stem_from_dropdown(name: str) -> str:
    """Dropdown row name → bare stem (already bare; kept as the seam).

    The loadout strip's :attr:`loadout_selected` signal carries a
    display name; row names are bare stems,
    others use the bare stem. This helper accepts both shapes so the
    wiring layer never has to second-guess the format.
    """
    return name


def _is_global(stem: str) -> bool:
    """Return True when ``stem`` refers to the reserved Global Loadout.

    Empty string is treated as Global because :mod:`nsl.domain.loadout_ops`
    encodes "Global active" as ``registry.state.active == ""`` or
    ``RESERVED_LOADOUT_STEM``.
    """
    return not stem or stem == RESERVED_LOADOUT_STEM


def _active_stack_for(registry, stem: str) -> Optional[UndoStack]:
    """Look up the per-Loadout stack for ``stem`` without mutating peers.

    Returns ``None`` when:
        * The registry is not a real :class:`UndoStackRegistry`
          (a bare placeholder may be present instead).
        * The active Loadout is Global (Global has no on-disk identity
          and therefore no persistent stack; the auto-create Custom
          flow flips this off as soon as the user toggles a Plugin).

    Otherwise lazily creates / returns the stack for ``stem``.
    Property reads (``can_undo`` / ``can_redo``) on a stack do not
    mutate state, so peer stacks remain read-only across switches.
    """
    if not isinstance(registry.undo_stacks, UndoStackRegistry):
        return None
    if _is_global(stem):
        return None
    return registry.undo_stacks.for_loadout(stem)


def _refresh_toolbar(panel, stem: str) -> None:
    """Update the top-toolbar's Undo / Redo button enabled state.

    Switching the Active Loadout preserves each Loadout's undo stack, so
    the buttons must reflect the *now-active* stack's history immediately
    on switch.

    Args:
        panel: The Loadout Panel.
        stem: Stem of the now-active Loadout (or ``""`` /
            ``RESERVED_LOADOUT_STEM`` for Global).
    """
    toolbar = getattr(panel, "top_toolbar", None)
    if toolbar is None:
        return
    registry = _registry(panel)
    stack = _active_stack_for(registry, stem)
    if stack is None:
        # Global active -- nothing to undo / redo. Disable both buttons.
        toolbar.set_undo_available(False)
        toolbar.set_redo_available(False)
        return
    toolbar.set_undo_available(stack.can_undo)
    toolbar.set_redo_available(stack.can_redo)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def wire_undo_switch(panel) -> None:
    """Connect Loadout-switch events to top-toolbar Undo / Redo rebinding.

    The wiring is intentionally narrow:

    * ``panel.loadout_strip.loadout_selected(str)`` -> refresh toolbar
      availability for the new active stack.
    * No mutation of peer stacks. We only read ``can_undo`` /
      ``can_redo`` to drive button enabled state.
    * The actual undo / redo *invocations* run through the
      ``_on_undo`` / ``_on_redo`` slots in the events wiring layer,
      which resolve the active stack dynamically from
      ``registry.state.active``. We do not duplicate
      that wiring.

    Args:
        panel: The Loadout Panel; must already have ``panel.registry``
            attached by the orchestrator.
    """
    strip = getattr(panel, "loadout_strip", None)
    if strip is None:
        return

    def _on_loadout_selected(dropdown_name: str) -> None:
        stem = _stem_from_dropdown(dropdown_name)
        _refresh_toolbar(panel, stem)

    strip.loadout_selected.connect(_on_loadout_selected)

    # Initial sync: bind toolbar availability to whatever Loadout is
    # currently active per ``registry.state.active``. This handles the first paint of the
    # panel as well as a re-wire after a programmatic state restore.
    # Tolerate a missing registry: the toolbar starts disabled by
    # default and re-syncs on the first switch.
    if getattr(panel, "registry", None) is None:
        return
    registry = _registry(panel)
    current = getattr(registry.state, "active", "") or ""
    _refresh_toolbar(panel, current)
