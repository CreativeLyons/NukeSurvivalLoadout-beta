"""Plugins Folder management operations.

Folders are declared at the top of each loadout file as ``plugins_A``,
``plugins_B``... assignments and surface as
``LoadoutModel.folders: list[FolderDecl]``. This module's pure functions
operate on a ``LoadoutModel``; the ``*_and_save`` wrappers write the active
loadout file back to disk via
:func:`NukeSurvivalLoadout.boot.loadout_file.write_loadout` (atomic-replace).

When mutating ``model.folders`` we always clear ``model.user_prefix`` so
the renderer regenerates the canonical prefix (imports + folder vars +
helper) from scratch. User customisations inside that prefix are lost
on edit - that's the explicit contract of the "NSL manages the folder
var region" rule. Anything the user wants preserved across folder edits
belongs *below* the ``# === BEGIN NSL MANAGED PLUGINS ===`` marker.

Public surface:
    - ``HealthState`` - Healthy / Unreachable / PermissionDenied / Empty
    - ``FolderHealth`` - ``(state, reason)`` carrier
    - ``health_check(path)`` - one of the four mutually exclusive states
    - ``add_folder(model, path)`` -> ``AddResult``
    - ``remove_folder(model, path, *, actively_loaded_plugin_names,
      plugin_names_unique_to_folder)`` -> ``RemoveResult``
    - ``reorder(model, new_order)`` -> new ``LoadoutModel``
    - ``add_folder_and_save``, ``remove_folder_and_save``,
      ``reorder_and_save`` - thin wrappers that persist via
      :func:`NukeSurvivalLoadout.boot.loadout_file.write_loadout` (atomic-replace).

Errors:
    - ``FolderAlreadyConfigured`` - add_folder no-op signal.
    - ``FolderNotConfigured`` - remove_folder / reorder targets a folder
      that is not in the model's folder list.
    - ``FolderValidationError`` - add_folder rejected the path (missing,
      not a directory, no read permission).
    - ``ReorderError`` - reorder received an invalid permutation.

This module never imports ``nuke``. It re-raises ``KeyboardInterrupt`` and
``SystemExit`` unconditionally.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, replace
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple, Union

from NukeSurvivalLoadout.boot.loadout_file import FolderDecl, LoadoutModel, write_loadout
from NukeSurvivalLoadout.constants import (
    GLOBAL_PLUGINS_VAR_NAME,
    PLUGIN_FOLDER_IGNORE_NAMES,
    PLUGIN_FOLDER_IGNORE_PREFIXES,
    PLUGIN_GITKEEP_EXCEPTION,
    PLUGIN_NON_CONTENT_FILE_NAMES,
    PLUGIN_NON_CONTENT_FILE_PREFIX,
)


__all__ = [
    "HealthState",
    "FolderHealth",
    "FolderValidationError",
    "FolderAlreadyConfigured",
    "FolderNotConfigured",
    "ReorderError",
    "AddResult",
    "RemoveResult",
    "health_check",
    "add_folder",
    "remove_folder",
    "reorder",
    "add_folder_and_save",
    "remove_folder_and_save",
    "reorder_and_save",
]


PathLike = Union[str, "os.PathLike[str]"]


# ---------------------------------------------------------------------------
# Health states
# ---------------------------------------------------------------------------


class HealthState(enum.Enum):
    HEALTHY = "Healthy"
    UNREACHABLE = "Unreachable"
    PERMISSION_DENIED = "PermissionDenied"
    EMPTY = "Empty"


@dataclass(frozen=True)
class FolderHealth:
    state: HealthState
    reason: str = ""


def _name_is_ignored(name: str) -> bool:
    if name in PLUGIN_FOLDER_IGNORE_NAMES:
        return True
    return name.startswith(PLUGIN_FOLDER_IGNORE_PREFIXES)


def _file_counts_as_content(name: str) -> bool:
    if name == PLUGIN_GITKEEP_EXCEPTION:
        return True
    if name in PLUGIN_NON_CONTENT_FILE_NAMES:
        return False
    if name.startswith(PLUGIN_NON_CONTENT_FILE_PREFIX):
        return False
    return True


def _subfolder_has_content(folder: str) -> bool:
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    return True
                if entry.is_file(follow_symlinks=False) and _file_counts_as_content(entry.name):
                    return True
        return False
    except PermissionError:
        return False
    except OSError:
        return False


def health_check(path: PathLike) -> FolderHealth:
    target = os.fspath(path)

    if not os.path.exists(target):
        return FolderHealth(
            HealthState.UNREACHABLE,
            f"Path not found: {target}",
        )
    if not os.path.isdir(target):
        return FolderHealth(
            HealthState.UNREACHABLE,
            f"Not a directory: {target}",
        )

    try:
        scanner = os.scandir(target)
    except PermissionError:
        return FolderHealth(
            HealthState.PERMISSION_DENIED,
            f"Permission denied: cannot read folder contents at {target}",
        )
    except OSError as exc:
        return FolderHealth(
            HealthState.UNREACHABLE,
            f"Cannot read folder: {target} ({exc})",
        )

    has_plugin = False
    with scanner as it:
        for entry in it:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if _name_is_ignored(entry.name):
                continue
            if not _subfolder_has_content(entry.path):
                continue
            has_plugin = True
            break

    if has_plugin:
        return FolderHealth(HealthState.HEALTHY, "")
    return FolderHealth(
        HealthState.EMPTY,
        f"No Plugin folders detected in {target}",
    )


# ---------------------------------------------------------------------------
# add / remove / reorder
# ---------------------------------------------------------------------------


class FolderValidationError(Exception):
    """``add_folder`` rejected a path (missing, not a directory, unreadable)."""

    def __init__(self, path: PathLike, reason: str) -> None:
        self.path: str = os.fspath(path)
        self.reason: str = reason
        super().__init__(f"{self.path}: {reason}")


class FolderAlreadyConfigured(Exception):
    """``add_folder`` saw the same folder already in the model's folder list."""

    def __init__(self, path: PathLike) -> None:
        self.path: str = os.fspath(path)
        super().__init__(f"already configured: {self.path}")


