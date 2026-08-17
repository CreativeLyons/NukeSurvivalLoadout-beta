"""Bootstrap a production :class:`Registry` from on-disk state.

The panel host calls :func:`build_registry_for_panel` before
``LoadoutPanel.__init__``. It reads three sources: the dispatcher at
``~/.nuke/loadouts/init.py``, the Global layer under ``<install>/Global``,
and the active user Loadout file.

A failure returns a structured error instead of raising into
``panel.__init__``. :func:`nsl.ui.degraded.wire_degraded` picks the error
up from ``panel.degraded``.

Never ``import nuke`` at module scope. The boot session record is read
through a guarded import.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from nsl.atomic_io import sweep_orphan_tmp
from nsl.boot.dispatcher import (
    BACKUP_SUFFIX,
    DispatcherState,
    read_dispatcher,
    write_dispatcher,
)
from nsl.boot.global_loader import read_head_config
from nsl.boot.loadout_file import (
    read_loadout as read_loadout_model,
)
from nsl.constants import (
    DEFAULT_CUSTOM_LOADOUT_STEM,
    GLOBAL_FOLDER_NAME,
    GLOBAL_LOADOUT_DIR_NAME,
    GLOBAL_PLUGINS_VAR_NAME,
    RESERVED_LOADOUT_STEM,
    install_root,
)
from nsl.data.loadout_file import LoadoutFile, PluginEntry
from nsl.domain.scanner import scan_folder
from nsl.ui.registry import Registry

__all__ = ["BootstrapResult", "build_registry_for_panel"]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of :func:`build_registry_for_panel`.

    ``registry`` is always set. On hard failure it carries first-run
    defaults, so the panel still constructs. ``error`` is ``None`` on a
    clean bootstrap, and otherwise a string for degraded mode to show.
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
    """Assemble a :class:`Registry` from on-disk state.

    ``dispatcher_path`` defaults to ``loadouts_dir/init.py``, the chain
    location. On hard failure the registry carries first-run defaults
    and ``error`` is set.
    """
    loadouts_dir = Path(loadouts_dir)
    loadouts_dir.mkdir(parents=True, exist_ok=True)

    # Panel construction is the safe moment to delete orphan ``*.tmp``
    # files. No NSL write is in flight, so every one of them is a dead
    # leftover. The same sweep inside a write path could delete a sibling
    # writer's live temp file (Issue 21).
    _sweep_orphans(loadouts_dir)

    # The registry needs the Global plugins dir, not only the model. Without
    # the dir, a rescan drops every Global Plugin from
    # ``discovered_plugins`` and the info button breaks on Global pills.
    nsl_root = install_root()
    global_layer = _load_global(nsl_root)

    dispatcher_file = Path(dispatcher_path or (loadouts_dir / "init.py"))
    state, state_error = _load_dispatcher_state(dispatcher_file)
    # ``Custom`` is in-memory only and never a folder on disk. An older build
    # wrote it as the active pointer. A stale one is reset to "" and written
    # back, because a memory-only reset leaves ``Custom`` on disk.
    if state.active == DEFAULT_CUSTOM_LOADOUT_STEM:
        state.active = ""
        try:
            write_dispatcher(str(dispatcher_file), state)
        except OSError as exc:
            _log.warning("could not persist Custom pointer reset: %s", exc)
    # The user-land ``Global_Loadout`` is hidden while the Global copy
    # exists. A pointer at it is reset to "" and written back. Boot then
    # agrees with the read-only Global view the panel shows.
    if state.active == GLOBAL_LOADOUT_DIR_NAME and global_layer.has_loadout_copy:
        state.active = ""
        try:
            write_dispatcher(str(dispatcher_file), state)
        except OSError as exc:
            _log.warning("could not persist Global_Loadout pointer reset: %s", exc)
    global_model = global_layer.model
    global_error = global_layer.error
    active_model, legacy_dirs, active_error = _load_active(loadouts_dir, state)

    # The dispatcher owns the folder list, so the Plugins Folders survive
    # any Loadout switch, Custom included. The Loadout's own decls are the
    # fallback for a tree written before folders moved to the dispatcher.
    if state.folders:
        user_plugin_dirs = [decl.path for decl in state.folders]
    else:
        user_plugin_dirs = legacy_dirs

    # Folders are configured but no Loadout is saved, so an in-memory
    # Custom stands in. Without it ``active_model`` stays None and the
    # pill grid defaults every user plugin to off. Nothing writes this
    # pointer to disk: ``persist_folder_authority`` maps ``Custom`` to "".
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

    # Scan here, so a returning user sees pills the moment the panel
    # opens and not after a manual Rescan.
    try:
        registry.scan_and_refresh()
    except Exception as exc:  # noqa: BLE001 - refresh must not break bootstrap
        scan_error = f"initial scan failed: {exc}"
        error = scan_error if error is None else f"{error}; {scan_error}"

    return BootstrapResult(registry=registry, error=error)


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _sweep_orphans(loadouts_dir: Path) -> None:
    """Reclaim orphaned ``*.tmp`` files under ``loadouts_dir``.

    ``sweep_orphan_tmp`` scans direct children only, so it runs once on
    ``loadouts_dir`` for the dispatcher temp and once per subfolder for
    the per-Loadout temps. A locked or vanished file must never block
    bootstrap, so every call logs its failure and moves on.
    """
    try:
        sweep_orphan_tmp(loadouts_dir)
    except OSError as exc:
        _log.debug("orphan tmp sweep skipped for %s: %s", loadouts_dir, exc)

    try:
        subfolders = [entry for entry in loadouts_dir.iterdir() if entry.is_dir()]
    except OSError as exc:
        _log.debug("could not enumerate loadout subfolders to sweep: %s", exc)
        return

    for subfolder in subfolders:
        try:
            sweep_orphan_tmp(subfolder)
        except OSError as exc:
            _log.debug("orphan tmp sweep skipped for %s: %s", subfolder, exc)


def _load_dispatcher_state(
    dispatcher_path: Path,
) -> tuple[DispatcherState, Optional[str]]:
    """Read the dispatcher state, with a structured error on damage.

    A missing file and a damaged file are not the same, so they are not
    conflated here:

    * Missing or clean -> ``(state, None)``. A normal boot.
    * Malformed -> ``(state, error)``. The file is on disk but does not
      parse, usually a hand-edit typo. The error puts the panel in
      degraded read-only mode, so the next write cannot reset panic,
      active and folders from a file read as empty.
    * OSError -> ``(default, error)``. Also surfaced to the panel.
    """
    try:
        state = read_dispatcher(str(dispatcher_path))
    except OSError as exc:
        _log.warning("dispatcher unreadable; falling back to defaults: %s", exc)
        return DispatcherState(), f"dispatcher unreadable: {exc}"

    if state.malformed:
        _log.warning(
            "dispatcher malformed (unparseable); entering degraded mode: %s",
            dispatcher_path,
        )
        return state, (
            f"dispatcher malformed (unparseable Python): {dispatcher_path}. "
            "Repair the file or reset it; the original was preserved as a "
            f"'{dispatcher_path.name}{BACKUP_SUFFIX}' side-copy before any rewrite."
        )

    return state, None


@dataclass(frozen=True)
class _GlobalLayer:
    """Resolved Global layer for panel consumption.

    ``model`` is ``None`` when nothing names a plugin, so either there is
    no chain head on disk, or the plugins dir and the Global Loadout are
    both empty. ``plugin_dirs`` is filled whenever the head exists, even
    for an empty dir, so a later rescan finds a plugin dropped in
    mid-session. ``has_loadout_copy`` drives the ``Global_Loadout`` name
    rules.
    """

    model: Optional[LoadoutFile] = None
    plugin_dirs: List[Path] = field(default_factory=list)
    has_loadout_copy: bool = False
    error: Optional[str] = None


def _recorded_global_dir() -> Optional[str]:
    """The Global plugins dir the boot loader recorded, when inside Nuke."""
    try:
        from nsl.boot.session_record import recorded_global_dir
    except Exception:  # noqa: BLE001 - no nuke module outside Nuke
        return None
    try:
        return recorded_global_dir()
    except Exception:  # noqa: BLE001 - record read must never block boot
        return None


def _recorded_global_loadout_dir() -> Optional[str]:
    """The Global loadout dir the boot loader recorded, when inside Nuke."""
    try:
        from nsl.boot.session_record import (
            recorded_global_loadout_dir,
        )
    except Exception:  # noqa: BLE001 - no nuke module outside Nuke
        return None
    try:
        return recorded_global_loadout_dir()
    except Exception:  # noqa: BLE001 - record read must never block boot
        return None


def _load_global(nsl_root: Path) -> _GlobalLayer:
    """Resolve the Global layer from the head, the Loadout and a scan.

    The model mirrors what the boot loader resolves. Every plugin folder
    in the Global plugins dir defaults on. The optional
    ``Global_Loadout/init.py`` then adds per-name directives under
    ``global_plugins``. A name it mentions enters the model even when it
    is not on disk, so the directive still shows in the Global view.
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
            # Debug, not warning. The terminal stays clean by design and
            # the Summary tab's Warnings section is the user-facing surface.
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
    """Read the active Loadout file and derive its plugin folder paths.

    The active Loadout is ``<loadouts_dir>/<stem>/init.py``. Returns
    ``(active_model, user_plugin_dirs, error)``:

      * ``active_model`` - the panel's ``LoadoutFile`` shape, or ``None``
        when Global is active.
      * ``user_plugin_dirs`` - paths from the Loadout's folder decls.
        Empty on Global, or when nothing is declared.
      * ``error`` - a string for the degraded panel when the read failed.
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
    # The ``global_plugins`` decl is not a user folder. It points at the
    # Global dir, which the Global layer already supplies.
    user_dirs = [
        decl.path
        for decl in model.folders
        if decl.var != GLOBAL_PLUGINS_VAR_NAME
    ]
    # The same bridge as ``nsl.ui.wiring.events._chain_to_legacy``.
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
