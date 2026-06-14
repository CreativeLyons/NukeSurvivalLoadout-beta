"""Bulk-operations wiring.

Five bulk actions reach this module from the grid toolbar:

* **Enable Selected** - every selected Plugin → ``enabled=True``.
* **Disable Selected** - every selected Plugin → ``enabled=False``.
* **Invert Selected** - flip each selected Plugin's ``enabled``.
* **Set GUI-only** - every selected Plugin → ``gui_only=True`` (Global
  Plugins silently skipped).
* **Clear GUI-only** - every selected Plugin → ``gui_only=False`` (Global
  Plugins silently skipped).

Key behavior:

* **One undo entry per bulk action.** All per-Plugin writes happen inside
  a single ``with stack.bulk():`` context, so the stack receives one
  combined entry regardless of how many Plugins were affected. The bulk
  context also drops the entry entirely when zero Plugins changed (e.g.
  every selected Plugin was Global for a ``gui_only`` bulk) - so
  ``Blocked`` results never inflate the undo count.
* **Full selection, not the visible-filtered subset.** Acts on the entire
  selection (``panel.selection_model.selected_keys()``), including Plugins
  currently hidden by a search filter.
* **Silent skip for Global ``gui_only``.** Setting / clearing
  ``gui_only`` on a Global Plugin is refused with no error and no
  surprise: the bulk action is not aborted, we just move on to the next
  selected key.
* **First-call-from-Global auto-creates Custom.** On the first successful
  write inside the bulk loop, the active Loadout flips to a new in-memory
  ``Custom``. We apply that result to the registry before the next
  iteration so subsequent writes operate against the new active model, and
  the undo entry lands on the new ``Custom`` stack.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from NukeSurvivalLoadout.boot.dispatcher import DispatcherState
from NukeSurvivalLoadout.boot.loadout_file import (
    FolderDecl,
    LoadoutModel,
    PluginEntry as ChainPluginEntry,
    read_loadout as read_chain_loadout,
)
from NukeSurvivalLoadout.constants import DEFAULT_CUSTOM_LOADOUT_STEM, RESERVED_LOADOUT_STEM
from NukeSurvivalLoadout.data.loadout_file import LoadoutFile, PluginEntry
from NukeSurvivalLoadout.domain import loadout_ops
from NukeSurvivalLoadout.domain.undo_stack import UndoStack, UndoStackRegistry
from NukeSurvivalLoadout.ui.filter_pipeline import bulk_target_keys

__all__ = ["wire_bulk_ops"]


# ---------------------------------------------------------------------------
# Local shim - implements a single-plugin set/persist on top of the
# `loadout_ops.save` API, sharing the chain-bridge pattern.
# ---------------------------------------------------------------------------


def _chain_loadout_init_path(loadouts_dir: Path, stem: str) -> Path:
    return Path(loadouts_dir) / stem / "init.py"


def _build_chain_from_legacy(
    loadouts_dir: Path,
    stem: str,
    legacy_model: LoadoutFile,
    user_plugin_dirs: Iterable[str],
) -> LoadoutModel:
    """Bridge a legacy LoadoutFile back to a chain LoadoutModel for save.

    Reads the existing on-disk init.py (if present) to preserve folder_var
    assignments, user_prefix, user_suffix, and trailing comments. New
    plugins land in the first declared folder var.
    """
    target = _chain_loadout_init_path(loadouts_dir, stem)
    try:
        base_model = read_chain_loadout(str(target))
    except (OSError, SyntaxError):
        folders = [
            FolderDecl(var=f"plugins_{chr(ord('A') + idx)}", path=path)
            for idx, path in enumerate(user_plugin_dirs or [])
        ]
        base_model = LoadoutModel(folders=folders)

    bucket_var = base_model.folders[0].var if base_model.folders else "plugins_A"
    on_disk_by_name = {entry.name: entry for entry in base_model.plugins}

    new_plugins: list[ChainPluginEntry] = []
    for name, entry in legacy_model.plugins.items():
        existing = on_disk_by_name.get(name)
        if existing is not None:
            new_plugins.append(
                ChainPluginEntry(
                    folder_var=existing.folder_var,
                    name=name,
                    gui=entry.gui_only,
                    disabled=not entry.enabled,
                    trailing_comment=existing.trailing_comment,
                )
            )
        else:
            new_plugins.append(
                ChainPluginEntry(
                    folder_var=bucket_var,
                    name=name,
                    gui=entry.gui_only,
                    disabled=not entry.enabled,
                )
            )

    return LoadoutModel(
        docstring=base_model.docstring,
        folders=list(base_model.folders),
        plugins=new_plugins,
        user_prefix=base_model.user_prefix,
        user_suffix=base_model.user_suffix,
        # Preserve any hand-authored text above the NSL prologue markers
        # (Issue 2). For a legacy file this is "" and the whole head still
        # rides verbatim in user_prefix above; for a re-saved file user_prefix
        # is "" and the head is regenerated from folders.
        user_prologue=base_model.user_prologue,
    )


def _set_plugin_entry(
    loadouts_dir: Path,
    plugin_name: str,
    next_entry: PluginEntry,
    state: DispatcherState,
    active_model: Optional[LoadoutFile],
    *,
    is_global_plugin: bool = False,
    previous_entry: Optional[PluginEntry] = None,
    global_model: Optional[LoadoutFile] = None,
    user_plugin_dirs: Iterable[str] = (),
) -> loadout_ops.OpResult:
    """Set ``plugin_name`` to ``next_entry`` and persist.

    When active is Global, materializes ``Custom`` from the global
    model first and flips the dispatcher pointer to it. Otherwise mutates
    the active model in place. Returns an ``OpResult`` whose ``model`` field
    is the **legacy LoadoutFile** so callers can hand it straight to
    ``registry.apply_op_result`` (which expects LoadoutFile).
    """
    if is_global_plugin and previous_entry is not None and (
        next_entry.gui_only != previous_entry.gui_only
    ):
        return loadout_ops.OpResult(
            path=None,
            model=active_model,  # type: ignore[arg-type]
            state=state,
            blocked=loadout_ops.Blocked(
                code="global_plugin",
                detail=(
                    "Global plugin gui_only cannot change via bulk"
                ),
            ),
        )

    is_global = (
        not state.active or state.active == RESERVED_LOADOUT_STEM
    )
    if is_global:
        # Materialize Custom from the global model + the new entry - in
        # MEMORY ONLY. The Custom slot doesn't land on disk until the user
        # explicitly saves. We flip the dispatcher pointer to "Custom" so
        # subsequent bulk iterations operate against the new active model.
        base = dict(global_model.plugins) if global_model is not None else {}
        base[plugin_name] = next_entry
        new_state = DispatcherState(
            panic=state.panic,
            active=DEFAULT_CUSTOM_LOADOUT_STEM,
            # Preserve the folder authority - folders live in the dispatcher
            # and must survive a bulk op that flips the active pointer.
            folders=list(state.folders),
        )
        new_legacy = LoadoutFile(
            name=DEFAULT_CUSTOM_LOADOUT_STEM, plugins=base
        )
        return loadout_ops.OpResult(
            path=None,
            model=new_legacy,  # type: ignore[arg-type]
            state=new_state,
            blocked=None,
        )

    # Named loadout - mutate. Persist to disk only if the loadout file
    # already exists on disk; if it doesn't (e.g., Custom synthesized
    # earlier in this bulk run from Global), stay in-memory per the
    # ceremonial-save contract - the file only lands on disk when the
    # user explicitly clicks Save (or Save As) on Custom.
    existing = dict(active_model.plugins) if active_model is not None else {}
    existing[plugin_name] = next_entry
    new_legacy = LoadoutFile(name=state.active, plugins=existing)

    init_path = _chain_loadout_init_path(Path(loadouts_dir), state.active)
    if not init_path.is_file():
        return loadout_ops.OpResult(
            path=None,
            model=new_legacy,  # type: ignore[arg-type]
            state=state,
            blocked=None,
        )

    chain_model = _build_chain_from_legacy(
        Path(loadouts_dir), state.active, new_legacy, user_plugin_dirs
    )
    save_result = loadout_ops.save(
        Path(loadouts_dir), state.active, chain_model, state
    )
    return loadout_ops.OpResult(
        path=save_result.path,
        model=new_legacy,  # type: ignore[arg-type]
        state=save_result.state,
        blocked=save_result.blocked,
    )


# ---------------------------------------------------------------------------
# Registry helpers (mirror NukeSurvivalLoadout.ui.wiring.events for stand-in compatibility)
# ---------------------------------------------------------------------------


def _registry(panel):
    """Return ``panel.registry``; raise a friendly error if missing.

    The wiring layer reads everything it needs through the registry - the
    shared state-shape carrier.
    """
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


def _active_stack(registry) -> Optional[UndoStack]:
    """Return the undo stack for the post-op active Loadout, or ``None``.

    Called *after* the first per-Plugin op runs (or before iteration for
    the trivial all-skip case). When the active Loadout is Global at the
    moment of the call there is no stack yet - one is created on first
    write; in that case we open the bulk context on the new stack lazily.
    """
    if not isinstance(registry.undo_stacks, UndoStackRegistry):
        return None
    if _is_global_active(registry.state):
        return None
    return registry.undo_stacks.for_loadout(registry.state.active)


def _resolve_entry(registry, plugin_name: str) -> Optional[PluginEntry]:
    """Resolve a Plugin's effective ``PluginEntry`` from active + Global.

    Mirrors :func:`NukeSurvivalLoadout.ui.wiring.events._previous_entry` so the bulk path
    and the single-pill path agree on what "previous state" means.
    """
    active = getattr(registry, "active_model", None)
    if active is not None:
        entry = active.plugins.get(plugin_name)
        if entry is not None:
            return entry
    global_model = getattr(registry, "global_model", None)
    if global_model is not None:
        return global_model.plugins.get(plugin_name)
    return None


def _global_names(registry) -> set:
    """Return the (snapshot) set of Global Plugin Names from registry."""
    names = getattr(registry, "global_plugin_names", ()) or ()
    return set(names)


# ---------------------------------------------------------------------------
# Per-action plan builders
# ---------------------------------------------------------------------------
#
# Each builder turns a (plugin_name, previous_entry) pair into the
# *desired* next PluginEntry. Returning ``None`` means "this Plugin needs
# no change" - the bulk loop skips it so the undo entry does not record a
# no-op. Splitting "what should happen" from "writing it through
# loadout_ops" keeps each action obviously correct in isolation.


def _plan_enable(previous: Optional[PluginEntry]) -> Optional[PluginEntry]:
    if previous is not None and previous.enabled is True:
        # Already enabled - bulk is idempotent; do not record a no-op.
        return None
    gui_only = previous.gui_only if previous is not None else False
    return PluginEntry(enabled=True, gui_only=gui_only)


def _plan_disable(previous: Optional[PluginEntry]) -> Optional[PluginEntry]:
    if previous is not None and previous.enabled is False:
        return None
    gui_only = previous.gui_only if previous is not None else False
    return PluginEntry(enabled=False, gui_only=gui_only)


def _plan_invert(previous: Optional[PluginEntry]) -> PluginEntry:
    # Invert always changes state by definition - record every selected
    # Plugin. A previously-unknown Plugin (no entry in either active or
    # Global) defaults to enabled=True, so invert flips it to False.
    if previous is None:
        return PluginEntry(enabled=False, gui_only=False)
    return PluginEntry(enabled=not previous.enabled, gui_only=previous.gui_only)


def _plan_set_gui_only(
    previous: Optional[PluginEntry],
) -> Optional[PluginEntry]:
    if previous is not None and previous.gui_only is True:
        return None
    enabled = previous.enabled if previous is not None else True
    return PluginEntry(enabled=enabled, gui_only=True)


def _plan_clear_gui_only(
    previous: Optional[PluginEntry],
) -> Optional[PluginEntry]:
    if previous is not None and previous.gui_only is False:
        return None
    enabled = previous.enabled if previous is not None else True
    return PluginEntry(enabled=enabled, gui_only=False)


# ---------------------------------------------------------------------------
# Bulk loop - one undo entry, silent Global gui_only skip
# ---------------------------------------------------------------------------


def _run_bulk(
    panel,
    *,
    plan_fn,
    kind: str,
    touches_gui_only: bool,
) -> None:
    """Apply ``plan_fn`` to every key in the full selection.

    All per-Plugin writes happen inside a single :meth:`UndoStack.bulk`
    context so the stack records one combined entry. The bulk context
    drops the entry when the buffer is empty (every selected Plugin was a
    no-op or a silent skip), so Blocked results never inflate the undo
    count.

    Args:
        panel: The Loadout Panel.
        plan_fn: ``Callable[[Optional[PluginEntry]], Optional[PluginEntry]]``
 - the action-specific desired-next-state builder. Returning
            ``None`` means "no change needed".
        kind: Short tag stored on the undo entry (``"bulk_enable"``,
            ``"bulk_disable"``, ``"bulk_invert"``, ``"bulk_set_gui_only"``,
            ``"bulk_clear_gui_only"``). The undo replay layer reads this to
            dispatch to the inverse op.
        touches_gui_only: When True the Global silent-skip rule
            applies on a per-Plugin basis (the domain layer enforces the
            refusal; this flag drives the ``is_global_plugin``
            argument). Enable / Disable / Invert pass ``False`` so Global
            Plugins receive the write like any other.
    """
    registry = _registry(panel)
    selection = getattr(panel, "selection_model", None)
    if selection is None:
        return
    # Pass through bulk_target_keys to document the contract at the call
    # site - bulk ops always act on the FULL selection
    # regardless of the visible-after-filter subset. The wrapper is an
    # identity pass-through today; the named call survives future refactors.
    keys = bulk_target_keys(selection.selected_keys())
    if not keys:
        return

    global_base = _global_names(registry)
    was_global_active = _is_global_active(registry.state)

    # The stack we open the bulk context on is the *active* stack at the
    # moment we want the undo entry to land. When starting from Global,
    # the active Loadout flips during the first successful write - we
    # therefore open the bulk on the post-first-write stack. To keep the
    # control flow simple, we collect plan + previous-entry tuples first,
    # then run the writes inside whichever stack ends up active.
    plan: List[tuple] = []
    for key in keys:
        previous = _resolve_entry(registry, key)
        if touches_gui_only and key in global_base:
            # Global Plugins are silently skipped for gui_only. We
            # could also let the domain layer return Blocked and ignore it;
            # doing the skip here keeps the bulk loop's intent obvious and
            # saves an unnecessary call.
            continue
        next_entry = plan_fn(previous)
        if next_entry is None:
            continue
        plan.append((key, previous, next_entry))

    if not plan:
        # Nothing to do - every selected Plugin was a no-op or a skipped
        # Global Plugin. Do not push an empty undo entry.
        return

    # Resolve the stack lazily - if we're starting from Global the first
    # set_plugin_entry call will create Custom and flip the active
    # pointer. We must open the bulk context *after* that flip so the
    # entry lands on the new Custom stack. The cleanest expression of
    # that is to do the first write outside the bulk context, then open
    # the bulk for the rest. The first write's entry still belongs to
    # the same logical bulk op, so we store it in a small buffer and
    # push it inside the bulk block.
    #
    # When we're not starting from Global, the active stack is known
    # up-front and we just wrap the whole loop in `with stack.bulk()`.

    if was_global_active:
        _run_bulk_from_global(
            panel, registry, plan=plan, kind=kind, global_base=global_base,
        )
    else:
        stack = _active_stack(registry)
        if stack is None:
            # Stand-in registries without a real UndoStackRegistry - just
            # run the writes without recording.
            _run_bulk_without_stack(
                panel, registry, plan=plan, global_base=global_base,
            )
            return
        with stack.bulk():
            _apply_plan(
                panel,
                registry,
                plan=plan,
                kind=kind,
                global_base=global_base,
                bulk_stack=stack,
            )
        # The combined entry lands on the undo branch only at the bulk
        # context's exit - every refresh inside the block ran while the
        # pushes were still buffered, so ``can_undo`` read False and the
        # Undo button stayed greyed. Re-sync now that the entry exists.
        _sync_undo_toolbar_after_bulk(panel)


def _run_bulk_from_global(
    panel,
    registry,
    *,
    plan: List[tuple],
    kind: str,
    global_base: set,
) -> None:
    """Bulk path when the active Loadout is Global at start.

    The first write auto-creates ``Custom``; we open the bulk
    context on the new Custom stack and record every write (including the
    first) as one combined entry there. The first write's OpResult is
    applied via the registry before we open the bulk context so the
    registry's ``active_model`` reflects Custom for subsequent calls.
    """
    if not plan:
        return

    # Phase 1: drive the first write outside any bulk context so the
    # active Loadout flips to Custom on disk + in registry state.
    first_key, first_prev, first_next = plan[0]
    first_result = _set_plugin_entry(
        registry.loadouts_dir,
        first_key,
        first_next,
        registry.state,
        registry.active_model,
        is_global_plugin=(first_key in global_base),
        previous_entry=first_prev,
        global_model=registry.global_model,
    )
    if first_result.is_blocked:
        # The plan builder filtered Global gui_only out already, so
        # a Blocked here means the Global rule fired against an
        # enabled toggle that the plan builder couldn't pre-filter.
        # Only gui_only-on-Global is ever blocked, so this
        # branch is theoretical; we treat it as a silent skip for
        # symmetry and move on to the next plan item.
        if len(plan) == 1:
            return
        return _run_bulk_from_global(
            panel,
            registry,
            plan=plan[1:],
            kind=kind,
            global_base=global_base,
        )

    # Apply the first result so the registry now points at Custom.
    registry.apply_op_result(first_result)

    # Phase 2: open the bulk context on the new Custom stack and replay
    # every plan entry (including the first) as buffered pushes.
    stack = _active_stack(registry)
    if stack is None:
        # Stand-in registry without a real UndoStackRegistry - finish the
        # writes without recording undo entries.
        _run_bulk_without_stack(
            panel,
            registry,
            plan=plan[1:],
            global_base=global_base,
        )
        return

    with stack.bulk():
        # Record the first write's payload inside the bulk so the combined
        # entry covers all N writes.
        stack.push(
            {
                "kind": kind,
                "plugin": first_key,
                "previous": first_prev,
                "next": first_next,
                "auto_created_custom": True,
            }
        )
        _apply_plan(
            panel,
            registry,
            plan=plan[1:],
            kind=kind,
            global_base=global_base,
            bulk_stack=stack,
            auto_created_custom=True,
        )
    # Sync after the bulk context exits - see the note in ``_run_bulk``:
    # the combined entry isn't on the undo branch until here.
    _sync_undo_toolbar_after_bulk(panel)


def _sync_undo_toolbar_after_bulk(panel) -> None:
    """Refresh the Undo / Redo button availability post-bulk.

    Thin import-and-call wrapper around the events-layer helper, kept
    here so the bulk module doesn't import events at load time (events
    imports nothing from bulk_ops, but the lazy import keeps the
    dependency one-directional and obvious).
    """
    from NukeSurvivalLoadout.ui.wiring.events import _sync_undo_toolbar
    _sync_undo_toolbar(panel)


def _apply_plan(
    panel,
    registry,
    *,
    plan: List[tuple],
    kind: str,
    global_base: set,
    bulk_stack: UndoStack,
    auto_created_custom: bool = False,
) -> None:
    """Walk the plan, write each entry, push undo records into the bulk."""
    for key, previous, next_entry in plan:
        result = _set_plugin_entry(
            registry.loadouts_dir,
            key,
            next_entry,
            registry.state,
            registry.active_model,
            is_global_plugin=(key in global_base),
            previous_entry=previous,
            global_model=registry.global_model,
        )
        if result.is_blocked:
            # Silent skip by design. We deliberately do NOT invoke
            # registry.on_blocked here - bulk gui_only on Global
            # is a "no error, no surprise" case. Move on without
            # disturbing the bulk count.
            continue
        # Push the per-Plugin payload into the bulk buffer; the
        # UndoStack coalesces them into one entry on context exit.
        bulk_stack.push(
            {
                "kind": kind,
                "plugin": key,
                "previous": previous,
                "next": next_entry,
                "auto_created_custom": auto_created_custom,
            }
        )
        registry.apply_op_result(result)


def _run_bulk_without_stack(
    panel,
    registry,
    *,
    plan: List[tuple],
    global_base: set,
) -> None:
    """Apply the plan against a registry that has no real UndoStackRegistry.

    This keeps stand-in panels usable without instantiating the full undo
    registry - the writes still go through the domain layer and the
    registry's state still updates, just without undo recording.
    """
    for key, previous, next_entry in plan:
        result = _set_plugin_entry(
            registry.loadouts_dir,
            key,
            next_entry,
            registry.state,
            registry.active_model,
            is_global_plugin=(key in global_base),
            previous_entry=previous,
            global_model=registry.global_model,
        )
        if result.is_blocked:
            continue
        registry.apply_op_result(result)


# ---------------------------------------------------------------------------
# Public entry point - orchestrator stitches this into panel._wire_signals
# ---------------------------------------------------------------------------


def wire_bulk_ops(panel) -> None:
    """Connect ``panel.grid_toolbar`` bulk signals to the bulk handlers.

    Called from ``LoadoutPanel._wire_signals``.

    Idempotent - calling twice does not double-connect; the second call
    is a no-op. The flag lives on the panel instance so a panel rebuild
    starts fresh.
    """
    if getattr(panel, "_bulk_ops_wired", False):
        return

    toolbar = getattr(panel, "grid_toolbar", None)
    if toolbar is None:
        # No toolbar to wire - be tolerant so stub panels work in tests.
        panel._bulk_ops_wired = True
        return

    # Each handler closes over ``panel`` and forwards to ``_run_bulk``
    # with the appropriate plan builder. Using small lambdas keeps the
    # signal connections obvious at a glance.
    if hasattr(toolbar, "bulk_enable_requested"):
        toolbar.bulk_enable_requested.connect(
            lambda: _run_bulk(
                panel,
                plan_fn=_plan_enable,
                kind="bulk_enable",
                touches_gui_only=False,
            )
        )
    if hasattr(toolbar, "bulk_disable_requested"):
        toolbar.bulk_disable_requested.connect(
            lambda: _run_bulk(
                panel,
                plan_fn=_plan_disable,
                kind="bulk_disable",
                touches_gui_only=False,
            )
        )
    if hasattr(toolbar, "bulk_invert_requested"):
        toolbar.bulk_invert_requested.connect(
            lambda: _run_bulk(
                panel,
                plan_fn=_plan_invert,
                kind="bulk_invert",
                touches_gui_only=False,
            )
        )
    if hasattr(toolbar, "bulk_set_gui_only_requested"):
        toolbar.bulk_set_gui_only_requested.connect(
            lambda: _run_bulk(
                panel,
                plan_fn=_plan_set_gui_only,
                kind="bulk_set_gui_only",
                touches_gui_only=True,
            )
        )
    if hasattr(toolbar, "bulk_clear_gui_only_requested"):
        toolbar.bulk_clear_gui_only_requested.connect(
            lambda: _run_bulk(
                panel,
                plan_fn=_plan_clear_gui_only,
                kind="bulk_clear_gui_only",
                touches_gui_only=True,
            )
        )

    panel._bulk_ops_wired = True
