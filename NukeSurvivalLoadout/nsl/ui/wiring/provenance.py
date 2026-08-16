"""Provenance-line wiring for the Loadout Panel's side panel header.

The provenance line sits below the Plugin name on the Info and Log
tabs. It names the folder the Plugin loads from and any pending change.
:func:`compute_provenance` holds the five exact strings.

:class:`ProvenanceController` re-renders the line and never composes the
prose. It reads the structural answer from
:func:`nsl.domain.effective_state.resolve_effective`.

Qt access goes through :mod:`nsl.compat` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from nsl import compat
from nsl.domain.effective_state import (
    EffectiveState,
    Layer,
    resolve_effective,
)
from nsl.ui.side_panel import PluginDetail


__all__ = [
    "SessionContext",
    "compute_provenance",
    "ProvenanceController",
    "wire_provenance",
]


# ---------------------------------------------------------------------------
# Session-context value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionContext:
    """The session-level facts the resolver does not carry.

    The resolver says what the effective state is. It does not know
    whether the Plugin loaded at this Nuke startup, which folder it
    loaded from, or whether a folder was removed later.

    Attributes
    ----------
    loaded_this_session:
        False when the user has just enabled the Plugin, or when boot
        did not reach it.
    loaded_from_path:
        ``None`` when ``loaded_this_session`` is False.
    will_load_from_path:
        The folder predicted for the next restart. ``None`` when the
        Plugin will not load at all.
    source_folder_removed:
        The Plugins Folder holding this Plugin is gone from the list.
    """

    loaded_this_session: bool
    loaded_from_path: Optional[str]
    will_load_from_path: Optional[str]
    source_folder_removed: bool


# ---------------------------------------------------------------------------
# Pure formatter - verbatim mapping onto the five canonical strings.
# ---------------------------------------------------------------------------


def _fmt_loaded(path: str) -> str:
    return f"Loaded from `{path}`"


def _fmt_will_load(path: str) -> str:
    return f"Will load from `{path}` on next restart"


def compute_provenance(state: EffectiveState, context: SessionContext) -> str:
    """Format the provenance line for a Plugin.

    The wording is locked. Checked in this order:
      * Variant 5 - not loaded this session, will load next restart.
      * Variant 4 - loaded, source folder removed.
      * Variant 3 - loaded, not enabled, so it will not load again.
      * Variant 2 - loaded, a different folder wins next restart.
      * Variant 1 - loaded, nothing has changed.
    """
    # Variant 5 - Plugin is enabled but wasn't loaded at startup.
    if not context.loaded_this_session:
        if context.will_load_from_path is not None and state.enabled:
            return (
                f"Not loaded this session · "
                f"{_fmt_will_load(context.will_load_from_path)}"
            )
        # Neither loaded nor scheduled. No variant covers this, so return
        # the prefix alone rather than invent wording.
        return "Not loaded this session"

    loaded_path = context.loaded_from_path or ""

    # Variant 4 - source folder removed.
    if context.source_folder_removed:
        return (
            f"{_fmt_loaded(loaded_path)} · "
            f"Source folder removed, will not appear on next restart"
        )

    # Variant 3 - effective-disabled in the active Loadout.
    if not state.enabled:
        return (
            f"{_fmt_loaded(loaded_path)} · "
            f"Disabled, will not load on next restart"
        )

    # Variant 2 - different folder will resolve next restart (shadowing).
    will_load = context.will_load_from_path
    if will_load is not None and will_load != loaded_path:
        return (
            f"{_fmt_loaded(loaded_path)} · "
            f"{_fmt_will_load(will_load)}"
        )

    # Variant 1 - steady-state.
    return _fmt_loaded(loaded_path)


# ---------------------------------------------------------------------------
# Controller - owns the side-panel re-render lifecycle.
# ---------------------------------------------------------------------------


# Loose on purpose, so a bound method or a plain function both fit.
ContextProvider = Callable[[str], SessionContext]
LoadoutProvider = Callable[[], "object"]


class _BannerWatcher(compat.QtCore.QObject):
    """Event filter that reports banner show and hide.

    The banner has no visibility signal. Visibility flips through
    ``setVisible`` elsewhere, so watch the Show and Hide events instead.
    """

    visibility_changed = compat.QtCore.Signal()

    def eventFilter(self, _obj, event):  # type: ignore[no-untyped-def]
        et = event.type()
        if et == compat.QtCore.QEvent.Show or et == compat.QtCore.QEvent.Hide:
            self.visibility_changed.emit()
        return False  # never consume - the banner still gets the event.


class ProvenanceController(compat.QtCore.QObject):
    """Repaints the side-panel provenance line on every trigger.

    Attached as ``panel._provenance_controller`` by
    :func:`wire_provenance`. Holds no domain state. Every render reads
    fresh values from the installed providers and from
    :func:`resolve_effective`.

    Call :meth:`bind_grid` again after a grid rebuild, or pill toggles
    stop re-rendering.
    """

    def __init__(self, panel) -> None:  # type: ignore[no-untyped-def]
        super().__init__(panel)
        self._panel = panel
        self._focused_plugin: Optional[str] = None
        self._context_provider: Optional[ContextProvider] = None
        self._loadout_provider: Optional[LoadoutProvider] = None
        self._global_loadout_provider: Optional[LoadoutProvider] = None
        self._source_provider: Optional[Callable[[str], Optional[str]]] = None
        self._body_provider: Optional[Callable[[str], str]] = None

        # Kept so bind_grid can disconnect on the next rebuild. The slot
        # is the same bound method each time, so disconnect matches.
        self._pill_connections: list[tuple[object, object]] = []

        self._banner_watcher = _BannerWatcher(self)
        if getattr(panel, "banner", None) is not None:
            panel.banner.installEventFilter(self._banner_watcher)
        self._banner_watcher.visibility_changed.connect(self.render_now)

    # -- provider installation ------------------------------------------

    def set_focused_plugin(self, plugin_name: Optional[str]) -> None:
        """Track which Plugin the side panel is currently showing."""
        self._focused_plugin = plugin_name
        self.render_now()

    def focused_plugin(self) -> Optional[str]:
        return self._focused_plugin

    def set_session_context_provider(self, provider: ContextProvider) -> None:
        self._context_provider = provider

    def set_loadout_provider(
        self,
        active: LoadoutProvider,
        global_loadout: Optional[LoadoutProvider] = None,
    ) -> None:
        self._loadout_provider = active
        self._global_loadout_provider = global_loadout

    def set_source_provider(
        self, provider: Callable[[str], Optional[str]]
    ) -> None:
        self._source_provider = provider

    def set_body_provider(self, provider: Callable[[str], str]) -> None:
        """Install the README or log text source.

        Without it a re-render keeps the body already on screen and
        refreshes the provenance line only.
        """
        self._body_provider = provider

    # -- grid rebinding -------------------------------------------------

    def bind_grid(self, grid=None) -> None:  # type: ignore[no-untyped-def]
        """Connect pill ``toggled`` signals on the current grid.

        Idempotent. Call it after :meth:`LoadoutPanel.rebuild_grid`, or
        toggle-driven re-renders stop after a Loadout switch.
        """
        for pill, slot in self._pill_connections:
            try:
                pill.toggled.disconnect(slot)
            except (RuntimeError, TypeError):
                # The pill may already be deleted, or already
                # disconnected. Both are normal at rebuild time.
                pass
        self._pill_connections.clear()

        grid = grid if grid is not None else getattr(self._panel, "grid", None)
        if grid is None:
            return

        pills = getattr(grid, "_pills", None)
        if pills is None:
            return
        for pill in pills:
            toggled = getattr(pill, "toggled", None)
            if toggled is None:
                continue
            slot = self._on_pill_toggled
            toggled.connect(slot)
            self._pill_connections.append((pill, slot))

    # -- re-render ------------------------------------------------------

    def render_now(self) -> None:
        """Recompute and apply the provenance line now.

        No-op when no Plugin is focused or no context provider is
        installed. The side panel then keeps its current state.
        """
        plugin = self._focused_plugin
        if plugin is None:
            return
        if self._context_provider is None or self._loadout_provider is None:
            return

        loadout = self._loadout_provider()
        global_loadout = (
            self._global_loadout_provider()
            if self._global_loadout_provider is not None
            else None
        )
        source = (
            self._source_provider(plugin)
            if self._source_provider is not None
            else None
        )

        state = resolve_effective(plugin, loadout, global_loadout, source)
        context = self._context_provider(plugin)
        provenance = compute_provenance(state, context)

        # Re-emit ``show_info`` and ``show_log`` so the side panel stays
        # the one owner of the rendered text.
        side_panel = getattr(self._panel, "side_panel", None)
        if side_panel is None:
            return

        info_detail = getattr(side_panel, "_info_plugin", None)
        if info_detail is not None and info_detail.plugin_name == plugin:
            body = (
                self._body_provider(plugin)
                if self._body_provider is not None
                else info_detail.body
            )
            side_panel.show_info(
                PluginDetail(
                    plugin_name=plugin,
                    provenance=provenance,
                    body=body,
                )
            )

        log_detail = getattr(side_panel, "_log_plugin", None)
        if log_detail is not None and log_detail.plugin_name == plugin:
            body = (
                self._body_provider(plugin)
                if self._body_provider is not None
                else log_detail.body
            )
            side_panel.show_log(
                PluginDetail(
                    plugin_name=plugin,
                    provenance=provenance,
                    body=body,
                )
            )

    # -- private slots --------------------------------------------------

    def _on_pill_toggled(self, _enabled: bool) -> None:  # noqa: D401
        """Re-render on any pill toggle.

        A toggle on one pill can change the focused Plugin's provenance,
        so the trigger is deliberately wide.
        """
        self.render_now()


# ---------------------------------------------------------------------------
# Public wire helper - the single entry point the orchestrator calls.
# ---------------------------------------------------------------------------


def wire_provenance(panel) -> None:  # type: ignore[no-untyped-def]
    """Install a :class:`ProvenanceController` on ``panel`` and wire it.

    Idempotent. A second call replaces the prior controller and removes
    its event filter.

    The providers (session context, Loadouts, source tag, body text) are
    installed elsewhere. Until then :meth:`render_now` is a no-op, so the
    snapshot path works with no data layer.
    """
    existing = getattr(panel, "_provenance_controller", None)
    if existing is not None:
        try:
            if getattr(panel, "banner", None) is not None and (
                getattr(existing, "_banner_watcher", None) is not None
            ):
                panel.banner.removeEventFilter(existing._banner_watcher)
        except RuntimeError:
            pass

    controller = ProvenanceController(panel)
    panel._provenance_controller = controller

    # Re-render triggers.
    if getattr(panel, "loadout_strip", None) is not None:
        panel.loadout_strip.loadout_selected.connect(
            lambda _name: controller.render_now()
        )

    if getattr(panel, "folder_card", None) is not None:
        panel.folder_card.add_folder_requested.connect(controller.render_now)
        # ``remove_confirmed`` fires after the dialog returns Yes. The
        # per-row ``remove_requested`` is only the intent, so avoid it.
        panel.folder_card.remove_confirmed.connect(
            lambda _path: controller.render_now()
        )
        panel.folder_card.reorder_requested.connect(
            lambda _order: controller.render_now()
        )

    if getattr(panel, "banner", None) is not None:
        panel.banner.dismissed.connect(controller.render_now)

    controller.bind_grid()
