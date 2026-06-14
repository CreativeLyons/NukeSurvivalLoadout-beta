"""Bootstrap a production :class:`Registry` from on-disk state.

The panel calls :func:`build_registry_for_panel` during ``__init__``
to populate its registry from:

* The dispatcher init.py at ``~/.nuke/loadouts/init.py`` (or first-run
  defaults when the file is missing). The dispatcher holds the panic
  flag and the active-loadout pointer.
* The resolved Global layer: the chain head ``<repo>/Global/init.py``
  names the Global plugins and loadout dirs (the boot session record
  carries the head's actual resolved values; a static head parse is the
  offline fallback for each), the optional ``Global/Global_Loadout/init.py``
  supplies per-plugin directives (parsed, never executed in this role),
  and a scan of the plugins dir supplies the default-on names.
* The active user Loadout file from disk (or ``None`` when Global
  is the active pointer).

User-added plugin source folders are derived from the dispatcher's
folder declarations (or the active loadout file's folders as a
fallback).

Failures here are funnelled to the degraded-panel path that
:func:`NukeSurvivalLoadout.ui.degraded.wire_degraded` already gates on
``panel.degraded`` - the bootstrap surfaces a structured error rather
than raising into ``panel.__init__``.

No ``import nuke`` at module scope - Nuke integration lives in the
top-level ``init.py`` / ``menu.py``; the boot session record is read
through a guarded import.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from NukeSurvivalLoadout.boot.dispatcher import (
    DispatcherState,
    read_dispatcher,
    write_dispatcher,
)
from NukeSurvivalLoadout.boot.global_loader import read_head_config
from NukeSurvivalLoadout.boot.loadout_file import (
    read_loadout as read_loadout_model,
)
from NukeSurvivalLoadout.constants import (
    DEFAULT_CUSTOM_LOADOUT_STEM,
    GLOBAL_FOLDER_NAME,
    GLOBAL_LOADOUT_DIR_NAME,
    GLOBAL_PLUGINS_VAR_NAME,
    RESERVED_LOADOUT_STEM,
)
from NukeSurvivalLoadout.data.loadout_file import LoadoutFile, PluginEntry
from NukeSurvivalLoadout.domain.scanner import scan_folder
from NukeSurvivalLoadout.ui.registry import Registry

__all__ = ["BootstrapResult", "build_registry_for_panel"]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of :func:`build_registry_for_panel`.

    ``registry`` is always populated - on hard failure the registry
    carries first-run defaults so the panel still constructs. ``error``
    is a human-readable string the panel's degraded mode can surface;
    ``None`` means a clean bootstrap.
    """

    registry: Registry
    error: Optional[str] = None


