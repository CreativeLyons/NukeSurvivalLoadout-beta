"""Event wiring - connects UI widget signals to domain ops.

:func:`wire_events` runs once after the panel is built. It reads all
panel state from ``panel.registry``.

* Pill toggles and folder edits change the active LoadoutFile in memory
  only. Nothing reaches disk until Save or Save As, and a toggle marks
  the Loadout dirty.
* One single-pill toggle pushes one undo entry.
* Plugins Folders live in the dispatcher, not in a Loadout. A folder
  add, remove or reorder always writes the dispatcher and syncs every
  Loadout, even while the in-memory Custom slot is active.
* Custom never saves as a Loadout. Save on Custom redirects to Save As.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from nsl.ui import dialogs
from nsl.constants import (
    DEFAULT_CUSTOM_LOADOUT_STEM,
    GLOBAL_FOLDER_NAME,
    GLOBAL_LOADOUT_DIR_NAME,
    GLOBAL_PLUGINS_VAR_NAME,
    RESERVED_LOADOUT_STEM,
    install_root,
)
from nsl.boot.dispatcher import DispatcherState
from nsl.boot.loadout_file import (
    FolderDecl,
    LoadoutModel,
    PluginEntry as ChainPluginEntry,
    read_loadout as read_chain_loadout,
    write_loadout as write_chain_loadout,
)
from nsl.data.loadout_file import LoadoutFile, PluginEntry
from nsl.domain import folder_ops, loadout_ops
from nsl.domain.undo_stack import UndoStack, UndoStackRegistry
from nsl.paths import canon_for_compare


__all__ = ["wire_events"]


# ---------------------------------------------------------------------------
# Helpers - registry access
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
    """True when no user loadout is the dispatcher's active pointer.

    An empty pointer or the reserved ``Global`` stem both mean Global.
    """
    return (
        not state.active
        or state.active == RESERVED_LOADOUT_STEM
    )


def _chain_loadout_path(registry, stem: str) -> Path:
    """Return the chain-architecture init.py path for a loadout stem."""
    return Path(registry.loadouts_dir) / stem / "init.py"


def _user_folder_entries(
    folders: list[FolderDecl],
    active_plugins: dict,
    discovered: dict,
    base_entries: list[ChainPluginEntry],
    base_folder_paths: dict[str, str],
) -> list[ChainPluginEntry]:
    """Build the user-folder exception lines for the managed block.

    The union of two sources, limited to still-configured folders:

    1. Live scan. Every discovered plugin whose decision differs from
       the default. A default-on plugin gets no line, because
       ``nsl_load_folder`` scan-loads it at boot.
    2. Carried deviations. On-disk lines for a plugin the scan did not
       see, such as one on an unmounted share. Without this the decision
       is lost and the plugin reloads default-on.

    Folders are matched by canon path, not by var name, so a reorder
    still maps each line correctly.
    """
    path_to_var: dict[str, str] = {
        canon_for_compare(decl.path): decl.var for decl in folders
    }
    configured_canon = set(path_to_var.keys())

    new_entries: list[ChainPluginEntry] = []
    emitted_per_var: dict[str, set] = {}

    # 1. Live scan.
    for decl in folders:
        names = sorted(
            plugin_name
            for plugin_name, plugin in discovered.items()
            if canon_for_compare(getattr(plugin, "source", "") or "")
            == canon_for_compare(decl.path)
        )
        seen = emitted_per_var.setdefault(decl.var, set())
        for plugin_name in names:
            decision = active_plugins.get(plugin_name)
            if decision is None:
                continue  # no explicit decision -> default on -> scan loads it
            disabled = not decision.enabled
            gui = decision.gui_only
            if not (disabled or gui):
                continue  # explicit but equals default -> still scan-loaded
            seen.add(plugin_name)
            existing = _base_entry_for(base_entries, plugin_name, decl.var)
            new_entries.append(
                ChainPluginEntry(
                    folder_var=decl.var,
                    name=plugin_name,
                    gui=gui,
                    disabled=disabled,
                    trailing_comment=(
                        existing.trailing_comment if existing else ""
                    ),
                )
            )

    # 2. Carried deviations.
    for entry in base_entries:
        if entry.folder_var == GLOBAL_PLUGINS_VAR_NAME:
            continue  # the Global branch handles these
        if entry.name in discovered:
            continue  # pass 1 already decided it
        base_path = base_folder_paths.get(entry.folder_var)
        if base_path is None:
            continue  # on-disk var no longer declared
        canon_path = canon_for_compare(base_path)
        if canon_path not in configured_canon:
            continue  # folder removed, so prune the deviation
        target_var = path_to_var[canon_path]
        seen = emitted_per_var.setdefault(target_var, set())
        if entry.name in seen:
            continue  # already emitted
        # An absent active entry keeps the on-disk deviation as it is.
        decision = active_plugins.get(entry.name)
        if decision is None:
            disabled = entry.disabled
            gui = entry.gui
        else:
            disabled = not decision.enabled
            gui = decision.gui_only
        if not (disabled or gui):
            continue  # deviation was reverted to default in memory
        seen.add(entry.name)
        new_entries.append(
            ChainPluginEntry(
                folder_var=target_var,
                name=entry.name,
                gui=gui,
                disabled=disabled,
                trailing_comment=entry.trailing_comment,
            )
        )

    return new_entries


def _base_entry_for(
    base_entries: list[ChainPluginEntry], name: str, folder_var: str
) -> Optional[ChainPluginEntry]:
    """First on-disk entry matching ``name`` - preferring ``folder_var``.

    Used only to recover a trailing comment for a discovered (pass-1)
    plugin. The folder-var preference keeps a comment attached to the
    right line when the same plugin name legitimately appears under two
    different folder vars on disk.
    """
    fallback: Optional[ChainPluginEntry] = None
    for entry in base_entries:
        if entry.name != name:
            continue
        if entry.folder_var == folder_var:
            return entry
        if fallback is None:
            fallback = entry
    return fallback


def _build_chain_model(
    registry,
    stem: str,
    active_model: LoadoutFile,
) -> LoadoutModel:
    """Build a sparse, exceptions-only chain ``LoadoutModel``.

    The rendered init.py carries a line only for a plugin the user turned
    off or set to GUI-only. A default-on plugin gets no line, and
    ``nsl_load_folder`` loads it at boot. So a newly dropped plugin does
    not mark the Loadout dirty.

    Global plugins get a block under ``global_plugins``, and only where
    the decision differs from the resolved Global model. The renderer
    writes no folder scan for that var.

    ``user_prefix`` is dropped so ``render`` re-declares the folder vars.
    ``user_prologue`` and ``user_suffix`` carry the user's own text
    forward unchanged, above and below the managed block.
    """
    # Read the on-disk model to keep trailing comments and to carry
    # deviations for plugins the scan cannot see.
    target = _chain_loadout_path(registry, stem)
    try:
        base_model = read_chain_loadout(str(target))
    except (OSError, SyntaxError):
        base_model = LoadoutModel()
    on_disk_by_name = {entry.name: entry for entry in base_model.plugins}
    base_folder_paths = {decl.var: decl.path for decl in base_model.folders}

    user_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    folders = [
        FolderDecl(var=folder_ops.canonical_folder_var(idx), path=path)
        for idx, path in enumerate(user_dirs)
    ]

    active_plugins = active_model.plugins if active_model is not None else {}
    discovered = getattr(registry, "discovered_plugins", {}) or {}

    new_plugins: list[ChainPluginEntry] = _user_folder_entries(
        folders=folders,
        active_plugins=active_plugins,
        discovered=discovered,
        base_entries=base_model.plugins,
        base_folder_paths=base_folder_paths,
    )

    # The ``global_plugins`` var is declared only when there is at least
    # one divergence to write.
    global_model = getattr(registry, "global_model", None)
    global_dirs = list(getattr(registry, "global_plugin_dirs", []) or [])
    if global_model is not None and global_dirs:
        global_exceptions: list[ChainPluginEntry] = []
        for plugin_name in sorted(global_model.plugins.keys()):
            decision = active_plugins.get(plugin_name)
            if decision is None:
                continue  # no explicit decision -> Global's value applies
            if decision == global_model.plugins.get(plugin_name):
                continue  # matches Global -> agreement stays implicit
            existing = on_disk_by_name.get(plugin_name)
            global_exceptions.append(
                ChainPluginEntry(
                    folder_var=GLOBAL_PLUGINS_VAR_NAME,
                    name=plugin_name,
                    gui=decision.gui_only,
                    disabled=not decision.enabled,
                    trailing_comment=existing.trailing_comment if existing else "",
                )
            )
        if global_exceptions:
            folders.append(
                FolderDecl(
                    var=GLOBAL_PLUGINS_VAR_NAME, path=str(global_dirs[0])
                )
            )
            new_plugins.extend(global_exceptions)

    return LoadoutModel(
        folders=folders,
        plugins=new_plugins,
        user_prefix="",
        user_suffix=base_model.user_suffix,
        user_prologue=base_model.user_prologue,
    )


def _chain_to_legacy(chain_model: Optional[LoadoutModel], name: str) -> Optional[LoadoutFile]:
    """Bridge a chain ``LoadoutModel`` back to the panel's ``LoadoutFile``."""
    if chain_model is None:
        return None
    return LoadoutFile(
        name=name,
        plugins={
            entry.name: PluginEntry(
                enabled=not entry.disabled,
                gui_only=entry.gui,
            )
            for entry in chain_model.plugins
        },
    )


