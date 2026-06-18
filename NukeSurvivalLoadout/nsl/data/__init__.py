"""NSL data layer: filename rules, in-memory loadout shapes, typed errors.

Re-exports the public surface so callers can
``from nsl.data import LoadoutFile, validate_filename``
without reaching into the submodule directly.
"""

from nsl.data.errors import (
    MalformedNSLDataError,
    MalformedSettingsError,
)
from nsl.data.filename_rules import (
    ValidationResult,
    next_available_name,
    sanitize_user_input,
    validate_filename,
)
from nsl.data.loadout_file import (
    LoadoutFile,
    PluginEntry,
)

__all__ = [
    "MalformedNSLDataError",
    "MalformedSettingsError",
    "ValidationResult",
    "next_available_name",
    "sanitize_user_input",
    "validate_filename",
    "LoadoutFile",
    "PluginEntry",
]
