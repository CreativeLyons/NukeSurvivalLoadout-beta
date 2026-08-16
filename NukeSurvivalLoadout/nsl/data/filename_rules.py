"""Loadout name validation, sanitisation, and collision handling.

A Loadout is a folder, so names are bare stems with no extension. This
module does no I/O, so callers list the directory and pass the stems in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from nsl.constants import (
    DEFAULT_CUSTOM_LOADOUT_STEM,
    LOADOUT_FILENAME_MAX_STEM_LEN,
    RESERVED_LOADOUT_STEM,
)


_ERR_DISALLOWED_CHARS = (
    "Loadout name can only contain ASCII letters, numbers, `-`, and `_`. "
    "Spaces are resolved to underscores. "
    "For richer display names, edit the `name` field inside the file."
)
_ERR_LEADING_DOT_OR_UNDERSCORE = "Loadout name cannot start with `.` or `_`."
_ERR_RESERVED_GLOBAL = "`Global` is a reserved name. Choose a different name."
# NSL creates a Loadout called `Custom` itself and keeps it in memory.
# The name is reserved in any case so a user Loadout cannot take it.
_ERR_RESERVED_CUSTOM = (
    "`Custom` is a reserved name (NSL's auto-scratch loadout). "
    "Please choose another name than `Custom` or `Global`."
)

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

# Rejected on every platform, not only Windows. Loadout folders get
# shared, and a `con` folder made on macOS cannot be opened on Windows.
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
        filename: The valid bare stem. When invalid, this is the closest
            sanitised candidate. Show it to the user, but never save it.
        error: Message for the UI. Empty when `is_valid` is True.
    """

    is_valid: bool
    filename: str
    error: str


def sanitize_user_input(text: str) -> str:
    """Apply NSL's whitespace normalisation to a user-typed Loadout name.

    Any whitespace becomes an underscore. Leading and trailing whitespace
    is stripped first, so a trailing space leaves no trailing underscore.
    """
    if text is None:  # type: ignore[unreachable]
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    return "".join("_" if ch.isspace() else ch for ch in stripped)


def validate_filename(name: str) -> ValidationResult:
    """Validate a Loadout name (a bare stem, e.g. `Comp_Daily`).

    Rejected, in this order:
      * An empty stem, or one starting with `.` or `_`.
      * The reserved stems `Global` and `Custom`, in any case.
      * Windows device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) in
        any case, on every platform.
      * A stem over ``LOADOUT_FILENAME_MAX_STEM_LEN`` characters.
      * Any character outside ASCII letters, digits, `-`, and `_`.
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

    Returns `base` when it is free, else appends `_2`, `_3`, and so on.
    `existing` is read once, so a generator is fine.

    Matching is case-insensitive, because NTFS and default macOS APFS
    treat `Foo` and `foo` as the same folder. A case-sensitive check
    would let a new `Foo` write into an existing `foo`. The returned
    stem keeps the caller's case.

    The result always passes `validate_filename`. The base is truncated
    to leave room for the `_<n>` suffix, and that room grows at `_10`.

    Raises ValueError when `base` cannot make a valid name. Run
    `validate_filename` first on user input.
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
