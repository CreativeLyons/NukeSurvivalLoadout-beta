"""NSL loadouts dispatcher - read/write module for ``~/.nuke/loadouts/init.py``.

``render`` is pure, so writing back what was read changes nothing when
the file already matches the template. An empty ``ACTIVE_LOADOUT`` means
no loadout is selected. The template then skips its ``pluginAddPath``,
so a fresh install boots with no loadout folder on disk.

The rendered dispatcher does no error handling on purpose. A broken
loadout surfaces as Nuke's own traceback, which names the file and
line. Recovery is edit and relaunch.
"""

from __future__ import annotations

import ast
import os
import shutil
from dataclasses import dataclass, field

from nsl.atomic_io import write_atomic
from nsl.boot.loadout_file import FolderDecl, _try_folder_decl

__all__ = ["DispatcherState", "read_dispatcher", "write_dispatcher", "render"]

#: Suffix for the copy taken before a malformed dispatcher is
#: overwritten. A hand-edit typo never loses the original bytes.
BACKUP_SUFFIX = ".bak"

# Not folder declarations, so the folder parser skips them.
_RESERVED_CONSTANTS = frozenset({"PANIC_MODE", "ACTIVE_LOADOUT"})


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class DispatcherState:
    """Mirror of the dispatcher init.py's module-level state.

    ``folders`` is the authority for the Plugins Folder list. Each
    loadout file keeps a copy so it is self-contained at Nuke boot, but
    the panel reads the dispatcher, so folders survive any switch.
    """

    panic: bool = False
    active: str = ""
    folders: list[FolderDecl] = field(default_factory=list)
    #: ``True`` when the file was on disk but would not parse. A missing
    #: file is a first-run default and leaves this ``False``. A broken
    #: dispatcher is never treated as an empty one. ``render`` ignores it.
    malformed: bool = False


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(state: DispatcherState) -> str:
    """Return the canonical dispatcher text for ``state``.

    Pure, so the same input always gives the same bytes. Paths and names
    are quoted with ``repr`` so Windows backslashes and embedded quotes
    stay valid Python literals.
    """
    panic_literal = "True" if state.panic else "False"
    active_literal = repr(state.active)

    if state.folders:
        folder_lines = "".join(
            f"{f.var} = {f.path!r}\n" for f in state.folders
        )
        folder_block = (
            "\n"
            "# Plugin source folders.\n"
            f"{folder_lines}"
        )
    else:
        folder_block = ""

    return (
        '"""NSL loadouts dispatcher.\n'
        "Edit PANIC_MODE or ACTIVE_LOADOUT below to control what loads next launch.\n"
        '"""\n'
        "\n"
        "import os\n"
        "import nuke\n"
        "\n"
        "\n"
        f"PANIC_MODE = {panic_literal}\n"
        f"ACTIVE_LOADOUT = {active_literal}\n"
        f"{folder_block}"
        "\n"
        "\n"
        "if not PANIC_MODE and ACTIVE_LOADOUT:\n"
        "    loadouts_dir = os.path.dirname(os.path.abspath(__file__))\n"
        "    active_dir = os.path.join(loadouts_dir, ACTIVE_LOADOUT)\n"
        '    active_init = os.path.join(active_dir, "init.py")\n'
        "\n"
        "    if os.path.exists(active_init):\n"
        "        nuke.pluginAddPath(active_dir)\n"
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_dispatcher(path: str) -> DispatcherState:
    """Parse ``path`` and return its ``DispatcherState``.

    AST-walks the file for top-level ``PANIC_MODE`` and ``ACTIVE_LOADOUT``
    assignments, in any order. Other top-level statements are ignored,
    and an unparseable constant falls back to the dataclass default.

    A missing file returns ``DispatcherState()`` with no side effects. A
    file that will not parse returns ``malformed=True``, see the field.
    """
    try:
        # Pinned to UTF-8 to match the write side, not the host locale.
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except FileNotFoundError:
        return DispatcherState()

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return DispatcherState(malformed=True)

    state = DispatcherState()

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue

        if target.id == "PANIC_MODE":
            panic = _extract_bool(node.value)
            if panic is not None:
                state.panic = panic
        elif target.id == "ACTIVE_LOADOUT":
            active = _extract_str(node.value)
            if active is not None:
                state.active = active
        elif target.id not in _RESERVED_CONSTANTS:
            # Any other top-level string assignment is a folder decl.
            # Reuse the loadout parser so both files agree on the shape.
            decl = _try_folder_decl(node)
            if decl is not None:
                state.folders.append(decl)

    return state


def _extract_bool(node: ast.expr) -> bool | None:
    """Return the bool literal of ``node`` or ``None`` if it isn't one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _extract_str(node: ast.expr) -> str | None:
    """Return the str literal of ``node`` or ``None`` if it isn't one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _backup_if_malformed(path: str) -> str | None:
    """Copy ``path`` to ``path + BACKUP_SUFFIX`` when it is on-disk but unparseable.

    Returns the backup path, or ``None`` when no copy was taken. A
    missing file, a parseable file, or any OSError is a no-op, because a
    best-effort backup must not block the write. An existing ``.bak`` is
    overwritten, so the newest damaged version is the one kept.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        # Missing or unreadable, so nothing recoverable to save.
        return None

    try:
        ast.parse(source, filename=path)
    except SyntaxError:
        pass  # malformed - fall through to the backup copy
    else:
        return None  # parseable - canonical overwrite loses nothing

    backup = path + BACKUP_SUFFIX
    try:
        shutil.copy2(path, backup)
    except OSError:
        return None
    return backup


def write_dispatcher(path: str, state: DispatcherState) -> None:
    """Atomically write the canonical dispatcher for ``state`` to ``path``.

    Idempotent, so writing the same state over a matching file changes
    no bytes. A malformed file on disk is backed up first, see
    :func:`_backup_if_malformed`.
    """
    _backup_if_malformed(os.fspath(path))
    write_atomic(path, render(state))