def build_registry_for_panel(
    *,
    loadouts_dir: Path,
    dispatcher_path: Optional[Path] = None,
    refresh_callback: Optional[Callable[[], None]] = None,
    parent_widget: Optional[Any] = None,
) -> BootstrapResult:
    """Assemble a :class:`Registry` from on-disk + env state.

    ``dispatcher_path`` is optional - defaults to ``loadouts_dir/init.py``
    (the canonical chain location). On hard failure (unreadable
    dispatcher, malformed Global) the registry carries first-run
    defaults and the result's ``error`` is populated.
    """
    loadouts_dir = Path(loadouts_dir)
    loadouts_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the Global layer ONCE: the model feeds the read-only Global
    # row; the plugins dir feeds the registry so its rescan walks the same
    # folder the boot-side Global head loaded. Without the dir reaching
    # the registry, ``discovered_plugins`` misses every Global Plugin →
    # info button breaks on Global pills.
    nsl_root = Path(__file__).resolve().parent.parent.parent
    global_layer = _load_global(nsl_root)

    dispatcher_file = Path(dispatcher_path or (loadouts_dir / "init.py"))
    state, state_error = _load_dispatcher_state(dispatcher_file)
    # Invariant: "Custom" is the in-memory wildcard - never a real on-disk
    # loadout. An older build wrote ``ACTIVE_LOADOUT="Custom"`` on a
    # dropdown-switch; normalise any such stale pointer to "" so
    # ``_load_active`` doesn't chase a non-existent ``loadouts/Custom/``
    # folder (surfacing a spurious "missing" error) and the pending-Custom
    # synthesis below applies instead.
    if state.active == DEFAULT_CUSTOM_LOADOUT_STEM:
        state.active = ""
    # Case B normalize-and-persist: when the Global copy of
    # ``Global_Loadout`` exists, the user-land loadout of the same name is
    # hidden and never activatable - a stale dispatcher pointer at it is
    # normalised to "" (the read-only Global view) and WRITTEN back once
    # so boot converges with what the panel shows.
    if state.active == GLOBAL_LOADOUT_DIR_NAME and global_layer.has_loadout_copy:
        state.active = ""
        try:
            write_dispatcher(str(dispatcher_file), state)
        except OSError as exc:
            _log.warning("could not persist Global_Loadout pointer reset: %s", exc)
    global_model = global_layer.model
    global_error = global_layer.error
    active_model, legacy_dirs, active_error = _load_active(loadouts_dir, state)

    # Folder authority is the dispatcher (``DispatcherState.folders``), so the
    # Plugins Folder list survives regardless of which loadout is active -
    # including the unsaveable Custom slot, which is what previously dropped
    # folders on panel close/reopen. Fall back to the active loadout's own
    # folder decls ONLY when the dispatcher has none yet (a tree written
    # before folders moved to the dispatcher); the next folder op / save
    # migrates them up to the dispatcher.
    if state.folders:
        user_plugin_dirs = [decl.path for decl in state.folders]
    else:
        user_plugin_dirs = legacy_dirs

    # Folders configured but NO saved loadout active → synthesize an
    # in-memory pending Custom so the discovered plugins default ON
    # (enabled + pending, ready for a single Save), matching the first-add
    # experience (``NukeSurvivalLoadout.ui.wiring.events._add_folder_in_memory``).
    #
    # Without this, ``active_model`` is None, so ``ui.state.pill_state_from``
    # defaults USER_ADDED plugins to DISABLED ("Global-active honesty").
    # That made a reopen after "Don't Save" show every discovered plugin
    # OFF (red ✕): a Save would then have persisted them off, forcing the
    # user to re-enable each by hand - the plugins are pending precisely
    # because no loadout is saved yet, so the honest default is ON+pending
    # awaiting Save, not OFF.
    #
    # The synthesized active is IN MEMORY ONLY - the on-disk dispatcher
    # keeps ``ACTIVE_LOADOUT=""`` (Custom never persists as the active
    # pointer). ``Registry._reconcile_discovered_into_active`` (run by the
    # ``scan_and_refresh`` below) auto-enables the discovered plugins now
    # that the active stem is Custom. The "no folders" first-run / Global
    # case is untouched (``user_plugin_dirs`` empty → no synthesis), so the
    # "I want just the Global view" selection still defaults user plugins
    # off as before.
    if not state.active and user_plugin_dirs:
        state.active = DEFAULT_CUSTOM_LOADOUT_STEM
        active_model = LoadoutFile(
            name=DEFAULT_CUSTOM_LOADOUT_STEM, plugins={}
        )

    error_lines = [m for m in (state_error, global_error, active_error) if m]
    error = "; ".join(error_lines) if error_lines else None

    registry = Registry(
        loadouts_dir=loadouts_dir,
        state=state,
        active_model=active_model,
        global_model=global_model,
        refresh_callback=refresh_callback,
        parent_widget=parent_widget,
        global_plugin_dirs=global_layer.plugin_dirs,
        user_plugin_dirs=user_plugin_dirs,
        global_loadout_copy_exists=global_layer.has_loadout_copy,
        global_loadout_error=global_layer.error,
    )

    # Populate the discovered_plugins set
    # at bootstrap so a returning user (loadout already lists their
    # Plugins Folders) sees pills immediately on panel open, not just
    # after a manual Rescan. Done before the refresh_callback is wired
    # so a single initial refresh in panel.__init__ picks up the
    # populated set in one pass.
    try:
        registry.scan_and_refresh()
    except Exception as exc:  # noqa: BLE001 - refresh must not break bootstrap
        scan_error = f"initial scan failed: {exc}"
        error = scan_error if error is None else f"{error}; {scan_error}"

    return BootstrapResult(registry=registry, error=error)


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _load_dispatcher_state(
    dispatcher_path: Path,
) -> tuple[DispatcherState, Optional[str]]:
    """Read the dispatcher state, falling back to defaults on hard error.

    The dispatcher reader already tolerates missing / SyntaxError-laden
    files by returning ``DispatcherState()`` defaults; this wrapper
    catches OSError for filesystem-level failures (perm denied, etc.)
    and surfaces them as a structured error string for the degraded
    panel.
    """
    try:
        return read_dispatcher(str(dispatcher_path)), None
    except OSError as exc:
        _log.warning("dispatcher unreadable; falling back to defaults: %s", exc)
        return DispatcherState(), f"dispatcher unreadable: {exc}"