class FolderNotConfigured(Exception):
    """``remove_folder`` / ``reorder`` referenced a path not in the list."""

    def __init__(self, path: PathLike) -> None:
        self.path: str = os.fspath(path)
        super().__init__(f"not in user-configured folders: {self.path}")


class ReorderError(Exception):
    """``reorder`` received an invalid permutation."""


def _normalise(path: PathLike) -> str:
    return os.path.normpath(os.fspath(path))


@dataclass(frozen=True)
class AddResult:
    model: LoadoutModel
    health: FolderHealth


@dataclass(frozen=True)
class RemoveResult:
    model: LoadoutModel
    transitioned_to_missing: Tuple[str, ...] = ()
    disappeared: Tuple[str, ...] = ()


def _next_folder_var(existing_vars: Iterable[str]) -> str:
    """Return the next unused ``plugins_X`` var name (A, B, C, ...).

    Walks the alphabet then falls back to ``plugins_AA``, ``plugins_AB``...
    Practically the user will never hit double letters; the recursion is a
    belt for the rare site that bolts on dozens of source folders.
    """
    taken = set(existing_vars)
    # Single letters A-Z.
    for code in range(ord("A"), ord("Z") + 1):
        candidate = f"plugins_{chr(code)}"
        if candidate not in taken:
            return candidate
    # Fallback - double letters AA, AB, ...
    for hi in range(ord("A"), ord("Z") + 1):
        for lo in range(ord("A"), ord("Z") + 1):
            candidate = f"plugins_{chr(hi)}{chr(lo)}"
            if candidate not in taken:
                return candidate
    raise ValueError("folder_ops: exhausted plugins_XX var name space")


def _with_folders(model: LoadoutModel, folders: List[FolderDecl]) -> LoadoutModel:
    """Return a copy of ``model`` with new ``folders`` and reset ``user_prefix``.

    Resetting ``user_prefix`` to ``""`` is intentional - see the module
    docstring. The renderer will synthesise a fresh canonical prefix from
    docstring + folder vars + helper on the next ``write_loadout``.
    """
    return replace(
        model,
        folders=list(folders),
        user_prefix="",
    )


def add_folder(model: LoadoutModel, path: PathLike) -> AddResult:
    """Validate ``path`` and prepend it to the model's folder list.

    Returns a *new* ``LoadoutModel`` with the path at index 0 (highest
    priority) and a ``FolderHealth`` derived from the same on-disk check.
    Assigns the next free ``plugins_X`` var name to the new folder.

    Raises:
        ``FolderValidationError`` - path missing, not a directory, or
        unreadable.
        ``FolderAlreadyConfigured`` - exact same normalised path already
        in the model's folder list.
    """
    norm = _normalise(path)

    if not os.path.exists(norm):
        raise FolderValidationError(norm, "path does not exist")
    if not os.path.isdir(norm):
        raise FolderValidationError(norm, "path is not a directory")
    if not os.access(norm, os.R_OK):
        raise FolderValidationError(norm, "no read permission")

    if any(_normalise(decl.path) == norm for decl in model.folders):
        raise FolderAlreadyConfigured(norm)

    new_var = _next_folder_var(decl.var for decl in model.folders)
    new_decl = FolderDecl(var=new_var, path=norm)
    new_folders: List[FolderDecl] = [new_decl, *model.folders]
    new_model = _with_folders(model, new_folders)
    return AddResult(model=new_model, health=health_check(norm))


