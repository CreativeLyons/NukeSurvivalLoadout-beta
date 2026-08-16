"""Typed exceptions for malformed NSL data files.

These never delete, rename, or repair the file. They only report the
failure, and chain the original parse error with ``raise ... from ...``.
"""

from __future__ import annotations

import os
from typing import Union

__all__ = [
    "MalformedNSLDataError",
    "MalformedSettingsError",
]

PathLike = Union[str, "os.PathLike[str]"]


class MalformedNSLDataError(Exception):
    """Common base for malformed NSL data-file exceptions."""

    def __init__(self, path: PathLike, reason: str) -> None:
        self.path: str = os.fspath(path)
        self.reason: str = reason
        super().__init__(f"{self.path}: {self.reason}")


class MalformedSettingsError(MalformedNSLDataError):
    """A persisted NSL settings file exists but does not match the schema.

    Covers invalid JSON and missing or wrong ``nsl_settings`` marker.
    """
