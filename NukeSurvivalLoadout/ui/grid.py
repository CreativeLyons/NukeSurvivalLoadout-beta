"""Plugins grid widget - NSL's cell+pill rendering surface.

Public API:
    - :class:`PillProtocol` - duck-typed pill (so callers don't need to
      import :mod:`NukeSurvivalLoadout.ui.pill`).
    - :class:`PillFactory` - callable returning a ``QWidget`` for a key.
    - :func:`compute_columns` - pure-Python: how many columns at width *w*.
    - :func:`marquee_hits` - pure-Python: which cell indices overlap a
      marquee rect (any-overlap, NOT containment).
    - :func:`toggle_selection` - pure-Python: ctrl-click toggle semantics.
    - :class:`PluginsGrid` - the custom ``QScrollArea`` + viewport
      ``QWidget`` with cell+pill structure, dynamic column reflow,
      marquee drag bounded to the grid region, and ctrl-click toggling.

Signals emitted by ``PluginsGrid``:
    - ``selection_changed(list)`` - emitted whenever the selection set
      changes. Payload is a list of the currently-selected pill keys
      (strings), in arbitrary order. The grid keeps an internal selection
      set only to drive cell-paint; it is a signal source only and does
      not own the canonical selection model.

Cross-cutting contracts:
    - Qt imported only via :mod:`NukeSurvivalLoadout.compat` - never ``import PySide2`` /
      ``import PySide6`` directly.
    - **Cell + pill split**: each grid slot is a ``_Cell`` ``QWidget`` whose
      paint paints the yellow-orange selection background. The pill widget
      is a child of the cell - it owns its own body tint, border, and
      status icon. The cell never paints over the pill.
    - **Marquee bounded** to the grid viewport rect. Drags that start in
      empty space or on a cell launch a marquee; drag attempts to extend
      past the viewport are clipped at the viewport edge.
    - **Any-overlap capture** (NOT containment).
    - No ``import nuke``. No edits to ``NukeSurvivalLoadout/ui/__init__.py``.

Pill sizing:
    - Pill min/max width: ``PILL_MIN_WIDTH = 241`` and
      ``PILL_MAX_WIDTH = 380``. Tunable via
      ``PluginsGrid.set_pill_size_hints(min, max)``.
    - Pill widget class - :mod:`NukeSurvivalLoadout.ui.pill`. We define a
      ``PillProtocol`` duck-type and accept any callable factory so this
      file does not hard-depend on the pill module.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

from NukeSurvivalLoadout import compat
from NukeSurvivalLoadout.ui import _theme

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# ---------------------------------------------------------------------------
# Layout defaults
# ---------------------------------------------------------------------------

#: Hard cap on column count. The grid reflows freely based on viewport
#: width - pills flow book-order (left to right, top to bottom) and pack
#: as many columns as fit. The cap is a sanity ceiling rather than a
#: design constraint.
MAX_COLUMNS = 16

#: Pill minimum visual width in px. Below this, the column count drops.
#: Kept in sync with ``NukeSurvivalLoadout.ui.pill._MIN_W`` - if either changes, both must
#: change together or the grid will clip pills at the right column edge.
#: V3.5 shadow halo bumped 12 → 15: ``_MIN_W = 211 + 2 * 15 = 241``.
PILL_MIN_WIDTH = 241

#: Pill maximum visual width in px. Above this, extra space becomes margin.
#: 304 = 380 × 0.8 - caps the visible pill width 20% tighter so a wide
#: panel doesn't grow the pill body to an over-aerated size. The remaining
#: viewport width becomes outer margin.
PILL_MAX_WIDTH = 380

#: Padding inside each cell around its pill (the cell-vs-pill breathing
#: room). V3.3 zeroed both axes - cells touch edge-to-edge, and the only space
#: between adjacent pill bodies is the pill widget's own (now halved)
#: shadow halo. The marquee drag-start zone is now ``_SHADOW_MARGIN``
#: per side instead of ``_SHADOW_MARGIN + CELL_PADDING``.
CELL_PADDING = 0
CELL_PADDING_V = 0

#: Outer horizontal margin around the grid block inside the viewport.
#: V3.3 zeroed out (3 → 0) - leftmost / rightmost pill columns now sit
#: flush against the grid viewport's left + right edges so the SectionBox
#: bounding line is the only visible chrome between the pill column and
#: the panel chrome outside.
GRID_MARGIN = 0

#: Outer vertical margin around the grid block - also zeroed (3 → 0)
#: on the same V3.3 call. Top + bottom edge sit flush against the
#: SectionBox bounding line.
GRID_MARGIN_V = 0

#: Vertical pixel height of each cell (cell padding top + pill body + cell
#: padding bottom). Must be ``>= NukeSurvivalLoadout.ui.pill._MIN_H + 2 * CELL_PADDING_V`` or
#: the cell will squeeze the pill below its minimum height and clip the
#: bottom row of paint geometry (status row / GUI button) on every render.
#:
#: V3.5 shadow halo: pill ``_MIN_H = 70 + 2 * 15 = 100``;
#: ``100 + 2 * CELL_PADDING_V(0) = 100``.
CELL_HEIGHT = 100

#: Vertical pixel height of one group-divider row (the **gutter band**
#: that separates buckets when the sort dropdown is set to a grouping
#: mode). The gutter
#: holds the bucket label + hairline; its background is a hair below
#: ``GRID_BG_COLOUR`` so it reads as a quiet gap rather than a dark slab.
#:
#: 18 px band in ``#252525``: a tight gutter with subtle contrast that sits
#: clear of the painted hairlines.
GROUP_DIVIDER_HEIGHT = 18


# ---------------------------------------------------------------------------
# Selection-visual colours
# ---------------------------------------------------------------------------
# Locked vocabulary (per the grid-toolbar pass, row #4): selection = orange
# ``#ee9626`` 2 px BORDER ONLY around the pill. No body wash on the cell - the
# pill body stays free for the pending-diff colour (green/red) painted by the
# pill widget itself. The cell paints the border just outside the pill rect
# so the line reads as "this pill is selected" while leaving any pill body
# tint intact.
#
# The marquee transient hover uses the same colour at a thinner 1 px width
# (and lower alpha) so cells lighting up as the marquee sweeps over them
# read as distinct from confirmed selections.

#: Canonical Nuke-accent orange (single source of truth in ``_theme.py``).
NUKE_SELECTION_RGB = _theme.NUKE_ORANGE_RGB

#: Persistent selection border width (px).
SELECTION_BORDER_WIDTH = 3

#: Inflation (px) of the pill rect when painting the selection halo.
#: Set to ``SELECTION_BORDER_WIDTH // 2`` so the centered stroke's inner
#: edge sits **flush** with the pill body edge - half the stroke draws
#: into the shadow-margin area (visible), half draws into the body area
#: (covered by the pill's own paint). Earlier this was ``2`` while the
#: width was also ``2``, which left a 1-px gap between the pill edge
#: and the ring; the new pairing closes that gap so the ring reads as
#: hugging the pill rather than floating around it.
SELECTION_HALO_INSET = SELECTION_BORDER_WIDTH // 2

#: Marquee transient hover border width (px).
MARQUEE_HOVER_WIDTH = 1

#: Marquee transient hover alpha (0-255) - lower than the confirmed selection
#: so the two states are visually distinct.
MARQUEE_HOVER_ALPHA = 180

#: Selection border alpha (0-255) - solid.
SELECTION_BORDER_ALPHA = 255

#: Confirmed-selection body tint alpha (0-255). A quiet orange wash on
#: the cell rect that pairs with the halo so the cell itself reads as
#: "highlighted" without competing with the pill body chrome.
SELECTED_CELL_TINT_ALPHA = 32

#: Marquee-hover body tint alpha (0-255). Fainter than the confirmed
#: selection - the transient state should read as "marquee is passing
#: over you" rather than "you're selected".
MARQUEE_HOVER_TINT_ALPHA = 22

#: Marquee rubber-band fill alpha (0-255). Translucent so cells underneath
#: stay visible while the drag is in progress.
MARQUEE_FILL_ALPHA = 50

#: Marquee rubber-band outline alpha (0-255). Solid edge.
MARQUEE_OUTLINE_ALPHA = 220

#: 1 px hairline drawn between adjacent cells. Lighter than the recessed
#: grid background (#303030) so the dividers read as engraved channels
#: against a slightly darker plane.
CELL_DIVIDER_COLOUR = (74, 74, 74)  # #4a4a4a

#: Background colour of the pill grid viewport - recessed below the panel
#: body (`#393939`) and a hair below the search field (`#303030`). The
#: grid reads as a recessed channel that holds the pills; the dividers
#: paint lighter against this darker plane.
GRID_BG_COLOUR = (45, 45, 45)  # #2d2d2d

#: Cell-background wash for pending-restart diff.
#: The pill border + glow already signal "will load on restart" (lime)
#: and "will unload on restart" (red); these RGBA washes paint the
#: same signal at the cell-padding level so the row reads green / red
#: at a glance without competing with the pill body. Alpha is
#: intentionally low - the cell's perceived luminance stays close to
#: ``GRID_BG_COLOUR`` and the wash is a subtle direction-of-change
#: cue, not a heavy fill.
#
# The load alpha (18) sits a touch below the unload alpha (22) because
# the broader lime channel reads heavier than red at equal alpha; the
# offset balances the two washes to the same perceived strength.
CELL_DIFF_BG_LOAD_RGBA = (80, 180, 80, 18)
CELL_DIFF_BG_UNLOAD_RGBA = (200, 80, 80, 22)

#: Cell wash for a GUI-only OFF->ON change. GUI-only is NOT a load/unload
#: change (still loads in this GUI session; skipped only on the render
#: farm), so it gets a purple wash rather than the green/red load washes.
#: The load wash takes precedence: this purple paints only when the cell
#: has no enable/disable change.
#:
#: A saturated violet (150,90,214) at alpha 32: saturation high enough to
#: read clearly as purple (rather than a plain highlight) while the low
#: alpha keeps it a subtle row hint, not a slab.
CELL_DIFF_BG_GUI_ON_RGBA = (150, 90, 214, 32)


# ---------------------------------------------------------------------------
# Empty-state placeholder
# ---------------------------------------------------------------------------
# Shown when the grid has no keys to render - e.g. a Loadout with zero
# enabled plugins, or every plugin filtered out by Search + Tag. Wording is
# deliberately neutral; the row #8 first-run "no folders yet" prompt is a
# different surface and lives in ``NukeSurvivalLoadout.ui.empty_state``.

EMPTY_PLACEHOLDER_TEXT = "No plugins to show."
EMPTY_PLACEHOLDER_COLOUR = "#7a7a7a"


# ---------------------------------------------------------------------------
# Pill protocol + factory typing
# ---------------------------------------------------------------------------


class PillProtocol:
    """Minimal duck-typed pill interface this grid relies on.

    The real ``NukeSurvivalLoadout.ui.pill.PluginPill`` satisfies this
    by virtue of being a ``QWidget`` subclass - any ``QWidget`` is enough.
    Declared here so this module is self-contained and does not hard-depend
    on ``NukeSurvivalLoadout.ui.pill``.
    """

    def setParent(self, parent):  # pragma: no cover - QWidget interface
        ...

    def setGeometry(self, rect):  # pragma: no cover - QWidget interface
        ...

    def show(self):  # pragma: no cover - QWidget interface
        ...


#: Type alias: callable that returns a fresh ``QWidget`` for the given key.
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

    A column requires ``pill_min_width + 2*cell_padding`` of horizontal
    space; total horizontal chrome is ``2*grid_margin``. Result is clamped
    to ``[1, max_columns]``.

    Pure function - no Qt dependency, so reflow can be computed without
    a ``QApplication``.
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
    """Return each cell's width in px given the chosen *columns*.

    Cells split the usable viewport width **evenly** so the grid resizes
    with the window - the column count is what determines pill density,
    not pill size. Pills inside each cell are capped at ``pill_max_width``
    and centred within their cell so the visible result reads as a
    regularly-spaced grid (even gaps between every column) instead of a
    left-pinned block with leftover margin on one side.

    ``pill_max_width`` no longer caps the cell - only the pill inside.
    See :meth:`PluginsGrid._relayout` for the cell-vs-pill centring.
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

    Top-left origin; cells flow left-to-right, top-to-bottom.
    Horizontal margin is ``grid_margin``; vertical margin is
    ``grid_margin_v`` - the two axes can flex independently so the grid
    can tighten its top/bottom edge without affecting its left/right
    spacing.
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
    """Total content height (px) for *n_pills* in *columns*-wide grid.

    The vertical extent depends on ``grid_margin_v`` (top + bottom
    outer margin) and ``cell_h``; ``grid_margin`` is retained in the
    signature so older callers that pass it as a kwarg don't break,
    but it doesn't enter the vertical math.
    """
    del grid_margin  # horizontal; vertical math uses grid_margin_v only
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
    """Place cells + group-divider rows into the viewport.

    Walks *group_labels* in order, treating each transition as a
    "finish the current row, drop a divider row, start the next bucket
    on a fresh row" event. Pure function - no Qt; the caller applies
    the returned geometries via ``setGeometry``.

    Args:
        group_labels: One entry per pill key, in render order. ``None``
            entries collapse into the same bucket (no transition); a
            non-``None`` entry that differs from the previous non-``None``
            entry triggers a divider row above the pill. A leading
            ``None`` run never emits a divider - the grid does not show
            a redundant divider above its very first pill.
        columns: Active column count (from :func:`compute_columns`).
        cell_w: Cell width in px (from :func:`cell_widths`).
        viewport_w: Inner viewport width in px (drives divider widget
            length; dividers span the full viewport minus margins).
        cell_h: Cell height in px. Defaults to :data:`CELL_HEIGHT`.
        divider_h: Divider row height in px. Defaults to
            :data:`GROUP_DIVIDER_HEIGHT`.
        grid_margin: Outer horizontal margin (left + right).
        grid_margin_v: Outer vertical margin (top + bottom).

    Returns:
        ``(cell_rects, divider_rects, content_height)`` where:

        * ``cell_rects[i]`` - ``(x, y, w, h)`` for pill index *i* in the
          input order. Always aligned with the input list; consumers can
          ``zip(cells, cell_rects)`` safely.
        * ``divider_rects`` - list of ``(label, x, y, w, h)`` tuples for
          every emitted divider, in document order. Empty list when
          ``group_labels`` has no transitions (e.g. all-``None`` for
          alphabetical sort modes).
        * ``content_height`` - total viewport height in px, including
          both pill rows and divider rows plus top/bottom margins.

    When ``group_labels`` is all-``None`` or empty, the output cell
    geometry is byte-identical to a uniform :func:`cell_rect` layout -
    callers that don't activate grouping get the existing behaviour for
    free.
    """
    if columns <= 0:
        columns = 1
    n = len(group_labels)
    cell_rects: List[Tuple[int, int, int, int]] = []
    divider_rects: List[Tuple[str, int, int, int, int]] = []

    if n == 0:
        return ([], [], 2 * grid_margin_v)

    # Row counter walks pill rows only; divider vertical offset is
    # picked up separately via ``len(divider_rects) * divider_h``.
    row = 0
    col = 0
    last_label: Optional[str] = None
    divider_w = max(0, viewport_w - 2 * grid_margin)

    for label in group_labels:
        # Group transition: emit a divider strip above the first pill
        # of the new bucket. The very first non-``None`` label also
        # counts as a transition - every bucket gets a leading header.
        # The user sees ``ON ────`` above the first On pill,
        # ``OFF ────`` between buckets - every section is visibly
        # labelled.
        #
        # ``None`` labels collapse into the previous bucket (no
        # divider), so an all-``None`` input (alphabetical sort
        # modes) renders zero dividers and an all-``None`` prefix
        # never spuriously claims that the first non-``None`` label
        # is its OWN bucket header above the prefix pills.
        if label is not None and label != last_label:
            # Finish partial row before the divider so the new
            # bucket starts on a fresh row at column 0.
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

        # Cell y picks up the current divider count (this includes the
        # divider just emitted above, if any, so the first pill of a
        # new bucket sits beneath its divider strip).
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

    # Final row counter: one past the last placed pill row.
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

    "Strictly inside" means ``y_top < y < y_bottom`` - equality at
    either boundary doesn't count, so a hairline at the top or bottom
    edge of a gutter (which abuts a pill row) still paints. Used by
    :meth:`PluginsGrid._paint_cell_dividers` to skip painting
    horizontal hairlines that would otherwise stripe through the
    middle of a gutter band.
    """
    for y_top, y_bottom in ranges:
        if y_top < y < y_bottom:
            return True
    return False


def _rects_overlap(
    ra: Tuple[int, int, int, int], rb: Tuple[int, int, int, int]
) -> bool:
    """Return True if two ``(x, y, w, h)`` rects share any pixel.

    Edge-touching counts as overlap (the "any overlap, not full
    containment" rule).
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
    """Return cell indices whose rect overlaps *marquee* (any overlap).

    Pure function implementing the any-overlap rule, so the captured set
    can be computed without instantiating any widgets.
    """
    mx, my, mw, mh = marquee
    # Normalise marquee to top-left origin with positive size.
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

    Pure helper so the grid widget's click handlers stay shallow.
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
    """One grid slot. Hosts a pill; paints the selection halo around it.

    The cell is a **pure positioning slot** for the pill body - it never
    fills its own rect. Selection signal is a 2 px orange ring drawn just
    outside the pill rect when ``_selected`` is True (1 px lower-alpha ring
    when the marquee is sweeping over it). The pill body itself stays free
    for whatever tint the pill widget paints (pending-add / pending-remove
    diff colour) - the locked vocabulary from the grid-toolbar pass.

    The cell owns the paint (rather than the pill) for two reasons:
      * It works for any pill widget plugged into the grid via
        ``PillFactory`` - the grid doesn't have to duck-type into the pill
        to forward selection state.
      * The pill rect is what the cell already knows from layout; painting
        at that rect keeps the selection halo geometrically locked to the
        pill regardless of cell padding or future anatomy changes.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._selected = False
        self._marquee_hover = False
        # Pending-restart diff tint - set by the panel's refresh loop
        # based on the matching pill's ``_pending_border_color``. ``None``
        # paints nothing (cell relies on ``GRID_BG_COLOUR`` showing
        # through). This is a second paint surface for the diff signal
        # alongside the pill border + glow so the direction of change
        # reads at the row level too.
        self._diff_tint: Optional[QtGui.QColor] = None
        # Pill rect the cell will paint the selection halo around. Set by
        # the grid during ``_relayout``; defaults to empty so an unsized
        # cell paints nothing even if ``_selected`` is True.
        self._pill_rect = QtCore.QRect()
        # Back-reference to the owning ``PluginsGrid`` so the cell can
        # forward mouse events to the grid's selection/marquee handlers.
        # Set by the grid right after construction; ``None`` means
        # "forwarding disabled" so the cell falls back to default behavior
        # when instantiated standalone.
        self._grid_ref: Optional["PluginsGrid"] = None
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        # Cells don't accept focus - pills (and the grid's marquee) do.
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

        Mirrors the matching pill's ``_pending_border_color`` signal at
        the cell level - lime wash for "would load on restart," red
        wash for "would unload." ``None`` clears the wash and the cell
        falls back to ``GRID_BG_COLOUR`` showing through.
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
    # Clicks that land on the bare cell (padding zone between the pill body
    # and the cell edge) belong to the marquee surface, not the cell.
    # Forward press / move / release to the owning grid with the position
    # translated into viewport coordinates. Pills are child widgets at
    # their own geometry and still receive their own clicks directly.

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

            # 1. Pending-restart diff wash - bottom layer. Paints the
            #    full cell rect (padding zone around the pill); the pill
            #    body paints on top so its own tint stays untouched. The
            #    selection orange (below) blends on top of this, so a
            #    selected pending-load cell reads as orange-over-green.
            if has_diff:
                painter.fillRect(self.rect(), self._diff_tint)

            if needs_select_paint:
                r, g, b = NUKE_SELECTION_RGB

                # 2. Slight orange body tint on the cell rect - fills the
                #    whole cell (including the padding around the pill) so
                #    the visible "highlighted" zone matches the dividers.
                #    The pill body paints on top so its own tint stays
                #    untouched.
                tint_alpha = (
                    SELECTED_CELL_TINT_ALPHA
                    if self._selected
                    else MARQUEE_HOVER_TINT_ALPHA
                )
                painter.fillRect(self.rect(), QtGui.QColor(r, g, b, tint_alpha))

            # 3. Halo around the pill body - confirmed selection only.
            #    Marquee-hover reads via the body tint alone, so the halo
            #    is reserved for "this is in your committed selection".
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
                # Inflate so the line sits **outside** the pill body - the
                # pill widget paints on top of the cell, so any halo pixels
                # inside the pill rect would be obscured. Rounded radius
                # matches ``NukeSurvivalLoadout.ui.pill._BORDER_RADIUS = 16`` (hard-coded
                # to avoid importing pill.py; if pill radius changes, this
                # must too).
                halo = self._pill_rect.adjusted(
                    -SELECTION_HALO_INSET,
                    -SELECTION_HALO_INSET,
                    SELECTION_HALO_INSET,
                    SELECTION_HALO_INSET,
                )
                # Pull the radius from pill.py so the halo always
                # matches the pill body's rounded corner - the previous
                # hardcoded ``16`` was a stale copy that didn't track
                # ``NSL_PILL_RADIUS`` env overrides or any future code
                # edit to the pill's corner radius.
                from NukeSurvivalLoadout.ui.pill import _BORDER_RADIUS as _PILL_RADIUS
                radius = _PILL_RADIUS + SELECTION_HALO_INSET
                painter.drawRoundedRect(halo, radius, radius)
        finally:
            painter.end()


# ---------------------------------------------------------------------------
# Group divider - thin label-plus-hairline strip between sort buckets
# ---------------------------------------------------------------------------
#
# The strip is a thin label-plus-hairline band: the label is a small
# uppercase chip on the left followed by a 1 px hairline that stretches
# to the right edge of the grid.
#
# Used by :class:`PluginsGrid` when ``set_group_labels`` is called with
# any non-``None`` label. Throwaway widgets - recreated on each
# ``_relayout`` because their position depends on the current viewport
# width and pill row arrangement.

_GROUP_DIVIDER_QSS = (
    # The divider strip is a **gutter band**, not a transparent strip
    # overlaid on the recessed grid background. The
    # gutter colour (``#252525``) sits one value-channel step below the
    # grid background (``GRID_BG_COLOUR`` = ``#2d2d2d``) so the strip
    # reads as a subtle dark gap in the grid surface - quiet but
    # clearly distinct. The horizontal cell-hairlines the grid painter
    # draws are made gutter-aware (see
    # ``PluginsGrid._paint_cell_dividers``) so they align with the
    # shifted pill rows but never paint inside the gutter zone.
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
    # 2 px line at #6a6a6a - clear against the gutter, doesn't compete
    # with the bolder label.
    "    background-color: #6a6a6a;"
    "    border: none;"
    "    min-height: 2px; max-height: 2px;"
    "}"
)


class _GroupDivider(QtWidgets.QFrame):
    """Thin horizontal divider with a small uppercase label on the left.

    Spans the full grid width minus the outer margins. Used between
    sort buckets when :meth:`PluginsGrid.set_group_labels` activates
    grouping for a non-alphabetical sort mode.
    """

    def __init__(
        self, label: str, parent: Optional["QtWidgets.QWidget"] = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("nsl_plugins_grid_group_divider")
        self.setStyleSheet(_GROUP_DIVIDER_QSS)
        layout = QtWidgets.QHBoxLayout(self)
        # 4 px top + 2 px bottom in the 18 px gutter band leaves a
        # 12 px content area - enough for the 9 px label baseline + the
        # 2 px line. Tight, centred, no wasted vertical room.
        layout.setContentsMargins(0, 4, 0, 2)
        layout.setSpacing(0)

        # Uppercase by design (a small uppercase group label on the
        # left) - `label.upper()` here so callers never need to
        # upper-case in their own label vocabulary.
        self._label = QtWidgets.QLabel(label.upper(), self)
        self._label.setObjectName("nsl_plugins_grid_group_divider_label")
        layout.addWidget(self._label)

        line = QtWidgets.QFrame(self)
        line.setObjectName("nsl_plugins_grid_group_divider_line")
        layout.addWidget(line, stretch=1)

        # Frozen height; expands horizontally with the parent layout.
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )


# ---------------------------------------------------------------------------
# PluginsGrid - the public widget
# ---------------------------------------------------------------------------


class PluginsGrid(QtWidgets.QScrollArea):
    """Dynamic multi-column pill grid with marquee selection.

    Construct with a list of pill ``keys`` and a ``pill_factory(key) -> QWidget``.
    The grid creates one :class:`_Cell` per key, parents the pill widget
    into the cell, and lays out cells in a dynamic column grid that
    reflows on resize. Marquee drag and ctrl-click drive the
    ``selection_changed(list)`` signal.

    Selection state is held internally only to drive cell paint - the
    canonical selection model lives elsewhere.
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

        # Viewport widget that hosts cells and paints the marquee overlay.
        self._viewport = _GridViewport(self)
        self._viewport.setObjectName("PluginsGridViewport")
        self.setWidget(self._viewport)

        # Empty-state placeholder - shown when ``_keys`` is empty (no
        # plugins after filter, or no plugins at all). Centred muted text;
        # no chrome. The first-run "no folders yet" prompt is a
        # different surface (handled by :mod:`NukeSurvivalLoadout.ui.empty_state`); this
        # placeholder is the grid-empty case.
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

        # Create cells and pills.
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

        # Group-divider state. Parallel to ``_keys``; entries are the
        # divider label for the bucket each pill belongs to, or ``None``
        # for alphabetical sort modes that don't group. The wiring layer
        # populates this via :meth:`set_group_labels` after each sort.
        # Empty list → no grouping → legacy uniform layout.
        self._group_labels: List[Optional[str]] = []
        # Throwaway divider widgets - recreated on every ``_relayout``
        # because their geometry depends on viewport width and current
        # row arrangement.
        self._dividers: List[_GroupDivider] = []
        # Parallel list of ``(y_top, y_bottom)`` tuples for each emitted
        # divider strip. Stored alongside the widgets so the cell-
        # hairline painter can query the gutter zones without poking
        # widget geometry every paint. Empty when no grouping is active.
        self._divider_y_ranges: List[Tuple[int, int]] = []

        # Marquee overlay sits ABOVE the cells so the rubber-band paints
        # on top of pill bodies. Created after cells so ``raise_()`` puts
        # it last in the z-order. Transparent to mouse - the viewport
        # below keeps receiving press/move/release.
        self._marquee_overlay = _MarqueeOverlay(self._viewport)
        self._marquee_overlay.raise_()

        # Marquee drag state (lives on the viewport, but the grid owns it
        # to keep the viewport class minimal).
        self._marquee_active = False
        self._marquee_origin: Optional[QtCore.QPoint] = None
        self._marquee_current: Optional[QtCore.QPoint] = None

        # Wire up the viewport's mouse events.
        self._viewport.mouse_press = self._on_viewport_mouse_press
        self._viewport.mouse_move = self._on_viewport_mouse_move
        self._viewport.mouse_release = self._on_viewport_mouse_release
        # Viewport paint composes two passes: dividers under the marquee
        # box. Both painted on the viewport, both under the cell widgets,
        # so the cell selection tint sits on top of dividers and the
        # marquee box sits on top of everything.
        self._viewport.paint_overlay = self._paint_viewport_overlay
        # When QScrollArea stretches the inner viewport widget taller
        # than ``content_h`` (because the populated cells don't fill the
        # scroll area), the marquee overlay must follow - otherwise the
        # rubber-band paint is clipped at the bottom of the last row
        # and a drag into the empty area below reads as "cut off".
        self._viewport.resize_hook = self._on_viewport_resize

        # Initial layout.
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
        """Replace the grid's contents with a fresh set of plugin keys.

        Used by :meth:`LoadoutPanel.refresh_from_registry` on Loadout
        switch - the new active Loadout's plugin set may differ from the
        previous one, and the grid rebuilds in place rather than
        re-instantiating. Selection is cleared (the previous selection
        rarely makes sense against new keys). Callers that need to
        re-attach signals to the freshly-created pills must invoke
        :func:`NukeSurvivalLoadout.ui.wiring.events.rewire_grid_pills` after this call.

        Returns ``True`` when a rebuild actually happened, ``False`` when
        ``keys`` matches the current set (no-op). Callers use this to
        avoid stacking duplicate signal connections on each refresh.
        """
        new_keys = list(keys)
        if new_keys == self._keys:
            return False

        # Tear down old cells + pills. setParent(None) detaches from the
        # Qt parent chain; deleteLater queues actual destruction so the
        # current event handler completes before the widget vanishes.
        for cell in self._cells:
            cell.setParent(None)
            cell.deleteLater()
        self._cells = []
        self._pills = []
        self._selected = set()
        # Stale group labels are aligned to the old key order; drop
        # them so a callers that doesn't immediately push fresh ones
        # via ``set_group_labels`` doesn't paint mismatched dividers.
        # Divider widgets themselves are torn down inside ``_relayout``.
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

        # Marquee overlay must stay above the freshly-created cells.
        self._marquee_overlay.raise_()
        self._relayout()
        self.selection_changed.emit([])
        return True

    def set_group_labels(self, labels: Sequence[Optional[str]]) -> None:
        """Set per-pill divider labels, aligned to :meth:`keys`.

        Pass a list of the same length as :meth:`keys`; each entry is
        the divider-bucket label for the matching pill, or ``None`` to
        place the pill in the "no divider above me" stream (used for
        alphabetical sort modes that don't group). A bucket transition
 - any change between consecutive non-``None`` labels - emits a
        :class:`_GroupDivider` strip above the first pill of the new
        bucket. The very first non-``None`` label also gets a leading
        divider header (every bucket is visibly labelled).

        Mismatched lengths reset to no-grouping rather than raise - the
        wiring layer occasionally races a recompute against a refresh;
        the worst case is one frame of un-divided pills before the next
        push lines up, which beats throwing an exception into a Qt
        slot. An empty list also clears any existing dividers.
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
        """Wire a pill's ``selection_requested`` signal - if present - to
        the grid's selection state.

        Pills own their own ``mousePressEvent`` and swallow body clicks
        (toggling the plugin's enabled state). Qt does not auto-propagate
        child mouse events, so a modifier-held click on a pill body would
        otherwise never reach the grid's marquee handler. The pill
        intercepts the modifier case and re-emits as a selection request
        carrying the ``Qt.KeyboardModifiers``; the grid maps shift → add
        and ctrl/cmd → smart toggle. Plain clicks still drop through to
        the pill's existing enable-toggle path.
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
            # Pills without a real Qt signal (duck-typed stand-ins)
            # quietly skip - the marquee/cell-padding selection path is
            # still in place for them.
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
            # Smart toggle - modifier-click on a selected pill removes
            # it, on an unselected pill adds it. Matches Finder /
            # Photoshop-style modifier semantics.
            self._selected = toggle_selection(
                self._selected, key, additive=True
            )
        else:
            # Defensive: pill only emits with a modifier held. If a
            # future caller emits without one, treat as plain-click
            # replace.
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
        # ``viewport()`` is the scroll area's viewport; we use its width
        # to drive the column count so the scrollbar doesn't cause loops.
        return int(self.viewport().width())

    def _relayout(self) -> None:
        vw = max(1, self._viewport_width())
        # Empty grid - show the placeholder and short-circuit. The label
        # spans the viewport so it stays centred when the panel resizes.
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

        # Place cells - they tile the viewport's usable width edge-to-edge
        # with no slack (``cell_widths`` divides usable / columns evenly).
        # Each pill is rendered at its **constant** sizeHint
        # (``pill._MIN_W × pill._MIN_H``) and centred inside its cell -
        # pills never squash or stretch. Any extra cell space (when the
        # viewport widens past the column-pack threshold) becomes
        # additional padding zone around the pill (where marquee drags
        # can start); the pill body itself
        # is one constant size across every viewport width.
        from NukeSurvivalLoadout.ui.pill import (
            _MIN_W as _PILL_W,
            _MIN_H as _PILL_H,
            _SHADOW_MARGIN as _PILL_SHADOW,
        )

        # Tear down any divider widgets from the previous layout.
        # Dividers are throwaway because their y depends on the new
        # column count and the new label list.
        for d in self._dividers:
            d.setParent(None)
            d.deleteLater()
        self._dividers = []
        self._divider_y_ranges = []

        # Resolve per-cell positions. When ``_group_labels`` is empty
        # (no grouping requested) ``layout_with_dividers`` returns the
        # byte-identical uniform layout - so the legacy non-grouped
        # path stays pixel-for-pixel unchanged.
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
            # Pill at constant size, centred. If the cell is narrower
            # than the pill (theoretically impossible because the
            # column-count math respects ``pill_min_width``), the pill
            # is clamped to fit so it doesn't overflow the cell.
            pill_w = min(_PILL_W, w)
            pill_h = min(_PILL_H, h)
            pill_x = (w - pill_w) // 2
            pill_y = (h - pill_h) // 2
            pill_rect = QtCore.QRect(pill_x, pill_y, pill_w, pill_h)
            pill.setGeometry(pill_rect)
            # Cell needs the **body** rect (inset by the pill's shadow
            # margin) - NOT the full widget rect - to paint the selection
            # halo. The pill widget reserves ``_PILL_SHADOW`` px around
            # its body for the drop shadow; if we hand the cell the
            # outer widget rect, the halo paints out at the shadow
            # boundary and reads as misaligned with the visible pill.
            # Pass the body rect so the halo hugs the rounded body
            # itself. The same inset also drives hit-testing - a click
            # in the shadow margin is "padding zone" and starts a
            # marquee instead of toggling the pill.
            body_rect = pill_rect.adjusted(
                _PILL_SHADOW, _PILL_SHADOW,
                -_PILL_SHADOW, -_PILL_SHADOW,
            )
            cell.set_pill_rect(body_rect)
            cell.show()
            pill.show()

        # Materialise divider widgets at the positions the layout
        # helper computed. Each divider is a fresh ``_GroupDivider``
        # because re-using cached widgets across re-layouts would
        # require keying them by (label, geometry) - cheaper to just
        # rebuild on every relayout (typically 5-10 dividers max).
        # Also record each divider's (y_top, y_bottom) on
        # ``_divider_y_ranges`` so the cell-hairline painter can hop
        # over the gutter zones cleanly.
        for label, dx, dy, dw, dh in divider_rects:
            divider = _GroupDivider(label, self._viewport)
            divider.setGeometry(QtCore.QRect(dx, dy, dw, dh))
            divider.show()
            self._dividers.append(divider)
            self._divider_y_ranges.append((dy, dy + dh))

        # Resize the inner viewport widget so the scroll area knows the
        # full content height and the scrollbar engages when needed.
        self._viewport.setMinimumHeight(content_h)
        self._viewport.resize(vw, content_h)
        # Marquee overlay must cover the full inner viewport widget - not
        # just ``content_h``. When the scroll area is taller than the
        # populated cells, QScrollArea (widgetResizable=True) stretches
        # the inner widget; use its actual height so rubber-band paint
        # extends into the empty area below the last populated row.
        overlay_h = max(content_h, self._viewport.height())
        self._marquee_overlay.setGeometry(0, 0, vw, overlay_h)
        self._marquee_overlay.raise_()

    def _on_viewport_resize(self, event) -> None:
        """Track the inner viewport widget's actual size.

        QScrollArea (``setWidgetResizable(True)``) resizes the inner
        widget to fill the scroll area when content is shorter than the
        visible area. The marquee overlay needs to follow so a drag
        into that empty region still paints the rubber-band; the cell
        dividers also need to repaint to fill the new extent.
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
    # Event-shape-independent entry points. ``_Cell`` forwards its own
    # press/move/release here so a click in the cell padding (between the
    # pill body and the cell edge) reaches the marquee logic. The
    # viewport's wrappers above call the same methods for clicks that
    # land directly on the viewport (gaps below the last populated row,
    # outer margins).
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
        # Update the end-point one last time so a release that fires
        # without an intervening move still has the right rect.
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
        # Zero-size marquee = a click without a drag. With a modifier
        # held, treat a click anywhere inside a cell (including its
        # padding zone) as targeting that cell's pill - "shift-click in
        # the grid unit selects it" per the user's mental model. Without
        # a modifier, a zero-size release on padding captures nothing
        # (the implicit selection-clear on press is the final state).
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
            # Any modifier (shift / ctrl / cmd) → **smart toggle**:
            #   * Every captured pill already selected ⇒ remove them.
            #   * Mixed / all-unselected captured ⇒ union (add).
            # A single modifier-click on a selected pill therefore
            # deselects it; on an unselected pill, adds it.
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
        """Viewport-level overlay paint - currently dividers only. The
        marquee rubber-band lives on a separate top-most child overlay
        (:class:`_MarqueeOverlay`) so it can sit above all pill bodies.
        """
        self._paint_cell_dividers(painter)

    def _paint_cell_dividers(self, painter: "QtGui.QPainter") -> None:
        """Draw 1 px hairlines between cells so the grid reads as
        discrete blocks rather than a flat tiled surface. Drawn on the
        viewport (not on each cell) so adjacent cells don't double-paint
        the same line.

        Lines extend across the full viewport - both axes - so the grid
        reads as a continuous lined plane even when the populated cells
        don't fill it. Empty grid slots below the last populated row
        and to the right of a partial last row still show their column
        and row structure.

        Horizontal hairlines are **gutter-aware**: each pill row's bottom
        edge gets a hairline IF that y doesn't fall strictly inside a
        gutter zone (``self._divider_y_ranges``). Below the last populated
        row, painting continues at uniform ``CELL_HEIGHT`` intervals so
        empty grid slots still read as grid surface. The hairlines stay
        (rather than being suppressed under grouping) so the grid keeps
        its lined feel; they simply align with the shifted pill rows.
        Vertical column lines paint regardless - column structure is
        stable.
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
            # Empty grid: uniform CELL_HEIGHT intervals from the top so
            # the placeholder area still shows row structure.
            y = GRID_MARGIN_V + CELL_HEIGHT
            while y < vp_h:
                painter.drawLine(0, y, vp_w, y)
                y += CELL_HEIGHT
        else:
            # Populated grid: paint a hairline at the bottom of each
            # unique pill row. Skip any candidate y that falls strictly
            # inside a gutter band - the gutter's own dark colour is
            # the row separator there, and a hairline through the
            # gutter would read as visual noise.
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
            # Past the last populated row, continue at uniform
            # CELL_HEIGHT intervals so the grid feels like it
            # extends past the pills (matches the legacy "infinite
            # grid" behaviour the ungrouped path always had).
            y = last_painted_y + CELL_HEIGHT
            while y < vp_h:
                if not _y_inside_any(y, self._divider_y_ranges):
                    painter.drawLine(0, y, vp_w, y)
                y += CELL_HEIGHT

        # Vertical lines: every column boundary, full viewport height,
        # so empty cells (right of a partial last row, or anywhere below
        # the last populated row) still show the column structure.
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
# Inner viewport widget - paints marquee overlay, forwards mouse events
# ---------------------------------------------------------------------------


class _MarqueeOverlay(QtWidgets.QWidget):
    """Top-most overlay child of the grid viewport - paints the marquee
    rubber-band box on top of every cell and pill.

    Cells live as children of the viewport, so anything painted on the
    viewport itself sits underneath them. The marquee needs to read as
    "drawn on top of the grid" so the user can see it crossing pill bodies
 - this overlay is raised above the cells and stays transparent to
    mouse events so the grid's existing press/move/release handlers on
    the viewport keep working.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        # Transparent to mouse so the underlying viewport keeps receiving
        # press / move / release - the overlay is purely a paint surface.
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
    """The scroll area's inner widget. Hosts cells; paints marquee overlay.

    Mouse-event forwarding is wired via callable attributes set by
    :class:`PluginsGrid` after construction. This keeps the viewport class
    Qt-only and the grid logic in one place.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        # Enable mouse-tracking so move events fire without a button held.
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
        # Accept so Qt grabs the mouse to this viewport for the rest of
        # the drag; otherwise QWidget's default ``ignore()`` lets the
        # press bubble to QScrollArea and subsequent move / release
        # events never reach our hooks.
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
        # Recessed grid background. Filled in paintEvent (not via
        # ``setAutoFillBackground`` + palette) so the colour stays
        # explicit and doesn't depend on the host palette inheritance
        # chain - important inside the QScrollArea viewport hierarchy.
        painter = QtGui.QPainter(self)
        try:
            r, g, b = GRID_BG_COLOUR
            painter.fillRect(self.rect(), QtGui.QColor(r, g, b))
            if self.paint_overlay is not None:
                self.paint_overlay(painter)
        finally:
            painter.end()

