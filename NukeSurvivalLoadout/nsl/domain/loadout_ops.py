"""Loadout management operations - create / save / save_as / rename / delete /
duplicate / switch_active / set_panic, plus a list helper for the panel.

A loadout is a folder containing an ``init.py``. The dispatcher owns the
``PANIC_MODE`` and ``ACTIVE_LOADOUT`` pointers.

On-disk layout this module manages::

    <loadouts_dir>/
      init.py              # dispatcher (panic + active pointer)
      <loadout_name>/
        init.py            # one user loadout

Every write goes through ``nsl.boot.loadout_file.write_loadout`` or
``nsl.boot.dispatcher.write_dispatcher``, both atomic. The reserved
``Global`` name is rejected earlier, at the filename-rules layer.
"""

from __future__ import annotations

import itertools
import os
import shutil
import stat
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Union

from nsl.boot.dispatcher import (
    DispatcherState,
    read_dispatcher,
    write_dispatcher,
)
from nsl.boot.loadout_file import (
    LoadoutModel,
    write_loadout,
    read_loadout,
)
from nsl.data.filename_rules import (
    next_available_name,
    validate_filename,
)

__all__ = [
    "BlockedReason",
    "Blocked",
    "OpResult",
    "DISPATCHER_FILENAME",
    "LOADOUT_INIT_FILENAME",
    "create",
    "save",
    "save_as",
    "rename",
    "delete",
    "duplicate",
    "switch_active",
    "set_panic",
    "list_loadouts",
    "loadout_path",
    "dispatcher_path",
    "read_dispatcher_state",
]


PathLike = Union[str, "os.PathLike[str]"]


DISPATCHER_FILENAME = "init.py"
LOADOUT_INIT_FILENAME = "init.py"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class BlockedReason:
    """Stable string codes returned in ``Blocked.code``."""

    INVALID_NAME = "invalid_name"
    SOURCE_NOT_FOUND = "source_not_found"
    NAME_COLLISION = "name_collision"
    # The filesystem refused the change, usually a Windows share lock or
    # a permission wall. Returned as a refusal, never as a raw traceback.
    FS_ERROR = "fs_error"


@dataclass(frozen=True)
class Blocked:
    """Structured no-op result. The op did not run; nothing on disk changed."""

    code: str
    detail: str = ""


@dataclass(frozen=True)
class OpResult:
    """Outcome of an op.

    Attributes:
        path: The loadout folder, not the ``init.py`` inside it. ``None``
            for a panic toggle or a refusal.
        model: The ``LoadoutModel`` after the op. ``None`` when the op
            removed a loadout or only touched the dispatcher.
        state: The active pointer and panic flag the next Nuke launch
            will see.
        blocked: Set when the op refused. The other fields then carry
            the unchanged state.
    """

    path: Optional[Path]
    model: Optional[LoadoutModel]
    state: DispatcherState
    blocked: Optional[Blocked] = None

    @property
    def is_blocked(self) -> bool:
        return self.blocked is not None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def dispatcher_path(loadouts_dir: PathLike) -> Path:
    """Return the dispatcher init.py path for ``loadouts_dir``."""
    return Path(loadouts_dir) / DISPATCHER_FILENAME


def loadout_path(loadouts_dir: PathLike, name: str) -> Path:
    """Return the per-loadout init.py path for ``<loadouts_dir>/<name>/``."""
    return Path(loadouts_dir) / name / LOADOUT_INIT_FILENAME


def read_dispatcher_state(loadouts_dir: PathLike) -> DispatcherState:
    """Convenience: read the dispatcher state for ``loadouts_dir``."""
    return read_dispatcher(str(dispatcher_path(loadouts_dir)))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _existing_loadout_names(loadouts_dir: Path) -> list[str]:
    """Folders directly under ``loadouts_dir`` that contain an ``init.py``.

    Folders starting with ``.`` or ``_`` are skipped, because a validated
    loadout name never starts that way. This keeps the delete quarantine
    out of the list, so it can never be picked as the new active pointer.
    """
    if not loadouts_dir.is_dir():
        return []
    names: list[str] = []
    for entry in loadouts_dir.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith((".", "_")):
            continue
        if (entry / LOADOUT_INIT_FILENAME).is_file():
            names.append(entry.name)
    return names


