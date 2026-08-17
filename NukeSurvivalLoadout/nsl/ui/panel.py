"""Top-level NSL Loadout Panel - composes the region widgets.

:meth:`LoadoutPanel._build_main` documents the widget hierarchy.

Conventions:

* Qt imports go only via :mod:`nsl.compat`, never ``import PySide2`` or
  ``import PySide6``.
* Layout state is per-session and never persisted.
* ``Reset panel`` touches splitter sizes and collapsed states only.
* No ``import nuke``. Nuke integration lives in top-level ``menu.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from nsl import compat

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
# Same purple as the "GUI:" counter chip and the pill GUI badge.
from nsl.ui.grid_toolbar import _COUNTER_PURPLE as _GUI_PURPLE
# Same red as the "-N" pending-remove chip. Both mark the same plugins.
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
# Default splitter geometry
# ---------------------------------------------------------------------------

_DEFAULT_FOLDER_SIDE_SPLIT = (60, 40)
_FOLDER_SIDE_SNAP_TOLERANCE = 0.025

# Panes: folder/side pair, discovery block, pill grid. Qt re-normalises
# these on ``setSizes``, so they are a proportion and not pixels.
_DEFAULT_VERTICAL_SPLIT = (260, 110, 1040)


# ---------------------------------------------------------------------------
# Empty-state grid backdrop
# ---------------------------------------------------------------------------


class _EmptyStatePage(QtWidgets.QWidget):
    """Empty-state page that paints the populated grid's divider lines.

    Uses the grid's own ``compute_columns`` and ``cell_widths`` so the
    two match. The panel paints the background through the palette, and
    ``paintEvent`` runs after that fill.
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

            y = GRID_MARGIN_V + CELL_HEIGHT
            while y < h:
                painter.drawLine(0, y, w, y)
                y += CELL_HEIGHT

            # Column boundaries only. No line down the left or right edge.
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

    Region widgets are public attributes so the ``wire_<module>(panel)``
    helpers can reach them.

    Splitters, used by :meth:`reset_panel_layout`:

    * :attr:`_folder_side_split` - left column and side panel. Active,
      and snaps back to the 60 / 40 default.
    * :attr:`_vertical_split` - pair, discovery block, grid. Only the
      divider above the discovery block is active.
    * :attr:`_left_col_split` - folder card over Loadout strip. Locked.

    Reset panel replays the sizes captured at build time. It never
    touches domain state.
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

        # Leaving this ``None`` puts wiring in a soft mode that swallows
        # errors. See the branch in :meth:`_wire_signals`.
        self.registry = registry

        self.top_toolbar = TopToolbar(self)
        self.banner = Banner(self)
        self.grid_counters = GridCounterStrip(self)
        self.loadout_strip = LoadoutStrip(self)
        self.search_tags = SearchTagsStrip(self)
        self.grid_toolbar = PluginsGridToolbar(self)
        self.folder_card = FolderCard(self)
        # SidePanel is not a QWidget. It wraps one on ``.widget`` and
        # holds the QTabWidget on ``.tabs``.
        self.side_panel = SidePanel(self)

        if pill_factory is None:
            if self.registry is not None:
                pill_factory = self._registry_pill_factory
            else:
                pill_factory = _default_pill_factory
        self._pill_factory = pill_factory
        self.grid = PluginsGrid(list(grid_keys or []), pill_factory, self)

        self._build_main()

        # Seed the Reset targets from the ratio tuples, for consumers
        # that never get a ``showEvent``. :meth:`showEvent` then
        # overwrites them with the sizes Qt actually laid out.
        self._default_folder_side_sizes = list(_DEFAULT_FOLDER_SIDE_SPLIT)
        self._default_vertical_sizes = list(_DEFAULT_VERTICAL_SPLIT)
        self._default_sizes_captured = False
        self._default_side_panel_tab_index = self.side_panel.tabs.currentIndex()

        # Attach before wiring runs, so an ``apply_op_result`` fired
        # during wiring still repaints the widgets.
        if self.registry is not None:
            self.registry.attach_refresh(self.refresh_from_registry)
            self.registry.attach_parent_widget(self)
            # The side-panel refresh button re-reads the README and
            # menu.py for the Info and Menu tabs. It runs no rescan.
            if hasattr(self.side_panel, "set_refresh_callback") and hasattr(
                self.registry, "on_side_panel_refresh"
            ):
                self.side_panel.set_refresh_callback(
                    self.registry.on_side_panel_refresh
                )

        self._wire_signals()

        if self.registry is not None:
            self.refresh_from_registry()

    # ----- layout ----------------------------------------------------------

    def _build_main(self) -> None:
        """Construct the panel composition.

        Hierarchy:

        * Outer QVBoxLayout
          - TopToolbar, never wrapped in a SectionBox
          - ``_vertical_split``, three panes:
            * ``_folder_side_split``, two panes:
              - ``_left_col_split`` - FolderCard over LoadoutStrip
              - SidePanel.widget
            * discovery_block - SearchTagsStrip over PluginsGridToolbar,
              then the counters row. Fixed height.
            * grid_stack - the populated grid, or the empty-state page.
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

        self._left_col_split = HairlineSplitter(QtCore.Qt.Vertical, self)
        self._left_col_split.setObjectName("NSLLeftColSplit")
        self._left_col_split.setHandleWidth(6)
        self._left_col_split.setChildrenCollapsible(True)
        # SectionBox draws the bounding line, so the card's own
        # StyledPanel frame would double-paint.
        self.folder_card.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._left_col_split.addWidget(SectionBox(self.folder_card, self))
        self._left_col_split.addWidget(SectionBox(self.loadout_strip, self))
        self._left_col_split.setStretchFactor(0, 1)
        self._left_col_split.setStretchFactor(1, 0)
        self._left_col_split.setCollapsible(1, False)
        # Locked divider. Neither side benefits from a drag.
        self._left_col_split.handle(1).setEnabled(False)
        self._left_col_split.handle(1).setCursor(QtCore.Qt.ArrowCursor)

        self._folder_side_split.addWidget(self._left_col_split)
        self._folder_side_split.addWidget(
            SectionBox(self.side_panel.widget, self)
        )
        self._folder_side_split.setStretchFactor(0, 1)
        self._folder_side_split.setStretchFactor(1, 1)
        self._folder_side_split.setSizes(list(_DEFAULT_FOLDER_SIDE_SPLIT))
        self._folder_side_split._snap_ratio = tuple(_DEFAULT_FOLDER_SIDE_SPLIT)
        self._folder_side_split._snap_tolerance = _FOLDER_SIDE_SNAP_TOLERANCE
        self._folder_side_split.splitterMoved.connect(
            self._on_folder_side_moved
        )
        self._folder_side_split.splitterMoved.connect(
            lambda *_: maybe_snap_splitter(self._folder_side_split)
        )

        # 3. Discovery block - search/tags strip, grid toolbar and
        # counter strip as one fixed-height unit. The counters sit here
        # and not under the banner, so the banner never covers them.
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

        # Counters and banner share one row. Only the banner stretches,
        # so hiding it leaves the chips where they are.
        counters_row = QtWidgets.QWidget(self)
        counters_row.setObjectName("NSLCountersRow")
        counters_row.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        counters_row_layout = QtWidgets.QHBoxLayout(counters_row)
        counters_row_layout.setContentsMargins(0, 0, 0, 0)
        counters_row_layout.setSpacing(8)
        counters_row_layout.addWidget(self.grid_counters)
        counters_row_layout.addWidget(self.banner, 1)
        # Cap the banner at the group-divider gutter height so it never
        # grows the counters row.
        from nsl.ui.grid import GROUP_DIVIDER_HEIGHT as _GUTTER_H
        _banner_h = min(_GUTTER_H, self.grid_counters.sizeHint().height())
        self.banner.setFixedHeight(_banner_h)
        discovery_layout.addWidget(counters_row)
        discovery_section = SectionBox(discovery_block, self)

        # 4. Grid stack - populated grid and empty state.
        grid_container = QtWidgets.QWidget(self)
        grid_container.setObjectName("NSLGridContainer")
        gc_layout = QtWidgets.QVBoxLayout(grid_container)
        gc_layout.setContentsMargins(0, 0, 0, 0)
        gc_layout.setSpacing(0)
        gc_layout.addWidget(self.grid)
        self._grid_container = grid_container

        # The banner sits in the counters row above, not in the grid pane.
        self.banner.hide()

        # Backdrop set through the palette, not QSS. QSS on a parent
        # breaks native rendering in the children.
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
        # Page 0 is the populated grid. Page 1 is the empty state.
        self._grid_stack.addWidget(grid_container)
        self._grid_stack.addWidget(self._empty_state_page)

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
        # ``QSizePolicy.Fixed`` is not enough inside a QSplitter. Qt still
        # gives the pane more than its sizeHint when height is left over.
        discovery_section.adjustSize()
        discovery_section.setFixedHeight(discovery_section.sizeHint().height())
        self._vertical_split.addWidget(self._folder_side_split)
        self._vertical_split.addWidget(discovery_section)
        self._vertical_split.addWidget(SectionBox(grid_pane, self))
        self._vertical_split.setStretchFactor(0, 1)
        self._vertical_split.setStretchFactor(1, 0)
        self._vertical_split.setStretchFactor(2, 4)
        self._vertical_split.setCollapsible(1, False)
        # Handle 2 is locked. The discovery block is fixed-height, so the
        # only useful divider here is handle 1, above it.
        self._vertical_split.handle(2).setEnabled(False)
        self._vertical_split.handle(2).setCursor(QtCore.Qt.ArrowCursor)
        # Do not call setSizes here. Stretch factors and sizeHints give
        # the pair its natural height. Absolute sizes squash the pair on
        # a short panel. ``reset_panel_layout`` does call setSizes.

        # QSS goes on each splitter, never on ``self``. Root-level QSS
        # breaks native paint in the children.
        self._folder_side_split.setStyleSheet(HANDLE_QSS)
        self._left_col_split.setStyleSheet(HANDLE_QSS)
        self._vertical_split.setStyleSheet(HANDLE_QSS)

        outer.addWidget(self._vertical_split, 1)

        # Floating Close button - no parent layout, anchored by
        # :meth:`resizeEvent`. ``self.close()`` runs the same
        # unsaved-changes guard as the title-bar close.
        self.close_button = QtWidgets.QPushButton("Close", self)
        self.close_button.setObjectName("NSLCloseButton")
        self.close_button.setAutoDefault(False)
        self.close_button.setDefault(False)
        self.close_button.raise_()
        self._reposition_close_button()

        self.folder_card.remove_confirmed.connect(
            lambda *_: self._refresh_grid_stack()
        )
        # Sync now, in case the caller seeded folder entries already.
        self._refresh_grid_stack()

        # Qt re-normalises ``setSizes`` against child sizeHints, so the
        # call above drifts. ``resizeEvent`` re-applies 60/40 until the
        # user drags the divider.
        self._folder_side_user_dragged = False

    def _on_folder_side_moved(self, *args) -> None:
        """Mark the divider user-controlled so resizes stop forcing 60/40."""
        self._folder_side_user_dragged = True

    # ------------------------------------------------------------------
    # Floating Close button anchoring
    # ------------------------------------------------------------------

    def _reposition_close_button(self) -> None:
        """Anchor the floating Close button to the bottom-right corner.

        The right margin is wider than the chrome margin so the button
        clears the grid's vertical scrollbar.
        """
        btn = getattr(self, "close_button", None)
        if btn is None:
            return
        hint = btn.sizeHint()
        bottom_margin = 8
        # 16 px for the scrollbar plus an 8 px gap, on top of the 8 px
        # chrome margin.
        right_margin = 8 + 24
        x = self.width() - hint.width() - right_margin
        y = self.height() - hint.height() - bottom_margin
        btn.setGeometry(x, y, hint.width(), hint.height())

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        # ``sizes()`` returns zeros during ``__init__``. The first
        # ``showEvent`` runs after Qt's first layout pass, so it reads
        # the real pixel sizes.
        if not getattr(self, "_default_sizes_captured", False):
            try:
                folder_sizes = self._folder_side_split.sizes()
                vertical_sizes = self._vertical_split.sizes()
            except AttributeError:
                folder_sizes = []
                vertical_sizes = []
            # Commit only a non-zero read, or Reset would restore a
            # broken layout. The flag stops a later show overwriting it.
            if all(s > 0 for s in folder_sizes) and all(
                s > 0 for s in vertical_sizes
            ):
                self._default_folder_side_sizes = list(folder_sizes)
                self._default_vertical_sizes = list(vertical_sizes)
                self._default_sizes_captured = True

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._reposition_close_button()
        # Re-apply 60/40 until the user drags. Read the width off the
        # vertical split, because the pair's own width is stale during
        # the outer resize event.
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
        """Pick the populated or the empty page from the grid key count.

        Keyed on the grid keys, not ``folder_card.row_count()``. A
        Global layer with no user folders still fills the grid, and a
        row count would wrongly show the empty page.
        """
        has_plugins = len(self.grid.keys()) > 0
        page = 0 if has_plugins else 1
        if self._grid_stack.currentIndex() != page:
            self._grid_stack.setCurrentIndex(page)
        self.folder_card.set_first_run_affordance(not has_plugins)

    def refresh_grid_stack(self) -> None:
        """Public hook for the wiring layer to re-evaluate the stack page.

        FolderCard emits no signal for a programmatic
        ``set_entries(...)``, so the caller must call this itself.
        """
        self._refresh_grid_stack()

    # ----- public API the wiring layer uses --------------------------------

    def rebuild_grid(self, keys, pill_factory=None) -> None:
        """Replace the grid contents with a new key list and factory.

        Splitter sizes and collapsed states are not affected.
        """
        if pill_factory is None:
            pill_factory = self._pill_factory
        else:
            self._pill_factory = pill_factory

        old_grid = self.grid
        parent = old_grid.parentWidget()
        layout = parent.layout() if parent is not None else None
        new_grid = PluginsGrid(list(keys), pill_factory, parent)
        if layout is not None:
            layout.replaceWidget(old_grid, new_grid)
        old_grid.setParent(None)
        old_grid.deleteLater()
        self.grid = new_grid
        try:
            new_grid.selection_changed.connect(self._on_grid_selection_changed)
        except AttributeError:
            pass

    def reset_panel_layout(self) -> None:
        """Restore the default layout and clear in-panel session state.

        Splitter sizes, the side panel tab, the filter, the pill
        selection and the sort mode. It never touches domain state.
        Every branch is a no-op when already at its default, so Reset
        at the default layout changes nothing.
        """
        if getattr(self, "_default_sizes_captured", False):
            self._folder_side_split.setSizes(
                list(self._default_folder_side_sizes)
            )
            self._vertical_split.setSizes(
                list(self._default_vertical_sizes)
            )
        else:
            # No showEvent capture yet, so fall back to ratio math.
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
        # Re-arm the 60/40 lock until the user drags again.
        self._folder_side_user_dragged = False
        self.side_panel.tabs.setCurrentIndex(self._default_side_panel_tab_index)

        # Deselect first, so the sort and filter rebuilds below do not
        # re-emit a stale selection.
        try:
            grid = getattr(self, "grid", None)
            if grid is not None and hasattr(grid, "clear_selection"):
                grid.clear_selection()
        except Exception:  # noqa: BLE001 - reset must not raise on any one branch
            pass
        try:
            search_tags = getattr(self, "search_tags", None)
            if search_tags is not None and hasattr(search_tags, "clear_filter"):
                search_tags.clear_filter()
        except Exception:  # noqa: BLE001
            pass
        try:
            from nsl.ui.grid_toolbar import SortMode
            grid_toolbar = getattr(self, "grid_toolbar", None)
            if grid_toolbar is not None and hasattr(grid_toolbar, "set_sort_mode"):
                grid_toolbar.set_sort_mode(SortMode.A_TO_Z)
        except Exception:  # noqa: BLE001
            pass
        # Restore every folder card eye to visible. Clear the registry
        # map and the filter pipeline, then refresh once.
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
        """Apply *ratios* to *splitter*, scaled to its current size.

        A bare ``setSizes`` lets the stretch factors take over and pad
        unevenly. Scaling each ratio to the current size is exact.
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

        The ``wire_<module>(self)`` helpers run in the P5 block below.
        """
        self.top_toolbar.reset_panel_requested.connect(self.reset_panel_layout)

        try:
            self.grid.selection_changed.connect(self._on_grid_selection_changed)
        except AttributeError:
            pass

        # The visible panic control is in the top toolbar, but
        # ``loadout_strip.panic_toggled`` is the contract downstream.
        self.top_toolbar.panic_toggled.connect(
            self.loadout_strip.btn_panic.setChecked
        )
        # Reverse, so ``loadout_strip.set_panic_engaged`` moves the button.
        self.loadout_strip.btn_panic.toggled.connect(
            self.top_toolbar.set_panic_engaged
        )

        # === BEGIN P5 WIRING ===
        # With no registry the wiring may fail on missing state. Print
        # the trace and let construction finish.
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
                # Without ``key_to_folder`` the folder eyes record state
                # but hide nothing.
                wire_filter_pipeline(
                    self, key_to_folder=build_key_to_folder(self)
                )
                # Must come after ``wire_filter_pipeline``. It pushes the
                # sort state lookup into the pipeline.
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
        """Re-emit widget state from ``self.registry`` after a mutation.

        Called by :meth:`Registry.apply_op_result` and once at
        construction. Reads the helpers in :mod:`nsl.ui.state` and
        pushes the result through each region's own setter.

        No-op when ``self.registry`` is None.
        """
        if self.registry is None:
            return

        registry = self.registry

        # Panic greys most of the panel. The loadout strip, top toolbar
        # and side panel stay enabled, so Save and release still work.
        panic_engaged = bool(getattr(registry.state, "panic", False))
        # A per-row setter, not a blanket ``setEnabled``. Add and Rescan
        # keep working, and the Global row stays lit because Global
        # plugins still load in panic.
        self.folder_card.set_panic_engaged(panic_engaged)
        # The grid stays enabled so the info buttons still work. Each
        # pill gates its own toggle zones on ``PillState.panic_engaged``
        # and USER_ADDED.
        self.grid_toolbar.setEnabled(not panic_engaged)
        self.search_tags.setEnabled(not panic_engaged)
        # Push panic to the top-toolbar button too. Without it a panel
        # reopened with panic saved shows a grey grid and an unchecked
        # button. ``set_panic_engaged`` does not emit, so nothing loops
        # back into ``_on_panic_toggled``.
        self.top_toolbar.set_panic_engaged(panic_engaged)
        self.loadout_strip.set_panic_engaged(panic_engaged)

        self._apply_panic_grid_visual(panic_engaged)

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

        empty = len(_plugin_key_union(registry)) == 0
        # Custom cannot commit, so the saved-change visuals must not
        # paint on it. This flag reaches ``pill_state_from`` and the
        # banner. See state.py for the per-pill effect.
        from nsl.constants import DEFAULT_CUSTOM_LOADOUT_STEM as _CUSTOM_STEM
        active_is_custom = (
            registry.state is not None
            and registry.state.active == _CUSTOM_STEM
        )
        self._grid_stack.setCurrentIndex(1 if empty else 0)
        # This path never calls ``_refresh_grid_stack``, so flip the
        # first-run border here too.
        self.folder_card.set_first_run_affordance(empty)

        # The Reset Global Plugins button is hidden, not disabled, when
        # no Global layer resolves.
        global_layer_active = bool(
            registry.global_model is not None
            and registry.global_model.plugins
        )
        self.search_tags.set_global_layer_active(global_layer_active)
        if hasattr(self.search_tags, "set_reset_global_enabled"):
            diverged = 0
            if hasattr(registry, "count_diverged_global_plugins"):
                try:
                    diverged = registry.count_diverged_global_plugins()
                except Exception:  # noqa: BLE001 - counter must not break refresh
                    diverged = 0
            self.search_tags.set_reset_global_enabled(diverged > 0)

        try:
            # Use ``empty``, not ``self.grid.keys()``. The grid is
            # rebuilt further down, so its keys are still the old set.
            self.side_panel.set_summary(
                _session_summary_html(
                    grid_has_pills=not empty, registry=registry
                ),
                html=True,
            )
        except Exception:  # noqa: BLE001 - summary update must not break refresh
            pass

        current_for_diff = registry.resolved_active_for_diff
        diff = pending_diff(
            current_active=current_for_diff,
            saved_baseline=registry.session_loaded_baseline,
        )
        # The banner is not inside the grid subtree, so hide it here
        # when the empty page is showing.
        empty_page = self._grid_stack.currentIndex() == 1
        # Banner state, in priority order:
        #   1. PANIC_ENGAGED          - panic wins. The count is unused.
        #   2. hidden                 - count is 0. It never says "0".
        #   3. PENDING_CHANGES        - count > 0 and dirty, or Custom.
        #   4. SAVED_AWAITING_RESTART - count > 0 and clean on disk.
        if empty_page:
            self.banner.hide()
        elif panic_engaged:
            # The Global-plugins sentence needs a resolved Global layer.
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
            self.banner.set_state(BannerKind.SAVED_AWAITING_RESTART, diff.count)
            self.banner.show()
            self.banner.raise_()

        # ``rewire_grid_pills`` connects new lambdas on each call, so it
        # runs only after a real rebuild. The master key list is stashed
        # for the pipeline, because ``grid.keys()`` is the filtered set.
        new_keys = _plugin_key_union(registry)
        self._all_plugin_keys = list(new_keys)

        # The counters read the same diff math as the banner, so the two
        # never disagree.
        selected_count = 0
        try:
            selected_count = len(self.grid.selected_keys())
        except Exception:  # noqa: BLE001 - counter strip is informational only.
            selected_count = 0
        pending_add, pending_del = pending_diff_split(
            current_active=current_for_diff,
            saved_baseline=registry.session_loaded_baseline,
        )
        gui_only_count = _count_gui_only(current_for_diff)
        loaded_session = self._count_loaded_session()
        # The Logs chip counts problem Plugins. There is no failure
        # surface yet, so the argument below stays 0.
        self.grid_counters.set_counters(
            selected_count,
            len(new_keys),
            pending_add,
            pending_del,
            gui_only_count,
            0,
            loaded_session,
        )
        # Always route through the pipeline, even with no filter query.
        # ``_plugin_key_union`` returns A to Z, so a bare ``set_keys``
        # here would reset the user's sort on every refresh.
        pipeline = getattr(self, "filter_pipeline", None)
        # ``_apply_visibility`` re-pushes pill states after a rebuild.
        # This method pushes below anyway, so let the pipeline skip it.
        self._in_registry_refresh = True
        try:
            if pipeline is not None:
                pipeline._recompute_and_apply()
            elif self.grid.set_keys(new_keys):
                from nsl.ui.wiring.events import rewire_grid_pills

                rewire_grid_pills(self)
        finally:
            self._in_registry_refresh = False

        self._set_pills_from_registry()

        from nsl.ui.wiring.events import _sync_undo_toolbar

        _sync_undo_toolbar(self)


    def _set_pills_from_registry(self) -> None:
        """Recompose and push per-pill visual state onto the grid.

        One pass over the visible pills: replay ``pill_state_from``,
        mirror the pending-restart signal onto the cell diff-tint, and
        re-apply the panic dim. The filter pipeline calls this after its
        own rebuilds, because only this pass sets the cell washes and
        the panic opacity.
        """
        registry = self.registry
        if registry is None:
            return
        panic_engaged = bool(getattr(registry.state, "panic", False))
        from nsl.constants import (  # noqa: PLC0415
            DEFAULT_CUSTOM_LOADOUT_STEM as _CUSTOM_STEM,
        )
        active_is_custom = (
            registry.state is not None
            and registry.state.active == _CUSTOM_STEM
        )
        # ``set_keys`` runs the factory only when the key list changes.
        # A pill toggle keeps the same keys. The pills then hold stale
        # state until this pass pushes a fresh one to each.
        try:
            grid_keys = self.grid.keys()
            grid_pills = list(getattr(self.grid, "_pills", []))
            grid_cells = list(getattr(self.grid, "_cells", []))
        except Exception:  # noqa: BLE001 - refresh must never raise.
            grid_keys, grid_pills, grid_cells = [], [], []
        if len(grid_keys) == len(grid_pills):
            global_names = registry.global_plugin_names
            # There is no per-pill failed state. Nuke's NUKE_PATH walker
            # is the loader, and a failing plugin stops Nuke from
            # starting, so the panel never opens.
            selected_keys = set(self.grid.selected_keys())
            # A plugin in ``session_loaded_baseline`` was on NUKE_PATH at
            # boot. Anything else loads on the next restart only. That
            # is what paints the green pending-enable tint.
            session_loaded = registry.session_loaded_baseline
            loaded_set: frozenset = (
                frozenset(session_loaded.plugins.keys())
                if session_loaded is not None
                else frozenset()
            )
            # The cell wash matches the pill's ``_pending_border_color``
            # so the pending signal also reads at the cell padding.
            cell_tint_load = QtGui.QColor(*CELL_DIFF_BG_LOAD_RGBA)
            cell_tint_unload = QtGui.QColor(*CELL_DIFF_BG_UNLOAD_RGBA)
            cell_tint_gui = QtGui.QColor(*CELL_DIFF_BG_GUI_ON_RGBA)
            for idx, (key, pill) in enumerate(zip(grid_keys, grid_pills)):
                loaded = key in loaded_set
                diagnostic_available = False
                failure_label = None
                # The source folder is no longer scanned and the plugin
                # is not in the Global layer. Paints the yellow hazard body.
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
                    # The active Loadout's saved-on-disk baseline, not
                    # ``session_loaded_baseline``. That one is the
                    # banner's.
                    saved_baseline=registry.active_saved_baseline,
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
                    # Panic drops the saved glow on USER_ADDED pills.
                    # They will not load, so the glow would be wrong.
                    # Global pills keep theirs.
                    panic_engaged=panic_engaged,
                    # Custom cannot commit, so every pill takes the
                    # dirty path and falls back to the white visual.
                    active_is_custom=active_is_custom,
                )
                fresh = pill_state_from(key, **kwargs)
                try:
                    pill.set_state(fresh)
                except Exception:  # noqa: BLE001 - one bad pill must not block the rest.
                    continue

                # Identity compare. ``_pending_border_color`` returns
                # the same class-level QColor objects every time.
                if idx < len(grid_cells):
                    cell = grid_cells[idx]
                    pending = pill._pending_border_color()
                    if pending is Palette.BORDER_PENDING_ENABLE:
                        cell.set_diff_tint(cell_tint_load)
                    elif pending is Palette.BORDER_PENDING_DISABLE:
                        cell.set_diff_tint(cell_tint_unload)
                    elif fresh.gui_pending_on and fresh.gui_committed:
                        # A committed GUI off-to-on gets a purple wash.
                        # An unsaved flip keeps the lit chip instead.
                        cell.set_diff_tint(cell_tint_gui)
                    else:
                        cell.set_diff_tint(None)

        # Re-apply after the pills are rebuilt. On a fresh open the call
        # in ``refresh_from_registry`` runs against an empty grid, so
        # those pills would carry no opacity.
        self._apply_panic_grid_visual(panic_engaged)

    def _apply_panic_grid_visual(self, engaged: bool) -> None:
        """Dim USER_ADDED pills with per-pill opacity when panic is on.

        Per-pill and not grid-wide, because Global plugins still load
        in panic. A key in ``registry.global_plugin_names`` is GLOBAL,
        the same rule :func:`nsl.ui.state.pill_state_from` uses.

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
        """Push ``info_active`` and ``menu_active`` to every pill.

        Both flags go out together so only one pill and chip stays lit.
        Pass ``info_plugin=name, menu_plugin=None`` on an info click,
        and the reverse on a menu click. Pass both ``None`` to clear.

        A pill paints from its own ``_state``, so every pill needs the
        push, not only the clicked one.
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
        """Update the counter strip's Selected chip and the pill rings.

        A full refresh is too much for a selection change, so the strip
        is rebuilt here and keeps its other chip values.

        The pill's orange ring paints from ``PillState.selected``, and
        the cell halo is a separate surface. Both need the push.
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
        """Count the plugins NSL loaded into this Nuke session.

        Reads ``registry.session_loaded_baseline``, the boot-time
        manifest. The count is a session total and never intersects
        with the visible grid, so filtering the grid or removing a
        folder does not move it.
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

        Derives the :class:`PillState` from live registry state, so the
        first paint is right. ``refresh_from_registry`` pushes fresh
        state on every mutation after that.
        """
        registry = self.registry
        if registry is None:
            return _default_pill_factory(key)
        from nsl.constants import DEFAULT_CUSTOM_LOADOUT_STEM  # noqa: PLC0415
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
            saved_baseline=registry.active_saved_baseline,
            force_dirty_plugins=getattr(
                registry, "force_dirty_plugins", frozenset()
            ),
            source_missing=source_missing,
            loaded_in_session=loaded,
            # The factory is the only state source when the pipeline
            # rebuilds the grid. Without this the GUI diff indicators
            # disappear on a sort or filter change.
            session_gui_only=(
                session_loaded.plugins[key].gui_only
                if session_loaded is not None
                and key in session_loaded.plugins
                else None
            ),
            diagnostic_available=diagnostic_available,
            failure_label=failure_label,
            # Read panic off settings, so a panel built in panic mints
            # pills with no saved glow.
            panic_engaged=bool(getattr(registry.state, "panic", False)),
            # Custom cannot commit, so the committed visuals stay off.
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

    Row names are bare stems. With no Global layer there is no
    ``Global`` row, so an empty stem falls back to Custom.
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
    """Render the Side Panel Summary tab content.

    The Summary shows the boot-time effective state, plus a Missing
    count for plugins whose source folders have gone.

    ``grid_has_pills`` picks the body copy when the active Loadout
    declares nothing. It separates an empty panel from one waiting on
    a save and a restart.
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

    # Loaded this session, but the source folder is gone now, so the
    # plugin has left the grid.
    removed: set[str] = {name for name in loaded if name not in discovered}

    # Missing means loaded this session, but the source folder no longer
    # resolves. The ``loaded`` gate stops a folder added and removed
    # without a save from reporting never-loaded plugins as Missing.
    loaded_set = set(loaded)
    missing_set: set[str] = set()
    for src in (active_model, global_model):
        if src is None:
            continue
        for name in src.plugins.keys():
            if name not in loaded_set:
                continue
            in_discovery = name in discovered
            in_global = (
                global_model is not None and name in global_model.plugins
            )
            # The global resolver already validated the Global paths.
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

    A removed plugin renders red with a '- removed' tag. GUI-only and
    removed are independent, so a row can carry both tags.
    """
    label = _escape(name)
    if is_removed:
        label = f'<span style="color:{_REMOVED_RED}">{label}</span>'
    return f"<li>{label}{_gui_tag(is_gui_only)}{_removed_tag(is_removed)}</li>"


