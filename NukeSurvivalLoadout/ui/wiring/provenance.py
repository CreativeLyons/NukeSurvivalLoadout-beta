"""Provenance-line wiring for the Loadout Panel's side panel header.

The provenance line is a single muted line below the Plugin-name header on
the Info and Log tabs. It identifies which Plugins Folder the Plugin is
being read from and surfaces any pending change to its load state. There
are five verbatim variants:

1. ``Loaded from `<path>```
   - Plugin loaded this session, nothing has changed.
2. ``Loaded from `<path-a>` · Will load from `<path-b>` on next restart``
   - a higher-priority folder now shadows the loaded copy; the next-startup
   resolution will pick a different folder.
3. ``Loaded from `<path>` · Disabled, will not load on next restart``
   - Plugin is loaded but the user has disabled it in the active Loadout.
4. ``Loaded from `<path>` · Source folder removed, will not appear on next restart``
   - the Plugins Folder containing this Plugin has been removed.
5. ``Not loaded this session · Will load from `<path>` on next restart``
   - Plugin is enabled in the active Loadout but wasn't loaded at startup.

This module owns one public helper - :func:`wire_provenance` - plus a tiny
:class:`SessionContext` value object and a :func:`compute_provenance` pure
formatter. :class:`ProvenanceController` re-renders the line on a Loadout
switch, folder add / remove / reorder, any pill toggle, and a change to the
restart-pending banner's visibility.

The controller does not compose the prose itself: it asks the domain
layer's :func:`NukeSurvivalLoadout.domain.effective_state.resolve_effective` for the
structured ``EffectiveState`` and routes the answer through
:func:`compute_provenance`, the thinnest possible formatter mapping
(resolver verdict + session context) onto one of the five variants. The
layer choice, the source-folder origin tag, and whether a Loadout / Global
entry exists are all read straight from the resolver's output.

Qt access goes through :mod:`NukeSurvivalLoadout.compat` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from NukeSurvivalLoadout import compat
from NukeSurvivalLoadout.domain.effective_state import (
    EffectiveState,
    Layer,
    resolve_effective,
)
from NukeSurvivalLoadout.ui.side_panel import PluginDetail


__all__ = [
    "SessionContext",
    "compute_provenance",
    "ProvenanceController",
    "wire_provenance",
]


# ---------------------------------------------------------------------------
# Session-context value object - the non-resolver inputs the formatter needs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionContext:
    """The session-level facts the resolver does not carry.

    The resolver answers *"what is the effective state of this Plugin?"*
    and *"which layer supplied each field?"* but it has no opinion on
    whether the Plugin was actually loaded at this Nuke startup, what
    path it loaded **from** this session, or whether a folder later got
    removed. These are session-runtime facts owned by the boot snapshot
    + the user-folder list. The controller threads them in as a
    :class:`SessionContext` per Plugin name; the formatter routes the
    combination of resolver-output + session-context into exactly one of
    the five canonical strings (locked wording).

    Attributes
    ----------
    loaded_this_session:
        True iff the Plugin was actually picked up by this Nuke session's
        bootstrap. False when the user has just enabled it (variant 5) or
        when boot raced past it.
    loaded_from_path:
        The folder NSL recorded this Plugin as having loaded from at
        startup. None when ``loaded_this_session`` is False.
    will_load_from_path:
        The folder NSL predicts the Plugin will load from on the next
        Nuke restart. Equal to ``loaded_from_path`` in the steady-state;
        differs when a higher-priority folder now shadows the loaded
        copy (variant 2) or when the Plugin only becomes resolvable
        after the user enables it (variant 5). None when no resolution
        is possible (Plugin will not load on next restart at all -
        e.g. disabled in active Loadout or source folder removed).
    source_folder_removed:
        True iff the Plugins Folder containing this Plugin has been
        removed from the user's configured folder list (variant 4).
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

    Reads the structural decision from ``state`` (the
    :class:`EffectiveState` returned by
    :func:`NukeSurvivalLoadout.domain.effective_state.resolve_effective`) and routes the
    combination through to one of the five canonical variants
    (locked wording).

    Variant precedence:

    1. Plugin not loaded this session AND will load on next restart →
       variant 5 (*"Not loaded this session · Will load from `<path>` on
       next restart"*). This is the just-enabled case.
    2. Plugin loaded this session AND source folder removed → variant 4.
    3. Plugin loaded this session AND not effective-enabled (will not
       load) → variant 3 (*"Disabled, will not load on next restart"*).
    4. Plugin loaded this session AND will load from a different folder
       on next restart → variant 2 (*"Will load from `<path-b>` on next
       restart"*).
    5. Plugin loaded this session, steady-state → variant 1.

    The formatter never makes a structural decision that the resolver
    could have answered: it consults ``state.enabled`` for the disabled
    leg (variant 3) - the same flag the resolver picked off the active
    Loadout / Global / default stack. The folder paths come from the
    :class:`SessionContext`, which the controller threads in from the
    snapshot reader + the user-folder list.
    """
    # Variant 5 - Plugin is enabled but wasn't loaded at startup.
    if not context.loaded_this_session:
        if context.will_load_from_path is not None and state.enabled:
            return (
                f"Not loaded this session · "
                f"{_fmt_will_load(context.will_load_from_path)}"
            )
        # Defensive fallback - Plugin is neither loaded nor scheduled.
        # The five canonical variants don't cover this exact case; render
        # the closest honest variant (variant 5 prefix only) without
        # inventing wording. This is a no-op for v1 because the
        # snapshot / scanner pair never produces this combo in
        # practice, but the branch keeps the formatter total.
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


# Type aliases for the orchestrator-installable callbacks. Kept loose so
# the wiring layer can hand in either bound methods or plain functions.
ContextProvider = Callable[[str], SessionContext]
LoadoutProvider = Callable[[], "object"]


class _BannerWatcher(compat.QtCore.QObject):
    """Event filter watching the change-detected banner for show / hide.

    The banner doesn't emit a ``visibility_changed`` signal - visibility
    flips via ``setVisible`` from the banner-state wiring elsewhere. We
    install an event filter that fires ``visibility_changed`` whenever
    the banner widget receives a ``Show`` or ``Hide`` event so the
    controller can re-render. (The banner's ``dismissed`` signal is also
    connected; that path covers the user-initiated dismissal.)
    """

    visibility_changed = compat.QtCore.Signal()

    def eventFilter(self, _obj, event):  # type: ignore[no-untyped-def]
        et = event.type()
        if et == compat.QtCore.QEvent.Show or et == compat.QtCore.QEvent.Hide:
            self.visibility_changed.emit()
        return False  # never consume - let the banner do its thing.


class ProvenanceController(compat.QtCore.QObject):
    """Listens for the four re-render triggers and repaints the side panel.

    Attached as ``panel._provenance_controller`` by :func:`wire_provenance`.
    Holds no domain state of its own - every re-render fetches fresh
    state from the configured providers and from
    :func:`resolve_effective`. The controller exposes a small public
    surface so rebuild paths can drive it deterministically:

    * :meth:`set_focused_plugin` - switch which Plugin the panel is
      showing in the Info or Log tab. Triggers a re-render.
    * :meth:`set_session_context_provider` - install / replace the
      callback that returns a :class:`SessionContext` for a Plugin name.
    * :meth:`set_loadout_provider` - install / replace the callbacks
      that return the active user Loadout and the resolved Global
      Loadout.
    * :meth:`set_source_provider` - install / replace the callback that
      returns the Plugins Folder origin tag for a Plugin name. Optional;
      the resolver accepts ``None``.
    * :meth:`bind_grid` - re-attach pill-toggle listeners after a grid
      rebuild. Idempotent - disconnects any prior bindings before
      re-attaching.
    * :meth:`render_now` - synchronous re-render. Public so callers can
      drive a render without firing a Qt signal.

    All five wiring connections are signal → :meth:`render_now`; the
    controller has no other slots.
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

        # Track which pill signals we've connected so :meth:`bind_grid`
        # can detach them on the next rebuild. A list of (pill, slot)
        # pairs; the slot is the same closure each time so disconnect
        # matches.
        self._pill_connections: list[tuple[object, object]] = []

        # Banner visibility watcher.
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
        """Optional README / log text source. When absent re-renders
        keep the existing body text and only refresh the provenance
        line by re-emitting :meth:`SidePanel.show_info` /
        :meth:`SidePanel.show_log` with the previously-shown body.
        """
        self._body_provider = provider

    # -- grid rebinding -------------------------------------------------

    def bind_grid(self, grid=None) -> None:  # type: ignore[no-untyped-def]
        """Connect pill ``toggled`` signals on the current grid.

        Idempotent - detaches any prior connections before re-attaching.
        Call after :meth:`LoadoutPanel.rebuild_grid` so toggle-driven
        re-renders survive a Loadout switch.
        """
        # Detach prior connections.
        for pill, slot in self._pill_connections:
            try:
                pill.toggled.disconnect(slot)
            except (RuntimeError, TypeError):
                # Pill may already be gone (rebuild_grid deletes the
                # widget) or the signal may have been disconnected; both
                # are benign at rebuild time.
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
        """Synchronously recompute and apply the provenance line.

        No-op when no Plugin is focused or no context provider is
        installed: the side panel keeps its current state. Can be called
        directly to drive a deterministic render without firing Qt
        signals through the dispatcher.
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

        # Domain layer is the source of truth for the structural decision.
        state = resolve_effective(plugin, loadout, global_loadout, source)
        context = self._context_provider(plugin)
        provenance = compute_provenance(state, context)

        # Push into whichever content tab is currently targeted. The
        # provenance line belongs on both Info and Log tabs with
        # the same content; we re-emit ``show_info`` / ``show_log`` so
        # the side-panel formatter (composed Markdown / HTML) stays the
        # single owner of the rendered surface.
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
        """Slot for ``PluginPill.toggled`` - re-render regardless of
        which pill toggled. A toggle on any pill changes the active
        Loadout's effective state, which can change the focused
        Plugin's provenance (e.g. an enable on a higher-priority
        shadow). Re-rendering on every toggle is the conservative
        contract; the per-Plugin filter could be added later if
        repaint cost ever shows up in profiling.
        """
        self.render_now()


# ---------------------------------------------------------------------------
# Public wire helper - the single entry point the orchestrator calls.
# ---------------------------------------------------------------------------


def wire_provenance(panel) -> None:  # type: ignore[no-untyped-def]
    """Install a :class:`ProvenanceController` on ``panel`` and wire it.

    One helper per wiring module, called from a single
    ``wire_<module>(self)`` line in
    :meth:`NukeSurvivalLoadout.ui.panel.LoadoutPanel._wire_signals`.
    The helper:

    1. Constructs a :class:`ProvenanceController` and attaches it to
       ``panel._provenance_controller``. Idempotent - calling
       :func:`wire_provenance` a second time replaces the prior
       controller cleanly (the QObject parent dance keeps Qt happy).
    2. Connects the four re-render triggers to
       :meth:`ProvenanceController.render_now`:

       * ``panel.loadout_strip.loadout_selected`` (Loadout switch)
       * ``panel.folder_card.add_folder_requested`` (folder added)
       * ``panel.folder_card.remove_confirmed`` (folder removed)
       * ``panel.folder_card.reorder_requested`` (folder reordered;
         priority changes may flip variant 2 → variant 1 / vice versa)
       * ``panel.banner.dismissed`` (restart-pending state dismissed)

    3. Calls :meth:`ProvenanceController.bind_grid` so pill-toggle
       re-renders are live on whatever grid the panel was constructed
       with. Downstream grid rebuilds should call ``bind_grid`` again.

    Providers (session-context, active Loadout, Global Loadout, source
    tag, body text) are installed by separate orchestrator-side wiring
    once the data layer is bound. The controller no-ops on
    :meth:`render_now` until those are in place: this keeps the
    snapshot path working without forcing a full data-layer stub.
    """
    # Idempotent replace - if a prior controller exists, clean its
    # event filter / parent links before installing the new one.
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

    # Re-render triggers - all five wire to :meth:`render_now`.
    if getattr(panel, "loadout_strip", None) is not None:
        # Loadout switch - payload is the new active Loadout name; the
        # controller's render path reads from the loadout provider on
        # each fire so we ignore the payload here.
        panel.loadout_strip.loadout_selected.connect(
            lambda _name: controller.render_now()
        )

    if getattr(panel, "folder_card", None) is not None:
        panel.folder_card.add_folder_requested.connect(controller.render_now)
        # FolderCard's user-facing "remove" signal - emitted after the
        # confirmation dialog returns Yes. The per-row ``remove_requested``
        # is the pre-confirmation intent; the panel-level
        # ``remove_confirmed`` is the "the row is gone" signal.
        panel.folder_card.remove_confirmed.connect(
            lambda _path: controller.render_now()
        )
        panel.folder_card.reorder_requested.connect(
            lambda _order: controller.render_now()
        )

    if getattr(panel, "banner", None) is not None:
        panel.banner.dismissed.connect(controller.render_now)

    # Bind pill-toggle listeners on the current grid. Idempotent.
    controller.bind_grid()
