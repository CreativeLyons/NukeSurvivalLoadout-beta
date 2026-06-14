"""Loadout management operations - create / save / save_as / rename / delete /
duplicate / switch_active / set_panic, plus a list helper for the panel.

A loadout is a folder containing an ``init.py``; the dispatcher owns the
``PANIC_MODE`` + ``ACTIVE_LOADOUT`` pointers. No JSON is used anywhere.

On-disk layout this module manages::

    <loadouts_dir>/
      init.py              # dispatcher (panic + active pointer)
      <loadout_name>/
        init.py            # one user loadout

Public surface:
    - ``OpResult`` - outcome of every op. Carries the on-disk folder path,
      the resulting in-memory ``LoadoutModel`` (or ``None``), and the
      resulting ``DispatcherState`` (or a ``Blocked`` reason).
    - ``Blocked`` / ``BlockedReason`` - structured no-op result for policy
      refusals (invalid name, source missing).
    - ``create(...)`` - mkdir ``<name>/`` + write empty (or seeded) loadout
      ``init.py`` + flip dispatcher.active to the new name.
    - ``save(...)`` - write a ``LoadoutModel`` to ``<name>/init.py``.
    - ``save_as(...)`` - write under a new name + flip dispatcher.active.
    - ``rename(...)`` - ``os.rename`` the folder + update dispatcher.active
      if the renamed loadout was active.
    - ``delete(...)`` - ``shutil.rmtree`` the folder + fall back to the
      first remaining loadout (alphabetical) or to no active pointer
      (``""`` - Custom-as-first-run takes over in the panel) if none.
    - ``duplicate(...)`` - ``shutil.copytree`` the folder under a new name
      + flip dispatcher.active.
    - ``switch_active(...)`` - write dispatcher with a new active pointer.
    - ``set_panic(...)`` - write dispatcher with a new panic flag.
    - ``list_loadouts(...)`` - sorted names of loadout folders containing
      an ``init.py``.

This module never imports ``nuke``. All writes flow through
``NukeSurvivalLoadout.boot.loadout_file.write_loadout`` and
``NukeSurvivalLoadout.boot.dispatcher.write_dispatcher`` (both atomic via
``NukeSurvivalLoadout.atomic_io``). The reserved ``Global`` name is rejected at the
filename-rules layer; the dispatcher pointer always identifies a real
loadout folder under ``<loadouts_dir>/``.
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

from NukeSurvivalLoadout.boot.dispatcher import (
    DispatcherState,
    read_dispatcher,
    write_dispatcher,
)
from NukeSurvivalLoadout.boot.loadout_file import (
    LoadoutModel,
    write_loadout,
    read_loadout,
)
from NukeSurvivalLoadout.data.filename_rules import (
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
    # Filesystem refused the mutation (Windows share locks on open
    # folders, permission walls). The op did not complete; surfaced as a
    # refusal instead of a raw traceback through the Qt signal layer.
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
        path: On-disk loadout *folder* path (not the init.py inside it).
            ``None`` when the op did not target a single loadout folder
            (panic toggle) or refused (Blocked).
        model: The in-memory ``LoadoutModel`` after the op. ``None`` when
            the op removed a loadout or only flipped the dispatcher.
        state: ``DispatcherState`` after the op - reflects the active
            pointer and panic flag the next Nuke launch will see.
        blocked: ``Blocked`` instance when the op refused. When set, the
            other fields carry the unchanged state.
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

    Dot- and underscore-prefixed folders are skipped: validated loadout
    names can never start with ``.`` or ``_`` (see ``filename_rules``), so
    such folders are never real loadouts. This keeps the in-flight delete
    quarantine (``.<name>.nsl-trash-...``, which still holds an ``init.py``)
    out of both the panel enumeration and the delete fallback picker, so a
    quarantined folder can never be chosen as the new active pointer.
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

    ``validate_filename`` returns bare stems under the runnable-python
    architecture; loadouts are folders.
    """
    result = validate_filename(name)
    if not result.is_valid:
        return Blocked(code=BlockedReason.INVALID_NAME, detail=result.error)
    return result.filename


def _next_free_name(
    loadouts_dir: Path, stem: str, *, exclude: Optional[str] = None
) -> str:
    """Return the lowest-numbered non-colliding loadout folder name.

    Collision matching is casefolded (see ``next_available_name``).
    ``exclude`` removes one exact name from the taken set - rename
    passes its own source name so a case-only rename (``foo`` ->
    ``Foo``) lands on the new casing instead of being suffixed to
    ``Foo_2`` by colliding with itself.
    """
    taken = set(_existing_loadout_names(loadouts_dir))
    if exclude is not None:
        taken.discard(exclude)
    return next_available_name(stem, taken)