def _bridged_op_result(result: loadout_ops.OpResult, stem: str) -> loadout_ops.OpResult:
    """Return a copy of ``result`` whose ``model`` field is a LoadoutFile.

    The wiring layer routes domain ops that emit chain ``LoadoutModel``s
    back through ``apply_op_result``, which expects the legacy
    ``LoadoutFile`` shape on ``model``. This is the single bridge point.
    """
    if result.model is None or isinstance(result.model, LoadoutFile):
        return result
    bridged = _chain_to_legacy(result.model, stem)  # type: ignore[arg-type]
    return loadout_ops.OpResult(
        path=result.path,
        model=bridged,  # type: ignore[arg-type]
        state=result.state,
        blocked=result.blocked,
    )


def _persist_active(registry, stem: str) -> Optional[loadout_ops.OpResult]:
    """Commit the active LoadoutFile to ``<loadouts_dir>/<stem>/init.py``.

    Returns the ``OpResult`` from ``loadout_ops.save`` (or ``None`` when
    there is nothing to save - no active model, or Global is active).
    """
    if not stem or stem == RESERVED_LOADOUT_STEM:
        return None
    if registry.active_model is None:
        return None
    chain_model = _build_chain_model(registry, stem, registry.active_model)
    return loadout_ops.save(
        registry.loadouts_dir, stem, chain_model, registry.state
    )


def _active_stack(registry) -> Optional[UndoStack]:
    """Return the undo stack for the active Loadout, or ``None`` for Global."""
    if not isinstance(registry.undo_stacks, UndoStackRegistry):
        return None
    if _is_global_active(registry.state):
        # Global has no stack. The auto-create flow flips the active
        # Loadout during the op. :func:`_handle_op_result` then pushes
        # the first entry onto the new Custom stack.
        return None
    return registry.undo_stacks.for_loadout(registry.state.active)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def wire_events(panel) -> None:
    """Connect ``panel`` widget signals to domain ops.

    ``panel.registry`` must already be attached. Pills go through
    :func:`rewire_grid_pills`, so a grid rebuild can re-attach them
    without a full wiring pass.
    """
    _wire_loadout_strip(panel)
    _wire_top_toolbar(panel)
    _wire_folder_card(panel)
    _wire_grid_toolbar(panel)
    _wire_search_tags_selection(panel)
    _wire_folder_engagement_clear(panel)
    rewire_grid_pills(panel)


def _wire_folder_engagement_clear(panel) -> None:
    """Clear engaged folder Select icons when the grid selection changes.

    Once a folder's Select icon is engaged, a direct selection in the
    grid makes the icon wrong, so it goes back to default.

    The check is deferred with ``QTimer.singleShot(0, ...)`` so a restore
    after ``grid.set_keys`` settles first.
    ``panel._folder_select_action_active`` blocks the check during the
    folder actions, so their own ``grid.select_keys`` calls do not look
    like a manual change.
    """
    grid = getattr(panel, "grid", None)
    if grid is None or not hasattr(grid, "selection_changed"):
        return
    if getattr(panel, "_folder_engagement_clear_wired", False):
        return
    panel._folder_engagement_clear_wired = True
    panel._folder_select_action_active = False

    from nsl.compat import QtCore  # lazy to keep this module Qt-light

    def _on_selection_changed(_keys) -> None:
        if getattr(panel, "_folder_select_action_active", False):
            return
        QtCore.QTimer.singleShot(0, lambda: _maybe_clear_engaged(panel))

    grid.selection_changed.connect(_on_selection_changed)


def _maybe_clear_engaged(panel) -> None:
    """Deferred companion to :func:`_wire_folder_engagement_clear`.

    Compares the grid's current selection against the union of every
    engaged folder's pill set. Mismatch → clear all engaged folder
    icons. Identical (or no folders engaged) → no-op.
    """
    card = getattr(panel, "folder_card", None)
    if card is None or not hasattr(card, "engaged_select_paths"):
        return
    engaged = card.engaged_select_paths()
    if not engaged:
        return
    registry = getattr(panel, "registry", None)
    if registry is None or not hasattr(registry, "on_folder_select"):
        return
    expected: set = set()
    for path in engaged:
        expected.update(registry.on_folder_select(path) or [])
    grid = getattr(panel, "grid", None)
    if grid is None:
        return
    actual = set(grid.selected_keys())
    if actual == expected:
        return
    card.clear_engaged_select()


# ---------------------------------------------------------------------------
# Grid toolbar - selection ops (Select All / Clear Selection)
# ---------------------------------------------------------------------------


def _wire_grid_toolbar(panel) -> None:
    toolbar = getattr(panel, "grid_toolbar", None)
    grid = getattr(panel, "grid", None)
    if toolbar is None or grid is None:
        return

    def _select_all() -> None:
        # Select every key currently in the grid.
        grid.select_keys(grid.keys())

    def _clear_selection() -> None:
        grid.clear_selection()

    if hasattr(toolbar, "select_all_requested"):
        toolbar.select_all_requested.connect(_select_all)
    if hasattr(toolbar, "clear_selection_requested"):
        toolbar.clear_selection_requested.connect(_clear_selection)


# ---------------------------------------------------------------------------
# Search/Tags strip - filtered selection ops (Select filtered / Deselect filtered)
# ---------------------------------------------------------------------------


def _wire_search_tags_selection(panel) -> None:
    search_tags = getattr(panel, "search_tags", None)
    grid = getattr(panel, "grid", None)
    if search_tags is None or grid is None:
        return

    def _filtered_keys() -> list:
        # ``last_visible`` is the matching subset after the last filter
        # recompute. ``grid.keys()`` would select every pill instead.
        pipeline = getattr(panel, "filter_pipeline", None)
        if pipeline is not None:
            last = getattr(pipeline, "last_visible", None)
            if last:
                return list(last)
        return list(grid.keys())

    def _on_select_filtered(add_to_selection: bool) -> None:
        keys = _filtered_keys()
        if add_to_selection:
            existing = set(grid.selected_keys())
            grid.select_keys(list(existing | set(keys)))
        else:
            grid.select_keys(keys)

    def _on_deselect_filtered() -> None:
        keys = set(_filtered_keys())
        existing = set(grid.selected_keys())
        grid.select_keys(list(existing - keys))

    if hasattr(search_tags, "select_filtered_requested"):
        search_tags.select_filtered_requested.connect(_on_select_filtered)
    if hasattr(search_tags, "deselect_filtered_requested"):
        search_tags.deselect_filtered_requested.connect(_on_deselect_filtered)


# ---------------------------------------------------------------------------
# Loadout strip - dropdown, file ops, panic
# ---------------------------------------------------------------------------


def _wire_loadout_strip(panel) -> None:
    strip = panel.loadout_strip

    strip.loadout_selected.connect(lambda name: _on_loadout_selected(panel, name))
    strip.rename_requested.connect(lambda name: _on_rename(panel, name))
    strip.duplicate_requested.connect(lambda name: _on_duplicate(panel, name))
    strip.delete_requested.connect(lambda name: _on_delete(panel, name))
    strip.revert_requested.connect(lambda name: _on_revert(panel, name))
    strip.save_requested.connect(lambda: _on_save(panel))
    strip.save_as_requested.connect(lambda: _on_save_as(panel))
    strip.import_requested.connect(lambda: _on_import(panel))
    strip.export_requested.connect(lambda: _on_export(panel))
    strip.panic_toggled.connect(lambda engaged: _on_panic_toggled(panel, engaged))

    # The title-bar close is guarded separately, in
    # ``_LoadoutPanelHost.closeEvent``. A flag stops both from prompting.
    close_button = getattr(panel, "close_button", None)
    if close_button is not None:
        close_button.clicked.connect(lambda: _on_close_button(panel))


