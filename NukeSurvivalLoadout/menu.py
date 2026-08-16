"""NSL GUI registration entrypoint - Nuke's `menu.py` pass.

Nuke runs this file in the GUI pass only, so no GUI re-check is needed.
It registers one command in the ``Edit`` menu, bound to ``F11``, which
opens a single floating window rather than a docked Nuke pane.

NSL never runs a plugin's own menu.py. Once a plugin folder is on the
path, Nuke finds and runs it without help.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback

_NSL_INSTALL_ROOT = os.path.dirname(os.path.abspath(__file__))
if _NSL_INSTALL_ROOT not in sys.path:
    sys.path.insert(0, _NSL_INSTALL_ROOT)

import nuke  # noqa: F401 - Nuke injects this at runtime

from nsl.boot.version_gate import check_nuke_version
from nsl.compat import QtWidgets
from nsl.constants import loadouts_dir
from nsl.ui.panel import LoadoutPanel
from nsl.ui.registry_bootstrap import build_registry_for_panel


_MENU_PATH = "Nuke"
_EDIT_MENU = "Edit"
_COMMAND_LABEL = "Nuke Survival Loadout Panel"
_HOTKEY = "F11"
_WINDOW_TITLE = "Loadout Panel"

# True drops every NSL module and re-imports before showing, so source
# edits apply without a Nuke restart. For development only.
_RELOAD_ON_OPEN = False
_PACKAGE_PREFIXES = ("nsl",)

# Kept between menu clicks so splitter sizes and the selected tab
# survive a close and reopen inside one Nuke session.
_panel_instance = None


class _LoadoutPanelHost(LoadoutPanel):
    """Floating LoadoutPanel host - builds the Registry at construct time.

    Subclassed so the Registry can be built before
    :meth:`LoadoutPanel.__init__` runs, which the first refresh needs.

    A bootstrap failure does not raise. The panel opens on defaults and
    the error string reaches degraded mode instead.
    """

    def __init__(self, parent=None):
        # Print the real traceback before re-raising. Nuke's menu
        # plumbing can otherwise mangle the frame.
        try:
            result = build_registry_for_panel(
                loadouts_dir=loadouts_dir(),
                parent_widget=parent,
            )
            super().__init__(parent=parent, registry=result.registry)
            self.setWindowTitle(_WINDOW_TITLE)
            # No dock host supplies a size, so set one. 1200x850 is the
            # panel's measured natural size.
            self.resize(1200, 850)
            # Stashed so the wiring can read the error without going back
            # to the settings file.
            self._bootstrap_error = result.error
        except BaseException:
            sys.stderr.write(
                "NSL PANEL INIT FAILED - full traceback follows:\n"
            )
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            raise

    def closeEvent(self, event):
        """Guard the floating-window close with the unsaved-changes prompt.

        The title-bar X and the Close button both land here, so they
        share one guard and the prompt fires once. The X cannot quietly
        drop unsaved Custom edits.

        App shutdown is exempt. Nuke's quit path cannot be intercepted
        reliably, so a prompt there would be unreliable.
        """
        # The Close button already ran the guard and set this flag, so
        # accept without asking again. Only the title-bar close reaches
        # the guard below.
        if getattr(self, "_nsl_close_confirmed", False):
            self._nsl_close_confirmed = False
            event.accept()
            return
        app = QtWidgets.QApplication.instance()
        if app is not None and app.closingDown():
            event.accept()
            return
        # Imported here so menu.py stays importable without Qt. The
        # events module pulls in the whole UI wiring layer.
        from nsl.ui.wiring import events as _events

        try:
            proceed = _events.should_close_panel(self)
        except BaseException:
            # Never trap the user inside the window on a guard failure -
            # surface the traceback and allow the close.
            sys.stderr.write(
                "NSL closeEvent guard failed - closing anyway:\n"
            )
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            proceed = True
        if proceed:
            event.accept()
        else:
            event.ignore()


def _show_loadout_panel():
    """Show the Loadout Panel as a non-modal floating window.

    Re-uses a live instance, so splitter sizes and the selected tab
    survive a close and reopen.
    """
    global _panel_instance
    try:
        if _panel_instance is None or not _panel_instance.isVisible():
            _panel_instance = _LoadoutPanelHost()
        _panel_instance.show()
        _panel_instance.raise_()
        _panel_instance.activateWindow()
    except BaseException:
        sys.stderr.write("NSL: failed to show Loadout Panel:\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise


def _reload_and_show() -> None:
    """Drop NSL modules, re-import fresh, and show the panel.

    Shows through the re-imported ``_show_loadout_panel``, so the reload
    path and the menu path stay the same. A failure part way through
    prints a traceback, and the next press retries.
    """
    global _panel_instance
    try:
        # Match top-level windows by title, so a stale _panel_instance
        # cannot leave an orphan window behind.
        try:
            from nsl.compat import QtWidgets

            app = QtWidgets.QApplication.instance()
            if app is not None:
                for widget in app.topLevelWidgets():
                    try:
                        if (widget.windowTitle() or "") == _WINDOW_TITLE:
                            widget.close()
                            widget.deleteLater()
                    except BaseException:
                        pass
        except BaseException:
            pass
        _panel_instance = None

        for mod_name in list(sys.modules):
            if mod_name.split(".")[0] in _PACKAGE_PREFIXES:
                del sys.modules[mod_name]

        spec = importlib.util.spec_from_file_location("_nsl_menu_reloaded", __file__)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not reload menu module from {__file__}")
        _fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_fresh)

        _fresh._show_loadout_panel()
    except BaseException:
        sys.stderr.write("NSL: reload-and-open failed:\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise


def _open_loadout_panel() -> None:
    """Menu command callback: reload-then-show, or plain show.

    Honours :data:`_RELOAD_ON_OPEN`. See its definition.
    """
    if _RELOAD_ON_OPEN:
        _reload_and_show()
    else:
        _show_loadout_panel()


def _register() -> None:
    if not check_nuke_version():
        return

    # Use the existing Edit menu, and create it only if a future Nuke
    # build leaves it out.
    edit_menu = nuke.menu(_MENU_PATH).menu(_EDIT_MENU)
    if edit_menu is None:
        edit_menu = nuke.menu(_MENU_PATH).addMenu(_EDIT_MENU)
    edit_menu.addCommand(_COMMAND_LABEL, _open_loadout_panel, _HOTKEY)


_register()
