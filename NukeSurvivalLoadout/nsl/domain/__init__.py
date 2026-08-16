"""NSL domain layer - pure-Python plugin / loadout state logic.

Never ``import nuke`` here.

There is no failure taxonomy on purpose. A plugin-load failure crashes
the interpreter, so there is nothing left for NSL to classify.
"""

from nsl.domain.effective_state import EffectiveState, Layer, resolve_effective
from nsl.domain.folder_ops import (
    HealthState,
    add_folder,
    add_folder_and_save,
    health_check,
    remove_folder,
    remove_folder_and_save,
    reorder,
    reorder_and_save,
)
from nsl.domain.loadout_ops import (
    Blocked,
    BlockedReason,
    OpResult,
    create,
    delete,
    duplicate,
    list_loadouts,
    rename,
    save,
    save_as,
    set_panic,
    switch_active,
)
from nsl.domain.panic import (
    engage_panic,
    is_panic_engaged,
    release_panic,
    reset_global_to_default,
)
from nsl.domain.scanner import Plugin, scan_folder
from nsl.domain.undo_stack import MAX_UNDO_STEPS, UndoStack, UndoStackRegistry

__all__ = [
    "EffectiveState",
    "Layer",
    "resolve_effective",
    "HealthState",
    "add_folder",
    "add_folder_and_save",
    "health_check",
    "remove_folder",
    "remove_folder_and_save",
    "reorder",
    "reorder_and_save",
    "Blocked",
    "BlockedReason",
    "OpResult",
    "create",
    "delete",
    "duplicate",
    "list_loadouts",
    "rename",
    "save",
    "save_as",
    "set_panic",
    "switch_active",
    "engage_panic",
    "is_panic_engaged",
    "release_panic",
    "reset_global_to_default",
    "Plugin",
    "scan_folder",
    "MAX_UNDO_STEPS",
    "UndoStack",
    "UndoStackRegistry",
]
