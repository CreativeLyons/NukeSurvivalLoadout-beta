"""NSL GUI registration entrypoint - Nuke's `menu.py` pass.

Nuke runs this file during the GUI `menu.py` pass, separately from
`init.py`. Headless invocations (`-t`/`-x`) skip this pass natively, so
no GUI re-check is needed here.

This module registers a single command ``Nuke Survival Loadout Panel`` in
Nuke's ``Edit`` menu (the same area as Preferences), bound to ``F11``.
Clicking the entry (or pressing F11) constructs (or re-shows) a singleton
:class:`_LoadoutPanelHost` widget as a **non-modal floating window** -
NOT a docked Nuke pane. The host wraps the production
:class:`NukeSurvivalLoadout.ui.panel.LoadoutPanel`, building the production
:class:`Registry` from disk + env state at construct time via
:func:`NukeSurvivalLoadout.ui.registry_bootstrap.build_registry_for_panel`.

NSL does not manually exec plugin menu.py files. Nuke's native NUKE_PATH
walker handles menu.py discovery the same way it handles init.py - once a
plugin folder is on the path (via the loadout chain's
``nsl_pluginAddPath`` helper), Nuke runs both init.py and menu.py without
NSL intervention.

Floating-vs-docked rationale: the Loadout Panel is a discovery + admin
surface, not a graph-editing or viewer-companion tool. Docking it would
compete with Nuke's compositing-workspace real estate; users open it,
toggle plugins, close it. A floating top-level window matches that
usage pattern and sidesteps the docked-panel registration plumbing
(``registerWidgetAsPanel`` / ``PyCustom_Knob`` / ``WidgetKnob``).
"""

from __future__ import annotations

import sys
import traceback

import nuke  # noqa: F401 - Nuke injects this at runtime

from NukeSurvivalLoadout.boot.version_gate import check_nuke_version
from NukeSurvivalLoadout.compat import QtWidgets
from NukeSurvivalLoadout.constants import loadouts_dir
from NukeSurvivalLoadout.ui.panel import LoadoutPanel
from NukeSurvivalLoadout.ui.registry_bootstrap import build_registry_for_panel


_MENU_PATH = "Nuke"
# Single command in Nuke's Edit menu (same area as Preferences), bound to F11.
_EDIT_MENU = "Edit"
_COMMAND_LABEL = "Nuke Survival Loadout Panel"
_HOTKEY = "F11"
_WINDOW_TITLE = "Loadout Panel"

# Open behaviour. False (shipped): plain open. True: drop all NSL modules and
# re-import before showing, so source edits take effect without a Nuke restart
# (dev only). Either way the panel rebuilds its Registry from disk on open, so
# the user always sees fresh on-disk state.
_RELOAD_ON_OPEN = False
_PACKAGE_PREFIXES = ("NukeSurvivalLoadout", "nsl")

# Singleton instance - survives menu re-invocations so the user's
# splitter sizes, side-panel tab selection, etc. persist across
# show/hide cycles within one Nuke session.
_panel_instance = None


