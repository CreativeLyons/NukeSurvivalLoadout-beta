"""NSL boot-sequence verification.

``NukeSurvivalLoadout/init.py`` is not the loader. The four-file chain
(``~/.nuke/init.py`` -> NSL middle -> loadouts dispatcher -> active
loadout) walks via ``pluginAddPath`` only; Nuke does the actual loading.
This module is a thin verification + log pass so the panel and
diagnostics still have something to inspect.

Public surface:
    - ``BootResult`` - dispatcher state observed at boot.
    - ``run_boot_sequence()`` - read the dispatcher, log, return.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from NukeSurvivalLoadout import log
from NukeSurvivalLoadout.boot.dispatcher import DispatcherState, read_dispatcher


__all__ = ["BootResult", "run_boot_sequence"]


@dataclass
class BootResult:
    dispatcher_path: str = ""
    dispatcher_present: bool = False
    dispatcher_state: Optional[DispatcherState] = None


def _loadouts_init_path() -> str:
    return os.path.expanduser("~/.nuke/loadouts/init.py")


def run_boot_sequence() -> BootResult:
    path = _loadouts_init_path()
    present = os.path.exists(path)
    state: Optional[DispatcherState] = None

    if present:
        try:
            state = read_dispatcher(path)
        except Exception as exc:
            # Real problem - a corrupt/unreadable dispatcher is worth a
            # terminal line. The routine cases below are intentionally
            # silent (see comment).
            log.warning(f"NSL dispatcher unreadable at {path}: {exc}")
            state = None

    # Routine boot states are NOT logged to the terminal:
    #   * dispatcher absent  -> normal first-run ("no loadouts yet"); the
    #     panel guides the user. Not an error.
    #   * dispatcher present -> normal every-launch state (panic/active);
    #     diagnostic chatter, not signal.
    # Both remain inspectable via the returned BootResult; only the
    # genuine "unreadable" case above surfaces in stdout.

    return BootResult(
        dispatcher_path=path,
        dispatcher_present=present,
        dispatcher_state=state,
    )
