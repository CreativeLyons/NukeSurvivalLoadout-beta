"""NSL bootstrap entrypoint - Nuke's `init.py` pass.

Install model: the user adds exactly one line to ``~/.nuke/init.py``:

    nuke.pluginAddPath("<path>/NukeSurvivalLoadout")

That makes Nuke's NUKE_PATH walker run this file. NSL then checks the
version, adds the loadouts dir and the Global dir, and reads the
dispatcher for diagnostics.

On a first run there is no dispatcher yet, so that step does nothing.
The panel writes one the first time the user saves a Loadout.

A broken loadout surfaces as Nuke's own traceback. Recovery is edit and
relaunch, or PANIC_MODE in the dispatcher.
"""

from __future__ import annotations

import os
import sys

import nuke  # noqa: F401 - Nuke injects this at runtime


_NSL_INSTALL_ROOT = os.path.dirname(os.path.abspath(__file__))
if _NSL_INSTALL_ROOT not in sys.path:
    sys.path.insert(0, _NSL_INSTALL_ROOT)

from nsl.boot.version_gate import check_nuke_version  # noqa: E402
from nsl.boot.sequence import run_boot_sequence  # noqa: E402


def _loadouts_dir() -> str:
    # Imported here, not at module top. The sys.path insert above has to
    # run before any nsl import.
    from nsl.constants import loadouts_dir

    return str(loadouts_dir())


def _global_dir() -> str:
    from nsl.constants import global_dir

    return str(global_dir())


def _run() -> None:
    if not check_nuke_version():
        return

    loadouts_dir = _loadouts_dir()

    if os.path.exists(os.path.join(loadouts_dir, "init.py")):
        nuke.pluginAddPath(loadouts_dir)

    # Nuke loads backwards and the last added, loads first.
    # Global is last so it loads first. The others loadouts go after.
    global_dir = _global_dir()
    if os.path.exists(os.path.join(global_dir, "init.py")):
        nuke.pluginAddPath(global_dir)

    run_boot_sequence()


_run()