class _LoadoutPanelHost(LoadoutPanel):
    """Floating LoadoutPanel host - builds the Registry at construct time.

    Subclasses :class:`LoadoutPanel` so the Registry can be built
    *before* :meth:`LoadoutPanel.__init__` runs (the panel needs the
    Registry attached at construct time so the initial
    refresh-from-registry works).

    Bootstrap failures (unreadable settings, malformed Global) do not
    raise - :func:`build_registry_for_panel` returns a
    :class:`BootstrapResult` carrying a first-run-defaults Registry plus
    a human-readable error string; the panel's degraded-mode wiring
    surfaces the error via ``panel.degraded`` if present.
    """

    def __init__(self, parent=None):
        # Diagnostic wrap - if construction fails, surface the real
        # traceback to stderr before re-raising. Otherwise Nuke's menu
        # plumbing can mangle the frame.
        try:
            result = build_registry_for_panel(
                loadouts_dir=loadouts_dir(),
                parent_widget=parent,
            )
            super().__init__(parent=parent, registry=result.registry)
            # Make this a top-level window - no parent dock.
            self.setWindowTitle(_WINDOW_TITLE)
            # Default geometry. The floating window owns its own initial
            # size (no dock host providing one). 1200×850 matches the
            # panel's measured natural widget dimensions.
            self.resize(1200, 850)
            # Bootstrap error surfacing - degraded panel mode handles
            # rendering. We stash the error on a public attribute so
            # downstream wiring can pick it up without re-reading
            # settings.
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

        Closing the panel should offer Save when the active Loadout is
        dirty. The window-manager close (title-bar X) routes here; the
        bottom-row Close button also routes here because it calls
        ``self.close()``.
        Both therefore share one guard (``should_close_panel``) and the
        prompt fires exactly once, so the title-bar X cannot silently
        discard unsaved Custom edits.

        App shutdown is deliberately exempt: Nuke's quit path can't be
        reliably intercepted (terminal close, crashes, force-quit), so
        when Qt is closing the application down we accept the close
        without prompting rather than show an unreliable quit prompt.
        """
        # The bottom-row Close button already ran the guard and set this
        # flag before calling ``close()`` - accept without re-prompting so
        # the button path never double-prompts. (Only the window-manager
        # title-bar close reaches the guard below.)
        if getattr(self, "_nsl_close_confirmed", False):
            self._nsl_close_confirmed = False
            event.accept()
            return
        app = QtWidgets.QApplication.instance()
        if app is not None and app.closingDown():
            event.accept()
            return
        # Import here (not at module load) so menu.py stays importable in
        # the no-Qt test stub path; events pulls in the UI wiring layer.
        from NukeSurvivalLoadout.ui.wiring import events as _events

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

    Re-uses an existing instance if one is alive (preserves splitter
    sizes / side-panel tab selection across show-hide cycles). Logs
    construction failures to stderr - the traceback wrap inside
    ``_LoadoutPanelHost.__init__`` handles the primary diagnostic.
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

    The live-iteration path: tears down any open panel, removes every
    ``NukeSurvivalLoadout.*`` / ``nsl.*`` module from ``sys.modules``,
    re-imports this module fresh, and shows via the re-imported
    ``_show_loadout_panel`` so the reload path and the menu path stay
    identical. Resilient: a mid-reload failure prints a full traceback and
    the next press retries from whatever state remains.
    """
    global _panel_instance
    try:
        # Close any live panel. Walk top-levels by title so a stale
        # _panel_instance reference cannot leave an orphan window behind.
        try:
            from NukeSurvivalLoadout.compat import QtWidgets

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

        import NukeSurvivalLoadout.menu as _fresh

        _fresh._show_loadout_panel()
    except BaseException:
        sys.stderr.write("NSL: reload-and-open failed:\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise


def _open_loadout_panel() -> None:
    """Menu command callback: reload-then-show, or plain show.

    Honours :data:`_RELOAD_ON_OPEN`; see its definition for the two
    behaviours.
    """
    if _RELOAD_ON_OPEN:
        _reload_and_show()
    else:
        _show_loadout_panel()


def _register() -> None:
    gate = check_nuke_version()
    if not getattr(gate, "accepted", bool(gate)):
        return

    # Single command in Nuke's Edit menu (callable form, Nuke 13+; no
    # string-eval round-trip). Find the existing Edit menu; fall back to
    # creating it if a future Nuke build omits it.
    edit_menu = nuke.menu(_MENU_PATH).menu(_EDIT_MENU)
    if edit_menu is None:
        edit_menu = nuke.menu(_MENU_PATH).addMenu(_EDIT_MENU)
    edit_menu.addCommand(_COMMAND_LABEL, _open_loadout_panel, _HOTKEY)


_register()
