"""NSL constants - single source of truth.

Every other NSL module imports from here. No string literals duplicated downstream.
"""

from __future__ import annotations

import os
from pathlib import Path


# -- Loadouts folder -------------------------------------------------------
# Loadouts: where files live. Default save location is
# ~/.nuke/loadouts/. Resolved at access time (not import time) so an
# env change is picked up. NOT Path.home(): on Windows, Python 3.8+
# Path.home()/expanduser ignore HOME (USERPROFILE only), while Nuke
# itself resolves ~/.nuke from HOME first when set - nuke_user_dir()
# mirrors Nuke's documented lookup so NSL and Nuke always agree on
# which .nuke tree is live.


NUKE_DIR_NAME = ".nuke"
LOADOUTS_DIR_NAME = "loadouts"


def nuke_user_dir() -> Path:
    """The directory Nuke resolves ``~/.nuke`` under.

    Mirrors Nuke's documented lookup order: ``HOME`` when set (on every
    platform - including Windows, where Python 3.8+ ``expanduser`` no
    longer consults it), otherwise the OS user profile via
    ``expanduser``. On POSIX this is behavior-identical to plain
    ``expanduser("~")``; empty ``HOME`` counts as unset.
    """
    home = os.environ.get("HOME")
    if home:
        return Path(home)
    if os.name == "nt":
        # Windows expanduser ignores HOME entirely (USERPROFILE, then
        # HOMEDRIVE+HOMEPATH) - exactly Nuke's fallback order.
        return Path(os.path.expanduser("~"))
    # POSIX with HOME unset or empty: skip expanduser (an empty HOME is
    # echoed back and collapses to "/") and resolve from the account
    # database directly.
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError):
        # No passwd entry (bare container): degrade like expanduser.
        return Path(os.path.expanduser("~"))


def loadouts_dir() -> Path:
    return nuke_user_dir() / NUKE_DIR_NAME / LOADOUTS_DIR_NAME


def install_root() -> Path:
    """The self-contained NSL install folder containing init.py and menu.py."""
    return Path(__file__).resolve().parents[1]


# -- Loadout names ----------------------------------------------------------
# Loadouts on disk are folders containing an init.py; names are bare
# stems everywhere (the JSON-era `.loadout` extension is fully retired).

LOADOUT_FILENAME_MAX_STEM_LEN = 100

# `Global` is the reserved stem for the read-only Global layer row;
# never written as a user loadout by NSL.
RESERVED_LOADOUT_STEM = "Global"

# Defaults for auto-created and Save-As-with-no-name.
DEFAULT_CUSTOM_LOADOUT_STEM = "Custom"


# -- Global source marker -------------------------------------------------
# Global Plugins (discovered in the Global plugins dir) record
# their ``Plugin.source`` as this marker rather than the absolute Global
# dir path, so the raw path never surfaces in the panel's
# source/visibility grouping. The angle brackets guarantee it can never
# collide with a real filesystem path. ``Plugin.path`` still holds the
# real filesystem path for README lookups.

GLOBAL_SOURCE_MARKER = "<global>"


# -- Global folder-card marker --------------------------------------------
# Plugins Folder management surface. The folder card lists
# user-added Plugins Folders. When a Global layer is resolved (Global
# Loadout has plugins), the card also surfaces a synthetic "Global"
# row pinned to the bottom of the list so artists discover that
# Global Plugins exist without leaking the raw folder path. The
# row uses this marker string as its ``FolderEntry.path`` so the
# wiring layer can recognise it (visibility map, select handler,
# reorder filter) without confusing it for a real filesystem path.

GLOBAL_PLUGINS_FOLDER_SENTINEL = "<NSL_GLOBAL_PLUGINS>"


# -- Supported Nuke version range -----------------------------------------
# Nuke version compatibility. NSL v1 supports Nuke 13 and
# later. The floor matches the first Foundry release with Python 3 as a
# supported runtime (VFX Reference Platform 2020).

SUPPORTED_NUKE_VERSION_MIN = 13
SUPPORTED_NUKE_VERSION_MAX = None  # None => no upper bound; 13..


# -- Global/ chain layer ---------------------------------------------------
# The Global layer lives at ``<install>/Global/`` so it follows the
# self-contained NSL install folder.
# ``Global/init.py`` is the executable chain head; it declares two
# redefinable folder vars and calls the NSL loader.
# ``Global/Global_Loadout/init.py`` is the declarative Global Loadout -
# parsed at boot, never executed in the Global role.

GLOBAL_FOLDER_NAME = "Global"
GLOBAL_PLUGINS_VAR_NAME = "global_plugins"
GLOBAL_LOADOUT_DIR_NAME = "Global_Loadout"
GLOBAL_DEFAULT_PLUGINS_REL = "./plugins"
GLOBAL_DEFAULT_LOADOUT_REL = "./Global_Loadout"


def global_dir() -> Path:
    return install_root() / GLOBAL_FOLDER_NAME


# -- Ignored folders ------------------------------------------------------
# Folders not treated as Plugins:
#   - Folders starting with `_` (underscore).
#   - Folders starting with `.` (dot).
#   - `__pycache__/` and similar tooling outputs.

PLUGIN_FOLDER_IGNORE_PREFIXES = ("_", ".")
PLUGIN_FOLDER_IGNORE_NAMES = ("__pycache__",)


# -- Empty-folder content rules -------------------------------------------
# Files starting with `.` (dot) and
# `Thumbs.db` do not count as content. `.gitkeep` is the one exception
# that does count as content.

PLUGIN_NON_CONTENT_FILE_NAMES = ("Thumbs.db",)
PLUGIN_NON_CONTENT_FILE_PREFIX = "."
PLUGIN_GITKEEP_EXCEPTION = ".gitkeep"