def _stem_from_dropdown(name: str) -> str:
    """Return the Loadout stem for a dropdown row name.

    Row names are already bare stems, so this returns ``name`` as is.
    It stays as the one place to change if that ever differs.
    """
    return name


def _on_loadout_selected(panel, dropdown_name: str) -> None:
    """Switch the active Loadout through the domain seam.

    The chosen file is read from disk. In-memory edits in the Loadout
    being left are kept, along with its undo stack.

    Global has no file to read, so the panel uses ``global_model``.
    """
    registry = _registry(panel)
    stem = _stem_from_dropdown(dropdown_name)

    # Without this guard a re-select reads the file from disk and
    # ``apply_op_result`` drops the unsaved edits in ``active_model``.
    current_stem = registry.state.active if registry.state else ""
    if stem == current_stem:
        return

    # The user-land ``Global_Loadout`` cannot be activated while the
    # Global copy exists. Only a stray programmatic emit reaches here.
    if stem == GLOBAL_LOADOUT_DIR_NAME and getattr(
        registry, "global_loadout_copy_exists", False
    ):
        return

    if stem == RESERVED_LOADOUT_STEM:
        # ``switch_active`` only accepts real loadout folders, so the
        # dispatcher write is repeated inline here for Global.
        from dataclasses import replace
        new_state = replace(registry.state, active=stem)
        from nsl.boot.dispatcher import write_dispatcher
        write_dispatcher(
            str(loadout_ops.dispatcher_path(registry.loadouts_dir)), new_state
        )
        result = loadout_ops.OpResult(path=None, model=None, state=new_state)
        registry.apply_op_result(result)
        return

    if stem == DEFAULT_CUSTOM_LOADOUT_STEM:
        _switch_to_custom_in_memory(registry)
        return

    op_result = loadout_ops.switch_active(
        registry.loadouts_dir, stem, registry.state
    )
    if op_result.is_blocked:
        registry.on_blocked(op_result.blocked)
        return
    # ``switch_active`` returns a chain LoadoutModel. The panel still
    # works on the legacy LoadoutFile shape, so bridge it here.
    chain_model = op_result.model
    bridged: Optional[LoadoutFile] = None
    if chain_model is not None:
        bridged = LoadoutFile(
            name=stem,
            plugins={
                entry.name: PluginEntry(
                    enabled=not entry.disabled,
                    gui_only=entry.gui,
                )
                for entry in chain_model.plugins
            },
        )
    # ``path`` stays None. A real path tells apply_op_result the file
    # was saved. The edits in the outgoing Loadout are then lost on a
    # switch back.
    bridged_result = loadout_ops.OpResult(
        path=None,
        model=bridged,  # type: ignore[arg-type]
        state=op_result.state,
    )
    # A switch changes plugin on/off state only. Leave
    # ``user_plugin_dirs`` alone and rescan the same folders. Re-deriving
    # it from the new Loadout would drop a folder the user just added.
    registry.apply_op_result(bridged_result)
    scan = getattr(registry, "scan_and_refresh", None)
    if scan is not None:
        scan()


def _on_rename(panel, dropdown_name: str) -> None:
    registry = _registry(panel)
    new_name = registry.prompt_rename(dropdown_name)
    if not new_name:
        return
    current_stem = _stem_from_dropdown(dropdown_name)
    result = loadout_ops.rename(
        registry.loadouts_dir,
        current_stem,
        new_name,
        registry.state,
    )
    _handle_op_result(panel, result, old_stem=current_stem)


def _on_duplicate(panel, dropdown_name: str) -> None:
    registry = _registry(panel)
    new_name = registry.prompt_duplicate(dropdown_name)
    if not new_name:
        return

    current_stem = _stem_from_dropdown(dropdown_name)
    if current_stem == RESERVED_LOADOUT_STEM:
        # Duplicating Global gives a user Loadout with no deviations. The
        # Global model is a legacy LoadoutFile, so there is no chain model
        # to seed from. The next Save fills it in.
        result = loadout_ops.create(
            registry.loadouts_dir,
            new_name,
            registry.state,
            base=None,
        )
        _handle_op_result(panel, result)
        return

    # Duplicate is Save As under a new name. Build from the in-memory
    # model, not the file on disk. Unsaved GUI-only toggles then travel
    # into the copy instead of being dropped.
    if registry.active_model is None:
        return
    chain_model = _build_chain_model(registry, current_stem, registry.active_model)
    result = loadout_ops.save_as(
        registry.loadouts_dir,
        chain_model,
        new_name,
        registry.state,
    )
    _handle_op_result(panel, result, mark_clean=True)


def _on_delete(panel, dropdown_name: str) -> None:
    registry = _registry(panel)
    if not registry.prompt_delete(dropdown_name):
        return
    stem = _stem_from_dropdown(dropdown_name)
    result = loadout_ops.delete(
        registry.loadouts_dir,
        stem,
        registry.state,
    )
    # Drop the undo stack so a new Loadout reusing this stem does not
    # inherit the old history.
    if isinstance(registry.undo_stacks, UndoStackRegistry):
        registry.undo_stacks.drop(stem)
    _handle_op_result(panel, result)


def _on_revert(panel, dropdown_name: str) -> None:
    """Revert the active Loadout to its on-disk state.

    Unsaved in-memory edits are discarded, so it confirms first. The
    strip disables the button when there is nothing to revert, and the
    guard here covers a programmatic emit.
    """
    registry = _registry(panel)
    if not registry.is_active_dirty:
        return
    parent_widget = getattr(registry, "_parent_widget", None) or panel
    name = _stem_from_dropdown(dropdown_name)
    if not dialogs.confirm_revert_loadout(parent_widget, name):
        return
    registry.revert_active_to_baseline()


def _on_save(panel) -> None:
    """Save the active Loadout.

    The strip disables the button on Global and when nothing changed.
    The guard here covers a programmatic call.

    Custom redirects to Save As. Custom is in-memory only and never
    persists on its own, so Save means "save these edits under a name".
    """
    registry = _registry(panel)
    if registry.active_model is None:
        return
    from nsl.constants import DEFAULT_CUSTOM_LOADOUT_STEM
    active_stem = (
        registry.state.active if registry.state else ""
    )
    if active_stem == DEFAULT_CUSTOM_LOADOUT_STEM:
        _on_save_as(panel)
        return
    chain_model = _build_chain_model(registry, active_stem, registry.active_model)
    result = loadout_ops.save(
        registry.loadouts_dir,
        active_stem,
        chain_model,
        registry.state,
    )
    _handle_op_result(panel, result, mark_clean=True)


def _on_save_as(panel) -> None:
    registry = _registry(panel)
    if registry.active_model is None:
        return
    new_name = registry.prompt_save_as()
    if not new_name:
        return
    active_stem = registry.state.active if registry.state else ""
    # Read trailing comments from the source loadout (active_stem).
    chain_model = _build_chain_model(registry, active_stem, registry.active_model)
    if _is_global_loadout_staging_save(registry, new_name):
        _stage_global_loadout(panel, registry, chain_model)
        return
    result = loadout_ops.save_as(
        registry.loadouts_dir,
        chain_model,
        new_name,
        registry.state,
    )
    _handle_op_result(panel, result, mark_clean=True)


def _is_global_loadout_staging_save(registry, new_name: str) -> bool:
    """Whether a Save As under ``new_name`` is the staging save.

    True only when the Global copy of ``Global_Loadout`` exists and the
    typed name resolves to that stem. Without the copy it saves as a
    normal Loadout.
    """
    if not getattr(registry, "global_loadout_copy_exists", False):
        return False
    from nsl.data.filename_rules import validate_filename

    checked = validate_filename(new_name)
    return checked.is_valid and checked.filename == GLOBAL_LOADOUT_DIR_NAME


