"""Production :class:`Registry` - the state-shape carrier on ``panel.registry``.

Every wiring helper and the wire functions inside the widget modules read
off ``panel.registry``; this class is the concrete production instance.

What the Registry owns
----------------------
* The live :class:`DispatcherState` - ``active`` (last-active loadout
  stem) and ``panic`` (panic flag).
* The in-memory ``active_model`` :class:`LoadoutFile` (or ``None`` when
  Global is active), and the resolved ``global_model`` :class:`LoadoutFile`
  (or ``None`` when no Global layer is configured).
* ``global_plugin_names`` - denormalised key set from
  ``global_model.plugins`` so pill state derivation skips a hot-path
  recomputation.
* The :class:`UndoStackRegistry` - per-Loadout, 50 steps, session-only.
* Boot-time snapshots so pending-change counts can be derived without the
  panel retaining its own baseline.

How the Registry talks to the panel
-----------------------------------
The Registry never reaches into widget internals. After mutating its
own state inside :meth:`apply_op_result` it calls a single
``refresh_callback`` that the panel installs at attach time. The panel's
refresh path reads the helpers in :mod:`NukeSurvivalLoadout.ui.state` and
pushes new state into each region widget.

Plugin Name is the key. No ``import nuke`` at module scope: this is a UI
module; Nuke is imported lazily inside the methods that need it.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional

from NukeSurvivalLoadout import compat
from NukeSurvivalLoadout.constants import (
    GLOBAL_SOURCE_MARKER,
    RESERVED_LOADOUT_STEM,
)
from NukeSurvivalLoadout.boot.dispatcher import DispatcherState
from NukeSurvivalLoadout.data.loadout_file import LoadoutFile, PluginEntry
from NukeSurvivalLoadout.domain import loadout_ops
from NukeSurvivalLoadout.domain.scanner import Plugin, scan_folder
from NukeSurvivalLoadout.paths import canon_for_compare
from NukeSurvivalLoadout.domain.undo_stack import UndoStackRegistry
from NukeSurvivalLoadout.ui import dialogs

__all__ = ["Registry"]


_log = logging.getLogger(__name__)


QtWidgets = compat.QtWidgets

# Undo entry kinds whose payload is pill-shaped (plugin / previous /
# next). The ``bulk_*`` kinds are pushed per-plugin inside a coalesced
# bulk entry but replay identically to a single pill toggle.
_PILL_SHAPED_UNDO_KINDS = frozenset({
    "pill_toggle",
    "bulk_enable",
    "bulk_disable",
    "bulk_invert",
    "bulk_set_gui_only",
    "bulk_clear_gui_only",
})


class Registry:
    """Concrete state-shape carrier attached to ``LoadoutPanel.registry``.

    Constructed by
    :func:`NukeSurvivalLoadout.ui.registry_bootstrap.build_registry_for_panel`
    in production.

    Optional hooks the wiring helpers reach for via ``getattr`` are all
    implemented here so production behaves consistently rather than
    silently no-op'ing.
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
        # Required surface fields.
        self.loadouts_dir = Path(loadouts_dir)
        self.state = state
        # ``user_plugin_dirs`` - the absolute paths the scanner walks.
        # These live as ``plugins_A`` / ``plugins_B`` vars at the top of
        # the active loadout file. The bootstrap layer derives them and
        # passes them here so ``scan_and_refresh`` has its input. Empty
        # list when Global is active (no user folders to scan).
        self.user_plugin_dirs: List[str] = list(user_plugin_dirs or [])
        self.active_model = active_model
        self.global_model = global_model
        # Unreadable ``Global_Loadout/init.py`` (syntax error / IO).
        # Boot and panel both fall back to loading every Global folder;
        # the Summary tab renders this as a Warnings entry. The terminal
        # stays silent by design.
        self.global_loadout_error: Optional[str] = global_loadout_error
        self.undo_stacks = undo_stacks if undo_stacks is not None else UndoStackRegistry()

        # Denormalised Global set (see class docstring).
        self.global_plugin_names: frozenset[str] = (
            frozenset(global_model.plugins.keys()) if global_model else frozenset()
        )

        # The resolved Global plugins dir (from the boot session record,
        # else a static parse of the ``Global/init.py`` chain head - see
        # ``ui.registry_bootstrap._load_global``). Needed by
        # :meth:`scan_and_refresh` so the panel-side rescan walks the
        # same folder the boot-side Global head loaded.
        # Without these, ``discovered_plugins`` only carries user-added
        # folders' plugins → clicking the info button on a Global pill
        # falls through to "(plugin not found in current scan)" even
        # though the plugin loaded fine at boot.
        self.global_plugin_dirs: List[Path] = list(
            global_plugin_dirs or []
        )

        # Case A/B switch for the ``Global_Loadout`` name rules: True when
        # a Global Loadout copy lives in the NSL Global folder (case B),
        # which hides the user-land ``Global_Loadout`` from the dropdown
        # and turns Save As under that name into a staging save.
        self.global_loadout_copy_exists: bool = bool(global_loadout_copy_exists)

        # Boot snapshots - never mutated after construction. Retained for
        # degraded-mode / diagnostic readers; the pill-diff baseline now
        # lives in the per-loadout saved-baseline cache below.
        self.boot_active: Optional[LoadoutFile] = _clone_loadout(active_model)
        self.boot_global: Optional[LoadoutFile] = _clone_loadout(global_model)

        # Frozen snapshot of what the chain ACTUALLY loaded at
        # boot (the effective enabled set, including scan-loaded defaults).
        # Captured once on the first ``scan_and_refresh`` (when
        # ``discovered_plugins`` is first populated). Under the sparse
        # loadout-file model ``boot_active`` is empty for an all-default
        # loadout, so the old ``boot_active``/``boot_global`` baseline
        # under-counted "loaded this session" - every default-on plugin
        # (loaded at boot) wrongly read as "pending restart" with the
        # green glow. ``session_loaded_baseline`` returns
        # this snapshot when present; it stays fixed so a folder added /
        # plugin toggled mid-session still surfaces as "+N pending restart".
        self._session_loaded_snapshot: Optional[LoadoutFile] = None
        # Whether the boot scan has actually run. Distinguishes "scan ran and
        # found nothing loaded" (snapshot legitimately None) from "scan never
        # ran" (degraded / tests). Without this, ``session_loaded_baseline``
        # treated a None snapshot as "no scan yet" and fell back to the
        # ``boot_active`` model - reporting the active loadout's DECLARED
        # enabled set as "loaded this session" even when nothing actually
        # loaded (fresh session, plugins just added, nothing on
        # ``nuke.pluginPath()``).
        self._session_scan_done: bool = False

        # Per-loadout saved-on-disk baseline cache.
        # Keyed by the loadout stem ("Custom", "Global", etc.). Snapshotted
        # on construction (here), on loadout switch (apply_op_result when
        # last_active_loadout flips), and on Save (apply_op_result when
        # the op carries a path). The pill diff math + banner count read
        # active_saved_baseline so tints reflect divergence from the
        # CURRENT loadout's on-disk state, not the boot snapshot.
        self._saved_baselines: dict[str, LoadoutFile] = {}

        # Per-loadout folder-list baseline, captured at the same moments
        # as ``_saved_baselines``. Revert restores the Plugins Folder
        # list to this snapshot alongside the model - without it, folder
        # add / remove / reorder would survive a Revert even though the
        # user asked to discard their edits.
        self._saved_folder_baselines: dict[str, List[str]] = {}
        self._snapshot_baseline_for_active()

        # Per-loadout pending-edits cache. When the user edits a loadout
        # then switches away without saving, the in-memory dirty model is
        # parked here keyed by stem so switching BACK restores the edits
        # (and the (*) dirty marker) instead of re-reading the
        # untouched-on-disk version. Cleared for a stem when that stem
        # is saved or its on-disk state advances (rename / save-as).
        # Without this, editing a loadout then switching away and back
        # would lose the in-memory edits.
        self._pending_models: dict[str, LoadoutFile] = {}

        # Dirty flag - set True by pill toggles via mark_clean(False) or
        # apply_undo / apply_redo; reset False by apply_op_result on
        # switch/save. Pre-initialised so apply_op_result can read it
        # unconditionally without an AttributeError when no mark_clean
        # call has fired yet (e.g. first user click on a fresh boot).
        self._is_dirty = False

        # Panel-side refresh hook. The panel installs its own
        # refresh_from_registry method here at attach time. Optional so
        # tests can drive the Registry without a real panel.
        self._refresh_callback = refresh_callback

        # Parent widget for Qt prompt dialogs. ``None`` is valid - Qt
        # tolerates parentless message boxes (the WM parents them).
        self._parent_widget = parent_widget

        # In-session folder UI state - visibility + last-known health per
        # configured folder. Owned here so the refresh path can hand the
        # current map to ``folder_list_from``. Never persisted.
        self._folder_visibility: dict[str, bool] = {}
        self._folder_health: dict[str, Any] = {}

        # Discovered plugins from the live scanner - keyed by Plugin
        # Name. Populated by :meth:`scan_and_refresh`; used by the panel's
        # plugin-key union so the grid shows pills for plugins on disk
        # even before any Loadout enables them.
        self.discovered_plugins: dict[str, Plugin] = {}

        # Force-dirty plugin set - names of plugins that should be
        # treated as uncommitted regardless of value comparison.
        # Adding a Plugins Folder is the canonical trigger: the loadout
        # entries for the folder's plugins are preserved on disk
        # (reactivate cleanly on re-add), so a
        # re-add against an existing saved baseline would leave
        # M == D and Save greyed. Marking JUST the newly-added
        # folder's plugins as force-dirty (rather than a global flag)
        # opens Save for the user's "re-confirm" gesture while
        # leaving every OTHER plugin's saved-glow intact, rather than
        # clearing the saved state of plugins unrelated to the added
        # folder. Cleared by ``apply_op_result`` on any disk write or
        # loadout switch.
        self._force_dirty_plugins: set[str] = set()

    # ------------------------------------------------------------------
    # Required surface - apply_op_result, on_blocked, compute_folder_removal
    # ------------------------------------------------------------------

    def apply_op_result(self, result: loadout_ops.OpResult) -> None:
        """Sync internal state from a successful op, then refresh widgets.

        ``state`` always carries forward. ``model`` carries forward when
        the op produced one; ``None`` means Global is active after the
        op (delete-of-active fallback).

        The wiring layer bridges op results back to the panel's
        ``LoadoutFile`` shape before forwarding here; this method always
        sees ``LoadoutFile`` (or ``None``) on ``result.model``.
        """
        from NukeSurvivalLoadout.constants import DEFAULT_CUSTOM_LOADOUT_STEM

        previous_active_stem = (
            self.state.active if self.state else ""
        )
        new_active_stem = (
            result.state.active if result.state else ""
        )
        switched = new_active_stem != previous_active_stem

        # Pure-switch detection: settings changed active stem and the op
        # didn't write to disk. Park the in-memory dirty model under the
        # outgoing stem so a switch-back restores it. Uses
        # :attr:`is_active_dirty` (now a value-comparison property; see
        # below) so the park decision sees the current truth about
        # whether the model differs from its on-disk state. A
        # flag-based check would park even when the user has toggled
        # back to the saved state, putting a stale ``(*)`` on a
        # loadout that is actually clean.
        pure_switch = switched and result.path is None
        if pure_switch and self.is_active_dirty and self.active_model is not None:
            self._pending_models[previous_active_stem] = self.active_model

        # Custom-specific park: the wildcard slot must survive every
        # switch-away (including Save-As, where ``result.path`` is
        # non-None - the new named loadout's file). Without this
        # branch, ``Custom → Save As → new`` would lose Custom's
        # parked state and a switch back to Custom would synthesise a
        # fresh-from-Global view, contradicting the rule that Custom(*)
        # persists and the user can always go back.
        #
        # The park ALWAYS OVERWRITES. A "park only if absent" guard
        # cannot update the parked entry after the first park, so
        # subsequent in-session edits would be lost on switch-back.
        # Unconditional overwrite makes the parked entry always
        # reflect the latest in-memory state, which is the only
        # honest contract for an in-memory-only slot.
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

        # On switch-in, if we have a parked dirty model for the new stem,
        # restore it so the user's prior edits survive the round-trip.
        restored_from_pending = False
        if pure_switch and new_active_stem in self._pending_models:
            self.active_model = self._pending_models[new_active_stem]
            restored_from_pending = True
        elif switched or result.path is not None:
            # A write to disk for THIS stem (Save / Save As / rename /
            # duplicate / import / auto-create-Custom) means the pending
            # edits just got committed. Drop any stale pending entry.
            if result.path is not None and new_active_stem in self._pending_models:
                del self._pending_models[new_active_stem]

        # Refresh the saved baseline for any op
        # whose post-state we know matches disk. Two triggers:
        #   * Save / Save As / rename / duplicate / import - ``result.path``
        #     is non-None and the active model now matches what's on disk.
        #   * Loadout switch THAT IS NOT a restore-from-parked. A clean
        #     switch reads the new active model from disk, so the
        #     snapshot is honest. A restore-from-parked replaces
        #     active_model with the in-memory dirty edits - snapshotting
        #     then would lock those edits in as the baseline and
        #     ``is_active_dirty`` would falsely report clean.
        if result.path is not None or (switched and not restored_from_pending):
            self._snapshot_baseline_for_active()

        # Ceremonial-save set (populated by
        # ``mark_plugins_force_dirty``) clears on any disk write OR
        # on a loadout switch. A successful Save is the user's "I'm
        # done re-confirming" gesture; a switch means we're now
        # looking at a different loadout whose dirty state should be
        # its own.
        if result.path is not None or switched:
            self._force_dirty_plugins.clear()

        # Vestigial flag kept in sync for any caller that still reads
        # ``_is_dirty`` directly. ``is_active_dirty`` is now driven by
        # the value comparison below; this assignment is informational
        # only (mirrors what the property would compute right now).
        self._is_dirty = self.is_active_dirty

        self._refresh()

    def on_blocked(self, blocked: loadout_ops.Blocked) -> None:
        """Surface a structured no-op. Currently logs; selected codes
        could later be promoted to a toast or banner."""
        _log.info("op blocked: code=%s detail=%s", blocked.code, blocked.detail)

    def compute_folder_removal(self, path: str) -> Mapping[str, Iterable[str]]:
        """Pre-flight data for :func:`folder_ops.remove_folder_and_save`.

        Stub: returns empty iterables. Real Plugin-Scan integration will
        populate ``actively_loaded`` and ``unique`` from the live scan
        results.
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
        # Loadout files are chain-format Python (the JSON-era .loadout
        # extension is retired), so the picker filters on .py.
        return self._open_file_prompt(
            title="Import Loadout",
            name_filter="Loadout files (*.py)",
        )

    def prompt_export(self) -> Optional[Path]:
        """Prompt for the export FOLDER; the caller writes ``<folder>/init.py``.

        A loadout in the chain architecture is always a folder holding
        an ``init.py`` - the file-export era's bare ``<name>.py`` could
        not run in the chain without a manual rename. Export therefore
        asks for a folder (the browser's new-folder button creates one)
        and the loadout lands inside it as ``init.py``, immediately
        droppable into ``~/.nuke/loadouts/`` or ``<repo>/Global/``.

        Default position is ``<loadouts>/<stem>``, where ``stem`` is the
        active loadout's name. Standing on Custom (or the Global view,
        no active model) defaults to ``<loadouts>/Global_Loadout``: the
        wildcard slot is where the Global layer gets curated, so the
        staging-save location is the natural target.
        """
        from NukeSurvivalLoadout.constants import (
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
        # ``(*)`` (not ``""``): an empty filter resolves to a ``None``
        # glob, which ``nuke.getFilename`` rejects - and the dialog
        # wrapper's catch-all turns that into a silently dead button.
        target = self._save_file_prompt(
            title="Export Loadout (pick or create the Loadout folder)",
            default_name=default_path,
            name_filter="Loadout folders (*)",
        )
        if target is None:
            return None
        # Normalise to the FOLDER. A typed/picked ``init.py`` means its
        # parent folder; any other ``.py`` name is file-export-era
        # muscle memory and means "the folder of that stem".
        if target.name.lower() == "init.py":
            target = target.parent
        elif target.suffix.lower() == ".py":
            target = target.with_suffix("")

        # ``Custom`` is the in-memory wildcard slot and ``Global`` is
        # the read-only baseline view - a folder under either name
        # would collide with the reserved stems the moment it lands in
        # a loadouts dir. (The JSON-era "Export Global.py is the TD
        # flow" allowance is retired; the Global layer is authored via
        # save-and-copy now.) Case-insensitive.
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
        # Prefer Nuke's native browser when running inside Nuke: it
        # matches the chrome of File > Open and carries Nuke's
        # recents/favorites sidebar. ``nuke.getFilename``
        # is file-oriented but accepts a folder selection - the user
        # navigates into the target folder and clicks OK without
        # picking a file. The browser ignores the panel's geometry
        # (no parent/position parameter); that's a known trade-off
        # to keep the Nuke vocabulary. Fall back to
        # the Qt-themed dialog when ``nuke`` isn't importable
        # (standalone, headless).
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

        Handles ``pill_toggle`` entries (the dominant case pushed by the
        wiring layer). Other kinds are logged and ignored until their
        corresponding bulk-op / file-op redo paths are added; undo of
        file-level ops is out of scope.
        """
        self._replay_entry(entry, direction="undo")

    def apply_redo(self, entry: Mapping[str, Any]) -> None:
        self._replay_entry(entry, direction="redo")

    @property
    def force_dirty_plugins(self) -> frozenset:
        """Names of plugins currently in the ceremonial-save set.

        Pill rendering reads this to suppress the saved-state glow
        on those specific plugins while a force-dirty gesture is
        pending - without this, a folder-add would either (a) show
        all pills as suddenly unsaved (wrong - only the new folder's
        plugins are part of the ceremony) or (b) show the loadout
        as (*) + Save enabled while pills still glow saved
        (contradictory). Cleared by ``apply_op_result`` on Save /
        loadout switch.
        """
        return frozenset(getattr(self, "_force_dirty_plugins", ()))

    def mark_plugins_force_dirty(self, plugin_names) -> None:
        """Add ``plugin_names`` to the ceremonial-save set.

        Used by folder-add to scope the "re-confirm" gesture to just
        the newly-added folder's plugins. Other plugins keep their
        saved-glow / value-based dirty state. The set clears on Save
        / loadout switch via ``apply_op_result``. Refreshes the panel
        so the dirty marker + Save button + per-pill borders pick up
        the new state.
        """
        names = {n for n in plugin_names if isinstance(n, str)}
        if not names:
            return
        self._force_dirty_plugins.update(names)
        self._refresh()

    def set_folder_baseline(self, stem: str, dirs: Iterable[str]) -> None:
        """Pin the Revert folder baseline for ``stem`` to ``dirs``.

        Used by the folder-add that auto-creates Custom: that op's own
        ``apply_op_result`` switch-snapshot runs mid-op (after the
        folder list already changed), so the natural capture would
        record the post-add list and make the very first add
        un-revertable. The wiring layer pins the pre-op list here right
        after the op lands.
        """
        self._saved_folder_baselines[stem] = list(dirs)

    def mark_clean(self, clean: bool) -> None:
        """Forwarded to the loadout strip's dirty indicator via the
        refresh path. The strip's own ``set_dirty`` slot drives the
        ``(*)`` suffix on the active row."""
        # We can't reach the strip directly without coupling; the panel's
        # refresh path reads dirty state out of the registry instead.
        self._is_dirty = not clean
        self._refresh()

    def revert_active_to_baseline(self) -> bool:
        """Discard in-memory edits on the active Loadout, restoring its
        on-disk state.

        Companion to the loadout strip's Revert button. Restores three
        things to the last baseline capture (the last Save or Loadout
        switch):

        * the model - :attr:`active_saved_baseline` cloned back into
          :attr:`active_model`;
        * the Plugins Folder list - folder add / remove / reorder since
          the baseline roll back, the dispatcher authority is
          re-persisted, and a rescan drops / restores the affected
          pills;
        * the ceremonial-save set - cleared, so a reverted folder-add
          stops holding the Save affordance open.

        Also drops any parked dirty edits cached for this stem.

        Returns ``True`` when a revert actually happened, ``False``
        when there was nothing to do (Global is active, no baseline
        cached, or everything already equals the baseline).
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
            # Rescan BEFORE the model restore below - the scan's
            # reconcile pass may write auto-enable entries into
            # ``active_model``, and the baseline clone must win.
            self.scan_and_refresh()
        # Clone so subsequent edits don't mutate the baseline through
        # the shared plugins dict.
        self.active_model = _clone_loadout(baseline)
        # Drop the parked-dirty cache for this stem too - the user
        # explicitly chose to discard, so a future switch-away
        # shouldn't try to restore the stale edits.
        if stem in self._pending_models:
            del self._pending_models[stem]
        # The ceremonial-save set is part of what Revert discards: a
        # folder-add marked its plugins force-dirty, and reverting must
        # also release the Save affordance that mark opened.
        self._force_dirty_plugins.clear()
        # Sync the vestigial flag for any caller that still reads it.
        self._is_dirty = False
        self._refresh()
        return True

    @property
    def is_active_dirty(self) -> bool:
        """Whether the active Loadout differs from its on-disk state.

        This is a value comparison, not a "changed since last save"
        flag: it must read clean when the current state equals the
        saved state even after intermediate edits. A byte-for-byte
        dict comparison is not enough either - toggling a plugin off
        then back on leaves an explicit
        ``PluginEntry(enabled=True, gui_only=False)`` in
        ``active_model.plugins`` (the same values as the implicit
        default) while the baseline has no entry at all, so raw dict
        inequality would report dirty even though the effective state
        is identical.

        Both sides are therefore normalised by dropping entries that
        match the plugin's default before comparison. Default rule
        mirrors the sparse-diff resolution:

        * Global plugin (key in ``global_model.plugins``) →
          default is that plugin's Global entry.
        * Otherwise → default is ``PluginEntry(enabled=True,
          gui_only=False)`` (load, not GUI-only).

        After normalisation, raw dict equality gives the correct
        "do these models differ semantically" answer. Round-trip
        toggles on user-added AND Global plugins both resolve
        to clean.

        Falls back to the legacy flag when no baseline has been
        cached yet (degraded fixtures / tests that build a Registry
        without invoking the bootstrap snapshot path).
        """
        # Ceremonial-save set wins - any plugin in
        # ``_force_dirty_plugins`` makes the loadout read as dirty
        # (folder-add opens Save without touching other plugins'
        # saved-glow). Cleared on the next
        # disk write or loadout switch by ``apply_op_result``.
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
        """LoadoutFile representing the active Loadout's effective state.

        Composes Global as the base, then overlays the active
        Loadout's plugin entries on top - the standard sparse-diff
        resolution rule. Keys absent from the active Loadout fall back
        to Global; keys present in the active Loadout win.

        Required for banner + counter diff math against
        :attr:`session_loaded_baseline`: a sparse active model
        (e.g. Custom after ``reset_global_to_default`` empties its
        plugins dict) would otherwise look like "every Global plugin
        removed" even though Global fallback resolution means the
        effective state is unchanged (otherwise changing a single
        plugin and then resetting Global plugins would report a
        spurious negative change count).

        Returns ``None`` only when neither Global nor active is
        available (degraded-mode contexts).
        """
        if (
            self.global_model is None
            and self.active_model is None
            and not self.discovered_plugins
        ):
            return None
        # Orphan-deviation filter.
        # An entry in ``active_model.plugins`` whose plugin is no longer
        # discoverable (source folder removed AND not in Global) is an
        # orphan: the loadout file remembers a user override on a
        # plugin whose folder has since been removed from
        # ``settings.user_plugins_dirs``. Surfacing it in the diff
        # produces a phantom "+N would load on restart" banner against
        # plugins that physically can't load. Limit the active overlay
        # to plugins that have a live source (discovered or Global)
        # so the diff math matches reality. The orphan entries stay in
        # the on-disk file untouched - they'll re-resolve naturally if
        # the user re-adds the folder later.
        loadable_keys: set[str] = set(self.discovered_plugins.keys())
        if self.global_model is not None:
            loadable_keys.update(self.global_model.plugins.keys())

        # No "known-failed at load time" filter applies here: there is
        # no per-pill load-result vocabulary.
        # The panel only knows what's currently on disk; if a
        # plugin's init.py raises at load time Nuke crashes the whole
        # interpreter and the panel never opens at all (recovery is
        # edit-the-file + relaunch). The pill states collapse to
        # Enabled / Disabled / Missing - none of them depend on
        # per-session load truth.
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
                # else: orphan deviation - folder was removed; the
                # entry survives on disk but doesn't enter the diff.
        # Newly-discovered plugins that no Loadout has touched yet
        # resolve to the sparse-diff default per
        # :func:`NukeSurvivalLoadout.ui.state.pill_state_from`'s rule. Default depends
        # on whether Global is active:
        #
        # * Global active (``active_model is None``) → user-added
        #   plugins default DISABLED. Global is the TD's view; user
        #   plugins aren't part of it.
        # * User loadout active → default ENABLED. Sparse-diff
        #   contract: file silent on a plugin = use default = load it.
        #
        # Without this, the diff math under-reports / over-reports:
        # adding a folder with 3 new plugins would show +1 instead of
        # +3 because the diff only saw plugins with explicit entries,
        # missing the others whose default-True state still implies
        # "will load on restart."
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
                # Known-failed filter (see comment above) applies to
                # the sparse-default path too: a discovered plugin
                # that failed at load should not be sparse-defaulted
                # into the next-restart projection.
                continue
            if global_is_active and name not in global_set:
                # User-added plugin under Global view → disabled.
                plugins[name] = PluginEntry(enabled=False, gui_only=False)
            else:
                plugins[name] = PluginEntry(enabled=True, gui_only=False)
        # The name field is informational - diff math doesn't read it.
        # Pick the active Loadout's name when available so degraded
        # readers still see the right label.
        name = (
            self.active_model.name
            if self.active_model is not None
            else (self.global_model.name if self.global_model is not None else "")
        )
        return LoadoutFile(name=name, plugins=plugins)

    def count_diverged_global_plugins(self) -> int:
        """Count Global Plugins whose active-loadout entry diverges
        from the resolved Global entry.

        A Global Plugin "diverges" when the active Loadout carries
        an entry for that Plugin AND its value (``enabled`` +
        ``gui_only``) differs from the Global Loadout's entry. Keys
        absent from the active Loadout fall back to Global through
        sparse-diff resolution and are NOT counted as diverged.

        Returns ``0`` when Global is active (Global has no divergence
        against itself), when no Global layer is configured, or when
        every Global key in the active Loadout matches its
        Global counterpart. Used by the Reset Global Plugins to
        Default button to gate its enabled state: the button is
        disabled when no plugin differs from the Global loadout state.
        """
        if self.global_model is None:
            return 0
        if self.active_model is None:
            return 0
        count = 0
        for name in self.global_plugin_names:
            active_entry = self.active_model.plugins.get(name)
            if active_entry is None:
                # Sparse: missing key resolves to Global → no divergence.
                continue
            global_entry = self.global_model.plugins.get(name)
            if active_entry != global_entry:
                count += 1
        return count

    @property
    def dirty_stems(self) -> frozenset[str]:
        """Stems of loadouts with parked unsaved edits (switched-away dirty).

        The active loadout's dirty state lives on :attr:`is_active_dirty`;
        this property covers the OTHER rows in the dropdown so the strip
        can surface ``(*)`` on a non-active loadout whose dirty in-memory
        model is parked in :attr:`_pending_models`.

        Without this, a non-active loadout with stored changes (such
        as Custom) would not show its ``(*)`` in the dropdown.
        """
        return frozenset(self._pending_models)

    def rescan(self) -> None:
        """Trigger a Plugins-Folder rescan.

        Re-runs the scanner against every configured user Plugins
        Folder and refreshes widgets so newly-discovered plugins
        materialise as pills in the grid. Delegates to
        :meth:`scan_and_refresh`.
        """
        self.scan_and_refresh()

    def persist_folder_authority(self) -> None:
        """Write the Plugins Folder list to the dispatcher and sync every loadout.

        The dispatcher (``~/.nuke/loadouts/init.py``) is the AUTHORITY
        for the folder list - folders are global state, not part of any
        one loadout - so this runs after every folder add / remove /
        reorder (including while Custom is active), after undo / redo
        of a folder op, and on Revert when the folder baseline differs.

        panic + active come from the in-memory :attr:`state` (the
        bootstrap-normalised, op-synced mirror), NOT a fresh re-read of
        disk. Re-reading disk was the resurrection bug: the bootstrap
        normalises a stale ``ACTIVE_LOADOUT="Custom"`` pointer to "" in
        memory, but a disk re-read here pulled the unchanged ``Custom``
        back and wrote it out again - violating the "Custom is in-memory
        only" invariant and re-arming the spurious "missing: Custom" boot
        log. Writing ``self.state`` carries the normalisation through; the
        Custom slot is additionally coerced to "" below as a belt so an
        in-memory pending-Custom pointer never lands on disk. ``panic`` and
        the last real active loadout are preserved.
        ``sync_folders_to_loadouts`` then fans the canonical decls into
        each loadout file, remapping plugin entries by PATH so reorders
        stay correct.
        """
        from dataclasses import replace

        from NukeSurvivalLoadout.constants import DEFAULT_CUSTOM_LOADOUT_STEM
        from NukeSurvivalLoadout.boot.dispatcher import (
            read_dispatcher,
            write_dispatcher,
        )
        from NukeSurvivalLoadout.boot.loadout_file import (
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

        # Base the write on the in-memory normalised state when present;
        # fall back to a disk read only for degraded fixtures that drive
        # the registry without a state (tests / headless).
        base_state = getattr(self, "state", None)
        if base_state is None:
            base_state = read_dispatcher(dispatcher)
        # Never persist the in-memory-only Custom wildcard as the active
        # pointer (bootstrap may synthesise ``active="Custom"`` in memory).
        active = base_state.active
        if active == DEFAULT_CUSTOM_LOADOUT_STEM:
            active = ""
        write_state = replace(base_state, active=active, folders=canonical)
        write_dispatcher(dispatcher, write_state)
        if getattr(self, "state", None) is not None:
            self.state.folders = list(canonical)

        sync_folders_to_loadouts(loadouts_dir, canonical)

    def scan_and_refresh(self) -> None:
        """Scan every configured Plugins Folder and refresh the UI.

        For each configured user Plugins Folder, calls
        :func:`NukeSurvivalLoadout.domain.scanner.scan_folder` and merges the result
        into ``discovered_plugins``. Later folders override earlier
        ones on Plugin Name collision (the scanner's last-wins
        resolution).

        Plugins gone from disk simply disappear from the grid. The "I
        depend on this and it vanished" signal is carried by the
        existing ``source_missing`` YELLOW + red-border treatment, which
        fires when a plugin loaded this session has lost its source
        folder - see :func:`NukeSurvivalLoadout.ui.state.pill_state_from`'s
        ``source_missing`` branch. Failures on a single folder don't
        abort the whole scan - the folder's contribution is dropped and
        a warning is logged.

        Calls ``self._refresh()`` so the panel rebuilds the grid against
        the new ``discovered_plugins`` keys via
        :func:`NukeSurvivalLoadout.ui.panel._plugin_key_union`.

        After the scan, reconciles any Plugin that's on disk but has
        no decision in the active Loadout AND no decision in Global by
        auto-enabling it in the active Loadout. This is the single
        source of truth for "newly discovered → gets ``enabled=True``"
 - folder-add, boot bootstrap, and manual rescan all flow
        through here.
        """
        live: dict[str, Plugin] = {}

        # Walk the Global Plugins Folders FIRST so the
        # subsequent user-added walk shadows them on Plugin Name
        # collisions (last-wins, mirrors ``NukeSurvivalLoadout.boot.sequence._phase_scan``).
        # Global-source plugins are rewritten with ``source =
        # GLOBAL_SOURCE_MARKER`` so the raw Global dir path never leaks
        # into the panel's source/visibility grouping; ``plugin.path``
        # keeps the real filesystem path so ``_read_plugin_readme`` can
        # still open the README.
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

        # First scan == boot scan: freeze the "loaded this session" baseline
        # from what is actually on Nuke's plugin path now that
        # discovered_plugins exists. Frozen on the FIRST scan (gated on
        # ``_session_scan_done``, not on the snapshot being None) so the
        # result sticks even when it is legitimately None (nothing loaded) -
        # and so a folder added / plugin toggled mid-session still reads as
        # "+N pending restart" rather than moving the boot baseline.
        if not self._session_scan_done:
            self._session_loaded_snapshot = self._compute_loaded_snapshot()
            self._session_scan_done = True

        # Reconcile discovered-but-undecided plugins into the active
        # Loadout. ``_reconcile_discovered_into_active`` calls
        # ``apply_op_result`` when work is needed (which itself emits
        # a refresh); when nothing needed reconciling we still emit a
        # refresh so the grid picks up new ``discovered_plugins``
        # keys (e.g. a rescan that found no truly-new plugins).
        if not self._reconcile_discovered_into_active():
            self._refresh()

    def _reconcile_discovered_into_active(self) -> bool:
        """Auto-enable any discovered Plugin that has no decision yet.

        A "decision" is an entry for the Plugin Name in either the
        active Loadout's plugins map or the resolved Global plugins
        map. Plugins on disk with no decision sit in a UI limbo:
        ``resolved_active_for_diff`` defaults them to ``enabled=True``
        so they appear in the pill grid as "pending enable" and the
        banner counts them as +N - but ``is_active_dirty`` doesn't
        see them (they aren't in ``active_model.plugins``), so Save
        stays locked and the user can never commit them. Restart →
        same limbo → infinite loop.

        Fix: at every scan_and_refresh (bootstrap, folder-add, explicit
        rescan) walk the discovered set and write an explicit
        ``enabled=True`` entry into the active Loadout for anything new.
        The active model becomes dirty in the same breath, Save lights
        up - then the explicit Save flushes via the wiring layer's
        chain-bridge.

        Returns ``True`` when a reconciliation actually mutated the
        active model (the caller can skip its own refresh), ``False``
        when nothing needed reconciling.
        """
        from NukeSurvivalLoadout.constants import RESERVED_LOADOUT_STEM

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
        # The reconcile only writes to the in-memory model; the on-disk
        # file stays sparse until the user Saves. Refresh the saved
        # baseline so the auto-added entries don't make the loadout
        # falsely read as dirty on every restart - for an explicit
        # gesture (folder-add) the wiring layer marks the new names as
        # force-dirty separately so Save still lights up.
        self._snapshot_baseline_for_active()
        self._refresh()
        return True

    def on_pill_info(self, plugin_name: str) -> None:
        """Pill info button → side panel Info tab.

        Reads the plugin's ``README.md`` (or ``readme.md``,
        case-insensitive lookup) from
        its on-disk folder via :attr:`discovered_plugins`, builds a
        :class:`PluginDetail`, and pushes it into the side panel's
        Info tab.

        Failure modes (no plugin entry, missing README, unreadable
        file) all degrade gracefully - the Info tab gets a clear
        "(no README)" placeholder rather than raising.
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
        from NukeSurvivalLoadout.ui.side_panel import PluginDetail

        side_panel.show_info(
            PluginDetail(
                plugin_name=plugin_name,
                provenance=provenance,
                body=body,
            )
        )
        self._push_active_chips(info_plugin=plugin_name, menu_plugin=None)

    def on_pill_menu(self, plugin_name: str) -> None:
        """Pill menu button → side panel Menu tab.

        Reads the Plugin's ``menu.py`` from its on-disk folder and shows it
        in the Menu tab (Monokai Python highlighting lives in the side
        panel). The chip is always clickable; when the folder has no
        ``menu.py`` the tab shows a clear "no menu.py" message rather than
        failing. Display-only: editing / saving ``menu.py`` is out of scope.
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

        from NukeSurvivalLoadout.ui.side_panel import PluginDetail

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
        """Re-read the README + menu.py for the plugins the Info / Menu tabs
        currently show, and re-render them in place (no tab switch).

        Wired to the side panel's ⟳ refresh button. Lets the user pick up
        external edits to either file without a full plugin rescan. Both
        tabs are refreshed regardless of which is active, so switching back
        to the other tab also shows fresh content.
        """
        side_panel = self._side_panel()
        if side_panel is None:
            return

        from NukeSurvivalLoadout.ui.side_panel import PluginDetail

        # Info tab - re-read README.
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

        # Menu tab - re-read menu.py.
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
        """Pill right-click "Open Plugin Folder" → reveal the Plugin's source
        folder in the OS file browser.

        Resolves the on-disk path via :attr:`discovered_plugins`. No-op
        (logged) when the plugin isn't in the current scan, or when its folder
        is gone (source-missing) - ``open_in_file_browser`` rejects a path that
        no longer exists, so a stale entry degrades to a warning rather than an
        empty file-browser window.
        """
        plugin = self.discovered_plugins.get(plugin_name)
        if plugin is None:
            _log.debug("open folder: plugin %r not in current scan", plugin_name)
            return
        from NukeSurvivalLoadout.ui.reveal import open_in_file_browser

        open_in_file_browser(plugin.path)

    def on_pill_diagnostic(self, plugin_name: str) -> None:
        """Pill diagnostic button → side panel Log tab.

        Under the runnable-python-loadout-chain architecture
        NSL no longer captures per-plugin load tracebacks (Nuke's
        walker is the loader; a failing init.py crashes the interpreter
        before any Python-level hook can record the failure). The Log
        tab therefore carries an honest "no diagnostic captured this
        session" message keyed on the plugin's name + source. The diag
        chip on the pill itself is no longer rendered as actionable
        for non-missing pills; this method stays as a defensive fallback
        in case a stale signal still fires.
        """
        side_panel = self._side_panel()
        if side_panel is None:
            return

        from NukeSurvivalLoadout.ui.side_panel import PluginDetail

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
        """Resolve the panel's :class:`SidePanel` via the parent widget.

        ``attach_parent_widget`` hands us the whole panel; the panel
        exposes its side panel as ``self.side_panel``. Returns ``None``
        when not attached (tests / headless paths).
        """
        parent = getattr(self, "_parent_widget", None)
        if parent is None:
            return None
        return getattr(parent, "side_panel", None)

    def _push_active_chips(self, *, info_plugin, menu_plugin) -> None:
        """Highlight at most one chip on at most one pill in the grid.

        Paired with
        ``LoadoutPanel._apply_active_chips_to_grid``. The two callbacks
        (info-button + menu-button) each invoke this helper with their
        own plugin_name and a ``None`` for the other so the previously
        lit chip clears in the same paint pass.

        No-op when not attached to a panel (tests / headless paths).
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
        """Locate and read the plugin's ``menu.py`` (case-insensitive).

        Returns ``(body, path)``: ``body`` is the raw source on success or a
        plain-language message on miss / unreadable; ``path`` is the absolute
        path to the file when found and readable, else ``None`` (so the Menu
        tab's Open button knows whether there is a file to open). Nuke
        conventionally names the file ``menu.py``; we match case-insensitively
        so an oddly-cased file is still surfaced.
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

        Folder card's Select button calls this; the wiring layer pushes
        the returned list to ``grid.select_keys``. Registry owns the
        domain knowledge (which plugins came from which folder); the
        wiring layer owns the UI mutation.

        Special case: the synthetic Global Plugins row identifies
        itself via :data:`GLOBAL_PLUGINS_FOLDER_SENTINEL` and resolves
        out of ``global_model.plugins``. A name also present in a user
        Plugins Folder is excluded there: the scanner's shadowing rule
        makes the user folder the pill's owner (one pill per name, owned
        by the top-most folder), so the Global row's folder-scoped
        actions must not claim it.
        """
        from NukeSurvivalLoadout.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
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
        """Folder-row right-click "Open Folder" → reveal *path* in the OS file
        browser. The synthetic Global Plugins row carries a marker rather
        than a real path (the Global row shows a friendly label, not a raw
        path), so it
        is skipped here as a second line of defence - the row also suppresses
        its own context menu for that case.
        """
        from NukeSurvivalLoadout.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL

        if not path or path == GLOBAL_PLUGINS_FOLDER_SENTINEL:
            _log.debug("open folder: skipping non-path row %r", path)
            return
        from NukeSurvivalLoadout.ui.reveal import open_in_file_browser

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
        """Install (or replace) the panel-side refresh callback.

        Called by the panel during ``__init__`` after the widget tree
        exists but before ``_wire_signals``. Idempotent.
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
    # Saved baseline - per-loadout on-disk state snapshot used
    # by the pill-diff tint + banner pending-count derivation.
    # ------------------------------------------------------------------

    def _snapshot_baseline_for_active(self) -> None:
        """Capture the currently-active loadout's baseline for diff math.

        Called from __init__ (initial baseline) and from apply_op_result
        on switch / save / import / rename. For named user Loadouts the
        baseline is the on-disk state - the op either just wrote to
        disk or just read from disk, so ``active_model`` equals disk.

        ``Global`` and ``Custom`` baseline against the resolved Global
        model - Global because that IS its own baseline, and Custom
        because the wildcard slot is conceptually "drift from Global"
        (Custom never persists to disk). The Revert button
        reads ``is_active_dirty`` against this baseline; the (*) on the
        Custom row is decoupled (always shown, see ``state.py``).
        """
        from NukeSurvivalLoadout.constants import DEFAULT_CUSTOM_LOADOUT_STEM

        stem = self._active_stem()
        # The folder baseline rides along with the model baseline:
        # Revert restores the Plugins Folder list to this same moment.
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
        """The saved-on-disk baseline for the active loadout.

        Returns ``None`` when no baseline has been captured (e.g. the
        loadout was never saved and the active model is still ``None``).

        The banner pending-change count + pill tint are driven by
        :attr:`session_loaded_baseline` (a fixed boot-time baseline);
        this property is retained for any future "save your edits" UX
        that genuinely needs per-loadout saved state.
        """
        return self._saved_baselines.get(self._active_stem())

    def _compute_loaded_snapshot(self) -> Optional[LoadoutFile]:
        """Freeze what THIS Nuke session actually loaded - from load-truth.

        Ground truth is ``nuke.pluginPath()``: the loadout chain runs in the
        same interpreter the panel lives in, so every plugin its
        ``nsl_pluginAddPath`` call loaded is on Nuke's plugin path right now,
        and every disabled/skipped one is not. We intersect that live path set
        with ``discovered_plugins`` (matched on each plugin's own folder
        ``path``) - the result is exactly "loaded this session". This is
        deliberately not "the effective ENABLED set" of the loadout model:
        that would conflate "what will load next restart" with "what loaded
        this session", and would lie whenever the viewed loadout differs from
        the boot-active one (e.g. delete ``loadouts/``, then create + switch
        to a loadout mid-session loaded nothing, yet would report the whole
        enabled set).

        Called once from the first ``scan_and_refresh`` and then frozen, so a
        mid-session folder-remove / pill-toggle doesn't move the boot baseline
        (a removed plugin still loaded this session; it just leaves the grid).

        Falls back to the effective-enabled derivation when ``nuke`` is absent
        or ``pluginPath`` is unavailable (headless / tests).

        Prefers the boot-time manifest when present. The
        loadout file's ``nsl_*`` helpers stamp ``nuke._nsl_loaded_session`` at
        the actual ``pluginAddPath`` call - captured at Nuke start, before the
        panel exists, and immune to a mid-session folder delete (a removed
        folder leaves the grid but stays in the manifest, so the "Loaded"
        count never under-reports). The live ``pluginPath`` intersection below
        is the fallback for loadout files generated before the recorder landed
        (their file prefix still carries the old helper), and the
        effective-enabled derivation is the final fallback (headless / tests).
        """
        manifest = self._nsl_session_manifest()
        if manifest is not None:
            plugins: dict[str, PluginEntry] = {}
            for item in manifest:
                name = item.get("name")
                if not name:
                    continue
                # The manifest IS load-truth: a recorded name had
                # ``pluginAddPath`` called on it, so it loaded -
                # ``enabled=True`` unconditionally. Adopting the resolved
                # active model's entry here (the previous behaviour) let a
                # disabled-in-loadout flag veto reality: in panic the
                # Global layer loads a user-disabled shadowed name (the
                # user chain never runs, nothing claims it), and the
                # Summary undercounted it as not-loaded.
                plugins[name] = PluginEntry(
                    enabled=True, gui_only=bool(item.get("gui"))
                )
            return LoadoutFile(name="<session>", plugins=plugins) if plugins else None

        loaded_paths = self._nuke_loaded_paths()
        if loaded_paths is None:
            # No Nuke / no pluginPath - headless or test. Fall back to the
            # effective-enabled derivation so non-GUI contexts still resolve.
            resolved = self.resolved_active_for_diff
            if resolved is None:
                return None
            plugins = {
                name: entry
                for name, entry in resolved.plugins.items()
                if entry.enabled
            }
            return LoadoutFile(name="<session>", plugins=plugins) if plugins else None

        # Load-truth path: a discovered plugin counts as loaded iff its own
        # folder path is on Nuke's live plugin path. ``resolved`` supplies the
        # entry shape (gui_only etc.) when present; default to a plain enabled
        # entry for anything loaded that the model doesn't explicitly carry.
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
        """Return the canonicalised set of folders on Nuke's live plugin path.

        Keys are ``canon_for_compare`` forms (case-folded on Windows) so
        membership tests don't miss on drive-letter/slash-case quirks in
        what ``nuke.pluginPath()`` echoes back.

        ``None`` (not an empty set) signals "Nuke unavailable" so the caller
        can fall back; an empty set is a legitimate "Nuke loaded nothing".
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

        The loadout file's ``nsl_*`` helpers stamp ``nuke._nsl_loaded_session``
        (a list of ``{"name", "path", "gui"}`` dicts) at each ``pluginAddPath``
        call - see :data:`loadout_file._HELPER_DEF`. ``None`` means "no
        manifest" (headless, or a loadout file generated before the recorder
        landed) and signals the caller to fall back to the live ``pluginPath``
        intersection. A present-but-empty list is not expected in practice: the
        attribute is created lazily on the first recorded load.
        """
        try:
            import nuke  # noqa: PLC0415 - only present inside a Nuke session
        except ImportError:
            return None
        rec = getattr(nuke, "_nsl_loaded_session", None)
        return rec if isinstance(rec, list) else None

    @property
    def session_loaded_baseline(self) -> Optional[LoadoutFile]:
        """LoadoutFile representing what NSL actually loaded at boot.

        NSL does not maintain a
        per-plugin loaded-set registry - Nuke's NUKE_PATH walker is the
        loader. Instead, the first boot ``scan_and_refresh`` freezes the
        effective enabled set into ``_session_loaded_snapshot`` (see
        :meth:`_compute_loaded_snapshot`); that snapshot IS the baseline.
        It includes the sparse loadout-file's scan-loaded defaults, which
        the older ``boot_active``/``boot_global`` derivation missed (under
        the sparse model ``boot_active`` is empty for an all-default loadout,
        so every default-on plugin wrongly read as "pending restart").

        Falls back to the boot-model derivation when no scan pass has run
        (headless contexts, tests that never scanned). Returns ``None`` when
        nothing resolves - callers treat ``None`` as "empty baseline",
        mirroring the :func:`pending_diff` convention.
        """
        # Once the boot scan has run, its snapshot IS the truth - including a
        # None result, which means "nothing actually loaded this session"
        # (fresh session / plugins added but not yet on nuke.pluginPath()).
        # Returning it verbatim (not falling through) is what stops the panel
        # from claiming the active loadout's declared-enabled plugins loaded
        # when they did not.
        if self._session_scan_done:
            return self._session_loaded_snapshot

        # Fallback: no boot scan ran (tests / degraded). Derive from the
        # frozen boot models.
        boot_eff: dict[str, PluginEntry] = {}
        if self.boot_global is not None:
            boot_eff.update(self.boot_global.plugins)
        if self.boot_active is not None:
            boot_eff.update(self.boot_active.plugins)
        if not boot_eff:
            return None
        # Filter to enabled entries - the baseline is "what loaded",
        # not "what was declared". Disabled-in-the-loadout entries
        # never reach the walker.
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
        # See ``prompt_add_folder`` - prefer Nuke's native browser, fall
        # back to the Qt-themed picker outside Nuke.
        try:
            import nuke  # noqa: PLC0415
        except ImportError:
            nuke = None  # type: ignore[assignment]

        if nuke is not None:
            try:
                # ``nuke.getFilename`` takes a glob pattern; pluck the
                # first one from the Qt ``;;``-separated filter so the
                # extension hint carries through (e.g. ``*.py``).
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
        # See ``prompt_add_folder`` - prefer Nuke's native browser, fall
        # back to the Qt-themed picker outside Nuke.
        try:
            import nuke  # noqa: PLC0415
        except ImportError:
            nuke = None  # type: ignore[assignment]

        if nuke is not None:
            # This is a save-style prompt
            # (user types a new filename), so use Nuke's save-mode
            # browser when available. ``nuke.getFilename`` accepts
            # ``type="save"`` to switch to the save dialog (allows
            # typing a non-existent name, default button reads
            # "Save"). Older builds that don't accept the kwarg
            # fall through to a positional retry, then to the Qt
            # save dialog if even that fails.
            try:
                pattern = _glob_from_qt_filter(name_filter)
                try:
                    chosen = nuke.getFilename(
                        title, pattern, default_name, type="save"
                    )
                except TypeError:
                    # Older Nuke signature: no ``type`` kwarg.
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
        """Apply (undo or redo) a single undo entry.

        ``pill_toggle`` entries - and the ``bulk_*`` kinds, which carry
        the identical plugin / previous / next payload - replay in
        memory, matching the pill-toggle contract: edits are held in
        memory; on-disk persistence happens only on Save / Save As.
        Coalesced bulk entries (``{"bulk": True, "entries": [...]}``)
        replay their sub-entries - reverse order for undo, recorded
        order for redo, so same-plugin sequences inside one bulk land
        correctly - with a single refresh at the end. ``folder_op``
        entries route to :meth:`_replay_folder_op` and
        ``panic_toggle`` to :meth:`_replay_panic_toggle`; both write
        through to the dispatcher, because the ops they reverse do.
        Other entry kinds are logged and skipped - they aren't pushed
        by any wiring helper today, so unknown kinds mean a contract
        drift the user should hear about.
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

        # Undo: restore previous; Redo: restore next.
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

        # In-memory mutation only; the active Loadout is dirty after a
        # replay, same as after a normal pill toggle.
        self.active_model = dataclasses.replace(self.active_model, plugins=plugins)
        self._is_dirty = True
        if refresh:
            self._refresh()

    def _replay_model_reset(
        self, entry: Mapping[str, Any], *, direction: str, refresh: bool = True
    ) -> None:
        """Undo / redo a whole-model swap (Reset Global Plugins to Default).

        The reset removes every Global entry from the active Loadout's
        plugins dict, so a per-entry delta would have to record each
        removed key. A wholesale model snapshot is simpler and exact:
        undo restores the pre-reset model, redo restores the post-reset
        one. In-memory only, matching the live handler - the reset
        doesn't touch disk until Save.
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
        """Undo / redo a Panic flip by re-running ``set_panic``.

        Panic lives in the dispatcher and writes through immediately,
        so replay writes through too. The active model is preserved
        across the flip, same as the live Panic handler; the panel
        refresh re-syncs the Panic button via ``set_panic_engaged``.
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
        """Reverse (or re-apply) one compound folder operation.

        A folder add / remove / reorder fans out into the scanner dirs,
        the dispatcher folder authority (synced into every loadout
        file), a rescan, and the ceremonial-save set. Replay reverses
        all of it:

        * The folder LIST adjusts by inverse delta, not snapshot -
          folders are global across loadouts while undo stacks are
          per-loadout, so restoring a full list captured on this
          loadout's stack would clobber folder changes made later from
          another loadout.
        * The MODEL and ceremonial-save set restore from the entry's
          wholesale per-side snapshots - both are per-loadout, and
          stack ordering guarantees later edits were already undone.

        Order matters: dirs first, then persist + rescan (the scan's
        reconcile pass may write auto-enable entries into
        ``active_model``), then the model snapshot LAST so it wins.
        """
        undo = direction == "undo"
        op = entry.get("op")
        path = entry.get("path")
        dirs = list(getattr(self, "user_plugin_dirs", []) or [])

        def _with_path_inserted(seq: List[str]) -> List[str]:
            # Re-insert ``path`` at its recorded position, clamped so a
            # list reshaped by other loadouts' folder ops still accepts it.
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

    Qt filters look like ``"Loadout files (*.py);;All (*)"``. Nuke's
    :func:`nuke.getFilename` takes a single glob (e.g. ``"*.py"``).
    Returns the first glob token found inside the parentheses of the
    first filter clause, or ``None`` when no extension can be inferred.
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
    # Multiple globs separated by spaces ("*.py *.txt") - Nuke's
    # browser accepts only one pattern; first one wins.
    first_glob = inside.split()[0]
    return first_glob or None