def _rmtree_force(path: Path) -> None:
    """``shutil.rmtree`` that clears the read-only attribute and retries.

    On Windows, files copied from read-only media (or stamped read-only
    by tooling) make plain ``rmtree`` raise where POSIX deletes. The
    handler chmods the failing entry writable and retries that one
    operation; anything still failing propagates to the caller.
    """

    def _retry_writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry_writable)
    else:
        # Pre-3.12 spelling: onerror receives an excinfo TUPLE (and is
        # deprecated from 3.12, hence the branch above).
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

    Used by :func:`delete` to *move* the target out of the way before the
    dispatcher pointer is rewritten, so the original is recoverable if that
    write fails. The name is dot-prefixed (kept out of the panel's loadout
    enumeration, which only lists folders holding an ``init.py`` under a
    validated name) and carries the PID plus a per-process counter:

        ``.<name>.nsl-trash-<pid>-<seq>``

    A validated loadout name can never contain ``.nsl-trash-``, and the
    ``<pid>-<seq>`` suffix is unique within and across concurrent processes,
    so the quarantine destination cannot collide with a real loadout or with
    another in-flight delete. The loop bumps the counter on the vanishingly
    unlikely event a stale quarantine with the same name already exists.
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

    First remaining loadout alphabetically, or ``""`` when none remain.
    The empty pointer cascades through ``_active_strip_name`` to
    Custom-as-first-run on the panel side; the dispatcher template's
    ``if not PANIC_MODE and ACTIVE_LOADOUT:`` guard skips the
    pluginAddPath cleanly so an empty pointer is safe at runtime.
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

    When ``base`` is provided, its ``folders`` + ``plugins`` are copied
    into the new loadout (the docstring + user-freeform sections are not
    inherited - the new file gets a fresh canonical prefix). When
    ``base`` is ``None``, the new loadout is empty (canonical prefix +
    empty managed section).

    On name collision the lowest-numbered suffix is appended via
    ``next_available_name``.
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
    # The new folder already exists, so a failed pointer write needs no
    # compensation - the old active loadout still resolves. Surface a
    # structured refusal rather than letting OSError raise through the Qt
    # signal layer as a traceback.
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

    Does not flip the dispatcher - Save means "commit the active
    loadout's current state to disk." Use :func:`switch_active` to change
    which loadout the next Nuke launch loads.

    The loadout folder is created lazily by ``write_loadout`` via
    ``atomic_io.ensure_parent_dir``.
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
    # New folder already on disk; a failed pointer write leaves the old
    # active loadout resolvable, so no compensation - just a structured
    # refusal instead of an unguarded raise.
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
        # Windows refuses while any file inside is open elsewhere;
        # surface as a refusal, not a traceback. Nothing changed on disk.
        return OpResult(
            path=None,
            model=None,
            state=state,
            blocked=Blocked(
                code=BlockedReason.FS_ERROR,
                detail=f"Could not rename {src_folder.name}: {exc}",
            ),
        )

    # Transactional: the folder rename is already committed above. If the
    # renamed loadout was active we must advance the dispatcher pointer too.
    # Should that write fail, the folder change would otherwise persist with
    # ACTIVE_LOADOUT still naming the (now gone) old folder - the active
    # plugins would silently stop loading at next launch. So on a failed
    # dispatcher write we rename the folder BACK and report FS_ERROR.
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

    When the deleted loadout is the active one, the dispatcher's active
    pointer falls back to the first remaining loadout (alphabetical)
    or to ``""`` when none remain. The dispatcher template skips its
    pluginAddPath when ``ACTIVE_LOADOUT`` is empty, so writing the
    empty fallback is always safe.
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

    # Transactional delete. A plain rmtree-then-dispatcher-write would, on a
    # failed dispatcher write, leave ACTIVE_LOADOUT pointing at a folder that
    # no longer exists - the active plugins silently stop loading at next
    # launch with nothing to recover. Instead we MOVE the folder to a
    # recoverable quarantine name first, write the fallback pointer, and only
    # then remove the quarantine. If the dispatcher write fails we move the
    # folder BACK and report FS_ERROR; nothing is lost.
    try:
        quarantine = _quarantine_folder(target_folder)
        os.rename(target_folder, quarantine)
    except OSError as exc:
        # Open handles / permission walls (routine on Windows). The folder is
        # untouched, the pointer is untouched, the survivors stay listable.
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
            # Pointer write failed - restore the quarantined folder so the
            # active loadout still resolves, and surface a refusal.
            try:
                os.rename(quarantine, target_folder)
            except OSError:
                # Restore failed; leave the folder quarantined rather than
                # risk a second partial move. It is recoverable on disk under
                # the .nsl-trash- name.
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

    # Pointer is consistent (or never needed updating). Now drop the
    # quarantined folder for real. A failure here leaves recoverable trash but
    # the dispatcher is already correct, so the op still succeeded logically;
    # best-effort cleanup, do not fail the op over leftover trash.
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

    Uses ``shutil.copytree`` so any user-authored files inside the source
    folder (e.g., notes, sub-helpers) come along.
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
    # Copy already on disk; a failed pointer write leaves the old active
    # loadout resolvable, so no compensation - structured refusal instead of
    # an unguarded raise.
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

    Refuses (``SOURCE_NOT_FOUND``) when the target loadout folder is
    missing - switching to a non-existent loadout would silently leave
    the user with no plugins next launch. The caller (panel) is
    expected to enumerate via :func:`list_loadouts` first.
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

    Returns ``path=None`` and ``model=None`` - panic is a
    dispatcher-level concern, not tied to any one loadout.
    """
    new_state = _state_with_panic(state, panic)
    _write_dispatcher(Path(loadouts_dir), new_state)
    return OpResult(path=None, model=None, state=new_state)


def list_loadouts(loadouts_dir: PathLike) -> list[str]:
    """Return the sorted list of loadout folder names under ``loadouts_dir``.

    A loadout is any direct subfolder that contains an ``init.py``.
    Folders without an init.py are ignored - the dispatcher would skip
    them anyway, and surfacing them in the panel would mislead the user.
    """
    return sorted(_existing_loadout_names(Path(loadouts_dir)))
