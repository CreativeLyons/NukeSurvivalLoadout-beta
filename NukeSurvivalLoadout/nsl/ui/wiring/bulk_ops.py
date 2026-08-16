"""Bulk-operations wiring.

Six bulk actions come from the grid toolbar: Enable, Disable and Invert
Selected, plus Set, Clear and Toggle GUI-only. Only Toggle is shown.

* One undo entry per action. Every write runs inside one
  ``with stack.bulk():``, and the entry is dropped when nothing changed.
* The full selection is used, including Plugins a search filter hides.
* ``gui_only`` on a Global Plugin is skipped with no message.
* The first write from Global creates an in-memory ``Custom``. That
  result is applied before the next write, so the later writes and the
  undo entry land on ``Custom``.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from nsl.boot.dispatcher import DispatcherState
from nsl.boot.loadout_file import (
    FolderDecl,
    LoadoutModel,
    PluginEntry as ChainPluginEntry,
    read_loadout as read_chain_loadout,
)
from nsl.constants import (
    DEFAULT_CUSTOM_LOADOUT_STEM,
    GLOBAL_PLUGINS_VAR_NAME,
    GLOBAL_SOURCE_MARKER,
    RESERVED_LOADOUT_STEM,
)
from nsl.data.loadout_file import LoadoutFile, PluginEntry
from nsl.domain import folder_ops, loadout_ops
from nsl.domain.undo_stack import UndoStack, UndoStackRegistry
from nsl.ui.filter_pipeline import bulk_target_keys

__all__ = ["wire_bulk_ops"]


# ---------------------------------------------------------------------------
# Single-plugin in-memory set, plus the legacy-to-chain bridge for Save
# ---------------------------------------------------------------------------


def _chain_loadout_init_path(loadouts_dir: Path, stem: str) -> Path:
    return Path(loadouts_dir) / stem / "init.py"


class _UnroutablePlugin(Exception):
    """A plugin's source folder maps to no configured folder var.

    Writing the directive against a guessed path would miss at boot, and
    the plugin would then load default-on. The caller returns a
    ``Blocked`` instead.
    """

    def __init__(self, plugin_name: str) -> None:
        super().__init__(plugin_name)
        self.plugin_name = plugin_name


def _build_routing_map(registry) -> dict:
    """Map each discovered plugin Name to the folder var it belongs in.

    User folders get ``plugins_A`` and ``plugins_B`` in configured order.
    Global-source plugins get ``GLOBAL_PLUGINS_VAR_NAME``. A plugin with
    no matching folder is absent, which the caller reads as unroutable.
    """
    user_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    var_for_path = {
        path: folder_ops.canonical_folder_var(idx)
        for idx, path in enumerate(user_dirs)
    }
    discovered = getattr(registry, "discovered_plugins", {}) or {}
    routing: dict[str, str] = {}
    for name, plugin in discovered.items():
        source = getattr(plugin, "source", None)
        if source == GLOBAL_SOURCE_MARKER:
            routing[name] = GLOBAL_PLUGINS_VAR_NAME
            continue
        var = var_for_path.get(source)
        if var is not None:
            routing[name] = var
    # Catch Global plugins the scan did not tag, for example when the
    # folder was not walked again this session.
    for name in (getattr(registry, "global_plugin_names", ()) or ()):
        routing.setdefault(name, GLOBAL_PLUGINS_VAR_NAME)
    return routing


def _build_chain_from_legacy(
    loadouts_dir: Path,
    stem: str,
    legacy_model: LoadoutFile,
    registry,
) -> LoadoutModel:
    """Bridge a legacy LoadoutFile back to a chain LoadoutModel for save.

    Reads the on-disk init.py to keep folder_var assignments, prefix,
    suffix, and trailing comments. A brand-new exception is routed by the
    plugin's ``source``, and an unroutable one raises
    :class:`_UnroutablePlugin`.
    """
    user_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    target = _chain_loadout_init_path(loadouts_dir, stem)
    try:
        base_model = read_chain_loadout(str(target))
    except (OSError, SyntaxError):
        folders = [
            FolderDecl(var=folder_ops.canonical_folder_var(idx), path=path)
            for idx, path in enumerate(user_dirs)
        ]
        base_model = LoadoutModel(folders=folders)

    routing = _build_routing_map(registry)
    on_disk_by_name = {entry.name: entry for entry in base_model.plugins}

    # The on-disk head may not declare the Global var yet. Add a
    # FolderDecl when a Global exception routes there.
    folders = list(base_model.folders)
    have_global_decl = any(
        decl.var == GLOBAL_PLUGINS_VAR_NAME for decl in folders
    )

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
            continue
        folder_var = routing.get(name)
        if folder_var is None:
            raise _UnroutablePlugin(name)
        if folder_var == GLOBAL_PLUGINS_VAR_NAME and not have_global_decl:
            global_dirs = list(getattr(registry, "global_plugin_dirs", []) or [])
            if global_dirs:
                folders.append(
                    FolderDecl(
                        var=GLOBAL_PLUGINS_VAR_NAME, path=str(global_dirs[0])
                    )
                )
                have_global_decl = True
        new_plugins.append(
            ChainPluginEntry(
                folder_var=folder_var,
                name=name,
                gui=entry.gui_only,
                disabled=not entry.enabled,
            )
        )

    # A new folder decl is not declared by the kept ``user_prefix``, so
    # boot would raise NameError. Reset it and let ``render`` rebuild the
    # head. Files with no new decl keep their prefix as it is.
    user_prefix = (
        "" if len(folders) != len(base_model.folders) else base_model.user_prefix
    )

    return LoadoutModel(
        docstring=base_model.docstring,
        folders=folders,
        plugins=new_plugins,
        user_prefix=user_prefix,
        user_suffix=base_model.user_suffix,
        # Keeps hand-written text above the prologue markers. Empty for
        # a legacy file, where the head rides in ``user_prefix``.
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
    registry=None,
) -> loadout_ops.OpResult:
    """Set ``plugin_name`` to ``next_entry`` in memory, with no disk write.

    When Global is active, materializes ``Custom`` from the Global model
    and points the dispatcher at it. Otherwise the active model changes
    in place. ``OpResult.model`` is a ``LoadoutFile``, ready to hand to
    ``registry.apply_op_result``.
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
        base = dict(global_model.plugins) if global_model is not None else {}
        base[plugin_name] = next_entry
        new_state = DispatcherState(
            panic=state.panic,
            active=DEFAULT_CUSTOM_LOADOUT_STEM,
            # Folders live in the dispatcher and must survive the flip.
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

    # In memory only, like the single-pill path. ``path`` must stay None.
    # A real path tells ``apply_op_result`` the file was saved, so the
    # Save button never unlocks and undo desyncs from disk.
    existing = dict(active_model.plugins) if active_model is not None else {}
    existing[plugin_name] = next_entry
    new_legacy = LoadoutFile(name=state.active, plugins=existing)

    return loadout_ops.OpResult(
        path=None,
        model=new_legacy,  # type: ignore[arg-type]
        state=state,
        blocked=None,
    )


# ---------------------------------------------------------------------------
# Block handling
# ---------------------------------------------------------------------------


def _handle_bulk_block(registry, result: loadout_ops.OpResult) -> None:
    """Triage a Blocked result raised inside the bulk write loop.

    ``global_plugin`` is a silent skip and is never surfaced. The plan
    builder already filters most of these out. ``source_not_found`` goes
    to ``registry.on_blocked`` when that exists.
    """
    blocked = result.blocked
    if blocked is None:
        return
    if blocked.code == loadout_ops.BlockedReason.SOURCE_NOT_FOUND:
        log = getattr(registry, "on_blocked", None)
        if log is not None:
            log(blocked)


# ---------------------------------------------------------------------------
# Registry helpers
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


def _is_global_active(state: DispatcherState) -> bool:
    """True when no user loadout is the dispatcher's active pointer."""
    return (
        not state.active
        or state.active == RESERVED_LOADOUT_STEM
    )


def _active_stack(registry) -> Optional[UndoStack]:
    """Return the undo stack for the active Loadout, or ``None``.

    ``None`` while Global is active, because the stack is created on the
    first write. The caller then opens the bulk on the new stack.
    """
    if not isinstance(registry.undo_stacks, UndoStackRegistry):
        return None
    if _is_global_active(registry.state):
        return None
    return registry.undo_stacks.for_loadout(registry.state.active)


def _resolve_entry(registry, plugin_name: str) -> Optional[PluginEntry]:
    """Resolve a Plugin's effective ``PluginEntry`` from active + Global.

    Matches ``events._previous_entry``, so the bulk path and the
    single-pill path agree on the previous state.
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
# Per-action plan builders. ``None`` means no change, so the bulk loop
# skips that Plugin and the undo entry records no no-ops.
# ---------------------------------------------------------------------------


def _plan_enable(previous: Optional[PluginEntry]) -> Optional[PluginEntry]:
    if previous is not None and previous.enabled is True:
        return None
    gui_only = previous.gui_only if previous is not None else False
    return PluginEntry(enabled=True, gui_only=gui_only)


def _plan_disable(previous: Optional[PluginEntry]) -> Optional[PluginEntry]:
    if previous is not None and previous.enabled is False:
        return None
    gui_only = previous.gui_only if previous is not None else False
    return PluginEntry(enabled=False, gui_only=gui_only)


def _plan_invert(previous: Optional[PluginEntry]) -> PluginEntry:
    # An unknown Plugin counts as enabled, so invert makes it disabled.
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
# Toggle GUI-only - survey the selection, then reuse set / clear
# ---------------------------------------------------------------------------


def _eligible_gui_only_keys(panel, registry) -> List[str]:
    """Selected keys a ``gui_only`` bulk can actually change.

    The full selection minus Global Plugins. The survey must use the
    same exclusion as the write path. Otherwise untouchable Globals
    would decide the direction for the Plugins that do move.
    """
    selection = getattr(panel, "selection_model", None)
    if selection is None:
        return []
    keys = bulk_target_keys(selection.selected_keys())
    if not keys:
        return []
    global_base = _global_names(registry)
    return [key for key in keys if key not in global_base]


def _all_gui_only(registry, keys: List[str]) -> bool:
    """``True`` when every key in *keys* already has ``gui_only`` set.

    A Plugin with no entry counts as off. Empty *keys* returns ``True``,
    so callers must handle that case first.
    """
    for key in keys:
        previous = _resolve_entry(registry, key)
        if previous is None or not previous.gui_only:
            return False
    return True


def _run_toggle_gui_only(panel) -> None:
    """Sync ``gui_only`` across the selection in one click.

    All on turns them off. Anything else turns them all on. So one click
    syncs a ragged selection and a second click clears it.
    """
    registry = _registry(panel)
    eligible = _eligible_gui_only_keys(panel, registry)
    if not eligible:
        return

    turn_on = not _all_gui_only(registry, eligible)
    _run_bulk(
        panel,
        plan_fn=_plan_set_gui_only if turn_on else _plan_clear_gui_only,
        kind="bulk_set_gui_only" if turn_on else "bulk_clear_gui_only",
        touches_gui_only=True,
    )


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

    Every write runs inside one :meth:`UndoStack.bulk` context, so the
    stack records one entry. The entry is dropped when nothing changed.

    Args:
        panel: The Loadout Panel.
        plan_fn: Builds the next entry, or ``None`` for no change.
        kind: Tag stored on the undo entry, read by the replay layer.
        touches_gui_only: True applies the silent Global skip per
            Plugin. Enable, Disable and Invert pass ``False``, so Global
            Plugins get the write like any other.
    """
    registry = _registry(panel)
    selection = getattr(panel, "selection_model", None)
    if selection is None:
        return
    # ``bulk_target_keys`` is an identity today. The named call records
    # that bulk acts on the full selection, not the filtered subset.
    keys = bulk_target_keys(selection.selected_keys())
    if not keys:
        return

    global_base = _global_names(registry)
    was_global_active = _is_global_active(registry.state)

    # Collect the plan first. Starting from Global, the active Loadout
    # flips on the first write, so the stack is only known after it.
    plan: List[tuple] = []
    for key in keys:
        previous = _resolve_entry(registry, key)
        if touches_gui_only and key in global_base:
            # Global Plugins are skipped for gui_only, with no message.
            continue
        next_entry = plan_fn(previous)
        if next_entry is None:
            continue
        plan.append((key, previous, next_entry))

    if not plan:
        # No empty undo entry.
        return

    if was_global_active:
        _run_bulk_from_global(
            panel, registry, plan=plan, kind=kind, global_base=global_base,
        )
    else:
        stack = _active_stack(registry)
        if stack is None:
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
        # The entry only lands when the bulk context exits. Refreshes
        # inside the block read ``can_undo`` as False, so re-sync here.
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

    The first write creates ``Custom``. Its OpResult is applied before
    the bulk context opens, so the registry points at ``Custom`` and the
    combined entry lands on the new stack.
    """
    if not plan:
        return

    # Phase 1. The first write runs outside any bulk context, so the
    # active Loadout flips to Custom in registry state.
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
        registry=registry,
    )
    if first_result.is_blocked:
        _handle_bulk_block(registry, first_result)
        if len(plan) == 1:
            return
        return _run_bulk_from_global(
            panel,
            registry,
            plan=plan[1:],
            kind=kind,
            global_base=global_base,
        )

    registry.apply_op_result(first_result)

    # Phase 2. Open the bulk on the new Custom stack.
    stack = _active_stack(registry)
    if stack is None:
        _run_bulk_without_stack(
            panel,
            registry,
            plan=plan[1:],
            global_base=global_base,
        )
        return

    with stack.bulk():
        # The first write's payload joins the same combined entry.
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
    # The entry only exists after the bulk context exits.
    _sync_undo_toolbar_after_bulk(panel)


def _sync_undo_toolbar_after_bulk(panel) -> None:
    """Refresh the Undo / Redo button state after a bulk.

    The import is lazy so ``bulk_ops`` does not import ``events`` at
    load time.
    """
    from nsl.ui.wiring.events import _sync_undo_toolbar
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
            registry=registry,
        )
        if result.is_blocked:
            _handle_bulk_block(registry, result)
            continue
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

    The writes and the registry state still happen. Only the undo
    recording is skipped.
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
            registry=registry,
        )
        if result.is_blocked:
            _handle_bulk_block(registry, result)
            continue
        registry.apply_op_result(result)


# ---------------------------------------------------------------------------
# Public entry point - orchestrator stitches this into panel._wire_signals
# ---------------------------------------------------------------------------


def wire_bulk_ops(panel) -> None:
    """Connect ``panel.grid_toolbar`` bulk signals to the bulk handlers.

    Idempotent. The flag lives on the panel, so a rebuild starts fresh.
    """
    if getattr(panel, "_bulk_ops_wired", False):
        return

    toolbar = getattr(panel, "grid_toolbar", None)
    if toolbar is None:
        panel._bulk_ops_wired = True
        return

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
    if hasattr(toolbar, "bulk_toggle_gui_only_requested"):
        toolbar.bulk_toggle_gui_only_requested.connect(
            lambda: _run_toggle_gui_only(panel)
        )

    panel._bulk_ops_wired = True
