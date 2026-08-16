"""NSL terminal logger.

Writes to stdout only. There are no log files, no rotation, and no
levels.
"""

from __future__ import annotations

import sys
import traceback


_LOADING_PREFIX = "NSL Loading..."
_FAILED_PREFIX = "NSL Failed ✗"
_WARNING_PREFIX = "NSL Warning:"


def _write_stdout(text: str) -> None:
    """Write ``text`` to stdout, surviving non-UTF-8 stdout encodings.

    A LANG=C session can resolve stdout to ASCII, where the ``✗`` glyph
    raises ``UnicodeEncodeError``. A log call must never abort a boot
    pass, so the line is re-sent with bad characters replaced.
    """
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        degraded = text.encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        sys.stdout.write(degraded)
    sys.stdout.flush()


def _emit(line: str) -> None:
    _write_stdout(line + "\n")


def loading(plugin_name: str) -> None:
    _emit(f"{_LOADING_PREFIX} {plugin_name}")


def warning(message: str) -> None:
    _emit(f"{_WARNING_PREFIX} {message}")


def critical_phase_failed(phase: str, exc: BaseException) -> None:
    _emit(f"{_FAILED_PREFIX} [Phase: {phase}] {type(exc).__name__}: {exc}")
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    _write_stdout(tb)
