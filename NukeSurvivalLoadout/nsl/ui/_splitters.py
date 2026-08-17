"""Shared hairline-handle splitter classes for the NSL panel.

Apply ``HANDLE_QSS`` to each splitter instance, never to the panel root.
QSS on the root breaks native ``QPushButton`` rendering below it. See
the QSS-cascade lessons in ``.ai/LESSONS.md``.
"""

from __future__ import annotations

from nsl import compat


__all__ = [
    "HairlineHandle",
    "HairlineSplitter",
    "maybe_snap_splitter",
    "HANDLE_QSS",
]


# Clears the default handle fill so only the painted hairline shows.
HANDLE_QSS = "QSplitter::handle { background: transparent; border: none; }"


class HairlineHandle(compat.QtWidgets.QSplitterHandle):
    """Splitter handle with a wide hit area and a 1 or 2 px hairline.

    ``setHandleWidth(N)`` sets both the painted strip and the mouse hit
    area, so the handle stays 6 px wide and paints a thin line inside it.
    Pair ``setEnabled(False)`` with ``setCursor(Qt.ArrowCursor)`` so the
    cursor does not promise a drag that cannot happen.
    """

    _LINE_COLOUR = compat.QtGui.QColor("#5a5a5a")
    _HOVER_COLOUR = compat.QtGui.QColor("#ee9626")
    _DISABLED_COLOUR = compat.QtGui.QColor("#3a3a3a")
    _ACTIVE_PX = 2
    _DISABLED_PX = 1

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setAttribute(compat.QtCore.Qt.WA_Hover, True)
        self._hovered = False

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = compat.QtGui.QPainter(self)
        try:
            if not self.isEnabled():
                colour = self._DISABLED_COLOUR
                line_px = self._DISABLED_PX
            else:
                colour = (
                    self._HOVER_COLOUR if self._hovered else self._LINE_COLOUR
                )
                line_px = self._ACTIVE_PX
            rect = self.rect()
            if self.orientation() == compat.QtCore.Qt.Horizontal:
                x = rect.center().x() - line_px // 2
                painter.fillRect(
                    compat.QtCore.QRect(x, rect.top(), line_px, rect.height()),
                    colour,
                )
            else:
                y = rect.center().y() - line_px // 2
                painter.fillRect(
                    compat.QtCore.QRect(rect.left(), y, rect.width(), line_px),
                    colour,
                )
        finally:
            painter.end()


class HairlineSplitter(compat.QtWidgets.QSplitter):
    """:class:`QSplitter` whose handles paint a centred hairline.

    Combine with ``setHandleWidth(6)`` for a grabbable hit area outside
    Nuke. Inside Nuke, HybridStyle inflates the hit area instead.
    """

    def createHandle(self):  # noqa: N802 - Qt override
        return HairlineHandle(self.orientation(), self)


def maybe_snap_splitter(splitter) -> None:
    """Snap-back helper for active splitter dividers.

    Wire to ``splitter.splitterMoved`` so the snap fires during the drag,
    not on release. Set ``splitter._snap_ratio``, two ints read as a
    proportional split, and ``splitter._snap_tolerance``, a fraction, to
    opt in. Without ``_snap_ratio`` it is a no-op.
    """
    snap_ratio = getattr(splitter, "_snap_ratio", None)
    if snap_ratio is None or len(splitter.sizes()) != 2:
        return
    tolerance = getattr(splitter, "_snap_tolerance", 0.05)
    sizes = splitter.sizes()
    total = sum(sizes)
    if total <= 0:
        return
    target_total = snap_ratio[0] + snap_ratio[1]
    target_left_frac = snap_ratio[0] / target_total
    current_left_frac = sizes[0] / total
    if abs(current_left_frac - target_left_frac) > tolerance:
        return
    target_left_px = int(round(total * target_left_frac))
    target_right_px = total - target_left_px
    new_sizes = [target_left_px, target_right_px]
    if new_sizes == sizes:
        return
    blocker = splitter.blockSignals(True)
    try:
        splitter.setSizes(new_sizes)
    finally:
        splitter.blockSignals(blocker)
