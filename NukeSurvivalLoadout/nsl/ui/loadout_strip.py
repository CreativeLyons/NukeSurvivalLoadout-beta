"""Loadout selector strip.

Two rows: the active Loadout dropdown with its Revert / Rename /
Duplicate / Delete buttons, then ``Save`` / ``Save As`` / ``Import`` /
``Export``.

* Qt imports go through :mod:`nsl.compat`, never ``PySide2`` / ``PySide6``.
* The ``Global`` row always sorts to the bottom of the dropdown.
* Rename and Delete are disabled on Global.
* ``(*)`` marks the active Loadout as unsaved. The domain layer owns the
  flag and the strip only renders it.
* ``Save`` / ``Save As`` / ``Export`` grey out when no Plugins are found.
* Signal-out only. The strip never writes files.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from nsl import compat
from nsl.ui import _theme
from nsl.ui._buttons import HybridTextButton, install_clickable_cursor

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# ---------------------------------------------------------------------------
# Constants - palette, label, reserved names
# ---------------------------------------------------------------------------

GLOBAL_LOADOUT_NAME = "Global"

# Display name of the in-memory scratch Loadout. The domain stem is
# ``nsl.constants.DEFAULT_CUSTOM_LOADOUT_STEM``, a reserved name.
CUSTOM_LOADOUT_NAME = "Custom"

ACTIVE_ROW_BG = QtGui.QColor(
    *_theme.ACTIVE_ROW_BLUE_RGB, _theme.ACTIVE_ROW_BLUE_ALPHA
)
# JSX active-row hover is rgba(86,160,244,0.34).
ACTIVE_ROW_HOVER_BG = QtGui.QColor(*_theme.ACTIVE_ROW_BLUE_RGB, 87)  # ≈ 0.34 * 255
ACTIVE_ROW_FG = QtGui.QColor(255, 255, 255)

# Inactive hover is a soft white tint, not Nuke-orange. Only the active
# row hovers blue.
INACTIVE_HOVER_BG = QtGui.QColor(255, 255, 255, 13)  # ≈ 0.05 * 255
INACTIVE_HOVER_FG = QtGui.QColor(255, 255, 255)

# The dot halo is a flat ring, not a blurred shadow.
ACTIVE_DOT_COLOR = QtGui.QColor(*_theme.NUKE_ORANGE_RGB)
ACTIVE_DOT_HALO = QtGui.QColor(*_theme.NUKE_ORANGE_RGB, 31)  # ≈ 0.12 * 255
ACTIVE_DOT_SIZE = 6
ACTIVE_DOT_HALO_PAD = 3  # px ring around the dot

NORMAL_BG = QtGui.QColor(48, 48, 48)
NORMAL_FG = QtGui.QColor(218, 218, 218)  # #dadada

# Engaged is the brighter red, so the user can see that user-added
# Plugins are hidden.
PANIC_RED_REST = "#9c2a2a"
PANIC_RED_ENGAGED = "#ff3a3a"

NO_PLUGINS_TOOLTIP = "Nothing to save. Add a Plugins Folder first."


# ---------------------------------------------------------------------------
# Loadout data carrier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Loadout:
    """Plain carrier for one dropdown row.

    ``is_dirty`` is a per-row hint. In practice only the active Loadout
    is dirty, but every row honours it. The strip's own ``set_dirty``
    drives the ``(*)`` suffix on the active row separately.
    """

    name: str
    is_global: bool = False
    is_dirty: bool = False


# ---------------------------------------------------------------------------
# Custom item delegate - active-row blue + hover yellow-orange
# ---------------------------------------------------------------------------


class _LoadoutItemDelegate(QtWidgets.QStyledItemDelegate):
    """Item delegate for the Active-Loadout dropdown's open list view.

    * The active row paints translucent blue and deepens on hover.
    * Inactive rows hover with a soft white tint, not Nuke-orange.
    * The active row carries a small orange dot with a flat halo.
    """

    ACTIVE_ROW_ROLE = QtCore.Qt.UserRole + 1

    # The fill is inset from the row rect, so its rounded corners show.
    _ROW_PAD_X = 8
    _ROW_PAD_Y = 0       # 0 keeps the rows flush, with no gap
    _DOT_TEXT_GAP = 12
    _ROW_RADIUS = 5

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
            font = QtGui.QFont(opt.font)
            font.setBold(True)
            font.setPointSizeF(font.pointSizeF() + 1.0)
            painter.setFont(font)

            # Order matters. Active wins over inactive hover, and only
            # its own hover shade lifts it.
            if is_active and hovered:
                bg, fg = ACTIVE_ROW_HOVER_BG, ACTIVE_ROW_FG
            elif is_active:
                bg, fg = ACTIVE_ROW_BG, ACTIVE_ROW_FG
            elif hovered:
                bg, fg = INACTIVE_HOVER_BG, INACTIVE_HOVER_FG
            else:
                bg, fg = None, NORMAL_FG

            # Inset the fill so the rounded corners are visible.
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

            # The dot slot is reserved on every row. The text then lines
            # up whether or not the dot is painted.
            dot_slot_left = opt.rect.left() + self._ROW_PAD_X + 6
            dot_cx = dot_slot_left + ACTIVE_DOT_SIZE // 2
            dot_cy = opt.rect.center().y()

            if is_active:
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
        # 28 px matches the 26 px trigger pill it pops below. The extra
        # 2 px keeps the bold row text legible.
        return QtCore.QSize(hint.width(), max(hint.height(), 28))


# ---------------------------------------------------------------------------
# Glyph icons for the per-Loadout action buttons (rename / duplicate / delete)
# ---------------------------------------------------------------------------


_GLYPH_COLOR = QtGui.QColor("#dcdcdc")
_GLYPH_HOVER_COLOR = QtGui.QColor("#ffffff")
_GLYPH_DISABLED_COLOR = QtGui.QColor("#4a4a4a")
_GLYPH_SIZE = 14
# Paint at 2x and set devicePixelRatio to 2. Qt then downsamples with
# anti-aliasing. A 14x14 source is fuzzy on retina, because integer pen
# widths land on sub-pixel boundaries.
_GLYPH_SUPERSAMPLE = 2


def _paint_glyph_pixmap(kind: str, color: QtGui.QColor) -> QtGui.QPixmap:
    """Paint one pixmap of a per-Loadout glyph in *color*.

    ``kind`` is ``"rename"``, ``"duplicate"``, ``"delete"`` or
    ``"revert"``. One call gives one colour variant, so a caller can
    build rest, hover and disabled icons from the same glyph.
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

    # All glyph geometry below sits on a 16-unit grid. ``s`` scales it
    # to the supersampled pixel size.
    s = size / 16.0

    if kind == "rename":
        # A pencil. The eraser cap is top right, the body runs
        # diagonally, and the tip points lower left.
        pen.setWidthF(1.6 * s)
        painter.setPen(pen)
        body = QtGui.QPainterPath()
        body.moveTo(11.5 * s, 2 * s)
        body.lineTo(14 * s, 4.5 * s)
        body.lineTo(5.5 * s, 13 * s)
        body.lineTo(3 * s, 13 * s)
        body.lineTo(3 * s, 10.5 * s)
        body.closeSubpath()
        painter.drawPath(body)
        # Seam between eraser cap and shaft.
        painter.drawLine(
            QtCore.QPointF(9 * s, 4.5 * s),
            QtCore.QPointF(11.5 * s, 7 * s),
        )
    elif kind == "duplicate":
        # Two pages offset diagonally. The front rect is cleared first,
        # so it punches a hole in the back rect, then stroked.
        pen.setWidthF(1.4 * s)
        painter.setPen(pen)
        back = QtCore.QRectF(2 * s, 2 * s, 9 * s, 9 * s)
        front = QtCore.QRectF(5 * s, 5 * s, 9 * s, 9 * s)
        painter.drawRoundedRect(back, 1.2 * s, 1.2 * s)
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
        # A counter-clockwise arrow. The head sits at the top and points
        # left, toward the Loadout dropdown. It reads as "go back".
        pen.setWidthF(2.0 * s)
        painter.setPen(pen)
        cx = 8 * s
        cy = 8.5 * s
        r = 4.0 * s
        rect = QtCore.QRectF(cx - r, cy - r, 2 * r, 2 * r)
        # Starts at 210 degrees and sweeps 240 counter-clockwise, so it
        # ends at 90 degrees, the top of the icon.
        painter.drawArc(rect, int(210 * 16), int(240 * 16))
        # The head goes at the end of the sweep, along the tangent.
        end_rad = math.radians(90)
        ex = cx + r * math.cos(end_rad)
        ey = cy - r * math.sin(end_rad)
        # At 90 degrees the tangent points straight left, so the tip
        # lands left of the arc. Qt flips the y axis, which makes the
        # counter-clockwise tangent (-sin t, -cos t).
        tx = -math.sin(end_rad)
        ty = -math.cos(end_rad)
        arrow_len = 4.6 * s
        arrow_half = 3.0 * s
        tip_x = ex + arrow_len * tx
        tip_y = ey + arrow_len * ty
        # Perpendicular to the tangent, rotated 90 degrees: (-ty, tx)
        px = -ty
        py = tx
        arrow = QtGui.QPainterPath()
        arrow.moveTo(tip_x, tip_y)
        arrow.lineTo(ex + arrow_half * px, ey + arrow_half * py)
        arrow.lineTo(ex - arrow_half * px, ey - arrow_half * py)
        arrow.closeSubpath()
        painter.fillPath(arrow, color)

    painter.end()
    pixmap.setDevicePixelRatio(_GLYPH_SUPERSAMPLE)
    return pixmap


