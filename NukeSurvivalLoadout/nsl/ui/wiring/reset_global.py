"""Reset Global Plugins to Default - bulk toolbar action wiring.

Connects the Reset Global button on the Search/Tags strip to its
handler. Only Global Plugin names are cleared, so a user Plugin with
the same name survives. The reset stays in the active Loadout's
in-memory model until Save, and it pushes one undo step.
"""

from __future__ import annotations

from nsl.boot.dispatcher import DispatcherState
from nsl.constants import RESERVED_LOADOUT_STEM
from nsl.data.loadout_file import LoadoutFile
from nsl.domain import loadout_ops
from nsl.domain.panic import reset_global_to_default
from nsl.domain.undo_stack import UndoStackRegistry
from nsl.ui import dialogs

__all__ = ["wire_reset_global"]


def _registry(panel):
    """Return ``panel.registry``, or raise when none is attached."""
    reg = getattr(panel, "registry", None)
    if reg is None:
        raise AttributeError(
            "panel.registry is None - attach a Registry "
            "(build_registry_for_panel) before wiring signals."
        )
    return reg


def _is_global_active(state: DispatcherState) -> bool:
    """True when no user loadout is the dispatcher's active pointer."""
    return (
        not state.active
        or state.active == RESERVED_LOADOUT_STEM
    )


def _handle_reset_global(panel) -> None:
    """Confirm + run the bulk Reset Global Plugins to Default action."""
    registry = _registry(panel)

    # The button is hidden when no Global layer is active, so this guard
    # is defence only. Stay silent instead of raising.
    if registry.global_model is None or not registry.global_model.plugins:
        return

    # Global has nothing to reset itself against.
    if _is_global_active(registry.state):
        return

    active = registry.active_model
    if active is None:
        return

    global_names = registry.global_plugin_names
    # Only entries whose value differs from Global count as diverged. A
    # copy that matches Global does not, so it is left alone.
    diverged_count = registry.count_diverged_global_plugins()

    if diverged_count == 0:
        return

    loadout_name = active.name or registry.state.active
    accepted = dialogs.confirm_reset_global_to_default(
        panel, diverged_count, loadout_name,
    )
    if not accepted:
        return

    previous_model = LoadoutFile(
        name=active.name, plugins=dict(active.plugins)
    )

    new_active = reset_global_to_default(
        active,
        scope="all",
        global_plugin_names=global_names,
    )

    # ``path`` stays None so apply_op_result knows nothing was written.
    # The reset lives in memory until the user saves.
    result = loadout_ops.OpResult(
        path=None,
        model=new_active,  # type: ignore[arg-type]
        state=registry.state,
    )
    registry.apply_op_result(result)

    # Whole-model snapshots, not a delta. The reset removes a variable
    # set of names, so a delta would have to list them all.
    if isinstance(registry.undo_stacks, UndoStackRegistry):
        stem = registry.state.active if registry.state else ""
        registry.undo_stacks.for_loadout(stem).push(
            {
                "kind": "model_reset",
                "previous": previous_model,
                "next": LoadoutFile(
                    name=new_active.name, plugins=dict(new_active.plugins)
                ),
            }
        )
        from nsl.ui.wiring.events import _sync_undo_toolbar
        _sync_undo_toolbar(panel)


def wire_reset_global(panel) -> None:
    """Connect ``panel.search_tags.reset_global_requested`` to the handler.

    Idempotent. The flag lives on the panel, so a rebuild starts fresh.
    """
    if getattr(panel, "_reset_global_wired", False):
        return

    strip = getattr(panel, "search_tags", None)
    if strip is None or not hasattr(strip, "reset_global_requested"):
        panel._reset_global_wired = True
        return

    strip.reset_global_requested.connect(
        lambda: _handle_reset_global(panel)
    )
    panel._reset_global_wired = True
