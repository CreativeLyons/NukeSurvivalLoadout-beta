"""Plugins grid widget - the cell and pill rendering surface.

Each grid slot is a ``_Cell`` ``QWidget`` holding one pill widget. The
cell paints the selection signal and the diff wash. The pill paints its
own body tint, border and status icon.

``selection_changed(list)`` carries the selected pill keys. The grid
keeps a selection set only to drive cell paint. The canonical model
lives elsewhere.

Marquee capture is any-overlap, not containment, and the drag is clipped
to the viewport rect. Qt comes from :mod:`nsl.compat` only.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

from nsl import compat
from nsl.ui import _theme

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# ---------------------------------------------------------------------------
# Layout defaults
# ---------------------------------------------------------------------------

#: A ceiling only. The grid packs as many columns as fit the viewport.
MAX_COLUMNS = 16

#: Must match ``nsl.ui.pill._MIN_W`` (211 + 2 * 15) or the grid clips
#: pills at the right column edge. Below it the column count drops.
PILL_MIN_WIDTH = 241

#: Passed to :func:`cell_widths`, which ignores it. No part of the
#: layout reads this value.
PILL_MAX_WIDTH = 380

#: Both axes are 0. The only gap between pill bodies is the pill's own
#: shadow margin.
CELL_PADDING = 0
CELL_PADDING_V = 0

#: 0, so the first and last pill columns sit flush with the viewport.
GRID_MARGIN = 0

#: 0, so the top and bottom rows sit flush with the SectionBox line.
GRID_MARGIN_V = 0

#: Must be at least ``nsl.ui.pill._MIN_H + 2 * CELL_PADDING_V``, which is
#: 70 + 2 * 15. Less than that clips the pill's bottom row of paint.
CELL_HEIGHT = 100

#: The gutter band between buckets in a grouping sort mode. It holds the
#: bucket label and a hairline, just below ``GRID_BG_COLOUR``.
GROUP_DIVIDER_HEIGHT = 18


# ---------------------------------------------------------------------------
# Selection-visual colours
# ---------------------------------------------------------------------------

#: The single source is ``_theme.py``.
NUKE_SELECTION_RGB = _theme.NUKE_ORANGE_RGB

SELECTION_BORDER_WIDTH = 3

#: Half the stroke width, so the centered stroke's inner edge sits flush
#: with the pill body edge.
SELECTION_HALO_INSET = SELECTION_BORDER_WIDTH // 2

#: Unused. Marquee hover paints a cell wash and no ring.
MARQUEE_HOVER_WIDTH = 1

#: Unused, with the border it belongs to.
MARQUEE_HOVER_ALPHA = 180

SELECTION_BORDER_ALPHA = 255

#: Quiet, so the wash does not compete with the pill body chrome.
SELECTED_CELL_TINT_ALPHA = 32

#: Fainter than a confirmed selection, so the two states read apart.
MARQUEE_HOVER_TINT_ALPHA = 22

#: Translucent, so cells under the drag stay visible.
MARQUEE_FILL_ALPHA = 50

MARQUEE_OUTLINE_ALPHA = 220

#: Lighter than ``GRID_BG_COLOUR``, so the 1 px dividers read as
#: engraved channels.
CELL_DIVIDER_COLOUR = (74, 74, 74)  # #4a4a4a

#: Recessed below the panel body (`#393939`) and a hair below the search
#: field (`#303030`).
GRID_BG_COLOUR = (45, 45, 45)  # #2d2d2d

#: Cell wash for the pending-restart diff, matching the pill border and
#: glow. Load sits below unload in alpha because lime reads heavier than
#: red.
CELL_DIFF_BG_LOAD_RGBA = (80, 180, 80, 18)
CELL_DIFF_BG_UNLOAD_RGBA = (200, 80, 80, 22)

#: Cell wash for a GUI-only OFF to ON change. Purple, because the plugin
#: still loads in this GUI session and is skipped only on the farm. The
#: load wash wins when a cell has both.
CELL_DIFF_BG_GUI_ON_RGBA = (150, 90, 214, 32)


# ---------------------------------------------------------------------------
# Empty-state placeholder
# ---------------------------------------------------------------------------

# Shown when the grid has no keys, from an empty Loadout or from Search
# and Tag filtering everything out. The first-run "no folders yet" prompt
# is a different surface, in ``nsl.ui.empty_state``.

EMPTY_PLACEHOLDER_TEXT = "No plugins to show."
EMPTY_PLACEHOLDER_COLOUR = "#7a7a7a"


# ---------------------------------------------------------------------------
# Pill protocol + factory typing
# ---------------------------------------------------------------------------


class PillProtocol:
    """Minimal duck-typed pill interface. Any ``QWidget`` satisfies it.

    Declared here so this module does not hard-depend on ``nsl.ui.pill``.
    """

    def setParent(self, parent):  # pragma: no cover - QWidget interface
        ...

    def setGeometry(self, rect):  # pragma: no cover - QWidget interface
        ...

    def show(self):  # pragma: no cover - QWidget interface
        ...


#: Returns a fresh ``QWidget`` on every call, never a cached one.
PillFactory = Callable[[str], "QtWidgets.QWidget"]


# ---------------------------------------------------------------------------
# Pure-Python helpers (testable without PySide)
# ---------------------------------------------------------------------------


def compute_columns(
    viewport_width: int,
    pill_min_width: int = PILL_MIN_WIDTH,
    cell_padding: int = CELL_PADDING,
    grid_margin: int = GRID_MARGIN,
    max_columns: int = MAX_COLUMNS,
) -> int:
    """Return how many columns fit in *viewport_width* px.

    A column needs ``pill_min_width + 2*cell_padding``. The result is
    clamped to ``[1, max_columns]``.
    """
    if viewport_width <= 0:
        return 1
    cell_width = pill_min_width + 2 * cell_padding
    usable = max(0, viewport_width - 2 * grid_margin)
    if cell_width <= 0:
        return max_columns
    n = max(1, usable // cell_width)
    return int(min(max_columns, n))


def cell_widths(
    viewport_width: int,
    columns: int,
    pill_max_width: int = PILL_MAX_WIDTH,
    cell_padding: int = CELL_PADDING,
    grid_margin: int = GRID_MARGIN,
) -> int:
    """Return each cell's width in px for the chosen *columns*.

    Cells split the usable width evenly. ``pill_max_width`` is unused.
    """
    if columns <= 0:
        return 0
    usable = max(0, viewport_width - 2 * grid_margin)
    per = usable // columns
    return int(max(pill_min_cell_width(cell_padding), per))


def pill_min_cell_width(cell_padding: int = CELL_PADDING) -> int:
    """Minimum cell width (one pill at its minimum size)."""
    return PILL_MIN_WIDTH + 2 * cell_padding


def cell_rect(
    index: int,
    columns: int,
    cell_w: int,
    cell_h: int = CELL_HEIGHT,
    grid_margin: int = GRID_MARGIN,
    grid_margin_v: int = GRID_MARGIN_V,
) -> Tuple[int, int, int, int]:
    """Return ``(x, y, w, h)`` for cell *index* in a *columns*-wide grid.

    Top-left origin. Cells flow left to right, then top to bottom.
    """
    if columns <= 0:
        columns = 1
    row, col = divmod(index, columns)
    x = grid_margin + col * cell_w
    y = grid_margin_v + row * cell_h
    return (x, y, cell_w, cell_h)


def grid_content_height(
    n_pills: int,
    columns: int,
    cell_h: int = CELL_HEIGHT,
    grid_margin: int = GRID_MARGIN,  # retained for backwards-compat callers
    grid_margin_v: int = GRID_MARGIN_V,
) -> int:
    """Total content height in px for *n_pills* in a *columns*-wide grid.

    ``grid_margin`` is ignored. It stays for older keyword callers.
    """
    del grid_margin
    if n_pills <= 0:
        return 2 * grid_margin_v
    if columns <= 0:
        columns = 1
    rows = (n_pills + columns - 1) // columns
    return rows * cell_h + 2 * grid_margin_v


def layout_with_dividers(
    group_labels: Sequence[Optional[str]],
    columns: int,
    cell_w: int,
    viewport_w: int,
    *,
    cell_h: int = CELL_HEIGHT,
    divider_h: int = GROUP_DIVIDER_HEIGHT,
    grid_margin: int = GRID_MARGIN,
    grid_margin_v: int = GRID_MARGIN_V,
) -> Tuple[
    List[Tuple[int, int, int, int]],
    List[Tuple[str, int, int, int, int]],
    int,
]:
    """Place cells and group-divider rows into the viewport.

    *group_labels* holds one entry per pill key, in render order. A
    change between consecutive non-``None`` labels finishes the current
    row, drops a divider row, and starts the next bucket at column 0. A
    ``None`` joins the previous bucket, and a leading run emits nothing.

    Returns ``(cell_rects, divider_rects, content_height)``.
    ``cell_rects`` stays aligned with *group_labels*, so ``zip`` is safe.
    ``divider_rects`` holds ``(label, x, y, w, h)`` in document order,
    and is empty when no label changes. All-``None`` input gives the same
    cell geometry as a uniform :func:`cell_rect` layout.
    """
    if columns <= 0:
        columns = 1
    n = len(group_labels)
    cell_rects: List[Tuple[int, int, int, int]] = []
    divider_rects: List[Tuple[str, int, int, int, int]] = []

    if n == 0:
        return ([], [], 2 * grid_margin_v)

    # ``row`` counts pill rows only. The divider offset comes from
    # ``len(divider_rects) * divider_h``.
    row = 0
    col = 0
    last_label: Optional[str] = None
    divider_w = max(0, viewport_w - 2 * grid_margin)

    for label in group_labels:
        if label is not None and label != last_label:
            if last_label is not None and col != 0:
                row += 1
                col = 0
            divider_y = (
                grid_margin_v
                + row * cell_h
                + len(divider_rects) * divider_h
            )
            divider_rects.append(
                (label, grid_margin, divider_y, divider_w, divider_h)
            )
            last_label = label

        # The divider count already includes the one emitted above. The
        # first pill of a bucket then lands under its strip.
        cell_y = (
            grid_margin_v
            + row * cell_h
            + len(divider_rects) * divider_h
        )
        cell_x = grid_margin + col * cell_w
        cell_rects.append((cell_x, cell_y, cell_w, cell_h))

        col += 1
        if col == columns:
            col = 0
            row += 1

    final_rows = row + (1 if col != 0 else 0)
    content_height = (
        grid_margin_v
        + final_rows * cell_h
        + len(divider_rects) * divider_h
        + grid_margin_v
    )
    return (cell_rects, divider_rects, content_height)


def _y_inside_any(y: int, ranges: List[Tuple[int, int]]) -> bool:
    """Return ``True`` if *y* falls strictly inside any ``(y_top, y_bottom)``.

    Strict, so a hairline exactly at a gutter edge still paints.
    :meth:`PluginsGrid._paint_cell_dividers` uses it to skip hairlines
    inside a gutter band.
    """
    for y_top, y_bottom in ranges:
        if y_top < y < y_bottom:
            return True
    return False


def _rects_overlap(
    ra: Tuple[int, int, int, int], rb: Tuple[int, int, int, int]
) -> bool:
    """Return True if two ``(x, y, w, h)`` rects share any pixel.

    Edge-touching counts as overlap.
    """
    ax, ay, aw, ah = ra
    bx, by, bw, bh = rb
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return False
    return not (
        ax + aw < bx or bx + bw < ax or ay + ah < by or by + bh < ay
    )


def marquee_hits(
    marquee: Tuple[int, int, int, int],
    cell_rects: Sequence[Tuple[int, int, int, int]],
) -> List[int]:
    """Return cell indices whose rect overlaps *marquee*. Any overlap counts."""
    mx, my, mw, mh = marquee
    # Normalise to a top-left origin with a positive size.
    if mw < 0:
        mx, mw = mx + mw, -mw
    if mh < 0:
        my, mh = my + mh, -mh
    norm = (mx, my, mw, mh)
    return [i for i, r in enumerate(cell_rects) if _rects_overlap(norm, r)]


def toggle_selection(
    current: Iterable[str], key: str, *, additive: bool = False
) -> Set[str]:
    """Return a new selection set with *key* toggled.

    * ``additive=False`` (plain click): replace selection with ``{key}``.
    * ``additive=True``  (ctrl/cmd-click): toggle *key*'s membership.
    """
    s = set(current)
    if additive:
        if key in s:
            s.discard(key)
        else:
            s.add(key)
    else:
        s = {key}
    return s


# ---------------------------------------------------------------------------
# Cell widget - owns the selection background paint
# ---------------------------------------------------------------------------


class _Cell(QtWidgets.QWidget):
    """One grid slot. Hosts a pill and paints the selection signal.

    A selected cell gets an orange ring just outside the pill rect and a
    low-alpha orange wash. Marquee hover gets the wash only. The pill
    body stays free for the pending-diff tint the pill paints.

    The cell owns this paint, not the pill, so any ``PillFactory`` widget
    works and the ring stays locked to the pill rect.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._selected = False
        self._marquee_hover = False
        # Pending-restart diff wash, set by the panel refresh loop.
        # ``None`` paints nothing and ``GRID_BG_COLOUR`` shows through.
        self._diff_tint: Optional[QtGui.QColor] = None
        # Where the cell paints the selection ring. The grid sets it in
        # ``_relayout``. Empty means paint nothing.
        self._pill_rect = QtCore.QRect()
        # Set by the grid right after construction. ``None`` turns mouse
        # forwarding off, so a standalone cell keeps Qt's behaviour.
        self._grid_ref: Optional["PluginsGrid"] = None
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        # Cells take no focus. Pills and the marquee do.
        self.setFocusPolicy(QtCore.Qt.NoFocus)

    # -- selection / marquee API --

    def set_selected(self, selected: bool) -> None:
        if self._selected == bool(selected):
            return
        self._selected = bool(selected)
        self.update()

    def is_selected(self) -> bool:
        return self._selected

    def set_marquee_hover(self, hover: bool) -> None:
        if self._marquee_hover == bool(hover):
            return
        self._marquee_hover = bool(hover)
        self.update()

    def set_pill_rect(self, rect: "QtCore.QRect") -> None:
        """Tell the cell where its pill sits, in cell-local coordinates."""
        new = QtCore.QRect(rect)
        if self._pill_rect == new:
            return
        self._pill_rect = new
        self.update()

    def set_diff_tint(self, color: Optional[QtGui.QColor]) -> None:
        """Set the cell's pending-restart background wash.

        Green for load on restart, red for unload. ``None`` clears it.
        """
        if self._diff_tint is None and color is None:
            return
        if (
            self._diff_tint is not None
            and color is not None
            and self._diff_tint.rgba() == color.rgba()
        ):
            return
        self._diff_tint = color
        self.update()

    # -- mouse forwarding --
    # A click on the bare cell belongs to the marquee. Forward it to the
    # grid in viewport coordinates. Pills keep their own clicks.

    def mousePressEvent(self, event):  # pragma: no cover - GUI path
        if (
            event.button() == QtCore.Qt.LeftButton
            and self._grid_ref is not None
        ):
            self._grid_ref._press_at(
                self.mapToParent(event.pos()), event.modifiers()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # pragma: no cover - GUI path
        if self._grid_ref is not None:
            self._grid_ref._move_at(self.mapToParent(event.pos()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # pragma: no cover - GUI path
        if (
            event.button() == QtCore.Qt.LeftButton
            and self._grid_ref is not None
        ):
            self._grid_ref._release_at(
                self.mapToParent(event.pos()), event.modifiers()
            )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- paint --

    def paintEvent(self, event):  # pragma: no cover - exercised via .grab()
        has_diff = self._diff_tint is not None
        needs_select_paint = self._selected or self._marquee_hover
        if not (has_diff or needs_select_paint):
            return
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)

            # 1. Diff wash, bottom layer, over the full cell rect. The
            #    pill body paints on top, so its own tint survives.
            if has_diff:
                painter.fillRect(self.rect(), self._diff_tint)

            if needs_select_paint:
                r, g, b = NUKE_SELECTION_RGB

                # 2. Orange wash over the whole cell, so the highlighted
                #    zone reaches the dividers.
                tint_alpha = (
                    SELECTED_CELL_TINT_ALPHA
                    if self._selected
                    else MARQUEE_HOVER_TINT_ALPHA
                )
                painter.fillRect(self.rect(), QtGui.QColor(r, g, b, tint_alpha))

            # 3. Ring around the pill body. Confirmed selection only.
            #    Marquee hover reads from the wash alone.
            if self._selected and not self._pill_rect.isEmpty():
                r, g, b = NUKE_SELECTION_RGB
                painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
                pen = QtGui.QPen(
                    QtGui.QColor(r, g, b, SELECTION_BORDER_ALPHA)
                )
                pen.setWidth(SELECTION_BORDER_WIDTH)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                # Inflate so the line sits outside the pill body. The
                # pill paints on top and would hide it otherwise.
                halo = self._pill_rect.adjusted(
                    -SELECTION_HALO_INSET,
                    -SELECTION_HALO_INSET,
                    SELECTION_HALO_INSET,
                    SELECTION_HALO_INSET,
                )
                # Read the radius from pill.py so the ring follows the
                # pill corner, ``NSL_PILL_RADIUS`` overrides included.
                from nsl.ui.pill import _BORDER_RADIUS as _PILL_RADIUS
                radius = _PILL_RADIUS + SELECTION_HALO_INSET
                painter.drawRoundedRect(halo, radius, radius)
        finally:
            painter.end()


# ---------------------------------------------------------------------------
# Group divider - thin label-plus-hairline strip between sort buckets
# ---------------------------------------------------------------------------

# :class:`PluginsGrid` rebuilds these widgets on every ``_relayout``.
# Their position depends on the viewport width and the row arrangement.

_GROUP_DIVIDER_QSS = (
    # The strip is a gutter band, not a transparent overlay. ``#252525``
    # sits one step below ``GRID_BG_COLOUR`` (``#2d2d2d``), so it reads
    # as a quiet dark gap. ``PluginsGrid._paint_cell_dividers`` keeps its
    # hairlines out of this zone.
    "QFrame#nsl_plugins_grid_group_divider {"
    "    background-color: #252525;"
    "    border: none;"
    "}"
    "QLabel#nsl_plugins_grid_group_divider_label {"
    "    color: #9a9a9a;"
    "    font-size: 9px;"
    "    font-weight: 700;"
    "    letter-spacing: 1px;"
    "    padding: 0 6px 0 4px;"
    "    background: transparent;"
    "}"
    "QFrame#nsl_plugins_grid_group_divider_line {"
    "    background-color: #6a6a6a;"
    "    border: none;"
    "    min-height: 2px; max-height: 2px;"
    "}"
)


class _GroupDivider(QtWidgets.QFrame):
    """Thin horizontal divider with a small uppercase label on the left.

    Spans the grid width minus the outer margins. Emitted between sort
    buckets when :meth:`PluginsGrid.set_group_labels` turns on grouping.
    """

    def __init__(
        self, label: str, parent: Optional["QtWidgets.QWidget"] = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("nsl_plugins_grid_group_divider")
        self.setStyleSheet(_GROUP_DIVIDER_QSS)
        layout = QtWidgets.QHBoxLayout(self)
        # 4 top and 2 bottom in the 18 px band leaves 12 px of content.
        # That fits the 9 px label and the 2 px line.
        layout.setContentsMargins(0, 4, 0, 2)
        layout.setSpacing(0)

        # ``upper()`` here, so callers do not need to upper-case their
        # own label vocabulary.
        self._label = QtWidgets.QLabel(label.upper(), self)
        self._label.setObjectName("nsl_plugins_grid_group_divider_label")
        layout.addWidget(self._label)

        line = QtWidgets.QFrame(self)
        line.setObjectName("nsl_plugins_grid_group_divider_line")
        layout.addWidget(line, stretch=1)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )


# ---------------------------------------------------------------------------
# PluginsGrid - the public widget
# ---------------------------------------------------------------------------


class PluginsGrid(QtWidgets.QScrollArea):
    """Dynamic multi-column pill grid with marquee selection.

    Construct with pill ``keys`` and a ``pill_factory(key) -> QWidget``.
    The grid makes one :class:`_Cell` per key, parents the pill into the
    cell, and reflows the columns on resize. Marquee drag and ctrl-click
    drive ``selection_changed(list)``.
    """

    selection_changed = QtCore.Signal(list)

    def __init__(
        self,
        keys: Sequence[str],
        pill_factory: PillFactory,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)

        self._keys: List[str] = list(keys)
        self._pill_factory = pill_factory
        self._selected: Set[str] = set()
        self._columns = 1
        self._cell_w = pill_min_cell_width()
        self._pill_min_width = PILL_MIN_WIDTH
        self._pill_max_width = PILL_MAX_WIDTH

        self._viewport = _GridViewport(self)
        self._viewport.setObjectName("PluginsGridViewport")
        self.setWidget(self._viewport)

        self._empty_label = QtWidgets.QLabel(
            EMPTY_PLACEHOLDER_TEXT, self._viewport
        )
        self._empty_label.setObjectName("PluginsGridEmptyState")
        self._empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self._empty_label.setStyleSheet(
            "QLabel#PluginsGridEmptyState {"
            f"  color: {EMPTY_PLACEHOLDER_COLOUR};"
            "  font-size: 12px;"
            "  background: transparent;"
            "}"
        )
        self._empty_label.hide()

        self._cells: List[_Cell] = []
        self._pills: List[QtWidgets.QWidget] = []
        for key in self._keys:
            cell = _Cell(self._viewport)
            cell._grid_ref = self
            pill = self._pill_factory(key)
            pill.setParent(cell)
            self._cells.append(cell)
            self._pills.append(pill)
            self._connect_pill_selection(key, pill)

        # One divider label per key, or ``None`` for a sort mode that
        # does not group. The wiring layer sets it after each sort. An
        # empty list means the uniform layout.
        self._group_labels: List[Optional[str]] = []
        self._dividers: List[_GroupDivider] = []
        # ``(y_top, y_bottom)`` per emitted divider, so the hairline
        # painter can find the gutter zones without reading geometry.
        self._divider_y_ranges: List[Tuple[int, int]] = []

        # Built after the cells, so ``raise_()`` puts it last in the
        # z-order and the rubber-band paints over the pill bodies.
        self._marquee_overlay = _MarqueeOverlay(self._viewport)
        self._marquee_overlay.raise_()

        self._marquee_active = False
        self._marquee_origin: Optional[QtCore.QPoint] = None
        self._marquee_current: Optional[QtCore.QPoint] = None

        self._viewport.mouse_press = self._on_viewport_mouse_press
        self._viewport.mouse_move = self._on_viewport_mouse_move
        self._viewport.mouse_release = self._on_viewport_mouse_release
        # The viewport paints the dividers under the cell widgets. The
        # marquee box is a separate raised overlay above everything.
        self._viewport.paint_overlay = self._paint_viewport_overlay
        # The overlay must follow when QScrollArea stretches the inner
        # widget, or the rubber-band is clipped at the last row.
        self._viewport.resize_hook = self._on_viewport_resize

        self._relayout()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def keys(self) -> List[str]:
        return list(self._keys)

    def selected_keys(self) -> List[str]:
        return list(self._selected)

    def column_count(self) -> int:
        return self._columns

    def cell_count(self) -> int:
        return len(self._cells)

    def set_pill_size_hints(self, pill_min: int, pill_max: int) -> None:
        """Override pill min/max width and re-layout."""
        self._pill_min_width = int(pill_min)
        self._pill_max_width = int(pill_max)
        self._relayout()

    def set_keys(self, keys: Sequence[str]) -> bool:
        """Replace the grid contents with a fresh set of plugin keys.

        The grid rebuilds in place and clears the selection. Call
        :func:`nsl.ui.wiring.events.rewire_grid_pills` afterwards to
        re-attach signals to the new pills.

        Returns ``True`` when a rebuild happened. Callers use that to
        avoid stacking duplicate signal connections on each refresh.
        """
        new_keys = list(keys)
        if new_keys == self._keys:
            return False

        # ``deleteLater`` queues the destruction, so the current event
        # handler finishes before the widget goes away.
        for cell in self._cells:
            cell.setParent(None)
            cell.deleteLater()
        self._cells = []
        self._pills = []
        self._selected = set()
        # The old labels line up with the old keys. Drop them so a caller
        # that pushes no fresh ones paints no mismatched dividers.
        self._group_labels = []

        self._keys = new_keys
        for key in self._keys:
            cell = _Cell(self._viewport)
            cell._grid_ref = self
            pill = self._pill_factory(key)
            pill.setParent(cell)
            self._cells.append(cell)
            self._pills.append(pill)
            self._connect_pill_selection(key, pill)

        self._marquee_overlay.raise_()
        self._relayout()
        self.selection_changed.emit([])
        return True

    def set_group_labels(self, labels: Sequence[Optional[str]]) -> None:
        """Set per-pill divider labels, aligned to :meth:`keys`.

        Pass one entry per key: the bucket label, or ``None`` for a pill
        with no divider above it. An empty list clears the dividers.

        A length mismatch resets to no grouping instead of raising. The
        wiring layer sometimes races a recompute against a refresh, and
        one frame of un-divided pills beats an exception in a Qt slot.
        """
        labels_list = [
            (str(label) if label is not None else None) for label in labels
        ]
        if labels_list and len(labels_list) != len(self._keys):
            labels_list = []
        self._group_labels = labels_list
        self._relayout()

    def select_keys(
        self, keys: Iterable[str], *, emit: bool = True
    ) -> None:
        """Replace the selection with *keys* (clamped to known keys)."""
        valid = {k for k in keys if k in set(self._keys)}
        if valid == self._selected:
            return
        self._selected = valid
        self._apply_selected_paint()
        if emit:
            self.selection_changed.emit(list(self._selected))

    def clear_selection(self, *, emit: bool = True) -> None:
        if not self._selected:
            return
        self._selected.clear()
        self._apply_selected_paint()
        if emit:
            self.selection_changed.emit([])

    def toggle_key(self, key: str, *, additive: bool = False) -> None:
        """Public ctrl-click hook."""
        if key not in set(self._keys):
            return
        self._selected = toggle_selection(self._selected, key, additive=additive)
        self._apply_selected_paint()
        self.selection_changed.emit(list(self._selected))

    def _connect_pill_selection(self, key: str, pill) -> None:
        """Connect a pill's ``selection_requested`` signal, when it has one.

        Pills swallow body clicks and Qt does not propagate child mouse
        events. A modifier-held click is re-emitted as this signal.
        Plain clicks still reach the pill's enable toggle.
        """
        signal = getattr(pill, "selection_requested", None)
        if signal is None:
            return
        try:
            signal.connect(
                lambda modifiers, k=key: self._on_pill_selection_request(
                    k, modifiers
                )
            )
        except Exception:
            # Duck-typed stand-ins have no real Qt signal. They still
            # get the marquee and cell-padding selection path.
            pass

    def _on_pill_selection_request(self, key: str, modifiers) -> None:
        if key not in set(self._keys):
            return
        any_modifier = bool(
            modifiers
            & (
                QtCore.Qt.ShiftModifier
                | QtCore.Qt.ControlModifier
                | QtCore.Qt.MetaModifier
            )
        )
        if any_modifier:
            # Modifier-click removes a selected pill and adds an
            # unselected one.
            self._selected = toggle_selection(
                self._selected, key, additive=True
            )
        else:
            # The pill only emits with a modifier held. A bare emit
            # falls back to plain-click replace.
            self._selected = {key}
        self._apply_selected_paint()
        self.selection_changed.emit(list(self._selected))

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def resizeEvent(self, event):  # pragma: no cover - covered by snapshot
        super().resizeEvent(event)
        self._relayout()

    def _viewport_width(self) -> int:
        # The scroll area viewport, not the inner widget. Its width
        # drives the column count, so the scrollbar cannot cause a loop.
        return int(self.viewport().width())

    def _relayout(self) -> None:
        vw = max(1, self._viewport_width())
        # Empty grid. The label spans the viewport so it stays centred
        # when the panel resizes.
        if not self._cells:
            content_h = max(120, int(self.viewport().height()))
            self._viewport.setMinimumHeight(content_h)
            self._viewport.resize(vw, content_h)
            self._empty_label.setGeometry(
                QtCore.QRect(0, 0, vw, content_h)
            )
            self._empty_label.show()
            return
        self._empty_label.hide()
        columns = compute_columns(
            vw,
            pill_min_width=self._pill_min_width,
            cell_padding=CELL_PADDING,
            grid_margin=GRID_MARGIN,
            max_columns=MAX_COLUMNS,
        )
        self._columns = columns
        self._cell_w = cell_widths(
            vw,
            columns,
            pill_max_width=self._pill_max_width,
            cell_padding=CELL_PADDING,
            grid_margin=GRID_MARGIN,
        )

        # Cells tile the usable width with no slack. Each pill renders at
        # its constant sizeHint and is centred. Extra cell width becomes
        # padding where a marquee drag can start.
        from nsl.ui.pill import (
            _MIN_W as _PILL_W,
            _MIN_H as _PILL_H,
            _SHADOW_MARGIN as _PILL_SHADOW,
        )

        for d in self._dividers:
            d.setParent(None)
            d.deleteLater()
        self._dividers = []
        self._divider_y_ranges = []

        # With no labels, ``layout_with_dividers`` returns the uniform
        # layout, so the non-grouped path is unchanged.
        labels = (
            list(self._group_labels)
            if self._group_labels
            else [None] * len(self._cells)
        )
        cell_rects, divider_rects, content_h = layout_with_dividers(
            labels,
            columns=columns,
            cell_w=self._cell_w,
            viewport_w=vw,
            cell_h=CELL_HEIGHT,
            divider_h=GROUP_DIVIDER_HEIGHT,
            grid_margin=GRID_MARGIN,
            grid_margin_v=GRID_MARGIN_V,
        )

        for cell, (x, y, w, h), pill in zip(self._cells, cell_rects, self._pills):
            cell.setGeometry(QtCore.QRect(x, y, w, h))
            pill_w = min(_PILL_W, w)
            pill_h = min(_PILL_H, h)
            pill_x = (w - pill_w) // 2
            pill_y = (h - pill_h) // 2
            pill_rect = QtCore.QRect(pill_x, pill_y, pill_w, pill_h)
            pill.setGeometry(pill_rect)
            # The cell needs the body rect, inset by the pill's shadow
            # margin. Otherwise the ring paints at the shadow edge and
            # reads as misaligned. The inset also marks marquee padding.
            body_rect = pill_rect.adjusted(
                _PILL_SHADOW, _PILL_SHADOW,
                -_PILL_SHADOW, -_PILL_SHADOW,
            )
            cell.set_pill_rect(body_rect)
            cell.show()
            pill.show()

        # Fresh widgets each time. Record each gutter's
        # ``(y_top, y_bottom)`` so the hairline painter can skip it.
        for label, dx, dy, dw, dh in divider_rects:
            divider = _GroupDivider(label, self._viewport)
            divider.setGeometry(QtCore.QRect(dx, dy, dw, dh))
            divider.show()
            self._dividers.append(divider)
            self._divider_y_ranges.append((dy, dy + dh))

        # The scroll area needs the full content height before the
        # scrollbar engages.
        self._viewport.setMinimumHeight(content_h)
        self._viewport.resize(vw, content_h)
        # The widget's real height, not ``content_h``. QScrollArea can
        # stretch it past the content.
        overlay_h = max(content_h, self._viewport.height())
        self._marquee_overlay.setGeometry(0, 0, vw, overlay_h)
        self._marquee_overlay.raise_()

    def _on_viewport_resize(self, event) -> None:
        """Track the inner viewport widget's actual size.

        QScrollArea resizes the inner widget to fill the scroll area when
        the content is shorter. The overlay and dividers must follow.
        """
        size = self._viewport.size()
        self._marquee_overlay.setGeometry(0, 0, size.width(), size.height())
        self._marquee_overlay.raise_()
        self._viewport.update()

    def _all_cell_rects(self) -> List[Tuple[int, int, int, int]]:
        rects: List[Tuple[int, int, int, int]] = []
        for cell in self._cells:
            g = cell.geometry()
            rects.append((g.x(), g.y(), g.width(), g.height()))
        return rects

    def _apply_selected_paint(self) -> None:
        keys = set(self._selected)
        for key, cell in zip(self._keys, self._cells):
            cell.set_selected(key in keys)

    # ------------------------------------------------------------------
    # Marquee + click handling (wired into viewport mouse events)
    # ------------------------------------------------------------------

    def _clamp_to_viewport(self, p: QtCore.QPoint) -> QtCore.QPoint:
        """Marquee cannot extend outside the grid region."""
        x = max(0, min(self._viewport.width(), p.x()))
        y = max(0, min(self._viewport.height(), p.y()))
        return QtCore.QPoint(x, y)

    def _on_viewport_mouse_press(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        self._press_at(event.pos(), event.modifiers())

    def _on_viewport_mouse_move(self, event) -> None:
        self._move_at(event.pos())

    def _on_viewport_mouse_release(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        self._release_at(event.pos(), event.modifiers())

    # ------------------------------------------------------------------
    # ``_Cell`` forwards its press, move and release here. A click in the
    # cell padding then reaches the marquee logic.
    # ------------------------------------------------------------------

    def _press_at(self, pos: "QtCore.QPoint", modifiers) -> None:
        additive = bool(
            modifiers
            & (
                QtCore.Qt.ControlModifier
                | QtCore.Qt.MetaModifier
                | QtCore.Qt.ShiftModifier
            )
        )
        self._marquee_active = True
        self._marquee_origin = self._clamp_to_viewport(pos)
        self._marquee_current = self._marquee_origin
        if not additive:
            self._selected.clear()
            self._apply_selected_paint()
        self._viewport.update()

    def _move_at(self, pos: "QtCore.QPoint") -> None:
        if not self._marquee_active:
            return
        self._marquee_current = self._clamp_to_viewport(pos)
        rect = self._current_marquee_rect()
        hits = set(marquee_hits(rect, self._all_cell_rects()))
        for i, cell in enumerate(self._cells):
            cell.set_marquee_hover(i in hits)
        self._marquee_overlay.set_marquee(rect)
        self._viewport.update()

    def _release_at(self, pos: "QtCore.QPoint", modifiers) -> None:
        if not self._marquee_active:
            return
        # A release can fire with no move in between, so refresh the end
        # point here.
        self._marquee_current = self._clamp_to_viewport(pos)
        rect = self._current_marquee_rect()
        additive = bool(
            modifiers
            & (
                QtCore.Qt.ShiftModifier
                | QtCore.Qt.ControlModifier
                | QtCore.Qt.MetaModifier
            )
        )
        # A zero-size marquee is a click with no drag. With a modifier
        # held it selects the cell under the cursor, padding included.
        # Without one it captures nothing.
        captured_keys: Set[str] = set()
        zero_size_click = rect[2] == 0 and rect[3] == 0
        if zero_size_click:
            origin = self._marquee_origin
            if origin is not None:
                for key, cell in zip(self._keys, self._cells):
                    cell_geo = cell.geometry()
                    if not cell_geo.contains(origin):
                        continue
                    local = origin - cell_geo.topLeft()
                    on_pill = (
                        not cell._pill_rect.isEmpty()
                        and cell._pill_rect.contains(local)
                    )
                    if on_pill or additive:
                        captured_keys = {key}
                    break
        else:
            hits = marquee_hits(rect, self._all_cell_rects())
            captured_keys = {self._keys[i] for i in hits}
        if additive:
            # Smart toggle. Remove the captured pills when every one is
            # already selected, and add them otherwise.
            if captured_keys and captured_keys.issubset(self._selected):
                self._selected -= captured_keys
            else:
                self._selected |= captured_keys
        else:
            self._selected = captured_keys
        self._marquee_active = False
        self._marquee_origin = None
        self._marquee_current = None
        for cell in self._cells:
            cell.set_marquee_hover(False)
        self._marquee_overlay.set_marquee(None)
        self._apply_selected_paint()
        self._viewport.update()
        self.selection_changed.emit(list(self._selected))

    def _current_marquee_rect(self) -> Tuple[int, int, int, int]:
        if self._marquee_origin is None or self._marquee_current is None:
            return (0, 0, 0, 0)
        o = self._marquee_origin
        c = self._marquee_current
        x, y = min(o.x(), c.x()), min(o.y(), c.y())
        w, h = abs(c.x() - o.x()), abs(c.y() - o.y())
        return (x, y, w, h)

    def _paint_viewport_overlay(self, painter: "QtGui.QPainter") -> None:
        """Paint the viewport overlay, currently the cell dividers only.

        The marquee box lives on :class:`_MarqueeOverlay`, above the pills.
        """
        self._paint_cell_dividers(painter)

    def _paint_cell_dividers(self, painter: "QtGui.QPainter") -> None:
        """Draw 1 px hairlines between cells.

        Painted on the viewport, not on each cell, so neighbours do not
        double-paint a line. The lines cross the full viewport, so empty
        slots still show the grid structure.

        A horizontal hairline is skipped inside a gutter zone, where the
        gutter's own dark colour separates the rows.
        """
        if self._columns <= 0 or self._cell_w <= 0:
            return
        grid_x0 = GRID_MARGIN
        vp_w = self._viewport.width()
        vp_h = self._viewport.height()

        r, g, b = CELL_DIVIDER_COLOUR
        pen = QtGui.QPen(QtGui.QColor(r, g, b))
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)

        # Horizontal lines.
        if not self._cells:
            # Uniform rows, so the placeholder area still reads as grid.
            y = GRID_MARGIN_V + CELL_HEIGHT
            while y < vp_h:
                painter.drawLine(0, y, vp_w, y)
                y += CELL_HEIGHT
        else:
            row_tops = sorted({c.geometry().y() for c in self._cells})
            last_painted_y = GRID_MARGIN_V
            for y_top in row_tops:
                y = y_top + CELL_HEIGHT
                if y >= vp_h:
                    break
                if _y_inside_any(y, self._divider_y_ranges):
                    continue
                painter.drawLine(0, y, vp_w, y)
                last_painted_y = y
            # Keep going below the last row, so the grid reads as
            # continuing past the pills.
            y = last_painted_y + CELL_HEIGHT
            while y < vp_h:
                if not _y_inside_any(y, self._divider_y_ranges):
                    painter.drawLine(0, y, vp_w, y)
                y += CELL_HEIGHT

        # Every column boundary, full height, so empty cells still show
        # the column structure.
        for col_i in range(1, self._columns):
            x = grid_x0 + col_i * self._cell_w
            painter.drawLine(x, 0, x, vp_h)

    def _paint_marquee_overlay(self, painter: "QtGui.QPainter") -> None:
        if not self._marquee_active:
            return
        x, y, w, h = self._current_marquee_rect()
        if w <= 0 or h <= 0:
            return
        r, g, b = NUKE_SELECTION_RGB
        fill = QtGui.QColor(r, g, b, MARQUEE_FILL_ALPHA)
        outline = QtGui.QColor(r, g, b, MARQUEE_OUTLINE_ALPHA)
        painter.setBrush(QtGui.QBrush(fill))
        painter.setPen(QtGui.QPen(outline, 1))
        painter.drawRect(QtCore.QRect(x, y, w, h))


# ---------------------------------------------------------------------------
# Marquee overlay and inner viewport widget
# ---------------------------------------------------------------------------


class _MarqueeOverlay(QtWidgets.QWidget):
    """Top-most overlay child of the grid viewport. Paints the marquee box.

    Cells are children of the viewport, so viewport paint sits under
    them. This overlay is raised above the cells and stays transparent
    to mouse events, so the grid's own handlers keep working.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        # No system background fill so cells underneath show through.
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._marquee: Optional[Tuple[int, int, int, int]] = None

    def set_marquee(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        if self._marquee == rect:
            return
        self._marquee = rect
        self.update()

    def paintEvent(self, event):  # pragma: no cover - exercised via grab()
        if not self._marquee:
            return
        x, y, w, h = self._marquee
        if w <= 0 or h <= 0:
            return
        painter = QtGui.QPainter(self)
        try:
            r, g, b = NUKE_SELECTION_RGB
            fill = QtGui.QColor(r, g, b, MARQUEE_FILL_ALPHA)
            outline = QtGui.QColor(r, g, b, MARQUEE_OUTLINE_ALPHA)
            painter.setBrush(QtGui.QBrush(fill))
            painter.setPen(QtGui.QPen(outline, 1))
            painter.drawRect(QtCore.QRect(x, y, w, h))
        finally:
            painter.end()


class _GridViewport(QtWidgets.QWidget):
    """The scroll area's inner widget. Hosts the cells.

    :class:`PluginsGrid` sets the mouse and paint hooks as callable
    attributes after construction, which keeps this class Qt-only.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        # Move events must fire without a button held.
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)
        self.mouse_press: Optional[Callable] = None
        self.mouse_move: Optional[Callable] = None
        self.mouse_release: Optional[Callable] = None
        self.paint_overlay: Optional[Callable] = None
        self.resize_hook: Optional[Callable] = None

    def mousePressEvent(self, event):  # pragma: no cover - GUI path
        if self.mouse_press is not None:
            self.mouse_press(event)
        # Accept so Qt grabs the mouse for the rest of the drag. The
        # default ``ignore()`` sends the press up to QScrollArea and the
        # move and release never reach these hooks.
        event.accept()

    def mouseMoveEvent(self, event):  # pragma: no cover - GUI path
        if self.mouse_move is not None:
            self.mouse_move(event)
        event.accept()

    def mouseReleaseEvent(self, event):  # pragma: no cover - GUI path
        if self.mouse_release is not None:
            self.mouse_release(event)
        event.accept()

    def resizeEvent(self, event):  # pragma: no cover - GUI path
        super().resizeEvent(event)
        if self.resize_hook is not None:
            self.resize_hook(event)

    def paintEvent(self, event):  # pragma: no cover - exercised via grab()
        # Fill the background here, not with a palette. Palette
        # inheritance in the QScrollArea would otherwise pick the colour.
        painter = QtGui.QPainter(self)
        try:
            r, g, b = GRID_BG_COLOUR
            painter.fillRect(self.rect(), QtGui.QColor(r, g, b))
            if self.paint_overlay is not None:
                self.paint_overlay(painter)
        finally:
            painter.end()

