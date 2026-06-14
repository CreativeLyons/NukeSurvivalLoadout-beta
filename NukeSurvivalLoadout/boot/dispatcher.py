"""NSL loadouts dispatcher - read/write module for ``~/.nuke/loadouts/init.py``.

Public API:
    DispatcherState(panic, active) - module constants as a dataclass
    read_dispatcher(path) -> DispatcherState
    write_dispatcher(path, state) -> None - atomic write of canonical text
    render(state) -> str - pure: state -> canonical text

The rendered dispatcher is byte-identical for the same input ``DispatcherState``
so ``write_dispatcher(path, read_dispatcher(path))`` is a no-op when the file
already matches the canonical template. Missing files read as defaults
(``panic=False``, ``active=""``) without side effects. Empty ``active``
is the "no loadout selected yet" signal - Custom-as-first-run takes
over in the panel layer; the runtime dispatcher template skips its
pluginAddPath when ``ACTIVE_LOADOUT`` is empty so a fresh install can
boot without any loadout folder existing on disk.

The rendered dispatcher does no error-handling of its own: a broken
active loadout surfaces as Nuke's own traceback (file + line), which is
more precise than anything we can synthesize. There is deliberately no
syntax pre-validation and no crash banner - recovery is edit-and-relaunch.
"""

from __future__ import annotations

import ast
import os
import shutil
from dataclasses import dataclass, field

from NukeSurvivalLoadout.atomic_io import write_atomic
from NukeSurvivalLoadout.boot.loadout_file import FolderDecl, _try_folder_decl

__all__ = ["DispatcherState", "read_dispatcher", "write_dispatcher", "render"]

#: Suffix for the side-copy taken before a malformed dispatcher is
#: overwritten / reset. A hand-edit typo leaves the file syntactically
#: broken but populated; ``write_dispatcher`` preserves those original
#: bytes here so the damaged-but-recoverable content is never lost.
BACKUP_SUFFIX = ".bak"

# Dispatcher constant names that are NOT folder declarations - excluded when
# parsing the top-level ``plugins_X = "..."`` folder block.
_RESERVED_CONSTANTS = frozenset({"PANIC_MODE", "ACTIVE_LOADOUT"})


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class DispatcherState:
    """Mirror of the dispatcher init.py's module-level state.

    ``folders`` is the **authority** for the user's Plugins Folder list
    (the "where are the plugins" fact), alongside ``panic`` / ``active``.
    Each loadout file keeps a synced copy of these decls so it stays
    self-contained at Nuke boot, but the dispatcher is the source of
    truth the panel reads on open - so folders survive regardless of
    which loadout is active, including the unsaveable Custom slot.
    """

    panic: bool = False
    active: str = ""
    folders: list[FolderDecl] = field(default_factory=list)
    #: ``True`` only when ``read_dispatcher`` found the file on disk but
    #: could NOT parse it (a populated-but-broken dispatcher, e.g. a
    #: hand-edit typo). A MISSING file is a genuine first-run default and
    #: leaves this ``False`` - so a malformed read is never silently
    #: equated with an empty one. It participates in equality so callers
    #: and tests can distinguish ``DispatcherState(malformed=True)`` from
    #: the plain default, but ``render`` ignores it (rendered bytes depend
    #: only on panic / active / folders).
    malformed: bool = False


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(state: DispatcherState) -> str:
    """Return the canonical dispatcher text for ``state``.

    Pure: same input always produces the same bytes. Quoting of
    ``state.active`` and every folder path uses ``repr`` so names and
    paths containing quotes, backslashes (Windows paths like
    ``C:\\Users\\...`` would otherwise be escape-sequence soup), or other
    surprises serialize as valid Python literals.
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

    AST-walks the file for top-level assignments of ``PANIC_MODE`` and
    ``ACTIVE_LOADOUT``. Tolerates either ordering and ignores any other
    top-level statements. Missing or unparseable individual constants
    fall back to the dataclass defaults.

    Two failure modes are deliberately kept DISTINCT:

    * **Missing file** - a genuine first-run default. Returns
      ``DispatcherState()`` (``malformed=False``) with no side effects
      (no implicit write).
    * **Malformed file** - present on disk but a whole-file
      ``SyntaxError`` (e.g. a hand-edit typo: the dispatcher is
      documented as user-editable). Returns
      ``DispatcherState(malformed=True)``. The panic flag, active
      pointer, and folder authority are NOT recoverable from a file that
      will not parse, so the state carries defaults for those - but the
      ``malformed`` flag lets the caller refuse to treat the file as
      empty and back it up before any overwrite (see
      :func:`write_dispatcher`).
    """
    try:
        # Pinned to UTF-8 to match the write side (atomic_io.write_atomic)
        # rather than the host locale - LANG=C sessions must read the
        # dispatcher identically to UTF-8 desktops.
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except FileNotFoundError:
        return DispatcherState()

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        # A corrupt dispatcher is NOT a default - the file is present and
        # populated, just unparseable. Flag it ``malformed`` so the
        # bootstrap enters degraded mode and ``write_dispatcher`` backs up
        # the original bytes before any rewrite, rather than silently
        # equating it with a fresh (missing) install and wiping config.
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
            # Any other top-level ``<name> = "<str>"`` is a folder decl.
            # (The ``loadouts_dir`` / ``active_dir`` assigns live inside the
            # ``if`` block, so they're never top-level here.) Reuse the
            # loadout parser so the dispatcher and loadout files agree on
            # what a folder declaration is.
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

    Returns the backup path when a copy was taken, else ``None``. A
    populated-but-broken dispatcher (a hand-edit typo) is damaged but
    recoverable: this preserves its original bytes verbatim before the
    caller overwrites the file with a canonical render, so config that was
    only one typo away from intact is never silently lost.

    Defensive by design: a missing file, a clean (parseable) file, or any
    OSError reading/copying the original is a no-op (we do not block the
    write on a best-effort backup, and a clean file needs no backup). The
    copy uses ``shutil.copy2`` to keep the original bytes and mtime; a
    pre-existing ``.bak`` is overwritten so the latest damaged version is
    the one preserved.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        # Missing (first run) or unreadable - nothing recoverable to save.
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
        # Best-effort: a failed backup must not block a panic toggle or
        # repair write. The malformed flag still routes the panel to
        # degraded mode so the user is warned before relying on the rewrite.
        return None
    return backup


def write_dispatcher(path: str, state: DispatcherState) -> None:
    """Atomically write the canonical dispatcher for ``state`` to ``path``.

    Delegates to ``NukeSurvivalLoadout.atomic_io.write_atomic`` (tempfile + fsync +
    ``os.replace``). Idempotent - re-calling with the same state on a
    matching file is a byte-for-byte no-op at the content level.

    SAFETY: before the replace, if the existing ``path`` is present but
    unparseable, its original bytes are copied to ``path + BACKUP_SUFFIX``
    (see :func:`_backup_if_malformed`). This means a malformed dispatcher
    that a write would otherwise overwrite with reset defaults
    (``panic=False`` / ``active=""`` / no folders) is never lost without a
    recoverable side-copy. A clean or missing file is untouched.
    """
    _backup_if_malformed(os.fspath(path))
    write_atomic(path, render(state))