def _validate_or_blocked(name: str) -> Union[str, Blocked]:
    """Run name validation and return the bare stem (or a Blocked refusal).

    """
    result = validate_filename(name)
    if not result.is_valid:
        return Blocked(code=BlockedReason.INVALID_NAME, detail=result.error)
    return result.filename


def _next_free_name(
    loadouts_dir: Path, stem: str, *, exclude: Optional[str] = None
) -> str:
    """Return the lowest-numbered non-colliding loadout folder name.

    ``exclude`` drops one name from the taken set. Rename passes its own
    source name, so a case-only rename (``foo`` to ``Foo``) lands on the
    new casing instead of colliding with itself and becoming ``Foo_2``.
    """
    taken = set(_existing_loadout_names(loadouts_dir))
    if exclude is not None:
        taken.discard(exclude)
    return next_available_name(stem, taken)


def _rmtree_force(path: Path) -> None:
    """``shutil.rmtree`` that clears the read-only attribute and retries.

    On Windows a read-only file makes plain ``rmtree`` raise where POSIX
    deletes. The handler makes that one entry writable and retries it.
    Anything still failing propagates.
    """

    def _retry_writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry_writable)
    else:
        # Pre-3.12 spelling. ``onerror`` receives an excinfo tuple.
        shutil.rmtree(
            path,
            onerror=lambda func, target, excinfo: _retry_writable(
                func, target, excinfo[1]
            ),
        )


def _write_dispatcher(loadouts_dir: Path, state: DispatcherState) -> None:
    """Atomic write of the dispatcher init.py for ``loadouts_dir``."""
    write_dispatcher(str(dispatcher_path(loadouts_dir)), state)


# Process-lifetime counter so two quarantines minted in the same process
# (same PID, same clock tick) still differ.
_quarantine_seq = itertools.count()


def _quarantine_folder(folder: Path) -> Path:
    """Return a collision-proof quarantine destination for ``folder``.

    The shape is ``.<name>.nsl-trash-<pid>-<seq>``. The leading dot keeps
    it out of the loadout list, and the pid and counter keep two deletes
    apart, even across processes.
    """
    parent = folder.parent
    while True:
        candidate = parent / (
            f".{folder.name}.nsl-trash-{os.getpid()}-{next(_quarantine_seq)}"
        )
        if not candidate.exists():
            return candidate


def _state_with_active(state: DispatcherState, active: str) -> DispatcherState:
    """Return a copy of ``state`` with a new active pointer."""
    return replace(state, active=active)


def _state_with_panic(state: DispatcherState, panic: bool) -> DispatcherState:
    """Return a copy of ``state`` with a new panic flag."""
    return replace(state, panic=panic)


def _pick_fallback_active(loadouts_dir: Path, deleted_name: str) -> str:
    """Pick the next active pointer after ``deleted_name`` was removed.

    The first remaining loadout alphabetically, or ``""`` when none are
    left. An empty pointer is safe, because the dispatcher template
    skips its ``pluginAddPath`` when ``ACTIVE_LOADOUT`` is empty.
    """
    remaining = sorted(
        name for name in _existing_loadout_names(loadouts_dir) if name != deleted_name
    )
    return remaining[0] if remaining else ""


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def create(
    loadouts_dir: PathLike,
    name: str,
    state: DispatcherState,
    *,
    base: Optional[LoadoutModel] = None,
) -> OpResult:
    """Create a new loadout folder + init.py and switch the dispatcher to it.

    With ``base``, its ``folders`` and ``plugins`` are copied over. The
    user-written sections are not inherited and the new file gets a
    fresh prefix. On a name collision a numbered suffix is appended.
    """
    validated = _validate_or_blocked(name)
    if isinstance(validated, Blocked):
        return OpResult(path=None, model=None, state=state, blocked=validated)

    target_dir = Path(loadouts_dir)
    new_name = _next_free_name(target_dir, validated)
    new_folder = target_dir / new_name

    if base is not None:
        model = LoadoutModel(
            folders=list(base.folders),
            plugins=list(base.plugins),
        )
    else:
        model = LoadoutModel()

    write_loadout(str(new_folder / LOADOUT_INIT_FILENAME), model)
    new_state = _state_with_active(state, new_name)
    # The new folder is already on disk, so a failed pointer write needs
    # no rollback. The old active loadout still resolves.
    try:
        _write_dispatcher(target_dir, new_state)
    except OSError as exc:
        return OpResult(
            path=new_folder,
            model=model,
            state=state,
            blocked=Blocked(
                code=BlockedReason.FS_ERROR,
                detail=f"Created {new_name} but could not update active pointer: {exc}",
            ),
        )
    return OpResult(path=new_folder, model=model, state=new_state)


