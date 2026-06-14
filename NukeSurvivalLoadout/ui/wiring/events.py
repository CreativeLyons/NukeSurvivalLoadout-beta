"""Event wiring - connects UI widget signals to domain ops.

The single public helper is :func:`wire_events`, called once after the panel
is built. It connects the loadout strip, top toolbar, folder card, grid
toolbar, search/tags strip, and grid pills to their domain-op handlers.

``wire_events`` reads everything it needs from ``panel.registry`` - a state
carrier attached at construction time. Routing all panel state through the
registry keeps this module decoupled and Qt-light: it imports the pure-Python
domain layer (``loadout_ops`` / ``folder_ops`` / ``undo_stack``) eagerly and
pulls in Qt only lazily where strictly needed.

Key behavior:
* User edits (pill toggles, folder add/remove) mutate the active LoadoutFile
  in memory only; nothing is written to disk until an explicit Save / Save As.
  A toggle marks the loadout dirty (the ``(*)`` marker + enabled Save button).
* Each single-pill toggle pushes exactly one undo entry on the active loadout's
  per-loadout undo stack.
* Plugins Folders are dispatcher-authoritative (global state, not per-loadout),
  so folder add/remove/reorder always re-persist to the dispatcher and sync
  into every loadout - including while the in-memory Custom slot is active.
* Custom never persists as a loadout on its own; Save on Custom redirects to
  Save As, and closing with pending Custom plugins prompts to save.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from NukeSurvivalLoadout.ui import dialogs
from NukeSurvivalLoadout.constants import (
    DEFAULT_CUSTOM_LOADOUT_STEM,
    GLOBAL_FOLDER_NAME,
    GLOBAL_LOADOUT_DIR_NAME,
    GLOBAL_PLUGINS_VAR_NAME,
    RESERVED_LOADOUT_STEM,
)
from NukeSurvivalLoadout.boot.dispatcher import DispatcherState
from NukeSurvivalLoadout.boot.loadout_file import (
    FolderDecl,
    LoadoutModel,
    PluginEntry as ChainPluginEntry,
    read_loadout as read_chain_loadout,
    write_loadout as write_chain_loadout,
)
from NukeSurvivalLoadout.data.loadout_file import LoadoutFile, PluginEntry
from NukeSurvivalLoadout.domain import folder_ops, loadout_ops
from NukeSurvivalLoadout.domain.undo_stack import UndoStack, UndoStackRegistry
from NukeSurvivalLoadout.paths import canon_for_compare


__all__ = ["wire_events"]


# ---------------------------------------------------------------------------
# Helpers - registry access
# ---------------------------------------------------------------------------


def _registry(panel):
    """Return ``panel.registry``; raise a friendly error if missing.

    Used at signal-emission time so a misconfigured panel surfaces a clear
    AttributeError rather than a cryptic Qt exception inside the slot.
    """
    reg = getattr(panel, "registry", None)
    if reg is None:
        raise AttributeError(
            "panel.registry is None - attach a Registry "
            "(build_registry_for_panel) before wiring signals."
        )
    return reg


def _is_global_active(state: DispatcherState) -> bool:
    """True when no user loadout is the dispatcher's active pointer.

    Empty pointer or the reserved ``Global`` stem both mean Global is
    active. Mirrors the rule the boot dispatcher uses (skipping the
    active pluginAddPath when ``ACTIVE_LOADOUT`` is empty / reserved).
    """
    return (
        not state.active
        or state.active == RESERVED_LOADOUT_STEM
    )


def _chain_loadout_path(registry, stem: str) -> Path:
    """Return the chain-architecture init.py path for a loadout stem."""
    return Path(registry.loadouts_dir) / stem / "init.py"


def _build_chain_model(
    registry,
    stem: str,
    active_model: LoadoutFile,
) -> LoadoutModel:
    """Build a SPARSE (exceptions-only) chain ``LoadoutModel``.

    The rendered loadout ``init.py`` lists explicit ``nsl_pluginAddPath``
    lines ONLY for plugins that deviate from the default - i.e. ones the user
    turned off (``disabled``) or set to GUI-only (``gui``). Every default-on
    plugin gets NO line: the rendered ``nsl_load_folder(<var>)`` scan loads
    those at boot (see ``boot/loadout_file._render_managed_block``). This
    matches the panel's existing sparse in-memory model - a default-on plugin
    is "no entry", so a freshly-dropped plugin loads without marking the
    loadout dirty.

    We enumerate ``registry.discovered_plugins`` (mapped to folders via
    ``Plugin.source``) but emit only the exceptions.

    Global plugins get their own block under the folder var named
    ``global_plugins`` (the Global chain head re-binds that NAME to its
    own resolved dir each boot; the absolute path literal written here
    serves the file's user-land life). An entry is emitted only when the
    user's decision DIVERGES from the resolved Global model - agreement
    stays implicit, and the renderer writes no folder scan for this var
    (the head owns baseline Global loading and skips exactly the names
    this file mentions).

    Trailing comments on existing on-disk exception lines are preserved.

    The NSL prologue (imports + folder vars + helper) is authored fresh every
    write: ``user_prefix`` is dropped so a folder add/remove always
    re-declares the ``plugins_X`` vars. Genuine user content is still
    preserved verbatim on both sides of the managed region: hand-authored
    text ABOVE the NSL prologue markers rides in ``user_prologue`` and text
    below the END marker rides in ``user_suffix``. Both are read back from
    the on-disk model and carried forward unchanged (Issue 2 - the old code
    zeroed user_prefix and, because a legacy parse folded the prologue text
    into it, silently discarded any custom import/helper above the markers).
    """
    # On-disk model - read solely to preserve trailing comments on calls.
    target = _chain_loadout_path(registry, stem)
    try:
        base_model = read_chain_loadout(str(target))
    except (OSError, SyntaxError):
        base_model = LoadoutModel()
    on_disk_by_name = {entry.name: entry for entry in base_model.plugins}

    # One FolderDecl per configured user plugin folder, in configured order.
    user_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    folders = [
        FolderDecl(var=folder_ops.canonical_folder_var(idx), path=path)
        for idx, path in enumerate(user_dirs)
    ]

    active_plugins = active_model.plugins if active_model is not None else {}
    discovered = getattr(registry, "discovered_plugins", {}) or {}

    # Emit folder-by-folder so the managed block groups cleanly; within a
    # folder, sort by name for stable, diff-friendly output. Only EXCEPTIONS
    # (disabled / gui-only) get a line; default-on plugins are scan-loaded.
    new_plugins: list[ChainPluginEntry] = []
    for decl in folders:
        names = sorted(
            plugin_name
            for plugin_name, plugin in discovered.items()
            if getattr(plugin, "source", None) == decl.path
        )
        for plugin_name in names:
            decision = active_plugins.get(plugin_name)
            if decision is None:
                continue  # no explicit decision -> default on -> scan loads it
            disabled = not decision.enabled
            gui = decision.gui_only
            if not (disabled or gui):
                continue  # explicit but equals default -> still scan-loaded
            existing = on_disk_by_name.get(plugin_name)
            new_plugins.append(
                ChainPluginEntry(
                    folder_var=decl.var,
                    name=plugin_name,
                    gui=gui,
                    disabled=disabled,
                    trailing_comment=existing.trailing_comment if existing else "",
                )
            )

    # Global-plugin overrides: one entry per divergence from the resolved
    # Global model, under the ``global_plugins`` var. Declared only when
    # there is at least one divergence to write.
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
        # Carry the hand-authored prologue forward so a panel Save preserves
        # any custom Python the user placed above the NSL prologue markers
        # (Issue 2). ``user_prefix`` stays empty so render() regenerates the
        # NSL head from ``folders`` - no stale plugins_X decls.
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
        # Pure-Python tests may use a bare object; tolerate that.
        return None
    if _is_global_active(registry.state):
        # Global has no on-disk identity; the auto-create flow flips the
        # active Loadout *during* the op so the first-toggle undo entry
        # lands on the newly-created Custom stack. We push the entry from
        # the post-op state in :func:`_handle_op_result`.
        return None
    return registry.undo_stacks.for_loadout(registry.state.active)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def wire_events(panel) -> None:
    """Connect ``panel`` widget signals to domain ops.

    Called by the orchestrator's stitched ``_wire_signals`` extension. The
    panel must already have ``panel.registry`` attached. Pills are wired
    via :func:`rewire_grid_pills` so that grid rebuilds (loadout switch)
    can re-attach signals without going through the full wiring pass.
    """
    _wire_loadout_strip(panel)
    _wire_top_toolbar(panel)
    _wire_folder_card(panel)
    # The grid_toolbar's select_all_requested / clear_selection_requested
    # and search_tags's select_filtered_requested / deselect_filtered_requested
    # signals are wired here so the selection controls mutate the grid.
    _wire_grid_toolbar(panel)
    _wire_search_tags_selection(panel)
    _wire_folder_engagement_clear(panel)
    rewire_grid_pills(panel)


def _wire_folder_engagement_clear(panel) -> None:
    """Auto-clear engaged folder Select icons when the grid selection
    diverges from what those icons promise.

    Once a folder's Select icon is engaged, selecting something directly in
    the grid makes those icons no longer truthful, so they revert to default.

    The check is deferred via ``QTimer.singleShot(0, ...)`` so any
    synchronous restore that follows a ``grid.set_keys`` (sort
    rebuild, pipeline recompute, panel refresh) settles before we
    compare expected vs. actual selection. Idempotent - multiple
    queued checks in the same event-loop iteration all see the same
    settled state and the first one to run clears (or no-ops);
    subsequent ones see no engaged folders and short-circuit.

    The folder-action guard (``panel._folder_select_action_active``)
    short-circuits during ``_on_folder_select`` /
    ``_on_folder_deselect`` so their own ``grid.select_keys`` calls
    don't appear as "user manually changed selection."
    """
    grid = getattr(panel, "grid", None)
    if grid is None or not hasattr(grid, "selection_changed"):
        return
    if getattr(panel, "_folder_engagement_clear_wired", False):
        return
    panel._folder_engagement_clear_wired = True
    panel._folder_select_action_active = False

    from NukeSurvivalLoadout.compat import QtCore  # lazy to keep this module Qt-light

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
        # The pipeline's last_visible is the cached result of the most
        # recent filter recompute. When no filter is active, it equals all
        # current keys; when a query is typed, it's the matching subset.
        # Reading this attribute avoids Select-filtered falling back to
        # grid.keys() (== all pills) even with a filter active.
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
    # Revert discards in-memory edits - destructive, so the handler asks
    # for confirmation before calling ``registry.revert_active_to_baseline``.
    strip.revert_requested.connect(lambda name: _on_revert(panel, name))
    strip.save_requested.connect(lambda: _on_save(panel))
    strip.save_as_requested.connect(lambda: _on_save_as(panel))
    strip.import_requested.connect(lambda: _on_import(panel))
    strip.export_requested.connect(lambda: _on_export(panel))
    strip.panic_toggled.connect(lambda engaged: _on_panic_toggled(panel, engaged))

    # Bottom-row floating Close button - prompts directly via
    # ``_on_close_button`` (F11-reloadable, so it works without a full
    # Nuke relaunch). The window-manager title-bar close is guarded
    # separately by ``_LoadoutPanelHost.closeEvent``; a flag keeps the two
    # from double-prompting. See :func:`_on_close_button` /
    # :func:`should_close_panel`.
    close_button = getattr(panel, "close_button", None)
    if close_button is not None:
        close_button.clicked.connect(lambda: _on_close_button(panel))


def _stem_from_dropdown(name: str) -> str:
    """Dropdown row name → bare stem.

    Row names ARE bare stems now (the ``.loadout`` display suffix is
    retired); the function survives as the single seam between the strip
    vocabulary and the domain ops, in case the display format grows
    decoration again.
    """
    return name


def _on_loadout_selected(panel, dropdown_name: str) -> None:
    """Switch active Loadout - route the dispatcher flip through the domain seam.

    The chosen file is read from disk; in-memory edits in the
    previously-active Loadout are preserved (the per-Loadout undo stack stays
    attached via the UndoStackRegistry).

    Selecting ``Global`` keeps the dispatcher pointing at the
    reserved stem; the panel reads ``global_model`` (resolved at startup)
 - there is no per-loadout file to read for Global.
    """
    registry = _registry(panel)
    stem = _stem_from_dropdown(dropdown_name)

    # Re-selecting the currently-active stem from the dropdown is a no-op.
    # Without this guard, the handler reads the file from disk and
    # ``apply_op_result`` overwrites the in-memory active_model, silently
    # discarding unsaved edits.
    current_stem = registry.state.active if registry.state else ""
    if stem == current_stem:
        return

    # Case B: the user-land ``Global_Loadout`` is hidden and never
    # activatable while the Global copy exists. The dropdown doesn't
    # list it, so this only fires on a stray programmatic emit.
    if stem == GLOBAL_LOADOUT_DIR_NAME and getattr(
        registry, "global_loadout_copy_exists", False
    ):
        return

    if stem == RESERVED_LOADOUT_STEM:
        # No on-disk file for Global; flip the dispatcher directly via
        # ``set_panic``'s sibling seam ``switch_active`` is only valid
        # against real loadout folders, so we replicate its dispatcher
        # write inline here.
        from dataclasses import replace
        new_state = replace(registry.state, active=stem)
        from NukeSurvivalLoadout.boot.dispatcher import write_dispatcher
        write_dispatcher(
            str(loadout_ops.dispatcher_path(registry.loadouts_dir)), new_state
        )
        result = loadout_ops.OpResult(path=None, model=None, state=new_state)
        registry.apply_op_result(result)
        return

    if stem == DEFAULT_CUSTOM_LOADOUT_STEM:
        _switch_to_custom_in_memory(registry)
        return

    # Real user loadout - route through the domain seam so the
    # dispatcher write and the file-read both flow through one path.
    op_result = loadout_ops.switch_active(
        registry.loadouts_dir, stem, registry.state
    )
    if op_result.is_blocked:
        registry.on_blocked(op_result.blocked)
        return
    # ``switch_active`` returns the chain LoadoutModel; the panel still
    # operates on the legacy LoadoutFile shape, so bridge here.
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
    # path=None on purpose: a switch READS the target loadout and flips the
    # dispatcher pointer (already written inside ``switch_active``); it never
    # writes loadout CONTENT to disk. ``apply_op_result`` treats a non-None
    # path as "this loadout's content was just saved", which defeats its
    # pure-switch park/restore machinery - the outgoing loadout's unsaved
    # edits wouldn't be parked, and a switch-back would reload clean from
    # disk, silently dropping the edits. Custom already returns path=None and
    # preserves correctly; this brings named loadouts to parity so dirty
    # edits survive switch-away-and-back (still (*), no prompt). The
    # close-panel guard remains scoped to the CURRENT loadout only; parked
    # dirty loadouts are discarded on panel close without warning, by design.
    bridged_result = loadout_ops.OpResult(
        path=None,
        model=bridged,  # type: ignore[arg-type]
        state=op_result.state,
    )
    # Switching loadouts changes ONLY the on/off state of plugins - NOT the
    # plugin directories. The folder list is session-stable: a directory the
    # user added must survive a loadout switch.
    #
    # Loadouts carry on/off only; where the plugins live is not a per-switch
    # concern. We leave ``user_plugin_dirs`` untouched and just rescan the
    # SAME folders to repaint pill on/off against the new loadout. Re-deriving
    # ``user_plugin_dirs`` from the switched-to loadout's FolderDecls would
    # wipe any folder added under the previous loadout.
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
        # Duplicating Global produces a user Loadout with an empty
        # deviation set (functionally identical to creating a new Loadout
        # when Global is set). The Global model is a legacy
        # LoadoutFile (dict-keyed) so we don't have a chain LoadoutModel
        # to seed from directly - call ``create`` with no base; the
        # in-memory bridge will catch up on the next Save.
        result = loadout_ops.create(
            registry.loadouts_dir,
            new_name,
            registry.state,
            base=None,
        )
        _handle_op_result(panel, result)
        return

    # Duplicate IS Save As under a new name - the two buttons are
    # intentionally near-identical. Build the new loadout from the
    # IN-MEMORY active model so unsaved edits (enable + GUI-only toggles)
    # travel into the copy, then write + switch + mark clean (same path as
    # ``_on_save_as``).
    #
    # Building from the in-memory model (rather than copying the on-disk
    # file) ensures unsaved edits - notably GUI-only toggles, which live in
    # memory until Save - travel into the copy instead of being dropped.
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
    # Drop the per-Loadout undo stack so a fresh Loadout reusing the same
    # stem doesn't inherit stale history (undo_stack.UndoStackRegistry.drop).
    if isinstance(registry.undo_stacks, UndoStackRegistry):
        registry.undo_stacks.drop(stem)
    _handle_op_result(panel, result)


def _on_revert(panel, dropdown_name: str) -> None:
    """Revert the active Loadout to its on-disk state.

    Destructive: unsaved in-memory edits are discarded. Confirms via
    :func:`NukeSurvivalLoadout.ui.dialogs.confirm_revert_loadout`
    before calling :meth:`Registry.revert_active_to_baseline`. The
    strip's enable gating prevents this slot from firing when there's
    nothing to revert (clean state, or Global active), but we still
    guard here so a programmatic emit is no-op-safe.
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

    Disabled when Global is active or there are no unsaved changes; the
    strip greys the button so this slot effectively never fires in those
    conditions. We still guard here so a programmatic invocation is
    no-op-safe.

    Custom redirects to the Save-As flow. Custom is the in-memory
    wildcard scratch slot - it never persists to disk on its own. The
    user-facing Save button is enabled on Custom for intuitive
    discoverability, and this redirect makes Save behave as "save my
    Custom edits under a new name."
    """
    registry = _registry(panel)
    if registry.active_model is None:
        return
    from NukeSurvivalLoadout.constants import DEFAULT_CUSTOM_LOADOUT_STEM
    active_stem = (
        registry.state.active if registry.state else ""
    )
    if active_stem == DEFAULT_CUSTOM_LOADOUT_STEM:
        _on_save_as(panel)
        return
    # Bridge the in-memory LoadoutFile to a chain LoadoutModel using the
    # on-disk file as the folder/var/prefix source, then hand the model
    # to ``loadout_ops.save``.
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
    """Whether a Save As under ``new_name`` is the case-B staging save.

    True only when the Global copy of ``Global_Loadout`` exists AND the
    typed name resolves to that stem. In case A (no copy) the name saves
    as a completely normal loadout.
    """
    if not getattr(registry, "global_loadout_copy_exists", False):
        return False
    from NukeSurvivalLoadout.data.filename_rules import validate_filename

    checked = validate_filename(new_name)
    return checked.is_valid and checked.filename == GLOBAL_LOADOUT_DIR_NAME


def _stage_global_loadout(panel, registry, chain_model: LoadoutModel) -> None:
    """Case-B staging save for ``Global_Loadout``.

    Writes (or overwrites) ``<loadouts_dir>/Global_Loadout/init.py``
    WITHOUT the usual Save As collision suffixing or active-pointer flip
    to the hidden stem: the panel lands on the read-only Global view
    (persisted, so boot agrees) and a message explains the copy step.
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
    from NukeSurvivalLoadout.boot.dispatcher import write_dispatcher

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
        Path(__file__).resolve().parents[3] / GLOBAL_FOLDER_NAME
    )
    parent_widget = getattr(registry, "_parent_widget", None) or panel
    dialogs.show_global_loadout_staged(parent_widget, staged_dir, global_dir)


