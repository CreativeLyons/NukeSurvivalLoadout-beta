"""Side panel - the "more info" surface for the Loadout Panel.

Locked behaviours:

* Three tabs, left to right: Summary, Menu, Info. The order mirrors the
  pill button order on each card.
* Summary is the default tab on first open. A pill click never activates
  it.
* A pill's info button loads the README into the Info tab and activates
  it. A pill's menu button does the same for ``menu.py`` and the Menu tab.
* The Log tab is retired. ``log_view`` is never added as a tab.

All Qt access goes through :mod:`nsl.compat`. Never import PySide2 or
PySide6 directly. No ``import nuke``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

TAB_SUMMARY = 0
TAB_MENU = 1
TAB_INFO = 2
# The Log tab is retired. ``TAB_LOG`` stays equal to ``TAB_MENU`` so a stale
# caller lands on a valid tab instead of out of range.
TAB_LOG = TAB_MENU

PLACEHOLDER_INFO = "Click the info button on a Plugin to view its README."
PLACEHOLDER_MENU = "Click the menu button on a Plugin to view its menu.py."
# Dormant. Retired with the Log tab.
PLACEHOLDER_LOG = "Click the diagnostic button on a Plugin to view its log."

DEFAULT_SUMMARY_TEXT = (
    "Session load status will appear here once the panel is populated."
)


# ---------------------------------------------------------------------------
# Pure helpers (no Qt) - usable without a PySide install.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginDetail:
    """The data a caller hands to :meth:`SidePanel.show_info` or ``show_menu``.

    ``provenance`` is composed by the caller. Only the dormant Log tab
    still renders it.
    """

    plugin_name: str
    provenance: str
    # README markdown for Info, ``menu.py`` source for Menu.
    body: str
    # Absolute path of the file ``body`` came from. The Menu tab's Open
    # button needs it. None for Info and Log, and when no file exists.
    source_path: Optional[str] = None


def info_tab_header(plugin_name: str) -> str:
    """Header line for the Info tab: ``README: <PluginName>``."""
    return f"README: {plugin_name}"


def log_tab_header(plugin_name: str) -> str:
    """Header line for the dormant Log tab."""
    return f"Log: {plugin_name}"


def menu_tab_header(plugin_name: str) -> str:
    """Header line for the Menu tab: ``menu.py - <PluginName>``."""
    return f"menu.py - {plugin_name}"


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class SidePanel:
    """Three-tab side panel: Summary, Menu, Info.

    Qt work happens in ``__init__``, so the module imports without PySide.
    Callers read :attr:`tabs`, ``summary_view``, ``info_view`` and
    ``menu_view`` directly. ``log_view`` exists but is dormant.
    """

    def __init__(self, parent=None):  # type: ignore[no-untyped-def]
        from nsl import compat

        QtWidgets = compat.QtWidgets
        QtGui = compat.QtGui
        QtCore = compat.QtCore

        self.widget = QtWidgets.QWidget(parent)
        # The tab-row area to the right of the last tab is transparent, so
        # this fill shows through there. Use setPalette, not setStyleSheet,
        # so the QSS cascade does not reach the children.
        self.widget.setAutoFillBackground(True)
        side_palette = self.widget.palette()
        side_palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#222222"))
        self.widget.setPalette(side_palette)
        layout = QtWidgets.QVBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QtWidgets.QTabWidget(self.widget)
        self.tabs.setObjectName("NSL_SidePanelTabs")
        if hasattr(self.tabs, "tabBar"):
            self.tabs.tabBar().setExpanding(False)
            # Set on the QTabBar, not the QTabWidget. Hover lives on the
            # bar and the QTabWidget's body is the pane content.
            self.tabs.tabBar().setCursor(QtCore.Qt.PointingHandCursor)
        # From the NSL_Design_System_New comp-tabs recipe. Two of its
        # effects have no Qt equivalent:
        #   * inset box-shadow  - the brighter #5e5e5e top border stands in.
        #   * ::after underline - border-bottom on :selected stands in.
        self.tabs.setStyleSheet(
            """
            /* QTabWidget itself paints the tab-row area to the RIGHT of
               the last tab - QTabBar does not extend past its tabs
               (expanding=False). Set both QTabWidget and QTabBar to the
               recessed gutter colour so the entire strip reads uniform
               and darker than the content pane (#262626). */
            QTabWidget#NSL_SidePanelTabs {
                background-color: #222222;
            }
            QTabWidget#NSL_SidePanelTabs::pane {
                border: none;
                /* Divider hairline lives on the pane's top edge so it
                   sits exactly at the boundary between the recessed
                   gutter (#222222) and the lighter content pane
                   (#262626). #4a4a4a matches the inactive tab cell
                   outline for vocabulary consistency. Put on the pane
                   (not the QTabBar's bottom) because the previous
                   `top: -1px` made the pane overlap the bar's border
                   and obscure it. */
                border-top: 1px solid #4a4a4a;
                background-color: #262626;
            }
            QTabWidget#NSL_SidePanelTabs QTabBar {
                background-color: #222222;
                qproperty-drawBase: 0;
            }
            QTabWidget#NSL_SidePanelTabs QTabBar::tab {
                background-color: #2f2f2f;
                color: #c8c8c8;
                font-weight: 700;
                font-size: 10pt;
                padding: 6px 18px 7px 18px;
                min-width: 60px;
                border-top: 1px solid #4a4a4a;
                border-left: 1px solid #4a4a4a;
                border-right: 1px solid #4a4a4a;
                border-bottom: 2px solid transparent;
                border-top-left-radius: 3px;
                border-top-right-radius: 3px;
            }
            QTabWidget#NSL_SidePanelTabs QTabBar::tab:!first {
                margin-left: -1px;
            }
            QTabWidget#NSL_SidePanelTabs QTabBar::tab:selected {
                background-color: #424242;
                color: #ffffff;
                border-top: 1px solid #5e5e5e;
                border-left: 1px solid #5e5e5e;
                border-right: 1px solid #5e5e5e;
                border-bottom: 2px solid #ee9626;
            }
            QTabWidget#NSL_SidePanelTabs QTabBar::tab:hover:!selected {
                color: #ffffff;
                background-color: #353535;
            }
            QTabWidget#NSL_SidePanelTabs QTextBrowser {
                /* Muted manilla-folder wash - desaturated tan derived
                   from the Nuke-orange ↔ manilla midpoint, then pulled
                   toward neutral. Dark text keeps selection legible. */
                selection-background-color: #c9a373;
                selection-color: #1a1a1a;
            }
            """
        )
        layout.addWidget(self.tabs)

        # --- Cmd+C / Cmd+A inside the QTextBrowser views -------------------

        # Nuke installs both as ApplicationShortcuts for the DAG, and a
        # QShortcut with WidgetShortcut context does not beat them. Accepting
        # QEvent.ShortcutOverride sends the key press to the focused widget.
        class _TextShortcutOverride(QtCore.QObject):
            def eventFilter(self, _obj, event):
                if event.type() == QtCore.QEvent.ShortcutOverride:
                    if event.matches(QtGui.QKeySequence.Copy):
                        event.accept()
                        return True
                    if event.matches(QtGui.QKeySequence.SelectAll):
                        event.accept()
                        return True
                return False

        def _install_text_shortcuts(view):
            # Hold a reference so Python does not garbage-collect the filter
            # while Qt still uses it.
            view._nsl_text_shortcuts = _TextShortcutOverride(view)
            view.installEventFilter(view._nsl_text_shortcuts)

        # --- Summary ---------------------------------------------------------

        self.summary_view = QtWidgets.QTextBrowser(self.widget)
        self.summary_view.setOpenExternalLinks(True)
        # Summary is set with setHtml, which honours the document stylesheet.
        # setMarkdown does not, so the Info tab needs
        # ``_apply_markdown_block_spacing`` instead.
        try:
            self.summary_view.document().setDefaultStyleSheet(
                "p { line-height: 110%; }"
                "li { line-height: 110%; margin-bottom: 1px; }"
            )
        except Exception:
            pass
        self._set_text(self.summary_view, DEFAULT_SUMMARY_TEXT)
        _install_text_shortcuts(self.summary_view)
        self.tabs.addTab(self.summary_view, "Summary")

        # --- Info ------------------------------------------------------------

        self.info_view = QtWidgets.QTextBrowser(self.widget)
        self.info_view.setOpenExternalLinks(True)
        self._set_text(self.info_view, PLACEHOLDER_INFO)
        _install_text_shortcuts(self.info_view)
        self.tabs.addTab(self.info_view, "Info")

        # --- Log (DORMANT) ---------------------------------------------------

        # The chain captures no per-plugin diagnostics, so the Menu tab took
        # this one's place. ``log_view`` and its methods stay defined so a
        # stale caller degrades to a no-op, not an AttributeError.
        self.log_view = QtWidgets.QTextBrowser(self.widget)
        self.log_view.setOpenExternalLinks(True)
        _install_text_shortcuts(self.log_view)
        mono = QtGui.QFont("Menlo")
        mono.setStyleHint(QtGui.QFont.StyleHint.TypeWriter) if hasattr(
            QtGui.QFont, "StyleHint"
        ) else mono.setStyleHint(QtGui.QFont.TypeWriter)
        self.log_view.setFont(mono)
        self._set_text(self.log_view, PLACEHOLDER_LOG)
        # This view is parented but never added to a tab. Without hide() it
        # paints as an orphan child at (0,0), over the tab bar and covering
        # the Summary tab.
        self.log_view.hide()

        # --- Menu ------------------------------------------------------------

        self.menu_container = QtWidgets.QWidget(self.widget)
        _menu_layout = QtWidgets.QVBoxLayout(self.menu_container)
        _menu_layout.setContentsMargins(0, 0, 0, 0)
        _menu_layout.setSpacing(0)

        # The header names the Plugin, because it cannot live inside the
        # highlighted document that holds the raw ``menu.py`` source.
        self.menu_header = QtWidgets.QWidget(self.menu_container)
        self.menu_header.setObjectName("NSL_MenuHeaderBar")
        _hdr_layout = QtWidgets.QHBoxLayout(self.menu_header)
        _hdr_layout.setContentsMargins(10, 5, 8, 5)
        _hdr_layout.setSpacing(8)

        self.menu_header_label = QtWidgets.QLabel("", self.menu_header)
        self.menu_header_label.setObjectName("NSL_MenuHeaderLabel")
        self.menu_header_label.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse
        )
        _hdr_layout.addWidget(self.menu_header_label)
        _hdr_layout.addStretch(1)

        self.menu_open_btn = QtWidgets.QPushButton("Open", self.menu_header)
        self.menu_open_btn.setObjectName("NSL_MenuOpenBtn")
        self.menu_open_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.menu_open_btn.setToolTip(
            "Open this menu.py in your system's default text editor"
        )
        self.menu_open_btn.clicked.connect(self._on_menu_open_clicked)
        _hdr_layout.addWidget(self.menu_open_btn)

        self.menu_header.setStyleSheet(
            """
            QWidget#NSL_MenuHeaderBar {
                background-color: #222222;
                border-bottom: 1px solid #4a4a4a;
            }
            QLabel#NSL_MenuHeaderLabel {
                color: #b8b8b8;
                font-weight: 700;
                font-size: 10px;
                background: transparent;
                border: none;
            }
            QPushButton#NSL_MenuOpenBtn {
                background-color: #2f2f2f;
                color: #c8c8c8;
                font-weight: 700;
                font-size: 10px;
                padding: 2px 10px 2px 10px;
                border: 1px solid #4a4a4a;
                border-radius: 3px;
            }
            QPushButton#NSL_MenuOpenBtn:hover {
                color: #ffffff;
                background-color: #353535;
            }
            QPushButton#NSL_MenuOpenBtn:disabled {
                color: #6e6e6e;
                background-color: #2a2a2a;
                border: 1px solid #3a3a3a;
            }
            """
        )
        self.menu_header.hide()  # only shown once a menu is loaded

        # Read-only Python code view with a line-number gutter. It falls back
        # to a plain QTextBrowser when the binding cannot build it, so
        # construction never fails. The fallback has no gutter and no colour.
        self.menu_view = None
        try:
            from nsl.ui.python_highlight import make_code_view
            self.menu_view = make_code_view(self.menu_container)
        except Exception:
            self.menu_view = None
        if self.menu_view is None:
            self.menu_view = QtWidgets.QTextBrowser(self.menu_container)
            if hasattr(self.menu_view, "setOpenExternalLinks"):
                self.menu_view.setOpenExternalLinks(True)
        self.menu_view.setObjectName("NSL_MenuView")
        _install_text_shortcuts(self.menu_view)
        menu_mono = QtGui.QFont("Menlo")
        menu_mono.setStyleHint(QtGui.QFont.StyleHint.TypeWriter) if hasattr(
            QtGui.QFont, "StyleHint"
        ) else menu_mono.setStyleHint(QtGui.QFont.TypeWriter)
        self.menu_view.setFont(menu_mono)
        # Keep the Monokai token colours, but not its olive background
        # (#272822), which clashed with the panel greys. The objectName-only
        # selector covers both the QPlainTextEdit and the fallback.
        self.menu_view.setStyleSheet(
            """
            #NSL_MenuView {
                background-color: #222222;
                color: #f8f8f2;
                selection-background-color: #3a3a3a;
                selection-color: #f8f8f2;
                border: none;
            }
            """
        )
        # Stored on ``self`` so Python does not GC the highlighter while Qt
        # uses it. Guarded: a binding without QSyntaxHighlighter still builds
        # the panel and shows the source uncoloured.
        self._menu_highlighter = None
        try:
            from nsl.ui.python_highlight import (
                attach_python_highlighter,
            )
            self._menu_highlighter = attach_python_highlighter(
                self.menu_view.document()
            )
        except Exception:
            self._menu_highlighter = None
        self._set_text(self.menu_view, PLACEHOLDER_MENU)

        _menu_layout.addWidget(self.menu_header)
        _menu_layout.addWidget(self.menu_view)
        # Insert, not append. The Menu page is built after Info, and this
        # pushes Info from index 1 to TAB_INFO.
        self.tabs.insertTab(TAB_MENU, self.menu_container, "menu.py")

        # Set before the signals are wired, so the currentChanged handler can
        # read them safely.
        self._info_plugin: Optional[PluginDetail] = None
        self._log_plugin: Optional[PluginDetail] = None  # dormant (Log retired)
        self._menu_plugin: Optional[PluginDetail] = None
        self._menu_source_path: Optional[str] = None  # on-disk menu.py for Open
        self._refresh_callback = None  # set by the panel, re-reads files
        self._info_mode: str = "preview"  # "preview" | "source"

        # --- Info-mode toggle (Preview / Markdown) ---------------------------

        # Parented to ``info_view`` so it positions in viewport-local coords
        # and stays clear of the tab bar. The viewport margin set below stops
        # README content scrolling under it.
        self._info_toggle_widget = QtWidgets.QWidget(self.info_view)
        self._info_toggle_widget.setObjectName("NSL_InfoModeToggle")
        _toggle_layout = QtWidgets.QHBoxLayout(self._info_toggle_widget)
        _toggle_layout.setContentsMargins(0, 0, 0, 0)
        _toggle_layout.setSpacing(0)

        # The ``README: <Plugin>`` caption lives here, not in the document.
        # The README body then starts at the top of the rendered text. It
        # also names the Plugin when the README has no title line.
        self._info_header_label = QtWidgets.QLabel("", self._info_toggle_widget)
        self._info_header_label.setObjectName("NSL_InfoHeaderLabel")
        self._info_header_label.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse
        )
        _toggle_layout.addWidget(self._info_header_label)
        _toggle_layout.addStretch(1)

        self._info_preview_btn = QtWidgets.QPushButton(
            "Preview", self._info_toggle_widget
        )
        self._info_preview_btn.setObjectName("NSL_InfoModeBtnPreview")
        self._info_preview_btn.setCheckable(True)
        self._info_preview_btn.setChecked(True)
        self._info_preview_btn.setCursor(QtCore.Qt.PointingHandCursor)

        self._info_source_btn = QtWidgets.QPushButton(
            "Markdown", self._info_toggle_widget
        )
        self._info_source_btn.setObjectName("NSL_InfoModeBtnSource")
        self._info_source_btn.setCheckable(True)
        self._info_source_btn.setCursor(QtCore.Qt.PointingHandCursor)

        _toggle_group = QtWidgets.QButtonGroup(self._info_toggle_widget)
        _toggle_group.setExclusive(True)
        _toggle_group.addButton(self._info_preview_btn)
        _toggle_group.addButton(self._info_source_btn)
        # Keep a handle. The QButtonGroup is parented, but PySide ownership
        # is not reliable here.
        self._info_toggle_group = _toggle_group

        _toggle_layout.addWidget(self._info_preview_btn)
        _toggle_layout.addWidget(self._info_source_btn)

        self._info_toggle_widget.setStyleSheet(
            """
            QWidget#NSL_InfoModeToggle { background: transparent; }
            QLabel#NSL_InfoHeaderLabel {
                color: #b8b8b8;
                font-weight: 700;
                font-size: 10px;
                padding: 2px 8px 2px 2px;
                background: transparent;
                border: none;
            }
            QWidget#NSL_InfoModeToggle QPushButton {
                background-color: #2f2f2f;
                color: #c8c8c8;
                font-weight: 700;
                font-size: 10px;
                padding: 2px 8px 2px 8px;
                border: 1px solid #4a4a4a;
            }
            QPushButton#NSL_InfoModeBtnPreview {
                border-top-left-radius: 3px;
                border-bottom-left-radius: 3px;
                border-right: none;
            }
            QPushButton#NSL_InfoModeBtnSource {
                border-top-right-radius: 3px;
                border-bottom-right-radius: 3px;
            }
            QWidget#NSL_InfoModeToggle QPushButton:hover:!checked {
                color: #ffffff;
                background-color: #353535;
            }
            QWidget#NSL_InfoModeToggle QPushButton:checked {
                background-color: #424242;
                color: #ffffff;
                border: 1px solid #5e5e5e;
            }
            QPushButton#NSL_InfoModeBtnPreview:checked {
                border-right: 1px solid #5e5e5e;
            }
            """
        )

        self._info_preview_btn.clicked.connect(
            lambda: self._set_info_mode("preview")
        )
        self._info_source_btn.clicked.connect(
            lambda: self._set_info_mode("source")
        )

        self.info_view.setViewportMargins(0, 28, 0, 0)
        # Start hidden. show_info and _on_tab_changed reveal it.
        self._info_toggle_widget.hide()

        # QWidget has no resize signal, only the resizeEvent virtual, so the
        # toggle is repositioned from an event filter.
        class _ResizeForwarder(QtCore.QObject):
            def __init__(self_filter, sidepanel, parent=None):
                super().__init__(parent)
                self_filter._sp = sidepanel

            def eventFilter(self_filter, obj, event):
                if event.type() == QtCore.QEvent.Type.Resize:
                    self_filter._sp._reposition_info_toggle()
                return False

        self._info_resize_filter = _ResizeForwarder(self, self.widget)
        self.info_view.installEventFilter(self._info_resize_filter)

        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Refresh sits in the tab bar corner, so it shows on every tab. It
        # reuses the loadout strip's painted "revert" glyph, because the
        # Unicode glyph renders unclear at this size.
        self.refresh_btn = None
        try:
            from nsl.ui.loadout_strip import _GlyphIconButton

            self.refresh_btn = _GlyphIconButton("revert", self.tabs)
            self.refresh_btn.setObjectName("NSL_SidePanelRefreshBtn")
            self.refresh_btn.setToolTip(
                "Refresh - re-read README and menu.py from disk"
            )
            self.refresh_btn.setFixedSize(QtCore.QSize(24, 24))
            self.refresh_btn.setIconSize(QtCore.QSize(14, 14))
            self.refresh_btn.setFocusPolicy(QtCore.Qt.NoFocus)
            self.refresh_btn.setFlat(True)
            self.refresh_btn.setStyleSheet(
                "QPushButton#NSL_SidePanelRefreshBtn {"
                "  background-color: rgba(255,255,255,0.02);"
                "  border: 1px solid #1f1f1f;"
                "  border-radius: 4px;"
                "  padding: 0px;"
                "  margin: 0px 8px 4px 0px;"
                "}"
                "QPushButton#NSL_SidePanelRefreshBtn:hover {"
                "  background-color: rgba(255,255,255,0.06);"
                "  border: 1px solid #2a2a2a;"
                "}"
                "QPushButton#NSL_SidePanelRefreshBtn:pressed {"
                "  background-color: rgba(0,0,0,0.20);"
                "}"
            )
            self.refresh_btn.clicked.connect(self._on_refresh_clicked)
            self.tabs.setCornerWidget(
                self.refresh_btn, QtCore.Qt.TopRightCorner
            )
        except Exception:
            self.refresh_btn = None

        self.tabs.setCurrentIndex(TAB_SUMMARY)

        # Give standalone, parentless use a stable size.
        self.widget.resize(420, 520)

    # ------------------------------------------------------------------
    # Public API used by the parent panel and pill callbacks.
    # ------------------------------------------------------------------

    def show_info(self, detail: PluginDetail, *, activate: bool = True) -> None:
        """Populate the Info tab with ``detail``.

        ``activate=True`` switches to the Info tab and resets the toggle to
        Preview, so every new README opens rendered. ``activate=False`` is
        the refresh path. It re-renders in place and keeps the current mode.
        """
        self._info_plugin = detail
        if activate:
            self._info_mode = "preview"
            self._info_preview_btn.setChecked(True)
        self._info_header_label.setText(info_tab_header(detail.plugin_name))
        self._render_info()
        if activate:
            self.tabs.setCurrentIndex(TAB_INFO)
            # currentChanged does not fire when Info is already active.
            self._on_tab_changed(self.tabs.currentIndex())

    def show_log(self, detail: PluginDetail) -> None:
        """Populate the dormant Log tab with ``detail``.

        ``log_view`` is hidden, so nothing appears. ``TAB_LOG`` equals
        ``TAB_MENU``, so the switch at the end lands on the Menu tab.
        """
        self._log_plugin = detail
        # ``pre-wrap`` keeps the whitespace inside a traceback and still
        # wraps long paths. A bare ``<pre>`` does not wrap, so it hides the
        # right edge of every long line.
        body_html = (
            f"<h3>{_html_escape(log_tab_header(detail.plugin_name))}</h3>"
            f"<p style='color:#888;font-size:smaller;margin-top:-6px;'>"
            f"{_html_escape(detail.provenance)}</p>"
            f"<pre style='font-family:Menlo,Monaco,Consolas,monospace; "
            f"white-space: pre-wrap; word-wrap: break-word; margin: 0;'>"
            f"{_html_escape(detail.body)}</pre>"
        )
        self.log_view.setHtml(body_html)
        self.tabs.setCurrentIndex(TAB_LOG)

    def show_menu(self, detail: PluginDetail, *, activate: bool = True) -> None:
        """Populate the Menu tab with a Plugin's ``menu.py``.

        ``detail.body`` is raw source, or the "no menu.py" message. It is set
        as plain text so the Monokai highlighter can colour it.
        ``activate=False`` is the refresh path and does not switch tab.
        """
        self._menu_plugin = detail
        self._menu_source_path = detail.source_path
        self.menu_header_label.setText(menu_tab_header(detail.plugin_name))
        self.menu_open_btn.setEnabled(bool(detail.source_path))
        self.menu_header.show()
        # Plain text, so the highlighter owns all character formatting and
        # any prior HTML state on the document is discarded.
        self.menu_view.setPlainText(detail.body)
        if activate:
            self.tabs.setCurrentIndex(TAB_MENU)

    def clear_menu(self) -> None:
        """Reset the Menu tab to its empty-state placeholder."""
        self._menu_plugin = None
        self._menu_source_path = None
        self.menu_header_label.setText("")
        self.menu_open_btn.setEnabled(False)
        self.menu_header.hide()
        self._set_text(self.menu_view, PLACEHOLDER_MENU)

    def _on_menu_open_clicked(self) -> None:
        """Open the current ``menu.py`` in the OS default text editor."""
        _open_path_in_editor(self._menu_source_path)

    def set_refresh_callback(self, callback) -> None:
        """Install the callable the refresh button invokes.

        The panel wires this to ``on_side_panel_refresh``, which re-reads the
        README and menu.py and calls back with ``activate=False``.
        """
        self._refresh_callback = callback

    def _on_refresh_clicked(self) -> None:
        """Invoke the installed refresh callback, if any. Never raises."""
        cb = self._refresh_callback
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass

    def set_summary(self, text_or_html: str, *, html: bool = False) -> None:
        """Update the Summary tab content. Does not change the active tab.

        Summary is never auto-activated, so the caller can repaint the
        aggregate status without moving the user off their Plugin.
        """
        if html:
            self.summary_view.setHtml(text_or_html)
        else:
            self._set_text(self.summary_view, text_or_html)

    def clear_info(self) -> None:
        """Reset the Info tab to its empty-state placeholder."""
        self._info_plugin = None
        self._info_header_label.setText("")
        self._set_text(self.info_view, PLACEHOLDER_INFO)
        self._info_toggle_widget.hide()

    def clear_log(self) -> None:
        """Reset the Log tab to its empty-state placeholder."""
        self._log_plugin = None
        self._set_text(self.log_view, PLACEHOLDER_LOG)

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        """Show the Preview/Markdown toggle only on the Info tab with content."""
        visible = (index == TAB_INFO) and self._info_plugin is not None
        if visible:
            self._info_toggle_widget.show()
            self._info_toggle_widget.raise_()
            self._reposition_info_toggle()
        else:
            self._info_toggle_widget.hide()

    def _reposition_info_toggle(self) -> None:
        """Stretch the Info gutter across the ``info_view`` width.

        It holds a left-aligned header and a right-aligned toggle.
        """
        if not self._info_toggle_widget.isVisible():
            return
        hint = self._info_toggle_widget.sizeHint()
        margin = 6  # sits inside the reserved viewport margin
        width = max(hint.width(), self.info_view.width() - 2 * margin)
        self._info_toggle_widget.resize(width, hint.height())
        self._info_toggle_widget.move(margin, margin)

    def _set_info_mode(self, mode: str) -> None:
        """Flip the Info tab between ``preview`` and ``source`` mode."""
        if mode not in ("preview", "source"):
            return
        self._info_mode = mode
        self._render_info()

    def _render_info(self) -> None:
        """Render the loaded Info plugin in the active mode.

        The README body is verbatim. The caption lives in the gutter widget
        and ``provenance`` is not shown at all.
        """
        if self._info_plugin is None:
            return
        detail = self._info_plugin
        md = detail.body
        if self._info_mode == "preview" and hasattr(self.info_view, "setMarkdown"):
            # setMarkdown exists in Qt 5.14+, so this path is always live in
            # Nuke. The parser leaves near-zero block margins, which
            # ``_apply_markdown_block_spacing`` then rewrites.
            self.info_view.setMarkdown(md)
            self._apply_markdown_block_spacing()
        else:
            # setHtml with an explicit <pre>, not setPlainText. The document
            # keeps character-format state from the previous setMarkdown
            # call, so plain text would inherit the blue link colour.
            self.info_view.setHtml(
                "<pre style='color:#c8c8c8; "
                "font-family: Menlo, Monaco, Consolas, monospace; "
                "font-size: 11px; white-space: pre-wrap; margin: 0;'>"
                f"{_html_escape(md)}</pre>"
            )

    def _apply_markdown_block_spacing(self) -> None:
        """Override per-block margins on ``info_view``'s document.

        Qt's Markdown parser sets near-zero margins on every block, and
        ``setDefaultStyleSheet`` does not apply here. Rewriting each block
        format is the only lever. Margins in px, top / bottom:

          * h1                - 18 / 10
          * h2                - 24 / 10
          * h3                - 18 / 8
          * h4+               - 12 / 6
          * list item         - 4 / 4
          * paragraph or code - 12 / 10

        Qt does not collapse adjacent margins, so the two values add across
        a block boundary.
        """
        # Re-imported here rather than cached on ``self``, so a reload picks
        # up compat shim changes.
        from nsl import compat  # noqa: PLC0415

        QtGui = compat.QtGui
        doc = self.info_view.document()
        cursor = QtGui.QTextCursor(doc)
        cursor.movePosition(QtGui.QTextCursor.Start)
        while True:
            block_format = cursor.blockFormat()
            level = block_format.headingLevel()
            in_list = cursor.currentList() is not None
            if level == 1:
                top, bottom = 18, 10
            elif level == 2:
                top, bottom = 24, 10
            elif level == 3:
                top, bottom = 18, 8
            elif level >= 4:
                top, bottom = 12, 6
            elif in_list:
                top, bottom = 4, 4
            else:
                top, bottom = 12, 10
            block_format.setTopMargin(top)
            block_format.setBottomMargin(bottom)
            cursor.setBlockFormat(block_format)
            if not cursor.movePosition(QtGui.QTextCursor.NextBlock):
                break

    @staticmethod
    def _set_text(view, text: str) -> None:  # type: ignore[no-untyped-def]
        """Set the visible text. Plain text keeps backticks verbatim."""
        view.setPlainText(text)


def _html_escape(text: str) -> str:
    """Escape ``&``, ``<`` and ``>`` for the HTML wrappers."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _open_path_in_editor(path: Optional[str]) -> bool:
    """Open *path* in the OS default text editor. Never raises.

    Returns True if a launch was attempted, False if *path* is not a file.

    * macOS   - ``open -t`` forces the text editor, not the ``.py`` file
                association, which might run the file.
    * Windows - the ``edit`` verb, falling back to Notepad. Never the
                default verb. For ``.py`` that is usually py.exe, which
                would execute the Plugin's script.
    * Linux   - ``xdg-open`` uses the default handler.

    The shell tools are used instead of ``QDesktopServices`` so the
    text-editor intent can be forced.
    """
    import os
    import subprocess
    import sys as _sys

    if not path or not os.path.isfile(path):
        return False
    try:
        if _sys.platform == "darwin":
            subprocess.Popen(["open", "-t", path])
        elif os.name == "nt":
            try:
                os.startfile(path, "edit")  # type: ignore[attr-defined]  # noqa: S606
            except OSError:
                # No ``edit`` verb registered for .py on this machine.
                subprocess.Popen(["notepad.exe", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False

