"""Global chain-head loader - boot-side loading for ``<repo>/Global/``.

``Global/init.py`` (the executable chain head) declares two folder paths
and calls :func:`nsl_load_global`. The loader:

  1. Resolves ``plugins`` / ``loadout`` relative to the head file's own
     folder (``./`` and bare-relative entries anchor there; ``~`` expands;
     absolute paths pass through unchanged).
  2. If ``<loadout>/init.py`` exists it is PARSED, never executed, via
     :func:`NukeSurvivalLoadout.boot.loadout_file.read_loadout`. The folder
     var named ``global_plugins`` binds to the freshly resolved plugins dir
     in memory; other folder vars keep their written literals. Nothing on
     disk is touched.
  3. Reads ahead: the dispatcher at ``~/.nuke/loadouts/init.py`` names the
     active user loadout; every plugin name that loadout will touch -
     explicitly mentioned (enabled OR disabled) or visible in a declared
     user folder it sweeps - belongs to the user's file and is skipped
     here, so each plugin name is added by exactly one file per session.
  4. ``nuke.pluginAddPath`` every enabled, unclaimed plugin folder,
     honoring ``disabled`` / ``gui`` directives, and records each load plus
     the resolved Global plugins dir via ``boot.session_record`` so the
     panel reads boot-time truth.
  5. With no loadout file, every plugin folder inside the plugins dir
     loads (``_`` / ``.`` prefixed folders skipped), minus user-claimed
     names. Zero-authoring default: drop plugins in, they all load.

Panic mode loads the Global layer in full with no claims: panic disables
user-added plugins only (the dispatcher skips the active loadout), so the
user's chain never runs and nothing is claimed.

``import nuke`` happens lazily inside the load path so the parsing helpers
(:func:`read_head_config`, :func:`resolve_global_path`) stay importable
from panel-side code and headless contexts.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from typing import Optional

from NukeSurvivalLoadout import log
from NukeSurvivalLoadout.boot.loadout_file import LoadoutModel, read_loadout
from NukeSurvivalLoadout.boot.dispatcher import read_dispatcher
from NukeSurvivalLoadout.constants import (
    GLOBAL_DEFAULT_LOADOUT_REL,
    GLOBAL_DEFAULT_PLUGINS_REL,
    GLOBAL_FOLDER_NAME,
    GLOBAL_PLUGINS_VAR_NAME,
    PLUGIN_FOLDER_IGNORE_NAMES,
    PLUGIN_FOLDER_IGNORE_PREFIXES,
    RESERVED_LOADOUT_STEM,
    loadouts_dir,
)

__all__ = [
    "GlobalHeadConfig",
    "nsl_load_global",
    "read_head_config",
    "resolve_global_path",
]


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_global_path(entry: str, base_dir: str) -> str:
    """Resolve one head-declared folder path against the head's folder.

    Rules (locked in the migration plan's decision log):
      * ``./foo`` (and any non-absolute path) → relative to ``base_dir``.
      * ``~/foo`` → home-expanded.
      * absolute → as-is.
    """
    expanded = os.path.expanduser(entry)
    if not os.path.isabs(expanded):
        expanded = os.path.join(base_dir, expanded)
    return os.path.normpath(expanded)


def _default_head_dir() -> str:
    """``<repo>/Global/`` derived from the package location.

    Fallback base when the caller's file location can't be determined.
    """
    package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(package_dir)
    return os.path.join(repo_root, GLOBAL_FOLDER_NAME)


def _resolve_base(base: Optional[str]) -> str:
    """Return the folder the head's relative paths anchor to.

    ``base`` may be the head file path or its folder. When ``None``, the
    calling file's location is derived from the caller's frame (the head
    calls :func:`nsl_load_global` directly); if that fails, fall back to
    the shipped ``<repo>/Global/`` location.
    """
    if base:
        base = os.path.abspath(os.path.expanduser(str(base)))
        return os.path.dirname(base) if os.path.isfile(base) else base
    import sys

    try:
        caller = sys._getframe(2)
        caller_file = caller.f_globals.get("__file__")
    except Exception:  # noqa: BLE001 - frame introspection is best-effort
        caller_file = None
    if caller_file:
        return os.path.dirname(os.path.abspath(caller_file))
    return _default_head_dir()


# ---------------------------------------------------------------------------
# Head file parsing (panel-side fallback; the head executes at boot)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlobalHeadConfig:
    """The two folder declarations parsed out of ``Global/init.py``.

    Both fields are RESOLVED absolute paths. Non-literal assignments
    (e.g. an ``os.environ.get`` expression) can't be read statically;
    the affected field falls back to the shipped default relative path.
    At boot the head executes for real, so the session record carries
    the true value - this parse is the panel's offline fallback.
    """

    plugins_dir: str
    loadout_dir: str


def read_head_config(head_path: str) -> GlobalHeadConfig:
    """Statically read ``global_plugins`` / ``global_loadout`` from the head.

    Missing or unparseable file resolves the shipped defaults against the
    head's folder.
    """
    head_dir = os.path.dirname(os.path.abspath(head_path))
    plugins_entry = GLOBAL_DEFAULT_PLUGINS_REL
    loadout_entry = GLOBAL_DEFAULT_LOADOUT_REL
    try:
        with open(head_path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=head_path)
    except (OSError, SyntaxError):
        tree = None
    if tree is not None:
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            value = node.value
            if not (
                isinstance(value, ast.Constant) and isinstance(value.value, str)
            ):
                continue
            if target.id == GLOBAL_PLUGINS_VAR_NAME:
                plugins_entry = value.value
            elif target.id == "global_loadout":
                loadout_entry = value.value
    return GlobalHeadConfig(
        plugins_dir=resolve_global_path(plugins_entry, head_dir),
        loadout_dir=resolve_global_path(loadout_entry, head_dir),
    )


# ---------------------------------------------------------------------------
# Read-ahead claims
# ---------------------------------------------------------------------------


def _user_claimed_names() -> frozenset:
    """Plugin names the active user loadout will load or suppress.

    Two sources, mirroring exactly what the user chain touches at boot:

      1. Explicit mentions - every ``nsl_pluginAddPath`` entry (enabled
         OR disabled).
      2. Folder-sweep contents - rendered loadout files sweep every
         declared folder via ``nsl_load_folder`` EXCEPT the
         ``global_plugins`` var (the Global head owns that folder's
         baseline), so names visible in the other declared folders load
         from the user chain even without an explicit mention. Walking
         them here preserves "each plugin name is added by exactly one
         file per session" when a user folder shadows a Global name
         without mentioning it (preventing a sweep-shadow double-load).
         The ``global_plugins`` var is excluded for the same
         reason render skips its sweep: claiming it would blank the
         whole Global layer (it broke case A test-driving when tried).

    Reads (never executes) the dispatcher and the active loadout file.
    First run (no dispatcher), Global-active, or panic mode all claim
    nothing - in each case the user chain adds no plugins this session.
    A declared folder that fails to list (unmounted share) contributes
    nothing, so its Global-shadowed names fall back to the Global copy.
    """
    user_loadouts = loadouts_dir()
    state = read_dispatcher(str(user_loadouts / "init.py"))
    if state.panic or not state.active or state.active == RESERVED_LOADOUT_STEM:
        return frozenset()
    active_init = user_loadouts / state.active / "init.py"
    try:
        model = read_loadout(str(active_init))
    except (OSError, SyntaxError):
        return frozenset()
    claimed = set(entry.name for entry in model.plugins)
    for decl in model.folders:
        if decl.var == GLOBAL_PLUGINS_VAR_NAME:
            continue
        try:
            names = os.listdir(decl.path)
        except OSError:
            continue
        for name in names:
            if not _is_loadable_folder_name(name):
                continue
            if os.path.isdir(os.path.join(decl.path, name)):
                claimed.add(name)
    return frozenset(claimed)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _is_loadable_folder_name(name: str) -> bool:
    if name.startswith(tuple(PLUGIN_FOLDER_IGNORE_PREFIXES)):
        return False
    if name in PLUGIN_FOLDER_IGNORE_NAMES:
        return False
    return True


def _add_plugin(folder: str, name: str, gui: bool) -> bool:
    """``pluginAddPath`` one plugin folder and record the load.

    Returns True when the path was actually added.
    """
    import nuke

    if gui and not nuke.GUI:
        return False
    path = os.path.join(folder, name)
    if not os.path.isdir(path):
        return False
    log.loading(name)
    nuke.pluginAddPath(path)
    try:
        from NukeSurvivalLoadout.boot.session_record import record_loaded

        record_loaded(name, path, gui)
    except Exception:  # noqa: BLE001 - recording must never block boot
        pass
    return True


def _scan_folder(folder: str, claims: frozenset, handled: set) -> None:
    """Load every plugin folder in ``folder`` not already decided or claimed."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return
    for name in names:
        if (folder, name) in handled:
            continue
        if name in claims:
            continue
        if not _is_loadable_folder_name(name):
            continue
        _add_plugin(folder, name, gui=False)


def _load_model(
    model: LoadoutModel, plugins_dir: str, claims: frozenset
) -> None:
    """Apply a parsed Global Loadout: explicit directives, then folder scans.

    The ``global_plugins`` var name binds to the resolved plugins dir in
    memory; other folder vars keep the literal written in the file.
    """
    folder_paths = {}
    for decl in model.folders:
        if decl.var == GLOBAL_PLUGINS_VAR_NAME:
            folder_paths[decl.var] = plugins_dir
        else:
            folder_paths[decl.var] = decl.path

    handled: set = set()
    for entry in model.plugins:
        folder = folder_paths.get(entry.folder_var)
        if folder is None:
            continue
        handled.add((folder, entry.name))
        if entry.name in claims:
            continue
        if entry.disabled:
            continue
        _add_plugin(folder, entry.name, gui=entry.gui)

    for decl in model.folders:
        _scan_folder(folder_paths[decl.var], claims, handled)


def nsl_load_global(
    plugins: str = GLOBAL_DEFAULT_PLUGINS_REL,
    loadout: str = GLOBAL_DEFAULT_LOADOUT_REL,
    base: Optional[str] = None,
) -> None:
    """Load the Global layer at Nuke boot. Called by ``Global/init.py``.

    ``plugins`` / ``loadout`` are the head's two folder declarations;
    ``base`` overrides the anchor folder for relative paths (defaults to
    the calling file's own folder).
    """
    base_dir = _resolve_base(base)
    plugins_dir = resolve_global_path(plugins, base_dir)
    loadout_dir = resolve_global_path(loadout, base_dir)

    try:
        from NukeSurvivalLoadout.boot.session_record import record_global_dir

        record_global_dir(plugins_dir)
    except Exception:  # noqa: BLE001 - recording must never block boot
        pass

    claims = _user_claimed_names()

    loadout_init = os.path.join(loadout_dir, "init.py")
    model: Optional[LoadoutModel] = None
    if os.path.isfile(loadout_init):
        try:
            model = read_loadout(loadout_init)
        except (OSError, SyntaxError):
            # Deliberately SILENT at the terminal: boot output stays
            # clean. The fallback below errs toward
            # loading everything, so a TD typo cannot dark the studio;
            # surfacing the unreadable-file condition in the PANEL
            # (Global row status) is the tracked follow-up.
            model = None

    if model is None:
        _scan_folder(plugins_dir, claims, handled=set())
        return

    _load_model(model, plugins_dir, claims)
