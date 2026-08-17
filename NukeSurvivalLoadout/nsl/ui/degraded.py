"""Degraded-mode panel surface, shown when startup fails.

Degraded mode adds a red advisory strip, selects the Summary tab, and
disables the pill grid and every write surface. The panic button stays
on, because it only flips ``PANIC_MODE`` in the dispatcher.

* :class:`DegradedBanner` - the advisory strip widget.
* :func:`wire_degraded` - the entry point. A no-op on a clean boot.
"""

from __future__ import annotations

from typing import Any

from nsl import compat


# ---------------------------------------------------------------------------
# Advisory text constants.
# ---------------------------------------------------------------------------

ADVISORY_TEXT = (
    "NSL had a problem during startup. "
    "Some features are unavailable. See the Summary tab."
)

DISABLED_TOOLTIP = "Disabled. NSL did not complete startup."

_ADVISORY_BG = "#c8261c"
_ADVISORY_FG = "#ffffff"


class DegradedBanner(compat.QtWidgets.QWidget):
    """Red advisory strip rendered at the top of the panel.

    Unlike :class:`nsl.ui.banner.Banner` there is no dismiss button. The
    failure lasts for the session, so a dismiss action would lie about
    the state. The widget is visible as soon as it is built.
    """

    MESSAGE = ADVISORY_TEXT

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)

        policy = compat.QtWidgets.QSizePolicy(
            compat.QtWidgets.QSizePolicy.Expanding,
            compat.QtWidgets.QSizePolicy.Fixed,
        )
        self.setSizePolicy(policy)

        self.setObjectName("NslDegradedAdvisory")
        self.setAttribute(compat.QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "#NslDegradedAdvisory {"
            "  background-color: " + _ADVISORY_BG + ";"
            "  color: " + _ADVISORY_FG + ";"
            "}"
        )

        layout = compat.QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._label = compat.QtWidgets.QLabel(ADVISORY_TEXT, self)
        self._label.setObjectName("NslDegradedAdvisoryLabel")
        self._label.setAlignment(
            compat.QtCore.Qt.AlignVCenter | compat.QtCore.Qt.AlignLeft
        )
        self._label.setStyleSheet(
            "color: " + _ADVISORY_FG + "; background: transparent;"
        )
        layout.addWidget(self._label, stretch=1)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def message(self) -> str:
        """Return the displayed advisory text."""
        return self._label.text()

    def label(self) -> Any:
        """Return the inner QLabel."""
        return self._label


# ---------------------------------------------------------------------------
# Wiring helper - the public entry point that renders degraded mode.
# ---------------------------------------------------------------------------


def wire_degraded(panel: Any) -> None:
    """Render ``panel`` in degraded mode if startup failed.

    Two triggers: :func:`nsl.boot.self_recovery.boot_failed`, or a
    non-empty ``panel._bootstrap_error``. A malformed dispatcher must not
    read as empty, or the next write replaces a file that can still be
    repaired.

    Idempotent. A second call does not stack banners.
    """
    # Local import so this module loads while the boot package is still
    # being built.
    from nsl.boot import self_recovery

    bootstrap_error = getattr(panel, "_bootstrap_error", None)

    if not self_recovery.boot_failed() and not bootstrap_error:
        return

    _apply_degraded_mode(panel)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _apply_degraded_mode(panel: Any) -> None:
    """Render ``panel`` in degraded mode unconditionally.

    Drives the rendering without touching the boot sequence's module
    state. The public path is :func:`wire_degraded`.
    """
    if getattr(panel, "_degraded_banner", None) is not None:
        return

    banner = _inject_advisory(panel)
    panel._degraded_banner = banner

    _activate_summary_tab(panel)
    _fill_summary_tab(panel)
    _disable_grid(panel)
    _disable_write_surfaces(panel)


def _inject_advisory(panel: Any) -> "DegradedBanner":
    """Prepend a :class:`DegradedBanner` to the panel's outer layout.

    Index 0, above the top toolbar and the change-detected banner.
    """
    banner = DegradedBanner(panel)
    layout = panel.layout()
    if layout is None:
        # ``LoadoutPanel.__init__`` always installs an outer layout. A
        # panel without one still gets the banner, as a loose child.
        return banner
    if hasattr(layout, "insertWidget"):
        layout.insertWidget(0, banner)
    else:  # pragma: no cover - non-box layouts not used by NSL panels
        layout.addWidget(banner)
    return banner


