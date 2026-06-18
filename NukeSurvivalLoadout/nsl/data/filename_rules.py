"""Loadout name validation, sanitisation, and collision handling.

Loadouts are folders (`<loadouts_dir>/<name>/init.py`); names are bare
stems everywhere (the JSON-era `.loadout` extension is retired).

Pure module: no I/O, no globals, no logger calls. `next_available_name`
accepts `existing` as an in-memory iterable of taken stems; callers are
responsible for listing the directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from nsl.constants import (
    DEFAULT_CUSTOM_LOADOUT_STEM,
    LOADOUT_FILENAME_MAX_STEM_LEN,
    RESERVED_LOADOUT_STEM,
)


# User-facing error strings for the loadout name rules.
_ERR_DISALLOWED_CHARS = (
    "Loadout name can only contain ASCII letters, numbers, `-`, and `_`. "
    "Spaces are resolved to underscores. "
    "For richer display names, edit the `name` field inside the file."
)
_ERR_LEADING_DOT_OR_UNDERSCORE = "Loadout name cannot start with `.` or `_`."
_ERR_RESERVED_GLOBAL = "`Global` is a reserved name. Choose a different name."
# `Custom` is NSL's auto-scratch slot - the loadout the user is dropped
# into when they edit while Global is active. Reserving the name (any
# case) keeps it usable as the wildcard. Users name their own loadouts
# anything else.
_ERR_RESERVED_CUSTOM = (
    "`Custom` is a reserved name (NSL's auto-scratch loadout). "
    "Please choose another name than `Custom` or `Global`."
)

# Additional error strings; minimal-surprise wording.
_ERR_EMPTY_STEM = "Loadout name cannot be empty."
_ERR_STEM_TOO_LONG = (
    f"Loadout name cannot exceed {LOADOUT_FILENAME_MAX_STEM_LEN} characters."
)
_ERR_RESERVED_DEVICE = (
    "`{name}` is a reserved device name on Windows and cannot be used as "
    "a folder name. Choose a different name."
)


_ALLOWED_STEM_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)

# Windows reserved device names (any case): such a directory cannot be
# created or opened through the normal Win32 namespace. Rejected on
# EVERY platform - a loadout folder is portable data (synced dotfiles,
# studio-shared setups), and a `con` folder created on macOS would be
# unopenable on a Windows box.
_WINDOWS_RESERVED_DEVICE_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of `validate_filename`.

    Attributes:
        is_valid: True when the input matches every name rule.
        filename: The candidate bare stem (e.g. ``"Comp_Daily"``) when valid.
            When invalid, the value is the closest sanitised candidate NSL
            would have written -- callers may surface it to the user for
            context but must not commit it.
        error: Human-readable error string for surfacing in the UI. Empty
            string when `is_valid` is True.
    """

    is_valid: bool
    filename: str
    error: str


def sanitize_user_input(text: str) -> str:
    """Apply NSL's whitespace normalisation to a user-typed Loadout name.

    Space-handling rule: spaces (any kind of whitespace) resolve to
    underscores. Leading/trailing whitespace is
    stripped first so a name typed with a trailing space does not produce a
    trailing underscore.
    """
    if text is None:  # type: ignore[unreachable]
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    return "".join("_" if ch.isspace() else ch for ch in stripped)


def validate_filename(name: str) -> ValidationResult:
    """Validate a Loadout name (a bare stem, e.g. `Comp_Daily`).

    Rules enforced:
      * Allowed characters: ASCII letters, digits, `-`, `_`.
      * No leading dot or underscore in the stem.
      * Reserved stem `Global` (case-insensitive).
      * Reserved stem `Custom` (case-insensitive).
      * Windows reserved device names (CON, PRN, AUX, NUL, COM1-9,
        LPT1-9; case-insensitive) rejected on every platform.
      * Stem length cap (``LOADOUT_FILENAME_MAX_STEM_LEN`` characters).
      * Empty stem rejected.
    """
    if name is None:  # type: ignore[unreachable]
        return ValidationResult(False, "", _ERR_EMPTY_STEM)

    stem = sanitize_user_input(name)

    if not stem:
        return ValidationResult(False, stem, _ERR_EMPTY_STEM)

    if stem[0] in (".", "_"):
        return ValidationResult(False, stem, _ERR_LEADING_DOT_OR_UNDERSCORE)

    if stem.lower() == RESERVED_LOADOUT_STEM.lower():
        return ValidationResult(False, stem, _ERR_RESERVED_GLOBAL)

    if stem.lower() == DEFAULT_CUSTOM_LOADOUT_STEM.lower():
        return ValidationResult(False, stem, _ERR_RESERVED_CUSTOM)

    if stem.lower() in _WINDOWS_RESERVED_DEVICE_STEMS:
        return ValidationResult(
            False, stem, _ERR_RESERVED_DEVICE.format(name=stem)
        )

    if len(stem) > LOADOUT_FILENAME_MAX_STEM_LEN:
        return ValidationResult(False, stem, _ERR_STEM_TOO_LONG)

    for ch in stem:
        if ch not in _ALLOWED_STEM_CHARS:
            return ValidationResult(False, stem, _ERR_DISALLOWED_CHARS)

    return ValidationResult(True, stem, "")


def next_available_name(base: str, existing: Iterable[str]) -> str:
    """Return the lowest-numbered non-colliding loadout stem for `base`.

    On collision, NSL appends `_2`, `_3`, ... (lowest unused integer
    >= 2). The base stem itself is preferred when it is not in
    `existing`.

    `existing` is consumed once; pass a set/list/tuple/generator of bare
    stems. Comparison is CASE-INSENSITIVE (casefold): the target
    filesystems (NTFS always, default macOS APFS) treat `Foo` and `foo`
    as the same directory, so a case-sensitive check would let a new
    `Foo` silently write into an existing `foo`. The returned value
    keeps the caller's case (filesystems are case-preserving; so is
    NSL).

    The collision suffix never pushes the result past the rules: the base
    stem is truncated to reserve room for `_<n>` before the suffix is
    appended, and every suffixed candidate is re-validated, so a returned
    `_<n>` stem always satisfies `validate_filename` (length cap
    included). The suffix budget is recomputed each iteration because the
    digit count grows (`_9` -> `_10` costs one more character).

    Raises ValueError when `base` cannot produce a valid name (e.g.
    empty, disallowed characters). Callers should run `validate_filename`
    first when accepting user input.
    """
    stem = sanitize_user_input(base)
    if not stem:
        raise ValueError(_ERR_EMPTY_STEM)

    taken = {name.casefold() for name in existing}
    candidate = stem
    if candidate.casefold() not in taken:
        return candidate
    suffix = 2
    while True:
        suffix_part = f"_{suffix}"
        # Reserve room for the suffix so the joined stem stays within the
        # length cap; recomputed each pass since the digit count grows.
        budget = LOADOUT_FILENAME_MAX_STEM_LEN - len(suffix_part)
        candidate = f"{stem[:budget]}{suffix_part}"
        if (
            candidate.casefold() not in taken
            and validate_filename(candidate).is_valid
        ):
            return candidate
        suffix += 1


__all__ = [
    "ValidationResult",
    "sanitize_user_input",
    "validate_filename",
    "next_available_name",
]