def remove_folder(
    model: LoadoutModel,
    path: PathLike,
    *,
    actively_loaded_plugin_names: Iterable[str] = (),
    plugin_names_unique_to_folder: Iterable[str] = (),
) -> RemoveResult:
    """Remove ``path`` from the model's folder list and classify its Plugins.

    Plugin entries inside the loadout that referenced the removed
    folder's ``plugins_X`` var are NOT pruned by this function - the
    caller decides. Loadout entries for Plugins coming from a removed
    folder are preserved on disk so a re-add reactivates them cleanly.

    Args:
        model: current in-memory active loadout model.
        path: the user-added folder to remove.
        actively_loaded_plugin_names: Plugin names currently loaded in
            the live Nuke session.
        plugin_names_unique_to_folder: Plugin names that only this
            folder provides.

    Returns:
        ``RemoveResult`` carrying the new ``LoadoutModel`` and two
        disjoint tuples: ``transitioned_to_missing`` and ``disappeared``.

    Raises:
        ``FolderNotConfigured`` if ``path`` is not in ``model.folders``.
    """
    norm = _normalise(path)
    match_index: Optional[int] = None
    for idx, decl in enumerate(model.folders):
        if _normalise(decl.path) == norm:
            match_index = idx
            break
    if match_index is None:
        raise FolderNotConfigured(norm)

    new_folders = list(model.folders)
    del new_folders[match_index]

    unique = list(plugin_names_unique_to_folder)
    loaded: FrozenSet[str] = frozenset(actively_loaded_plugin_names)

    missing: List[str] = [name for name in unique if name in loaded]
    gone: List[str] = [name for name in unique if name not in loaded]

    new_model = _with_folders(model, new_folders)
    return RemoveResult(
        model=new_model,
        transitioned_to_missing=tuple(missing),
        disappeared=tuple(gone),
    )


def reorder(
    model: LoadoutModel,
    new_order: Sequence[PathLike],
) -> LoadoutModel:
    """Return a new ``LoadoutModel`` whose folders match ``new_order``.

    ``new_order`` must be a permutation of the current USER folder paths -
    same length, same set of (normalised) paths, no duplicates. The
    ``global_plugins`` decl (Global-plugin overrides written by Save) is
    not part of the reorderable list: the Global row is pinned in the UI,
    so its decl is carried through unchanged at the end.
    """
    user_decls = [
        decl for decl in model.folders if decl.var != GLOBAL_PLUGINS_VAR_NAME
    ]
    global_decls = [
        decl for decl in model.folders if decl.var == GLOBAL_PLUGINS_VAR_NAME
    ]
    current_paths = [_normalise(decl.path) for decl in user_decls]
    incoming = [_normalise(p) for p in new_order]

    if len(incoming) != len(current_paths):
        raise ReorderError(
            "new_order length does not match current folder count"
        )
    if len(set(incoming)) != len(incoming):
        raise ReorderError("new_order contains duplicates")
    if set(incoming) != set(current_paths):
        raise ReorderError(
            "new_order is not a permutation of current folder paths"
        )

    # Preserve each folder's existing ``var`` assignment when reordering -
    # the loadout's plugin call lines still reference those vars and we
    # don't want a benign reorder to invalidate every plugin call.
    by_path = {_normalise(decl.path): decl for decl in user_decls}
    reordered = [by_path[p] for p in incoming]
    return _with_folders(model, [*reordered, *global_decls])


# ---------------------------------------------------------------------------
# Persisting wrappers
# ---------------------------------------------------------------------------
#
# These wrappers do the in-memory transform and then delegate to
# ``write_loadout``, which atomically replaces the active loadout file:
# NSL writes the active loadout file immediately when the user adds,
# removes, or reorders a Plugins Folder.


def add_folder_and_save(
    model: LoadoutModel,
    path: PathLike,
    *,
    loadout_path: PathLike,
) -> AddResult:
    """Add a folder to ``model`` and persist the result to ``loadout_path``."""
    result = add_folder(model, path)
    write_loadout(os.fspath(loadout_path), result.model)
    return result


def remove_folder_and_save(
    model: LoadoutModel,
    path: PathLike,
    *,
    actively_loaded_plugin_names: Iterable[str] = (),
    plugin_names_unique_to_folder: Iterable[str] = (),
    loadout_path: PathLike,
) -> RemoveResult:
    """Remove a folder from ``model`` and persist the result to ``loadout_path``."""
    result = remove_folder(
        model,
        path,
        actively_loaded_plugin_names=actively_loaded_plugin_names,
        plugin_names_unique_to_folder=plugin_names_unique_to_folder,
    )
    write_loadout(os.fspath(loadout_path), result.model)
    return result


def reorder_and_save(
    model: LoadoutModel,
    new_order: Sequence[PathLike],
    *,
    loadout_path: PathLike,
) -> LoadoutModel:
    """Reorder ``model.folders`` and persist the result to ``loadout_path``."""
    new_model = reorder(model, new_order)
    write_loadout(os.fspath(loadout_path), new_model)
    return new_model