def save(
    loadouts_dir: PathLike,
    name: str,
    model: LoadoutModel,
    state: DispatcherState,
) -> OpResult:
    """Write ``model`` to ``<loadouts_dir>/<name>/init.py``.

    Does not touch the dispatcher. Use :func:`switch_active` to change
    which loadout the next Nuke launch loads.
    """
    validated = _validate_or_blocked(name)
    if isinstance(validated, Blocked):
        return OpResult(path=None, model=model, state=state, blocked=validated)

    target_dir = Path(loadouts_dir)
    folder = target_dir / validated
    write_loadout(str(folder / LOADOUT_INIT_FILENAME), model)
    return OpResult(path=folder, model=model, state=state)


def save_as(
    loadouts_dir: PathLike,
    model: LoadoutModel,
    new_name: str,
    state: DispatcherState,
) -> OpResult:
    """Write ``model`` to a new loadout folder under ``new_name`` and switch.

    The source loadout (if any) is untouched. The new loadout becomes
    the active pointer in the dispatcher.
    """
    validated = _validate_or_blocked(new_name)
    if isinstance(validated, Blocked):
        return OpResult(path=None, model=model, state=state, blocked=validated)

    target_dir = Path(loadouts_dir)
    final_name = _next_free_name(target_dir, validated)
    folder = target_dir / final_name

    saved_model = LoadoutModel(
        folders=list(model.folders),
        plugins=list(model.plugins),
    )
    write_loadout(str(folder / LOADOUT_INIT_FILENAME), saved_model)
    new_state = _state_with_active(state, final_name)
    # Already on disk, so a failed pointer write needs no rollback.
    try:
        _write_dispatcher(target_dir, new_state)
    except OSError as exc:
        return OpResult(
            path=folder,
            model=saved_model,
            state=state,
            blocked=Blocked(
                code=BlockedReason.FS_ERROR,
                detail=f"Saved {final_name} but could not update active pointer: {exc}",
            ),
        )
    return OpResult(path=folder, model=saved_model, state=new_state)


