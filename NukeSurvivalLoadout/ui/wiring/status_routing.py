"""Status icon to side panel routing.

Wires each pill's two buttons to the side panel:

* The info button (top-right ``i``) opens the README in the Info tab.
* The diagnostic button (bottom-left ``!`` / ``?``) opens the captured
  error / missing-folder info in the Log tab.

The Summary tab is the default on first open and is **never** auto-activated
by a pill-button click; only the loadout selector strip drives Summary
auto-switch.

Public surface is a single helper, :func:`wire_status_routing`. The pure
helpers (:func:`read_readme`, :func:`build_info_detail`,
:func:`build_log_detail`) perform no Qt work, so they can be exercised
without a PySide install. Detail content is resolved through optional,
duck-typed provider callables stashed on the panel
(``panel.plugin_lookup``, ``panel.load_result_lookup``,
``panel.missing_lookup``); when a provider is missing the helper falls back
to safe defaults (:data:`NO_README_TEXT`, :data:`NO_DIAGNOSTIC_TEXT`).

The helper is **idempotent**: calling it twice (e.g. after
``panel.rebuild_grid``) reconnects only newly-created pills and leaves prior
connections in place.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

# Side-panel detail dataclass. Import lazily inside helpers/wirers - the
# module itself must be import-safe on hosts without PySide.

__all__ = [
    "PluginLookup",
    "LoadResultLookup",
    "MissingLookup",
    "NO_README_TEXT",
    "NO_DIAGNOSTIC_TEXT",
    "read_readme",
    "build_info_detail",
    "build_log_detail",
    "wire_status_routing",
]


# ---------------------------------------------------------------------------
# Fallback display strings
# ---------------------------------------------------------------------------

#: Shown when README.md is absent or unreadable.
NO_README_TEXT = "No README available for this Plugin."

#: Conservative fallback when no load-result / missing info is available. This
#: is the safety net for "wiring runs before a provider is attached";
#: production sessions always provide a real lookup.
NO_DIAGNOSTIC_TEXT = "No diagnostic captured for {name}."


# Type aliases - providers are simple callables returning a duck-typed
# domain object or ``None``. We do NOT import the concrete classes
# (:class:`NukeSurvivalLoadout.domain.scanner.Plugin` and the load-result record)
# at module scope so the wiring layer can be imported without the rest of
# the codebase paying for those imports.
PluginLookup = Callable[[str], Optional[Any]]
LoadResultLookup = Callable[[str], Optional[Any]]
MissingLookup = Callable[[str], Optional[Any]]


# Attribute name we stash on the pill once we've connected its signals so a
# second call to :func:`wire_status_routing` is a no-op for that pill. Lives
# on the widget; survives only as long as the pill itself.
_WIRED_FLAG = "_nsl_status_routing_wired"


# ---------------------------------------------------------------------------
# Pure helpers - no Qt runtime. Each helper deliberately accepts plain
# objects so callers can pass dataclass stand-ins.
# ---------------------------------------------------------------------------


def read_readme(plugin_path: Optional[str]) -> str:
    """Return the Plugin's ``README.md`` content as raw Markdown.

    Returns :data:`NO_README_TEXT` when:

    * ``plugin_path`` is None / empty (no Plugin folder known).
    * ``<plugin_path>/README.md`` does not exist.
    * The file is unreadable (permission error, decode failure).

    README is the only metadata source NSL consults in v1; no ``init.py``
    docstrings, no ``description.md``, no ``setup.py``.
    """
    if not plugin_path:
        return NO_README_TEXT
    candidate = os.path.join(plugin_path, "README.md")
    if not os.path.isfile(candidate):
        return NO_README_TEXT
    try:
        with open(candidate, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return NO_README_TEXT


def _provenance_for_plugin(plugin: Any) -> str:
    """Return a short provenance line for the side-panel header.

    Falls back gracefully on partial objects - the side panel will render
    whatever string we hand it. Production callers pass a fuller string
    composed by the domain layer; this stub keeps the pure-Python helpers
    self-contained.
    """
    # Prefer an explicit ``provenance`` attribute if the caller has built
    # one (e.g. composed by the loadout/scan integration). Otherwise derive
    # from common Plugin fields.
    explicit = getattr(plugin, "provenance", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    source = getattr(plugin, "source", None)
    if isinstance(source, str) and source:
        return f"Loaded from `{source}`"
    path = getattr(plugin, "path", None)
    if isinstance(path, str) and path:
        return f"Loaded from `{path}`"
    return ""


def build_info_detail(
    plugin_name: str,
    plugin_lookup: Optional[PluginLookup] = None,
) -> "Any":
    """Compose the :class:`PluginDetail` for the Info tab of ``plugin_name``.

    The returned object is the :class:`NukeSurvivalLoadout.ui.side_panel.PluginDetail`
    dataclass - imported lazily so the wiring module stays import-safe
    without PySide.
    """
    from NukeSurvivalLoadout.ui.side_panel import PluginDetail  # local import - see module docstring.

    plugin = plugin_lookup(plugin_name) if plugin_lookup is not None else None
    body = read_readme(getattr(plugin, "path", None))
    provenance = _provenance_for_plugin(plugin) if plugin is not None else ""
    return PluginDetail(
        plugin_name=plugin_name,
        provenance=provenance,
        body=body,
    )


def _format_missing_body(missing: Any, plugin_name: str) -> str:
    """Compose the Log-tab body for a Missing Plugin.

    A Missing Plugin click shows the Plugin's name, the path it was last
    seen at, and the relevant Loadout entries that still reference it.
    README content cached from the last successful scan is parked for v2.
    """
    last_seen = getattr(missing, "last_seen_path", None) or getattr(
        missing, "path", None
    )
    referencing = getattr(missing, "loadouts", None) or getattr(
        missing, "referencing_loadouts", None
    )

    lines = [f"Plugin not found: {plugin_name}"]
    if last_seen:
        lines.append("")
        lines.append(f"Last seen at: {last_seen}")
    if referencing:
        lines.append("")
        lines.append("Referenced by:")
        for entry in referencing:
            lines.append(f"  - {entry}")
    return "\n".join(lines)


def build_log_detail(
    plugin_name: str,
    *,
    pill_state: Optional[Any] = None,
    load_result_lookup: Optional[LoadResultLookup] = None,
    missing_lookup: Optional[MissingLookup] = None,
) -> "Any":
    """Compose the :class:`PluginDetail` for the Log tab of ``plugin_name``.

    Resolution order:

    1. If a load-result lookup is supplied and the plugin has a captured
       traceback, render the category line followed by the traceback.
    2. Else if a Missing lookup is supplied and resolves, render the
       missing-folder info (name, last-seen path, referencing Loadouts).
    3. Else fall back to :data:`NO_DIAGNOSTIC_TEXT`.

    The optional ``pill_state`` argument carries the pill's
    :class:`NukeSurvivalLoadout.ui.pill.PillState`; we use it only to derive a short
    provenance line ("session-failed", "missing on disk") that helps the
    user orient inside the Log tab when no domain object is present yet.
    """
    from NukeSurvivalLoadout.ui.side_panel import PluginDetail

    # --- 1. Load-result path -------------------------------------------
    # The load-result record carries just ``(plugin_name, success, error)``
    # - no classification, no traceback. NSL no longer wraps plugin loads in
    # its own try/except, so the only load results ever produced carry
    # FileNotFoundError-style errors from the path check. The lookup path is
    # retained so an unfailing provider can still surface that message.
    if load_result_lookup is not None:
        result = load_result_lookup(plugin_name)
        if result is not None:
            success = bool(getattr(result, "success", True))
            error = getattr(result, "error", None)
            if not success and error is not None:
                body = (
                    f"{type(error).__name__}: {error}\n"
                    "\n"
                    "(NSL no longer wraps plugin loads in its own "
                    "try/except - if Nuke crashed loading this plugin "
                    "the traceback is in the terminal output that "
                    "preceded the panel.)"
                )
                return PluginDetail(
                    plugin_name=plugin_name,
                    provenance="Load attempt failed",
                    body=body,
                )

    # --- 2. Missing path (Missing icon click) ---------------------------
    if missing_lookup is not None:
        missing = missing_lookup(plugin_name)
        if missing is not None:
            body = _format_missing_body(missing, plugin_name)
            return PluginDetail(
                plugin_name=plugin_name,
                provenance="Plugin folder not found at any configured Plugins Folder",
                body=body,
            )

    # --- 3. Fallback ----------------------------------------------------
    provenance = ""
    if pill_state is not None:
        icon = getattr(pill_state, "status_icon", None)
        if icon is not None:
            # failed / missing - we surface whichever the caller said the
            # pill was without making up other taxonomy.
            icon_value = getattr(icon, "value", str(icon))
            provenance = f"Pill status: {icon_value}"
    return PluginDetail(
        plugin_name=plugin_name,
        provenance=provenance,
        body=NO_DIAGNOSTIC_TEXT.format(name=plugin_name),
    )


# ---------------------------------------------------------------------------
# The Qt-touching helper. Only the connect path lives here; everything that
# does Real Work goes through the pure helpers above.
# ---------------------------------------------------------------------------


def wire_status_routing(panel: Any) -> None:
    """Connect every pill's info/diagnostic buttons to the side panel.

    Called once from :meth:`LoadoutPanel._wire_signals`. The helper is
    idempotent - re-running it after :meth:`LoadoutPanel.rebuild_grid` is
    safe.

    Behavior:

    * Info button (``pill.info_clicked``) → :meth:`SidePanel.show_info`
      with the Plugin's README. Auto-switches to the **Info** tab.
    * Diagnostic button (``pill.diagnostic_clicked``) →
      :meth:`SidePanel.show_log` with the captured traceback or missing
      info. Auto-switches to the **Log** tab.
    * **The Summary tab is never auto-targeted by a pill click.** Only
      the loadout selector strip drives Summary auto-switch.

    Providers (optional; set by the orchestrator before this call):

    * ``panel.plugin_lookup(name)`` - resolve Plugin metadata.
    * ``panel.load_result_lookup(name)`` - resolve failure traceback.
    * ``panel.missing_lookup(name)`` - resolve missing-Plugin info.
    """
    side_panel = getattr(panel, "side_panel", None)
    grid = getattr(panel, "grid", None)
    if side_panel is None or grid is None:
        # Defensive - the panel wasn't fully built. Bail out silently;
        # painters and signal connectors must never raise.
        return

    plugin_lookup = getattr(panel, "plugin_lookup", None)
    load_result_lookup = getattr(panel, "load_result_lookup", None)
    missing_lookup = getattr(panel, "missing_lookup", None)

    pills = getattr(grid, "_pills", None) or []

    for pill in pills:
        if getattr(pill, _WIRED_FLAG, False):
            # Idempotent: don't re-connect a pill we already wired.
            continue

        info_signal = getattr(pill, "info_clicked", None)
        diag_signal = getattr(pill, "diagnostic_clicked", None)

        # Use default-argument binding to capture ``pill`` per closure (the
        # common late-binding trap for signal/slot lambdas).
        if info_signal is not None and hasattr(info_signal, "connect"):
            info_signal.connect(
                lambda *_args, _pill=pill: _on_info_clicked(
                    _pill, side_panel, plugin_lookup
                )
            )

        if diag_signal is not None and hasattr(diag_signal, "connect"):
            diag_signal.connect(
                lambda *_args, _pill=pill: _on_diagnostic_clicked(
                    _pill,
                    side_panel,
                    load_result_lookup=load_result_lookup,
                    missing_lookup=missing_lookup,
                )
            )

        try:
            setattr(pill, _WIRED_FLAG, True)
        except Exception:
            # Pills are QWidgets - setattr should always succeed; defensive
            # only. If it doesn't, repeated wiring will duplicate signal
            # connections, which is benign for show_info/show_log (Qt
            # tolerates repeated connects); the routing contract is preserved.
            pass


def _plugin_name_from_pill(pill: Any) -> str:
    """Read ``plugin_name`` off a pill's :class:`PillState`. Never raises."""
    try:
        state = pill.state()
    except Exception:
        return ""
    name = getattr(state, "plugin_name", "")
    return name if isinstance(name, str) else ""