def _activate_summary_tab(panel: Any) -> None:
    """Set the side panel's Summary tab as the active tab."""
    side_panel = getattr(panel, "side_panel", None)
    if side_panel is None:
        return
    tabs = getattr(side_panel, "tabs", None)
    if tabs is None:
        return
    # Local import so this module does not load ``side_panel`` just for
    # one constant.
    try:
        from nsl.ui.side_panel import TAB_SUMMARY
    except Exception:  # pragma: no cover - defensive
        TAB_SUMMARY = 0
    tabs.setCurrentIndex(TAB_SUMMARY)


def _fill_summary_tab(panel: Any) -> None:
    """Populate the Summary tab with the failed-phase context.

    Renders the failed phase, the exception, and any bootstrap error as
    HTML.
    """
    side_panel = getattr(panel, "side_panel", None)
    if side_panel is None or not hasattr(side_panel, "set_summary"):
        return

    from nsl.boot import self_recovery

    phase = self_recovery.failed_phase() or "unknown phase"
    exc = self_recovery.failure_exception()
    exc_text = "" if exc is None else f"{type(exc).__name__}: {exc}"
    bootstrap_error = getattr(panel, "_bootstrap_error", None)

    body_lines = [
        "<h3 style='color:#c8261c;'>NSL did not complete startup</h3>",
        f"<p><b>Failed phase:</b> {_html_escape(str(phase))}</p>",
    ]
    if exc_text:
        body_lines.append(
            "<p><b>Exception:</b></p>"
            "<pre style='font-family:Menlo,Monaco,Consolas,monospace;'>"
            f"{_html_escape(exc_text)}"
            "</pre>"
        )
    # A separate channel from the boot-sequence exception above. It names
    # the damaged file, which a hand-edit typo in the dispatcher needs.
    if bootstrap_error:
        body_lines.append(
            "<p><b>Bootstrap error:</b></p>"
            "<pre style='font-family:Menlo,Monaco,Consolas,monospace;'>"
            f"{_html_escape(str(bootstrap_error))}"
            "</pre>"
        )
    body_lines.append(
        "<p>Restart Nuke. If the problem repeats, check "
        "<code>~/.nuke/loadouts/</code> for malformed files, or revert NSL "
        "to a known-good version.</p>"
    )
    side_panel.set_summary("\n".join(body_lines), html=True)


def _disable_grid(panel: Any) -> None:
    """Grey out the pill grid and tag it with the disabled tooltip."""
    grid = getattr(panel, "grid", None)
    if grid is None:
        return
    grid.setEnabled(False)
    if hasattr(grid, "setToolTip"):
        grid.setToolTip(DISABLED_TOOLTIP)


def _disable_write_surfaces(panel: Any) -> None:
    """Disable every write surface on the panel except the panic button."""
    # ---- Loadout strip --------------------------------------------------
    strip = getattr(panel, "loadout_strip", None)
    if strip is not None:
        for attr in (
            "btn_save",
            "btn_save_as",
            "btn_import",
            "btn_export",
            "btn_rename",
            "btn_duplicate",
            "btn_delete",
        ):
            _disable_button(getattr(strip, attr, None))
        # Panic stays enabled. It is the one write surface that still
        # works in degraded mode.
        panic = getattr(strip, "btn_panic", None)
        if panic is not None and hasattr(panic, "setEnabled"):
            panic.setEnabled(True)

    # ---- Top toolbar ----------------------------------------------------
    toolbar = getattr(panel, "top_toolbar", None)
    if toolbar is not None:
        for attr in ("_btn_undo", "_btn_redo"):
            _disable_button(getattr(toolbar, attr, None))
        # The reset-panel button only changes splitter sizes, so it stays
        # enabled and the user can still fix the layout.

    # ---- Grid toolbar (bulk ops) ----------------------------------------
    grid_toolbar = getattr(panel, "grid_toolbar", None)
    if grid_toolbar is not None:
        for attr in (
            "_btn_enable",
            "_btn_disable",
            "_btn_invert",
            "_btn_clear_selection",
            "_btn_set_gui_only",
            "_btn_clear_gui_only",
            "_btn_toggle_gui_only",
        ):
            _disable_button(getattr(grid_toolbar, attr, None))


def _disable_button(button: Any) -> None:
    """Set ``button`` to disabled with the disabled tooltip."""
    if button is None:
        return
    if hasattr(button, "setEnabled"):
        button.setEnabled(False)
    if hasattr(button, "setToolTip"):
        button.setToolTip(DISABLED_TOOLTIP)


def _html_escape(text: str) -> str:
    """Minimal HTML escape, a copy of the ``side_panel`` helper.

    Copied so this module does not import ``side_panel`` at load time.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