def rename(
    loadouts_dir: PathLike,
    current_name: str,
    new_name: str,
    state: DispatcherState,
) -> OpResult:
    """Rename a loadout folder in place. File contents are not modified.

    If the renamed loadout is the active one, the dispatcher's active
    pointer is updated to the new name.
    """
    validated = _validate_or_blocked(new_name)
    if isinstance(validated, Blocked):
        return OpResult(path=None, model=None, state=state, blocked=validated)

    target_dir = Path(loadouts_dir)
    src_folder = target_dir / current_name
    if not src_folder.is_dir():
        return OpResult(
            path=None,
            model=None,
            state=state,
            blocked=Blocked(
                code=BlockedReason.SOURCE_NOT_FOUND,
                detail=f"{src_folder} does not exist",
            ),
        )

    final_name = _next_free_name(target_dir, validated, exclude=current_name)
    new_folder = target_dir / final_name
    try:
        os.rename(src_folder, new_folder)
    except OSError as exc:
        # Windows refuses while a file inside is open elsewhere.
        return OpResult(
            path=None,
            model=None,
            state=state,
            blocked=Blocked(
                code=BlockedReason.FS_ERROR,
                detail=f"Could not rename {src_folder.name}: {exc}",
            ),
        )

    # The folder is already renamed. Rename it back if the pointer write
    # fails. Otherwise ACTIVE_LOADOUT names a folder that is gone, and
    # the plugins stop loading at next launch.
    new_state = state
    if state.active == current_name:
        new_state = _state_with_active(state, final_name)
        try:
            _write_dispatcher(target_dir, new_state)
        except OSError as exc:
            try:
                os.rename(new_folder, src_folder)
            except OSError:
                # Compensation itself failed - leave the folder under the
                # new name rather than risk a second partial move. The
                # detail below records both the original and rollback fault
                # so the panel surfaces a refusal, not a traceback.
                return OpResult(
                    path=None,
                    model=None,
                    state=state,
                    blocked=Blocked(
                        code=BlockedReason.FS_ERROR,
                        detail=(
                            f"Could not update active pointer after renaming "
                            f"{current_name}; rollback also failed: {exc}"
                        ),
                    ),
                )
            return OpResult(
                path=None,
                model=None,
                state=state,
                blocked=Blocked(
                    code=BlockedReason.FS_ERROR,
                    detail=(
                        f"Could not update active pointer after renaming "
                        f"{current_name}; rolled back: {exc}"
                    ),
                ),
            )

    try:
        model: Optional[LoadoutModel] = read_loadout(
            str(new_folder / LOADOUT_INIT_FILENAME)
        )
    except (FileNotFoundError, SyntaxError):
        model = None

    return OpResult(path=new_folder, model=model, state=new_state)


def delete(
    loadouts_dir: PathLike,
    name: str,
    state: DispatcherState,
) -> OpResult:
    """Remove a loadout folder. If active, fall back to next loadout alphabetical.

    Deleting the active loadout moves the pointer on. See
    :func:`_pick_fallback_active` for how the next one is chosen.
    """
    target_dir = Path(loadouts_dir)
    target_folder = target_dir / name
    if not target_folder.is_dir():
        return OpResult(
            path=None,
            model=None,
            state=state,
            blocked=Blocked(
                code=BlockedReason.SOURCE_NOT_FOUND,
                detail=f"{target_folder} does not exist",
            ),
        )

    # Move the folder to a temporary name instead of deleting it. This way
    # it can be restored if the dispatcher write fails.
    try:
        quarantine = _quarantine_folder(target_folder)
        os.rename(target_folder, quarantine)
    except OSError as exc:
        # Open handles or permission walls, routine on Windows. Nothing
        # on disk changed.
        return OpResult(
            path=None,
            model=None,
            state=state,
            blocked=Blocked(
                code=BlockedReason.FS_ERROR,
                detail=f"Could not delete {target_folder.name}: {exc}",
            ),
        )

    new_state = state
    if state.active == name:
        fallback = _pick_fallback_active(target_dir, name)
        new_state = _state_with_active(state, fallback)
        try:
            _write_dispatcher(target_dir, new_state)
        except OSError as exc:
            # Restore the quarantined folder so the active loadout still
            # resolves.
            try:
                os.rename(quarantine, target_folder)
            except OSError:
                # Restore failed, so leave the folder quarantined. It is
                # still on disk under the .nsl-trash- name.
                return OpResult(
                    path=None,
                    model=None,
                    state=state,
                    blocked=Blocked(
                        code=BlockedReason.FS_ERROR,
                        detail=(
                            f"Could not update active pointer after deleting "
                            f"{name}; folder quarantined as {quarantine.name}: "
                            f"{exc}"
                        ),
                    ),
                )
            return OpResult(
                path=None,
                model=None,
                state=state,
                blocked=Blocked(
                    code=BlockedReason.FS_ERROR,
                    detail=(
                        f"Could not update active pointer after deleting "
                        f"{name}; restored: {exc}"
                    ),
                ),
            )

    # The pointer is correct now, so drop the quarantine. A failure here
    # only leaves recoverable trash, so it must not fail the op.
    try:
        _rmtree_force(quarantine)
    except OSError:
        pass

    return OpResult(path=target_folder, model=None, state=new_state)


