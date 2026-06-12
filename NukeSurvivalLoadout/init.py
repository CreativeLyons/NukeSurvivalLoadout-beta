"""NSL bootstrap entrypoint - Nuke's `init.py` pass.

Install model: the user adds exactly one line to ``~/.nuke/init.py``:

    nuke.pluginAddPath("<path>/NukeSurvivalLoadout")

That makes Nuke's NUKE_PATH walker run this file. From here NSL:

  1. Runs the version gate; refusal short-circuits the rest of NSL.
  2. Calls ``nuke.pluginAddPath`` on ``~/.nuke/loadouts`` iff a
     dispatcher ``init.py`` already exists there - that hands control
     to the dispatcher on a later NUKE_PATH pass.
  3. Calls ``nuke.pluginAddPath`` on ``<repo>/Global`` iff a chain head
     ``init.py`` exists there. The Global layer is the baseline and must
     EXECUTE first; ``pluginAddPath`` prepends to the remaining scan
     queue (last-added runs first, verified on Nuke 16.0v9), so Global
     is added AFTER the loadouts dir to run BEFORE it.
  4. Runs the boot sequence (diagnostic read of dispatcher state).

If no dispatcher exists (first run on this machine), step 2 is a
no-op: the panel materializes it the first time the user saves a
loadout.

A broken active loadout is left to surface as Nuke's own traceback
(file + line) - there is no crash signpost. Recovery is edit-and-relaunch
(or PANIC_MODE in the dispatcher).

``KeyboardInterrupt`` / ``SystemExit`` propagate.
"""

from __future__ import annotations

import os
import sys

import nuke  # noqa: F401 - Nuke injects this at runtime


_NSL_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _NSL_REPO_ROOT not in sys.path:
    sys.path.insert(0, _NSL_REPO_ROOT)

from NukeSurvivalLoadout.boot.version_gate import check_nuke_version  # noqa: E402
from NukeSurvivalLoadout.boot.sequence import run_boot_sequence  # noqa: E402


def _loadouts_dir() -> str:
    return os.path.expanduser("~/.nuke/loadouts")


def _global_dir() -> str:
    from NukeSurvivalLoadout.constants import GLOBAL_FOLDER_NAME

    return os.path.join(_NSL_REPO_ROOT, GLOBAL_FOLDER_NAME)


def _run() -> None:
    gate = check_nuke_version()
    if not getattr(gate, "accepted", bool(gate)):
        return

    loadouts_dir = _loadouts_dir()

    if os.path.exists(os.path.join(loadouts_dir, "init.py")):
        nuke.pluginAddPath(loadouts_dir)

    # Global is added AFTER the loadouts dir so it EXECUTES first:
    # pluginAddPath prepends to the remaining scan queue, last-added
    # runs first. Global loads as the baseline; the active loadout
    # layers on top.
    global_dir = _global_dir()
    if os.path.exists(os.path.join(global_dir, "init.py")):
        nuke.pluginAddPath(global_dir)

    run_boot_sequence()


_run()