def _make_glyph_icon(kind: str) -> QtGui.QIcon:
    """Return a rest-state ``QIcon`` for one glyph kind.

    For a button that brightens on hover use :class:`_GlyphIconButton`.
    """
    return QtGui.QIcon(_paint_glyph_pixmap(kind, _GLYPH_COLOR))


class _GlyphIconButton(QtWidgets.QPushButton):
    """``QPushButton`` that brightens its glyph to white on hover.

    It swaps the icon in :meth:`enterEvent` and :meth:`leaveEvent`,
    because a QSS ``:hover`` rule on the button masks QIcon's
    ``Active`` mode.
    """

    def __init__(
        self,
        kind: str,
        parent: Optional["QtWidgets.QWidget"] = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        # A Disabled pixmap on the rest icon. Qt's default faded Normal
        # still reads too strong against the near-transparent button.
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
        install_clickable_cursor(self)

    def enterEvent(self, event):  # noqa: N802 - Qt override
        if self.isEnabled():
            self.setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self.setIcon(self._rest_icon)
        super().leaveEvent(event)


def _make_active_dot_icon(size: int = 18) -> QtGui.QIcon:
    """Return a small orange-dot ``QIcon``: a dot over a flat halo ring.

    Nothing calls it now. :class:`_LoadoutTrigger` paints its own dot.
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
# Custom trigger pill - replaces QComboBox's closed state.
# ---------------------------------------------------------------------------


class _LoadoutTrigger(QtWidgets.QAbstractButton):
    """Click-to-open trigger pill for the active Loadout.

    Paints the orange dot, the bold Loadout name with its ``(*)``
    suffix, and a chevron. Emits ``clicked``, which the strip uses to
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
        # 26 px matches HybridTextButton, so the panel chrome lines up.
        self.setMinimumHeight(26)
        self.setMinimumWidth(280)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
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

        # Hover treatment mirrors the rename / duplicate / delete buttons.
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

        # 1px highlight on the top inside edge. No gradient, no blur.
        hi_rect = QtCore.QRectF(
            rect.left() + 1, rect.top() + 1, rect.width() - 2, 1
        )
        p.fillRect(hi_rect, QtGui.QColor(255, 255, 255, highlight_alpha))

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
# Popup menu - a QListView in a frameless widget. The container and the
# active row are painted directly.
# ---------------------------------------------------------------------------


class _LoadoutPopup(QtWidgets.QWidget):
    """Frameless popup with a QListView for the open-menu state.

    :meth:`paintEvent` paints the container. Rows are painted by
    :class:`_LoadoutItemDelegate`.
    """

    #: Payload is the Loadout name.
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
        # Pre-realise the native window. Without it the first ``move()``
        # on macOS reads the coordinates as child-relative, and the
        # popup lands inside the trigger.
        self.setAttribute(QtCore.Qt.WA_NativeWindow, True)
        self.winId()

        # ``self.height()`` is stale before the popup is shown, so
        # :meth:`set_items` stores the computed height here.
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
        # A minimal sheet only, to kill Fusion's default item background.
        # The container itself is painted in :meth:`paintEvent`.
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
        n = self._model.rowCount()
        row_h = 28  # the delegate sizeHint floor
        list_h = max(row_h, n * row_h)
        # +2 for the QListView's frame compensator inside the layout.
        self._intended_height = list_h + 2 * self._PADDING + 2
        self.setFixedHeight(self._intended_height)

    def show_under(self, anchor: QtWidgets.QWidget) -> None:
        """Position the popup just below ``anchor``, the trigger pill.

        It maps through the top-level window, because
        ``QWidget.mapToGlobal`` on a nested child can return
        parent-relative coordinates on the first call under macOS.
        """
        anchor.ensurePolished()
        top = anchor.window()
        if top is None:
            top = anchor

        anchor_bottom_left_in_top = anchor.mapTo(
            top, QtCore.QPoint(0, anchor.height())
        )
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
        # 1px highlight on the top inside edge.
        hi = QtCore.QRectF(rect.left() + 1, rect.top() + 1, rect.width() - 2, 1)
        p.fillRect(hi, QtGui.QColor(255, 255, 255, 10))


# ---------------------------------------------------------------------------


_ACTIVE_DOT_ICON: Optional[QtGui.QIcon] = None


def _active_dot_icon() -> QtGui.QIcon:
    """Return the shared dot icon, building it on first use."""
    global _ACTIVE_DOT_ICON
    if _ACTIVE_DOT_ICON is None:
        _ACTIVE_DOT_ICON = _make_active_dot_icon()
    return _ACTIVE_DOT_ICON


# ---------------------------------------------------------------------------
# The strip itself
# ---------------------------------------------------------------------------


class LoadoutStrip(QtWidgets.QWidget):
    """Active Loadout dropdown, per-Loadout buttons, file ops and panic.

    Signal-out only. The strip never writes state itself. The rename,
    duplicate, delete and revert signals carry the active Loadout name.
    """

    # --- signals -----------------------------------------------------------

    loadout_selected = QtCore.Signal(str)
    rename_requested = QtCore.Signal(str)
    duplicate_requested = QtCore.Signal(str)
    delete_requested = QtCore.Signal(str)
    # Discard in-memory edits and reload the active Loadout from disk.
    revert_requested = QtCore.Signal(str)
    save_requested = QtCore.Signal()
    save_as_requested = QtCore.Signal()
    import_requested = QtCore.Signal()
    export_requested = QtCore.Signal()
    panic_toggled = QtCore.Signal(bool)

    # ----------------------------------------------------------------------

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        # Internal state. Read-only from outside.
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

        self.lbl_loadout = QtWidgets.QLabel("Loadout:", self)
        self.lbl_loadout.setObjectName("nsl_loadout_label")
        # Nuke applies an app-wide stylesheet after construction and it
        # overrides setFont() on a QLabel. A QSS rule keyed on the
        # objectName wins the cascade instead.
        self.lbl_loadout.setStyleSheet(
            "QLabel#nsl_loadout_label {"
            "  color: #ffffff;"
            "  font-size: 14pt;"
            "  font-weight: bold;"
            "}"
        )
        # A min-width stops the label clipping when the strip is
        # squeezed. Measure the font the QSS will apply, 14 pt bold, not
        # the label's current one. That rule resolves after this line.
        _label_font = QtGui.QFont(self.lbl_loadout.font())
        _label_font.setPointSizeF(14.0)
        _label_font.setBold(True)
        _label_metrics = QtGui.QFontMetrics(_label_font)
        self.lbl_loadout.setMinimumWidth(
            _label_metrics.horizontalAdvance("Loadout:") + 8
        )

        self.trigger = _LoadoutTrigger(self)
        self.trigger.setObjectName("nsl_active_loadout_trigger")
        # This floor decides how narrow the left column can squeeze
        # before the splitter collapses. A long name truncates here and
        # still shows in full in the popup, so 140 px loses nothing.
        self.trigger.setMinimumWidth(140)
        self.trigger.setMaximumWidth(340)

        self.popup = _LoadoutPopup(self)
        self.popup.setObjectName("nsl_active_loadout_popup")

        # ``#7a7a7a`` is the project's muted grey. It reads as
        # actionable against the dark borders on the sibling buttons.
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
        # Buttons are auto-width and flush left, not stretched.
        file_row = QtWidgets.QHBoxLayout()
        file_row.setSpacing(6)

        self.btn_save = HybridTextButton("&Save", self)
        self.btn_save_as = HybridTextButton("Save &As…", self)
        self.btn_import = HybridTextButton("&Import", self)
        self.btn_export = HybridTextButton("E&xport", self)
        for btn in (self.btn_save, self.btn_save_as, self.btn_import, self.btn_export):
            file_row.addWidget(btn)
        file_row.addStretch(1)

        # --- Panic button (hidden - TopToolbar renders it) ----------------
        # Kept for the wiring layer, which reads ``loadout_strip.btn_panic``.
        # Panel composition cross-wires the two buttons.
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

        ``accent_color`` replaces the border colour in the enabled and
        hover states only. Pressed and disabled keep the dark border, so
        a disabled accented button merges back into the row.
        """
        btn = _GlyphIconButton(kind, self)
        btn.setObjectName(f"nsl_loadout_{kind}_button")
        btn.setToolTip(tooltip)
        # 26x26 matches HybridTextButton and the trigger pill height.
        btn.setFixedSize(QtCore.QSize(26, 26))
        btn.setIconSize(QtCore.QSize(14, 14))
        btn.setFocusPolicy(QtCore.Qt.NoFocus)
        btn.setFlat(True)
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

        Global sorts to the bottom. The active selection survives if the
        name is still in the list, otherwise the first row wins.
        """
        ordered = self._with_global_at_bottom(loadouts)
        self._loadouts = list(ordered)

        if active is None:
            active = self._active_name
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
        """Grey out Save / Save As / Export when no Plugins are detected.

        Import stays enabled.
        """
        self._plugins_detected = bool(detected)
        self._refresh_button_states()

    def set_panic_engaged(self, engaged: bool) -> None:
        """Programmatically reflect panic state without re-emitting."""
        if engaged == self._panic_engaged:
            return
        self._panic_engaged = bool(engaged)
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
        """Move the Global row to the bottom, keeping the other order.

        More than one Global row is unsupported, but they all sink.
        """
        users = [lo for lo in loadouts if not lo.is_global]
        globals_ = [lo for lo in loadouts if lo.is_global]
        return users + globals_

    def _display_name(self, lo: Loadout) -> str:
        """Compose the user-facing label for one dropdown row."""
        base = lo.name
        is_active = lo.name == self._active_name
        # Global never shows ``(*)``. Edits made while Global is active
        # go into the Custom scratch Loadout, not into Global.
        if self._is_global(lo.name):
            return base
        dirty = (self._dirty and is_active) or lo.is_dirty
        suffix = " (*)" if dirty else ""
        return f"{base}{suffix}"

    def _refresh_button_states(self) -> None:
        """Recompute enabled state and tooltips for every button.

        Call it after any change to the selection, the dirty flag or the
        plugins-detected flag.
        """
        active_is_global = self._is_global(self._active_name)
        active_is_custom = self._is_custom(self._active_name)
        has_active = self._active_name is not None

        # Per-Loadout buttons (act on active Loadout)
        self.btn_rename.setEnabled(has_active and not active_is_global)
        self.btn_delete.setEnabled(has_active and not active_is_global)
        # Duplicate is enabled for every Loadout including Global -
        # duplicating Global produces a fresh user Loadout.
        self.btn_duplicate.setEnabled(has_active)
        # Global has no on-disk baseline, so Revert stays disabled there.
        self.btn_revert.setEnabled(
            has_active and not active_is_global and self._dirty
        )

        # Save button state by active Loadout:
        #   * Global       - disabled. Read-only.
        #   * User Loadout - enabled when dirty.
        #   * Custom       - follows Save As. In-memory only, so the wiring
        #                    redirects the click.
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
        # Import stays enabled with zero Plugins detected.
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
        """Return ``True`` when *name* is the Custom slot, in any case."""
        return name is not None and name.lower() == CUSTOM_LOADOUT_NAME.lower()

    def _apply_panic_style(self) -> None:
        """Repaint the panic button to match engaged / rest state."""
        colour = PANIC_RED_ENGAGED if self._panic_engaged else PANIC_RED_REST
        # Nuke's app-wide stylesheet can override parts of this sheet.
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