def _stage_global_loadout(panel, registry, chain_model: LoadoutModel) -> None:
    """Staging save for ``Global_Loadout``.

    Writes ``<loadouts_dir>/Global_Loadout/init.py`` with no Save As
    collision suffix and no flip to the hidden stem. The panel lands on
    the read-only Global view and a dialog explains the copy step.
    """
    result = loadout_ops.save(
        registry.loadouts_dir,
        GLOBAL_LOADOUT_DIR_NAME,
        chain_model,
        registry.state,
    )
    if result.is_blocked:
        registry.on_blocked(result.blocked)
        return

    from dataclasses import replace
    from nsl.boot.dispatcher import write_dispatcher

    new_state = replace(registry.state, active="")
    write_dispatcher(
        str(loadout_ops.dispatcher_path(registry.loadouts_dir)), new_state
    )
    mark = getattr(registry, "mark_clean", None)
    if mark is not None:
        mark(True)
    registry.apply_op_result(
        loadout_ops.OpResult(path=result.path, model=None, state=new_state)
    )

    staged_dir = str(Path(registry.loadouts_dir) / GLOBAL_LOADOUT_DIR_NAME)
    global_dir = str(
        install_root() / GLOBAL_FOLDER_NAME
    )
    parent_widget = getattr(registry, "_parent_widget", None) or panel
    dialogs.show_global_loadout_staged(parent_widget, staged_dir, global_dir)


def _close_needs_prompt(registry) -> bool:
    """Whether closing the panel should prompt the user to save.

    True when the active Loadout is dirty, or when Custom is active and
    at least one discovered plugin resolves to enabled. Custom never
    persists, so those green pills are unsaved work.

    Resolve the effective state the way the grid does, not from
    ``active_model.plugins``. Switching to Custom from the dropdown
    seeds the model from Global and leaves that dict empty while the
    pills are green.

    Only discovered plugins count. After the last folder is removed the
    stale entries are gone from the scan, so an empty Custom closes with
    no prompt.
    """
    if getattr(registry, "is_active_dirty", False):
        return True
    state = getattr(registry, "state", None)
    active_stem = (state.active if state else "") or ""
    if active_stem == DEFAULT_CUSTOM_LOADOUT_STEM:
        active_model = getattr(registry, "active_model", None)
        global_model = getattr(registry, "global_model", None)
        discovered = getattr(registry, "discovered_plugins", None) or {}
        for name in discovered:
            # active entry > Global entry > default-enabled.
            entry = None
            if active_model is not None:
                entry = active_model.plugins.get(name)
            if entry is None and global_model is not None:
                entry = global_model.plugins.get(name)
            effective_enabled = entry.enabled if entry is not None else True
            if effective_enabled:
                return True
    return False


def should_close_panel(panel) -> bool:
    """Decide whether the panel may close, prompting to Save when the
    active Loadout has unsaved edits.

    Returns ``True`` when the close should proceed (the active Loadout is
    clean, the edits were saved, or the user chose *Don't Save*) and
    ``False`` when the close must be cancelled (user chose *Cancel*, or a
    Save attempt did not actually clean the dirty flag - e.g. Custom's
    Save-As prompt was itself cancelled).

    The prompt fires when :func:`_close_needs_prompt` is True: either the
    active Loadout is value/force-dirty, OR it is Custom with enabled
    (pending) plugins (Custom never persists, so those plugins are unsaved
    work that won't load until Save As). So Global / clean user / empty
    Custom close immediately; a dirty user Loadout offers Save / Don't
    Save / Cancel; a Custom with pending plugins offers Save As… / Don't
    Save / Cancel. Save routes through the existing ``_on_save`` flow
    (which itself redirects Custom → Save As).

    This is the SINGLE guard shared by BOTH close gestures so they behave
    identically:
      * the bottom-row Close button (wired in ``_wire_loadout_strip``
        to call ``panel.close()``), and
      * the floating window's title-bar close, intercepted by
        ``nsl.menu._LoadoutPanelHost.closeEvent``.

    Both funnel through the widget's ``close()`` → ``closeEvent`` →
    this function, so the prompt fires exactly once per close attempt.
    The guard must cover both gestures: closing the floating window through
    the window-manager X must prompt just like the bottom-row Close button,
    so unsaved Custom edits aren't silently discarded.
    """
    registry = _registry(panel)
    if not _close_needs_prompt(registry):
        return True

    active_stem = (
        registry.state.active
        if registry.state else ""
    ) or ""
    is_custom = active_stem == DEFAULT_CUSTOM_LOADOUT_STEM

    from nsl.ui import dialogs

    choice = dialogs.confirm_close_with_unsaved_changes(
        panel,
        loadout_name=active_stem or "Loadout",
        is_custom=is_custom,
    )
    if choice == dialogs.CloseUnsavedChoice.CANCEL:
        return False
    if choice == dialogs.CloseUnsavedChoice.SAVE:
        _on_save(panel)
        # A cancelled Save As leaves the pending Custom in place. The
        # same guard then reads True and the close is held.
        if _close_needs_prompt(registry):
            return False
    # The next panel open builds a fresh Registry from disk, so Don't
    # Save discards every in-memory edit.
    return True


def _on_close_button(panel) -> None:
    """Bottom-row floating Close button handler.

    Runs the unsaved-changes guard here, so the button works as soon as
    the UI reloads. ``_nsl_close_confirmed`` stops the window-manager
    ``closeEvent`` guard from prompting a second time for this close.
    """
    if should_close_panel(panel):
        setattr(panel, "_nsl_close_confirmed", True)
        panel.close()


def _on_import(panel) -> None:
    """Import a chain-format loadout file into the user's loadouts dir.

    The stem comes from the source filename. ``save_as`` creates the
    folder and flips the dispatcher pointer. A blocked name or an
    unreadable source goes to ``on_blocked``.
    """
    registry = _registry(panel)
    source = registry.prompt_import()
    if not source:
        return
    source_path = Path(source)
    try:
        chain_model = read_chain_loadout(str(source_path))
    except (OSError, SyntaxError) as exc:
        log = getattr(registry, "on_blocked", None)
        if log is not None:
            log(
                loadout_ops.Blocked(
                    code=loadout_ops.BlockedReason.SOURCE_NOT_FOUND,
                    detail=str(exc),
                )
            )
        return
    raw_stem = source_path.stem
    result = loadout_ops.save_as(
        registry.loadouts_dir,
        chain_model,
        raw_stem,
        registry.state,
    )
    _handle_op_result(panel, result)


def _on_export(panel) -> None:
    """Export the active Loadout as a Loadout folder to a user path.

    Writes ``<chosen folder>/init.py`` from the in-memory model. The
    folder is a complete Loadout, ready to drop in. ``mark_clean`` stays
    untouched, because Export does not commit the active Loadout.
    """
    registry = _registry(panel)
    if registry.active_model is None:
        return
    target = registry.prompt_export()
    if not target:
        return
    active_stem = registry.state.active if registry.state else ""
    chain_model = _build_chain_model(registry, active_stem, registry.active_model)
    try:
        write_chain_loadout(str(target / "init.py"), chain_model)
    except OSError as exc:
        log = getattr(registry, "on_blocked", None)
        if log is not None:
            log(
                loadout_ops.Blocked(
                    code=loadout_ops.BlockedReason.SOURCE_NOT_FOUND,
                    detail=f"export write failed: {exc}",
                )
            )


def _on_panic_toggled(panel, engaged: bool) -> None:
    """Panic button - engage or release through ``loadout_ops.set_panic``.

    One flip pushes one undo step. Panic is global, but the stacks are
    per-Loadout, so the entry lands on the Loadout active at flip time.
    """
    registry = _registry(panel)
    previous = bool(getattr(registry.state, "panic", False))
    result = loadout_ops.set_panic(
        registry.loadouts_dir, engaged, registry.state
    )
    # ``set_panic`` returns ``model=None``. A panic flip must not change
    # ``active_model``, so carry it forward.
    forward = loadout_ops.OpResult(
        path=result.path,
        model=registry.active_model,  # type: ignore[arg-type]
        state=result.state,
    )
    registry.apply_op_result(forward)
    # A stray signal that re-asserts the current state must not burn an
    # undo step.
    if previous != bool(engaged) and isinstance(
        registry.undo_stacks, UndoStackRegistry
    ):
        stem = registry.state.active if registry.state else ""
        registry.undo_stacks.for_loadout(stem).push(
            {
                "kind": "panic_toggle",
                "previous": previous,
                "next": bool(engaged),
            }
        )
        _sync_undo_toolbar(panel)


# ---------------------------------------------------------------------------
# Top toolbar - undo / redo
# ---------------------------------------------------------------------------


def _wire_top_toolbar(panel) -> None:
    toolbar = panel.top_toolbar
    toolbar.undo_requested.connect(lambda: _on_undo(panel))
    toolbar.redo_requested.connect(lambda: _on_redo(panel))


