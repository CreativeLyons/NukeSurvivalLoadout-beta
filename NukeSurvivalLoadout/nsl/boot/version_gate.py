"""NSL Nuke-version gate.

Reads ``nuke.NUKE_VERSION_MAJOR`` and returns ``False`` after printing a
refusal line when the version is out of range.

A refusal is a hard stop. The caller registers no panel and runs no
further NSL code, and Nuke starts normally without NSL.
"""

from __future__ import annotations

from typing import Optional

from nsl import log
from nsl.constants import (
    SUPPORTED_NUKE_VERSION_MAX,
    SUPPORTED_NUKE_VERSION_MIN,
)


def _supported_range_label() -> str:
    if SUPPORTED_NUKE_VERSION_MAX is None:
        return f"Nuke {SUPPORTED_NUKE_VERSION_MIN} and later"
    if SUPPORTED_NUKE_VERSION_MAX == SUPPORTED_NUKE_VERSION_MIN:
        return f"Nuke {SUPPORTED_NUKE_VERSION_MIN}"
    return f"Nuke {SUPPORTED_NUKE_VERSION_MIN} to {SUPPORTED_NUKE_VERSION_MAX}"


def _emit_refusal(detected: object) -> None:
    line = (
        f"Unsupported Nuke version: {detected}. "
        f"NSL v1 supports {_supported_range_label()}."
    )
    # The refusal prefix carries the ✗ glyph. Go through the logger's own
    # writer so it cannot raise on ASCII stdout.
    log._write_stdout(f"{log._FAILED_PREFIX} {line}\n")


def _read_nuke_version_major() -> Optional[int]:
    try:
        import nuke  # type: ignore
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None
    try:
        return int(getattr(nuke, "NUKE_VERSION_MAJOR"))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


def check_nuke_version() -> bool:
    detected = _read_nuke_version_major()
    if detected is None:
        _emit_refusal("unknown")
        return False
    if detected < SUPPORTED_NUKE_VERSION_MIN:
        _emit_refusal(detected)
        return False
    if (
        SUPPORTED_NUKE_VERSION_MAX is not None
        and detected > SUPPORTED_NUKE_VERSION_MAX
    ):
        _emit_refusal(detected)
        return False
    return True
