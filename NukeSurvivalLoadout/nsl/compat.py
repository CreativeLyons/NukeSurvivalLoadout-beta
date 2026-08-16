"""PySide2 / PySide6 compatibility shim - the single Qt import surface for NSL.

Import Qt through ``from nsl import compat``. Never ``import PySide2``
or ``import PySide6`` anywhere else in the codebase.

PySide6 is preferred (Nuke 16+) and PySide2 is the fallback (Nuke
13-15). The binding is resolved once at import, and ``PYSIDE_VERSION``
records which one won.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Tuple

__all__ = ["QtCore", "QtWidgets", "QtGui", "PYSIDE_VERSION", "run_modal"]


def _resolve_pyside() -> Tuple[ModuleType, ModuleType, ModuleType, int]:
    """Pick a PySide binding and return ``(QtCore, QtWidgets, QtGui, version)``.

    Raises ``ImportError`` when neither binding is importable.
    """
    errors = []
    for binding, version in (("PySide6", 6), ("PySide2", 2)):
        try:
            qt_core = importlib.import_module(f"{binding}.QtCore")
            qt_widgets = importlib.import_module(f"{binding}.QtWidgets")
            qt_gui = importlib.import_module(f"{binding}.QtGui")
        except ImportError as exc:
            errors.append(f"{binding}: {exc}")
            continue
        return qt_core, qt_widgets, qt_gui, version

    raise ImportError(
        "NSL could not import a PySide binding. Tried PySide6 and PySide2. "
        "Details: " + " | ".join(errors)
    )


QtCore, QtWidgets, QtGui, PYSIDE_VERSION = _resolve_pyside()


def run_modal(qt_object, *args):
    """Run a modal exec loop on ``qt_object`` across PySide2 and PySide6.

    PySide6 has ``exec()`` and PySide2 has only ``exec_()``. The probe is
    by attribute, not by a version table, so new bindings keep working.

    Any modal Qt object works. ``QMenu`` takes the optional global
    position, ``QDialog`` and ``QMessageBox`` take none.
    """
    exec_method = getattr(qt_object, "exec", None)
    if exec_method is None:
        exec_method = qt_object.exec_
    return exec_method(*args)
