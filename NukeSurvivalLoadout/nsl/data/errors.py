"""Typed exceptions for malformed NSL data files.

The exceptions are inert: they carry diagnostic detail and chain the
underlying parse exception via ``raise ... from ...``. They never delete,
rename, modify, or auto-recover the on-disk file. Preserving the file is
the reader's job; this module only describes the failure.
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
    """Common base for malformed NSL data-file exceptions.

    Callers may catch this to handle any concrete subclass uniformly
    (terminal warning, Summary-tab advisory) while still branching on
    subclass for file-specific messaging.
    """

    def __init__(self, path: PathLike, reason: str) -> None:
        self.path: str = os.fspath(path)
        self.reason: str = reason
        super().__init__(f"{self.path}: {self.reason}")


class MalformedSettingsError(MalformedNSLDataError):
    """A persisted NSL settings file exists but does not match the schema.

    Covers invalid JSON and missing or wrong ``nsl_settings`` marker.
    """
