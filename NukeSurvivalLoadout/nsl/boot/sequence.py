"""NSL boot-sequence verification.

NSL is not the loader. The four-file chain only walks via
``pluginAddPath`` and Nuke does the loading. This module just reads the
dispatcher and reports, so the panel has something to inspect.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from nsl import log
from nsl.boot.dispatcher import DispatcherState, read_dispatcher
from nsl.constants import loadouts_dir


__all__ = ["BootResult", "run_boot_sequence"]


@dataclass
class BootResult:
    dispatcher_path: str = ""
    dispatcher_present: bool = False
    dispatcher_state: Optional[DispatcherState] = None


def _loadouts_init_path() -> str:
    return os.fspath(loadouts_dir() / "init.py")


def run_boot_sequence() -> BootResult:
    path = _loadouts_init_path()
    present = os.path.exists(path)
    state: Optional[DispatcherState] = None

    if present:
        try:
            state = read_dispatcher(path)
        except Exception as exc:
            log.warning(f"NSL dispatcher unreadable at {path}: {exc}")
            state = None

    # Only the unreadable case above is logged. A missing dispatcher is a
    # normal first run and a present one is the normal state. Neither is
    # worth a terminal line, and both are readable from the BootResult.

    return BootResult(
        dispatcher_path=path,
        dispatcher_present=present,
        dispatcher_state=state,
    )