def _removed_tag(is_removed: bool) -> str:
    """Reddish '- removed' suffix for a loaded-plugin row, or ''.

    Marks a plugin that loaded this session but whose source folder is
    gone. Same red as the '-N' pending-remove chip.
    """
    if not is_removed:
        return ""
    return f' <span style="color:{_REMOVED_RED}">- removed</span>'


def _gui_tag(is_gui_only: bool) -> str:
    """Muted '- GUI-only' suffix for a loaded-plugin row, or ''.

    Marks plugins the loadout flagged ``gui=True``. They load in Nuke's
    GUI mode and are skipped in terminal and render sessions.
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
    """Union of plugin names that render as pills, sorted A to Z.

    Two sources contribute keys: ``registry.discovered_plugins`` and
    ``registry.global_model.plugins``.

    ``registry.active_model`` contributes none on its own. The loadout
    file is sparse and holds exceptions only, so an entry with no live
    source is stale data. Rendering it gave a Missing pill that the
    banner's orphan filter did not count.
    """
    keys: set = set()
    discovered = getattr(registry, "discovered_plugins", None)
    if discovered:
        keys.update(discovered.keys())
    if registry.global_model is not None:
        keys.update(registry.global_model.plugins.keys())
    # Drop keys whose source folder is hidden, so the eye toggle
    # survives every refresh path.
    visibility = getattr(registry, "folder_visibility", {}) or {}
    if visibility and discovered:
        keys = {
            k for k in keys
            if k not in discovered
            or visibility.get(discovered[k].source, True)
        }
    return sorted(keys)


# ---------------------------------------------------------------------------
# Default pill factory
# ---------------------------------------------------------------------------


def _default_pill_factory(key: str):
    """Return a placeholder :class:`PluginPill` for a key.

    The fallback used when no registry-aware factory is installed.
    """
    state = PillState(plugin_name=key)
    return PluginPill(state)

