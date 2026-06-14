"""Degraded-mode panel surface - the UI shown when startup fails critically.

When the boot sequence records a critical failure, the panel renders in
degraded mode:

* A single vivid-red, single-line, non-dismissable advisory at the top of
  the panel reading
  *"NSL had a problem during startup. Some features are unavailable. See the Summary tab."*
* The Summary tab becomes the active tab (degraded-mode targeting is
  system-driven, not pill-driven).
* The pill grid is greyed/inert via ``grid.setEnabled(False)``.
* Every write surface (Loadout strip Save / Save As / Import / Export /
  Rename / Duplicate / Delete, the top toolbar Undo / Redo, all
  grid-toolbar bulk buttons) is disabled with a tooltip explaining why.
* The panic button stays available - it only flips ``PANIC_MODE`` in the
  loadouts dispatcher and does not depend on the rest of the boot
  completing.
* No ``import nuke``. Qt access only via :mod:`NukeSurvivalLoadout.compat`.

Public API:

* :class:`DegradedBanner` - the non-dismissable red advisory strip widget.
* :func:`wire_degraded` - reads ``boot_failed`` and either renders the panel
  in degraded mode or returns immediately. On a clean boot it is a no-op.
"""

from __future__ import annotations

from typing import Any

from NukeSurvivalLoadout import compat


# ---------------------------------------------------------------------------
# Advisory text constants.
# ---------------------------------------------------------------------------

#: Non-dismissable advisory shown at the top of the panel.
ADVISORY_TEXT = (
    "NSL had a problem during startup. "
    "Some features are unavailable. See the Summary tab."
)

#: Tooltip shown on every disabled write surface.
DISABLED_TOOLTIP = "Disabled. NSL did not complete startup."

# Colour vocabulary mirrors the change-detected banner so the "vivid red
# strip across the panel" reading stays consistent.
_ADVISORY_BG = "#c8261c"
_ADVISORY_FG = "#ffffff"


class DegradedBanner(compat.QtWidgets.QWidget):
    """Non-dismissable red advisory strip rendered at the top of the panel.

    Visually mirrors :class:`NukeSurvivalLoadout.ui.banner.Banner` (the change-detected
    strip) so degraded mode uses the panel's established "vivid red strip"
    vocabulary. Unlike that banner, this one has **no dismiss button** -
    the failure persists for the session, so a dismiss action would lie
    about the state.

    The widget is visible immediately on construction; the advisory appears
    at the top of the panel whenever degraded mode is in effect, so there is
    no hidden-by-default state to expose.
    """

    #: The advisory text displayed by the banner.
    MESSAGE = ADVISORY_TEXT

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)

        # The strip stretches across the panel horizontally and is a
        # fixed single-line height. Same size policy as the change-detected
        # banner so the layout pattern stays uniform.
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

        # Explicitly no dismiss QToolButton - the advisory is non-dismissable
        # for the entire session. We encode that by never building the close
        # button rather than building one and hiding it.

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
    """Render ``panel`` in degraded mode if the boot sequence recorded a failure.

    Behaviour:

    * Enters degraded mode when EITHER the boot sequence recorded a
      critical failure (:func:`NukeSurvivalLoadout.boot.self_recovery.boot_failed`)
      OR the panel carries a non-empty ``_bootstrap_error`` string set by
      :func:`NukeSurvivalLoadout.ui.registry_bootstrap.build_registry_for_panel`
      (e.g. a MALFORMED, unparseable dispatcher). A malformed dispatcher
      must not be treated as empty: degraded read-only mode disables the
      write surfaces so the next normal write cannot overwrite the
      damaged-but-recoverable file. (``write_dispatcher`` additionally
      keeps a ``.bak`` side-copy before any rewrite.)
    * If neither condition holds (clean boot, no bootstrap error), returns
      immediately - degraded mode is purely a failure-time UI and the
      normal panel is untouched.
    * When degraded:

      - Injects a :class:`DegradedBanner` as the very first widget in the
        panel's outer ``QVBoxLayout`` (above the top toolbar and the
        change-detected banner). The widget stores a back-reference on
        ``panel._degraded_banner``.
      - Switches the side panel's active tab to *Summary* (degraded mode is
        system-driven, unlike the normal-mode rule where Summary is never
        auto-targeted by pill clicks).
      - Fills the Summary tab with the failed-phase text plus the
        captured exception message so the user has somewhere to read the
        "what happened" detail.
      - Disables the pill grid (``grid.setEnabled(False)``) and tags it
        with the disabled tooltip.
      - Disables every write surface (Save / Save As / Import / Export
        / Rename / Duplicate / Delete on the Loadout strip; Undo / Redo
        on the top toolbar; every bulk-action button on the grid
        toolbar). Each disabled button carries
        :data:`DISABLED_TOOLTIP`.
      - Leaves the panic button enabled - it is the one always-available
        write surface in degraded mode.

    The helper is intentionally idempotent: calling it twice on the same
    panel does not stack banners or accumulate side effects.
    """
    # Import here so that this module loads even if the boot package is
    # mid-build (the helper is only meaningful once the boot sequence has run).
    from NukeSurvivalLoadout.boot import self_recovery

    # A bootstrap-level error (malformed dispatcher, unreadable Global,
    # etc.) is a degrade trigger even when the boot sequence itself did
    # not record a critical failure - the panel must enter read-only mode
    # rather than letting the next write overwrite a damaged file.
    bootstrap_error = getattr(panel, "_bootstrap_error", None)

    if not self_recovery.boot_failed() and not bootstrap_error:
        return

    _apply_degraded_mode(panel)