def _on_undo(panel) -> None:
    """Pop the most recent entry from the active stack.

    The entry is an opaque payload here. Replaying it onto the active
    model is ``registry.apply_undo``'s job.
    """
    registry = _registry(panel)
    stack = _active_stack(registry)
    if stack is None or not stack.can_undo:
        return
    entry = stack.undo()
    apply = getattr(registry, "apply_undo", None)
    if apply is not None:
        apply(entry)
    _sync_undo_toolbar(panel)


def _on_redo(panel) -> None:
    registry = _registry(panel)
    stack = _active_stack(registry)
    if stack is None or not stack.can_redo:
        return
    entry = stack.redo()
    apply = getattr(registry, "apply_redo", None)
    if apply is not None:
        apply(entry)
    _sync_undo_toolbar(panel)


def _sync_undo_toolbar(panel) -> None:
    """Reflect the active stack's can_undo / can_redo on the top toolbar.

    Call it after any push. The loadout-switch wiring only refreshes on
    a switch, so without this the buttons stay disabled after the first
    pill toggle.
    """
    toolbar = getattr(panel, "top_toolbar", None)
    set_undo = getattr(toolbar, "set_undo_available", None)
    set_redo = getattr(toolbar, "set_redo_available", None)
    if set_undo is None or set_redo is None:
        return
    registry = _registry(panel)
    stack = _active_stack(registry)
    if stack is None:
        set_undo(False)
        set_redo(False)
        return
    set_undo(stack.can_undo)
    set_redo(stack.can_redo)


# ---------------------------------------------------------------------------
# Folder card - add / remove / reorder / rescan / select / visibility
# ---------------------------------------------------------------------------


def _wire_folder_card(panel) -> None:
    card = panel.folder_card
    card.add_folder_requested.connect(lambda: _on_add_folder(panel))
    card.rescan_requested.connect(lambda: _on_rescan(panel))
    card.reorder_requested.connect(lambda order: _on_reorder(panel, order))
    card.remove_confirmed.connect(lambda path: _on_remove_folder(panel, path))
    card.visibility_changed.connect(
        lambda path, visible: _on_folder_visibility(panel, path, visible)
    )
    card.open_folder_requested.connect(lambda path: _on_folder_open(panel, path))
    card.select_requested.connect(lambda path: _on_folder_select(panel, path))
    card.deselect_requested.connect(
        lambda path: _on_folder_deselect(panel, path)
    )
    card.health_inspected.connect(lambda path: _on_folder_health(panel, path))


def _discovered_names_from_folder(registry, folder_path: str) -> list:
    """Discovered plugin names sourced from *folder_path*.

    Paths are compared in canon form. The directory picker returns a raw
    path, often with a trailing slash, while ``source`` holds the
    normalised one. A raw comparison matches nothing, and the folder add
    then leaves the Loadout looking clean.
    """
    target = canon_for_compare(folder_path)
    discovered = getattr(registry, "discovered_plugins", None) or {}
    return [
        name for name, plugin in discovered.items()
        if canon_for_compare(getattr(plugin, "source", "") or "") == target
    ]


def _persist_folder_authority(registry) -> None:
    """Write the Plugins Folder list to the dispatcher and sync every Loadout.

    The logic lives on :meth:`Registry.persist_folder_authority`, so undo
    replay and Revert can re-persist without calling the wiring layer.
    """
    persist = getattr(registry, "persist_folder_authority", None)
    if persist is not None:
        persist()


def _model_side_snapshot(registry) -> dict:
    """One side, previous or next, of a ``folder_op`` undo entry.

    The model is snapshotted whole, because it is per-Loadout. The
    folder list is not. Folders are global, so the entry carries a delta
    instead, or a restore would undo folder changes made elsewhere.
    """
    model = registry.active_model
    cloned = (
        LoadoutFile(name=model.name, plugins=dict(model.plugins))
        if model is not None
        else None
    )
    return {
        "model": cloned,
        "force_dirty": set(getattr(registry, "force_dirty_plugins", ()) or ()),
    }


def _push_folder_undo_entry(registry, payload: dict, previous: dict) -> None:
    """Push one compound undo entry for a folder op.

    One folder add, remove or reorder is one undo step. The ``next`` side
    is taken here, after the rescan, so redo includes what the reconcile
    pass turned on.
    """
    if not isinstance(registry.undo_stacks, UndoStackRegistry):
        return
    stem = registry.state.active if registry.state else ""
    entry = dict(payload)
    entry["kind"] = "folder_op"
    entry["previous"] = previous
    entry["next"] = _model_side_snapshot(registry)
    registry.undo_stacks.for_loadout(stem).push(entry)


def _on_add_folder(panel) -> None:
    """Add a Plugins Folder to the active Loadout.

    On first run, Global or Custom there is no file to change, so the
    folder goes into the in-memory Custom slot and reaches disk only on
    Save As. A named Loadout goes through
    ``folder_ops.add_folder_and_save``.
    """
    registry = _registry(panel)
    chosen = registry.prompt_add_folder()
    if not chosen:
        return
    active_stem = registry.state.active if registry.state else ""
    if (
        not active_stem
        or active_stem == RESERVED_LOADOUT_STEM
        or active_stem == DEFAULT_CUSTOM_LOADOUT_STEM
    ):
        if _add_folder_in_memory(registry, chosen):
            _sync_undo_toolbar(panel)
        return
    previous = _model_side_snapshot(registry)
    loadout_init = _chain_loadout_path(registry, active_stem)
    try:
        current_model = read_chain_loadout(str(loadout_init))
    except (OSError, SyntaxError):
        current_model = LoadoutModel()
    try:
        result = folder_ops.add_folder_and_save(
            current_model, chosen,
            loadout_path=loadout_init,
        )
    except folder_ops.FolderAlreadyConfigured:
        already = getattr(registry, "on_folder_already_configured", None)
        if already is not None:
            already(chosen)
        return
    except folder_ops.FolderValidationError as exc:
        invalid = getattr(registry, "on_folder_validation_error", None)
        if invalid is not None:
            invalid(exc)
        return
    new_op = loadout_ops.OpResult(
        path=loadout_init,
        model=registry.active_model,  # type: ignore[arg-type]
        state=registry.state,
    )
    registry.user_plugin_dirs = [decl.path for decl in result.model.folders]
    registry.apply_op_result(new_op)
    _persist_folder_authority(registry)
    # ``apply_op_result`` does not scan, so the grid would stay empty
    # without this. The scan also reconciles the new plugins into the
    # active Loadout, so they do not sit pending on every restart.
    scan = getattr(registry, "scan_and_refresh", None)
    if scan is not None:
        scan()
    # Mark only the new folder's plugins dirty. Loadout entries survive a
    # remove, so a re-add would otherwise match on disk and leave Save
    # greyed out.
    mark = getattr(registry, "mark_plugins_force_dirty", None)
    if mark is not None:
        new_names = _discovered_names_from_folder(registry, chosen)
        if new_names:
            mark(new_names)
    # ``add_folder`` prepends, so record the real post-op index for redo.
    dirs_now = list(getattr(registry, "user_plugin_dirs", []) or [])
    added_path = os.path.normpath(chosen)
    _push_folder_undo_entry(
        registry,
        {
            "op": "add",
            "path": added_path,
            "index": dirs_now.index(added_path) if added_path in dirs_now else 0,
            "auto_created_custom": False,
        },
        previous,
    )
    _sync_undo_toolbar(panel)


