"""Plugin Standard scanner - discover Plugins inside a Plugins Folder.

Read-only. Nothing here writes to disk or changes its input.

``Plugin`` carries identity and folder metadata only. `enabled` and
`gui_only` are Loadout state and live elsewhere. Ignored and empty
folders are skipped with no panel message.
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

    `name` and `folder_name` are both the folder basename, byte-exact.
    See :func:`_resolve_plugin_name` for why there is no normalization.
    `path` is absolute. `source` is the Plugins Folder it was found in.
    """

    name: str
    folder_name: str
    path: str
    source: str


def _is_naming_rule_valid(folder_name: str) -> bool:
    """Accept any non-empty folder name as a Plugin Name.

    Do not add a character whitelist. Real plugin folders carry version
    suffixes like ``KnobScripter-3.2.0`` and would be filtered out. Junk
    and empty folders are already handled by :func:`_is_ignored_folder`
    and :func:`plugin_folder_has_content`.
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

    Do not normalize, and never turn spaces into underscores. The name
    addresses the folder on disk and is compared against real
    ``os.listdir`` basenames, so any change forks the plugin's identity.
    A Disable on a space-named folder would target a missing path and
    revert at next boot.
    """
    return folder_name


def plugin_folder_has_content(folder_path: PathLike) -> bool:
    """Report whether a Plugin folder has meaningful content.

    The one canonical test, used by both :func:`scan_folder` and
    ``folder_ops.health_check`` so the two cannot drift.

    Dotfiles and `Thumbs.db` do not count as content. `.gitkeep` does.
    Any subfolder counts, because only files are checked.
    """
    try:
        entries = list(os.scandir(os.fspath(folder_path)))
    except (FileNotFoundError, PermissionError, NotADirectoryError, OSError):
        return False

    for entry in entries:
        try:
            name = entry.name
            # Default ``follow_symlinks=True``, so a Plugin that symlinks
            # its own subfolders still counts as having content.
            if entry.is_dir():
                return True
            if name == PLUGIN_GITKEEP_EXCEPTION:
                return True
            if name in PLUGIN_NON_CONTENT_FILE_NAMES:
                continue
            if name.startswith(PLUGIN_NON_CONTENT_FILE_PREFIX):
                continue
            return True
        except OSError:
            # One unreadable entry must not abort the scan.
            continue
    return False


def scan_folder(path: PathLike) -> List[Plugin]:
    """Return the Plugins discovered in a Plugins Folder.

    Non-recursive, so only top-level folders. Results are sorted by
    Plugin Name. A path that does not exist or is not a directory
    returns an empty list, and the scanner reports no error.
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
            # Default ``follow_symlinks=True``, so Plugin folders that are
            # symlinks into a shared tree are still discovered.
            is_dir = entry.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue

        folder_name = entry.name

        if _is_ignored_folder(folder_name):
            continue

        if not _is_naming_rule_valid(folder_name):
            continue

        entry_path = Path(entry.path)
        if not plugin_folder_has_content(entry_path):
            continue

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
