"""Plugins Folder management operations.

Folders are declared at the top of each loadout file as ``plugins_A``,
``plugins_B`` and so on, and surface as ``LoadoutModel.folders``. The
``*_and_save`` wrappers write the file back through
:func:`nsl.boot.loadout_file.write_loadout`.

Clearing ``model.user_prefix`` makes the renderer rebuild the NSL
prologue. Text above the ``BEGIN NSL PROLOGUE`` marker and below the
END marker is carried through untouched, so user edits survive.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, replace
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple, Union

from nsl.boot.loadout_file import FolderDecl, LoadoutModel, write_loadout
from nsl.domain.scanner import plugin_folder_has_content
from nsl.paths import canon_for_compare
from nsl.constants import (
    GLOBAL_PLUGINS_VAR_NAME,
    PLUGIN_FOLDER_IGNORE_NAMES,
    PLUGIN_FOLDER_IGNORE_PREFIXES,
)


__all__ = [
    "canonical_folder_var",
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
            # Default ``follow_symlinks=True`` so this matches
            # ``scanner.scan_folder`` and health agrees with the grid.
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            if _name_is_ignored(entry.name):
                continue
            if not plugin_folder_has_content(entry.path):
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
    """The STORED form of a folder path - separators/dots unified, case kept.

    Use :func:`canon_for_compare` for comparisons instead. It also folds
    case, which this does not.
    """
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


#: Total positional ``plugins_X`` slots: A-Z (26) then AA-ZZ (676).
_MAX_FOLDER_VAR_INDEX = 26 + 26 * 26 - 1  # 701 (inclusive)


def canonical_folder_var(index: int) -> str:
    """Positional ``plugins_X`` var name for the folder at ``index``.

    Single letters A-Z cover ``0..25`` and double letters AA-ZZ cover
    ``26..701``. The dispatcher and the per-loadout ``init.py`` both
    derive vars here so they agree. A plain ``chr(ord('A') + index)``
    gives ``plugins_[`` at 26 and breaks the rendered file.

    Raises:
        ``ValueError`` when ``index`` is out of range.
    """
    if index < 0 or index > _MAX_FOLDER_VAR_INDEX:
        raise ValueError(
            f"folder_ops: folder index {index} out of range "
            f"(0..{_MAX_FOLDER_VAR_INDEX}; A-Z then AA-ZZ)"
        )
    if index < 26:
        return f"plugins_{chr(ord('A') + index)}"
    hi, lo = divmod(index - 26, 26)
    return f"plugins_{chr(ord('A') + hi)}{chr(ord('A') + lo)}"


def _next_folder_var(existing_vars: Iterable[str]) -> str:
    """Return the next unused ``plugins_X`` var name (A, B, C, ...).

    """
    taken = set(existing_vars)
    for index in range(_MAX_FOLDER_VAR_INDEX + 1):
        candidate = canonical_folder_var(index)
        if candidate not in taken:
            return candidate
    raise ValueError("folder_ops: exhausted plugins_XX var name space")


def _with_folders(model: LoadoutModel, folders: List[FolderDecl]) -> LoadoutModel:
    """Return a copy of ``model`` with new ``folders`` and reset ``user_prefix``.

    Resetting ``user_prefix`` is intentional. See the module docstring.
    """
    return replace(
        model,
        folders=list(folders),
        user_prefix="",
    )


def add_folder(model: LoadoutModel, path: PathLike) -> AddResult:
    """Validate ``path`` and prepend it to the model's folder list.

    The path goes to index 0, which is the highest priority. Also
    assigns the next free ``plugins_X`` var.

    Raises:
        ``FolderValidationError`` when the path is missing, not a
        directory, or unreadable.
        ``FolderAlreadyConfigured`` when it is already in the list.
    """
    norm = _normalise(path)

    if not os.path.exists(norm):
        raise FolderValidationError(norm, "path does not exist")
    if not os.path.isdir(norm):
        raise FolderValidationError(norm, "path is not a directory")
    if not os.access(norm, os.R_OK):
        raise FolderValidationError(norm, "no read permission")

    target_key = canon_for_compare(norm)
    if any(canon_for_compare(decl.path) == target_key for decl in model.folders):
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

    Plugin entries that referenced the removed folder are not pruned.
    They stay on disk so a re-add brings those Plugins back.

    Args:
        model: current in-memory active loadout model.
        path: the user-added folder to remove.
        actively_loaded_plugin_names: Plugin names loaded in the live
            Nuke session.
        plugin_names_unique_to_folder: Plugin names only this folder
            provides.

    Returns:
        ``RemoveResult`` with the new model and two disjoint tuples,
        ``transitioned_to_missing`` and ``disappeared``.

    Raises:
        ``FolderNotConfigured`` if ``path`` is not in ``model.folders``.
    """
    norm = _normalise(path)
    target_key = canon_for_compare(norm)
    match_index: Optional[int] = None
    for idx, decl in enumerate(model.folders):
        if canon_for_compare(decl.path) == target_key:
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

    ``new_order`` must be a permutation of the current user folder
    paths. The ``global_plugins`` decl is not reorderable, because the
    Global row is pinned in the UI. It is carried through at the end.
    """
    user_decls = [
        decl for decl in model.folders if decl.var != GLOBAL_PLUGINS_VAR_NAME
    ]
    global_decls = [
        decl for decl in model.folders if decl.var == GLOBAL_PLUGINS_VAR_NAME
    ]
    current_paths = [canon_for_compare(decl.path) for decl in user_decls]
    incoming = [canon_for_compare(p) for p in new_order]

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

    # Keep each folder's existing ``var``. The plugin call lines still
    # reference them, so reassigning would break every call.
    by_path = {canon_for_compare(decl.path): decl for decl in user_decls}
    reordered = [by_path[p] for p in incoming]
    return _with_folders(model, [*reordered, *global_decls])


# ---------------------------------------------------------------------------
# Persisting wrappers
# ---------------------------------------------------------------------------


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