def _add_folder_in_memory(registry, chosen: str) -> bool:
    """Add a folder to the in-memory Custom slot, with no Loadout write.

    Used on first run, on Global and on Custom. The state flips to
    Custom in memory so pills resolve under the user-Loadout rules, and
    the new plugins are marked dirty so Save opens.

    ``True`` when the folder landed, ``False`` when validation rejected it.
    """
    from dataclasses import replace

    prev_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    was_custom = bool(
        registry.state
        and registry.state.active == DEFAULT_CUSTOM_LOADOUT_STEM
    )
    # When this op creates Custom the pre-op model is None, and undo
    # keeps Custom active. A None model under a Custom pointer would
    # dead-end pill toggles, so snapshot an empty Custom instead.
    if registry.active_model is not None:
        prev_model = LoadoutFile(
            name=registry.active_model.name,
            plugins=dict(registry.active_model.plugins),
        )
    else:
        prev_model = LoadoutFile(name=DEFAULT_CUSTOM_LOADOUT_STEM, plugins={})
    previous = {
        "model": prev_model,
        "force_dirty": set(getattr(registry, "force_dirty_plugins", ()) or ()),
    }

    existing_model = LoadoutModel(
        folders=[
            FolderDecl(var=folder_ops.canonical_folder_var(idx), path=path)
            for idx, path in enumerate(registry.user_plugin_dirs)
        ]
    )
    try:
        result = folder_ops.add_folder(existing_model, chosen)
    except folder_ops.FolderAlreadyConfigured:
        already = getattr(registry, "on_folder_already_configured", None)
        if already is not None:
            already(chosen)
        return False
    except folder_ops.FolderValidationError as exc:
        invalid = getattr(registry, "on_folder_validation_error", None)
        if invalid is not None:
            invalid(exc)
        return False

    registry.user_plugin_dirs = [decl.path for decl in result.model.folders]
    # Custom never writes its on/off state. The folder still lives in the
    # dispatcher and must persist, or it is lost on reopen.
    _persist_folder_authority(registry)

    new_state = replace(registry.state, active=DEFAULT_CUSTOM_LOADOUT_STEM)
    base_plugins = (
        dict(registry.active_model.plugins)
        if registry.active_model is not None
        else {}
    )
    custom_active = LoadoutFile(
        name=DEFAULT_CUSTOM_LOADOUT_STEM, plugins=base_plugins
    )
    registry.apply_op_result(
        loadout_ops.OpResult(path=None, model=custom_active, state=new_state)
    )

    scan = getattr(registry, "scan_and_refresh", None)
    if scan is not None:
        scan()

    mark = getattr(registry, "mark_plugins_force_dirty", None)
    if mark is not None:
        new_names = _discovered_names_from_folder(registry, chosen)
        if new_names:
            mark(new_names)

    # ``apply_op_result`` snapshotted the list after the add. Pin the
    # Revert baseline back, or the first add cannot be reverted.
    if not was_custom:
        pin = getattr(registry, "set_folder_baseline", None)
        if pin is not None:
            pin(DEFAULT_CUSTOM_LOADOUT_STEM, prev_dirs)

    dirs_now = list(getattr(registry, "user_plugin_dirs", []) or [])
    added_path = os.path.normpath(chosen)
    _push_folder_undo_entry(
        registry,
        {
            "op": "add",
            "path": added_path,
            "index": dirs_now.index(added_path) if added_path in dirs_now else 0,
            "auto_created_custom": not was_custom,
        },
        previous,
    )
    return True


def _remove_folder_in_memory(registry, path: str) -> bool:
    """Remove a folder from the in-memory Custom slot.

    The mirror of :func:`_add_folder_in_memory`. The rescan drops the
    folder's plugins from ``discovered_plugins``, so they leave the grid.

    ``True`` when the folder was dropped, ``False`` when it was not
    configured.
    """
    prev_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    if path not in prev_dirs:
        return False
    previous = _model_side_snapshot(registry)
    registry.user_plugin_dirs = [p for p in prev_dirs if p != path]
    # Folders persist in the dispatcher even on Custom. A removal prunes
    # the folder and its plugin calls from every Loadout.
    _persist_folder_authority(registry)
    scan = getattr(registry, "scan_and_refresh", None)
    if scan is not None:
        scan()
    _push_folder_undo_entry(
        registry,
        {
            "op": "remove",
            "path": path,
            "index": prev_dirs.index(path),
            "auto_created_custom": False,
        },
        previous,
    )
    return True


def _on_remove_folder(panel, path: str) -> None:
    registry = _registry(panel)
    # The Global Plugins row is read-only. FolderRow hides its remove
    # control, and this guard covers a stray signal.
    from nsl.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
    if path == GLOBAL_PLUGINS_FOLDER_SENTINEL:
        return
    active_stem = registry.state.active if registry.state else ""
    # Every in-memory slot routes to the in-memory prune, like
    # ``_on_add_folder`` does. An empty active pointer still renders as
    # Custom. Returning here would leave the folder in the dispatcher.
    if (
        not active_stem
        or active_stem == RESERVED_LOADOUT_STEM
        or active_stem == DEFAULT_CUSTOM_LOADOUT_STEM
    ):
        user_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
        if path in user_dirs and _remove_folder_in_memory(registry, path):
            _sync_undo_toolbar(panel)
        return
    prev_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    previous = _model_side_snapshot(registry)
    loadout_init = _chain_loadout_path(registry, active_stem)
    try:
        current_model = read_chain_loadout(str(loadout_init))
    except (OSError, SyntaxError):
        return
    removal = registry.compute_folder_removal(path)
    try:
        result = folder_ops.remove_folder_and_save(
            current_model,
            path,
            actively_loaded_plugin_names=removal.get("actively_loaded", ()),
            plugin_names_unique_to_folder=removal.get("unique", ()),
            loadout_path=loadout_init,
        )
    except folder_ops.FolderNotConfigured:
        return
    new_op = loadout_ops.OpResult(
        path=loadout_init,
        model=registry.active_model,  # type: ignore[arg-type]
        state=registry.state,
    )
    registry.user_plugin_dirs = [decl.path for decl in result.model.folders]
    registry.apply_op_result(new_op)
    _persist_folder_authority(registry)
    # Without the rescan the grid keeps showing pills from the removed
    # folder until the next manual rescan or restart.
    scan = getattr(registry, "scan_and_refresh", None)
    if scan is not None:
        scan()
    # The recorded index lets undo put the folder back where it was.
    _push_folder_undo_entry(
        registry,
        {
            "op": "remove",
            "path": path,
            "index": prev_dirs.index(path) if path in prev_dirs else 0,
            "auto_created_custom": False,
        },
        previous,
    )
    _sync_undo_toolbar(panel)


def _on_reorder(panel, new_order) -> None:
    registry = _registry(panel)
    # The folder card includes the Global marker in its order. The
    # persisted list holds real paths only, so strip the marker first.
    from nsl.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
    real_order = [p for p in new_order if p != GLOBAL_PLUGINS_FOLDER_SENTINEL]
    active_stem = registry.state.active if registry.state else ""
    if not active_stem or active_stem == RESERVED_LOADOUT_STEM:
        return
    if active_stem == DEFAULT_CUSTOM_LOADOUT_STEM:
        # Custom has no file to reorder, so reorder the dispatcher list
        # directly and keep only the paths already known.
        prev_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
        previous = _model_side_snapshot(registry)
        known = set(prev_dirs)
        registry.user_plugin_dirs = [p for p in real_order if p in known]
        _persist_folder_authority(registry)
        scan = getattr(registry, "scan_and_refresh", None)
        if scan is not None:
            scan()
        # A reorder that changes nothing must not burn an undo step.
        if list(registry.user_plugin_dirs) != prev_dirs:
            _push_folder_undo_entry(
                registry,
                {
                    "op": "reorder",
                    "path": None,
                    "prev_order": prev_dirs,
                    "next_order": list(registry.user_plugin_dirs),
                    "auto_created_custom": False,
                },
                previous,
            )
            _sync_undo_toolbar(panel)
        return
    prev_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    previous = _model_side_snapshot(registry)
    loadout_init = _chain_loadout_path(registry, active_stem)
    try:
        current_model = read_chain_loadout(str(loadout_init))
    except (OSError, SyntaxError):
        return
    try:
        new_model = folder_ops.reorder_and_save(
            current_model, real_order,
            loadout_path=loadout_init,
        )
    except folder_ops.ReorderError:
        return
    new_op = loadout_ops.OpResult(
        path=loadout_init,
        model=registry.active_model,  # type: ignore[arg-type]
        state=registry.state,
    )
    registry.user_plugin_dirs = [decl.path for decl in new_model.folders]
    registry.apply_op_result(new_op)
    # Entries are remapped by path, so they stay correct after the sync.
    _persist_folder_authority(registry)
    # A reorder that changes nothing must not burn an undo step.
    if list(registry.user_plugin_dirs) != prev_dirs:
        _push_folder_undo_entry(
            registry,
            {
                "op": "reorder",
                "path": None,
                "prev_order": prev_dirs,
                "next_order": list(registry.user_plugin_dirs),
                "auto_created_custom": False,
            },
            previous,
        )
        _sync_undo_toolbar(panel)


def _on_rescan(panel) -> None:
    registry = _registry(panel)
    rescan = getattr(registry, "rescan", None)
    if rescan is not None:
        rescan()