@dataclass(frozen=True)
class _GlobalLayer:
    """Resolved Global layer for panel consumption.

    ``model`` is ``None`` when no Global layer is configured (no chain
    head on disk, or the plugins dir holds nothing and no Global Loadout
    names anything). ``plugin_dirs`` carries the resolved Global plugins
    dir whenever the head exists, even when empty, so a mid-session
    rescan can pick up freshly-dropped plugins. ``has_loadout_copy`` is
    the case A/B switch for the ``Global_Loadout`` name rules.
    """

    model: Optional[LoadoutFile] = None
    plugin_dirs: List[Path] = field(default_factory=list)
    has_loadout_copy: bool = False
    error: Optional[str] = None


def _recorded_global_dir() -> Optional[str]:
    """The Global plugins dir the boot loader recorded, when inside Nuke."""
    try:
        from NukeSurvivalLoadout.boot.session_record import recorded_global_dir
    except Exception:  # noqa: BLE001 - no nuke module outside Nuke
        return None
    try:
        return recorded_global_dir()
    except Exception:  # noqa: BLE001 - record read must never block boot
        return None


def _recorded_global_loadout_dir() -> Optional[str]:
    """The Global loadout dir the boot loader recorded, when inside Nuke."""
    try:
        from NukeSurvivalLoadout.boot.session_record import (
            recorded_global_loadout_dir,
        )
    except Exception:  # noqa: BLE001 - no nuke module outside Nuke
        return None
    try:
        return recorded_global_loadout_dir()
    except Exception:  # noqa: BLE001 - record read must never block boot
        return None


