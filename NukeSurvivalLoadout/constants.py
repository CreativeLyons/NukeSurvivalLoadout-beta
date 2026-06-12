"""NSL constants - single source of truth.

Every other NSL module imports from here. No string literals duplicated downstream.
"""

from __future__ import annotations

from pathlib import Path


# -- Loadouts folder -------------------------------------------------------
# Loadouts: where files live. Default save location is
# ~/.nuke/loadouts/. Resolved at access time via Path.home() so HOME
# can be overridden.

NUKE_DIR_NAME = ".nuke"
LOADOUTS_DIR_NAME = "loadouts"


def loadouts_dir() -> Path:
    return Path.home() / NUKE_DIR_NAME / LOADOUTS_DIR_NAME


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
# The Global layer lives at ``<nsl_root>/Global/`` (anchored at runtime
# from ``NukeSurvivalLoadout/__file__`` so it follows the install tree).
# ``Global/init.py`` is the executable chain head; it declares two
# redefinable folder vars and calls the NSL loader.
# ``Global/Global_Loadout/init.py`` is the declarative Global Loadout -
# parsed at boot, never executed in the Global role.

GLOBAL_FOLDER_NAME = "Global"
GLOBAL_PLUGINS_VAR_NAME = "global_plugins"
GLOBAL_LOADOUT_DIR_NAME = "Global_Loadout"
GLOBAL_DEFAULT_PLUGINS_REL = "./plugins"
GLOBAL_DEFAULT_LOADOUT_REL = "./Global_Loadout"


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
