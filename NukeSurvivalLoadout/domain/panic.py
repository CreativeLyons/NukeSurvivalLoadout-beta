"""Panic-button state + Reset-Global-to-Default operations.

Panic lives in the dispatcher (``~/.nuke/loadouts/init.py``) as the
``PANIC_MODE`` constant. Reads / writes flow through
:mod:`NukeSurvivalLoadout.boot.dispatcher`, which writes atomically and
immediately (panic toggles take effect right away).

Public surface:
    - ``is_panic_engaged(dispatcher_path)`` - read the current panic flag.
    - ``engage_panic(dispatcher_path)`` - set panic True and write immediately.
    - ``release_panic(dispatcher_path)`` - set panic False and write immediately.
    - ``reset_global_to_default(loadout, scope, ...)`` - clear per-Plugin
      overrides for Global Plugins inside an in-memory user Loadout.

``dispatcher_path`` is optional on every panic helper - it defaults to the
canonical location under ``loadouts_dir()/init.py``. ``reset_global_to_default``
remains a pure in-memory transformation on a ``LoadoutFile`` (legacy
panel-side model); persistence is the caller's responsibility.

The ``nuke`` module is not imported anywhere here. ``KeyboardInterrupt``
and ``SystemExit`` are never caught.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional, Union

from NukeSurvivalLoadout.boot.dispatcher import (
    DispatcherState,
    read_dispatcher,
    write_dispatcher,
)
from NukeSurvivalLoadout.constants import loadouts_dir
from NukeSurvivalLoadout.data.loadout_file import LoadoutFile


PathLike = Union[str, "os.PathLike[str]"]


__all__ = [
    "is_panic_engaged",
    "engage_panic",
    "release_panic",
    "reset_global_to_default",
    "ResetScope",
]


# Accepted values for ``scope=`` on ``reset_global_to_default``.
ResetScope = str
_SCOPE_ALL = "all"
_SCOPE_PLUGIN = "plugin"
_VALID_SCOPES = frozenset({_SCOPE_ALL, _SCOPE_PLUGIN})


def _default_dispatcher_path() -> str:
    """Resolve the canonical dispatcher path under ``loadouts_dir()``."""
    return os.fspath(loadouts_dir() / "init.py")


def _resolve_path(path: Optional[PathLike]) -> str:
    return os.fspath(path) if path is not None else _default_dispatcher_path()


# ---------------------------------------------------------------------------
# Panic button -- read / engage / release
# ---------------------------------------------------------------------------


def is_panic_engaged(path: Optional[PathLike] = None) -> bool:
    """Return the persisted panic flag from the dispatcher.

    Missing dispatcher reads as ``DispatcherState()`` (panic off) without
    side effects. A dispatcher with a SyntaxError parses as defaults too
    (see :func:`NukeSurvivalLoadout.boot.dispatcher.read_dispatcher`).
    """
    return read_dispatcher(_resolve_path(path)).panic


def engage_panic(path: Optional[PathLike] = None) -> None:
    """Set panic True in the dispatcher and write immediately.

    Atomic-replace via :func:`NukeSurvivalLoadout.boot.dispatcher.write_dispatcher`.
    """
    _set_panic(True, path=path)


def release_panic(path: Optional[PathLike] = None) -> None:
    """Set panic False in the dispatcher and write immediately."""
    _set_panic(False, path=path)


def _set_panic(value: bool, path: Optional[PathLike]) -> None:
    target = _resolve_path(path)
    current = read_dispatcher(target)
    # Preserve the folder list - it's authoritative state in the dispatcher
    # now, so a panic toggle must not wipe it. (Constructing a fresh
    # DispatcherState without folders would clobber the user's Plugins
    # Folders on every panic on/off.)
    updated = DispatcherState(
        panic=value, active=current.active, folders=current.folders
    )
    write_dispatcher(target, updated)


# ---------------------------------------------------------------------------
# Reset Global Plugins to Default
# ---------------------------------------------------------------------------


def reset_global_to_default(
    loadout: LoadoutFile,
    scope: ResetScope = _SCOPE_ALL,
    *,
    plugin_name: Optional[str] = None,
    global_plugin_names: Optional[Iterable[str]] = None,
) -> LoadoutFile:
    """Clear per-Plugin overrides for Global Plugins in ``loadout``.

    Operates on the *active user Loadout's* in-memory ``LoadoutFile``.
    Removes per-Plugin entries whose name is part of the Global layer so
    resolution falls back to Global. Returns the same ``LoadoutFile``
    object, mutated in place.
    """
    if scope not in _VALID_SCOPES:
        raise ValueError(
            f"reset_global_to_default: scope must be one of "
            f"{sorted(_VALID_SCOPES)}; got {scope!r}"
        )

    if loadout.name == "Global":
        raise ValueError(
            "reset_global_to_default: refusing to operate on the Global Loadout; "
            "Global is read-only"
        )

    global_names = (
        frozenset(global_plugin_names)
        if global_plugin_names is not None
        else frozenset()
    )

    if scope == _SCOPE_ALL:
        survivors = {
            name: entry
            for name, entry in loadout.plugins.items()
            if name not in global_names
        }
        loadout.plugins.clear()
        loadout.plugins.update(survivors)
        return loadout

    # scope == 'plugin'
    if not isinstance(plugin_name, str) or not plugin_name:
        raise ValueError(
            "reset_global_to_default: scope='plugin' requires a non-empty "
            "plugin_name argument"
        )

    if plugin_name not in global_names:
        return loadout

    loadout.plugins.pop(plugin_name, None)
    return loadout