# ---------------------------------------------------------------------------
# Internals - broken out so the rendering can be driven directly without
# having to push state through self_recovery.
# ---------------------------------------------------------------------------


def _apply_degraded_mode(panel: Any) -> None:
    """Render ``panel`` in degraded mode unconditionally.

    Helper kept under a leading underscore because the public path is
    :func:`wire_degraded`. This drives the rendering directly without
    having to manipulate the boot sequence's module state.
    """
    if getattr(panel, "_degraded_banner", None) is not None:
        # Idempotent - already in degraded mode.
        return

    banner = _inject_advisory(panel)
    panel._degraded_banner = banner

    _activate_summary_tab(panel)
    _fill_summary_tab(panel)
    _disable_grid(panel)
    _disable_write_surfaces(panel)


def _inject_advisory(panel: Any) -> "DegradedBanner":
    """Prepend a :class:`DegradedBanner` to the panel's outer layout.

    The banner is inserted at index 0 so it sits above the top toolbar
    and the change-detected banner - a single advisory at the very top of
    the panel.
    """
    banner = DegradedBanner(panel)
    layout = panel.layout()
    if layout is None:
        # Defensive: a panel built without an outer layout (shouldn't
        # happen - :class:`LoadoutPanel.__init__` always installs one)
        # still gets the banner as a free-floating child.
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
    # NukeSurvivalLoadout.ui.side_panel.TAB_SUMMARY is the canonical constant; import
    # locally so this module does not pull in the side-panel module at
    # load time when only the constant is needed.
    try:
        from NukeSurvivalLoadout.ui.side_panel import TAB_SUMMARY
    except Exception:  # pragma: no cover - defensive
        TAB_SUMMARY = 0
    tabs.setCurrentIndex(TAB_SUMMARY)


def _fill_summary_tab(panel: Any) -> None:
    """Populate the Summary tab with the failed-phase context.

    The Summary tab in degraded mode shows the failed phase, the exception
    message, and (where available) the full traceback. The renderer is HTML
    so the phase name reads as a header and the message reads as monospaced
    detail.
    """
    side_panel = getattr(panel, "side_panel", None)
    if side_panel is None or not hasattr(side_panel, "set_summary"):
        return

    from NukeSurvivalLoadout.boot import self_recovery

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
    # Bootstrap-level detail (malformed dispatcher path + parse note,
    # unreadable Global, etc.). Distinct channel from the boot-sequence
    # exception above; surfaced so a hand-edit typo on the user-editable
    # dispatcher tells the user the exact file and that it was backed up.
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
    """Disable every write surface on the panel except the panic button.

    Each disabled button carries :data:`DISABLED_TOOLTIP` so a user
    hovering "why can't I save?" gets an explanation.
    """
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
        # Panic button explicitly stays enabled - the one always-available
        # write surface in degraded mode.
        panic = getattr(strip, "btn_panic", None)
        if panic is not None and hasattr(panic, "setEnabled"):
            panic.setEnabled(True)

    # ---- Top toolbar ----------------------------------------------------
    toolbar = getattr(panel, "top_toolbar", None)
    if toolbar is not None:
        for attr in ("_btn_undo", "_btn_redo"):
            _disable_button(getattr(toolbar, attr, None))
        # Reset-panel button is a layout-only action with no domain
        # side-effects (splitter sizes only). Keep it enabled so the user
        # can recover layout while in degraded mode.

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
    """Minimal HTML escape - same surface as side_panel's helper.

    Inlined so this module does not import the side_panel package at
    module-load time, keeping the dependency direction clean.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
