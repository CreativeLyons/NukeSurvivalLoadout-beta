"""Per-Loadout undo / redo stack.

Undo / Redo is capped at 50 steps, scoped per-Loadout, session-only, and
excludes file-level Loadout ops. Bulk operations coalesce into a single step.

Pure module: no I/O, no globals, no `nuke` imports, no persistence.
Callers own the meaning of an undo entry; this module only stores
opaque payloads and enforces the discipline (cap, FIFO eviction,
redo-branch clear on push, bulk coalescing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

MAX_UNDO_STEPS: int = 50


@dataclass
class UndoStack:
    """Mutable per-Loadout undo / redo stack.

    Entries are opaque to this module; the calling layer decides their
    shape (typically a dict describing the prior and next state of the
    affected Plugins so the operation can be reversed).

    Cap is `MAX_UNDO_STEPS`. When the cap is exceeded by a new push,
    the oldest entry is discarded so the most recent steps survive.

    Standard undo discipline: a new push clears the redo branch.

    Bulk coalescing: wrap a series of related state changes in
    `with stack.bulk():` and push individually inside. The block's
    pushes are buffered and emitted on exit as a single combined
    entry under the `entries` key.
    """

    _undo: List[Any] = field(default_factory=list)
    _redo: List[Any] = field(default_factory=list)
    _bulk_depth: int = 0
    _bulk_buffer: List[Any] = field(default_factory=list)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def __len__(self) -> int:
        return len(self._undo)

    def push(self, entry: Any) -> None:
        """Record a new undo entry.

        Inside a `bulk()` context the entry is buffered for coalescing
        rather than pushed individually. Outside a bulk context the
        entry is appended; if doing so exceeds `MAX_UNDO_STEPS` the
        oldest entry is evicted. Any redo branch is discarded.
        """
        if self._bulk_depth > 0:
            self._bulk_buffer.append(entry)
            return
        self._undo.append(entry)
        if len(self._undo) > MAX_UNDO_STEPS:
            del self._undo[0]
        self._redo.clear()

    def undo(self) -> Optional[Any]:
        """Pop the most recent entry onto the redo branch and return it.

        Returns None when the undo branch is empty.
        """
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(entry)
        return entry

    def redo(self) -> Optional[Any]:
        """Pop the most recent redo entry back onto the undo branch.

        Returns None when the redo branch is empty.
        """
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(entry)
        return entry

    def clear(self) -> None:
        """Drop every undo and redo entry."""
        self._undo.clear()
        self._redo.clear()
        self._bulk_buffer.clear()
        self._bulk_depth = 0

    def snapshot(self) -> Dict[str, List[Any]]:
        """Return a shallow copy of the current undo / redo branches.

        For introspection only -- callers must not mutate the returned lists.
        """
        return {"undo": list(self._undo), "redo": list(self._redo)}

    def bulk(self) -> "_BulkContext":
        """Open a bulk-operation context that coalesces N pushes into one entry.

        Pushes made inside the `with` block are buffered. On normal
        exit the buffered entries are committed as a single combined
        entry of shape `{"bulk": True, "entries": [...]}`. If the buffer
        is empty on exit nothing is pushed. On exception (including
        `KeyboardInterrupt` / `SystemExit`) the buffer is discarded and
        the exception is re-raised; the existing undo / redo branches
        are not disturbed.
        """
        return _BulkContext(self)


class _BulkContext:
    """Context manager returned by `UndoStack.bulk()`.

    Nested `bulk()` blocks are supported -- only the outermost commit
    flushes the buffer, mirroring how a user-visible bulk operation
    composed of helper functions still counts as one undo step.
    """

    def __init__(self, stack: UndoStack) -> None:
        self._stack = stack

    def __enter__(self) -> "_BulkContext":
        self._stack._bulk_depth += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._stack._bulk_depth -= 1
        if self._stack._bulk_depth > 0:
            return False
        # Outermost exit: commit or discard the buffer.
        buffered = self._stack._bulk_buffer
        self._stack._bulk_buffer = []
        # Re-raise control-flow exceptions before mutating state further.
        if exc_type is KeyboardInterrupt or exc_type is SystemExit:
            return False
        if exc_type is not None:
            return False
        if not buffered:
            return False
        combined = {"bulk": True, "entries": buffered}
        self._stack._undo.append(combined)
        if len(self._stack._undo) > MAX_UNDO_STEPS:
            del self._stack._undo[0]
        self._stack._redo.clear()
        return False


class UndoStackRegistry:
    """Per-session collection of `UndoStack` instances keyed by Loadout stem.

    The stem is the Loadout folder name
    (e.g. `Custom`, `Comp_Daily`). Each Loadout owns its own stack;
    switching the Active Loadout simply changes which key the caller
    consults -- peer stacks are untouched.

    The registry is session-only by design: it has no save / load
    methods and is not persisted to disk.
    """

    def __init__(self) -> None:
        self._stacks: Dict[str, UndoStack] = {}

    def for_loadout(self, stem: str) -> UndoStack:
        """Return the `UndoStack` for `stem`, creating it lazily on first access."""
        if stem not in self._stacks:
            self._stacks[stem] = UndoStack()
        return self._stacks[stem]

    def has(self, stem: str) -> bool:
        return stem in self._stacks

    def drop(self, stem: str) -> None:
        """Remove the stack for `stem` if present.

        Called by file-level Loadout ops (delete) so a freshly created
        Loadout that later reuses the same stem does not inherit a
        stale undo history. File-level ops never push to a stack, but
        they may need to discard one.
        """
        self._stacks.pop(stem, None)

    def rename(self, old_stem: str, new_stem: str) -> None:
        """Move the existing stack from `old_stem` to `new_stem`.

        File-level rename does not push to the undo stack but it does
        relocate identity; this keeps the user's in-session history
        attached to the renamed Loadout.
        """
        if old_stem == new_stem:
            return
        stack = self._stacks.pop(old_stem, None)
        if stack is None:
            return
        self._stacks[new_stem] = stack

    def stems(self) -> Iterator[str]:
        return iter(self._stacks)

    def clear(self) -> None:
        """Discard every stack -- e.g. on Nuke close."""
        self._stacks.clear()


__all__ = [
    "MAX_UNDO_STEPS",
    "UndoStack",
    "UndoStackRegistry",
]
