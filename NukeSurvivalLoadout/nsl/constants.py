"""NSL constants - single source of truth.

Every other NSL module imports from here. No string literals duplicated downstream.
"""

from __future__ import annotations

import os
from pathlib import Path


# -- Loadouts folder -------------------------------------------------------
# Default save location is ~/.nuke/loadouts/. Resolved when called, not
# at import, so a changed HOME is picked up.


NUKE_DIR_NAME = ".nuke"
LOADOUTS_DIR_NAME = "loadouts"


def nuke_user_dir() -> Path:
    """The directory Nuke resolves ``~/.nuke`` under.

    Mirrors Nuke's lookup order. ``HOME`` first on every platform, which
    matters on Windows where ``expanduser`` no longer reads it. An empty
    ``HOME`` counts as unset.
    """
    home = os.environ.get("HOME")
    if home:
        return Path(home)
    if os.name == "nt":
        # Matches Nuke's fallback: USERPROFILE, then HOMEDRIVE+HOMEPATH.
        return Path(os.path.expanduser("~"))
    # Skip expanduser here. With HOME empty it echoes back and collapses
    # to "/", so read the account database instead.
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

LOADOUT_FILENAME_MAX_STEM_LEN = 100

# The read-only Global layer row. NSL never writes it as a user loadout.
RESERVED_LOADOUT_STEM = "Global"

# Used for the auto-created Loadout and for Save As with no name.
DEFAULT_CUSTOM_LOADOUT_STEM = "Custom"


# -- Global source marker -------------------------------------------------

# Global Plugins record ``Plugin.source`` as this marker, so the real
# folder path never shows in the panel. The angle brackets cannot
# collide with a real path. ``Plugin.path`` still holds the real path.

GLOBAL_SOURCE_MARKER = "<global>"


# -- Global folder-card marker --------------------------------------------

# The folder card shows a synthetic "Global" row when a Global layer
# exists. The row carries this marker as its ``FolderEntry.path``, so
# the wiring can tell it apart from a real folder.

GLOBAL_PLUGINS_FOLDER_SENTINEL = "<NSL_GLOBAL_PLUGINS>"


# -- Supported Nuke version range -----------------------------------------

# The floor is Nuke 13, the first Foundry release with Python 3
# (VFX Reference Platform 2020).

SUPPORTED_NUKE_VERSION_MIN = 13
SUPPORTED_NUKE_VERSION_MAX = None  # None means no upper bound


# -- Global/ chain layer ---------------------------------------------------

# ``Global/init.py`` is the executable chain head. It declares the two
# folder vars and calls the NSL loader. ``Global/Global_Loadout/init.py``
# is declarative and is parsed at boot, never executed.

GLOBAL_FOLDER_NAME = "Global"
GLOBAL_PLUGINS_VAR_NAME = "global_plugins"
GLOBAL_LOADOUT_DIR_NAME = "Global_Loadout"
GLOBAL_DEFAULT_PLUGINS_REL = "./plugins"
GLOBAL_DEFAULT_LOADOUT_REL = "./Global_Loadout"


def global_dir() -> Path:
    return install_root() / GLOBAL_FOLDER_NAME


# -- Ignored folders ------------------------------------------------------

PLUGIN_FOLDER_IGNORE_PREFIXES = ("_", ".")
PLUGIN_FOLDER_IGNORE_NAMES = ("__pycache__",)


# -- Empty-folder content rules -------------------------------------------

# `.gitkeep` counts as content even though it starts with a dot.

PLUGIN_NON_CONTENT_FILE_NAMES = ("Thumbs.db",)
PLUGIN_NON_CONTENT_FILE_PREFIX = "."
PLUGIN_GITKEEP_EXCEPTION = ".gitkeep"
