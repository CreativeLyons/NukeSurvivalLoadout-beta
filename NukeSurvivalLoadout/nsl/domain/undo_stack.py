"""Per-Loadout undo / redo stack.

Session only, never persisted. Entries are opaque payloads and the
caller decides what they mean. File-level Loadout ops do not push here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

MAX_UNDO_STEPS: int = 50


@dataclass
class UndoStack:
    """Mutable per-Loadout undo / redo stack."""

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

        Inside a `bulk()` block the entry is buffered instead. Otherwise
        the oldest entry is dropped past `MAX_UNDO_STEPS`, and the redo
        branch is cleared.
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

        For introspection only. Do not mutate the returned lists.
        """
        return {"undo": list(self._undo), "redo": list(self._redo)}

    def bulk(self) -> "_BulkContext":
        """Open a bulk-operation context that coalesces N pushes into one entry.

        Pushes inside the block are buffered and committed on exit as one
        entry of shape `{"bulk": True, "entries": [...]}`. An empty buffer
        pushes nothing. On any exception the buffer is dropped and the
        existing branches are left alone.
        """
        return _BulkContext(self)


class _BulkContext:
    """Context manager returned by `UndoStack.bulk()`.

    Nesting works. Only the outermost block flushes the buffer, so one
    user action built from helpers still counts as one undo step.
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
        buffered = self._stack._bulk_buffer
        self._stack._bulk_buffer = []
        # Let control-flow exceptions out before touching more state.
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

    The stem is the Loadout folder name. Switching the Active Loadout
    only changes which key the caller reads, so peer stacks survive.
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

        Called on delete, so a new Loadout that reuses the stem does not
        inherit a stale history.
        """
        self._stacks.pop(stem, None)

    def rename(self, old_stem: str, new_stem: str) -> None:
        """Move the existing stack from `old_stem` to `new_stem`.

        Keeps the in-session history attached to the renamed Loadout.
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
        """Discard every stack, for example on Nuke close."""
        self._stacks.clear()


__all__ = [
    "MAX_UNDO_STEPS",
    "UndoStack",
    "UndoStackRegistry",
]
