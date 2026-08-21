"""Global chain-head loader - boot-side loading for ``<install>/Global/``.

``Global/init.py`` declares two folder paths and calls
:func:`nsl_load_global`. The Global Loadout file is parsed, never
executed, and nothing on disk is touched.

Plugin names the active user loadout will touch are skipped here, so
each name is added by exactly one file per session. See
:func:`_user_claimed_names`. With no Global Loadout file, every plugin
folder in the plugins dir loads.

``import nuke`` is lazy inside the load path, so the parsing helpers
stay importable outside Nuke.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from typing import Optional

from nsl import log
from nsl.boot.loadout_file import LoadoutModel, read_loadout
from nsl.boot.dispatcher import read_dispatcher
from nsl.constants import (
    GLOBAL_DEFAULT_LOADOUT_REL,
    GLOBAL_DEFAULT_PLUGINS_REL,
    GLOBAL_FOLDER_NAME,
    GLOBAL_PLUGINS_VAR_NAME,
    PLUGIN_FOLDER_IGNORE_NAMES,
    PLUGIN_FOLDER_IGNORE_PREFIXES,
    RESERVED_LOADOUT_STEM,
    global_dir,
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

    Rules:
      * ``./foo`` and any non-absolute path resolve against ``base_dir``.
      * ``~/foo`` is home-expanded.
      * absolute paths pass through.
    """
    expanded = os.path.expanduser(entry)
    if not os.path.isabs(expanded):
        expanded = os.path.join(base_dir, expanded)
    return os.path.normpath(expanded)


def _default_head_dir() -> str:
    """``<install>/Global/`` derived from the package location.

    Fallback base when the caller's file location can't be determined.
    """
    return str(global_dir())


def _resolve_base(base: Optional[str]) -> str:
    """Return the folder the head's relative paths anchor to.

    ``base`` may be the head file path or its folder. A file resolves to
    its folder. The choice does not depend on the path existing, so
    ``base=__file__`` works however deep the call stack is.

    With no ``base``, the caller's frame is used, then the shipped
    ``<install>/Global/``. Prefer passing ``base``.
    """
    if base:
        base = os.path.abspath(os.path.expanduser(str(base)))
        if os.path.isdir(base):
            return base
        if os.path.isfile(base) or os.path.splitext(base)[1]:
            return os.path.dirname(base)
        return base
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
# Head file parsing. The panel's fallback, the head runs for real at boot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlobalHeadConfig:
    """The two folder declarations parsed out of ``Global/init.py``.

    Both fields are resolved absolute paths. A non-literal assignment
    cannot be read statically, so that field falls back to the shipped
    default. At boot the head really runs, so the session record holds
    the true value and this parse is only the panel's fallback.
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

    Two sources:
      1. Every ``nsl_pluginAddPath`` entry, enabled or disabled.
      2. Folder contents of every declared folder except
         ``global_plugins``. Claiming that one would blank the whole
         Global layer.

    This keeps each plugin name added by exactly one file per session.

    Reads, never executes. First run, Global active, and panic all claim
    nothing. A folder that will not list contributes nothing.
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
        from nsl.boot.session_record import record_loaded

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
    """Apply a parsed Global Loadout, last folder first.

    The ``global_plugins`` var name binds to the resolved plugins dir in
    memory; other folder vars keep the literal written in the file.
    """
    folder_paths = {}
    for decl in model.folders:
        if decl.var == GLOBAL_PLUGINS_VAR_NAME:
            folder_paths[decl.var] = plugins_dir
        else:
            folder_paths[decl.var] = decl.path

    entries_by_var: dict = {}
    handled: set = set()
    for entry in model.plugins:
        folder = folder_paths.get(entry.folder_var)
        if folder is None:
            continue
        handled.add((folder, entry.name))
        entries_by_var.setdefault(entry.folder_var, []).append(entry)

    for decl in reversed(model.folders):
        folder = folder_paths[decl.var]
        for entry in entries_by_var.get(decl.var, []):
            if entry.name in claims:
                continue
            if entry.disabled:
                continue
            _add_plugin(folder, entry.name, gui=entry.gui)
        _scan_folder(folder, claims, handled)


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
        from nsl.boot.session_record import (
            record_global_dir,
            record_global_loadout_dir,
        )

        record_global_dir(plugins_dir)
        record_global_loadout_dir(loadout_dir)
    except Exception:  # noqa: BLE001 - recording must never block boot
        pass

    claims = _user_claimed_names()

    loadout_init = os.path.join(loadout_dir, "init.py")
    model: Optional[LoadoutModel] = None
    if os.path.isfile(loadout_init):
        try:
            model = read_loadout(loadout_init)
        except (OSError, SyntaxError):
            # Silent on purpose. The fallback below loads everything, so
            # a typo in the Global Loadout cannot leave artists with no
            # plugins.
            model = None

    if model is None:
        _scan_folder(plugins_dir, claims, handled=set())
        return

    _load_model(model, plugins_dir, claims)