def _on_folder_visibility(panel, path: str, visible: bool) -> None:
    """Eye toggle on a folder row - hide/show its pills in the grid.

    The registry records visibility into ``_folder_visibility`` and fires
    a refresh; :func:`_plugin_key_union` in ``panel.py`` also filters by
    that map so visibility survives every refresh path. This helper
    additionally
    drives ``grid.set_keys`` so the eye toggle reflects immediately
    without waiting for the registry's refresh callback.
    """
    registry = _registry(panel)
    hook = getattr(registry, "on_folder_visibility", None)
    if hook is not None:
        hook(path, visible)
    _apply_folder_visibility_to_grid(panel)


def _on_folder_select(panel, path: str) -> None:
    """Select button on a folder row - additive across folders.

    Two behaviours decided by how many folder rows are engaged
    (icon orange / checked) at the moment of the click. Qt toggles
    the clicked button BEFORE this slot fires, so ``engaged`` here
    already includes the just-clicked folder.

    * **First folder engaged** (no other folder currently checked):
      REPLACE the current grid selection with this folder's pills.
      Any pills the user had selected via grid clicks, marquee, or
      search are wiped - "the folder list select icon wins."
    * **Second or later folder engaged**: ADD this folder's pills
      to the current selection. Other engaged folders' pills stay
      selected; non-folder selections that survived the first
      folder engagement also stay selected.

    Deselect (peeling off a folder via clicking its now-orange
    icon) lives in :func:`_on_folder_deselect` and is always a
    subtract regardless of engaged count.
    """
    registry = _registry(panel)
    grid = getattr(panel, "grid", None)
    if grid is None:
        return
    folder_keys = set(registry.on_folder_select(path) or [])
    card = getattr(panel, "folder_card", None)
    engaged: set[str] = set()
    if card is not None and hasattr(card, "engaged_select_paths"):
        engaged = set(card.engaged_select_paths())
    other_engaged = engaged - {path}
    if other_engaged:
        current = set(grid.selected_keys())
        new_selection = list(current | folder_keys)
    else:
        new_selection = list(folder_keys)
    panel._folder_select_action_active = True
    try:
        grid.select_keys(new_selection)
    finally:
        panel._folder_select_action_active = False


def _on_folder_deselect(panel, path: str) -> None:
    """Inverse of :func:`_on_folder_select` - subtract this folder's
    pills from the current selection.

    Clicking the Select icon a second time (after it turned orange)
    turns it back to default AND deselects the pills it just selected.
    Computed as
    ``current_selection - folder_keys`` so any pills the user had
    selected before clicking this folder's Select button (or pills
    selected via search / marquee / other folders) survive.
    """
    registry = _registry(panel)
    grid = getattr(panel, "grid", None)
    if grid is None:
        return
    folder_keys = set(registry.on_folder_select(path) or [])
    current = set(grid.selected_keys())
    panel._folder_select_action_active = True
    try:
        grid.select_keys(list(current - folder_keys))
    finally:
        panel._folder_select_action_active = False


def _apply_folder_visibility_to_grid(panel) -> None:
    """Rebuild the grid's key list so hidden folders contribute no pills.

    The selection is captured and restored around ``set_keys``. Without
    that, ``set_keys`` clears the selection and emits an empty
    ``selection_changed``, which greys every engaged folder button.

    The filter pipeline does the same thing on the same signal. The
    duplicate stays because some test fixtures have no pipeline.
    """
    registry = _registry(panel)
    grid = getattr(panel, "grid", None)
    if grid is None:
        return
    visibility = getattr(registry, "folder_visibility", {}) or {}
    discovered = getattr(registry, "discovered_plugins", {}) or {}
    master = list(getattr(panel, "_all_plugin_keys", None) or grid.keys())
    hidden_keys = {
        name
        for name, plugin in discovered.items()
        if visibility.get(plugin.source, True) is False
    }
    # Global plugins live in ``global_model``, not in
    # ``discovered_plugins``, so the loop above misses them.
    from nsl.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
    if visibility.get(GLOBAL_PLUGINS_FOLDER_SENTINEL, True) is False:
        hidden_keys.update(
            getattr(registry, "global_plugin_names", ()) or ()
        )
    visible_keys = [k for k in master if k not in hidden_keys]
    try:
        preserved_selection = list(grid.selected_keys())
    except Exception:  # noqa: BLE001 - selection capture must not break visibility
        preserved_selection = []
    if grid.set_keys(visible_keys):
        rewire_grid_pills(panel)
    if preserved_selection:
        try:
            grid.select_keys(preserved_selection)
        except Exception:  # noqa: BLE001 - restore must not break visibility
            pass


def _on_folder_health(panel, path: str) -> None:
    registry = _registry(panel)
    hook = getattr(registry, "on_folder_health", None)
    if hook is not None:
        hook(path)


def _on_folder_open(panel, path: str) -> None:
    """Reveal a Plugins Folder in the OS file browser.

    The registry resolves the path and skips the Global marker.
    """
    registry = _registry(panel)
    hook = getattr(registry, "on_folder_open", None)
    if hook is not None:
        hook(path)


# ---------------------------------------------------------------------------
# Grid pills - pill click / gui_only toggle / info / diagnostic
# ---------------------------------------------------------------------------


def rewire_grid_pills(panel) -> None:
    """(Re-)connect every pill in ``panel.grid`` to the wiring slots.

    Called once during :func:`wire_events`; also called when the grid
    rebuilds (sort / filter / loadout switch).
    """
    grid = panel.grid
    keys = grid.keys()
    pills = getattr(grid, "_pills", [])
    for key, pill in zip(keys, pills):
        _wire_one_pill(panel, key, pill)


def _wire_one_pill(panel, plugin_name: str, pill) -> None:
    # Use default-arg trick so each lambda binds its own plugin name.
    pill.toggled.connect(
        lambda enabled, name=plugin_name: _on_pill_toggled(panel, name, enabled)
    )
    pill.gui_only_toggled.connect(
        lambda gui_only, name=plugin_name: _on_pill_gui_only(panel, name, gui_only)
    )
    pill.info_clicked.connect(
        lambda name=plugin_name: _on_pill_info(panel, name)
    )
    pill.menu_clicked.connect(
        lambda name=plugin_name: _on_pill_menu(panel, name)
    )
    pill.open_folder_requested.connect(
        lambda name=plugin_name: _on_pill_open_folder(panel, name)
    )
    # Dormant. The diag chip is gone, so ``diagnostic_clicked`` never
    # fires. The connection stays in case the chip returns.
    pill.diagnostic_clicked.connect(
        lambda name=plugin_name: _on_pill_diagnostic(panel, name)
    )


def _on_pill_toggled(panel, plugin_name: str, enabled: bool) -> None:
    """Single-pill enable toggle.

    One click, immediate, reversible, no confirmation. Toggling while
    Global is the active Loadout auto-creates ``Custom(*)``
    and routes the toggle there.
    """
    _toggle_plugin(panel, plugin_name, enabled=enabled, gui_only=None)


def _on_pill_gui_only(panel, plugin_name: str, gui_only: bool) -> None:
    """Single-pill GUI-only toggle.

    Global pills never reach this slot (the pill widget swallows the
    click in :meth:`PluginPill.mousePressEvent`). User-Plugin gui_only
    writes against Global auto-create Custom, same as enable toggles.
    """
    _toggle_plugin(panel, plugin_name, enabled=None, gui_only=gui_only)


def _switch_to_custom_in_memory(registry) -> None:
    """Flip the active view to the in-memory Custom wildcard slot.

    Custom is NSL's in-memory wildcard slot - it has no on-disk
    folder and is NEVER a bootable loadout. So the IN-MEMORY active
    becomes "Custom" (the panel runs as Custom), but the ON-DISK
    ``ACTIVE_LOADOUT`` is cleared to "". Writing "Custom" to disk would
    send the next Nuke restart's boot dispatcher chasing a
    non-existent ``loadouts/Custom/`` folder: it would silently load
    nothing AND orphan the user's last real loadout pointer. Clearing
    to "" is the honest serialization of the invariant "Custom never
    persists as the active loadout"; the user is warned at panel-close
    that leaving Custom means nothing loads next launch (see
    ``confirm_close_with_unsaved_changes``). Panic + folders already on
    disk are preserved.

    The fresh Custom model seeds from the resolved Global model - the
    departure point IS the Global view. When a dirty Custom model is
    parked from earlier in the session, ``apply_op_result``'s
    pure-switch restore brings that back instead of the seed.

    Shared by the dropdown's explicit Custom selection and the
    auto-create on the first pill toggle while Global is active.
    """
    from dataclasses import replace
    from nsl.boot.dispatcher import (
        read_dispatcher,
        write_dispatcher,
    )
    dispatcher = str(loadout_ops.dispatcher_path(registry.loadouts_dir))
    disk_state = read_dispatcher(dispatcher)
    disk_state.active = ""
    write_dispatcher(dispatcher, disk_state)
    in_memory_state = replace(
        registry.state, active=DEFAULT_CUSTOM_LOADOUT_STEM
    )
    base_plugins = (
        dict(registry.global_model.plugins)
        if registry.global_model is not None
        else {}
    )
    new_active = LoadoutFile(
        name=DEFAULT_CUSTOM_LOADOUT_STEM, plugins=base_plugins
    )
    result = loadout_ops.OpResult(
        path=None,
        model=new_active,  # type: ignore[arg-type]
        state=in_memory_state,
    )
    registry.apply_op_result(result)


