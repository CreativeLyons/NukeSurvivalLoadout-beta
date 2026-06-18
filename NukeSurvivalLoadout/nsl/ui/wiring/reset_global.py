"""Reset Global Plugins to Default - bulk toolbar action wiring.

Wires the bulk Reset Global Plugins to Default action (the toolbar button
on the Search/Tags strip) to its handler via :func:`wire_reset_global`.

Behaviour:

* **Scoped strictly to Global Plugins.** The resolved
  ``global_plugin_names`` are passed into the domain's
  ``reset_global_to_default`` call so user-added Plugins are never
  touched even if they happen to share a name.
* **The Global Loadout is read-only.** The handler short-circuits before the
  dialog when the active Loadout is Global, because resetting Global
  against itself has no meaning.
* **In-memory mutation only.** The reset stays in the active Loadout's
  in-memory model until the user saves; the banner picks up the
  resulting diff against the boot-time snapshot.
* **Confirmation dialog before mutation.** The action is bulk and can
  affect many Plugins, so the dialog earns the user's explicit intent.
* **Undoable.** One reset = one undo step on the active Loadout's
  stack. The entry carries a before/after model snapshot; undo restores
  the pre-reset model, redo re-applies. In-memory only, like the reset
  itself - neither touches disk until Save.
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
    """Return ``panel.registry``; raise a friendly error if missing."""
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

    # Defensive: the button is hidden when no Global layer is active, so
    # this branch is theoretical. Treat it as a silent no-op rather than
    # surfacing an error.
    if registry.global_model is None or not registry.global_model.plugins:
        return

    # If the active "Loadout" is Global itself, there is nothing to
    # reset against. The per-Plugin path (when it lands) will surface
    # the same constraint via a disabled right-click item; for the bulk
    # toolbar action a silent no-op is the cleanest behaviour.
    if _is_global_active(registry.state):
        return

    active = registry.active_model
    if active is None:
        return

    global_names = registry.global_plugin_names
    # Count only entries whose VALUE differs from the resolved Global
    # entry - keys present in the active Loadout that happen to match
    # Global (e.g. Custom's auto-create mirror) are NOT diverged. When
    # nothing has diverged there is nothing to revert, so the button is
    # disabled in this state via ``search_tags.set_reset_global_enabled``;
    # the guard below is defence-in-depth for programmatic / shortcut
    # invocations.
    diverged_count = registry.count_diverged_global_plugins()

    if diverged_count == 0:
        return

    loadout_name = active.name or registry.state.active
    accepted = dialogs.confirm_reset_global_to_default(
        panel, diverged_count, loadout_name,
    )
    if not accepted:
        return

    # Snapshot the pre-reset model for undo before mutating.
    previous_model = LoadoutFile(
        name=active.name, plugins=dict(active.plugins)
    )

    # Pure-domain mutation: removes every Global entry from the
    # active Loadout's plugins dict so resolution falls back to Global
    # for those names. User-added entries are preserved by the domain
    # function's filter (``name not in global_names``).
    new_active = reset_global_to_default(
        active,
        scope="all",
        global_plugin_names=global_names,
    )

    # Build an OpResult so the registry's refresh pipeline picks up the
    # new active_model on the same path bulk_ops uses. ``path=None``
    # because the reset stays in memory until the user saves - the
    # banner's pending-diff against ``boot_active`` will surface the
    # change, and Save commits it to disk through the existing path.
    result = loadout_ops.OpResult(
        path=None,
        model=new_active,  # type: ignore[arg-type]
        state=registry.state,
    )
    registry.apply_op_result(result)

    # One reset = one undo step on the active Loadout's stack. The
    # before/after snapshots let replay swap the whole model wholesale
    # (the reset removes a variable set of Global entries; a delta would
    # have to enumerate them).
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

    The Reset Global button lives on the Search/Tags strip's controls
    row (right-aligned, beside Select-filtered / Deselect-filtered /
    Clear-selection). The wiring layer hooks the strip's signal - the
    grid toolbar carries no Reset-Global affordance.

    Idempotent - calling twice does not double-connect; the second call
    is a no-op. The flag lives on the panel instance so a panel rebuild
    starts fresh.

    No-op when the strip lacks the signal (stub widgets in unit tests)
    or when the panel carries no search_tags at all.
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
