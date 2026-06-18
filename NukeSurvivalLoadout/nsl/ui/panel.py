"""Top-level NSL Loadout Panel - composes the region widgets.

This module is the single composition surface for the panel. The
composition is:

* The top-of-panel toolbar (Undo / Redo / Reset panel), always visible.
* The change-detected banner (hidden by default - zero layout cost when
  hidden). Attached as an overlay pinned to the populated grid
  container's top edge so it never pushes the grid layout down and never
  appears over the empty state.
* The loadout selector strip (active Loadout + ops + panic), nested
  under the Plugins Folder card in the left column.
* The search-and-tags strip (search + chip filter + bulk-action buttons)
  and the plugins grid toolbar (bulk ops + sort), tied together into a
  single fixed-height "discovery block" so the two strips slide as one
  when the active divider above them is dragged.
* The pill grid region - a :class:`QStackedWidget` swapping between the
  populated :class:`PluginsGrid` (page 0) and the
  :class:`EmptyStateWidget` (page 1) based on folder count. The empty
  page paints the grid's ``#2d2d2d`` backdrop via ``setPalette`` so it
  reads as occupying the same recessed channel.
* Two active dividers - one horizontal (folder/side, 60/40 default with
  snap-back) and one vertical (pair ↔ grid, with the discovery block
  sliding rigidly in between). All other dividers are locked.

The default splitter sizes are captured at construction time and applied
by :meth:`reset_panel_layout` (driven by the Reset panel signal).

Conventions:

* Qt imports go only via :mod:`nsl.compat` - never
  ``import PySide2`` / ``import PySide6`` directly.
* Layout state - splitter positions, region collapsed states, side-panel
  proportions, the side panel's active tab - is per-session and not
  persisted.
* ``Reset panel`` touches splitter sizes and collapsed states only -
  not domain state, selection, sort, filter, Loadouts, or Plugins Folders.
* No ``import nuke`` / ``import nukescripts``. Nuke integration lives in
  top-level ``menu.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from nsl import compat

# Import the region widgets. They are part of the same
# nsl/ui package and carry no nuke dependency.
from nsl.ui._section import SectionBox
from nsl.ui._splitters import HANDLE_QSS, HairlineSplitter, maybe_snap_splitter
from nsl.ui.banner import Banner, BannerKind
from nsl.ui.empty_state import EmptyStateWidget
from nsl.ui.folder_card import FolderCard, FolderEntry, Health
from nsl.ui.grid import (
    CELL_DIFF_BG_LOAD_RGBA,
    CELL_DIFF_BG_UNLOAD_RGBA,
    CELL_DIFF_BG_GUI_ON_RGBA,
    CELL_DIVIDER_COLOUR,
    CELL_HEIGHT,
    GRID_BG_COLOUR,
    GRID_MARGIN,
    GRID_MARGIN_V,
    PluginsGrid,
    cell_widths,
    compute_columns,
)
from nsl.ui.grid_toolbar import GridCounterStrip, PluginsGridToolbar
# Canonical design-system GUI-only purple - shared with the "GUI:" counter
# chip and the per-pill GUI badge so the Summary tag agrees on tone.
from nsl.ui.grid_toolbar import _COUNTER_PURPLE as _GUI_PURPLE
# Pending-remove red - reused for the Summary's "- removed" tag so a plugin
# that loaded this session but is now gone reads in the SAME red as the "-N"
# pending-remove counter chip (they describe the same plugins).
from nsl.ui.grid_toolbar import _COUNTER_RED as _REMOVED_RED
from nsl.ui.loadout_strip import Loadout, LoadoutStrip
from nsl.ui.pill import Palette, PillState, PluginPill, Source, StatusIcon, Tint
from nsl.ui.search_tags import SearchTagsStrip
from nsl.ui.side_panel import SidePanel
from nsl.ui.state import (
    folder_list_from,
    loadout_list_from,
    pending_diff,
    pending_diff_split,
    pill_state_from,
)
from nsl.ui.top_toolbar import TopToolbar

if TYPE_CHECKING:
    from nsl.ui.registry import Registry

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# ---------------------------------------------------------------------------
# Default splitter geometry - captured at construction so Reset panel can
# restore it.
# ---------------------------------------------------------------------------

# Horizontal pair (folder/side): 60 / 40 default. This ratio keeps folder
# paths legible while the side panel still has room for the README.
# Snap-back tolerance lives at ±2.5 % around this split - see
# :func:`maybe_snap_splitter`.
_DEFAULT_FOLDER_SIDE_SPLIT = (60, 40)
_FOLDER_SIDE_SNAP_TOLERANCE = 0.025

# Bottom vertical splitter - three panes: (folder/side pair, discovery
# block, pill grid). The discovery block is fixed-height (sizeHint clamp),
# so only the first and third panes are elastic. Stretch factors (1, 0, 4)
# give the grid roughly 80 % of the elastic height. The integer tuple
# below is what gets passed back through ``setSizes`` on Reset panel - Qt
# re-normalises against actual widget width, so the absolute values just
# describe the proportion.
_DEFAULT_VERTICAL_SPLIT = (260, 110, 1040)


# ---------------------------------------------------------------------------
# Empty-state grid backdrop
# ---------------------------------------------------------------------------


class _EmptyStatePage(QtWidgets.QWidget):
    """Empty-state page that paints the same grid the populated grid uses.

    Without this, the empty area read as a flat dark void that didn't
    visually rhyme with the populated grid above/below. Painting the
    same ``CELL_DIVIDER_COLOUR`` lines at the same ``CELL_HEIGHT`` pitch
    + column boundaries (derived from the current width via the grid's
    own ``compute_columns`` / ``cell_widths``) tells the user "this is
    where your plugins will live" without competing with the welcome
    message overlaid on top.

    The page still owns its recessed ``#2d2d2d`` background via the
    autoFillBackground + palette pattern set by the panel - paintEvent
    runs after the native background fill, so the grid lines render on
    top of the recessed channel exactly the way the populated grid does.
    """

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        cols = compute_columns(w)
        cell_w = cell_widths(w, cols)

        painter = QtGui.QPainter(self)
        try:
            r, g, b = CELL_DIVIDER_COLOUR
            pen = QtGui.QPen(QtGui.QColor(r, g, b))
            pen.setWidth(1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setRenderHint(
                QtGui.QPainter.Antialiasing, False
            )

            # Horizontal lines at uniform CELL_HEIGHT intervals - same
            # algorithm the populated grid uses for its empty-cells
            # region past the last pill row.
            y = GRID_MARGIN_V + CELL_HEIGHT
            while y < h:
                painter.drawLine(0, y, w, y)
                y += CELL_HEIGHT

            # Vertical column boundaries - start at GRID_MARGIN +
            # cell_w (no line down the left edge); skip the right
            # edge (no line down the right edge); columns - 1 lines
            # total, matching the populated grid's _paint_grid_lines.
            grid_x0 = GRID_MARGIN
            for col_i in range(1, cols):
                x = grid_x0 + col_i * cell_w
                painter.drawLine(x, 0, x, h)
        finally:
            painter.end()


# ---------------------------------------------------------------------------
# LoadoutPanel - the top-level composition widget
# ---------------------------------------------------------------------------


class LoadoutPanel(QtWidgets.QWidget):
    """Top-level Loadout Panel.

    Composes the region widgets via nested :class:`QSplitter` instances
    so each region is resizable and collapsible. The widget exposes its
    region children as public attributes so the wiring helpers
    (``wire_<module>(panel)``) can reach them without poking at private
    state:

    * :attr:`top_toolbar` - :class:`TopToolbar`
    * :attr:`banner` - :class:`Banner` (hidden by default)
    * :attr:`loadout_strip` - :class:`LoadoutStrip`
    * :attr:`search_tags` - :class:`SearchTagsStrip`
    * :attr:`grid_toolbar` - :class:`PluginsGridToolbar`
    * :attr:`folder_card` - :class:`FolderCard`
    * :attr:`grid` - :class:`PluginsGrid`
    * :attr:`side_panel` - :class:`SidePanel`

    Splitter attributes (used by :meth:`reset_panel_layout`):

    * :attr:`_folder_side_split` - horizontal: folder/loadout left column
      ↔ side panel. The "pair". Snap-back wired to the 60 / 40
      default with ±2.5 % tolerance.
    * :attr:`_vertical_split` - vertical: pair ↔ discovery block ↔ pill
      grid (with the grid being a :class:`QStackedWidget` swap between
      populated grid + empty state). Two handles total - the active one
      above the discovery block (drag trades pair-height for grid-height)
      and the locked one below it.
    * :attr:`_left_col_split` - vertical, inside the folder/side pair's
      left column: Plugins Folder card on top, Loadout selector strip
      below. Locked divider; not part of the Reset panel surface.

    The default sizes the splitters were constructed with are captured at
    build time and re-applied by :meth:`reset_panel_layout` - this is the
    Reset panel button's effect. The reset touches splitter sizes and
    collapsed states **only**; it never affects domain state, selection,
    sort, filter, Loadouts, or Plugins Folders.
    """

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        grid_keys: Optional[list] = None,
        pill_factory=None,
        registry: Optional["Registry"] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NSLLoadoutPanel")

        # The Registry is the state-shape carrier every wiring helper
        # reads off. Production panels build one via
        # ``nsl.ui.registry_bootstrap.build_registry_for_panel`` and
        # pass it here; callers that leave it ``None`` get a soft wiring
        # mode that swallows registry-missing errors. See the
        # no-registry-vs-production branch in :meth:`_wire_signals`.
        self.registry = registry

        # Region widgets - instantiate before the layout build so the wiring
        # layer can grab references during construction if it needs to.
        self.top_toolbar = TopToolbar(self)
        self.banner = Banner(self)
        self.grid_counters = GridCounterStrip(self)
        self.loadout_strip = LoadoutStrip(self)
        self.search_tags = SearchTagsStrip(self)
        self.grid_toolbar = PluginsGridToolbar(self)
        self.folder_card = FolderCard(self)
        # Side panel is a non-QWidget wrapper exposing a ``.widget`` attribute
        # plus the QTabWidget on ``.tabs``. We embed ``.widget`` into the
        # layout; tests and wiring helpers reach the tab content via
        # ``panel.side_panel`` (the wrapper) directly.
        self.side_panel = SidePanel(self)

        # Plugins grid - caller may inject keys + factory. When a registry
        # is attached we install a registry-aware factory that calls
        # :func:`nsl.ui.state.pill_state_from` so
        # freshly-rebuilt pills carry the right state. Without a registry
        # we fall back to the placeholder factory.
        if pill_factory is None:
            if self.registry is not None:
                pill_factory = self._registry_pill_factory
            else:
                pill_factory = _default_pill_factory
        self._pill_factory = pill_factory
        self.grid = PluginsGrid(list(grid_keys or []), pill_factory, self)

        # Layout build
        self._build_main()

        # Capture default splitter sizes so Reset panel can restore them.
        #
        # Reset at the default layout must be a true no-op. Raw ratio math
        # against ``_DEFAULT_FOLDER_SIDE_SPLIT`` / ``_DEFAULT_VERTICAL_SPLIT``
        # can't reproduce Qt's actual construct-time layout, which is shaped
        # by stretch factors + fixed-height children + each pane's
        # ``minimumSizeHint`` - so replaying ratios on Reset visibly shifts
        # the panes.
        #
        # Instead: seed the cached sizes from the ratio tuples for off-screen
        # consumers that never get a ``showEvent``, then overwrite with
        # Qt's actual ``sizes()`` the first time the panel is shown via
        # :meth:`showEvent`. After that one-shot capture, Reset replays
        # exact integer sizes Qt already laid the panel out to - so Reset
        # at the default layout leaves the panel untouched.
        self._default_folder_side_sizes = list(_DEFAULT_FOLDER_SIDE_SPLIT)
        self._default_vertical_sizes = list(_DEFAULT_VERTICAL_SPLIT)
        self._default_sizes_captured = False
        self._default_side_panel_tab_index = self.side_panel.tabs.currentIndex()

        # Install the panel-side refresh callback before wiring runs so
        # any ``apply_op_result`` triggered during initial wiring re-emits
        # widget state correctly.
        if self.registry is not None:
            self.registry.attach_refresh(self.refresh_from_registry)
            self.registry.attach_parent_widget(self)
            # Side-panel ⟳ refresh button → re-read the README + menu.py for
            # the plugins the Info / Menu tabs currently show (picks up
            # external edits without a full plugin rescan).
            if hasattr(self.side_panel, "set_refresh_callback") and hasattr(
                self.registry, "on_side_panel_refresh"
            ):
                self.side_panel.set_refresh_callback(
                    self.registry.on_side_panel_refresh
                )

        # Wire intra-panel signals (Reset panel is the only one owned
        # here). The wiring helpers extend :meth:`_wire_signals`.
        self._wire_signals()

        # Initial refresh - populate widgets from the registry. The
        # no-registry path leaves the registry None and skips this.
        if self.registry is not None:
            self.refresh_from_registry()

    # ----- layout ----------------------------------------------------------

    def _build_main(self) -> None:
        """Construct the panel composition.

        Hierarchy:

        * Outer QVBoxLayout (8 px margins, 8 px spacing)
          - TopToolbar (always visible, never wrapped in SectionBox -
            chrome, not a content region)
          - ``_vertical_split`` - vertical :class:`HairlineSplitter`
            (the "bottom split"), three panes:
            * ``_folder_side_split`` - horizontal :class:`HairlineSplitter`
              ("pair"), two panes:
              - ``_left_col_split`` - vertical :class:`HairlineSplitter`,
                two panes: SectionBox(FolderCard), SectionBox(LoadoutStrip).
                Locked divider between them; LoadoutStrip non-collapsible.
              - SectionBox(SidePanel.widget). Active divider between left
                column and side panel; snap-back to 60 / 40 default.
            * SectionBox(discovery_block) - SearchTagsStrip stacked over
              PluginsGridToolbar in one fixed-height block; non-collapsible.
              Active divider above it trades pair-height for grid-height
              (the block slides as one).
            * SectionBox(grid_stack) - :class:`QStackedWidget` with two
              pages: grid_container (populated PluginsGrid + Banner
              overlay) and empty_state_page (#2d2d2d backdrop +
              EmptyStateWidget). Locked divider above (under discovery
              block).
        """
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # 1. Top toolbar - always visible, not wrapped in SectionBox.
        outer.addWidget(self.top_toolbar)

        # 2. Horizontal pair: folder/loadout left column ↔ side panel.
        self._folder_side_split = HairlineSplitter(QtCore.Qt.Horizontal, self)
        self._folder_side_split.setObjectName("NSLFolderSideSplit")
        self._folder_side_split.setHandleWidth(6)
        self._folder_side_split.setChildrenCollapsible(True)

        # Left column: folder card on top, loadout strip below. Locked
        # divider - neither side stretches or collapses meaningfully, so
        # the handle is disabled (still present for visual structure,
        # painted thinner + dimmer by HairlineHandle.paintEvent).
        self._left_col_split = HairlineSplitter(QtCore.Qt.Vertical, self)
        self._left_col_split.setObjectName("NSLLeftColSplit")
        self._left_col_split.setHandleWidth(6)
        self._left_col_split.setChildrenCollapsible(True)
        # FolderCard renders its own QFrame.StyledPanel by default; SectionBox
        # below provides the canonical bounding line so the built-in frame
        # would double-paint. Suppress it.
        self.folder_card.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._left_col_split.addWidget(SectionBox(self.folder_card, self))
        self._left_col_split.addWidget(SectionBox(self.loadout_strip, self))
        # FolderCard takes most of the left column height; LoadoutStrip
        # claims just enough for its rows.
        self._left_col_split.setStretchFactor(0, 1)
        self._left_col_split.setStretchFactor(1, 0)
        # LoadoutStrip non-collapsible - sinks to its minimumSizeHint
        # but never to zero.
        self._left_col_split.setCollapsible(1, False)
        # Lock the divider between FolderCard and LoadoutStrip - neither
        # side benefits from a drag.
        self._left_col_split.handle(1).setEnabled(False)
        self._left_col_split.handle(1).setCursor(QtCore.Qt.ArrowCursor)

        self._folder_side_split.addWidget(self._left_col_split)
        self._folder_side_split.addWidget(
            SectionBox(self.side_panel.widget, self)
        )
        self._folder_side_split.setStretchFactor(0, 1)
        self._folder_side_split.setStretchFactor(1, 1)
        self._folder_side_split.setSizes(list(_DEFAULT_FOLDER_SIDE_SPLIT))
        # Snap-back: when the user releases within ±2.5 % of the 60/40
        # default, the divider re-anchors. Outside that zone the drag is
        # free-form.
        self._folder_side_split._snap_ratio = tuple(_DEFAULT_FOLDER_SIDE_SPLIT)
        self._folder_side_split._snap_tolerance = _FOLDER_SIDE_SNAP_TOLERANCE
        self._folder_side_split.splitterMoved.connect(
            self._on_folder_side_moved
        )
        self._folder_side_split.splitterMoved.connect(
            lambda *_: maybe_snap_splitter(self._folder_side_split)
        )

        # 3. Discovery block - search/tags strip + grid toolbar (action
        # row) + grid counter strip as one static unit. Fixed height
        # (sizeHint clamp) so the splitter cannot squash or stretch it;
        # the three strips read as one continuous discovery / bulk-action
        # surface, with the counter strip immediately below the action
        # row surfacing ambient state.
        #
        # The counter strip lives here in the grid toolbar region (not
        # stacked under the banner): the banner must never overlay the
        # counters, so it gets its own row below rather than sharing a
        # ``QStackedLayout(StackAll)`` slot with the counter.
        discovery_block = QtWidgets.QWidget(self)
        discovery_block.setObjectName("NSLDiscoveryBlock")
        discovery_block.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        discovery_layout = QtWidgets.QVBoxLayout(discovery_block)
        discovery_layout.setContentsMargins(0, 0, 0, 0)
        discovery_layout.setSpacing(6)
        discovery_layout.addWidget(self.search_tags)
        discovery_layout.addWidget(self.grid_toolbar)

        # Counters + banner share one horizontal row. Counter chips on
        # the left at their natural width;
        # banner fills the right-hand remainder with horizontal stretch.
        # When the banner is hidden (no pending changes), its layout
        # space collapses - the chip row stays in place either way
        # because the chips themselves have no horizontal stretch.
        counters_row = QtWidgets.QWidget(self)
        counters_row.setObjectName("NSLCountersRow")
        counters_row.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        counters_row_layout = QtWidgets.QHBoxLayout(counters_row)
        counters_row_layout.setContentsMargins(0, 0, 0, 0)
        counters_row_layout.setSpacing(8)
        # Strip on the left at its natural width; banner takes the
        # remaining width to the right edge. All chips (including the
        # diff +N / −N) live inside the strip in display order, so
        # the diff chips paint with the same pill chrome as the rest.
        counters_row_layout.addWidget(self.grid_counters)
        counters_row_layout.addWidget(self.banner, 1)
        # Pin the banner to a fixed slim height so it matches the
        # group-divider gutter band and never grows the counter row.
        # Hard-cap at the gutter band height (the same band the sort
        # group dividers use) so the banner reads as one consistent
        # strip vocabulary rather than a chunky standalone row.
        from nsl.ui.grid import GROUP_DIVIDER_HEIGHT as _GUTTER_H
        _banner_h = min(_GUTTER_H, self.grid_counters.sizeHint().height())
        self.banner.setFixedHeight(_banner_h)
        discovery_layout.addWidget(counters_row)
        discovery_section = SectionBox(discovery_block, self)

        # 4. Grid stack - populated grid + empty state. The Banner is
        # parented to the populated branch (grid_container) so the
        # stack swap hides it naturally in empty mode.
        grid_container = QtWidgets.QWidget(self)
        grid_container.setObjectName("NSLGridContainer")
        gc_layout = QtWidgets.QVBoxLayout(grid_container)
        gc_layout.setContentsMargins(0, 0, 0, 0)
        gc_layout.setSpacing(0)
        gc_layout.addWidget(self.grid)
        self._grid_container = grid_container

        # Banner lives inline in the counters row above (to the right of
        # the action buttons), so the grid pane reclaims the strip that
        # would otherwise host a permanently-reserved banner slot.
        self.banner.hide()

        # Empty-state page - own backdrop painted at the grid bg colour
        # (#2d2d2d) via setPalette (cascade-safe; QSS on a parent would
        # pollute child native rendering, per the QSS-cascade lesson).
        # The EmptyStateWidget itself paints no background. The page is
        # an ``_EmptyStatePage`` so the empty area paints the same
        # cell-divider grid lines the populated grid uses.
        self._empty_state_page = _EmptyStatePage(self)
        self._empty_state_page.setObjectName("NSLEmptyStatePage")
        self._empty_state_page.setAutoFillBackground(True)
        empty_palette = self._empty_state_page.palette()
        empty_palette.setColor(
            QtGui.QPalette.Window, QtGui.QColor(*GRID_BG_COLOUR)
        )
        self._empty_state_page.setPalette(empty_palette)
        empty_layout = QtWidgets.QVBoxLayout(self._empty_state_page)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.setSpacing(0)
        self.empty_state = EmptyStateWidget(self._empty_state_page)
        empty_layout.addWidget(self.empty_state)

        self._grid_stack = QtWidgets.QStackedWidget(self)
        self._grid_stack.setObjectName("NSLGridStack")
        self._grid_stack.addWidget(grid_container)         # page 0 - populated
        self._grid_stack.addWidget(self._empty_state_page) # page 1 - empty

        # Slot + grid_stack share a single splitter pane so the slot's
        # fixed height never participates in splitter resize math. The
        # slot sits at the top of the pane; the grid stack fills the
        # remainder.
        grid_pane = QtWidgets.QWidget(self)
        grid_pane.setObjectName("NSLGridPane")
        grid_pane_layout = QtWidgets.QVBoxLayout(grid_pane)
        grid_pane_layout.setContentsMargins(0, 0, 0, 0)
        grid_pane_layout.setSpacing(0)
        grid_pane_layout.addWidget(self._grid_stack, 1)

        # 5. Bottom vertical splitter - pair / discovery block / grid pane.
        self._vertical_split = HairlineSplitter(QtCore.Qt.Vertical, self)
        self._vertical_split.setObjectName("NSLVerticalSplit")
        self._vertical_split.setHandleWidth(6)
        self._vertical_split.setChildrenCollapsible(True)
        # Lock discovery block to its sizeHint (QSizePolicy.Fixed alone is
        # not enough inside a QSplitter - Qt still hands the pane more
        # space than sizeHint when leftover height exists).
        discovery_section.adjustSize()
        discovery_section.setFixedHeight(discovery_section.sizeHint().height())
        self._vertical_split.addWidget(self._folder_side_split)
        self._vertical_split.addWidget(discovery_section)
        self._vertical_split.addWidget(SectionBox(grid_pane, self))
        self._vertical_split.setStretchFactor(0, 1)
        self._vertical_split.setStretchFactor(1, 0)
        self._vertical_split.setStretchFactor(2, 4)
        # Discovery block non-collapsible (fixed-purpose strip never
        # sinks below sizeHint). Pair + grid stay collapsible.
        self._vertical_split.setCollapsible(1, False)
        # Lock the divider below the discovery block (handle index 2):
        # the discovery block is fixed-height, so the only meaningful
        # active divider in this splitter is the one above it (handle 1).
        self._vertical_split.handle(2).setEnabled(False)
        self._vertical_split.handle(2).setCursor(QtCore.Qt.ArrowCursor)
        # NOTE: we deliberately do NOT call setSizes here. Stretch
        # factors + sizeHints give the pair its natural height (so the
        # folder card and loadout strip are fully readable on first
        # paint) and let the grid stretch into the leftover. An explicit
        # setSizes at init forces absolute proportions that squash the
        # pair at small panel heights. ``reset_panel_layout`` *does*
        # call setSizes with ``_DEFAULT_VERTICAL_SPLIT`` so the user has
        # a deterministic "restore" anchor.

        # Splitter handle QSS goes on each splitter instance, NOT on
        # ``self`` - root-level QSS pollutes descendant native paint
        # (HybridStyle / Fusion QPushButton chrome stops firing).
        self._folder_side_split.setStyleSheet(HANDLE_QSS)
        self._left_col_split.setStyleSheet(HANDLE_QSS)
        self._vertical_split.setStyleSheet(HANDLE_QSS)

        outer.addWidget(self._vertical_split, 1)

        # Floating Close button - direct child of the panel, no
        # parent layout. Anchored to the bottom-right corner via
        # :meth:`resizeEvent`. Zero structural footprint (doesn't eat
        # grid scroll height); reads as a panel-chrome action rather
        # than a layout-level row. It calls ``self.close()``, which routes
        # through ``_LoadoutPanelHost.closeEvent`` →
        # ``nsl.ui.wiring.events.should_close_panel`` - the
        # SAME guard the window-manager (title-bar) close uses. That guard
        # checks ``registry.is_active_dirty`` and routes through
        # ``confirm_close_with_unsaved_changes`` only when the active
        # Loadout has real edits.
        self.close_button = QtWidgets.QPushButton("Close", self)
        self.close_button.setObjectName("NSLCloseButton")
        self.close_button.setAutoDefault(False)
        self.close_button.setDefault(False)
        # Raise above the splitter children so it always renders on
        # top of whichever pane sits in the bottom-right slot.
        self.close_button.raise_()
        self._reposition_close_button()

        # Empty-state gating - wire after the stack exists. Folder count
        # drives which page is current.
        self.folder_card.remove_confirmed.connect(
            lambda *_: self._refresh_grid_stack()
        )
        # First-paint sync so the stack reflects whatever entries the
        # caller seeded on the folder card before _build_main ran.
        self._refresh_grid_stack()

        # Pin the 60/40 horizontal split until the user drags. Qt
        # re-normalises ``setSizes`` against child sizeHints, so the
        # construction-time call alone drifts.
        self._folder_side_user_dragged = False

    def _on_folder_side_moved(self, *args) -> None:
        """Mark the folder/side divider as user-controlled so subsequent
        panel resizes stop forcing the 60/40 default."""
        self._folder_side_user_dragged = True

    # ------------------------------------------------------------------
    # Floating Close button anchoring
    # ------------------------------------------------------------------

    def _reposition_close_button(self) -> None:
        """Anchor the floating Close button to the bottom-right corner.

        Called from :meth:`_build_main` on first paint and from
        :meth:`resizeEvent` on every resize. The button's natural
        ``sizeHint`` drives its width/height; we just translate it
        into the corner with an 8 px vertical margin (matches the
        panel's outer-layout margin so the button sits flush with
        the rest of the chrome) and a wider right margin so the
        button clears the populated PluginsGrid's vertical
        scrollbar. Positioning the button left of the scrollbar is the
        clean fix: shrinking the scrollbar (``setViewportMargins`` +
        parent layout bottom-padding) had no visual effect under Nuke's
        HybridStyle and left an ugly bottom gutter on the grid.
        """
        btn = getattr(self, "close_button", None)
        if btn is None:
            return
        hint = btn.sizeHint()
        bottom_margin = 8
        # Scrollbar ~16 px wide on macOS HybridStyle + ~8 px breathing
        # gap between scrollbar and button right edge = 24 px additional
        # right-side inset beyond the standard 8 px chrome margin.
        right_margin = 8 + 24
        x = self.width() - hint.width() - right_margin
        y = self.height() - hint.height() - bottom_margin
        btn.setGeometry(x, y, hint.width(), hint.height())

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        # Capture the actual splitter sizes Qt produced for the
        # construction-time layout on the *first* show. We can't read
        # ``splitter.sizes()`` during ``__init__`` because the widget has
        # no rendered geometry yet - Qt would return zeros or the raw
        # ``setSizes`` requests pre-normalisation. ``showEvent`` fires
        # after Qt's first layout pass, so ``sizes()`` returns real
        # pixel allocations.
        if not getattr(self, "_default_sizes_captured", False):
            try:
                folder_sizes = self._folder_side_split.sizes()
                vertical_sizes = self._vertical_split.sizes()
            except AttributeError:
                folder_sizes = []
                vertical_sizes = []
            # Only commit the capture if Qt has handed us non-zero
            # numbers - otherwise we'd freeze a degenerate layout into
            # the Reset target. The ``showEvent`` may fire again on
            # re-show; the captured flag keeps us from clobbering the
            # initial values once we have a good read.
            if all(s > 0 for s in folder_sizes) and all(
                s > 0 for s in vertical_sizes
            ):
                self._default_folder_side_sizes = list(folder_sizes)
                self._default_vertical_sizes = list(vertical_sizes)
                self._default_sizes_captured = True

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Floating Close button - re-anchor to the bottom-right corner
        # whenever the panel resizes.
        self._reposition_close_button()
        # Re-apply 60/40 on every resize until the user drags the
        # divider. ``Reset panel`` clears the flag so the default
        # returns mid-session. The pair lives nested inside the
        # vertical split; read the vertical split's width because the
        # pair's own width is stale during the outer resize event.
        if (
            not getattr(self, "_folder_side_user_dragged", True)
            and getattr(self, "_folder_side_split", None) is not None
            and getattr(self, "_vertical_split", None) is not None
        ):
            width = self._vertical_split.width()
            if width <= 0:
                return
            ratio_sum = sum(_DEFAULT_FOLDER_SIDE_SPLIT)
            left = max(1, width * _DEFAULT_FOLDER_SIDE_SPLIT[0] // ratio_sum)
            right = max(1, width - left)
            blocker = self._folder_side_split.blockSignals(True)
            try:
                self._folder_side_split.setSizes([left, right])
            finally:
                self._folder_side_split.blockSignals(blocker)

    # ----- empty-state gating ---------------------------------------------

    def _refresh_grid_stack(self) -> None:
        """Pick the populated-vs-empty page based on discovered plugin count.

        Empty-state trigger: replace the grid
        region with the welcome message ONLY when there are zero
        Plugins to show - i.e. neither user-added folders NOR the
        Global layer have contributed any discovered Plugins. Global
        Plugins (from the ``<nsl_root>/Global/`` folder convention
        and/or ``NSL_GLOBAL_PLUGIN_DIRS``) keep the panel populated
        even when ``user_plugins_dirs`` is empty.

        Keyed on the actual grid key count (the union of user-added +
        Global after discovery merges), not ``folder_card.row_count()``
        which counts only user-added folders - with a Global layer
        configured and no user folders, the row count would wrongly
        show the empty-state page even though plugins were discovered.
        """
        has_plugins = len(self.grid.keys()) > 0
        page = 0 if has_plugins else 1
        if self._grid_stack.currentIndex() != page:
            self._grid_stack.setCurrentIndex(page)
        # First-run affordance on Add Plugins Folder - ON when the
        # grid is empty (no plugins from any source), OFF otherwise.
        # Same trigger handles initial first-run AND mid-session
        # return-to-empty when the user removes their last folder.
        self.folder_card.set_first_run_affordance(not has_plugins)

    def refresh_grid_stack(self) -> None:
        """Public hook for the wiring layer to re-evaluate the stack page
        after a programmatic ``folder_card.set_entries(...)`` call.

        FolderCard does not emit an "entries-changed" signal for
        programmatic updates (only ``remove_confirmed`` for in-card
        removes and ``add_folder_requested`` for the Add button), so
        anyone who replaces the entries list externally should call
        this to keep the stack in sync.
        """
        self._refresh_grid_stack()

    # ----- public API the wiring layer uses --------------------------------

    def rebuild_grid(self, keys, pill_factory=None) -> None:
        """Replace the grid contents with a new key list + factory.

        Used by the wiring layer to repopulate the grid when the active
        Loadout changes. Splitter sizes and collapsed states are NOT
        affected (the new grid takes the existing grid's slot).
        """
        if pill_factory is None:
            pill_factory = self._pill_factory
        else:
            self._pill_factory = pill_factory

        # Replace the grid in its parent column. We hold a reference to
        # the grid's parent layout to swap it cleanly.
        old_grid = self.grid
        parent = old_grid.parentWidget()
        layout = parent.layout() if parent is not None else None
        new_grid = PluginsGrid(list(keys), pill_factory, parent)
        if layout is not None:
            layout.replaceWidget(old_grid, new_grid)
        old_grid.setParent(None)
        old_grid.deleteLater()
        self.grid = new_grid
        # Re-connect info-bar selection sync; the signal is on the new
        # grid instance.
        try:
            new_grid.selection_changed.connect(self._on_grid_selection_changed)
        except AttributeError:
            pass

    def reset_panel_layout(self) -> None:
        """Restore default splitter ratios, side panel tab, AND clear
        in-panel session state (filters, selection, sort).

        Wired to :attr:`TopToolbar.reset_panel_requested` in
        :meth:`_wire_signals`. Touches splitter geometry, the side
        panel's active tab, the search/tags filter, the pill
        selection, and the sort dropdown. **Never** touches domain
        state - Loadouts, Plugins Folders, the dirty flag, undo
        history, and saved baselines all stay intact.

        Beyond layout (splitter sizes + side panel tab), Reset also wipes
        transient in-panel session state: it clears filters, deselects all
        pills, and resets sort to A→Z.

        Idempotent at default: when no filter is active, no pills are
        selected, and sort is already A→Z, the session-state branches
        each early-return / no-op. Likewise for layout - once
        :meth:`showEvent` has captured Qt's actual first-paint sizes,
        Reset replays them via ``setSizes`` directly, so at the default
        layout the splitters are already at those sizes and nothing
        moves. The ratio-based path is kept as a fallback for the rare
        case where ``showEvent`` never fires (off-screen consumers).
        """
        if getattr(self, "_default_sizes_captured", False):
            self._folder_side_split.setSizes(
                list(self._default_folder_side_sizes)
            )
            self._vertical_split.setSizes(
                list(self._default_vertical_sizes)
            )
        else:
            # No showEvent capture yet - fall back to ratio math against
            # the cached construct-time tuples.
            self._apply_split_by_ratio(
                self._folder_side_split,
                self._default_folder_side_sizes,
                horizontal=True,
            )
            self._apply_split_by_ratio(
                self._vertical_split,
                self._default_vertical_sizes,
                horizontal=False,
            )
        # Re-arm the folder/side auto-centre so subsequent resizes lock to
        # the 60/40 default again until the user drags the divider next.
        self._folder_side_user_dragged = False
        # Restore the side panel's default active tab.
        self.side_panel.tabs.setCurrentIndex(self._default_side_panel_tab_index)

        # Clear transient session state. Order matters slightly:
        # deselect first so the subsequent sort/filter rebuilds don't
        # re-emit a stale selection through the bridge.
        try:
            grid = getattr(self, "grid", None)
            if grid is not None and hasattr(grid, "clear_selection"):
                grid.clear_selection()
        except Exception:  # noqa: BLE001 - reset must not raise on any one branch
            pass
        # Clear filters (search text + invert toggle). The strip's
        # ``clear_filter`` is idempotent when both are already at
        # defaults so this is a no-op when no filter is active.
        try:
            search_tags = getattr(self, "search_tags", None)
            if search_tags is not None and hasattr(search_tags, "clear_filter"):
                search_tags.clear_filter()
        except Exception:  # noqa: BLE001
            pass
        # Reset the sort dropdown to A → Z. ``set_sort_mode`` is a
        # no-op when the dropdown is already on A → Z (Qt's
        # ``setCurrentText`` short-circuits when the text is unchanged).
        try:
            from nsl.ui.grid_toolbar import SortMode
            grid_toolbar = getattr(self, "grid_toolbar", None)
            if grid_toolbar is not None and hasattr(grid_toolbar, "set_sort_mode"):
                grid_toolbar.set_sort_mode(SortMode.A_TO_Z)
        except Exception:  # noqa: BLE001
            pass
        # Restore every folder card eye icon to visible. Clear the
        # registry's visibility map (missing keys default to True in
        # ``folder_list_from``) AND mirror the change into the filter
        # pipeline so the grid re-shows pills from any folder that
        # had been eye-toggled off. A single ``_refresh`` at the end
        # propagates both through ``refresh_from_registry``.
        try:
            registry = getattr(self, "registry", None)
            if registry is not None and hasattr(registry, "_folder_visibility"):
                registry._folder_visibility.clear()
            pipeline = getattr(self, "filter_pipeline", None)
            if pipeline is not None and hasattr(pipeline, "_folder_visible"):
                for path in list(pipeline._folder_visible.keys()):
                    pipeline._folder_visible[path] = True
            if registry is not None and hasattr(registry, "_refresh"):
                registry._refresh()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _apply_split_by_ratio(
        splitter: "QtWidgets.QSplitter",
        ratios,
        *,
        horizontal: bool,
    ) -> None:
        """Apply *ratios* to *splitter* scaled against its current dimension.

        Qt's ``setSizes`` re-normalises against the splitter's actual
        width / height, but only when the supplied total is non-trivial
        relative to current size - otherwise stretch factors take over
        and pad unevenly. Multiplying each ratio against the current
        dimension lands the requested proportions deterministically.
        """
        total = splitter.width() if horizontal else splitter.height()
        if total <= 0:
            splitter.setSizes(list(ratios))
            return
        ratio_sum = sum(ratios) or 1
        sizes = [max(1, total * r // ratio_sum) for r in ratios]
        splitter.setSizes(sizes)

    # ----- signal wiring ---------------------------------------------------

    def _wire_signals(self) -> None:
        """Connect intra-panel signals.

        The one panel-internal signal wired here is the Reset panel
        button to :meth:`reset_panel_layout`. The per-widget
        ``wire_<module>(self)`` helpers (each connecting one widget's
        outbound signals to the right domain module) are called in the
        block below, between the ``# === BEGIN P5 WIRING ===`` /
        ``# === END P5 WIRING ===`` markers.
        """
        # Intra-panel wiring - Reset panel restores layout defaults.
        self.top_toolbar.reset_panel_requested.connect(self.reset_panel_layout)

        # Grid selection updates the info bar's Selected counter without
        # a full refresh round-trip.
        try:
            self.grid.selection_changed.connect(self._on_grid_selection_changed)
        except AttributeError:
            pass

        # Panic-button cross-wire: the visible panic control lives in the
        # top toolbar, but the existing ``panic_toggled`` signal source on
        # ``loadout_strip`` is the contract the wiring layer + tests use.
        # Forward the top-toolbar toggle into loadout_strip.btn_panic so
        # loadout_strip.panic_toggled fires for downstream consumers.
        self.top_toolbar.panic_toggled.connect(
            self.loadout_strip.btn_panic.setChecked
        )
        # Reverse: keep the top-toolbar button in sync if anyone calls
        # loadout_strip.set_panic_engaged() (e.g. tests or domain reset).
        self.loadout_strip.btn_panic.toggled.connect(
            self.top_toolbar.set_panic_engaged
        )

        # === BEGIN P5 WIRING ===
        # Production path: registry attached → wiring must succeed.
        # No-registry path: registry is None → wiring may noop on missing
        # state, so we wrap in a try/except that surfaces the trace but
        # never blocks construction.
        from nsl.ui.wiring.events import wire_events
        from nsl.ui.selection import wire_selection
        from nsl.ui.sort import build_key_to_folder, wire_sort
        from nsl.ui.filter_pipeline import wire_filter_pipeline
        from nsl.ui.wiring.provenance import wire_provenance
        from nsl.ui.wiring.sort_state import wire_sort_state_lookup
        from nsl.ui.wiring.status_routing import wire_status_routing
        from nsl.ui.degraded import wire_degraded
        from nsl.ui.wiring.bulk_ops import wire_bulk_ops
        from nsl.ui.wiring.undo_switch import wire_undo_switch
        from nsl.ui.wiring.reset_global import wire_reset_global

        if self.registry is None:
            try:
                wire_events(self)
                wire_selection(self)
                wire_sort(self)
                # Pass ``build_key_to_folder`` so folder-eye toggles
                # actually filter pills (previously the default-None
                # mapping made the eyes record state without
                # hiding anything).
                wire_filter_pipeline(
                    self, key_to_folder=build_key_to_folder(self)
                )
                # Production state lookup - wires the sort dropdown's
                # five non-alpha modes (Status / Selected / Changed
                # state / Warnings / Folder of origin) to live registry
                # + grid + folder-card data. MUST come AFTER
                # ``wire_filter_pipeline`` because it pushes the lookup
                # into the pipeline.
                wire_sort_state_lookup(self)
                wire_provenance(self)
                wire_status_routing(self)
                wire_degraded(self)
                wire_bulk_ops(self)
                wire_undo_switch(self)
                wire_reset_global(self)
            except Exception:  # noqa: BLE001 - no-registry path only.
                import traceback
                traceback.print_exc()
        else:
            wire_events(self)
            wire_selection(self)
            wire_sort(self)
            wire_filter_pipeline(
                self, key_to_folder=build_key_to_folder(self)
            )
            wire_sort_state_lookup(self)
            wire_provenance(self)
            wire_status_routing(self)
            wire_degraded(self)
            wire_bulk_ops(self)
            wire_undo_switch(self)
            wire_reset_global(self)
        # === END P5 WIRING ===

    # ----- registry-driven refresh ---------------------------------------

    def refresh_from_registry(self) -> None:
        """Re-emit widget state from ``self.registry`` after a domain mutation.

        Called by :meth:`Registry.apply_op_result` (via the attached
        ``refresh_callback``) and once at construction so the panel
        boots populated. Reads pure state-derivation helpers in
        :mod:`nsl.ui.state` and pushes the result into each region's
        existing setter API - no widget-internal poking.

        Refreshed regions:
            * Loadout strip - dropdown contents + active row + dirty flag.
            * Folder card - entries from settings.user_plugins_dirs.
            * Banner - pending count + kind (hidden when count == 0).
            * Grid stack - page swap to empty-state when no folders are
              configured.
            * Plugins grid - rebuilt against the union of active +
              global plugin names so loadout switch repopulates pills.
              Re-runs ``rewire_grid_pills`` so the new pills carry
              signal connections.

        No-op when ``self.registry`` is None.
        """
        if self.registry is None:
            return

        registry = self.registry

        # Panic mode disables most of the panel. When panic is engaged,
        # user-added Plugins are held; the panel UX reflects that by
        # greying out everything
        # except the loadout strip (so saves still work), the side
        # panel tabs (Summary/Info/Log remain inspectable), and the
        # top toolbar (so the panic button itself stays clickable to
        # disengage).
        panic_engaged = bool(getattr(registry.state, "panic", False))
        # Panic dim flows through the folder card's per-row setter, not a
        # blanket ``setEnabled``: the card stays interactive (Add / Rescan
        # keep working); user-added rows dim + strike through to read as
        # "ignored on next restart"; the pinned Global Plugins row stays
        # fully painted because the Global layer keeps loading regardless of
        # panic (panic drops user plugins only, never Globals). This keeps
        # it clear that non-Global folders are ignored while Globals remain.
        self.folder_card.set_panic_engaged(panic_engaged)
        # The grid itself stays enabled in panic so info buttons remain
        # clickable on every pill (user can still
        # inspect plugin info mid-panic). Each pill self-gates its
        # mutating zones (body toggle, GUI chip) on
        # ``PillState.panic_engaged + source == USER_ADDED`` in
        # :meth:`PluginPill.mousePressEvent`. GLOBAL pills stay
        # fully interactive - Globals still load in panic. The
        # per-pill opacity overlay (``_apply_panic_grid_visual``) +
        # disabled grid_toolbar carry the "non-actionable for user
        # plugins" visual signal.
        self.grid_toolbar.setEnabled(not panic_engaged)
        self.search_tags.setEnabled(not panic_engaged)
        # Push panic state to the top-toolbar's panic button too - the
        # saved settings flag reaches the folder card / grid / banner /
        # pills on panel reopen, but without this push the button itself
        # stays unchecked. With ``settings.panic=True`` on disk that
        # produced a panel where the banner said "Panic engaged" and the
        # grid was greyed out but the button read "Panic Mode: Disable
        # All User Plugins", making panic look like it didn't persist
        # across close+reopen. ``set_panic_engaged`` is the no-emit setter
        # so the cross-wire to ``loadout_strip.btn_panic`` doesn't
        # round-trip into ``_on_panic_toggled`` and re-write disk.
        self.top_toolbar.set_panic_engaged(panic_engaged)
        self.loadout_strip.set_panic_engaged(panic_engaged)
        # loadout_strip, top_toolbar, side_panel remain enabled.

        # Qt's setEnabled(False) is too subtle on the custom-painted
        # pills (they keep their own colours), so layer a translucent
        # overlay on the grid to make the disabled state read at a
        # glance. The treatment greys only USER_ADDED pills and leaves
        # GLOBAL coloured (see :meth:`_apply_panic_grid_visual`).
        self._apply_panic_grid_visual(panic_engaged)

        # Loadout strip - name list + active + dirty flag. ``dirty_stems``
        # surfaces ``(*)`` on non-active rows whose dirty in-memory model
        # is parked in :attr:`Registry._pending_models`.
        has_global_layer = bool(
            registry.global_model and registry.global_model.plugins
        )
        loadouts = loadout_list_from(
            registry.loadouts_dir,
            registry.state,
            active_is_dirty=registry.is_active_dirty,
            dirty_stems=getattr(registry, "dirty_stems", ()),
            has_global_layer=has_global_layer,
            global_loadout_copy_exists=getattr(
                registry, "global_loadout_copy_exists", False
            ),
        )
        active_name = _active_strip_name(registry)
        self.loadout_strip.set_loadouts(loadouts, active=active_name)
        self.loadout_strip.set_dirty(registry.is_active_dirty)

        # Folder card - paths + visibility + health, plus the
        # synthetic Global Plugins row (pinned-to-bottom) when a
        # Global layer is resolved. A Global layer with no user folders
        # would otherwise leave the card empty and read as misleading;
        # ``folder_list_from`` appends the synthetic row when
        # ``global_model`` carries plugins.
        global_dirs = list(
            getattr(registry, "global_plugin_dirs", []) or []
        )
        folder_entries = folder_list_from(
            registry.user_plugin_dirs,
            visibility=registry.folder_visibility,
            health=registry.folder_health,
            global_model=registry.global_model,
            global_plugins_dir=str(global_dirs[0]) if global_dirs else "",
        )
        self.folder_card.set_entries(folder_entries)

        # Empty-state page swap - triggered only when there are zero
        # pills to show. Uses the same union
        # the grid is rebuilt against below (``_plugin_key_union``),
        # which spans three sources: scanner-discovered plugins, the
        # active Loadout's plugins, AND the resolved Global model.
        # Global-only first run has ``user_plugins_dirs=[]`` (no
        # discovered plugins) but ``global_model.plugins`` carries the
        # Global layer's Octopus/Otter/Penguin - those keys land in the
        # union, the grid renders 3 pills, and the welcome page stays
        # hidden. Keyed on the canonical pill-key union (what the grid
        # actually builds): a user-folder count would miss Global-
        # only first runs, and a discovered-plugin count would miss
        # Global contributions (the scanner runs on user dirs only).
        empty = len(_plugin_key_union(registry)) == 0
        # Custom honesty: compute
        # whether the active loadout is the in-memory Custom slot.
        # Threaded into ``pill_state_from`` (force-dirty path) and the
        # banner state machine (forces PENDING_CHANGES voice) so the
        # committed-pending vocabulary (lime/red border, saved-glow,
        # "Saved Change" banner copy) never paints on a slot that
        # can't commit. See state.py for the per-pill effect.
        from nsl.constants import DEFAULT_CUSTOM_LOADOUT_STEM as _CUSTOM_STEM
        active_is_custom = (
            registry.state is not None
            and registry.state.active == _CUSTOM_STEM
        )
        self._grid_stack.setCurrentIndex(1 if empty else 0)
        # Mirror the affordance flip ``_refresh_grid_stack`` does -
        # this refresh path bypasses it but must keep the orange
        # first-run border invariant in sync. Without this, adding the
        # first folder swaps the stack page but leaves the border on
        # because ``refresh_from_registry`` is the canonical "I just
        # processed the registry" path called by the wiring layer
        # after a folder add, and it doesn't route through
        # ``_refresh_grid_stack``.
        self.folder_card.set_first_run_affordance(empty)

        # Search/Tags strip - show/hide the "Reset Global Plugins to
        # Default" button based on whether a Global layer is currently
        # active for this session. The button is hidden entirely (not
        # disabled) when no Global layer resolves; the rule is that the
        # affordance does not appear when there's nothing to reset
        # against. It lives on the controls row beside the
        # Select-filtered / Deselect-filtered / Clear-selection buttons.
        global_layer_active = bool(
            registry.global_model is not None
            and registry.global_model.plugins
        )
        self.search_tags.set_global_layer_active(global_layer_active)
        # Reset Global Plugins to Default - enabled only when at least
        # one Global Plugin in the active Loadout has actually
        # drifted from its Global default.
        if hasattr(self.search_tags, "set_reset_global_enabled"):
            diverged = 0
            if hasattr(registry, "count_diverged_global_plugins"):
                try:
                    diverged = registry.count_diverged_global_plugins()
                except Exception:  # noqa: BLE001 - counter must not break refresh
                    diverged = 0
            self.search_tags.set_reset_global_enabled(diverged > 0)

        # Side panel ▸ Summary - surface the session-load truth so the
        # user can read which plugins NSL actually loaded this session
        # and predict the diff signal a toggle will produce. Without
        # this surface the panel UI never showed *which* plugins were
        # loaded; the only way to see a RED tint (= disabled but was
        # loaded this session) is to know which pills were successfully
        # loaded by NSL's boot pass, then toggle one of those off. This
        # surface answers "which plugins are pre-loaded".
        try:
            # Use the ``empty`` flag computed at the grid-stack swap
            # above - not ``self.grid.keys()``. This refresh
            # path sets the summary BEFORE rebuilding the grid further
            # down, so ``self.grid.keys()`` here would still be the
            # pre-refresh set (empty on first folder add → state 1 copy
            # would wrongly stick even after the new pills appear). The
            # canonical ``_plugin_key_union(registry)`` already drove
            # ``empty``; re-using it keeps the empty-page swap, the
            # first-run border, and the summary body in lock-step.
            self.side_panel.set_summary(
                _session_summary_html(
                    grid_has_pills=not empty, registry=registry
                ),
                html=True,
            )
        except Exception:  # noqa: BLE001 - summary update must not break refresh
            pass

        # Banner - count + kind derived from pending_diff against the
        # session-loaded baseline (what NSL actually loaded at boot;
        # see :attr:`Registry.session_loaded_baseline`). The comparison
        # is against the baseline loaded this Nuke session, not against
        # another loadout. Switching the active loadout does not change
        # the baseline - only the current side of the comparison - so the
        # banner correctly reflects "what will load on restart different
        # from what's loaded right now in this session."
        #
        # Diff current-side = active overlaid on Global (sparse-diff
        # resolution). When the active model
        # is missing a Global key (e.g. after
        # ``reset_global_to_default`` empties Custom's plugins dict),
        # Global fallback resolution keeps the effective state stable;
        # comparing raw ``active.plugins`` would falsely surface the
        # absent keys as pending removes (e.g. changing one Global
        # plugin would report a phantom -N after a Global reset).
        current_for_diff = registry.resolved_active_for_diff
        diff = pending_diff(
            current_active=current_for_diff,
            saved_baseline=registry.session_loaded_baseline,
        )
        # Empty-state suppression - when
        # the grid stack is on the empty-state page (no folders
        # configured), the banner does not appear. The banner lives in
        # the counters row rather than the populated grid subtree, so
        # show() is gated on the stack page explicitly.
        empty_page = self._grid_stack.currentIndex() == 1
        # Banner selection priority:
        #   1. PANIC_ENGAGED - always wins when panic is on. Count
        #      slot unused; the message is the binary "restart will
        #      skip user plugins" signal.
        #   2. (hidden) - ``diff.count == 0``. The banner is strictly a
        #      "N changes pending a Nuke restart" signal; when nothing is
        #      pending it disappears. Unsaved in-memory edits that happen
        #      to cancel out the session-loaded diff (e.g. save a disable,
        #      then re-enable) are signalled by the (*) marker + the lit
        #      Save button - NOT by a "0 pending changes" banner. The
        #      banner never says "0 pending changes"; it disappears
        #      instead.
        #   3. PENDING_CHANGES - count > 0 AND (in-memory edits unsaved,
        #      OR Custom active - Custom never commits; the voice is
        #      "Save As to promote").
        #   4. SAVED_AWAITING_RESTART - count > 0 AND clean on disk
        #      (Save fired). Only a Nuke restart is left.
        if empty_page:
            self.banner.hide()
        elif panic_engaged:
            # Append the "Only Global Plugins will be loaded." sentence
            # only when a Global Loadout is actually configured (resolved
            # global_model has plugins). No Global -> the banner stays the
            # plain "all User Plugins will be skipped." statement.
            gm = getattr(registry, "global_model", None)
            globals_present = bool(gm is not None and gm.plugins)
            self.banner.set_state(
                BannerKind.PANIC_ENGAGED, 0, globals_present=globals_present
            )
            self.banner.show()
            self.banner.raise_()
        elif diff.count == 0:
            self.banner.hide()
        elif active_is_custom or registry.is_active_dirty:
            self.banner.set_state(BannerKind.PENDING_CHANGES, diff.count)
            self.banner.show()
            self.banner.raise_()
        else:
            # count > 0 AND clean on disk → saved, awaiting restart.
            self.banner.set_state(BannerKind.SAVED_AWAITING_RESTART, diff.count)
            self.banner.show()
            self.banner.raise_()

        # Plugins grid - rebuild against the current key union. Only
        # re-run pill signal wiring when a rebuild actually happened;
        # rewire_grid_pills connects new lambdas each call so calling
        # it on a no-op set_keys would stack duplicate connections.
        #
        # Also stash the master key list on the panel so the filter
        # pipeline can rebuild against it (rather than against
        # grid.keys(), which after filter is itself the filtered subset).
        new_keys = _plugin_key_union(registry)
        self._all_plugin_keys = list(new_keys)

        # Grid counter strip. Reads counts off the same diff math the
        # banner uses so the two never disagree.
        # ``pending_diff_split`` returns (add, del) - pills that will
        # load vs unload on next Save.
        selected_count = 0
        try:
            selected_count = len(self.grid.selected_keys())
        except Exception:  # noqa: BLE001 - counter strip is informational only.
            selected_count = 0
        # Same Global-active fallback as the banner - see above.
        pending_add, pending_del = pending_diff_split(
            current_active=current_for_diff,
            saved_baseline=registry.session_loaded_baseline,
        )
        gui_only_count = _count_gui_only(current_for_diff)
        # Loaded chip - fixed count of plugins NSL loaded this session
        # (boot-time manifest). Counts the session total, not the
        # visible intersection, so it does not change with grid filtering
        # or a mid-session folder delete.
        loaded_session = self._count_loaded_session()
        # Logs chip = problematic Plugins (load failures + missing). The
        # registry has no failure-count surface yet; show 0 until that
        # wiring lands.
        self.grid_counters.set_counters(
            selected_count,
            len(new_keys),
            pending_add,
            pending_del,
            gui_only_count,
            0,
            loaded_session,
        )
        # Route through the filter pipeline whenever it exists, NOT
        # just when a filter query is active. The pipeline composes
        # filter + sort in one pass; ``_plugin_key_union`` returns
        # the master list in A→Z order regardless of which sort the
        # user picked, so a bare ``set_keys(new_keys)`` here would
        # silently reset the grid to A→Z on every refresh. Symptom:
        # toggling a pill while Z→A is active caused the grid to
        # rebuild in A→Z order, making the pill at the cursor
        # appear to "change name" because a different plugin's pill
        # now occupies that position. Sort is visibility-only - it
        # reorders pills and must never disturb toggling or pill
        # identity.
        pipeline = getattr(self, "filter_pipeline", None)
        if pipeline is not None:
            # Pipeline will call _apply_visibility which calls
            # set_keys with the filter+sort-composed key list, then
            # rewires pills.
            pipeline._recompute_and_apply()
        elif self.grid.set_keys(new_keys):
            from nsl.ui.wiring.events import rewire_grid_pills

            rewire_grid_pills(self)

        # *Push fresh PillState to every existing pill.*
        # ``set_keys`` only fires the factory when the key list itself
        # changed (a plugin appeared or disappeared); a pill toggle
        # flips an ``enabled`` flag but keeps the same key set, so
        # ``set_keys`` short-circuits and existing pills keep the
        # PillState the factory minted at construction - stale enabled
        # flag, stale tint, stale selected, stale gui_only. That's why
        # pill body tints "stopped working" after a toggle: the
        # canonical ``_derive_tint`` math runs only once per pill life,
        # at birth, when ``enabled == True`` and status defaults to
        # LOADED → NEUTRAL. After a toggle, the registry is updated
        # but no signal carried the new state back to the pill widget.
        #
        # Replay ``pill_state_from`` for every key currently displayed
        # in the grid and push the result via ``pill.set_state``. The
        # pill widget calls ``update()`` internally, so the body tint,
        # status icon, selection ring, and gui-only chip all refresh
        # in one pass. Cheap - O(n_visible) - and runs only after a
        # domain mutation (not on every Qt paint).
        try:
            grid_keys = self.grid.keys()
            grid_pills = list(getattr(self.grid, "_pills", []))
            grid_cells = list(getattr(self.grid, "_cells", []))
        except Exception:  # noqa: BLE001 - refresh must never raise.
            grid_keys, grid_pills, grid_cells = [], [], []
        if len(grid_keys) == len(grid_pills):
            global_names = registry.global_plugin_names
            # Under the runnable-python-loadout-chain architecture, NSL
            # no longer maintains a per-plugin
            # loaded-set registry. Nuke's NUKE_PATH walker IS the loader;
            # if a plugin's init.py raised, Nuke crashed the interpreter
            # and the panel never constructed. Therefore: if the panel
            # is open, every enabled-in-the-loadout pill is by definition
            # "loaded this session" - there is no per-pill failed state.
            # Per-pill diagnostic / failure-category vocabulary is gone
            # with the rest of the runtime classifier.
            selected_keys = set(self.grid.selected_keys())
            # loaded-in-session is read off the boot snapshot
            # (``session_loaded_baseline``). A plugin in there was on
            # NUKE_PATH when this Nuke session booted; anything else
            # (folder added mid-session, plugin newly toggled) is NOT
            # loaded yet and will only load on next restart. This is
            # what drives the green pending-enable tint on freshly-
            # added pills - without it ``_derive_tint`` collapses
            # ``enabled=True + status=LOADED`` to NEUTRAL and the user
            # sees no "will load on restart" signal. Same source the
            # banner / Loaded counter chip uses.
            session_loaded = registry.session_loaded_baseline
            loaded_set: frozenset = (
                frozenset(session_loaded.plugins.keys())
                if session_loaded is not None
                else frozenset()
            )
            # Cell diff-tint wash matches the pill's
            # ``_pending_border_color`` so the pending-restart signal is
            # also legible at the cell padding (not only from the pill
            # border + glow). Precomputed once per refresh.
            cell_tint_load = QtGui.QColor(*CELL_DIFF_BG_LOAD_RGBA)
            cell_tint_unload = QtGui.QColor(*CELL_DIFF_BG_UNLOAD_RGBA)
            cell_tint_gui = QtGui.QColor(*CELL_DIFF_BG_GUI_ON_RGBA)
            for idx, (key, pill) in enumerate(zip(grid_keys, grid_pills)):
                loaded = key in loaded_set
                diagnostic_available = False
                failure_label = None
                # Source-missing: the plugin's source folder is no
                # longer scanned (folder removed mid-session) AND it
                # isn't part of the Global layer. Under the new
                # architecture this is the only "needs attention"
                # signal the pill carries; drives the YELLOW hazard
                # body + tooltip "source folder no longer reachable".
                source_missing = (
                    (
                        registry.discovered_plugins is None
                        or key not in registry.discovered_plugins
                    )
                    and (
                        registry.global_model is None
                        or key not in registry.global_model.plugins
                    )
                )
                kwargs = dict(
                    active=registry.active_model,
                    global_model=registry.global_model,
                    global_plugin_names=global_names,
                    # ``saved_baseline`` drives the per-pill
                    # is-dirty-vs-saved signal (white vs lime/red
                    # border). Read the active Loadout's saved-on-disk
                    # baseline - NOT ``session_loaded_baseline``, which
                    # is the boot-loaded set the banner uses.
                    saved_baseline=registry.active_saved_baseline,
                    # Ceremonial-save set - names of plugins that
                    # should read as uncommitted (no glow) regardless
                    # of value comparison. Populated by folder-add,
                    # cleared on Save / loadout switch. Per-plugin
                    # scope so an unrelated pill's saved-glow isn't
                    # affected by a folder-add.
                    force_dirty_plugins=getattr(
                        registry, "force_dirty_plugins", frozenset()
                    ),
                    source_missing=source_missing,
                    selected=key in selected_keys,
                    loaded_in_session=loaded,
                    session_gui_only=(
                        session_loaded.plugins[key].gui_only
                        if session_loaded is not None
                        and key in session_loaded.plugins
                        else None
                    ),
                    diagnostic_available=diagnostic_available,
                    failure_label=failure_label,
                    # Panic suppresses the saved-glow on USER_ADDED
                    # pills. They won't load on next restart in panic,
                    # so a lime "will load" glow would lie. Globals keep
                    # their glow.
                    panic_engaged=panic_engaged,
                    # Custom can't commit (Save redirects to Save As),
                    # so the lime/red saved
                    # vocabulary is dishonest. The flag forces the
                    # dirty path inside ``pill_state_from`` for every
                    # pill while Custom is active; ``_pending_border_color``
                    # then returns None and pills fall back to the
                    # uncommitted-white visual.
                    active_is_custom=active_is_custom,
                )
                fresh = pill_state_from(key, **kwargs)
                try:
                    pill.set_state(fresh)
                except Exception:  # noqa: BLE001 - one bad pill must not block the rest.
                    continue

                # Cell diff-tint - mirror the pill's pending-restart
                # border colour onto the matching cell's background
                # wash. Identity-compares against ``Palette`` constants
                # because ``_pending_border_color`` returns the same
                # class-level QColor objects. ``None`` clears the wash.
                if idx < len(grid_cells):
                    cell = grid_cells[idx]
                    pending = pill._pending_border_color()
                    if pending is Palette.BORDER_PENDING_ENABLE:
                        cell.set_diff_tint(cell_tint_load)
                    elif pending is Palette.BORDER_PENDING_DISABLE:
                        cell.set_diff_tint(cell_tint_unload)
                    elif fresh.gui_pending_on and fresh.gui_committed:
                        # GUI OFF->ON, committed (saved on a saveable
                        # slot): purple wash. Only reached when there's
                        # no enable/disable change (load wash wins above)
                        # and the GUI flip is persisted - never on Custom
                        # or an unsaved edit (those keep the lit-purple
                        # chip as the pending signal).
                        cell.set_diff_tint(cell_tint_gui)
                    else:
                        cell.set_diff_tint(None)

        # RE-APPLY the panic dim AFTER the grid is (re)built. The earlier
        # call near the top of this method (``_apply_panic_grid_visual``)
        # runs against whatever
        # pills exist at that point. On a fresh panel open with panic
        # ALREADY engaged on disk, the grid is still empty then, so that
        # first call dims nothing; the pills are built below and end up
        # full-colour - making a boot-into-panic panel look un-panicked
        # while the live-toggle path (pills already exist) dimmed
        # correctly. Re-applying here makes both paths identical: the
        # final, rebuilt pills always carry the panic opacity. Idempotent
        # + cheap (effects cached per pill), so the double call is safe.
        self._apply_panic_grid_visual(panic_engaged)

        # Keep top-toolbar Undo/Redo in sync with the active stack on
        # every refresh, not just wiring-layer ops. Without
        # this, a registry mutation that bypasses `_toggle_plugin`
        # (loadout switch, external apply_op_result) leaves the toolbar's
        # enabled state stale.
        from nsl.ui.wiring.events import _sync_undo_toolbar

        _sync_undo_toolbar(self)

    def _apply_panic_grid_visual(self, engaged: bool) -> None:
        """Dim USER_ADDED pills via per-pill opacity when panic is on.

        Per-pill rather than a grid-wide wash: panic only skips
        USER-added Plugins on next restart, while GLOBAL (Global)
        pills still load, so dimming the whole grid would falsely
        suggest Global pills were also off.

        The treatment dims each USER_ADDED pill individually
        via its own ``QGraphicsOpacityEffect`` at 0.35; GLOBAL
        pills stay full-color so the user sees "these will load,
        those won't" at a glance. The classification mirrors
        :func:`nsl.ui.state.pill_state_from` - a key in
        ``registry.global_plugin_names`` is GLOBAL; anything
        else is USER_ADDED.

        Effects are cached per pill widget via ``setGraphicsEffect``
        so toggling panic off only flips ``setEnabled(False)`` on
        each existing effect, never re-allocating. Cheap to call
        on every ``refresh_from_registry`` pass.

        No-op when ``self.registry`` is None.
        """
        registry = self.registry
        if registry is None:
            return
        global_names = getattr(
            registry, "global_plugin_names", frozenset()
        ) or frozenset()
        try:
            keys = self.grid.keys()
            pills = list(getattr(self.grid, "_pills", []))
        except Exception:  # noqa: BLE001 - visual treatment must not break refresh
            return
        if len(keys) != len(pills):
            return
        for key, pill in zip(keys, pills):
            is_user_added = key not in global_names
            should_dim = bool(engaged and is_user_added)
            try:
                effect = pill.graphicsEffect()
                if should_dim:
                    if effect is None:
                        effect = compat.QtWidgets.QGraphicsOpacityEffect(pill)
                        effect.setOpacity(0.35)
                        pill.setGraphicsEffect(effect)
                    effect.setEnabled(True)
                else:
                    if effect is not None:
                        effect.setEnabled(False)
            except Exception:  # noqa: BLE001 - one bad pill must not block the rest.
                continue

    def _apply_active_chips_to_grid(self, info_plugin, menu_plugin) -> None:
        """Push ``info_active`` + ``menu_active`` to every pill.

        Both flags are pushed together so they stay mutually exclusive:
        whichever pill+chip combo the user last clicked is the only one
        lit. Pass ``info_plugin=name, menu_plugin=None`` when reacting
        to an info click; the inverse on a menu click. Pass both
        ``None`` to clear everything.

        Called by ``Registry.on_pill_info`` and
        ``Registry.on_pill_menu`` after the side panel content
        is set.

        Mirrors ``_on_grid_selection_changed``'s shape - per-pill paint
        state has to be pushed from the outer source to every pill on
        every change, because pill paint reads its own ``_state`` not
        the panel's.
        """
        try:
            pill_keys = list(self.grid.keys())
            pill_widgets = list(getattr(self.grid, "_pills", []))
        except Exception:  # noqa: BLE001 - bad grid must not raise.
            return
        if len(pill_keys) != len(pill_widgets):
            return
        for key, pill in zip(pill_keys, pill_widgets):
            setter = getattr(pill, "update_state", None)
            if setter is None:
                continue
            try:
                setter(
                    info_active=(key == info_plugin),
                    menu_active=(key == menu_plugin),
                )
            except Exception:  # noqa: BLE001 - bad pill must not break the loop.
                pass

    def _on_grid_selection_changed(self, selected_keys: list) -> None:
        """Update the counter strip's Selected chip on selection mutation.

        Full refresh is overkill for a selection change; rebuild the
        strip locally so its non-selection chips keep the values the
        last refresh computed.

        The per-pill selected flag is pushed too.
        ``_Cell.set_selected`` only drives the cell's
        selection halo; the pill's orange selection ring is painted
        inside the pill itself from ``PillState.selected`` and never
        updated by the cell. Without this loop a Select-All → Clear
        sequence left every pill painting the orange ring against a
        zero-key selection - pill border + cell halo are independent
        paint surfaces, both need updating on selection change.
        """
        selected_set = set(selected_keys)
        try:
            pill_keys = list(self.grid.keys())
            pill_widgets = list(getattr(self.grid, "_pills", []))
        except Exception:  # noqa: BLE001 - selection refresh must not raise.
            pill_keys, pill_widgets = [], []
        if len(pill_keys) == len(pill_widgets):
            for key, pill in zip(pill_keys, pill_widgets):
                setter = getattr(pill, "update_state", None)
                if setter is None:
                    continue
                try:
                    setter(selected=key in selected_set)
                except Exception:  # noqa: BLE001 - bad pill must not break selection.
                    pass

        strip = getattr(self, "grid_counters", None)
        if strip is None:
            return
        master_keys = getattr(self, "_all_plugin_keys", []) or self.grid.keys()
        total = len(master_keys)
        loaded_session = self._count_loaded_session()
        registry = self.registry
        if registry is None:
            strip.set_counters(len(selected_keys), total, 0, 0, 0, 0, loaded_session)
            return
        # Sparse-diff resolution - see refresh_from_registry banner
        # block. ``resolved_active_for_diff`` overlays the active
        # Loadout on Global so keys absent from active correctly
        # resolve to their Global value before the diff math runs.
        current_for_diff = registry.resolved_active_for_diff
        pending_add, pending_del = pending_diff_split(
            current_active=current_for_diff,
            saved_baseline=registry.session_loaded_baseline,
        )
        strip.set_counters(
            len(selected_keys),
            total,
            pending_add,
            pending_del,
            _count_gui_only(current_for_diff),
            0,
            loaded_session,
        )

    def _count_loaded_session(self) -> int:
        """Count plugins NSL loaded into THIS Nuke session - a fixed total.

        Reads the session baseline (``registry.session_loaded_baseline``),
        which is backed by the boot-time manifest the loadout file stamps at
        each ``pluginAddPath`` call (see
        :meth:`Registry._nsl_session_manifest`). The count does NOT intersect
        with the visible grid, so filtering the grid, toggling a pill, or
        deleting a plugin folder mid-session never moves it: a plugin that
        loaded this session stays counted even after its folder is gone.

        The count is the session total, not the visible intersection: a
        visible-intersection count ("of the visible plugins, how many
        loaded") under-reports whenever a loaded plugin leaves the grid
        (e.g. its folder is deleted, so the Loaded count would lie). One
        chip, one honest meaning: the session-total truth.
        """
        registry = self.registry
        if registry is None:
            return 0
        baseline = registry.session_loaded_baseline
        if baseline is None:
            return 0
        return sum(1 for entry in baseline.plugins.values() if entry.enabled)

    def _registry_pill_factory(self, key: str):
        """Pill factory used when a Registry is attached.

        Derives :class:`PillState` from the live registry state via
        :func:`nsl.ui.state.pill_state_from` so newly-created pills
        carry the right enabled / gui_only / source / status hints.
        Also threads ``loaded_in_session`` from the boot-time
        session-loaded baseline so the
        ``(enabled, status_icon)`` tint derivation has session truth
        at birth - otherwise newly-built pills would default to
        optimistic LOADED, collapsing every diff to NEUTRAL.
        ``refresh_from_registry`` re-pushes fresh state on every
        registry mutation; this method only needs to get the *first*
        paint right.
        """
        registry = self.registry
        if registry is None:
            return _default_pill_factory(key)
        from nsl.constants import DEFAULT_CUSTOM_LOADOUT_STEM  # noqa: PLC0415
        # loaded-in-session is read off the boot snapshot
        # (``session_loaded_baseline``). A plugin in there was on
        # NUKE_PATH when this Nuke session booted; anything else
        # (folder added mid-session) is NOT loaded yet and will only
        # load on next restart. Without this the freshly-discovered
        # pills paint NEUTRAL instead of GREEN pending-enable. Same
        # derivation the refresh path uses.
        session_loaded = registry.session_loaded_baseline
        loaded = session_loaded is not None and key in session_loaded.plugins
        diagnostic_available = False
        failure_label = None
        source_missing = (
            (
                registry.discovered_plugins is None
                or key not in registry.discovered_plugins
            )
            and (
                registry.global_model is None
                or key not in registry.global_model.plugins
            )
        )
        state = pill_state_from(
            key,
            active=registry.active_model,
            global_model=registry.global_model,
            global_plugin_names=registry.global_plugin_names,
            # See refresh_from_registry - saved_baseline drives the
            # white/lime/red border, not the banner; use the active
            # Loadout's on-disk baseline.
            saved_baseline=registry.active_saved_baseline,
            force_dirty_plugins=getattr(
                registry, "force_dirty_plugins", frozenset()
            ),
            source_missing=source_missing,
            loaded_in_session=loaded,
            diagnostic_available=diagnostic_available,
            failure_label=failure_label,
            # First-paint panic gate: read it off settings so a
            # registry built in panic mode mints pills without the
            # misleading lime saved-glow. refresh_from_registry
            # re-pushes the same flag on every subsequent refresh.
            panic_engaged=bool(getattr(registry.state, "panic", False)),
            # First-paint Custom-honesty gate: same rationale as
            # ``refresh_from_registry`` - Custom can't commit, so
            # never paint the lime/red committed vocabulary on a
            # pill while Custom is active. See state.py.
            active_is_custom=bool(
                registry.state is not None
                and registry.state.active
                == DEFAULT_CUSTOM_LOADOUT_STEM
            ),
        )
        return PluginPill(state)


# ---------------------------------------------------------------------------
# Internal helpers (module-level, no Qt parent)
# ---------------------------------------------------------------------------


def _active_strip_name(registry: "Registry") -> str:
    """Resolve the strip's active-row name from registry settings.

    Row names are bare stems. When no Global layer is configured, the
    ``Global`` row does not exist - first-run / empty-stem falls back to
    Custom instead of Global.
    """
    from nsl.constants import (
        DEFAULT_CUSTOM_LOADOUT_STEM,
        RESERVED_LOADOUT_STEM,
    )
    from nsl.ui.loadout_strip import GLOBAL_LOADOUT_NAME

    has_global_layer = bool(
        registry.global_model and registry.global_model.plugins
    )
    stem = registry.state.active or RESERVED_LOADOUT_STEM
    if stem == RESERVED_LOADOUT_STEM:
        if not has_global_layer:
            return DEFAULT_CUSTOM_LOADOUT_STEM
        return GLOBAL_LOADOUT_NAME
    return stem


def _count_gui_only(model) -> int:
    """Count enabled, gui_only-flagged plugins in a loadout model."""
    if model is None:
        return 0
    return sum(
        1 for v in model.plugins.values()
        if v.enabled and getattr(v, "gui_only", False)
    )


def _session_summary_html(
    grid_has_pills: bool = False, *, registry=None
) -> str:
    """Render the Side Panel ▸ Summary tab content.

    Under the runnable-python-loadout-chain architecture,
    NSL no longer maintains a per-plugin loaded-set registry - Nuke's
    NUKE_PATH walker is the loader, and a failing plugin's init.py
    crashes the whole interpreter (no per-plugin Failed list survives
    into the running session). The Summary therefore collapses to the
    boot-time effective state ("what should be loaded right now") plus
    a Missing count for plugins whose source folders have vanished.

    The ``grid_has_pills`` flag drives the body copy when nothing is
    declared by the active loadout - splits "true empty" (no pills
    anywhere) from "pills enabled, awaiting save+restart".
    """
    baseline = (
        registry.session_loaded_baseline if registry is not None else None
    )
    loaded: list[str] = []
    gui_only: set[str] = set()
    if baseline is not None:
        for name, entry in baseline.plugins.items():
            if not entry.enabled:
                continue
            loaded.append(name)
            if entry.gui_only:
                gui_only.add(name)
        loaded.sort()

    discovered = (
        getattr(registry, "discovered_plugins", None) or {}
        if registry is not None
        else {}
    )
    global_model = (
        getattr(registry, "global_model", None) if registry is not None else None
    )
    active_model = (
        getattr(registry, "active_model", None) if registry is not None else None
    )

    # Removed - loaded THIS session but their source folder is gone now
    # (so they've left the grid). They still count as loaded for this
    # running session (that truth is frozen in the boot manifest), but
    # they won't load next time Nuke starts. "Not in discovered" maps
    # exactly to "no longer a pill in the grid", which is what the user
    # sees.
    removed: set[str] = {name for name in loaded if name not in discovered}

    # Missing - plugins that were ACTIVELY LOADED THIS SESSION whose source
    # folder no longer resolves on disk. Plugins unique to a removed folder
    # that were actively loaded this session become Missing; Plugins not
    # actively loaded simply disappear from the list. The "loaded this
    # session" gate (``name in loaded``, derived from session_loaded_baseline)
    # guards a false-positive: adding a Plugins Folder then removing it
    # WITHOUT a save/restart leaves the never-loaded
    # plugin names in active_model.plugins, which would otherwise be
    # wrongly reported as
    # Missing. A never-loaded plugin that's gone from the loadout is just gone
    # - it disappears silently, it was never "missing" from a running session.
    loaded_set = set(loaded)
    missing_set: set[str] = set()
    for src in (active_model, global_model):
        if src is None:
            continue
        for name in src.plugins.keys():
            if name not in loaded_set:
                # Never loaded this session -> not Missing, just gone.
                continue
            in_discovery = name in discovered
            in_global = (
                global_model is not None and name in global_model.plugins
            )
            # Global-resident plugins are always considered resolvable
            # (the global resolver already validated their paths).
            if not (in_discovery or in_global):
                missing_set.add(name)
    missing = sorted(missing_set)

    parts: list[str] = []
    parts.append(
        f"<p><b>Loaded this session ({len(loaded)}):</b></p>"
    )
    if loaded:
        items = "".join(
            _loaded_row(name, name in gui_only, name in removed)
            for name in loaded
        )
        parts.append(f"<ul>{items}</ul>")
        if removed:
            parts.append(
                "<p><i>{n} plugin(s) loaded this session but their source "
                "folder is gone now (shown in red). They still count as "
                "loaded for this running session, but won't load next time "
                "Nuke starts.</i></p>".format(n=len(removed))
            )
        if gui_only:
            parts.append(
                "<p><i>GUI-only plugins load when Nuke runs in GUI mode; "
                "they're skipped in terminal and render sessions.</i></p>"
            )
        parts.append(
            "<p><i>Toggle any plugins On/Off. Save the Loadout, and "
            "restart Nuke for the change to take effect.</i></p>"
        )
    elif grid_has_pills:
        parts.append(
            "<p><i>Save Loadout and restart to load enabled "
            "Plugins.</i></p>"
        )
    else:
        parts.append(
            "<p><i>Nothing loaded yet. "
            "Add a Plugins Folder to get started.</i></p>"
        )

    if missing:
        items = "".join(
            f"<li>{_escape(name)}</li>" for name in missing
        )
        parts.append(
            f"<p><b>Missing ({len(missing)}):</b></p><ul>{items}</ul>"
        )

    global_loadout_error = (
        getattr(registry, "global_loadout_error", None)
        if registry is not None
        else None
    )
    if global_loadout_error:
        parts.append(
            "<p><b>Warning:</b> the Global Loadout file is unreadable, "
            "so every Global plugin folder loaded instead. Global "
            "On/Off choices are not applied this session.</p>"
        )

    return "".join(parts)


def _loaded_row(name: str, is_gui_only: bool, is_removed: bool) -> str:
    """One ``<li>`` for the 'Loaded this session' list.

    A removed plugin (loaded this session, but its source folder is gone
    now) renders reddish with a '- removed' tag in the same red as the
    '-N' pending-remove counter chip, so the user can see at a glance
    which loaded plugins won't exist next time Nuke starts. GUI-only and
    removed are independent: a row can carry both tags.
    """
    label = _escape(name)
    if is_removed:
        label = f'<span style="color:{_REMOVED_RED}">{label}</span>'
    return f"<li>{label}{_gui_tag(is_gui_only)}{_removed_tag(is_removed)}</li>"


def _removed_tag(is_removed: bool) -> str:
    """Reddish '- removed' suffix for a loaded-plugin row, or '' if not.

    Marks a plugin that loaded this session but whose source folder is no
    longer on disk (it has left the grid). It still counts as loaded for
    this running session, but will not load next time Nuke starts. Same
    red as the '-N' pending-remove chip so the two read as one story.
    """
    if not is_removed:
        return ""
    return f' <span style="color:{_REMOVED_RED}">- removed</span>'


def _gui_tag(is_gui_only: bool) -> str:
    """Muted '- GUI-only' suffix for a loaded-plugin row, or '' if not.

    Marks plugins the loadout flagged ``gui=True`` (rendered as
    ``if gui and not nuke.GUI: skip`` in the loadout init.py) - they load
    in Nuke's GUI mode but are skipped in terminal and render sessions.
    """
    if not is_gui_only:
        return ""
    return f' <span style="color:{_GUI_PURPLE}">- GUI-only</span>'


def _escape(text: str) -> str:
    """Minimal HTML escape - keep it dependency-free."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _plugin_key_union(registry: "Registry") -> list:
    """Union of plugin names that should render as pills, alpha-sorted.

    Three live sources contribute keys:

    * ``registry.discovered_plugins`` - plugins the scanner found in
      the configured Plugins Folders this scan.
    * ``registry.global_model.plugins`` - plugins listed in the
      resolved Global Global Loadout.
    Active-loadout (``registry.active_model``) entries do NOT contribute
    keys on their own. Under the sparse loadout-file model the file holds
    only *exceptions* (disabled / gui-only); a disabled entry whose source
    folder is gone (removed or ``_``-ignored before boot) is stale data,
    not a dependency-loss signal. Showing it as a Missing pill while the
    diff/banner's orphan filter (see :attr:`Registry.resolved_active_for_diff`)
    ignored it produced a contradictory "marked for removal, nothing pending"
    state. So an active entry shows ONLY when it also has a live source -
    i.e. it's discovered on disk now, or it's a Global/Global plugin
    (both already contribute via the unions above). Global plugins
    that vanish still surface as Missing via the Global union; user-loadout
    orphans simply don't render.
    """
    keys: set = set()
    discovered = getattr(registry, "discovered_plugins", None)
    if discovered:
        keys.update(discovered.keys())
    if registry.global_model is not None:
        keys.update(registry.global_model.plugins.keys())
    # Drop keys whose source folder is currently hidden so visibility
    # survives every refresh path (pill toggle,
    # loadout switch). Keys without a discovered source (active/global
    # only) are unaffected.
    visibility = getattr(registry, "folder_visibility", {}) or {}
    if visibility and discovered:
        keys = {
            k for k in keys
            if k not in discovered
            or visibility.get(discovered[k].source, True)
        }
    return sorted(keys)


# ---------------------------------------------------------------------------
# Default pill factory - used when the wiring layer hasn't provided one yet.
# ---------------------------------------------------------------------------


def _default_pill_factory(key: str):
    """Return a placeholder :class:`PluginPill` for a key.

    The wiring layer (sort / filter / status routing) is responsible for
    producing real pills bound to real Plugin state. This factory is the
    fallback used when no registry-aware factory is installed.
    """
    state = PillState(plugin_name=key)
    return PluginPill(state)

