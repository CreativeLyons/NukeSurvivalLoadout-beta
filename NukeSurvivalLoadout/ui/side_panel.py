"""Side panel - the universal "more info" surface for the Loadout Panel.

Locked behaviours:

* Three tabs, left to right: **Summary**, **Menu**, **Info** (mirroring the
  pill-button order GUI / menu / info on each card). (The old **Log**
  tab was retired: the loadout chain no longer captures per-plugin
  diagnostics, so it had no live data source. ``show_log`` / ``log_view``
  remain defined but dormant: never wired or shown.)
* **Summary is the DEFAULT active tab on first open.** It is NEVER auto-activated by a
  pill-button click - the user switches to it manually.
* Clicking a pill's **info button** loads README into the Info tab AND
  activates that tab. Clicking a pill's **menu button** loads that Plugin's
  ``menu.py`` into the Menu tab AND activates that tab.
* Empty-state placeholders:

    - Info: *"Click the info button on a Plugin to view its README."*
    - Menu: *"Click the menu button on a Plugin to view its menu.py."*

* The Info tab carries a gutter header naming the targeted Plugin
  (``README: <Plugin>``) plus the Preview/Markdown toggle. The Menu tab
  carries a gutter header (``menu.py - <Plugin>``) above the code view. The
  Summary tab carries no header.
* Info renders Markdown via ``QTextBrowser.setMarkdown()`` (Nuke 16 ships PySide6
  6.5.3, Qt 6.5 - ``setMarkdown`` is present). Menu renders raw ``menu.py``
  source as plain text with a Monokai Python ``QSyntaxHighlighter`` attached.

All Qt access goes through :mod:`NukeSurvivalLoadout.compat` - never import PySide2 / PySide6 directly.
No ``import nuke``. This module never edits ``NukeSurvivalLoadout/ui/__init__.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# Tab index constants - also part of the public API for callers wanting to assert /
# drive the active tab without depending on the QTabWidget's internal ordering.
TAB_SUMMARY = 0
# Tab order mirrors the pill-button order on each card (GUI / menu / info):
# menu.py sits in the middle, Info on the right end.
TAB_MENU = 1
TAB_INFO = 2
# Dormant alias: the old "Log" tab was retired (the chain no longer captures
# per-plugin diagnostics, so the tab had no live data source).
# ``show_log`` / ``log_view`` remain defined but are never wired or shown.
# Kept equal to ``TAB_MENU`` so any stale caller lands on a valid tab rather
# than out of range.
TAB_LOG = TAB_MENU

# Placeholder strings.
PLACEHOLDER_INFO = "Click the info button on a Plugin to view its README."
PLACEHOLDER_MENU = "Click the menu button on a Plugin to view its menu.py."
# Dormant - retired with the Log tab (see ``TAB_LOG`` above).
PLACEHOLDER_LOG = "Click the diagnostic button on a Plugin to view its log."

# Default Summary content shown before any session-level aggregate is supplied.
DEFAULT_SUMMARY_TEXT = (
    "Session load status will appear here once the panel is populated."
)


# Markdown preview block-spacing.
# Qt's QTextDocument applies near-zero default margins to headings,
# paragraphs and list items rendered from ``setMarkdown()``; blank lines
# in the source vanish and the rendered output reads as one continuous
# wall of text. ``document().setDefaultStyleSheet(css)`` cannot fix this:
# Qt's defaultStyleSheet only applies to HTML content set via ``setHtml()``,
# and ``setMarkdown`` is built by an internal parser that writes
# QTextBlockFormat values directly, bypassing the stylesheet entirely.
#
# The fix lives in ``SidePanel._apply_markdown_block_spacing``:
# walk every block in the document post-parse and rewrite each block's
# ``topMargin`` / ``bottomMargin`` based on ``headingLevel()`` and list
# membership. Adjust the values in that method if a future README needs
# tighter/airier rhythm.


# ---------------------------------------------------------------------------
# Pure helpers (no Qt) - usable without a PySide install.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginDetail:
    """The data a caller hands to :meth:`SidePanel.show_info` / ``show_log``.

    Callers are responsible for composing the right provenance-line variant;
    this widget just renders whatever string it is given.
    """

    plugin_name: str
    provenance: str
    body: str  # README markdown source for Info; traceback / diagnostic text for Log.
    source_path: Optional[str] = None  # absolute path to the on-disk file the
                                       # body came from (the Menu tab's
                                       # ``menu.py``), so the Menu tab's "Open"
                                       # button can open it in the OS default
                                       # editor. ``None`` for Info/Log and when
                                       # no file exists.


def info_tab_header(plugin_name: str) -> str:
    """Header line for the Info tab - verbatim ``README: <PluginName>`` form."""
    return f"README: {plugin_name}"


def log_tab_header(plugin_name: str) -> str:
    """Header line for the (dormant) Log tab - analogous to the Info form."""
    return f"Log: {plugin_name}"


def menu_tab_header(plugin_name: str) -> str:
    """Header line for the Menu tab - names the Plugin whose ``menu.py``
    is shown. Mirrors the Info gutter caption form."""
    return f"menu.py - {plugin_name}"


# ---------------------------------------------------------------------------
# Widget - Qt construction is deferred to call time so the module itself can be
# imported without PySide present (mirrors the pattern used by other NSL UI
# modules that need to stay headless-importable on the build host).
# ---------------------------------------------------------------------------


class SidePanel:
    """Three-tab side panel (Summary / Info / Log).

    Construction defers all Qt work until ``__init__`` runs so the *module* can
    be imported on machines without PySide. Once instantiated, the panel owns
    a :class:`QTabWidget` with three sub-pages - each a :class:`QTextBrowser`
    so README markdown, monospace tracebacks, and the session summary can all
    flow through the same rendering surface.

    Parameters
    ----------
    parent:
        Optional Qt parent. None when used standalone outside the panel.

    Notes
    -----
    The widget exposes :attr:`tabs` (the :class:`QTabWidget`) and three content
    browsers (``summary_view``, ``info_view``, ``log_view``) so callers can do
    direct content-tree introspection.
    """

    def __init__(self, parent=None):  # type: ignore[no-untyped-def]
        # Imported lazily so the module loads without PySide.
        from NukeSurvivalLoadout import compat

        QtWidgets = compat.QtWidgets
        QtGui = compat.QtGui
        QtCore = compat.QtCore

        # Top-level container - the side panel as a whole. Holds a QTabWidget
        # spanning its full area. Callers read the tabs widget directly via
        # ``self.tabs``.
        self.widget = QtWidgets.QWidget(parent)
        # Fill the side panel's outer widget with the recessed gutter
        # colour (`#222222`) so it shows through the QTabWidget's
        # transparent tab-row area - the space to the right of the last
        # tab where QTabBar does not extend. The pane below the tabs is
        # opaque #262626 so this fill only affects the tab-row strip.
        # setPalette + autoFillBackground (NOT setStyleSheet) so we
        # don't pollute child rendering through the QSS cascade - see
        # HybridTextButton's history for the prior incident this guards
        # against.
        self.widget.setAutoFillBackground(True)
        side_palette = self.widget.palette()
        side_palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#222222"))
        self.widget.setPalette(side_palette)
        layout = QtWidgets.QVBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QtWidgets.QTabWidget(self.widget)
        # Nuke-style tab chrome: flat rectangular tabs sized to content,
        # active tab carries the signature yellow-orange underline + text
        # tint, inactive tabs are muted on a slightly-darker bar than the
        # panel body. Hairline separators (1 px shadow) between adjacent
        # tabs match Nuke's panel divider style. Stylesheet is scoped to
        # the NSL_SidePanelTabs objectName so it does NOT leak.
        self.tabs.setObjectName("NSL_SidePanelTabs")
        if hasattr(self.tabs, "tabBar"):
            self.tabs.tabBar().setExpanding(False)
            # Pointing-hand cursor across the Summary / Info / Log tab
            # bar so clicking between tabs reads as the same interactive
            # vocabulary as the rest of the panel's affordances. Set on
            # the QTabBar (not the QTabWidget) because hover lives on
            # the bar; the QTabWidget's body is the pane content.
            self.tabs.tabBar().setCursor(QtCore.Qt.PointingHandCursor)
        # Per NSL_Design_System_New (comp-tabs canonical recipe): tabs read
        # as raised discrete cells over a darker gutter, with the active
        # cell brighter still and carrying a 2px accent underline.
        #
        # Tokens (from colors_and_type.css / preview/comp-tabs.html):
        #   gutter             #2a2a2a  + 1px #1f1f1f bottom hairline
        #   tab inactive fill  #2f2f2f  + 1px #4a4a4a outline (T/L/R)
        #   tab active fill    #424242  + 1px #5e5e5e outline (T/L/R)
        #   pane (body bg)     #262626  (surface-base - recessed body)
        #   active accent      #ee9626  (2px underline + white text)
        #
        # Qt limitations vs canonical:
        #   * box-shadow inset (active highlight) - unsupported; brighter
        #     top border #5e5e5e carries the raised-cell read instead.
        #   * ::after overlay underline at bottom:-1px - unsupported;
        #     border-bottom on :selected is the in-engine equivalent.
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

        # --- Cmd+C / Cmd+A inside the three QTextBrowser views -------------
        # Nuke installs Cmd+C and Cmd+A as ApplicationShortcuts targeting the
        # DAG's node-copy / "Select All Nodes" actions. A plain
        # ``QShortcut(QKeySequence.Copy, view)`` with ``WidgetShortcut`` context
        # does not win inside Nuke (the ApplicationShortcut still fires, since
        # Nuke's DAG widget keeps keyboard focus and the docked panel's focus
        # chain doesn't transfer cleanly to the ``QTextBrowser`` on click). The
        # robust fix is an event filter: catch ``QEvent.ShortcutOverride`` for
        # the affected sequences and ``accept()`` them. Accepting
        # ShortcutOverride tells Qt "don't route this as a shortcut: deliver the
        # keyPress to the focused widget instead." ``QTextBrowser``'s built-in
        # keyPressEvent handles both Copy and SelectAll natively (it's a
        # read-only QTextEdit), so the selection / clipboard work happens
        # without us calling any QTextEdit method ourselves.
        #
        # Right-click → Copy / Select All is unaffected (context-menu path
        # was never intercepted). This filter is purely for the keyboard
        # path. The filter is parented to the view so its lifetime matches.
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
            # Keep a reference on the view itself so Python doesn't
            # garbage-collect the filter object out from under Qt.
            view._nsl_text_shortcuts = _TextShortcutOverride(view)
            view.installEventFilter(view._nsl_text_shortcuts)

        # --- Summary ---------------------------------------------------------
        # The session-wide Load Status surface. The full grouped layout (counts
        # line + Failed / Missing / Pending lists with click-through) is a later
        # phase; this widget simply renders whatever text/HTML is fed to it via
        # ``set_summary``.
        self.summary_view = QtWidgets.QTextBrowser(self.widget)
        self.summary_view.setOpenExternalLinks(True)
        # Summary line spacing. The Summary is rendered via setHtml, which
        # (unlike setMarkdown - see the block-spacing note above) DOES honour
        # the document's default stylesheet. Qt's default is line-height 100%
        # with tight list margins, which reads cramped; bump the leading to
        # give the plugin list a bit more breathing room.
        # Guarded because some document() implementations may not accept
        # setDefaultStyleSheet.
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
        # Markdown surface for the Plugin's README.md, rendered via
        # QTextBrowser.setMarkdown.
        self.info_view = QtWidgets.QTextBrowser(self.widget)
        self.info_view.setOpenExternalLinks(True)
        self._set_text(self.info_view, PLACEHOLDER_INFO)
        _install_text_shortcuts(self.info_view)
        self.tabs.addTab(self.info_view, "Info")

        # --- Log (DORMANT) ---------------------------------------------------
        # The Log tab was retired: the runnable-python-loadout chain no longer
        # captures per-plugin diagnostics, so the tab had no live data source
        # (the diag chip that drove it never lit in production).
        # ``log_view`` / ``show_log`` / ``clear_log`` are kept
        # defined so any stale caller (e.g. a reload re-emit) degrades to a
        # no-op rather than an AttributeError - but the view is NOT added as a
        # tab and nothing wires it. Replaced in the tab bar by the Menu tab.
        self.log_view = QtWidgets.QTextBrowser(self.widget)
        self.log_view.setOpenExternalLinks(True)
        _install_text_shortcuts(self.log_view)
        mono = QtGui.QFont("Menlo")
        mono.setStyleHint(QtGui.QFont.StyleHint.TypeWriter) if hasattr(
            QtGui.QFont, "StyleHint"
        ) else mono.setStyleHint(QtGui.QFont.TypeWriter)
        self.log_view.setFont(mono)
        self._set_text(self.log_view, PLACEHOLDER_LOG)
        # CRITICAL: this view is parented to the side-panel container but NEVER
        # added to a tab/layout. Without an explicit hide() it renders as an
        # orphan child at (0,0), floating over the tab bar and covering the
        # Summary tab. Hide it so it stays a dormant, non-painting widget that
        # show_log() can still target if some stale caller ever fires.
        self.log_view.hide()

        # --- Menu ------------------------------------------------------------
        # Shows a Plugin's ``menu.py`` so artists can spot hotkey / menu
        # assignments and manage them from one place. Monokai (Sublime-style)
        # Python syntax highlighting via ``python_highlight``. v1 is
        # display-only; editing / saving is a later phase. A thin gutter
        # header names the Plugin (the header can't live inside the
        # highlighted document, which holds raw ``menu.py`` source).
        self.menu_container = QtWidgets.QWidget(self.widget)
        _menu_layout = QtWidgets.QVBoxLayout(self.menu_container)
        _menu_layout.setContentsMargins(0, 0, 0, 0)
        _menu_layout.setSpacing(0)

        # Header strip - plugin caption on the LEFT, an "Open" action button on
        # the RIGHT. Mirrors the Info tab's gutter (header label + Preview/
        # Markdown toggle). The Open button launches the on-disk menu.py in the
        # OS default text editor.
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

        # Styled to match the Info tab's small toggle buttons (same palette /
        # sizing), minus the checkable state since Open is a one-shot action.
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

        # Read-only, line-numbered Python code view (QPlainTextEdit-based, with
        # a left line-number gutter). Built lazily; falls back to a plain
        # QTextBrowser if the Qt binding can't build it (e.g. a headless
        # environment) so panel construction never fails - the fallback simply
        # has no gutter and no highlighting.
        self.menu_view = None
        try:
            from NukeSurvivalLoadout.ui.python_highlight import make_code_view
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
        # Code canvas. We keep the Monokai *token* colours (set by the
        # highlighter) but deliberately do NOT use Monokai's olive-tinted
        # background (#272822): it clashed with the panel's neutral greys.
        # The canvas is a dark, unsaturated grey matching the
        # panel chrome (#222222, same as the Menu gutter header) so the code
        # area reads as part of the panel. ``#NSL_MenuView`` (objectName-only
        # selector) so it applies whether the view is a QPlainTextEdit or the
        # QTextBrowser fallback, without cascading into sibling views.
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
        # Attach the Monokai Python highlighter to the view's document. Stored
        # on ``self`` so Python doesn't GC the highlighter out from under Qt.
        # Guarded: a binding without QSyntaxHighlighter (e.g. a headless
        # environment) must still construct the panel - it just shows the
        # menu.py source uncoloured.
        self._menu_highlighter = None
        try:
            from NukeSurvivalLoadout.ui.python_highlight import (
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
        # Insert at TAB_MENU (index 1) rather than append: the Menu page is
        # built after the Info page, but the tab order is Summary / menu.py /
        # Info, so this pushes Info from index 1 to index 2 (= TAB_INFO).
        self.tabs.insertTab(TAB_MENU, self.menu_container, "menu.py")

        # Track currently-targeted plugin per content-bearing tab. Summary is
        # session-wide and has no targeted Plugin. Set BEFORE wiring signals so
        # the currentChanged handler can safely read these attributes.
        self._info_plugin: Optional[PluginDetail] = None
        self._log_plugin: Optional[PluginDetail] = None  # dormant (Log retired)
        self._menu_plugin: Optional[PluginDetail] = None
        self._menu_source_path: Optional[str] = None  # on-disk menu.py for Open
        self._refresh_callback = None  # set by the panel; re-reads files
        self._info_mode: str = "preview"  # "preview" | "source"

        # --- Info-mode toggle (Preview / Markdown) ---------------------------
        # Tiny segmented two-button pill floating over the Info tab's text
        # area, pinned to the top-right corner. Visible only when the Info
        # tab is active AND has a loaded plugin. Flips the README between
        # rendered Markdown (Preview) and raw source (Markdown).
        #
        # Parented to ``info_view`` so it positions in viewport-local coords
        # and stays clear of the QTabWidget's tab bar entirely. The viewport
        # margin on ``info_view`` reserves top whitespace so README content
        # never scrolls underneath the floating toggle.
        self._info_toggle_widget = QtWidgets.QWidget(self.info_view)
        self._info_toggle_widget.setObjectName("NSL_InfoModeToggle")
        _toggle_layout = QtWidgets.QHBoxLayout(self._info_toggle_widget)
        _toggle_layout.setContentsMargins(0, 0, 0, 0)
        _toggle_layout.setSpacing(0)

        # Plugin-name header lives in the same gutter as the
        # Preview/Markdown toggle (``README: <Plugin>`` sits beside the
        # preview / markdown area, which would otherwise be blank).
        # Left-aligned label + stretch + right-aligned toggle buttons.
        # Replaces the old in-document `### README: <Plugin>` header +
        # italic provenance front matter so the README body now starts at
        # the very top of the rendered text. Also guards the rare case
        # where a README has no name line at the top: the gutter still
        # carries the Plugin identity.
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
        # Keep a handle so it isn't GC'd; the QButtonGroup is parented but
        # PySide can be opportunistic about ownership here.
        self._info_toggle_group = _toggle_group

        _toggle_layout.addWidget(self._info_preview_btn)
        _toggle_layout.addWidget(self._info_source_btn)

        # Tiny floating pill: small font, tight padding. Uses the same
        # active-tab palette so the active button reads as the same surface
        # as the active Side panel tab. Header label on the left of the
        # same strip - muted bold so it reads as a gutter caption,
        # not as part of the README body.
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

        # Reserve top whitespace in the Info text viewport so the floating
        # toggle never overlaps README content (the toggle sits inside this
        # reserved margin band).
        self.info_view.setViewportMargins(0, 28, 0, 0)
        # Start hidden; show_info / _on_tab_changed reveal it on demand.
        self._info_toggle_widget.hide()

        # Reposition toggle on info_view resize via a tiny event filter
        # (QWidget has no resize signal - only the resizeEvent virtual).
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

        # Visibility tracks the active tab + whether Info has a plugin loaded.
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # ⟳ Refresh - mounted in the tab bar's top-right corner so it's
        # visible across all tabs. Reuses the loadout strip's crisp painted
        # "revert" circular-arrow glyph button (a Unicode glyph rendered
        # unclear at this size). Clicking re-reads the README + menu.py for
        # the currently-shown plugins. Guarded: a headless environment can't
        # build the glyph button, so on any failure the corner button is
        # simply omitted and the panel still constructs.
        self.refresh_btn = None
        try:
            from NukeSurvivalLoadout.ui.loadout_strip import _GlyphIconButton

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

        # Summary is the DEFAULT active tab on first open.
        # Tab indices match TAB_SUMMARY / TAB_INFO / TAB_LOG.
        self.tabs.setCurrentIndex(TAB_SUMMARY)

        # Keep a Qt handle for sizing so standalone (parentless) use gets a
        # stable widget size regardless of layout hints.
        self.widget.resize(420, 520)

    # ------------------------------------------------------------------
    # Public API used by the parent panel and pill callbacks.
    # ------------------------------------------------------------------

    def show_info(self, detail: PluginDetail, *, activate: bool = True) -> None:
        """Populate the Info tab with ``detail``.

        Clicking a pill's info button loads the README and activates the Info
        tab. The Preview/Markdown toggle is reset to
        Preview on every new Info load - opening a different README always
        starts in rendered mode.

        ``activate=False`` is the refresh path: re-render the README in place
        WITHOUT switching to the Info tab and WITHOUT resetting the
        Preview/Markdown mode (so a refresh preserves whatever the user was
        viewing).
        """
        self._info_plugin = detail
        if activate:
            self._info_mode = "preview"
            self._info_preview_btn.setChecked(True)
        self._info_header_label.setText(info_tab_header(detail.plugin_name))
        self._render_info()
        if activate:
            self.tabs.setCurrentIndex(TAB_INFO)
            # Force toggle visibility in case the tab was already on Info (so
            # currentChanged doesn't fire to re-evaluate).
            self._on_tab_changed(self.tabs.currentIndex())

    def show_log(self, detail: PluginDetail) -> None:
        """Populate the Log tab with ``detail`` AND auto-switch to it.

        Tracebacks render as monospace text inside an HTML wrapper so the
        Plugin-name header + provenance line stay legible above the captured
        Python traceback.
        """
        self._log_plugin = detail
        # ``white-space: pre-wrap`` + ``word-wrap: break-word`` mirrors the
        # Info source-view pre style above - preserves significant whitespace
        # inside tracebacks while wrapping long file paths / no-space tokens
        # at the viewport edge. Bare ``<pre>`` defaults to ``white-space: pre``
        # (no wrap) which overflows the viewport and hides the right edge of
        # every long line.
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

        ``detail.body`` is the raw ``menu.py`` source (or the
        "No menu.py found…" message). It is rendered as plain text so the
        attached Monokai Python highlighter can colour it; the gutter header
        names the Plugin. Like the info button, clicking the menu button loads
        the file and activates the Menu tab.

        ``activate=False`` is the refresh path: re-render in place WITHOUT
        switching to the Menu tab.
        """
        self._menu_plugin = detail
        self._menu_source_path = detail.source_path
        self.menu_header_label.setText(menu_tab_header(detail.plugin_name))
        # Open is only actionable when there's a real file on disk to open
        # (disabled for the "no menu.py" / "plugin not found" cases).
        self.menu_open_btn.setEnabled(bool(detail.source_path))
        self.menu_header.show()
        # Plain text so the QSyntaxHighlighter owns all char formatting; any
        # prior HTML/markdown state on the document is discarded.
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
        """Open the currently-shown ``menu.py`` in the OS default text editor.

        No-op when there is no on-disk file (the button is disabled in that
        case anyway). Never raises - a failed launch must not surface as an
        unhandled exception out of the click handler.
        """
        _open_path_in_editor(self._menu_source_path)

    def set_refresh_callback(self, callback) -> None:
        """Install the callable the ⟳ refresh button invokes.

        The panel wires this to the registry's ``on_side_panel_refresh``,
        which re-reads the README + menu.py for the currently-shown plugins
        and pushes fresh content back via ``show_info`` / ``show_menu``
        (``activate=False``). Left ``None`` in standalone use, where
        the button is a harmless no-op.
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
        """Update the Summary tab content. Does NOT change the active tab.

        Summary is never auto-activated. The caller can
        repaint the aggregate status as often as it likes without yanking the
        user away from whatever Plugin they were inspecting.
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
        """Stretch the Info gutter across the info_view width.

        The gutter now carries a left-aligned ``README: <Plugin>``
        header AND the right-aligned Preview/Markdown toggle. Span
        the full content width minus the side margins so the label
        anchors flush-left and the toggle anchors flush-right.
        """
        if not self._info_toggle_widget.isVisible():
            return
        hint = self._info_toggle_widget.sizeHint()
        margin = 6  # 4-base spacing scale; sits flush inside reserved viewport margin
        width = max(hint.width(), self.info_view.width() - 2 * margin)
        self._info_toggle_widget.resize(width, hint.height())
        self._info_toggle_widget.move(margin, margin)

    def _set_info_mode(self, mode: str) -> None:
        """Flip the Info tab between rendered Markdown ("preview") and raw
        Markdown source ("source"). No-op if there is no plugin loaded.
        """
        if mode not in ("preview", "source"):
            return
        self._info_mode = mode
        self._render_info()

    def _render_info(self) -> None:
        """Render the currently-loaded Info plugin per the active mode.

        The rendered body is the README's verbatim markdown with NO
        injected front matter. The plugin-name ``README: <Plugin>``
        caption lives in the gutter widget alongside the Preview/Markdown
        toggle. The provenance line (``from <folder>``) is dropped
        entirely: it was visual noise that duplicated info available
        elsewhere in the panel.
        """
        if self._info_plugin is None:
            return
        detail = self._info_plugin
        md = detail.body
        if self._info_mode == "preview" and hasattr(self.info_view, "setMarkdown"):
            # QTextBrowser.setMarkdown is present in Qt 5.14+ / 6.x. Nuke 16
            # ships PySide6 6.5.3 so this path is always live. After the
            # parser populates the document, walk each block and inject
            # margins - see ``_apply_markdown_block_spacing`` for why this
            # post-process is required instead of a CSS stylesheet.
            self.info_view.setMarkdown(md)
            self._apply_markdown_block_spacing()
        else:
            # Raw Markdown source view - verbatim what the README author wrote.
            #
            # Wrap in <pre> via setHtml rather than setPlainText: Qt's
            # QTextDocument retains char-format state from the prior
            # setMarkdown call (e.g. an inherited link colour from a
            # rendered Markdown link), and setPlainText alone does NOT
            # reset that state, so the raw view would inherit the blue
            # link tint. setHtml with an explicit <pre> style gives full
            # control over colour + monospace rendering and naturally
            # signals "this is raw source".
            self.info_view.setHtml(
                "<pre style='color:#c8c8c8; "
                "font-family: Menlo, Monaco, Consolas, monospace; "
                "font-size: 11px; white-space: pre-wrap; margin: 0;'>"
                f"{_html_escape(md)}</pre>"
            )

    def _apply_markdown_block_spacing(self) -> None:
        """Override per-block margins on ``info_view``'s document.

        Qt's Markdown parser builds the document with near-zero
        ``topMargin`` / ``bottomMargin`` on every QTextBlockFormat,
        collapsing the visual rhythm of the source. ``setDefaultStyleSheet``
        does not apply on this path (HTML-only). Walking the document and
        rewriting each block's format is the only reliable lever.

        Per-block margins (top / bottom px). These are sized so that
        heading->paragraph and paragraph->heading boundaries read as
        deliberate breathing room against the parser's near-zero baseline:

          * h1 - 18 / 10
          * h2 - 24 / 10
          * h3 - 18 / 8
          * h4+ - 12 / 6
          * list item - 4 / 4  (the QTextList itself supplies
                                indentation; per-item space is just
                                enough to read each line distinctly)
          * paragraph / fenced code - 12 / 10

        Qt does not collapse adjacent block margins, so values sum across
        the boundary (p bottom 10 + h2 top 24 -> 34 px between paragraph
        and next heading; h2 bottom 10 + p top 12 -> 22 px between
        heading and following paragraph).
        """
        # Qt symbols are imported via the compat module per the file's
        # convention (no module-level Qt import - keeps this module
        # importable headless). Re-import inside the method
        # rather than caching on ``self`` so reloads pick up any compat
        # shim changes.
        from NukeSurvivalLoadout import compat  # noqa: PLC0415

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
        """Set the visible text on a ``QTextBrowser`` - plain text path.

        Placeholders are pure prose; using ``setPlainText`` keeps backticks etc.
        verbatim so the placeholder wording reads identically in the UI.
        """
        view.setPlainText(text)


def _html_escape(text: str) -> str:
    """Escape ``&``, ``<``, ``>`` so tracebacks containing angle brackets render
    intact in the Log tab's HTML wrapper.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _open_path_in_editor(path: Optional[str]) -> bool:
    """Open *path* in the OS default text editor. Best-effort, never raises.

    Returns True if a launch was attempted, False if the path is missing /
    not a file. Per-OS behaviour:

    * macOS - ``open -t`` opens the user's default *text editor* (rather than
      whatever app ``.py`` is associated with, which might try to *run* it).
    * Windows - ``os.startfile`` uses the default handler for ``.py``.
    * Linux - ``xdg-open`` uses the default handler.

    Uses the shell ``open``/``xdg-open`` tools rather than
    ``QDesktopServices`` so we can force the text-editor intent on macOS.
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
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False

