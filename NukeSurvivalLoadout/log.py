"""NSL terminal logger.

Public API:
    loading(plugin_name)
    failed(plugin_name, category, detail)
    warning(message)
    critical_phase_failed(phase, exc)

Output target is stdout only. v1 ships terminal output exclusively; persistent
log files, log rotation, and log levels are out of scope.
"""

from __future__ import annotations

import sys
import traceback
from typing import Optional


_LOADING_PREFIX = "NSL Loading..."
_FAILED_PREFIX = "NSL Failed ✗"
_WARNING_PREFIX = "NSL Warning:"


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def loading(plugin_name: str) -> None:
    _emit("{prefix} {name}".format(prefix=_LOADING_PREFIX, name=plugin_name))


def failed(plugin_name: str, category: str, detail: Optional[str] = None) -> None:
    if detail:
        line = "{prefix} {name}  ({category}: {detail})".format(
            prefix=_FAILED_PREFIX,
            name=plugin_name,
            category=category,
            detail=detail,
        )
    else:
        line = "{prefix} {name}  ({category})".format(
            prefix=_FAILED_PREFIX,
            name=plugin_name,
            category=category,
        )
    _emit(line)


def traceback_block(tb: str) -> None:
    """Emit a captured exception traceback as a sub-block under ``failed()``.

    Terminal output formerly stopped
    at the one-line ``NSL Failed ✗ Name (Category: detail)`` headline,
    leaving engineers to open the side panel's Log tab for the actual
    traceback. Headless / render-farm sessions don't have a panel at
    all, so the traceback was unreachable. This helper writes the full
    traceback directly to stdout (same channel as the headline above)
    so the terminal carries the complete failure context inline.

    The headline above is the scannable tl;dr; this is the engineer
    detail. Empty/None ``tb`` is a no-op so the helper is safe to call
    unconditionally from the loader.
    """
    if not tb:
        return
    sys.stdout.write(tb.rstrip("\n") + "\n")
    sys.stdout.flush()


def warning(message: str) -> None:
    _emit("{prefix} {message}".format(prefix=_WARNING_PREFIX, message=message))


def critical_phase_failed(phase: str, exc: BaseException) -> None:
    _emit("{prefix} [Phase: {phase}] {exc_type}: {exc}".format(
        prefix=_FAILED_PREFIX,
        phase=phase,
        exc_type=type(exc).__name__,
        exc=exc,
    ))
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    sys.stdout.write(tb)
    sys.stdout.flush()