def _load_global(nsl_root: Path) -> _GlobalLayer:
    """Resolve the Global layer: head declarations + loadout parse + scan.

    The Global model mirrors what the boot loader resolves: every plugin
    folder inside the Global plugins dir defaults on; the optional
    ``Global_Loadout/init.py`` contributes per-name directives under the
    ``global_plugins`` var (disabled / GUI-only). Names the loadout
    mentions that aren't on disk still enter the model so the directive
    isn't silently dropped from the read-only Global view.
    """
    head_path = nsl_root / GLOBAL_FOLDER_NAME / "init.py"
    if not head_path.is_file():
        return _GlobalLayer(model=None, plugin_dirs=[], has_loadout_copy=False)

    config = read_head_config(str(head_path))
    plugins_dir = _recorded_global_dir() or config.plugins_dir
    loadout_dir = _recorded_global_loadout_dir() or config.loadout_dir
    loadout_init = Path(loadout_dir) / "init.py"
    has_loadout_copy = loadout_init.is_file()

    error: Optional[str] = None
    plugins: dict[str, PluginEntry] = {}
    try:
        for plugin in scan_folder(plugins_dir):
            plugins[plugin.name] = PluginEntry(enabled=True, gui_only=False)
    except (OSError, ValueError) as exc:
        _log.warning("Global plugins dir scan failed: %s", exc)

    if has_loadout_copy:
        try:
            chain_model = read_loadout_model(str(loadout_init))
        except (OSError, SyntaxError) as exc:
            # Debug, not warning: the terminal stays clean by design;
            # the Summary tab's Warnings section is
            # the user-facing surface for this condition.
            _log.debug("Global Loadout unreadable: %s", exc)
            error = f"Global Loadout unreadable: {exc}"
            chain_model = None
        if chain_model is not None:
            for entry in chain_model.plugins:
                if entry.folder_var != GLOBAL_PLUGINS_VAR_NAME:
                    continue
                plugins[entry.name] = PluginEntry(
                    enabled=not entry.disabled,
                    gui_only=entry.gui,
                )

    model = (
        LoadoutFile(name=RESERVED_LOADOUT_STEM, plugins=plugins)
        if plugins
        else None
    )
    return _GlobalLayer(
        model=model,
        plugin_dirs=[Path(plugins_dir)],
        has_loadout_copy=has_loadout_copy,
        error=error,
    )


def _load_active(
    loadouts_dir: Path,
    state: DispatcherState,
) -> tuple[Optional[LoadoutFile], List[str], Optional[str]]:
    """Read the active loadout file and derive its user plugin folder paths.

    Returns ``(active_model, user_plugin_dirs, error)``:
      * ``active_model`` - ``LoadoutFile`` shape used by the panel
        layer (or ``None`` when Global is active).
      * ``user_plugin_dirs`` - absolute paths derived from the active
        loadout's ``LoadoutModel.folders`` (or empty list when Global
        is active or no folders are declared).
      * ``error`` - human-readable error string for the degraded panel
        when the active loadout couldn't be read.

    The active loadout is a Python file at
    ``<loadouts_dir>/<stem>/init.py`` containing ``plugins_X`` folder
    vars + ``nsl_pluginAddPath(...)`` calls.
    """
    stem = state.active
    if not stem or stem == RESERVED_LOADOUT_STEM:
        return None, [], None

    new_path = loadouts_dir / stem / "init.py"
    if not new_path.is_file():
        _log.warning(
            "active loadout missing: %s; falling back to Global.", new_path
        )
        return None, [], f"active loadout missing: {stem}"
    try:
        model = read_loadout_model(str(new_path))
    except (OSError, SyntaxError) as exc:
        _log.warning("active loadout init.py unreadable: %s", exc)
        return None, [], f"active loadout init.py unreadable: {exc}"
    # Bridge ``LoadoutModel.folders`` (list of FolderDecl) to the flat
    # ``user_plugin_dirs`` list the registry expects. The ``global_plugins``
    # decl is NOT a user folder - it's the Global dir reference written
    # for Global-plugin overrides; the Global layer supplies that dir.
    user_dirs = [
        decl.path
        for decl in model.folders
        if decl.var != GLOBAL_PLUGINS_VAR_NAME
    ]
    # Bridge ``LoadoutModel.plugins`` (list of chain PluginEntry) to
    # the panel's ``LoadoutFile`` (dict of PluginEntry by name). Same
    # bridge as ``NukeSurvivalLoadout/ui/wiring/events.py::_chain_to_legacy``.
    bridged = LoadoutFile(
        name=stem,
        plugins={
            entry.name: PluginEntry(
                enabled=not entry.disabled,
                gui_only=entry.gui,
            )
            for entry in model.plugins
        },
    )
    return bridged, user_dirs, None
