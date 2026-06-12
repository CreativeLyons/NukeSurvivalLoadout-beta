"""Loadout selector strip.

The strip is divided into horizontal zones, top to bottom:

1. Active Loadout row - the dropdown plus per-Loadout management icon
   buttons (Revert / Rename / Duplicate / Delete).
2. File operations row - ``Save`` / ``Save As`` / ``Import`` / ``Export``.

Behavioral contracts:

* Qt imports only via :mod:`NukeSurvivalLoadout.compat`. No direct
  ``PySide2`` / ``PySide6``.
* The ``Global`` row always sorts to the bottom of the dropdown.
* Rename + Delete are disabled when Global is the current selection.
  Duplicate is enabled for every Loadout (including Global - duplicating
  Global produces a fresh user Loadout).
* ``(*)`` indicator appears on the active Loadout's name when unsaved.
  The dropdown renders a dirty flag driven from the domain layer; the
  strip never owns the flag.
* Active-row highlight: persistent blue background inside the open
  dropdown; the active Loadout name is large, bold, white.
* ``Save`` / ``Save As`` / ``Export`` grey out when no Plugins are
  detected, with a tooltip. ``Import`` remains enabled. ``Save`` also
  greys when Global is active (Global is read-only).
* Signal-out only - the strip never writes files itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from NukeSurvivalLoadout import compat
from NukeSurvivalLoadout.ui import _theme
from NukeSurvivalLoadout.ui._buttons import HybridTextButton, install_clickable_cursor

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# ---------------------------------------------------------------------------
# Constants - palette, label, reserved names
# ---------------------------------------------------------------------------

GLOBAL_LOADOUT_NAME = "Global"

# Custom is the wildcard scratch loadout (see ``NukeSurvivalLoadout.constants``
# ``DEFAULT_CUSTOM_LOADOUT_STEM``). The filename-validation layer
# rejects ``Custom`` as a reserved stem (``filename_rules.py:141-142``)
# so a direct Save against the Custom loadout returns ``Blocked`` and
# silently no-ops. We mirror that contract here at the button-state
# layer: Save is disabled while Custom is active. Save As remains
# enabled - it prompts for a new non-reserved name, which is the
# supported way to promote Custom edits into a permanent Loadout.
# If a Save cannot succeed, the Save button is disabled rather than
# left clickable as a no-op.
CUSTOM_LOADOUT_NAME = "Custom"

# Active-row blue - pulled from `_theme.ACTIVE_ROW_BLUE_*` so the JSX
# prototype's `rgba(86, 160, 244, 0.28)` value lives in exactly one place.
ACTIVE_ROW_BG = QtGui.QColor(
    *_theme.ACTIVE_ROW_BLUE_RGB, _theme.ACTIVE_ROW_BLUE_ALPHA
)
# JSX: .menu-item--active:hover { background: rgba(86,160,244,0.34); }
ACTIVE_ROW_HOVER_BG = QtGui.QColor(*_theme.ACTIVE_ROW_BLUE_RGB, 87)  # ≈ 0.34 * 255
ACTIVE_ROW_FG = QtGui.QColor(255, 255, 255)

# Inactive hover inside the open dropdown - JSX `.menu-item:hover` is a
# soft white tint, not Nuke-orange. Active-only hover stays blue.
INACTIVE_HOVER_BG = QtGui.QColor(255, 255, 255, 13)  # ≈ 0.05 * 255
INACTIVE_HOVER_FG = QtGui.QColor(255, 255, 255)

# Active-loadout dot - same orange role, smaller geometry. The halo is
# the JSX `box-shadow: 0 0 0 3px rgba(238,150,38,0.12)` translated to a
# flat outer ring (no blur).
ACTIVE_DOT_COLOR = QtGui.QColor(*_theme.NUKE_ORANGE_RGB)
ACTIVE_DOT_HALO = QtGui.QColor(*_theme.NUKE_ORANGE_RGB, 31)  # ≈ 0.12 * 255
ACTIVE_DOT_SIZE = 6
ACTIVE_DOT_HALO_PAD = 3  # px ring around the dot

# Default row treatment.
NORMAL_BG = QtGui.QColor(48, 48, 48)
NORMAL_FG = QtGui.QColor(218, 218, 218)  # #dadada - bold readable inactive

# Panic button red - "scary action" energy. Engaged state is a brighter,
# fully-saturated red so the user can see at a glance that user-added
# Plugins are currently hidden.
PANIC_RED_REST = "#9c2a2a"
PANIC_RED_ENGAGED = "#ff3a3a"

NO_PLUGINS_TOOLTIP = "Nothing to save. Add a Plugins Folder first."


# ---------------------------------------------------------------------------
# Loadout data carrier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Loadout:
    """Plain carrier used by the strip - domain layer wires real Loadouts.

    ``is_global`` lets the widget enforce the Global-at-bottom invariant
    and the Rename/Delete/Save greying.

    ``is_dirty`` is an optional per-row dirty hint. Production-wise the
    only Loadout that *should* be dirty is the active one (in-memory
    edits live against the active selection), but the strip honours the
    hint on every row so any caller can show what a dirty row looks
    like inside the open dropdown. The strip's own ``set_dirty()`` flag
    still drives the ``(*)`` suffix on the active row independently.
    """

    name: str
    is_global: bool = False
    is_dirty: bool = False


# ---------------------------------------------------------------------------
# Custom item delegate - active-row blue + hover yellow-orange
# ---------------------------------------------------------------------------


class _LoadoutItemDelegate(QtWidgets.QStyledItemDelegate):
    """Item delegate for the Active-Loadout dropdown's open list view.

    Matches the JSX prototype's ``.menu-item`` vocabulary:

    * Rows whose data carries the "active" flag paint with a persistent
      translucent blue (``rgba(86,160,244,0.28)``). Hovering the active
      row deepens the blue to ``0.34`` - still no orange.
    * Inactive rows hover with a soft white tint
      (``rgba(255,255,255,0.05)``) - *not* Nuke-orange. The orange
      hover-bg was a Nuke heuristic that contradicts the JSX.
    * The active row carries a small orange dot with a flat 12%-alpha
      halo (JSX's ``box-shadow`` translated to a no-blur outer ring).
    """

    #: Custom role flagging which row is the currently active Loadout.
    ACTIVE_ROW_ROLE = QtCore.Qt.UserRole + 1

    # Row geometry - items pack flush (no vertical gap between rows) so
    # the open menu reads like the canonical NSL_Design_System_New target.
    # The fill rect is inset horizontally only; the rounded corners only
    # appear around the active/hover fill, not the row itself.
    _ROW_PAD_X = 8       # horizontal inset of the fill from the row edge
    _ROW_PAD_Y = 0       # vertical inset - flush rows, no gap between fills
    _DOT_TEXT_GAP = 12   # JSX `gap: 12px` between dot and label
    _ROW_RADIUS = 5      # rounded fill on active/hover - matches the target

    def paint(self, painter, option, index):  # noqa: D401 - Qt override
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        is_active = bool(index.data(self.ACTIVE_ROW_ROLE))
        is_hover = bool(opt.state & QtWidgets.QStyle.State_MouseOver)
        is_selected = bool(opt.state & QtWidgets.QStyle.State_Selected)
        hovered = is_hover or is_selected

        painter.save()
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            # Bold + a touch larger than body - matches the trigger pill
            # and the canonical target's open-menu type weight.
            font = QtGui.QFont(opt.font)
            font.setBold(True)
            font.setPointSizeF(font.pointSizeF() + 1.0)
            painter.setFont(font)

            # Pick the background colour. Order matters - active wins
            # over inactive-hover; only its own active-hover shade lifts
            # it. Inactive hover is a soft white tint per JSX.
            if is_active and hovered:
                bg, fg = ACTIVE_ROW_HOVER_BG, ACTIVE_ROW_FG
            elif is_active:
                bg, fg = ACTIVE_ROW_BG, ACTIVE_ROW_FG
            elif hovered:
                bg, fg = INACTIVE_HOVER_BG, INACTIVE_HOVER_FG
            else:
                bg, fg = None, NORMAL_FG

            # Background fill is drawn inside a slightly-inset row rect
            # so the 2px corner radius is visible (matches JSX menu-item).
            fill_rect = opt.rect.adjusted(
                self._ROW_PAD_X // 2, self._ROW_PAD_Y,
                -(self._ROW_PAD_X // 2), -self._ROW_PAD_Y,
            )
            if bg is not None:
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(bg)
                painter.drawRoundedRect(
                    QtCore.QRectF(fill_rect), self._ROW_RADIUS, self._ROW_RADIUS
                )

            # Dot column - drawn for every row so the text column lines
            # up identically between active (with dot) and inactive (dot
            # slot left blank, per JSX `.dot-empty`).
            dot_slot_left = opt.rect.left() + self._ROW_PAD_X + 6
            dot_cx = dot_slot_left + ACTIVE_DOT_SIZE // 2
            dot_cy = opt.rect.center().y()

            if is_active:
                # Halo first (no blur - a flat outer ring per the engraved
                # vocabulary), then the inner dot on top.
                halo_r = (ACTIVE_DOT_SIZE / 2.0) + ACTIVE_DOT_HALO_PAD
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(ACTIVE_DOT_HALO)
                painter.drawEllipse(
                    QtCore.QPointF(dot_cx, dot_cy), halo_r, halo_r
                )
                painter.setBrush(ACTIVE_DOT_COLOR)
                painter.drawEllipse(
                    QtCore.QPointF(dot_cx, dot_cy),
                    ACTIVE_DOT_SIZE / 2.0, ACTIVE_DOT_SIZE / 2.0,
                )

            text_left = (
                dot_slot_left + ACTIVE_DOT_SIZE + self._DOT_TEXT_GAP
            )
            painter.setPen(fg)
            text_rect = opt.rect.adjusted(
                text_left - opt.rect.left(), 0, -self._ROW_PAD_X, 0
            )
            painter.drawText(
                text_rect,
                int(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft),
                opt.text or index.data(QtCore.Qt.DisplayRole) or "",
            )
        finally:
            painter.restore()

    def sizeHint(self, option, index):  # noqa: D401 - Qt override
        hint = super().sizeHint(option, index)
        # Row height shaved 36 → 28 px to match the 26 px trigger pill
        # height it pops below (+2 px breathing for list-item legibility).
        # Bold + a touch larger font still fits comfortably at this size;
        # gaps between rows stay killed via _ROW_PAD_Y = 0.
        return QtCore.QSize(hint.width(), max(hint.height(), 28))


# ---------------------------------------------------------------------------
# Glyph icons for the per-Loadout action buttons (rename / duplicate / delete)
# ---------------------------------------------------------------------------


_GLYPH_COLOR = QtGui.QColor("#dcdcdc")
_GLYPH_HOVER_COLOR = QtGui.QColor("#ffffff")
_GLYPH_DISABLED_COLOR = QtGui.QColor("#4a4a4a")
_GLYPH_SIZE = 14
# Paint at 2x and tell Qt the pixmap has a devicePixelRatio of 2. Qt
# downsamples with anti-aliasing to the logical size, giving crisp
# glyphs on both 1x and 2x displays. Rendering at the logical 14x14
# source directly is fuzzy on retina because integer pen widths land
# on sub-pixel boundaries.
_GLYPH_SUPERSAMPLE = 2


def _paint_glyph_pixmap(kind: str, color: QtGui.QColor) -> QtGui.QPixmap:
    """Paint one variant pixmap of the per-Loadout glyph (rename /
    duplicate / delete / revert).

    Factored from :func:`_make_glyph_icon` so the icon can carry Normal
    (rest), Active (hover) and Disabled pixmaps. Qt picks the right
    variant based on widget state. Painted at 2x and flagged
    devicePixelRatio=2 so the glyph stays sharp at 1x and 2x displays.
    """
    dpr = _GLYPH_SUPERSAMPLE
    size = _GLYPH_SIZE * dpr
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(color)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    painter.setBrush(QtCore.Qt.NoBrush)

    # JSX viewBox is 16; ``s`` scales from the 16-unit logical grid to
    # the 2x-supersampled pixel grid. All glyph geometry below uses
    # the same ``s`` factor, so the painted lines scale uniformly.
    s = size / 16.0

    if kind == "rename":
        # Lucide-style pencil: a long diagonal body from upper-right to
        # lower-left, with a clear eraser cap at the top and a sharp tip
        # at the bottom. Geometry on the 16-unit grid:
        #   * cap (eraser): a 4-unit-wide rect at the top-right corner
        #   * body: a long diagonal parallelogram
        #   * tip: pointed lower-left
        pen.setWidthF(1.6 * s)
        painter.setPen(pen)
        body = QtGui.QPainterPath()
        body.moveTo(11.5 * s, 2 * s)    # top of cap
        body.lineTo(14 * s, 4.5 * s)    # right edge of cap
        body.lineTo(5.5 * s, 13 * s)    # diagonal down to tip start
        body.lineTo(3 * s, 13 * s)      # tip - pointed left edge
        body.lineTo(3 * s, 10.5 * s)    # tip - bottom
        body.closeSubpath()
        painter.drawPath(body)
        # Seam between eraser cap and shaft.
        painter.drawLine(
            QtCore.QPointF(9 * s, 4.5 * s),
            QtCore.QPointF(11.5 * s, 7 * s),
        )
    elif kind == "duplicate":
        # Two square pages, offset diagonally - the back page peeks from
        # the top-left, the front page sits to the bottom-right. Rounded
        # corners read as "paper" rather than generic outlines. Draw the
        # back stroked, then the front with a subtle inner fill that
        # masks the back so the offset is unambiguous.
        pen.setWidthF(1.4 * s)
        painter.setPen(pen)
        back = QtCore.QRectF(2 * s, 2 * s, 9 * s, 9 * s)
        front = QtCore.QRectF(5 * s, 5 * s, 9 * s, 9 * s)
        painter.drawRoundedRect(back, 1.2 * s, 1.2 * s)
        # Fill the front rect with the icon background colour to "punch
        # out" the back rectangle's bottom-right corner, then stroke it
        # so it reads as a separate page.
        painter.setBrush(QtCore.Qt.transparent)
        painter.save()
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
        painter.fillRect(front, QtCore.Qt.transparent)
        painter.restore()
        painter.drawRoundedRect(front, 1.2 * s, 1.2 * s)
    elif kind == "delete":
        pen.setWidthF(1.8 * s)
        painter.setPen(pen)
        painter.drawLine(QtCore.QPointF(4 * s, 4 * s), QtCore.QPointF(12 * s, 12 * s))
        painter.drawLine(QtCore.QPointF(12 * s, 4 * s), QtCore.QPointF(4 * s, 12 * s))
    elif kind == "revert":
        # Counter-clockwise circular arrow ("revert" / "undo" / "go
        # back to where you started").
        #
        # The arrowhead sits at 90 degrees (top of the icon) pointing
        # LEFT, toward the Loadout dropdown to its left in the panel
        # row, so it reads as "go back to the Loadout" rather than
        # "go forward" toward the next button. Pen width and arrowhead
        # size are tuned for legibility at the 14 px target, and the
        # arc gap is 240 degrees so the head has breathing room above
        # the upper-left arc terminus.
        pen.setWidthF(2.0 * s)
        painter.setPen(pen)
        cx = 8 * s
        cy = 8.5 * s
        r = 4.0 * s
        rect = QtCore.QRectF(cx - r, cy - r, 2 * r, 2 * r)
        # Arc: starts at 210° (lower-left, around 8 o'clock), sweeps
        # 240° CCW → ends at 90° (12 o'clock, top of the icon). Open
        # gap covers the upper-left quadrant + a bit of the top.
        painter.drawArc(rect, int(210 * 16), int(240 * 16))
        # Arrowhead at the END of the sweep (90° on the circle = top
        # of the icon), pointing along the CCW tangent. At 90°, the
        # CCW tangent direction in Qt screen coordinates is (-1, 0)
        # - straight LEFT. So the arrow tip extends left of the arc
        # terminus, reading as "rotation is heading this way, back
        # toward the Loadout dropdown."
        end_rad = math.radians(90)
        ex = cx + r * math.cos(end_rad)  # = cx
        ey = cy - r * math.sin(end_rad)  # = cy - r (top of arc)
        # CCW tangent at angle θ (Qt screen coords, y flipped):
        #   d/dθ (cos θ, -sin θ) = (-sin θ, -cos θ)
        tx = -math.sin(end_rad)  # -1
        ty = -math.cos(end_rad)  # 0
        # Arrow sized so the triangle reads as an unmistakable
        # arrowhead at the 14 px target.
        arrow_len = 4.6 * s
        arrow_half = 3.0 * s
        tip_x = ex + arrow_len * tx
        tip_y = ey + arrow_len * ty
        # Perpendicular to tangent (rotate 90° CCW): (-ty, tx)
        px = -ty
        py = tx
        arrow = QtGui.QPainterPath()
        arrow.moveTo(tip_x, tip_y)
        arrow.lineTo(ex + arrow_half * px, ey + arrow_half * py)
        arrow.lineTo(ex - arrow_half * px, ey - arrow_half * py)
        arrow.closeSubpath()
        painter.fillPath(arrow, color)

    painter.end()
    # Flag the pixmap as a 2x source. Qt's icon rendering will treat
    # the painted surface as covering ``_GLYPH_SIZE`` logical pixels
    # (14 px) but use the full 28-px raster for anti-aliased
    # downsampling on retina displays.
    pixmap.setDevicePixelRatio(_GLYPH_SUPERSAMPLE)
    return pixmap


def _make_glyph_icon(kind: str) -> QtGui.QIcon:
    """Return a 14×14 rest-state ``QIcon`` for the given action kind.

    For the hover-brighten behaviour on per-Loadout action buttons,
    construct a :class:`_GlyphIconButton` instead - that subclass
    holds both rest + hover pixmaps and swaps them explicitly on
    enter / leave (QIcon's ``Active`` mode is unreliable when the
    button carries a custom stylesheet, because the QSS ``:hover``
    rule on the same widget masks the icon-state transition).
    """
    return QtGui.QIcon(_paint_glyph_pixmap(kind, _GLYPH_COLOR))


class _GlyphIconButton(QtWidgets.QPushButton):
    """``QPushButton`` that brightens its glyph icon to white on hover.

    Swaps ``setIcon`` explicitly in :meth:`enterEvent` / :meth:`leaveEvent`
    so the colour change happens regardless of any QSS the caller has
    applied to the button (the QSS ``:hover`` rule normally masks
    QIcon's ``Active`` mode transition).
    """

    def __init__(
        self,
        kind: str,
        parent: Optional["QtWidgets.QWidget"] = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        # Bundle a Disabled-state pixmap on the rest icon so Qt renders
        # the glyph in a low-contrast grey whenever the button is
        # disabled, instead of the Qt-default faded-Normal which still
        # reads too prominent against the near-transparent button
        # background.
        self._rest_icon = QtGui.QIcon()
        self._rest_icon.addPixmap(
            _paint_glyph_pixmap(kind, _GLYPH_COLOR), QtGui.QIcon.Normal
        )
        self._rest_icon.addPixmap(
            _paint_glyph_pixmap(kind, _GLYPH_DISABLED_COLOR),
            QtGui.QIcon.Disabled,
        )
        self._hover_icon = QtGui.QIcon(
            _paint_glyph_pixmap(kind, _GLYPH_HOVER_COLOR)
        )
        self.setIcon(self._rest_icon)
        # Panel-wide interactive cursor - pointing hand while enabled,
        # arrow when disabled (e.g. Delete is greyed when Global is the
        # active Loadout; Duplicate is greyed when there's no active
        # user Loadout to copy).
        install_clickable_cursor(self)

    def enterEvent(self, event):  # noqa: N802 - Qt override
        if self.isEnabled():
            self.setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self.setIcon(self._rest_icon)
        super().leaveEvent(event)


def _make_active_dot_icon(size: int = 18) -> QtGui.QIcon:
    """Return a small orange-dot ``QIcon`` for the closed combo trigger.

    The icon paints over Qt's standard combo-box item-icon slot so the
    closed state of the loadout combo shows the same "you are here" dot
    that the delegate paints inside the open dropdown - inner orange dot
    on top of a 12%-alpha halo ring (JSX `box-shadow` translated to a
    flat outer ellipse, no blur).
    """
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setPen(QtCore.Qt.NoPen)

    cx = cy = size / 2.0
    halo_r = (ACTIVE_DOT_SIZE / 2.0) + ACTIVE_DOT_HALO_PAD
    painter.setBrush(ACTIVE_DOT_HALO)
    painter.drawEllipse(QtCore.QPointF(cx, cy), halo_r, halo_r)
    painter.setBrush(ACTIVE_DOT_COLOR)
    painter.drawEllipse(
        QtCore.QPointF(cx, cy),
        ACTIVE_DOT_SIZE / 2.0, ACTIVE_DOT_SIZE / 2.0,
    )
    painter.end()
    return QtGui.QIcon(pixmap)


# ---------------------------------------------------------------------------
# Custom trigger pill - replaces QComboBox's closed state so we can paint
# the orange-dot-with-halo + bold name + chevron exactly per the design.
# ---------------------------------------------------------------------------


class _LoadoutTrigger(QtWidgets.QAbstractButton):
    """Click-to-open trigger pill for the active Loadout.

    Painted to match the canonical NSL_Design_System_New design (closed
    state of the Loadout dropdown):

    * Pill background ``rgba(255,255,255,0.04)`` with a 1px ``#141414``
      border and a 1px inner highlight at the top - the JSX
      ``.loadout-trigger`` chrome.
    * Orange dot with a flat 12%-alpha halo at the left (the "you are
      here" anchor that mirrors the dropdown's active row).
    * Bold white Loadout name in the middle, with the ``(*)`` suffix
      appended by the strip when dirty.
    * Down-chevron at the far right, painted in ``#c8c8c8``.

    Emits :attr:`QAbstractButton.clicked` - the strip listens for that to
    pop the menu.
    """

    _PILL_RADIUS = 6
    _PAD_X = 14
    _DOT_TEXT_GAP = 10
    _CHEV_W = 16
    _CHEV_PAD_R = 10

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._name: str = ""
        self._show_dot: bool = True
        self._hover: bool = False
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        # 26 px trigger height so the loadout strip reads at the same
        # chrome height as HybridTextButton across the panel. Leaves
        # ~5 px headroom around the 11 pt bold label, with no clipping
        # at this font size.
        self.setMinimumHeight(26)
        self.setMinimumWidth(280)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        # Bold + a touch larger than body - the JSX uses 14px bold for
        # the trigger name (panel body is 12-13px).
        f = self.font()
        f.setPointSizeF(f.pointSizeF() + 1.0)
        f.setBold(True)
        self.setFont(f)

    # -- public API ----------------------------------------------------

    def setText(self, text: str) -> None:  # noqa: D401 - keep Qt naming
        self._name = text
        self.update()

    def text(self) -> str:  # noqa: D401 - keep Qt naming
        return self._name

    def set_show_dot(self, show: bool) -> None:
        if show == self._show_dot:
            return
        self._show_dot = bool(show)
        self.update()

    # -- hover state ---------------------------------------------------

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self._hover = False
        self.update()
        super().leaveEvent(event)

    # -- painting ------------------------------------------------------

    def paintEvent(self, _ev) -> None:  # noqa: D401 - Qt override
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        # Pill body - flat fill + 1px engraved border. On hover, lift
        # the body alpha and brighten the border slightly to mirror the
        # rename / duplicate / delete icon buttons' hover treatment.
        # Quiet enough to read as "interactive" rather than "primary".
        if self._hover:
            border_color = QtGui.QColor("#2a2a2a")
            body_alpha = 20  # ≈ 0.08 alpha
            highlight_alpha = 20
        else:
            border_color = QtGui.QColor("#141414")
            body_alpha = 10  # ≈ 0.04 alpha
            highlight_alpha = 10

        p.setPen(QtGui.QPen(border_color, 1))
        p.setBrush(QtGui.QColor(255, 255, 255, body_alpha))
        p.drawRoundedRect(rect, self._PILL_RADIUS, self._PILL_RADIUS)

        # 1px highlight on the top inside edge - keeps the engraved look
        # without introducing a gradient or blur.
        hi_rect = QtCore.QRectF(
            rect.left() + 1, rect.top() + 1, rect.width() - 2, 1
        )
        p.fillRect(hi_rect, QtGui.QColor(255, 255, 255, highlight_alpha))

        # Dot + halo (only when there's an active selection).
        dot_cy = rect.center().y()
        dot_cx = rect.left() + self._PAD_X + ACTIVE_DOT_SIZE / 2.0
        if self._show_dot:
            p.setPen(QtCore.Qt.NoPen)
            halo_r = (ACTIVE_DOT_SIZE / 2.0) + ACTIVE_DOT_HALO_PAD
            p.setBrush(ACTIVE_DOT_HALO)
            p.drawEllipse(QtCore.QPointF(dot_cx, dot_cy), halo_r, halo_r)
            p.setBrush(ACTIVE_DOT_COLOR)
            p.drawEllipse(
                QtCore.QPointF(dot_cx, dot_cy),
                ACTIVE_DOT_SIZE / 2.0, ACTIVE_DOT_SIZE / 2.0,
            )

        # Name text - bold white, vertically centered.
        text_left = (
            rect.left() + self._PAD_X
            + ACTIVE_DOT_SIZE + self._DOT_TEXT_GAP
        )
        text_right = (
            rect.right() - self._CHEV_PAD_R - self._CHEV_W - 6
        )
        text_rect = QtCore.QRectF(
            text_left, rect.top(), text_right - text_left, rect.height()
        )
        if not self.isEnabled():
            p.setPen(QtGui.QColor(122, 122, 122))  # #7a7a7a
        else:
            p.setPen(QtGui.QColor(255, 255, 255))
        p.drawText(
            text_rect,
            int(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft),
            self._name,
        )

        # Down chevron - small painted polyline, no glyph font.
        cx = rect.right() - self._CHEV_PAD_R - self._CHEV_W / 2.0
        cy = rect.center().y()
        pen = QtGui.QPen(QtGui.QColor(200, 200, 200), 1.6)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        path = QtGui.QPainterPath()
        path.moveTo(cx - 4, cy - 1.5)
        path.lineTo(cx, cy + 2.5)
        path.lineTo(cx + 4, cy - 1.5)
        p.drawPath(path)

    def sizeHint(self) -> QtCore.QSize:  # noqa: D401 - Qt override
        return QtCore.QSize(self.minimumWidth(), self.minimumHeight())


# ---------------------------------------------------------------------------
# Popup menu - custom top-level widget hosting a QListView with our delegate.
# Replaces QComboBox's stock popup so we can paint the rounded #2c2c2c
# container + the rounded-fill active row exactly per the design.
# ---------------------------------------------------------------------------


class _LoadoutPopup(QtWidgets.QWidget):
    """Frameless popup with a QListView for the open-menu state.

    Stylistic contract (mirrors the NSL_Design_System_New ``.menu``):

    * Outer container painted ``#2c2c2c`` with a 1px ``#1c1c1c`` border
      and an 8px corner radius. 4px inner padding around the list.
    * Items use :class:`_LoadoutItemDelegate` - translucent-blue active
      row, white-tint inactive hover, orange dot+halo, transparent
      placeholder slot for the inactive rows.
    """

    #: Emitted when the user picks a row. Payload is the Loadout name.
    item_selected = QtCore.Signal(str)

    _RADIUS = 8
    _PADDING = 4

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(
            parent,
            QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.NoDropShadowWindowHint,
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        # Pre-realise the native window so the very first show_under()
        # has a top-level NSWindow to position against. Without this,
        # the first ``move()`` on macOS treats coordinates as if the
        # popup were a child widget - and the popup lands inside the
        # trigger instead of below it.
        self.setAttribute(QtCore.Qt.WA_NativeWindow, True)
        self.winId()

        # Stored intended height - ``self.height()`` can return stale
        # data before the popup is shown, so we keep the value we
        # computed in :meth:`set_items` and use it directly in
        # :meth:`show_under` for the geometry.
        self._intended_height: int = 0

        self._list = QtWidgets.QListView(self)
        self._list.setObjectName("nsl_loadout_popup_list")
        self._model = QtGui.QStandardItemModel(self._list)
        self._list.setModel(self._model)
        self._delegate = _LoadoutItemDelegate(self._list)
        self._list.setItemDelegate(self._delegate)
        self._list.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._list.setMouseTracking(True)
        self._list.setUniformItemSizes(True)
        self._list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )
        # No global QSS on the QListView frame - we paint the container
        # ourselves. A minimal sheet kills Fusion's default item bg so
        # only the delegate's paint shows through.
        self._list.setStyleSheet(
            "QListView { background: transparent; border: none;"
            " outline: 0; }"
        )

        lo = QtWidgets.QVBoxLayout(self)
        lo.setContentsMargins(
            self._PADDING, self._PADDING, self._PADDING, self._PADDING
        )
        lo.addWidget(self._list)

        self._list.clicked.connect(self._on_clicked)

    # ------------------------------------------------------------------

    def set_items(
        self, loadouts: Sequence["Loadout"], active: Optional[str],
        display_for: Optional["callable"] = None,
    ) -> None:
        """Replace the list contents.

        ``display_for`` lets the strip inject the ``(*)`` suffix on the
        active row without the popup having to know about dirty state.
        """
        self._model.clear()
        for lo in loadouts:
            text = display_for(lo) if display_for else lo.name
            it = QtGui.QStandardItem(text)
            it.setEditable(False)
            it.setData(lo.name, QtCore.Qt.UserRole)
            it.setData(
                lo.name == active,
                _LoadoutItemDelegate.ACTIVE_ROW_ROLE,
            )
            self._model.appendRow(it)
        # Resize the popup vertically to fit content + 2*padding.
        n = self._model.rowCount()
        row_h = 28  # delegate sizeHint floor; see _LoadoutItemDelegate
        list_h = max(row_h, n * row_h)
        # +2 for the QListView's frame compensator inside the layout.
        self._intended_height = list_h + 2 * self._PADDING + 2
        self.setFixedHeight(self._intended_height)

    def show_under(self, anchor: QtWidgets.QWidget) -> None:
        """Position the popup just below ``anchor`` (the trigger pill).

        Computes the anchor's bottom-left in the top-level window's
        coordinate system, then maps THAT to global via the top-level
        window. ``QWidget.mapToGlobal`` on a deeply-nested child widget
        can return parent-relative coords on first invocation under
        certain macOS / Qt frameless-popup combinations - going via the
        top-level window sidesteps that.

        The native popup window is pre-realised in :meth:`__init__`, so
        the first ``move()`` operates in global screen coords as
        expected on every platform.
        """
        anchor.ensurePolished()
        top = anchor.window()
        if top is None:
            top = anchor

        # Anchor's bottom-left, expressed in the top-level window's
        # coordinate system. ``mapTo(top, pt)`` walks the parent chain
        # accumulating ``QWidget.pos()`` at each step - never fails to
        # account for the click anchor's true on-screen position.
        anchor_bottom_left_in_top = anchor.mapTo(
            top, QtCore.QPoint(0, anchor.height())
        )
        # Translate top-level → global via the top-level window itself.
        origin = top.mapToGlobal(anchor_bottom_left_in_top)
        origin.setY(origin.y() + 4)

        width = max(anchor.width(), 220)
        height = self._intended_height or self.height() or 200

        self.setFixedSize(width, height)
        self.move(origin)
        self.show()
        self.raise_()
        self.activateWindow()
        self._list.setFocus()

    # ------------------------------------------------------------------

    def _on_clicked(self, idx: QtCore.QModelIndex) -> None:
        name = idx.data(QtCore.Qt.UserRole)
        if name:
            self.item_selected.emit(name)
        self.hide()

    def paintEvent(self, _ev) -> None:  # noqa: D401 - Qt override
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(QtGui.QColor("#1c1c1c"), 1))
        p.setBrush(QtGui.QColor("#2c2c2c"))
        p.drawRoundedRect(rect, self._RADIUS, self._RADIUS)
        # 1px engraved highlight (the JSX inset top highlight).
        hi = QtCore.QRectF(rect.left() + 1, rect.top() + 1, rect.width() - 2, 1)
        p.fillRect(hi, QtGui.QColor(255, 255, 255, 10))


# ---------------------------------------------------------------------------


_ACTIVE_DOT_ICON: Optional[QtGui.QIcon] = None


def _active_dot_icon() -> QtGui.QIcon:
    """Memoise the dot icon - built once, reused for every active row."""
    global _ACTIVE_DOT_ICON
    if _ACTIVE_DOT_ICON is None:
        _ACTIVE_DOT_ICON = _make_active_dot_icon()
    return _ACTIVE_DOT_ICON


# ---------------------------------------------------------------------------
# The strip itself
# ---------------------------------------------------------------------------


class LoadoutStrip(QtWidgets.QWidget):
    """Active Loadout dropdown + per-Loadout buttons + file ops + panic.

    Signals (signal-out only - the strip never writes state itself):

    * ``loadout_selected(str)`` - user picked a Loadout from the dropdown.
    * ``rename_requested(str)`` - Rename button clicked; payload is the
      Loadout name as currently shown in the dropdown.
    * ``duplicate_requested(str)`` - Duplicate button clicked.
    * ``delete_requested(str)`` - Delete button clicked.
    * ``save_requested()``
    * ``save_as_requested()``
    * ``import_requested()``
    * ``export_requested()``
    * ``panic_toggled(bool)`` - panic button engaged/disengaged.

    Inbound API (slot-style methods):

    * :meth:`set_loadouts` - replace the list of Loadouts. Re-sorts Global
      to the bottom.
    * :meth:`set_active_loadout` - change which row carries the
      ``(*)``-eligible active-row treatment.
    * :meth:`set_dirty` - toggle the ``(*)`` indicator on the active name.
    * :meth:`set_plugins_detected` - drive Save/Save As/Export greying.
    * :meth:`set_panic_engaged` - programmatically reflect panic state.
    """

    # --- signals -----------------------------------------------------------

    loadout_selected = QtCore.Signal(str)
    rename_requested = QtCore.Signal(str)
    duplicate_requested = QtCore.Signal(str)
    delete_requested = QtCore.Signal(str)
    # Discard in-memory edits, reload active Loadout from disk. Payload
    # is the active Loadout name (matches the rename/duplicate/delete
    # pattern).
    revert_requested = QtCore.Signal(str)
    save_requested = QtCore.Signal()
    save_as_requested = QtCore.Signal()
    import_requested = QtCore.Signal()
    export_requested = QtCore.Signal()
    panic_toggled = QtCore.Signal(bool)

    # ----------------------------------------------------------------------

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        # Internal state - all read-only from outside, owned by the strip.
        self._loadouts: List[Loadout] = []
        self._active_name: Optional[str] = None
        self._dirty: bool = False
        self._plugins_detected: bool = True
        self._panic_engaged: bool = False

        self._build_ui()
        self._wire_signals()
        self._refresh_button_states()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(8)

        # --- Active Loadout row ---------------------------------------
        active_row = QtWidgets.QHBoxLayout()
        active_row.setSpacing(8)

        # "Loadout:" label - JSX `.loadout-label` is bold white, 15px,
        # sitting flush-left of the trigger. Inside Nuke the body font is
        # ~10pt; bumping the label +3pt and bolding it carries the same
        # "title for the row" weight without inventing a new font size.
        self.lbl_loadout = QtWidgets.QLabel("Loadout:", self)
        self.lbl_loadout.setObjectName("nsl_loadout_label")
        # Nuke applies an app-wide stylesheet after widget construction
        # that overrides per-widget setFont() calls on QLabel. The fix is
        # to use a high-specificity QSS rule keyed on the objectName so it
        # wins the cascade. font-size in pt is honoured even when Nuke's
        # app stylesheet sets a smaller default. 14pt + bold lands at the
        # "section title" visual weight.
        self.lbl_loadout.setStyleSheet(
            "QLabel#nsl_loadout_label {"
            "  color: #ffffff;"
            "  font-size: 14pt;"
            "  font-weight: bold;"
            "}"
        )
        # Hard min-width prevents the active_row layout from clipping
        # the label when the strip is squeezed by a small splitter.
        # The floor is built from a QFontMetrics for the EXPECTED
        # post-QSS font (14 pt bold), not the label's current font: at
        # construction the label still reflects Nuke's body font, since
        # the QSS rule above resolves later. Measuring the font we will
        # actually render against, plus a tight 8 px breathing pad,
        # keeps the floor snug so the strip can squeeze narrow without
        # leaving empty space between the label and the trigger pill.
        _label_font = QtGui.QFont(self.lbl_loadout.font())
        _label_font.setPointSizeF(14.0)
        _label_font.setBold(True)
        _label_metrics = QtGui.QFontMetrics(_label_font)
        self.lbl_loadout.setMinimumWidth(
            _label_metrics.horizontalAdvance("Loadout:") + 8
        )

        # Custom trigger pill + popup - replaces QComboBox so the orange
        # dot+halo, bold name, chevron, and rounded-fill open menu can be
        # painted exactly per the canonical design.
        self.trigger = _LoadoutTrigger(self)
        self.trigger.setObjectName("nsl_active_loadout_trigger")
        # The trigger pill is the binding floor on how narrow the left
        # column can squeeze before the folder/side splitter hits its
        # minimum and collapses. Long Loadout names truncate at the
        # trigger and render in full inside the popup list, so the
        # trigger can run narrower than its natural text width without
        # losing data. A 140 px floor gives the divider a wide usable
        # range before snap-collapse.
        self.trigger.setMinimumWidth(140)
        self.trigger.setMaximumWidth(340)

        self.popup = _LoadoutPopup(self)
        self.popup.setObjectName("nsl_active_loadout_popup")

        # Revert button. Sits between the trigger pill and the rename
        # pencil so the destructive "discard in-memory edits" affordance
        # is adjacent to the loadout name it operates on. Disabled when
        # there is nothing to revert (clean state, or Global, which has
        # no on-disk baseline to roll back to). The accent is ``#7a7a7a``,
        # the project's canonical muted-but-visible grey (also used for
        # empty-state text and muted labels): bright enough to register
        # as an actionable affordance against the dark borders the
        # sibling icon buttons carry, but well short of pure white.
        self.btn_revert = self._mk_icon_button(
            "revert",
            "Revert unsaved edits. Reload the active Loadout from disk.",
            accent_color="#7a7a7a",
        )
        self.btn_rename = self._mk_icon_button(
            "rename",
            "Rename selected Loadout (disabled for Global).",
        )
        self.btn_duplicate = self._mk_icon_button(
            "duplicate",
            "Duplicate selected Loadout.",
        )
        self.btn_delete = self._mk_icon_button(
            "delete",
            "Delete selected Loadout (disabled for Global).",
        )

        active_row.addWidget(self.lbl_loadout)
        active_row.addWidget(self.trigger, 1)
        active_row.addSpacing(2)
        active_row.addWidget(self.btn_revert)
        active_row.addWidget(self.btn_rename)
        active_row.addWidget(self.btn_duplicate)
        active_row.addWidget(self.btn_delete)
        active_row.addStretch(0)

        # --- File operations row --------------------------------------
        # JSX `.action-row` is `display: flex; gap: 8px` - buttons are
        # auto-width and flush left, NOT stretched evenly across the row.
        file_row = QtWidgets.QHBoxLayout()
        file_row.setSpacing(6)

        self.btn_save = HybridTextButton("&Save", self)
        self.btn_save_as = HybridTextButton("Save &As…", self)
        self.btn_import = HybridTextButton("&Import", self)
        self.btn_export = HybridTextButton("E&xport", self)
        for btn in (self.btn_save, self.btn_save_as, self.btn_import, self.btn_export):
            file_row.addWidget(btn)
        file_row.addStretch(1)

        # --- Panic button (parked: rendered by TopToolbar) ----------------
        # The visible panic control now lives in the top-of-panel toolbar,
        # right-aligned. We keep the widget + ``panic_toggled`` signal here
        # so the existing wiring layer (which reaches for
        # ``loadout_strip.btn_panic`` / ``loadout_strip.panic_toggled``)
        # continues to work without churn. Panel composition cross-wires the
        # top-toolbar button to this one so toggling either reflects state.
        self.btn_panic = QtWidgets.QPushButton(
            "Panic Mode: Disable all User-Added Plugins", self
        )
        self.btn_panic.setObjectName("nsl_panic_button")
        self.btn_panic.setCheckable(True)
        self.btn_panic.setVisible(False)
        self._apply_panic_style()

        outer.addLayout(active_row)
        outer.addLayout(file_row)

    def _mk_icon_button(
        self,
        kind: str,
        tooltip: str,
        *,
        accent_color: Optional[str] = None,
    ) -> QtWidgets.QPushButton:
        """Build a small per-Loadout glyph button.

        ``kind`` is one of ``"rename"``, ``"duplicate"``, ``"delete"``,
        ``"revert"``.

        ``accent_color``: when set, the button's enabled-state border
        uses this colour instead of the default dark border. Used for
        the Revert button to call attention to the available action
        whenever it's actionable - the brighter ring reads as "you can
        click this." The ``:disabled`` selector still falls back to the
        default dark border so a disabled accented button quietly merges
        into the row chrome.

        The canonical design (NSL_Design_System_New) renders these
        slightly inset from the panel background - the chrome should be
        *quieter* than the Save row, only a hair lighter than the panel
        bg. We use a flat QPushButton with a stylesheet that keeps it
        engraved-look and barely raised. HybridStyle inside Nuke can
        override; this matches the Fusion headless snapshot to the
        canonical target.
        """
        btn = _GlyphIconButton(kind, self)
        btn.setObjectName(f"nsl_loadout_{kind}_button")
        btn.setToolTip(tooltip)
        # 26x26 (square) so these icon buttons read at the same height
        # as HybridTextButton (26 px) and the trigger pill. The 14x14
        # icon leaves a comfortable 6 px clearance on all sides inside
        # the 26x26 hit area.
        btn.setFixedSize(QtCore.QSize(26, 26))
        btn.setIconSize(QtCore.QSize(14, 14))
        # Icon set inside _GlyphIconButton.__init__ already; hover swap
        # handled in its enterEvent / leaveEvent.
        btn.setFocusPolicy(QtCore.Qt.NoFocus)
        btn.setFlat(True)
        # Flat chrome: barely-there fill, 1px dark border, small radius.
        # Matches the user's screenshot - buttons read as quiet anchors,
        # not as primary controls competing with the Save row.
        # Accent override (``accent_color`` kwarg) swaps in a brighter
        # border for the enabled + hover states only; pressed and
        # disabled keep the default dark border.
        enabled_border = accent_color if accent_color else "#1f1f1f"
        hover_border = accent_color if accent_color else "#2a2a2a"
        btn.setStyleSheet(
            f"QPushButton#{btn.objectName()} {{"
            "  background-color: rgba(255,255,255,0.02);"
            f"  border: 1px solid {enabled_border};"
            "  border-radius: 4px;"
            "  padding: 0px;"
            "}"
            f"QPushButton#{btn.objectName()}:hover {{"
            "  background-color: rgba(255,255,255,0.06);"
            f"  border: 1px solid {hover_border};"
            "}"
            f"QPushButton#{btn.objectName()}:pressed {{"
            "  background-color: rgba(0,0,0,0.20);"
            "}"
            f"QPushButton#{btn.objectName()}:disabled {{"
            "  background-color: rgba(255,255,255,0.01);"
            "  border: 1px solid #1a1a1a;"
            "}"
        )
        return btn

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self.trigger.clicked.connect(self._open_popup)
        self.popup.item_selected.connect(self._on_item_selected)
        self.btn_revert.clicked.connect(self._on_revert_clicked)
        self.btn_rename.clicked.connect(self._on_rename_clicked)
        self.btn_duplicate.clicked.connect(self._on_duplicate_clicked)
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        self.btn_save.clicked.connect(self.save_requested)
        self.btn_save_as.clicked.connect(self.save_as_requested)
        self.btn_import.clicked.connect(self.import_requested)
        self.btn_export.clicked.connect(self.export_requested)
        self.btn_panic.toggled.connect(self._on_panic_toggled)

    def _open_popup(self) -> None:
        """Open the dropdown menu under the trigger pill."""
        self.popup.set_items(
            self._loadouts, self._active_name, display_for=self._display_name
        )
        self.popup.show_under(self.trigger)

    def _on_item_selected(self, name: str) -> None:
        if name and name in [lo.name for lo in self._loadouts]:
            self.loadout_selected.emit(name)

    # ------------------------------------------------------------------
    # Public API (inbound - signal-in)
    # ------------------------------------------------------------------

    def set_loadouts(
        self,
        loadouts: Sequence[Loadout],
        active: Optional[str] = None,
    ) -> None:
        """Replace the dropdown's contents.

        Reorders the list so that exactly one ``Global`` row (if any)
        ends up at the bottom. Preserves the active selection if the
        active name still exists in the new list; otherwise selects the
        first available Loadout (or clears the selection if the list is
        empty).
        """
        ordered = self._with_global_at_bottom(loadouts)
        self._loadouts = list(ordered)

        if active is None:
            active = self._active_name
        # If the previously-active name vanished, snap to the first entry.
        names = [lo.name for lo in self._loadouts]
        if active not in names:
            active = names[0] if names else None
        self._active_name = active

        self._refresh_trigger()
        self._refresh_button_states()

    def set_active_loadout(self, name: str) -> None:
        """Change which row is treated as the active Loadout.

        Silently no-ops if ``name`` is not in the current list.
        """
        names = [lo.name for lo in self._loadouts]
        if name not in names:
            return
        self._active_name = name
        self._refresh_trigger()
        self._refresh_button_states()

    def set_dirty(self, dirty: bool) -> None:
        """Slot for ``dirty_changed(bool)`` from the domain layer.

        Drives the ``(*)`` indicator on the active Loadout name. Does not
        own the flag - only renders it.
        """
        if dirty == self._dirty:
            return
        self._dirty = bool(dirty)
        self._refresh_trigger()
        self._refresh_button_states()

    def _refresh_trigger(self) -> None:
        """Sync the painted trigger pill's text to current state."""
        if self._active_name is None:
            self.trigger.setText("")
            self.trigger.set_show_dot(False)
        else:
            active = next(
                (lo for lo in self._loadouts if lo.name == self._active_name),
                None,
            )
            text = (
                self._display_name(active)
                if active is not None
                else self._active_name
            )
            self.trigger.setText(text)
            self.trigger.set_show_dot(True)

    def set_plugins_detected(self, detected: bool) -> None:
        """Drive Save / Save As / Export greying.

        When no Plugins are detected anywhere, those three buttons are
        disabled with a tooltip. Import stays enabled regardless.
        """
        self._plugins_detected = bool(detected)
        self._refresh_button_states()

    def set_panic_engaged(self, engaged: bool) -> None:
        """Programmatically reflect panic state without re-emitting."""
        if engaged == self._panic_engaged:
            return
        self._panic_engaged = bool(engaged)
        # Toggle the button without re-emitting our own ``panic_toggled``.
        blocked = self.btn_panic.blockSignals(True)
        try:
            self.btn_panic.setChecked(self._panic_engaged)
        finally:
            self.btn_panic.blockSignals(blocked)
        self._apply_panic_style()

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    def loadout_names(self) -> List[str]:
        """Return the dropdown's current entries in display order."""
        return [lo.name for lo in self._loadouts]

    def active_loadout(self) -> Optional[str]:
        return self._active_name

    def is_dirty(self) -> bool:
        return self._dirty

    def is_panic_engaged(self) -> bool:
        return self._panic_engaged

    def plugins_detected(self) -> bool:
        return self._plugins_detected

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _with_global_at_bottom(loadouts: Sequence[Loadout]) -> List[Loadout]:
        """Stable-sort the list so the (single) Global row sits last.

        User Loadouts retain their incoming order. If more than one
        Loadout is flagged ``is_global`` (an unsupported state)
        the implementation keeps the relative order among them but still
        sinks all of them to the bottom.
        """
        users = [lo for lo in loadouts if not lo.is_global]
        globals_ = [lo for lo in loadouts if lo.is_global]
        return users + globals_

    def _display_name(self, lo: Loadout) -> str:
        """Compose the user-facing label for one dropdown row.

        Names are bare stems (the JSON-era ``.loadout`` suffix is
        retired everywhere); the "Loadout:" label to the left already
        establishes the context.
        """
        base = lo.name
        is_active = lo.name == self._active_name
        # Global is read-only and cannot be saved - suppress the dirty
        # marker entirely. The auto-create-Custom-from-Global path means
        # any "edits" the user makes while Global is active are routed
        # into the Custom scratch loadout, not held against Global itself.
        # Showing `Global (*)` was a lie about whose state was dirty.
        if self._is_global(lo.name):
            return base
        dirty = (self._dirty and is_active) or lo.is_dirty
        suffix = " (*)" if dirty else ""
        return f"{base}{suffix}"

    def _refresh_button_states(self) -> None:
        """Recompute enabled/disabled + tooltips for every button.

        Called whenever the selection, active Loadout, dirty flag, or
        plugins-detected flag changes.
        """
        # The trigger always reflects the *active* Loadout, so the
        # per-Loadout buttons act on it directly - there is no longer a
        # separate "currently-selected-but-not-active" combo state.
        active_is_global = self._is_global(self._active_name)
        active_is_custom = self._is_custom(self._active_name)
        has_active = self._active_name is not None

        # Per-Loadout buttons (act on active Loadout)
        self.btn_rename.setEnabled(has_active and not active_is_global)
        self.btn_delete.setEnabled(has_active and not active_is_global)
        # Duplicate is enabled for every Loadout including Global -
        # duplicating Global produces a fresh user Loadout.
        self.btn_duplicate.setEnabled(has_active)
        # Revert is only meaningful when there are unsaved edits to
        # discard. Disabled on Global (no on-disk baseline to roll
        # back to) and when the active Loadout is clean. The tooltip
        # below stays informative in both states.
        self.btn_revert.setEnabled(
            has_active and not active_is_global and self._dirty
        )

        # File ops (act on active Loadout)
        #
        # Save semantics by active Loadout:
        #   * Global - disabled (read-only; the user must Save As).
        #   * User Loadout - enabled when dirty (overwrite the file).
        #   * Custom - enabled whenever Save As would be (the wiring
        #     layer redirects the Save click to the Save-As flow;
        #     Custom is in-memory only, so any "save" gesture must
        #     prompt for a new name). Leaving Save greyed for Custom is
        #     confusing, since there is no obvious way to intuit that
        #     Save As is the path forward; remapping the Save click to
        #     the Save-As flow avoids that dead end. The validator would
        #     also reject ``Custom`` as a reserved stem via
        #     ``filename_rules.py:141-142``.
        can_save_as = self._plugins_detected and self._active_name is not None
        if active_is_custom:
            can_save = can_save_as
        else:
            can_save = (
                self._plugins_detected
                and self._dirty
                and not active_is_global
                and self._active_name is not None
            )
        can_export = self._plugins_detected and self._active_name is not None
        self.btn_save.setEnabled(can_save)
        self.btn_save_as.setEnabled(can_save_as)
        self.btn_export.setEnabled(can_export)
        # Import always enabled - even with zero Plugins detected, you
        # can pull a Loadout file in from disk.
        self.btn_import.setEnabled(True)

        # Tooltips - only the "nothing to save" branch has locked wording.
        if not self._plugins_detected:
            for btn in (self.btn_save, self.btn_save_as, self.btn_export):
                btn.setToolTip(NO_PLUGINS_TOOLTIP)
        else:
            if active_is_global:
                save_tip = "Global is read-only - Save As to create a user Loadout."
            elif active_is_custom:
                save_tip = (
                    "Save Custom as a new named Loadout. Custom never "
                    "persists on its own - Save prompts for a name and "
                    "writes a new Loadout."
                )
            else:
                save_tip = "Save active Loadout to disk."
            self.btn_save.setToolTip(save_tip)
            self.btn_save_as.setToolTip(
                "Save active Loadout to a new file."
            )
            self.btn_export.setToolTip(
                "Write the active Loadout to a chosen path."
            )
        self.btn_import.setToolTip("Import a Loadout file from disk.")

    @staticmethod
    def _is_global(name: Optional[str]) -> bool:
        return name is not None and name.lower() == GLOBAL_LOADOUT_NAME.lower()

    @staticmethod
    def _is_custom(name: Optional[str]) -> bool:
        """Return ``True`` when *name* is the Custom wildcard slot.

        Mirrors :meth:`_is_global`. Case-insensitive comparison against
        :data:`CUSTOM_LOADOUT_NAME` - filename case is preserved
        on disk but the wildcard semantic is name-driven, not
        filesystem-driven.
        """
        return name is not None and name.lower() == CUSTOM_LOADOUT_NAME.lower()

    def _apply_panic_style(self) -> None:
        """Repaint the panic button to match engaged / rest state."""
        colour = PANIC_RED_ENGAGED if self._panic_engaged else PANIC_RED_REST
        # Stylesheet with hover/pressed echoes - Nuke's global stylesheet
        # may partially override; production will verify in-Nuke.
        self.btn_panic.setStyleSheet(
            "QPushButton#nsl_panic_button {"
            f"  background-color: {colour};"
            "  color: white;"
            "  border: 1px solid #111;"
            "  border-radius: 2px;"
            "  font-weight: bold;"
            "  padding: 4px 12px;"
            "}"
            "QPushButton#nsl_panic_button:hover {"
            f"  background-color: {PANIC_RED_ENGAGED};"
            "}"
        )

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _on_revert_clicked(self) -> None:
        name = self._active_name
        if name is None or self._is_global(name):
            return
        self.revert_requested.emit(name)

    def _on_rename_clicked(self) -> None:
        name = self._active_name
        if name is None or self._is_global(name):
            return
        self.rename_requested.emit(name)

    def _on_duplicate_clicked(self) -> None:
        name = self._active_name
        if name is None:
            return
        self.duplicate_requested.emit(name)

    def _on_delete_clicked(self) -> None:
        name = self._active_name
        if name is None or self._is_global(name):
            return
        self.delete_requested.emit(name)

    def _on_panic_toggled(self, checked: bool) -> None:
        self._panic_engaged = bool(checked)
        self._apply_panic_style()
        self.panic_toggled.emit(self._panic_engaged)

