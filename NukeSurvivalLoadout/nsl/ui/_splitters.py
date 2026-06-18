"""Shared hairline-handle splitter classes for the NSL panel composition.

Originally lived inside ``scripts/snapshot_panel_trio.py`` while the trio
assembly was the iteration surface. Promoted out here so both the trio
harness and the production panel (:mod:`nsl.ui.panel`) compose against
the same handles + snap-back helper.

Resize and collapse behaviour: active dividers paint 2 px
``#5a5a5a`` (orange ``#ee9626`` on hover); locked dividers paint 1 px
``#3a3a3a`` (no hover, arrow cursor). Snap-back uses ``splitterMoved``
so the snap fires *during* drag, not on release.

Two patterns are exported here:

* :class:`HairlineSplitter` - :class:`QSplitter` subclass whose handles
  paint a 1 / 2 px line centred inside a 6 px hit area. Combine with
  ``setHandleWidth(6)``.
* :func:`maybe_snap_splitter` - wire to ``splitterMoved`` to give a
  splitter a snap-back zone around a target ratio. Set
  ``splitter._snap_ratio = (left, right)`` and
  ``splitter._snap_tolerance = 0.025`` to opt in.

The transparent-handle QSS must be applied **to each splitter
instance** (not to the panel root) so it doesn't pollute descendant
native ``QPushButton`` rendering. See the QSS-cascade lessons in
``.ai/LESSONS.md``.
"""

from __future__ import annotations

from nsl import compat


__all__ = [
    "HairlineHandle",
    "HairlineSplitter",
    "maybe_snap_splitter",
    "HANDLE_QSS",
]


# QSS to scrub the default splitter-handle fill so the custom
# paintEvent's hairline reads alone. Apply per-splitter, NOT to root.
HANDLE_QSS = "QSplitter::handle { background: transparent; border: none; }"


class HairlineHandle(compat.QtWidgets.QSplitterHandle):
    """Splitter handle with a wide hit-area but a 1 / 2 px painted hairline.

    Qt's ``setHandleWidth(N)`` controls BOTH the painted strip and the
    mouse hit area. To stay grabbable outside Nuke (where HybridStyle
    isn't inflating hit areas for us) without making the divider look
    fat, we keep the handle width at 6 px for grabbability and paint
    only a thin line down the middle.

    Enabled handles paint 2 px in ``#5a5a5a`` (orange ``#ee9626`` on
    hover); disabled handles paint 1 px in ``#3a3a3a`` (no hover state)
    so the user can tell at a glance which dividers will respond to a
    drag. Pair ``setEnabled(False)`` on a locked handle with
    ``setCursor(Qt.ArrowCursor)`` so the cursor doesn't tease an
    interaction that won't happen.
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
    Nuke. Inside Nuke's HybridStyle the hit area is inflated by the
    style; the painted line stays a hairline regardless.
    """

    def createHandle(self):  # noqa: N802 - Qt override
        return HairlineHandle(self.orientation(), self)


def maybe_snap_splitter(splitter) -> None:
    """Snap-back helper for active splitter dividers.

    Wire to ``splitter.splitterMoved`` so the snap fires *live during
    drag*, not just on release. The user feels a brief "stick" while
    passing through the target zone; dragging firmly outside the zone
    breaks free. Reads ``splitter._snap_ratio`` (tuple of two ints,
    interpreted as a proportional split) and
    ``splitter._snap_tolerance`` (fractional, e.g. 0.025 = 2.5 %).
    Splitters without ``_snap_ratio`` set are no-ops, so it's safe to
    wire eagerly.

    Uses ``blockSignals`` around the corrective ``setSizes`` call to
    prevent the resulting signal from re-entering this handler.
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