def _close_needs_prompt(registry) -> bool:
    """Whether closing the panel should prompt the user to save.

    True when EITHER:

    * the active Loadout is value-dirty / force-dirty
      (``is_active_dirty`` - a real edit since the last save), OR
    * the active slot is **Custom with at least one DISCOVERED plugin whose
      EFFECTIVE state is enabled** (i.e. a green/pending pill in the grid).
      Custom is in-memory only and NEVER persists as a loadout, so an
      enabled, on-disk-present plugin is unsaved work that won't load on the
      next Nuke restart until the user Saves As. A freshly reopened pending
      Custom reads value-*clean* against its Global baseline, yet closing it
      without Save As still drops real intent: the pills are green but
      nothing will load. So we must still prompt - a Custom(*) close must
      offer to save.

      We resolve each discovered plugin's effective state the SAME way the
      pill grid does (``ui.state.pill_state_from``): active entry > Global
      entry > default-enabled. Checking the EFFECTIVE state, not the
      explicit ``active_model.plugins`` dict, is essential - some Custom
      entry paths leave the discovered plugins default-enabled WITHOUT
      writing explicit entries. Concretely, switching to Custom from the
      dropdown (``_on_loadout_selected``) seeds ``active_model`` from Global
      and never reconciles the folder's plugins in, so the dict is empty
      even though the grid shows green pills. An explicit-dict check would
      return False there and let the panel close silently.

      The discoverability gate matters: after the user removes the last
      Plugins Folder, ``active_model`` may still carry stale entries for the
      now-"Missing" plugins, but they aren't discovered any more, so the
      loop skips them - and an empty Custom (no folders / no plugins) has
      nothing to lose. So "removed the last folder → nothing to save →
      close silently" is correct: with no plugin path added at all, it is
      safe to close with no prompt.
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
            # Effective enabled state, resolved as the grid resolves it:
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
        ``NukeSurvivalLoadout.menu._LoadoutPanelHost.closeEvent``.

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

    from NukeSurvivalLoadout.ui import dialogs

    choice = dialogs.confirm_close_with_unsaved_changes(
        panel,
        loadout_name=active_stem or "Loadout",
        is_custom=is_custom,
    )
    if choice == dialogs.CloseUnsavedChoice.CANCEL:
        return False
    if choice == dialogs.CloseUnsavedChoice.SAVE:
        _on_save(panel)
        # If the save didn't actually land (user cancelled the Save As
        # prompt for Custom, or the op was blocked), cancel the close -
        # treat as an implicit cancel so edits aren't lost. Re-use the same
        # guard: a successful Save As flips the active slot to a named
        # loadout (no longer Custom, value-clean) so this reads False and
        # the close proceeds; a cancelled Save As leaves the pending Custom
        # in place so this stays True and the close is held.
        if _close_needs_prompt(registry):
            return False
    # DISCARD or SAVE-succeeded → allow the close. The caller's
    # ``close()`` hides the widget; ``NukeSurvivalLoadout.menu`` (line 117)
    # checks ``not _panel_instance.isVisible()`` on the next open and
    # constructs a fresh ``_LoadoutPanelHost``, which builds a new
    # ``Registry`` from boot scratch. Net effect: "Don't Save" discards
    # in-memory edits across the board, and the next panel open shows the
    # saved-on-disk state. Matches the floating Close button's
    # "Any unsaved changes will be lost." prompt copy.
    return True


def _on_close_button(panel) -> None:
    """Bottom-row floating Close button handler.

    Runs the unsaved-changes guard HERE (this module is F11-reloadable, so
    the button works the instant the UI is reloaded - no full Nuke
    relaunch needed) and then closes. Sets ``_nsl_close_confirmed`` on the
    panel so the window-manager ``closeEvent`` guard
    (``NukeSurvivalLoadout.menu._LoadoutPanelHost.closeEvent``, which only
    reloads on a relaunch) does NOT re-prompt for this same close. When
    that override is present it sees the flag and accepts immediately;
    when it is absent (pre-relaunch) ``panel.close()`` just closes - either
    way the prompt fires exactly once, from here.
    """
    if should_close_panel(panel):
        setattr(panel, "_nsl_close_confirmed", True)
        panel.close()


def _on_import(panel) -> None:
    """Import a chain-format loadout file into the user's loadouts dir.

    Reads the source file as a chain ``LoadoutModel``, sanitises the
    destination stem from the source filename, and routes the write
    through ``loadout_ops.save`` (which also creates the folder + flips
    the dispatcher active pointer via ``save_as``). Blocked names land
    on ``on_blocked``; missing / malformed source files surface via the
    usual handler.
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
    """Export the active loadout as a Loadout FOLDER to a user path.

    Builds the chain ``LoadoutModel`` from the in-memory active model,
    then writes it as ``<chosen folder>/init.py`` (``prompt_export``
    returns the folder; ``write_atomic`` lazily creates it). The folder
    is a complete, droppable loadout - no rename needed on arrival.
    ``mark_clean`` is intentionally not toggled - Export does not commit
    the on-disk active loadout.
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
    """Panic button - engage / release through ``loadout_ops.set_panic``.

    Panic lives in the dispatcher (``~/.nuke/loadouts/init.py``) as the
    ``PANIC_MODE`` constant. The set_panic seam writes the dispatcher
    atomically and returns the updated state; we forward through the
    standard refresh path so the registry stays in sync.

    One Panic flip = one undo step. Panic is global state, but the
    entry lands on the stack of the loadout active at flip time (the
    stacks are per-loadout); replay re-runs ``set_panic``, and the
    panel refresh re-syncs the button.
    """
    registry = _registry(panel)
    previous = bool(getattr(registry.state, "panic", False))
    result = loadout_ops.set_panic(
        registry.loadouts_dir, engaged, registry.state
    )
    # set_panic returns an OpResult with model=None. The panel's
    # active_model should not change on a panic flip, so preserve it.
    forward = loadout_ops.OpResult(
        path=result.path,
        model=registry.active_model,  # type: ignore[arg-type]
        state=result.state,
    )
    registry.apply_op_result(forward)
    # Skip the push when the flip is a no-op (a stray signal that
    # re-asserts the current state must not burn an undo step).
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

    The wiring layer signals the intent and surfaces the entry back to the
    panel via ``registry.apply_undo``; replaying the entry's payload onto
    the active model is the registry's job.
    """
    registry = _registry(panel)
    stack = _active_stack(registry)
    if stack is None or not stack.can_undo:
        return
    entry = stack.undo()
    # Surface to the panel - the registry decides how to replay; for the
    # wiring layer the entry is an opaque payload.
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

    Without this, the Undo / Redo buttons never enable after the first pill
    toggle: the loadout-switch wiring only refreshes availability on
    loadout-switch events. ``refresh_from_registry`` in panel.py also
    invokes this helper so registry mutations that bypass the wiring layer
    keep the toolbar in sync.
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
    """Discovered plugin names sourced from *folder_path*, matched by
    NORMALISED path.

    ``folder_ops.add_folder`` stores the *normalised* path in the
    ``FolderDecl`` (``os.path.normpath``), and the scanner records that
    same normalised path as each plugin's ``source``. But the raw path
    handed back by the directory picker (``prompt_add_folder`` →
    ``QFileDialog.getExistingDirectory``) frequently carries a trailing
    slash (or ``.`` / ``..`` segments). Comparing the raw picked string
    directly against the normalised ``source`` matches NOTHING, which would
    keep a folder-add from landing any plugin in the ceremonial-save set -
    leaving ``is_active_dirty`` False and the Close button's unsaved-changes
    prompt silent. Adding a Plugins Folder to Custom is unambiguously a
    changed state (the discovered plugins are pending and only become real
    on Save), so the match must be robust to path representation.
    """
    target = canon_for_compare(folder_path)
    discovered = getattr(registry, "discovered_plugins", None) or {}
    return [
        name for name, plugin in discovered.items()
        if canon_for_compare(getattr(plugin, "source", "") or "") == target
    ]


