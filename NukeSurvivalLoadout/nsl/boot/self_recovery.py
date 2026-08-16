"""NSL self-recovery - phase-level try/except with cascade-on-failure.

`KeyboardInterrupt` and `SystemExit` always propagate, so the user can
still abort Nuke. Once any phase fails, later phases skip their body and
return at once. The panel reads the failure flag to show degraded mode.

Phase names are free-form. Pass a short phrase such as "Plugins Folder
scan", and the logger prefixes it with `Phase: `.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional, Tuple

from nsl import log


_state = {
    "failed": False,
    "phase": None,
    "exc": None,
}


def boot_failed() -> bool:
    return bool(_state["failed"])


def failed_phase() -> Optional[str]:
    return _state["phase"]


def failure_exception() -> Optional[BaseException]:
    return _state["exc"]


def reset() -> None:
    _state["failed"] = False
    _state["phase"] = None
    _state["exc"] = None


def _record_failure(phase_name: str, exc: BaseException) -> None:
    _state["failed"] = True
    _state["phase"] = phase_name
    _state["exc"] = exc
    log.critical_phase_failed(phase_name, exc)


def run_phase(
    phase_name: str,
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Tuple[bool, Any]:
    if _state["failed"]:
        return (False, None)
    try:
        value = func(*args, **kwargs)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        _record_failure(phase_name, exc)
        return (False, None)
    return (True, value)


@contextmanager
def phase(phase_name: str) -> Iterator[dict]:
    token = {"skipped": False, "ok": False}
    if _state["failed"]:
        token["skipped"] = True
        yield token
        return
    try:
        yield token
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        _record_failure(phase_name, exc)
        return
    token["ok"] = True