def _on_info_clicked(
    pill: Any,
    side_panel: Any,
    plugin_lookup: Optional[PluginLookup],
) -> None:
    """Handle a pill's info-button click - load README + activate Info tab."""
    name = _plugin_name_from_pill(pill)
    if not name:
        return
    detail = build_info_detail(name, plugin_lookup=plugin_lookup)
    try:
        side_panel.show_info(detail)
    except Exception:
        # Painter / setter failures inside Qt must never propagate out of a
        # signal handler. The *never block Nuke from starting*
        # promise extends to the panel: a broken side panel must not
        # surface as an unhandled exception.
        pass


def _on_diagnostic_clicked(
    pill: Any,
    side_panel: Any,
    *,
    load_result_lookup: Optional[LoadResultLookup],
    missing_lookup: Optional[MissingLookup],
) -> None:
    """Handle a pill's diagnostic-button click - load traceback / missing
    info + activate Log tab."""
    name = _plugin_name_from_pill(pill)
    if not name:
        return
    try:
        pill_state = pill.state()
    except Exception:
        pill_state = None
    detail = build_log_detail(
        name,
        pill_state=pill_state,
        load_result_lookup=load_result_lookup,
        missing_lookup=missing_lookup,
    )
    try:
        side_panel.show_log(detail)
    except Exception:
        pass