def _toggle_plugin(
    panel,
    plugin_name: str,
    *,
    enabled: Optional[bool],
    gui_only: Optional[bool],
) -> None:
    """Common path for pill body and pill gui_only toggles.

    Exactly one of ``enabled`` / ``gui_only`` is non-None; the other is
    carried over from the previous entry - the same gesture and same code
    path regardless of where the Plugin came from.
    """
    registry = _registry(panel)
    global_base = plugin_name in set(
        getattr(registry, "global_plugin_names", ()) or ()
    )

    previous = _previous_entry(registry, plugin_name)
    next_enabled = enabled if enabled is not None else (
        previous.enabled if previous is not None else True
    )
    next_gui_only = gui_only if gui_only is not None else (
        previous.gui_only if previous is not None else False
    )
    entry = PluginEntry(enabled=next_enabled, gui_only=next_gui_only)

    was_global_active = _is_global_active(registry.state)

    # On Global there is no model to change. The toggle creates the
    # in-memory Custom slot and lands there, so Global is never edited.
    # The switch runs first, as its own op. The baseline then holds the
    # clean seed and the toggle reads as dirty.
    if registry.active_model is None:
        if not was_global_active:
            return
        _switch_to_custom_in_memory(registry)
        if registry.active_model is None:
            return
    new_plugins = dict(registry.active_model.plugins)
    new_plugins[plugin_name] = entry
    new_active = LoadoutFile(
        name=registry.active_model.name, plugins=new_plugins
    )
    result = loadout_ops.OpResult(
        path=None,
        model=new_active,  # type: ignore[arg-type]
        state=registry.state,
    )

    _push_undo_entry(registry, was_global_active, plugin_name, previous, entry, result)

    registry.apply_op_result(result)
    # ``apply_op_result`` never flips ``_is_dirty``. Without this call a
    # toggle produces no ``(*)`` on the strip's active row.
    mark = getattr(registry, "mark_clean", None)
    if mark is not None:
        mark(False)
    _sync_undo_toolbar(panel)


def _previous_entry(registry, plugin_name: str) -> Optional[PluginEntry]:
    """Resolve the Plugin's effective entry before this toggle.

    Active Loadout entry first, then the Global one, then ``None``. The
    Global step keeps a Global Plugin's ``gui_only`` measured against
    the Global value rather than a default.
    """
    if registry.active_model is not None:
        entry = registry.active_model.plugins.get(plugin_name)
        if entry is not None:
            return entry
    if registry.global_model is not None:
        return registry.global_model.plugins.get(plugin_name)
    return None


def _push_undo_entry(
    registry,
    was_global_active: bool,
    plugin_name: str,
    previous: Optional[PluginEntry],
    next_entry: PluginEntry,
    result: loadout_ops.OpResult,
) -> None:
    """Push exactly one undo entry on the post-op active Loadout's stack.

    A first toggle against Global lands on the fresh Custom stack. Any
    other toggle lands on the active Loadout's own stack. The payload is
    opaque to the stack, because the registry owns the replay.
    """
    if not isinstance(registry.undo_stacks, UndoStackRegistry):
        return
    if result.model is None:
        # Global is active again, so there is no stack to push to.
        return
    # A LoadoutModel has no ``.name``, a LoadoutFile does. The stack keys
    # by stem, so fall back to the dispatcher pointer.
    stem = getattr(result.model, "name", None) or registry.state.active
    stack = registry.undo_stacks.for_loadout(stem)
    stack.push(
        {
            "kind": "pill_toggle",
            "plugin": plugin_name,
            "previous": previous,
            "next": next_entry,
            "auto_created_custom": was_global_active,
        }
    )


def _on_pill_info(panel, plugin_name: str) -> None:
    """Pill info button - route to the side panel's Info tab.

    The registry loads the README, because it knows where it lives.
    """
    registry = _registry(panel)
    hook = getattr(registry, "on_pill_info", None)
    if hook is not None:
        hook(plugin_name)


def _on_pill_menu(panel, plugin_name: str) -> None:
    """Pill menu button - route to the side panel's Menu tab.

    Clicking the menu button loads the Plugin's ``menu.py`` into the Menu
    tab AND activates that tab. The registry owns content loading (it knows
    where the file lives); the wiring layer just forwards the click.
    """
    registry = _registry(panel)
    hook = getattr(registry, "on_pill_menu", None)
    if hook is not None:
        hook(plugin_name)


def _on_pill_open_folder(panel, plugin_name: str) -> None:
    """Pill right-click → reveal the Plugin's source folder in the OS file
    browser. The registry resolves the plugin's on-disk path (via
    ``discovered_plugins``) and opens it; the wiring layer just forwards.
    """
    registry = _registry(panel)
    hook = getattr(registry, "on_pill_open_folder", None)
    if hook is not None:
        hook(plugin_name)


def _on_pill_diagnostic(panel, plugin_name: str) -> None:
    """DORMANT - old Log-chip route. Never reached now (the diag chip was
    removed from the pill's bottom row, so ``diagnostic_clicked`` never
    fires). Kept so the dormant connection has a valid target.
    """
    registry = _registry(panel)
    hook = getattr(registry, "on_pill_diagnostic", None)
    if hook is not None:
        hook(plugin_name)


# ---------------------------------------------------------------------------
# Op-result handling
# ---------------------------------------------------------------------------


def _handle_op_result(
    panel,
    result: loadout_ops.OpResult,
    *,
    old_stem: Optional[str] = None,
    mark_clean: Optional[bool] = None,
) -> None:
    """Forward an :class:`OpResult` to the panel.

    Args:
        panel: The Loadout Panel.
        result: ``loadout_ops`` op outcome.
        old_stem: Rename's previous stem - used to relocate the undo
            stack so in-session history follows the renamed file.
        mark_clean: When ``True`` (Save / Save As), the strip's dirty flag
            is cleared post-op. ``False`` (Export) keeps it. ``None`` means
            the wiring layer doesn't touch dirty state; the panel decides.
    """
    registry = _registry(panel)
    if result.is_blocked:
        registry.on_blocked(result.blocked)
        return

    # No op may leave the active pointer on the hidden
    # ``Global_Loadout``. Normalise to the read-only Global view and
    # persist, so boot agrees.
    if (
        result.state is not None
        and result.state.active == GLOBAL_LOADOUT_DIR_NAME
        and getattr(registry, "global_loadout_copy_exists", False)
    ):
        from dataclasses import replace
        from nsl.boot.dispatcher import write_dispatcher

        normalized = replace(result.state, active="")
        write_dispatcher(
            str(loadout_ops.dispatcher_path(registry.loadouts_dir)), normalized
        )
        result = loadout_ops.OpResult(
            path=result.path, model=None, state=normalized
        )

    post_stem = result.state.active if result.state else ""
    if result.path is not None:
        post_stem = result.path.name

    if old_stem is not None and result.path is not None:
        # ``loadout_ops.rename`` leaves the in-memory ``name`` alone, so
        # a custom display name survives. Only the stack follows.
        new_stem = result.path.name
        if isinstance(registry.undo_stacks, UndoStackRegistry):
            registry.undo_stacks.rename(old_stem, new_stem)

    if mark_clean is not None:
        mark = getattr(registry, "mark_clean", None)
        if mark is not None:
            mark(bool(mark_clean))

    # Bridge chain LoadoutModel back to the panel's legacy LoadoutFile
    # shape before forwarding through ``apply_op_result``.
    forward = _bridged_op_result(result, post_stem)
    registry.apply_op_result(forward)
