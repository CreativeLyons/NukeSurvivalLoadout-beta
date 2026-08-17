"""Production :class:`Registry` - the state carrier on ``panel.registry``.

Every wiring helper and widget module reads state off
``panel.registry``. This class is the production instance. It owns the
live :class:`DispatcherState`, the in-memory ``active_model``, the
resolved ``global_model``, the per-Loadout undo stacks, and the boot
snapshots the pending counts read.

The Registry never reaches into widget internals. It mutates its own
state and then calls the ``refresh_callback`` the panel installs at
attach time.

Plugin Name is the key. ``nuke`` is imported lazily inside the methods
that need it, never at module scope.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional

from nsl import compat
from nsl.constants import (
    GLOBAL_SOURCE_MARKER,
    RESERVED_LOADOUT_STEM,
)
from nsl.boot.dispatcher import DispatcherState
from nsl.data.loadout_file import LoadoutFile, PluginEntry
from nsl.domain import folder_ops, loadout_ops
from nsl.domain.scanner import Plugin, scan_folder
from nsl.paths import canon_for_compare
from nsl.domain.undo_stack import UndoStackRegistry
from nsl.ui import dialogs

__all__ = ["Registry"]


_log = logging.getLogger(__name__)


QtWidgets = compat.QtWidgets

# Payload shape is plugin / previous / next. The ``bulk_*`` kinds are
# pushed one per plugin, so they replay like a single pill toggle.
_PILL_SHAPED_UNDO_KINDS = frozenset({
    "pill_toggle",
    "bulk_enable",
    "bulk_disable",
    "bulk_invert",
    "bulk_set_gui_only",
    "bulk_clear_gui_only",
})


class Registry:
    """State carrier attached to ``LoadoutPanel.registry``.

    Built by :func:`nsl.ui.registry_bootstrap.build_registry_for_panel`.
    Every optional hook the wiring helpers reach for via ``getattr`` is
    implemented here, so nothing silently no-ops in production.
    """

    def __init__(
        self,
        *,
        loadouts_dir: Path,
        state: DispatcherState,
        active_model: Optional[LoadoutFile] = None,
        global_model: Optional[LoadoutFile] = None,
        undo_stacks: Optional[UndoStackRegistry] = None,
        refresh_callback: Optional[Callable[[], None]] = None,
        parent_widget: Optional[Any] = None,
        global_plugin_dirs: Optional[List[Path]] = None,
        user_plugin_dirs: Optional[List[str]] = None,
        global_loadout_copy_exists: bool = False,
        global_loadout_error: Optional[str] = None,
    ) -> None:
        self.loadouts_dir = Path(loadouts_dir)
        self.state = state
        # Absolute paths the scanner walks. They live as ``plugins_A``
        # and ``plugins_B`` vars in the loadout file, and the list is
        # empty when Global is active.
        self.user_plugin_dirs: List[str] = list(user_plugin_dirs or [])
        self.active_model = active_model
        self.global_model = global_model
        # Set when ``Global_Loadout/init.py`` will not read. Boot and
        # panel then load every Global folder, and the Summary tab shows
        # it as a warning.
        self.global_loadout_error: Optional[str] = global_loadout_error
        self.undo_stacks = undo_stacks if undo_stacks is not None else UndoStackRegistry()

        self.global_plugin_names: frozenset[str] = (
            frozenset(global_model.plugins.keys()) if global_model else frozenset()
        )

        # The panel rescan must walk the same folders the Global head
        # loaded at boot. Without them a Global pill's info button
        # reports the plugin as not found in the current scan.
        self.global_plugin_dirs: List[Path] = list(
            global_plugin_dirs or []
        )

        # True when a Global Loadout copy lives in the NSL Global folder.
        # That hides the user-land ``Global_Loadout`` from the dropdown
        # and turns Save As under that name into a staging save.
        self.global_loadout_copy_exists: bool = bool(global_loadout_copy_exists)

        # Never mutated after construction. The pill-diff baseline is not
        # these, it is the per-Loadout saved-baseline cache below.
        self.boot_active: Optional[LoadoutFile] = _clone_loadout(active_model)
        self.boot_global: Optional[LoadoutFile] = _clone_loadout(global_model)

        # What the chain loaded at boot, captured once on the first
        # ``scan_and_refresh``. It stays fixed, so a folder added or a
        # plugin toggled later still reads as "+N pending restart".
        self._session_loaded_snapshot: Optional[LoadoutFile] = None
        # Separates "the scan ran and nothing loaded" from "no scan yet".
        # Both leave the snapshot None, but only the second may fall back
        # to the boot models.
        self._session_scan_done: bool = False

        # Keyed by loadout stem. Captured here, on a loadout switch, and
        # on Save. Read through :attr:`active_saved_baseline`.
        self._saved_baselines: dict[str, LoadoutFile] = {}

        # Captured at the same moments as ``_saved_baselines``. Revert
        # restores the Plugins Folder list to it, so a folder add or
        # remove does not survive a Revert.
        self._saved_folder_baselines: dict[str, List[str]] = {}
        self._snapshot_baseline_for_active()

        # Dirty models held by stem when the user switches away without
        # saving, so a switch back restores the edits. Cleared when the
        # stem is saved, renamed or saved under a new name.
        self._pending_models: dict[str, LoadoutFile] = {}

        # Legacy flag. :attr:`is_active_dirty` is the real answer. This
        # exists so ``apply_op_result`` can read it before any
        # ``mark_clean`` call has fired.
        self._is_dirty = False

        self._refresh_callback = refresh_callback

        # Parent widget for Qt prompt dialogs. ``None`` is valid - Qt
        # tolerates parentless message boxes (the WM parents them).
        self._parent_widget = parent_widget

        # Per-folder UI state for ``folder_list_from``. Session only,
        # never written to disk.
        self._folder_visibility: dict[str, bool] = {}
        self._folder_health: dict[str, Any] = {}

        # Keyed by Plugin Name, filled by :meth:`scan_and_refresh`. The
        # grid shows a pill for every key, even before a Loadout enables
        # it.
        self.discovered_plugins: dict[str, Plugin] = {}

        # Names treated as dirty whatever the value comparison says.
        # Folder add is the trigger, scoped to that folder's plugins.
        # Loadout entries survive a folder remove, so a re-add matches
        # the baseline and would otherwise leave Save greyed.
        self._force_dirty_plugins: set[str] = set()

    # ------------------------------------------------------------------
    # Required surface - apply_op_result, on_blocked, compute_folder_removal
    # ------------------------------------------------------------------

    def apply_op_result(self, result: loadout_ops.OpResult) -> None:
        """Sync internal state from a successful op, then refresh widgets.

        ``state`` always carries forward. ``model`` carries forward when
        the op produced one, and ``None`` means Global is active after
        the op.

        The wiring layer bridges the op result first, so
        ``result.model`` is always a ``LoadoutFile`` or ``None`` here.
        """
        from nsl.constants import DEFAULT_CUSTOM_LOADOUT_STEM

        previous_active_stem = (
            self.state.active if self.state else ""
        )
        new_active_stem = (
            result.state.active if result.state else ""
        )
        switched = new_active_stem != previous_active_stem

        # The op changed the active stem without writing to disk. Hold
        # the dirty model under the outgoing stem so a switch back
        # restores it. ``is_active_dirty`` is a value comparison, so a
        # model toggled back to its saved state is not held.
        pure_switch = switched and result.path is None
        if pure_switch and self.is_active_dirty and self.active_model is not None:
            self._pending_models[previous_active_stem] = self.active_model

        # Custom must survive every switch away, including Save As,
        # where ``result.path`` is set. Always overwrite, so the held
        # model tracks the latest in-memory edits.
        if (
            switched
            and previous_active_stem == DEFAULT_CUSTOM_LOADOUT_STEM
            and self.active_model is not None
        ):
            self._pending_models[previous_active_stem] = self.active_model

        self.state = result.state
        if result.model is not None:
            self.active_model = result.model
        elif self.state.active == RESERVED_LOADOUT_STEM or not self.state.active:
            self.active_model = None

        restored_from_pending = False
        if pure_switch and new_active_stem in self._pending_models:
            self.active_model = self._pending_models[new_active_stem]
            restored_from_pending = True
        elif switched or result.path is not None:
            if result.path is not None and new_active_stem in self._pending_models:
                del self._pending_models[new_active_stem]

        # Snapshot only when the model is known to match disk.
        #   * ``result.path`` is set. Save, Save As, rename, duplicate
        #     or import just wrote the model out.
        #   * A switch that did not restore a held dirty model.
        #     Snapshotting a restored one would lock the edits in as
        #     the baseline. ``is_active_dirty`` would then read clean.
        if result.path is not None or (switched and not restored_from_pending):
            self._snapshot_baseline_for_active()

        # The force-dirty set clears on a disk write or a switch. A Save
        # ends the gesture, and a switch moves to another Loadout.
        if result.path is not None or switched:
            self._force_dirty_plugins.clear()

        self._is_dirty = self.is_active_dirty

        self._refresh()

    def on_blocked(self, blocked: loadout_ops.Blocked) -> None:
        """Surface a structured no-op. Logs only."""
        _log.info("op blocked: code=%s detail=%s", blocked.code, blocked.detail)

    def compute_folder_removal(self, path: str) -> Mapping[str, Iterable[str]]:
        """Pre-flight data for :func:`folder_ops.remove_folder_and_save`.

        Stub. Returns empty iterables until the live scan results are
        wired in.
        """
        return {"actively_loaded": (), "unique": ()}

    # ------------------------------------------------------------------
    # Prompt callbacks - text input + file pickers + confirmations
    # ------------------------------------------------------------------

    def prompt_rename(self, current_name: str) -> Optional[str]:
        return self._text_prompt(
            title="Rename Loadout",
            label="New name:",
            default=current_name,
        )

    def prompt_duplicate(self, current_name: str) -> Optional[str]:
        suggestion = current_name + " copy"
        return self._text_prompt(
            title="Duplicate Loadout",
            label="Name for the new Loadout:",
            default=suggestion,
        )

    def prompt_save_as(self) -> Optional[str]:
        return self._text_prompt(
            title="Save Loadout As",
            label="Name for the new Loadout:",
            default="",
        )

    def prompt_delete(self, current_name: str) -> bool:
        return bool(
            dialogs.confirm_delete_loadout(
                self._parent_widget, current_name
            )
        )

    def prompt_import(self) -> Optional[Path]:
        return self._open_file_prompt(
            title="Import Loadout",
            name_filter="Loadout files (*.py)",
        )

    def prompt_export(self) -> Optional[Path]:
        """Prompt for the export folder. The caller writes ``<folder>/init.py``.

        A Loadout is always a folder holding an ``init.py``, so Export
        asks for a folder. The default is ``<loadouts>/<active stem>``,
        or ``<loadouts>/Global_Loadout`` on Custom and Global.
        """
        from nsl.constants import (
            DEFAULT_CUSTOM_LOADOUT_STEM,
            GLOBAL_LOADOUT_DIR_NAME,
        )
        active = self.active_model
        if (
            active is None
            or active.name.lower() == DEFAULT_CUSTOM_LOADOUT_STEM.lower()
        ):
            default_stem = GLOBAL_LOADOUT_DIR_NAME
        else:
            default_stem = active.name
        default_path = str(Path(self.loadouts_dir) / default_stem)
        # ``(*)``, not ``""``. An empty filter gives a ``None`` glob,
        # which ``nuke.getFilename`` rejects, and the dialog wrapper's
        # catch-all then leaves a dead button.
        target = self._save_file_prompt(
            title="Export Loadout (pick or create the Loadout folder)",
            default_name=default_path,
            name_filter="Loadout folders (*)",
        )
        if target is None:
            return None
        # Normalise to the folder. Any other ``.py`` name means the
        # folder of that stem.
        if target.name.lower() == "init.py":
            target = target.parent
        elif target.suffix.lower() == ".py":
            target = target.with_suffix("")

        # ``Custom`` and ``Global`` are reserved stems. A folder under
        # either name collides once it lands in a loadouts dir.
        stem = target.name
        if stem.lower() in (
            DEFAULT_CUSTOM_LOADOUT_STEM.lower(),
            RESERVED_LOADOUT_STEM.lower(),
        ):
            QtWidgets.QMessageBox.warning(
                self._parent_widget,
                "Reserved name",
                f"`{stem}` is a reserved name. Please choose another "
                "folder name for the exported Loadout.",
            )
            return None
        return target

    def prompt_add_folder(self) -> Optional[str]:
        # Prefer Nuke's own browser inside Nuke. ``nuke.getFilename`` is
        # file-oriented but accepts a folder. The user navigates into it
        # and clicks OK. It ignores the panel geometry.
        try:
            import nuke  # noqa: PLC0415 - lazy import is the convention
        except ImportError:
            nuke = None  # type: ignore[assignment]

        if nuke is not None:
            try:
                directory = nuke.getFilename("Add Plugins Folder")
            except Exception:  # noqa: BLE001 - never block on a dialog quirk
                directory = None
            return directory or None

        options = (
            QtWidgets.QFileDialog.ShowDirsOnly
            | QtWidgets.QFileDialog.DontUseNativeDialog
        )
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self._parent_widget,
            "Add Plugins Folder",
            "",
            options,
        )
        return directory or None

    # ------------------------------------------------------------------
    # Optional hooks - apply_undo/redo, mark_clean, rescan, side panel
    # ------------------------------------------------------------------

    def apply_undo(self, entry: Mapping[str, Any]) -> None:
        """Replay an undo entry against ``active_model``.

        Delegates to :meth:`_replay_entry`, which lists the kinds it
        handles. An unknown kind is logged and skipped.
        """
        self._replay_entry(entry, direction="undo")

    def apply_redo(self, entry: Mapping[str, Any]) -> None:
        self._replay_entry(entry, direction="redo")

    @property
    def force_dirty_plugins(self) -> frozenset:
        """Names of plugins currently in the force-dirty set.

        Pill rendering reads this to drop the saved-state glow on just
        those plugins. Cleared by ``apply_op_result`` on a Save or a
        Loadout switch.
        """
        return frozenset(getattr(self, "_force_dirty_plugins", ()))

    def mark_plugins_force_dirty(self, plugin_names) -> None:
        """Add ``plugin_names`` to the force-dirty set.

        Folder add uses it to scope the re-confirm gesture to the new
        folder's plugins. Refreshes so the dirty marker, the Save
        button and the pill borders pick it up.
        """
        names = {n for n in plugin_names if isinstance(n, str)}
        if not names:
            return
        self._force_dirty_plugins.update(names)
        self._refresh()

    def set_folder_baseline(self, stem: str, dirs: Iterable[str]) -> None:
        """Pin the Revert folder baseline for ``stem`` to ``dirs``.

        The folder add that auto-creates Custom snapshots mid-op, after
        the folder list already changed, so the first add would not be
        revertable. The wiring layer pins the pre-op list here.
        """
        self._saved_folder_baselines[stem] = list(dirs)

    def mark_clean(self, clean: bool) -> None:
        """Set the dirty flag. The refresh path feeds the strip's
        ``set_dirty`` slot, which draws the ``(*)`` on the active row."""
        self._is_dirty = not clean
        self._refresh()

    def revert_active_to_baseline(self) -> bool:
        """Discard in-memory edits on the active Loadout.

        Restores three things to the last baseline capture, taken at
        the last Save or Loadout switch:

        * the model, cloned back from :attr:`active_saved_baseline`
        * the Plugins Folder list, then a re-persist and a rescan
        * the force-dirty set, cleared

        Held dirty edits for this stem are dropped too. Returns
        ``True`` when a revert happened, ``False`` when there was
        nothing to do.
        """
        if self.active_model is None:
            return False
        baseline = self.active_saved_baseline
        if baseline is None:
            return False
        stem = self._active_stem()
        folder_baseline = self._saved_folder_baselines.get(stem)
        current_dirs = list(getattr(self, "user_plugin_dirs", []) or [])
        dirs_differ = (
            folder_baseline is not None
            and list(folder_baseline) != current_dirs
        )
        if (
            self.active_model == baseline
            and not dirs_differ
            and not self._force_dirty_plugins
        ):
            return False
        if dirs_differ:
            self.user_plugin_dirs = list(folder_baseline)
            self.persist_folder_authority()
            # Rescan before the model restore below. The scan's
            # reconcile pass may write auto-enable entries, and the
            # baseline clone must win.
            self.scan_and_refresh()
        # Clone, or later edits reach the baseline through the shared
        # plugins dict.
        self.active_model = _clone_loadout(baseline)
        if stem in self._pending_models:
            del self._pending_models[stem]
        self._force_dirty_plugins.clear()
        self._is_dirty = False
        self._refresh()
        return True

    @property
    def is_active_dirty(self) -> bool:
        """Whether the active Loadout differs from its on-disk state.

        A value comparison, not a changed-since-last-save flag. Raw
        dict equality is not enough. A plugin toggled off then on
        leaves an explicit entry equal to the default, while the
        baseline has none. Both sides therefore go through
        :func:`_normalised_plugins` first. Falls back to the legacy
        ``_is_dirty`` flag when no baseline exists yet.
        """
        if getattr(self, "_force_dirty_plugins", None):
            return True
        if self.active_model is None:
            return False
        baseline = self.active_saved_baseline
        if baseline is None:
            return getattr(self, "_is_dirty", False)
        return _normalised_plugins(
            self.active_model, self.global_model
        ) != _normalised_plugins(baseline, self.global_model)

    @property
    def resolved_active_for_diff(self) -> Optional[LoadoutFile]:
        """LoadoutFile carrying the active Loadout's effective state.

        Global is the base and the active Loadout's entries overlay
        it, the standard sparse-diff rule. A key the active Loadout
        does not carry falls back to Global.

        The banner and the counter diff this against
        :attr:`session_loaded_baseline`. Without the fallback a sparse
        active model, such as Custom after ``reset_global_to_default``
        empties its plugins dict, reads as "every Global plugin
        removed" and reports a phantom -N count.

        Returns ``None`` only when Global, the active model and the
        scan are all empty.
        """
        if (
            self.global_model is None
            and self.active_model is None
            and not self.discovered_plugins
        ):
            return None
        # An active entry for a plugin that is neither discovered nor
        # in Global is an orphan. Its folder was removed. Counting it
        # gives a phantom "+N would load on restart" for a plugin that
        # cannot load. The entry stays in the file for a re-add.
        loadable_keys: set[str] = set(self.discovered_plugins.keys())
        if self.global_model is not None:
            loadable_keys.update(self.global_model.plugins.keys())

        # Always empty. A plugin whose init.py raises crashes Nuke
        # before the panel opens. There is no per-plugin load result to
        # filter on, and pill states are Enabled, Disabled or Missing.
        failed_now: frozenset[str] = frozenset()

        plugins: dict[str, PluginEntry] = {}
        if self.global_model is not None:
            for name, entry in self.global_model.plugins.items():
                if name in failed_now:
                    continue
                plugins[name] = entry
        if self.active_model is not None:
            for name, entry in self.active_model.plugins.items():
                if name in failed_now:
                    continue
                if name in loadable_keys:
                    plugins[name] = entry
        # Plugins no Loadout has touched take the sparse-diff default:
        #   * Global active - user plugins default to disabled.
        #   * A user Loadout active - default enabled.
        # Same rule as :func:`nsl.ui.state.pill_state_from`. Without it
        # a folder of 3 new plugins would count as +1.
        global_is_active = self.active_model is None
        global_set = (
            frozenset(self.global_model.plugins.keys())
            if self.global_model is not None
            else frozenset()
        )
        for name in self.discovered_plugins.keys():
            if name in plugins:
                continue
            if name in failed_now:
                continue
            if global_is_active and name not in global_set:
                plugins[name] = PluginEntry(enabled=False, gui_only=False)
            else:
                plugins[name] = PluginEntry(enabled=True, gui_only=False)
        # Informational only. The diff math never reads the name.
        name = (
            self.active_model.name
            if self.active_model is not None
            else (self.global_model.name if self.global_model is not None else "")
        )
        return LoadoutFile(name=name, plugins=plugins)

    def count_diverged_global_plugins(self) -> int:
        """Count Global Plugins whose active entry differs from Global.

        A key the active Loadout omits falls back to Global and never
        counts. Returns ``0`` when Global is active or no Global layer
        exists. The Reset Global Plugins button gates on this.
        """
        if self.global_model is None:
            return 0
        if self.active_model is None:
            return 0
        count = 0
        for name in self.global_plugin_names:
            active_entry = self.active_model.plugins.get(name)
            if active_entry is None:
                continue
            global_entry = self.global_model.plugins.get(name)
            if active_entry != global_entry:
                count += 1
        return count

    @property
    def dirty_stems(self) -> frozenset[str]:
        """Stems of Loadouts holding unsaved edits after a switch away.

        The active Loadout's own state is :attr:`is_active_dirty`.
        This covers the other dropdown rows, so the strip can put
        ``(*)`` on a Loadout that is not active, such as Custom.
        """
        return frozenset(self._pending_models)

    def rescan(self) -> None:
        """Rescan every Plugins Folder through :meth:`scan_and_refresh`."""
        self.scan_and_refresh()

    def persist_folder_authority(self) -> None:
        """Write the Plugins Folder list to the dispatcher and sync every loadout.

        Folders are global state, so the dispatcher owns the list. This
        runs after any folder add, remove or reorder, after undo or
        redo of one, and on Revert.

        ``panic`` and ``active`` come from the in-memory :attr:`state`.
        A disk re-read brought back the stale ``Custom`` pointer that
        the bootstrap had already normalised away.

        ``sync_folders_to_loadouts`` runs first and is transactional,
        so the folder authority never advances past a stale Loadout.
        """
        from dataclasses import replace

        from nsl.constants import DEFAULT_CUSTOM_LOADOUT_STEM
        from nsl.boot.dispatcher import (
            read_dispatcher,
            write_dispatcher,
        )
        from nsl.boot.loadout_file import (
            FolderDecl,
            sync_folders_to_loadouts,
        )

        loadouts_dir = self.loadouts_dir
        dispatcher = str(loadout_ops.dispatcher_path(loadouts_dir))
        dirs = list(getattr(self, "user_plugin_dirs", []) or [])
        canonical = [
            FolderDecl(var=_canonical_folder_var(i), path=path)
            for i, path in enumerate(dirs)
        ]

        result = sync_folders_to_loadouts(loadouts_dir, canonical)
        if result.skipped:
            for stem, reason in result.skipped:
                _log.warning(
                    "folder sync skipped loadout %r (left on stale folders): %s",
                    stem,
                    reason,
                )

        # Custom is in-memory only, so it never persists as the pointer.
        base_state = getattr(self, "state", None)
        if base_state is None:
            base_state = read_dispatcher(dispatcher)
        active = base_state.active
        if active == DEFAULT_CUSTOM_LOADOUT_STEM:
            active = ""
        write_state = replace(base_state, active=active, folders=canonical)
        write_dispatcher(dispatcher, write_state)
        if getattr(self, "state", None) is not None:
            self.state.folders = list(canonical)

    def scan_and_refresh(self) -> None:
        """Scan every configured Plugins Folder and refresh the UI.

        Merges each :func:`nsl.domain.scanner.scan_folder` result into
        ``discovered_plugins``. Later folders win on a Plugin Name
        collision, and a folder that fails is dropped with a warning.

        Afterwards, any Plugin on disk with no decision in the active
        Loadout and none in Global is auto-enabled. Folder add, boot
        bootstrap and manual rescan all pass through here.
        """
        live: dict[str, Plugin] = {}

        # Global folders walk first, so a user folder shadows them on a
        # name collision. Global plugins carry ``GLOBAL_SOURCE_MARKER``
        # as their source, and ``plugin.path`` keeps the real path for
        # the README reader.
        for path in self.global_plugin_dirs:
            try:
                plugins = scan_folder(path)
            except (OSError, ValueError):
                _log.warning(
                    "scan_folder failed for Global layer %s; skipping",
                    path, exc_info=True,
                )
                continue
            for plugin in plugins:
                live[plugin.name] = dataclasses.replace(
                    plugin, source=GLOBAL_SOURCE_MARKER
                )

        for path in self.user_plugin_dirs:
            try:
                plugins = scan_folder(path)
            except (OSError, ValueError):
                _log.warning("scan_folder failed for %s; skipping", path, exc_info=True)
                continue
            for plugin in plugins:
                live[plugin.name] = plugin

        self.discovered_plugins = dict(live)

        # Freeze the boot baseline on the first scan. The gate is
        # ``_session_scan_done``, not the snapshot, so a legitimate
        # None sticks and later edits still read as pending restart.
        if not self._session_scan_done:
            self._session_loaded_snapshot = self._compute_loaded_snapshot()
            self._session_scan_done = True

        # The reconcile refreshes on its own when it changed something.
        # Refresh here otherwise, so the grid picks up the new keys.
        if not self._reconcile_discovered_into_active():
            self._refresh()

    def _reconcile_discovered_into_active(self) -> bool:
        """Auto-enable any discovered Plugin that has no decision yet.

        A decision is an entry in the active Loadout's plugins map or
        in the resolved Global map. Without one the Plugin is stuck.
        ``resolved_active_for_diff`` defaults it to enabled and the
        banner counts it, but ``is_active_dirty`` does not see it, so
        Save stays locked. Writing an explicit ``enabled=True`` entry
        unlocks Save.

        Returns ``True`` when the active model changed, so the caller
        can skip its own refresh.
        """
        from nsl.constants import RESERVED_LOADOUT_STEM

        active_stem = self.state.active if self.state else ""
        if not active_stem or active_stem == RESERVED_LOADOUT_STEM:
            return False
        if self.active_model is None:
            return False

        global_keys = (
            set(self.global_model.plugins.keys())
            if self.global_model is not None
            else set()
        )
        already_decided = set(self.active_model.plugins.keys()) | global_keys
        truly_new = [
            name for name in self.discovered_plugins.keys()
            if name not in already_decided
        ]
        if not truly_new:
            return False

        new_plugins = dict(self.active_model.plugins)
        for name in truly_new:
            new_plugins[name] = PluginEntry(enabled=True, gui_only=False)
        self.active_model = LoadoutFile(
            name=self.active_model.name, plugins=new_plugins
        )
        # Refresh the baseline, or the auto-added entries make the
        # Loadout read dirty on every restart. Folder add still lights
        # Save up, through the force-dirty set the wiring layer marks.
        self._snapshot_baseline_for_active()
        self._refresh()
        return True

    def on_pill_info(self, plugin_name: str) -> None:
        """Pill info button. Shows the plugin README in the Info tab.

        A missing plugin entry, a missing README or an unreadable file
        all put a placeholder in the tab instead of raising.
        """
        plugin = self.discovered_plugins.get(plugin_name)
        if plugin is None:
            body = f"(plugin '{plugin_name}' not found in current scan)"
        else:
            body = self._read_plugin_readme(plugin.path)

        provenance = (
            f"from {plugin.source}" if plugin is not None else "(unknown source)"
        )

        side_panel = self._side_panel()
        if side_panel is None:
            _log.debug("pill info: no side panel attached")
            return

        # Lazy import - keeps the headless test import path light.
        from nsl.ui.side_panel import PluginDetail

        side_panel.show_info(
            PluginDetail(
                plugin_name=plugin_name,
                provenance=provenance,
                body=body,
            )
        )
        self._push_active_chips(info_plugin=plugin_name, menu_plugin=None)

    def on_pill_menu(self, plugin_name: str) -> None:
        """Pill menu button. Shows the plugin's ``menu.py`` in the Menu tab.

        The chip is always clickable. A folder with no ``menu.py`` gets
        a message in the tab. Display only, the file is never edited
        here.
        """
        side_panel = self._side_panel()
        if side_panel is None:
            _log.debug("pill menu: no side panel attached")
            return

        plugin = self.discovered_plugins.get(plugin_name)
        if plugin is None:
            body = f"(plugin '{plugin_name}' not found in current scan)"
            menu_path = None
        else:
            body, menu_path = self._read_plugin_menu(plugin.path)

        provenance = (
            f"from {plugin.source}" if plugin is not None else "(unknown source)"
        )

        from nsl.ui.side_panel import PluginDetail

        side_panel.show_menu(
            PluginDetail(
                plugin_name=plugin_name,
                provenance=provenance,
                body=body,
                source_path=menu_path,
            )
        )
        self._push_active_chips(info_plugin=None, menu_plugin=plugin_name)

    def on_side_panel_refresh(self) -> None:
        """Re-read the README and ``menu.py`` for the plugins on show.

        Wired to the side panel's refresh button, so the user picks up
        external edits without a full rescan. Both tabs refresh in
        place, whichever one is active.
        """
        side_panel = self._side_panel()
        if side_panel is None:
            return

        from nsl.ui.side_panel import PluginDetail

        info_detail = getattr(side_panel, "_info_plugin", None)
        if info_detail is not None:
            name = info_detail.plugin_name
            plugin = self.discovered_plugins.get(name)
            if plugin is None:
                body = f"(plugin '{name}' not found in current scan)"
            else:
                body = self._read_plugin_readme(plugin.path)
            provenance = (
                f"from {plugin.source}" if plugin is not None else "(unknown source)"
            )
            try:
                side_panel.show_info(
                    PluginDetail(
                        plugin_name=name, provenance=provenance, body=body
                    ),
                    activate=False,
                )
            except Exception:
                pass

        menu_detail = getattr(side_panel, "_menu_plugin", None)
        if menu_detail is not None:
            name = menu_detail.plugin_name
            plugin = self.discovered_plugins.get(name)
            if plugin is None:
                body, menu_path = (
                    f"(plugin '{name}' not found in current scan)",
                    None,
                )
            else:
                body, menu_path = self._read_plugin_menu(plugin.path)
            provenance = (
                f"from {plugin.source}" if plugin is not None else "(unknown source)"
            )
            try:
                side_panel.show_menu(
                    PluginDetail(
                        plugin_name=name,
                        provenance=provenance,
                        body=body,
                        source_path=menu_path,
                    ),
                    activate=False,
                )
            except Exception:
                pass

    def on_pill_open_folder(self, plugin_name: str) -> None:
        """Pill right-click "Open Plugin Folder". Reveals the source folder.

        Nothing happens when the plugin is not in the current scan.
        ``open_in_file_browser`` also rejects a path that is gone, so a
        stale entry logs a warning instead of opening an empty window.
        """
        plugin = self.discovered_plugins.get(plugin_name)
        if plugin is None:
            _log.debug("open folder: plugin %r not in current scan", plugin_name)
            return
        from nsl.ui.reveal import open_in_file_browser

        open_in_file_browser(plugin.path)

    def on_pill_diagnostic(self, plugin_name: str) -> None:
        """Pill diagnostic button. Shows the side panel Log tab.

        NSL captures no per-plugin load traceback. A failing init.py
        crashes the interpreter before any hook can record it. The chip
        is no longer actionable, so this only catches a stale signal.
        """
        side_panel = self._side_panel()
        if side_panel is None:
            return

        from nsl.ui.side_panel import PluginDetail

        plugin = self.discovered_plugins.get(plugin_name)
        provenance = (
            f"from {plugin.source}" if plugin is not None else "(unknown source)"
        )
        body = (
            "(no diagnostic captured - NSL no longer wraps plugin loads in "
            "its own try/except; if Nuke crashed on this plugin, check the "
            "terminal output that preceded the panel for the traceback)"
        )
        side_panel.show_log(
            PluginDetail(
                plugin_name=plugin_name,
                provenance=provenance,
                body=body,
            )
        )
        self._push_active_chips(info_plugin=None, menu_plugin=None)

    def _side_panel(self):
        """Resolve the panel's :class:`SidePanel` through the parent widget.

        ``attach_parent_widget`` hands over the whole panel. Returns
        ``None`` when nothing is attached.
        """
        parent = getattr(self, "_parent_widget", None)
        if parent is None:
            return None
        return getattr(parent, "side_panel", None)

    def _push_active_chips(self, *, info_plugin, menu_plugin) -> None:
        """Highlight at most one chip on at most one pill in the grid.

        Each caller passes its own plugin name and ``None`` for the
        other, so the chip lit before clears in the same paint pass.
        Paired with ``LoadoutPanel._apply_active_chips_to_grid``.
        """
        parent = getattr(self, "_parent_widget", None)
        if parent is None:
            return
        hook = getattr(parent, "_apply_active_chips_to_grid", None)
        if hook is not None:
            hook(info_plugin, menu_plugin)

    def _read_plugin_readme(self, plugin_dir: str) -> str:
        """Locate and read the plugin's README.md (case-insensitive)."""
        import os

        if not plugin_dir or not os.path.isdir(plugin_dir):
            return "(plugin folder unreadable)"
        for name in os.listdir(plugin_dir):
            if name.lower() == "readme.md":
                try:
                    with open(
                        os.path.join(plugin_dir, name),
                        "r",
                        encoding="utf-8",
                    ) as fh:
                        return fh.read()
                except OSError:
                    return "(README found but could not be read)"
        return "(no README.md in this plugin folder)"

    def _read_plugin_menu(self, plugin_dir: str):
        """Locate and read the plugin's ``menu.py``, case-insensitive.

        Returns ``(body, path)``. ``body`` is the source, or a message
        on a miss. ``path`` is ``None`` unless the file was read, so
        the Menu tab's Open button knows there is a file to open.
        """
        import os

        if not plugin_dir or not os.path.isdir(plugin_dir):
            return "No menu.py found in this plugin's folder.", None
        for name in os.listdir(plugin_dir):
            if name.lower() == "menu.py":
                full = os.path.join(plugin_dir, name)
                try:
                    with open(full, "r", encoding="utf-8") as fh:
                        return fh.read(), full
                except OSError:
                    return "(menu.py found but could not be read)", None
        return "No menu.py found in this plugin's folder.", None

    def on_folder_visibility(self, path: str, visible: bool) -> None:
        """Eye-toggle on a folder card row. Session-only state."""
        self._folder_visibility[path] = visible
        self._refresh()

    def on_folder_select(self, path: str) -> list:
        """Return the Plugin Names sourced from ``path``.

        The Global Plugins row passes
        :data:`GLOBAL_PLUGINS_FOLDER_SENTINEL` and resolves out of
        ``global_model.plugins``. A name a user folder also carries is
        left out, because the scanner's shadowing rule gives that
        folder the pill.
        """
        from nsl.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
        discovered = self.discovered_plugins or {}
        if path == GLOBAL_PLUGINS_FOLDER_SENTINEL:
            if self.global_model is None:
                return []
            return [
                name for name in self.global_model.plugins.keys()
                if getattr(
                    discovered.get(name), "source", GLOBAL_SOURCE_MARKER
                ) == GLOBAL_SOURCE_MARKER
            ]
        return [
            name for name, plugin in discovered.items()
            if getattr(plugin, "source", None) == path
        ]

    def on_folder_health(self, path: str) -> None:
        _log.debug("folder health inspected: %s", path)

    def on_folder_open(self, path: str) -> None:
        """Folder-row right-click "Open Folder". Reveals *path*.

        The Global Plugins row carries a marker, not a real path, so it
        is skipped here. The row also hides its own context menu.
        """
        from nsl.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL

        if not path or path == GLOBAL_PLUGINS_FOLDER_SENTINEL:
            _log.debug("open folder: skipping non-path row %r", path)
            return
        from nsl.ui.reveal import open_in_file_browser

        open_in_file_browser(path)

    def on_folder_already_configured(self, path: str) -> None:
        _log.info("folder already configured: %s", path)

    def on_folder_validation_error(self, exc: Exception) -> None:
        _log.warning("folder validation error: %s", exc)

    @property
    def folder_visibility(self) -> Mapping[str, bool]:
        """Read-only view used by the panel's refresh path."""
        return dict(self._folder_visibility)

    @property
    def folder_health(self) -> Mapping[str, Any]:
        return dict(self._folder_health)

    # ------------------------------------------------------------------
    # Attachment helpers
    # ------------------------------------------------------------------

    def attach_refresh(self, callback: Callable[[], None]) -> None:
        """Install or replace the panel-side refresh callback.

        The panel calls it in ``__init__``, after the widget tree
        exists and before ``_wire_signals``. Idempotent.
        """
        self._refresh_callback = callback

    def attach_parent_widget(self, widget: Any) -> None:
        self._parent_widget = widget

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._refresh_callback is None:
            return
        try:
            self._refresh_callback()
        except Exception:  # noqa: BLE001 - refresh must never break ops.
            _log.exception("panel refresh raised; state mutation kept.")

    # ------------------------------------------------------------------
    # Saved baseline - per-Loadout snapshot of the on-disk state.
    # ------------------------------------------------------------------

    def _snapshot_baseline_for_active(self) -> None:
        """Capture the active Loadout's baseline for the diff math.

        Called from ``__init__`` and from ``apply_op_result`` on a
        switch, save, import or rename. For a named Loadout the model
        equals disk at that moment.

        ``Global`` and ``Custom`` baseline against the Global model
        instead. Global is its own baseline, and Custom never persists.
        """
        from nsl.constants import DEFAULT_CUSTOM_LOADOUT_STEM

        stem = self._active_stem()
        self._saved_folder_baselines[stem] = list(
            getattr(self, "user_plugin_dirs", []) or []
        )
        if stem in (RESERVED_LOADOUT_STEM, DEFAULT_CUSTOM_LOADOUT_STEM):
            self._saved_baselines[stem] = _clone_loadout(self.global_model) or LoadoutFile(
                name=stem, plugins={}
            )
            return
        cloned = _clone_loadout(self.active_model)
        if cloned is not None:
            self._saved_baselines[stem] = cloned

    def _active_stem(self) -> str:
        stem = ""
        if self.state is not None:
            stem = self.state.active or ""
        return stem or RESERVED_LOADOUT_STEM

    @property
    def active_saved_baseline(self) -> Optional[LoadoutFile]:
        """The saved-on-disk baseline for the active Loadout.

        Returns ``None`` when nothing has been captured yet. The banner
        count and the pill tint read :attr:`session_loaded_baseline`
        instead, which is fixed at boot.
        """
        return self._saved_baselines.get(self._active_stem())

    def _compute_loaded_snapshot(self) -> Optional[LoadoutFile]:
        """Freeze what this Nuke session actually loaded.

        Three sources, in order:

        * the boot manifest ``nuke._nsl_loaded_session``, stamped at
          each ``pluginAddPath`` by the ``nsl_*`` helpers
        * the live ``nuke.pluginPath()``, matched against
          ``discovered_plugins`` by folder path
        * the effective enabled set, for headless runs and tests

        The manifest survives a mid-session folder delete, so the
        Loaded count never under-reports. The effective enabled set is
        last because it answers "what will load next restart". Called
        once from the first scan, then frozen.
        """
        manifest = self._nsl_session_manifest()
        if manifest is not None:
            plugins: dict[str, PluginEntry] = {}
            for item in manifest:
                name = item.get("name")
                if not name:
                    continue
                # A recorded name had ``pluginAddPath`` called on it, so
                # it loaded. Do not read the model's entry here. In
                # panic the Global layer loads a name the user
                # disabled, and the Summary would then undercount it.
                plugins[name] = PluginEntry(
                    enabled=True, gui_only=bool(item.get("gui"))
                )
            return LoadoutFile(name="<session>", plugins=plugins) if plugins else None

        loaded_paths = self._nuke_loaded_paths()
        if loaded_paths is None:
            resolved = self.resolved_active_for_diff
            if resolved is None:
                return None
            plugins = {
                name: entry
                for name, entry in resolved.plugins.items()
                if entry.enabled
            }
            return LoadoutFile(name="<session>", plugins=plugins) if plugins else None

        # A discovered plugin counts as loaded when its folder is on
        # Nuke's live plugin path. ``resolved`` gives the entry shape,
        # and anything it misses gets a plain enabled entry.
        resolved = self.resolved_active_for_diff
        resolved_entries = resolved.plugins if resolved is not None else {}
        plugins: dict[str, PluginEntry] = {}
        for name, plugin in self.discovered_plugins.items():
            if canon_for_compare(plugin.path) in loaded_paths:
                plugins[name] = resolved_entries.get(
                    name, PluginEntry(enabled=True, gui_only=False)
                )
        return LoadoutFile(name="<session>", plugins=plugins) if plugins else None

    @staticmethod
    def _nuke_loaded_paths() -> Optional[set]:
        """Return the folders on Nuke's live plugin path, canonicalised.

        Keys are ``canon_for_compare`` forms, so a drive-letter or
        slash-case difference does not miss. ``None`` means Nuke is
        unavailable. An empty set means Nuke loaded nothing.
        """
        try:
            import nuke  # noqa: PLC0415 - only present inside a Nuke session
        except ImportError:
            return None
        plugin_path = getattr(nuke, "pluginPath", None)
        if plugin_path is None:
            return None
        try:
            return {canon_for_compare(p) for p in plugin_path()}
        except Exception:  # noqa: BLE001 - never let a Nuke API quirk break the panel
            return None

    @staticmethod
    def _nsl_session_manifest() -> Optional[list]:
        """Return the boot-time load manifest, or ``None`` when absent.

        The loadout file's ``nsl_*`` helpers stamp
        ``nuke._nsl_loaded_session`` with ``{"name", "path", "gui"}``
        dicts at each ``pluginAddPath``. See
        :data:`loadout_file._HELPER_DEF`. ``None`` tells the caller to
        fall back to the live ``pluginPath``.
        """
        try:
            import nuke  # noqa: PLC0415 - only present inside a Nuke session
        except ImportError:
            return None
        rec = getattr(nuke, "_nsl_loaded_session", None)
        return rec if isinstance(rec, list) else None

    @property
    def session_loaded_baseline(self) -> Optional[LoadoutFile]:
        """LoadoutFile carrying what NSL loaded at boot.

        The first boot ``scan_and_refresh`` freezes
        :meth:`_compute_loaded_snapshot` into
        ``_session_loaded_snapshot``, and that snapshot is the
        baseline. It includes the scan-loaded defaults a sparse loadout
        file leaves out.

        Falls back to the boot models when no scan has run. ``None``
        means an empty baseline, as in :func:`pending_diff`.
        """
        # Return the snapshot even when it is None. None means nothing
        # loaded, and falling through would claim the declared enabled
        # set loaded when it did not.
        if self._session_scan_done:
            return self._session_loaded_snapshot

        boot_eff: dict[str, PluginEntry] = {}
        if self.boot_global is not None:
            boot_eff.update(self.boot_global.plugins)
        if self.boot_active is not None:
            boot_eff.update(self.boot_active.plugins)
        if not boot_eff:
            return None
        # The baseline is what loaded, not what was declared. A
        # disabled entry never reaches the walker.
        plugins = {
            name: entry
            for name, entry in boot_eff.items()
            if entry.enabled
        }
        if not plugins:
            return None
        return LoadoutFile(name="<session>", plugins=plugins)

    def _text_prompt(
        self, *, title: str, label: str, default: str
    ) -> Optional[str]:
        text, ok = QtWidgets.QInputDialog.getText(
            self._parent_widget, title, label, text=default
        )
        if not ok:
            return None
        text = text.strip()
        return text or None

    def _open_file_prompt(
        self, *, title: str, name_filter: str
    ) -> Optional[Path]:
        # See ``prompt_add_folder`` for the Nuke-browser preference.
        try:
            import nuke  # noqa: PLC0415
        except ImportError:
            nuke = None  # type: ignore[assignment]

        if nuke is not None:
            try:
                # ``nuke.getFilename`` takes one glob, so pull the first
                # one out of the Qt filter string.
                pattern = _glob_from_qt_filter(name_filter)
                chosen = nuke.getFilename(title, pattern)
            except Exception:  # noqa: BLE001 - never block on a dialog quirk
                chosen = None
            return Path(chosen) if chosen else None

        filename, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self._parent_widget,
            title,
            "",
            name_filter,
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        return Path(filename) if filename else None

    def _save_file_prompt(
        self, *, title: str, default_name: str, name_filter: str
    ) -> Optional[Path]:
        # See ``prompt_add_folder`` for the Nuke-browser preference.
        try:
            import nuke  # noqa: PLC0415
        except ImportError:
            nuke = None  # type: ignore[assignment]

        if nuke is not None:
            # ``type="save"`` switches to the save dialog, which
            # accepts a name that does not exist yet. Older builds
            # reject the kwarg, so there is a positional retry below.
            try:
                pattern = _glob_from_qt_filter(name_filter)
                try:
                    chosen = nuke.getFilename(
                        title, pattern, default_name, type="save"
                    )
                except TypeError:
                    chosen = nuke.getFilename(title, pattern, default_name)
            except Exception:  # noqa: BLE001 - never block on a dialog quirk
                chosen = None
            return Path(chosen) if chosen else None

        filename, _selected = QtWidgets.QFileDialog.getSaveFileName(
            self._parent_widget,
            title,
            default_name,
            name_filter,
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        return Path(filename) if filename else None

    def _replay_entry(
        self, entry: Mapping[str, Any], *, direction: str, refresh: bool = True
    ) -> None:
        """Apply, as undo or redo, a single undo entry.

        ``pill_toggle`` and the ``bulk_*`` kinds replay in memory. Disk
        is only touched on Save. A coalesced bulk entry replays its
        sub-entries, reversed for undo and in order for redo, with one
        refresh at the end.

        ``folder_op``, ``panic_toggle`` and ``model_reset`` route to
        their own helpers. The first two write through to the
        dispatcher, because the ops they reverse do. An unknown kind is
        logged and skipped.
        """
        if not isinstance(entry, Mapping):
            _log.warning("undo entry is not a mapping: %r", entry)
            return
        if entry.get("bulk"):
            subs = [
                sub for sub in (entry.get("entries") or ())
                if isinstance(sub, Mapping)
            ]
            ordered = reversed(subs) if direction == "undo" else iter(subs)
            for sub in ordered:
                self._replay_entry(sub, direction=direction, refresh=False)
            self._refresh()
            return
        kind = entry.get("kind")
        if kind == "folder_op":
            self._replay_folder_op(entry, direction=direction)
            return
        if kind == "panic_toggle":
            self._replay_panic_toggle(entry, direction=direction)
            return
        if kind == "model_reset":
            self._replay_model_reset(entry, direction=direction, refresh=refresh)
            return
        if kind not in _PILL_SHAPED_UNDO_KINDS:
            _log.info("undo replay skipped - unsupported kind %r", kind)
            return
        if self.active_model is None:
            _log.info("undo replay skipped - Global is active, nothing to mutate.")
            return

        plugin_name = entry.get("plugin")
        if not isinstance(plugin_name, str):
            return

        target_entry = entry.get("previous" if direction == "undo" else "next")
        plugins = dict(self.active_model.plugins)

        if target_entry is None:
            plugins.pop(plugin_name, None)
        elif isinstance(target_entry, PluginEntry):
            plugins[plugin_name] = target_entry
        else:
            _log.warning(
                "undo entry payload is neither None nor PluginEntry: %r",
                target_entry,
            )
            return

        self.active_model = dataclasses.replace(self.active_model, plugins=plugins)
        self._is_dirty = True
        if refresh:
            self._refresh()

    def _replay_model_reset(
        self, entry: Mapping[str, Any], *, direction: str, refresh: bool = True
    ) -> None:
        """Undo or redo a whole-model swap, from Reset Global Plugins.

        The reset drops every Global entry, so a per-entry delta would
        have to record each removed key. The entry carries both whole
        models instead. In memory only, like the live handler.
        """
        side = entry.get("previous" if direction == "undo" else "next")
        if not isinstance(side, LoadoutFile):
            _log.warning("model_reset entry side is not a LoadoutFile: %r", side)
            return
        self.active_model = _clone_loadout(side)
        self._is_dirty = self.is_active_dirty
        if refresh:
            self._refresh()

    def _replay_panic_toggle(
        self, entry: Mapping[str, Any], *, direction: str
    ) -> None:
        """Undo or redo a Panic flip by re-running ``set_panic``.

        Panic lives in the dispatcher and writes through at once, so
        the replay writes through too. The active model survives the
        flip, and the panel refresh re-syncs the Panic button.
        """
        target = bool(entry.get("previous" if direction == "undo" else "next"))
        result = loadout_ops.set_panic(self.loadouts_dir, target, self.state)
        forward = loadout_ops.OpResult(
            path=result.path,
            model=self.active_model,  # type: ignore[arg-type]
            state=result.state,
        )
        self.apply_op_result(forward)

    def _replay_folder_op(
        self, entry: Mapping[str, Any], *, direction: str
    ) -> None:
        """Reverse, or re-apply, one compound folder operation.

        The folder list moves by inverse delta, never by snapshot.
        Folders are global while undo stacks are per-Loadout, so a full
        restore from this stack would undo folder changes made from
        another Loadout. The model and the force-dirty set do restore
        from the entry's per-side snapshots, because both are
        per-Loadout.

        Order matters. Dirs first, then persist and rescan, then the
        model snapshot last so it wins over the scan's auto-enable
        entries.
        """
        undo = direction == "undo"
        op = entry.get("op")
        path = entry.get("path")
        dirs = list(getattr(self, "user_plugin_dirs", []) or [])

        def _with_path_inserted(seq: List[str]) -> List[str]:
            # Clamp the recorded position, so a list reshaped by
            # another Loadout's folder op still accepts it.
            if not isinstance(path, str) or path in seq:
                return list(seq)
            index = entry.get("index")
            spot = index if isinstance(index, int) else len(seq)
            out = list(seq)
            out.insert(max(0, min(spot, len(out))), path)
            return out

        if op == "add":
            dirs = (
                [p for p in dirs if p != path]
                if undo
                else _with_path_inserted(dirs)
            )
        elif op == "remove":
            dirs = (
                _with_path_inserted(dirs)
                if undo
                else [p for p in dirs if p != path]
            )
        elif op == "reorder":
            order = list(entry.get("prev_order" if undo else "next_order") or [])
            known = set(dirs)
            recorded = set(order)
            dirs = [p for p in order if p in known] + [
                p for p in dirs if p not in recorded
            ]
        else:
            _log.warning("folder_op replay skipped - unknown op %r", op)
            return

        self.user_plugin_dirs = dirs
        self.persist_folder_authority()
        self.scan_and_refresh()

        side = entry.get("previous" if undo else "next")
        if isinstance(side, Mapping):
            self.active_model = _clone_loadout(side.get("model"))
            self._force_dirty_plugins = set(side.get("force_dirty") or ())
        self._is_dirty = self.is_active_dirty
        self._refresh()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _glob_from_qt_filter(qt_filter: str) -> Optional[str]:
    """Extract a glob pattern from a Qt name-filter string.

    A Qt filter looks like ``"Loadout files (*.py);;All (*)"`` and
    :func:`nuke.getFilename` takes one glob. Returns the first glob in
    the first clause, or ``None`` when there is none.
    """
    if not qt_filter:
        return None
    first_clause = qt_filter.split(";;", 1)[0]
    open_paren = first_clause.find("(")
    close_paren = first_clause.rfind(")")
    if open_paren < 0 or close_paren <= open_paren:
        return None
    inside = first_clause[open_paren + 1: close_paren].strip()
    if not inside:
        return None
    # Nuke's browser takes one pattern only, so the first one wins.
    first_glob = inside.split()[0]
    return first_glob or None


def _canonical_folder_var(index: int) -> str:
    """Positional ``plugins_X`` var for the folder at ``index``.

    Alias for :func:`folder_ops.canonical_folder_var`, the one source
    of the A-Z then AA-ZZ ordering. It keeps the dispatcher's var
    names in line with what each loadout file carries.
    """
    return folder_ops.canonical_folder_var(index)


def _clone_loadout(model: Optional[LoadoutFile]) -> Optional[LoadoutFile]:
    """Copy a model deeply enough for a snapshot.

    :class:`PluginEntry` is immutable and no caller mutates one in
    place, so copying the dict shell is enough.
    """
    if model is None:
        return None
    return LoadoutFile(name=model.name, plugins=dict(model.plugins))


def _normalised_plugins(
    model: LoadoutFile, global_model: Optional[LoadoutFile]
) -> Mapping[str, PluginEntry]:
    """Drop ``model.plugins`` entries that match the plugin's default.

    Two models with the same effective state must compare equal, even
    when one carries an explicit default-valued entry and the other
    carries none.

    The default is the Global entry when the key is in
    ``global_model``, and ``PluginEntry(enabled=True, gui_only=False)``
    otherwise, matching the chain-format default.

    That second default must be ``enabled=True``, or a user-added
    plugin toggled on and back off never reads clean. Folder add stays
    dirty through ``_force_dirty_plugins``, not through this rule.
    """
    result: dict[str, PluginEntry] = {}
    global_plugins = global_model.plugins if global_model is not None else {}
    implicit_default = PluginEntry(enabled=True, gui_only=False)
    for key, entry in model.plugins.items():
        default = global_plugins.get(key, implicit_default)
        if entry != default:
            result[key] = entry
    return result

