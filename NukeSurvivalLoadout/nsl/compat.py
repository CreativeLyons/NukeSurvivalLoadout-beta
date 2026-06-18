"""PySide2 / PySide6 compatibility shim - the single Qt import surface for NSL.

Canonical import pattern (every NSL UI file uses exactly this - never
``import PySide2`` or ``import PySide6`` directly anywhere else in the
codebase)::

    from nsl import compat
    widget = compat.QtWidgets.QWidget()
    compat.QtCore.Qt.AlignCenter
    compat.QtGui.QColor(255, 0, 0)

Re-exported submodules:

    QtCore, QtWidgets, QtGui

The constant ``PYSIDE_VERSION`` is set to the integer ``6`` or ``2`` to record
which binding was resolved at import time, in case downstream code needs to
branch on a genuine API difference the shim cannot absorb.

Resolution rule:

* PySide6 is preferred when available (Nuke 16+).
* PySide2 is the fallback (Nuke 13-15).
* If neither binding is importable, this module raises ``ImportError`` with a
  message identifying both missing bindings. The shim does NOT swallow this
  with ``except Exception:`` - the boot sequence is responsible for wrapping
  its consumers; this module is the foundation, not a load path.

Resolution happens exactly once, at import time. There is no lazy lookup, no
per-call detection, and no runtime switching between bindings within a single
session.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Tuple

__all__ = ["QtCore", "QtWidgets", "QtGui", "PYSIDE_VERSION", "run_modal"]


def _resolve_pyside() -> Tuple[ModuleType, ModuleType, ModuleType, int]:
    """Pick a PySide binding and return ``(QtCore, QtWidgets, QtGui, version)``.

    Prefers PySide6 over PySide2. Raises ``ImportError`` if neither is
    importable.
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

    PySide6 exposes ``exec()``; PySide2 exposes only ``exec_()`` - verified
    empirically against the bindings Nuke actually bundles (PySide2 5.15.2.1
    in Nuke 14.1/15.2 has no ``exec`` alias). Probed by attribute, never by
    version table, so future bindings keep working without maintenance.

    Works for any modal-exec Qt object: ``QDialog`` / ``QMessageBox`` take
    no args, ``QMenu`` takes the optional global position.
    """
    exec_method = getattr(qt_object, "exec", None)
    if exec_method is None:
        exec_method = qt_object.exec_
    return exec_method(*args)
