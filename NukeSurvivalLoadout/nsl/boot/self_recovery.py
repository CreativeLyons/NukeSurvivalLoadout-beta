"""NSL self-recovery - phase-level try/except with cascade-on-failure.

Public API:
    run_phase(phase, func, *args, **kwargs) -> (ok, value)
    phase(phase_name) -> context-manager (records failures, cascades)
    boot_failed() -> bool
    failed_phase() -> str | None
    failure_exception() -> BaseException | None
    reset() -> None  (clears the recorded failure state)

The wrapper is itself a universal `except Exception:` with two unconditional
exclusions: `KeyboardInterrupt` and `SystemExit` ALWAYS propagate so the user
can still abort Nuke. Cascade rule: once any phase records a failure,
subsequent `run_phase` / `phase` calls skip their body and return immediately.
A degraded panel can read the failure flag exposed here.

Phase identity strings are free-form. Callers pass a short human-readable
phrase (e.g. "Plugins Folder scan", "Loadout resolution"); the logger prefixes
that string with `Phase: ` when emitting.
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