def _canonical_folder_var(index: int) -> str:
    """Positional ``plugins_X`` var for the folder at ``index``.

    0->A ... 25->Z, 26->AA, 27->AB ... - same ordering as
    ``folder_ops._next_folder_var`` so the dispatcher's canonical var
    names line up with what add-folder assigned.
    """
    if index < 26:
        return f"plugins_{chr(ord('A') + index)}"
    hi, lo = divmod(index - 26, 26)
    return f"plugins_{chr(ord('A') + hi)}{chr(ord('A') + lo)}"


def _clone_loadout(model: Optional[LoadoutFile]) -> Optional[LoadoutFile]:
    """Deep-enough copy for boot snapshots.

    ``LoadoutFile`` carries a dict of immutable :class:`PluginEntry`
    instances; copying the dict shell is sufficient for snapshot
    semantics because no caller mutates ``PluginEntry`` in place.
    """
    if model is None:
        return None
    return LoadoutFile(name=model.name, plugins=dict(model.plugins))


def _normalised_plugins(
    model: LoadoutFile, global_model: Optional[LoadoutFile]
) -> Mapping[str, PluginEntry]:
    """Drop ``model.plugins`` entries that match the plugin's default.

    Two models with identical *effective* state should compare equal
    even if one has explicit default-valued entries and the other has
    no entry for the same key. This helper produces a "minimal sparse"
    representation suitable for value comparison.

    Default rule (sparse-diff resolution):

    * If ``global_model`` has an entry for the key → that entry is
      the default (Global plugin).
    * Otherwise → ``PluginEntry(enabled=True, gui_only=False)``. This
      mirrors the chain-format ``PluginEntry`` default (``disabled=False
      gui=False`` in ``NukeSurvivalLoadout/boot/loadout_file.py``). A scanner-discovered
      plugin with no explicit decision in active or Global loads by
      default, so an explicit ``enabled=True`` entry the user wrote
      via pill-toggle round-trip is semantically equivalent to "no
      entry at all." The default must be ``enabled=True`` (not
      ``enabled=False``) or round-trip clean breaks for user-added
      plugins. Folder-add staying dirty is handled by
      ``_force_dirty_plugins`` (ceremonial-save set), not by this
      default.

    An ``active_model`` entry equal to its default is semantically
    equivalent to no entry at all - both produce the same runtime
    behaviour. Dropping defaults lets :attr:`Registry.is_active_dirty`
    return False when the user toggles a user-added plugin on then
    back off (round-trip clean).
    """
    result: dict[str, PluginEntry] = {}
    global_plugins = global_model.plugins if global_model is not None else {}
    implicit_default = PluginEntry(enabled=True, gui_only=False)
    for key, entry in model.plugins.items():
        default = global_plugins.get(key, implicit_default)
        if entry != default:
            result[key] = entry
    return result


