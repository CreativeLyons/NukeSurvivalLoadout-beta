"""Plugin Standard scanner - discover Plugins inside a Plugins Folder.

Public surface:
    Plugin - frozen value object (identity + folder metadata)
    scan_folder(path) - non-recursive scan returning list[Plugin]

Scope contract:
    - Pure / read-only. No disk writes, no input mutation.
    - Plugin value object carries identity + folder metadata only; it does NOT
      embed `enabled` / `gui_only` (those are Loadout state).
    - No `import nuke`. Domain layer is Nuke-free.
    - Space→underscore resolution happens here. Ignored and empty folders are
      silently skipped (no panel surfacing for non-Plugin folders).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

from nsl.constants import (
    PLUGIN_FOLDER_IGNORE_NAMES,
    PLUGIN_FOLDER_IGNORE_PREFIXES,
    PLUGIN_GITKEEP_EXCEPTION,
    PLUGIN_NON_CONTENT_FILE_NAMES,
    PLUGIN_NON_CONTENT_FILE_PREFIX,
)

__all__ = ["Plugin", "plugin_folder_has_content", "scan_folder"]

PathLike = Union[str, "os.PathLike[str]"]


@dataclass(frozen=True)
class Plugin:
    """A Plugin discovered by a scan of a Plugins Folder.

    `name` is the Plugin Name: the on-disk folder basename, byte-exact (no
    normalization - see :func:`_resolve_plugin_name`). `folder_name` is the
    same byte-exact basename, retained as an explicit alias for call sites that
    address the folder on disk. `path` is the absolute path to the folder.
    `source` is the Plugins Folder that owned this Plugin in this scan
    (absolute path).

    The value object intentionally carries no Loadout state (enabled /
    gui_only); resolution of effective state lives downstream.
    """

    name: str
    folder_name: str
    path: str
    source: str


def _is_naming_rule_valid(folder_name: str) -> bool:
    """Accept any non-empty folder name as a Plugin Name.

    Do not gate on the folder name: real-world plugin folders carry dotted
    version suffixes (``KnobScripter-3.2.0``, ``AnimationMaker_v1.5``,
    ``Dots_v5.1``, ``Stamps_1.1.0``) and other punctuation. A character
    whitelist (letters / digits / dash / underscore / space only, leading
    alnum) would silently filter every one of them. NSL must surface whatever
    is actually present in the Plugins Folder, not an invented standard.

    The real filtering still happens - just not here:
      * junk / hidden dirs (``.git``, ``__pycache__``, anything starting
        with ``_`` or ``.``) → :func:`_is_ignored_folder`.
      * empty folders → :func:`plugin_folder_has_content`.

    So this guard only rejects a literally empty name (which a real directory
    entry can never have).
    """
    return bool(folder_name)


def _is_ignored_folder(folder_name: str) -> bool:
    """Report whether a folder name is an ignored (non-Plugin) folder."""
    if folder_name in PLUGIN_FOLDER_IGNORE_NAMES:
        return True
    for prefix in PLUGIN_FOLDER_IGNORE_PREFIXES:
        if folder_name.startswith(prefix):
            return True
    return False


def _resolve_plugin_name(folder_name: str) -> str:
    """Resolve a folder name to a Plugin Name: the byte-exact basename.

    The Plugin Name is the on-disk folder basename, unchanged. We deliberately
    do NOT normalize (e.g. spaces -> underscores): the name is used downstream
    to address the folder on disk - ``os.path.join(folder, name)`` and the
    ``(folder, name)`` dedup tuple in both the per-loadout helper
    (``_HELPER_DEF``) and the global loader. Those compare against the real
    basenames returned by ``os.listdir``, so any normalization here forks the
    plugin's identity: an explicit Disable / GUI-only on a space-named folder
    would target a path that does not exist and fail to suppress the folder
    sweep, silently reverting the user's decision at next boot. Normalization
    also collapsed ``My Plugin`` and ``My_Plugin`` into one key, hiding a
    plugin. The name is always rendered with ``repr()`` in generated code, so a
    space is syntactically safe. Keep this seam identity; do not re-add
    normalization without solving the on-disk identity problem first.
    """
    return folder_name


def plugin_folder_has_content(folder_path: PathLike) -> bool:
    """Report whether a Plugin folder has meaningful content.

    This is the ONE canonical "is this Plugin folder non-empty" predicate.
    Both Plugin discovery (:func:`scan_folder`) and folder health
    classification (``folder_ops.health_check``) call it, so the two cannot
    drift on what counts as content - in particular on the symlink policy
    below. (Issue 18: ``health_check`` previously used
    ``follow_symlinks=False`` and reported a folder of symlinked plugins
    EMPTY even though those plugins appeared in the grid.)

    A folder is considered empty if it contains no files, or only files that
    don't count as content. Files starting with `.` (dot) and `Thumbs.db` do
    not count. `.gitkeep` is the one exception that DOES count.

    Subfolders (e.g. `gizmos/`, `python/`) count as content - only files are
    enumerated as non-content. A folder containing only a non-empty subfolder
    is treated as having meaningful content.
    """
    try:
        entries = list(os.scandir(os.fspath(folder_path)))
    except (FileNotFoundError, PermissionError, NotADirectoryError, OSError):
        return False

    for entry in entries:
        try:
            name = entry.name
            # ``follow_symlinks=True`` (the default) so a Plugin organising
            # its internals via symlinks (``Octopus/python -> shared/python``)
            # still counts as having content. Broken / circular symlinks
            # return False from ``is_dir`` naturally, so they don't get
            # miscounted as content.
            if entry.is_dir():
                # Any subfolder counts as content - the non-content list
                # enumerates files only (.dotfiles, Thumbs.db).
                return True
            if name == PLUGIN_GITKEEP_EXCEPTION:
                return True
            if name in PLUGIN_NON_CONTENT_FILE_NAMES:
                continue
            if name.startswith(PLUGIN_NON_CONTENT_FILE_PREFIX):
                continue
            return True
        except OSError:
            # A single inaccessible entry must not abort the scan; treat as
            # non-content and keep looking.
            continue
    return False


def scan_folder(path: PathLike) -> List[Plugin]:
    """Return the Plugins discovered in a Plugins Folder.

    Behavior:
        - Non-recursive (top-level folders only).
        - Skip ignored and empty folders.
        - Resolve spaces in folder names to underscores at scan time.
        - Read-only - never modify anything in the Plugins Folder.

    Results are sorted by Plugin Name for deterministic ordering. If the
    given path does not exist or is not a directory, returns an empty list -
    higher layers surface detection errors; the scanner itself simply finds
    nothing.
    """
    try:
        folder = Path(os.fspath(path))
    except TypeError:
        return []

    try:
        if not folder.is_dir():
            return []
    except OSError:
        return []

    source = os.fspath(folder)
    plugins: List[Plugin] = []

    try:
        scan = list(os.scandir(folder))
    except (FileNotFoundError, PermissionError, NotADirectoryError, OSError):
        return []

    for entry in scan:
        try:
            # ``follow_symlinks=True`` (the default) so a TD who symlinks
            # Plugin folders into a Plugins Folder
            # (e.g. ``Global/plugins/Octopus -> /mnt/shared/.../Octopus``,
            # or a user organising their plugin tree with symlinks) gets
            # those Plugins discovered. With ``follow_symlinks=False`` every
            # symlink-to-directory would be silently filtered. Broken /
            # circular symlinks still naturally return False from ``is_dir``
            # so the scanner doesn't get tricked into walking them.
            is_dir = entry.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue

        folder_name = entry.name

        # Skip ignored (non-Plugin) folders.
        if _is_ignored_folder(folder_name):
            continue

        # Skip folders with no usable name.
        if not _is_naming_rule_valid(folder_name):
            continue

        # Skip empty folders.
        entry_path = Path(entry.path)
        if not plugin_folder_has_content(entry_path):
            continue

        # Spaces resolve to underscores in the Plugin Name.
        name = _resolve_plugin_name(folder_name)

        plugins.append(
            Plugin(
                name=name,
                folder_name=folder_name,
                path=os.fspath(entry_path),
                source=source,
            )
        )

    plugins.sort(key=lambda p: p.name)
    return plugins