def duplicate(
    loadouts_dir: PathLike,
    source_name: str,
    new_name: str,
    state: DispatcherState,
) -> OpResult:
    """Copy a loadout folder under a new name. The new loadout becomes active.

    Uses ``shutil.copytree``, so any user files inside the source folder
    come along too.
    """
    validated = _validate_or_blocked(new_name)
    if isinstance(validated, Blocked):
        return OpResult(path=None, model=None, state=state, blocked=validated)

    target_dir = Path(loadouts_dir)
    src_folder = target_dir / source_name
    if not src_folder.is_dir():
        return OpResult(
            path=None,
            model=None,
            state=state,
            blocked=Blocked(
                code=BlockedReason.SOURCE_NOT_FOUND,
                detail=f"{src_folder} does not exist",
            ),
        )

    final_name = _next_free_name(target_dir, validated)
    new_folder = target_dir / final_name
    try:
        shutil.copytree(src_folder, new_folder)
    except OSError as exc:
        return OpResult(
            path=None,
            model=None,
            state=state,
            blocked=Blocked(
                code=BlockedReason.FS_ERROR,
                detail=f"Could not duplicate {src_folder.name}: {exc}",
            ),
        )

    try:
        model: Optional[LoadoutModel] = read_loadout(
            str(new_folder / LOADOUT_INIT_FILENAME)
        )
    except (FileNotFoundError, SyntaxError):
        model = None

    new_state = _state_with_active(state, final_name)
    # Already on disk, so a failed pointer write needs no rollback.
    try:
        _write_dispatcher(target_dir, new_state)
    except OSError as exc:
        return OpResult(
            path=new_folder,
            model=model,
            state=state,
            blocked=Blocked(
                code=BlockedReason.FS_ERROR,
                detail=f"Duplicated to {final_name} but could not update active pointer: {exc}",
            ),
        )

    return OpResult(path=new_folder, model=model, state=new_state)


def switch_active(
    loadouts_dir: PathLike,
    name: str,
    state: DispatcherState,
) -> OpResult:
    """Flip the dispatcher's active pointer to ``name``.

    Refuses with ``SOURCE_NOT_FOUND`` when the folder is missing, which
    would otherwise leave the user with no plugins next launch. Callers
    should enumerate with :func:`list_loadouts` first.
    """
    target_dir = Path(loadouts_dir)
    folder = target_dir / name
    if not folder.is_dir() or not (folder / LOADOUT_INIT_FILENAME).is_file():
        return OpResult(
            path=None,
            model=None,
            state=state,
            blocked=Blocked(
                code=BlockedReason.SOURCE_NOT_FOUND,
                detail=f"{folder} does not contain {LOADOUT_INIT_FILENAME}",
            ),
        )

    new_state = _state_with_active(state, name)
    _write_dispatcher(target_dir, new_state)

    try:
        model: Optional[LoadoutModel] = read_loadout(
            str(folder / LOADOUT_INIT_FILENAME)
        )
    except (FileNotFoundError, SyntaxError):
        model = None

    return OpResult(path=folder, model=model, state=new_state)


def set_panic(
    loadouts_dir: PathLike,
    panic: bool,
    state: DispatcherState,
) -> OpResult:
    """Flip the dispatcher's panic flag.

    Returns ``path=None`` and ``model=None``, because panic belongs to
    the dispatcher and not to any one loadout.
    """
    new_state = _state_with_panic(state, panic)
    _write_dispatcher(Path(loadouts_dir), new_state)
    return OpResult(path=None, model=None, state=new_state)


def list_loadouts(loadouts_dir: PathLike) -> list[str]:
    """Return the sorted list of loadout folder names under ``loadouts_dir``.

    A loadout is any direct subfolder holding an ``init.py``. Folders
    without one are ignored, because the dispatcher skips them anyway.
    """
    return sorted(_existing_loadout_names(Path(loadouts_dir)))