def _persist_folder_authority(registry) -> None:
    """Write the Plugins Folder list to the dispatcher and sync every loadout.

    Thin delegate: the authority-write logic lives on
    :meth:`Registry.persist_folder_authority` so undo / redo replay and
    Revert can re-persist without reaching back into the wiring layer.
    Guarded so stand-in registries without the method stay usable (their
    folder list just isn't persisted, same as their other missing hooks).
    """
    persist = getattr(registry, "persist_folder_authority", None)
    if persist is not None:
        persist()


def _model_side_snapshot(registry) -> dict:
    """One side (previous or next) of a ``folder_op`` undo entry.

    The model and the ceremonial-save set are snapshotted wholesale -
    both are per-loadout, so replay can restore them without inverse
    logic. The folder list itself is NOT snapshotted; the entry carries
    a per-op delta instead (folders are global across loadouts, and a
    full-list restore from this loadout's stack could clobber folder
    changes made later from another loadout).
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

    Mirrors :func:`_push_undo_entry`: one folder add / remove / reorder
    is one undo step on the post-op active Loadout's stack. ``payload``
    carries the op kind + delta fields; ``previous`` is the
    :func:`_model_side_snapshot` taken before the op ran. The matching
    ``next`` side is captured here, after the op - post-rescan, so the
    reconcile pass's auto-enable entries are included for redo.
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
    """Add a Plugins Folder to the active loadout.

    Routing depends on what the active pointer currently names:

    * **First-run / Global / Custom** - no on-disk loadout file to
      mutate. The folder is added to the in-memory Custom slot, the
      scanner picks it up, pills surface; nothing is written to disk
      until the user clicks Save As. This is the Custom-as-wildcard
      behaviour: no silent "default" auto-materialisation to disk.
    * **Real named loadout** - read the chain ``LoadoutModel`` from
      disk, call ``folder_ops.add_folder_and_save``, refresh.
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
    # Previous side of the undo entry - captured before any mutation so
    # undo can restore the pre-add model + ceremonial-save set.
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
        # Adding the same folder twice is a no-op surfaced via the existing
        # folder list. Pass through to the panel so it can show a
        # user-readable message; the registry decides on the UI.
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
    # Mirror folder_ops result into the registry's user_plugin_dirs so
    # the scanner walks the just-added path on the rescan below.
    registry.user_plugin_dirs = [decl.path for decl in result.model.folders]
    registry.apply_op_result(new_op)
    # Folders are dispatcher-authoritative: persist the list to the
    # dispatcher and sync it into every loadout (not just this active one).
    _persist_folder_authority(registry)
    # Adding a folder must trigger a scan so pills for the newly-configured
    # plugins appear immediately. The apply_op_result above only syncs
    # settings + active_model and refreshes; without an explicit rescan the
    # grid stays empty.
    #
    # ``scan_and_refresh`` also reconciles newly-discovered plugins
    # into the active Loadout (auto-enabling any plugin not yet
    # decided in active or Global). The reconciliation lives there
    # rather than here so boot bootstrap and explicit rescan both
    # benefit from it - a folder added in a previous session whose
    # plugins were never saved into the active Loadout would
    # otherwise sit in "+N pending, can't save" limbo on every
    # restart. See ``Registry._reconcile_discovered_into_active``.
    scan = getattr(registry, "scan_and_refresh", None)
    if scan is not None:
        scan()
    # Ceremonial-save set - adding a Plugins Folder opens the Save
    # affordance for JUST the newly-added folder's plugins (other
    # plugins keep their saved-glow / value-based dirty state).
    # The scan above just populated ``discovered_plugins``; pick the
    # names whose source matches the folder we just added. Loadout
    # entries are preserved on remove, so a re-add against pre-saved
    # entries would leave M == D and Save greyed without this scoped
    # force-dirty. The set clears on Save / Save As / loadout switch, and
    # is scoped so unrelated saved-glow pills don't get cleared.
    mark = getattr(registry, "mark_plugins_force_dirty", None)
    if mark is not None:
        new_names = _discovered_names_from_folder(registry, chosen)
        if new_names:
            mark(new_names)
    # One folder-add = one undo step. ``add_folder`` PREPENDS, so record
    # the path's actual post-op index for redo. Pushed after the rescan
    # + force-dirty mark so the entry's "next" side captures the state
    # redo must restore.
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
    """Add a folder to the in-memory Custom slot without writing to disk.

    Used for the three "no on-disk loadout to mutate" cases - first-run
    (no active pointer), Global-active (read-only), Custom-already-
    active (in-memory wildcard). The folder is appended to
    ``registry.user_plugin_dirs`` so the scanner walks it, the state
    flips to Custom-active in memory so pills resolve under the
    user-loadout rules (default-enabled = green pending), and the
    newly-discovered plugins land in the ceremonial-save set so Save
    opens. No dispatcher write - Custom is the wildcard slot, the
    dispatcher only materialises on Save / Save As.

    Returns ``True`` when the folder landed (one undo entry pushed),
    ``False`` when validation rejected it.
    """
    from dataclasses import replace

    prev_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    was_custom = bool(
        registry.state
        and registry.state.active == DEFAULT_CUSTOM_LOADOUT_STEM
    )
    # Previous side of the undo entry. When this op auto-creates Custom
    # (Global / first-run view), the pre-op model is None - but undo
    # keeps Custom active (per-loadout stacks can't restore the Global
    # view's pointer), and a None model under a Custom pointer would
    # dead-end pill toggles. Snapshot the empty seeded Custom instead:
    # its sparse plugins resolve through Global fallback, so it renders
    # the same state the user was looking at before the add.
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
    # The fix for the close/reopen data loss: even on Custom (which never
    # writes its on/off to disk), the FOLDER is dispatcher-authoritative and
    # must persist. This writes it to the dispatcher + syncs all loadouts.
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

    # Pin the Revert folder baseline to the pre-op list when this op
    # created Custom: ``apply_op_result``'s switch-snapshot ran mid-op
    # (after the dirs mutation) and captured the post-add list, which
    # would make this very first add un-revertable.
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
    """Remove a folder from the in-memory Custom slot (no on-disk file).

    The remove-direction mirror of :func:`_add_folder_in_memory`, for the
    "no on-disk loadout to rewrite" case (Custom-active; first-run/Global are
    handled by the early return in :func:`_on_remove_folder`). Drops ``path``
    from ``user_plugin_dirs`` and re-runs the scanner so the removed folder's
    plugins leave ``discovered_plugins`` - and therefore the grid. No
    dispatcher / file write - Custom is the wildcard slot.

    Returns ``True`` when the folder was dropped (one undo entry
    pushed), ``False`` when ``path`` wasn't configured.
    """
    prev_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
    if path not in prev_dirs:
        return False
    previous = _model_side_snapshot(registry)
    registry.user_plugin_dirs = [p for p in prev_dirs if p != path]
    # Folders persist in the dispatcher even on Custom - removal updates the
    # authority and prunes the folder (and its plugin calls) from every loadout.
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
    # The Global Plugins row is read-only - only the Global
    # configuration controls it (the ``<nsl_root>/Global/`` convention,
    # ``NSL_GLOBAL_PLUGIN_DIRS``, or both). The FolderRow
    # already hides the ✕ control for ``is_global`` rows; this is the
    # defensive belt so a stray signal (test fixtures, future
    # regression) never ends up calling
    # ``folder_ops.remove_folder_and_save`` with the marker path.
    from NukeSurvivalLoadout.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
    if path == GLOBAL_PLUGINS_FOLDER_SENTINEL:
        return
    active_stem = registry.state.active if registry.state else ""
    # In-memory slots - Custom (explicit stem) AND the empty active pointer
    # that the strip RENDERS as "Custom(*)" (first-run with no Global layer,
    # or a post-reopen Custom session whose active pointer was never
    # persisted - see ``_active_strip_name``). None of these have an on-disk
    # loadout init.py to rewrite, but folders are dispatcher-authoritative
    # and must still be pruned + re-persisted.
    #
    # Route every in-memory slot to the in-memory prune, mirroring
    # ``_on_add_folder``'s first-run/Global/Custom routing. Blanket-returning
    # on the empty / RESERVED case would silently no-op a removal while the
    # strip shows "Custom(*)" with an empty active pointer: the folder would
    # stay in the dispatcher and its plugins in the grid (surviving a panel
    # close/reopen). The read-only Global *marker* row was already handled
    # above; user folders are never Global folders, so this never
    # touches the Global plugins dir.
    if (
        not active_stem
        or active_stem == RESERVED_LOADOUT_STEM
        or active_stem == DEFAULT_CUSTOM_LOADOUT_STEM
    ):
        # Only act on a path the user actually has configured - a genuine
        # first-run slot (no user folders) has no removable rows to click,
        # so an unmatched path is a harmless no-op rather than a redundant
        # dispatcher rewrite.
        user_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
        if path in user_dirs and _remove_folder_in_memory(registry, path):
            _sync_undo_toolbar(panel)
        return
    # Previous side of the undo entry - captured before the disk write
    # so undo can restore the pre-remove model + ceremonial-save set.
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
    # Dispatcher is the folder authority: persist the removal + prune the
    # folder's plugin calls from every loadout (no dangling var refs).
    _persist_folder_authority(registry)
    # Mirror the add-folder path: re-run the scanner so
    # ``discovered_plugins`` drops the removed folder's plugins.
    # Without this, ``_plugin_key_union`` keeps reading the stale
    # entries and the grid still shows pills sourced from the
    # removed folder until the next manual rescan or restart - a
    # removed folder must take its plugins out of the grid immediately,
    # not just out of the folder list.
    scan = getattr(registry, "scan_and_refresh", None)
    if scan is not None:
        scan()
    # One folder-remove = one undo step; the recorded index lets undo
    # re-insert the folder where it was.
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
    # The folder card may emit the Global marker as part of its
    # path order (it's the last row). The persisted user-folders
    # list contains real paths only - strip the marker before
    # handing the order to ``folder_ops.reorder_and_save``.
    from NukeSurvivalLoadout.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
    real_order = [p for p in new_order if p != GLOBAL_PLUGINS_FOLDER_SENTINEL]
    active_stem = registry.state.active if registry.state else ""
    if not active_stem or active_stem == RESERVED_LOADOUT_STEM:
        return
    if active_stem == DEFAULT_CUSTOM_LOADOUT_STEM:
        # Custom has no on-disk loadout to reorder. Folders are
        # dispatcher-authoritative now, so reorder the authority directly:
        # keep known paths in the requested order, persist + sync.
        prev_dirs = list(getattr(registry, "user_plugin_dirs", []) or [])
        previous = _model_side_snapshot(registry)
        known = set(prev_dirs)
        registry.user_plugin_dirs = [p for p in real_order if p in known]
        _persist_folder_authority(registry)
        scan = getattr(registry, "scan_and_refresh", None)
        if scan is not None:
            scan()
        # A reorder that lands in the same order is a no-op for undo -
        # don't burn a step on it.
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
    # Dispatcher is the folder authority: persist the new order + sync it
    # into every loadout (entries remapped by path so they stay correct).
    _persist_folder_authority(registry)
    # One reorder = one undo step; skip the push when the order didn't
    # actually change.
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

    Capture + restore grid selection around the ``set_keys`` call so the
    user's pill selection survives the folder-visibility flip. This path
    runs BEFORE the filter
    pipeline's parallel handler (both are connected to the same
    ``visibility_changed`` signal; Qt fires in connect order, and
    ``wire_events`` runs first). Without selection preservation
    here, ``set_keys`` would unconditionally clear ``grid._selected``
    and emit ``selection_changed([])`` - that empty signal would
    propagate to the selection bridge and to ``_maybe_clear_engaged``
    (which then greys every orange folder-select button when it
    deferred-fires). The pipeline's parallel handler also captures +
    restores, so both paths are now equivalent; the duplicate is
    retained because legacy test fixtures don't always wire the
    pipeline.
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
    # Global Plugins row eye-toggle - when the synthetic row is
    # marked hidden, every Global Plugin Name drops out of the
    # visible grid. Global plugins live in ``global_model``,
    # not ``discovered_plugins`` (the scanner only walks user dirs),
    # so the loop above misses them; this branch catches them via
    # the denormalised name set on the registry.
    from NukeSurvivalLoadout.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
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
    """Folder-row right-click → reveal that Plugins Folder in the OS file
    browser. The registry owns path resolution (and skips the Global
    marker); the wiring layer just forwards the click.
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
    # DORMANT - the diag/Log chip was retired; ``diagnostic_clicked`` is no
    # longer emitted (the chip is absent from the pill's bottom row). The
    # connection is harmless and kept so the dormant signal stays wired.
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
    from NukeSurvivalLoadout.boot.dispatcher import (
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

    # The panel mutates the active LoadoutFile IN MEMORY only. The edit
    # persists to disk solely on an explicit Save (``loadout_ops.save``);
    # a toggle marks the loadout dirty (the ``(*)`` marker + enabled Save
    # button) but does not touch disk. This preserves the explicit-save
    # design (dirty marker / Save / Revert / saved-baseline).
    #
    # Global active (or first-run, no active pointer): there is no model
    # to mutate, so the gesture auto-creates the in-memory Custom slot
    # seeded from the Global view and the toggle lands there - Global itself
    # is never silently edited. The switch applies FIRST
    # as its own op so the baseline snapshots the clean seed; the toggle
    # then reads as dirty and the strip shows ``Custom (*)``.
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

    # Push one undo entry on the active stack - multiple toggles in a row
    # each register as separate undo steps.
    _push_undo_entry(registry, was_global_active, plugin_name, previous, entry, result)

    # The active Loadout is dirty after any toggle; the strip's
    # ``set_dirty(True)`` slot drives Save's enabled state. No disk write
    # here - that happens only on an explicit Save (see the in-memory-only
    # note above).
    registry.apply_op_result(result)
    # apply_op_result only syncs settings + active_model; it never flips
    # _is_dirty. Without this explicit mark_clean(False), pill toggles
    # never produce the (*) suffix on the loadout strip's active row name.
    mark = getattr(registry, "mark_clean", None)
    if mark is not None:
        mark(False)
    # Every successful pill toggle pushed an undo entry; the toolbar must
    # reflect that the active stack is no longer empty so the Undo button
    # becomes clickable. The push side also clears the redo branch
    # (UndoStack.push), so Redo goes back to disabled regardless of prior
    # state.
    _sync_undo_toolbar(panel)


def _previous_entry(registry, plugin_name: str) -> Optional[PluginEntry]:
    """Resolve the Plugin's effective entry *before* this toggle.

    Order:
        1. If the active user Loadout has an explicit entry, use it.
        2. Else fall back to the Global Loadout (so a Global Plugin's
           gui_only is computed against the Global value, not a default).
        3. Else ``None`` - no previous record at all.
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
    """Push exactly one undo entry on the *post-op* active Loadout's stack.

    When the toggle was the first one against Global, the active Loadout is
    now the newly-created Custom - its stack is fresh, so the entry lands
    there. When the toggle was against an existing user Loadout, it lands
    on that Loadout's stack.

    A single-pill toggle pushes one undo entry. The undo stack is
    per-Loadout; the entry payload is opaque to the stack module (the
    registry owns the replay).
    """
    if not isinstance(registry.undo_stacks, UndoStackRegistry):
        return
    if result.model is None:
        # Global is now active (e.g. delete-of-active fallback). No stack
        # to push to; undo for file-level ops is out of scope anyway.
        return
    # ``result.model`` may be the new LoadoutModel (no ``.name``) or the
    # legacy LoadoutFile (``.name``). The undo stack keys by stem; reach
    # for ``.name`` defensively and fall back to the dispatcher's active
    # pointer when the new shape is in play.
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

    Clicking the info button loads the plugin's README into the Info tab
    AND activates that tab. The actual content loading is the registry's
    responsibility (it knows where the README lives); the wiring layer
    just forwards the click.
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

    # Case-B defence: no op may leave the active pointer on the hidden
    # user-land ``Global_Loadout`` (rename / duplicate / import routes
    # could produce it). Normalise to the read-only Global view and
    # persist so boot converges. The Save As staging path never reaches
    # here (it is intercepted in ``_on_save_as``).
    if (
        result.state is not None
        and result.state.active == GLOBAL_LOADOUT_DIR_NAME
        and getattr(registry, "global_loadout_copy_exists", False)
    ):
        from dataclasses import replace
        from NukeSurvivalLoadout.boot.dispatcher import write_dispatcher

        normalized = replace(result.state, active="")
        write_dispatcher(
            str(loadout_ops.dispatcher_path(registry.loadouts_dir)), normalized
        )
        result = loadout_ops.OpResult(
            path=result.path, model=None, state=normalized
        )

    # Derive the post-op stem from the result's folder path (or the
    # dispatcher state when the op didn't target a single folder).
    post_stem = result.state.active if result.state else ""
    if result.path is not None:
        post_stem = result.path.name

    if old_stem is not None and result.path is not None:
        # Rename: the on-disk folder has a new stem; the in-memory
        # ``name`` field is deliberately not rewritten (see
        # ``loadout_ops.rename`` - preserves any user-customised display
        # name). The undo stack follows the renamed folder.
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
