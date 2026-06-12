"""NSL domain layer - pure-Python plugin / loadout state logic.

Re-exports the public surface of each domain submodule. NO ``import nuke``
in this layer.

There is no ``failure_taxonomy`` (FailureCategory / classify) in this
layer: Nuke's NUKE_PATH walker is the loader and plugin-load failures
crash the interpreter outright, so NSL does not classify them. The
graceful-failure surface that survives (source folder missing, dispatcher
syntax error) is small enough not to need an enum.
"""

from NukeSurvivalLoadout.domain.effective_state import EffectiveState, Layer, resolve_effective
from NukeSurvivalLoadout.domain.folder_ops import (
    HealthState,
    add_folder,
    add_folder_and_save,
    health_check,
    remove_folder,
    remove_folder_and_save,
    reorder,
    reorder_and_save,
)
from NukeSurvivalLoadout.domain.loadout_ops import (
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
from NukeSurvivalLoadout.domain.panic import (
    engage_panic,
    is_panic_engaged,
    release_panic,
    reset_global_to_default,
)
from NukeSurvivalLoadout.domain.scanner import Plugin, scan_folder
from NukeSurvivalLoadout.domain.undo_stack import MAX_UNDO_STEPS, UndoStack, UndoStackRegistry

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
