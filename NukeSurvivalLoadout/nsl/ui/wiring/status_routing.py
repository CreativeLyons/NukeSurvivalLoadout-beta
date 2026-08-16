"""Status icon to side panel routing.

Wires each pill's two buttons to the side panel. The info button opens
the README in the Info tab, and the diagnostic button opens the error
or missing-folder text in the Log tab. A pill click never switches to
the Summary tab, only the loadout strip does that.

:func:`wire_status_routing` is idempotent, so a call after
``panel.rebuild_grid`` connects the new pills only.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

# ``PluginDetail`` is imported inside the helpers. This module must
# import on hosts without PySide.

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

#: Shown when no load-result or missing info is available.
NO_DIAGNOSTIC_TEXT = "No diagnostic captured for {name}."


# Providers return a duck-typed domain object or ``None``.
PluginLookup = Callable[[str], Optional[Any]]
LoadResultLookup = Callable[[str], Optional[Any]]
MissingLookup = Callable[[str], Optional[Any]]


# Set on a pill once its signals are connected, so a second call skips
# it. The flag dies with the pill.
_WIRED_FLAG = "_nsl_status_routing_wired"


# ---------------------------------------------------------------------------
# Pure helpers - no Qt runtime
# ---------------------------------------------------------------------------


def read_readme(plugin_path: Optional[str]) -> str:
    """Return the Plugin's ``README.md`` as raw Markdown.

    :data:`NO_README_TEXT` when the file is missing or unreadable.
    README is the only metadata source NSL reads.
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

    Returns ``""`` when the object carries none of the known fields.
    """
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
    """Compose the Info-tab :class:`PluginDetail` for ``plugin_name``."""
    from nsl.ui.side_panel import PluginDetail

    plugin = plugin_lookup(plugin_name) if plugin_lookup is not None else None
    body = read_readme(getattr(plugin, "path", None))
    provenance = _provenance_for_plugin(plugin) if plugin is not None else ""
    return PluginDetail(
        plugin_name=plugin_name,
        provenance=provenance,
        body=body,
    )


def _format_missing_body(missing: Any, plugin_name: str) -> str:
    """Compose the Log-tab body for a Missing Plugin."""
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
    """Compose the Log-tab :class:`PluginDetail` for ``plugin_name``.

    Resolution order:
      1. A captured load failure, rendered as the error text.
      2. Missing-folder info, when the missing lookup resolves.
      3. :data:`NO_DIAGNOSTIC_TEXT`.

    ``pill_state`` supplies the provenance line for case 3 only.
    """
    from nsl.ui.side_panel import PluginDetail

    # 1. Load result. NSL does not wrap plugin loads in a try/except, so
    # the errors here come from the path check. There is no traceback.
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

    # 2. Missing folder.
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
            icon_value = getattr(icon, "value", str(icon))
            provenance = f"Pill status: {icon_value}"
    return PluginDetail(
        plugin_name=plugin_name,
        provenance=provenance,
        body=NO_DIAGNOSTIC_TEXT.format(name=plugin_name),
    )


# ---------------------------------------------------------------------------
# The Qt-touching helper. Only the connect path lives here
# ---------------------------------------------------------------------------


def wire_status_routing(panel: Any) -> None:
    """Connect every pill's info and diagnostic buttons to the side panel.

    Idempotent, so it is safe to re-run after
    :meth:`LoadoutPanel.rebuild_grid`.

    Optional providers, set on the panel by the orchestrator:
      * ``panel.plugin_lookup(name)`` - Plugin metadata.
      * ``panel.load_result_lookup(name)`` - load failure.
      * ``panel.missing_lookup(name)`` - missing-Plugin info.
    """
    side_panel = getattr(panel, "side_panel", None)
    grid = getattr(panel, "grid", None)
    if side_panel is None or grid is None:
        # The panel is not fully built. Signal connectors never raise.
        return

    plugin_lookup = getattr(panel, "plugin_lookup", None)
    load_result_lookup = getattr(panel, "load_result_lookup", None)
    missing_lookup = getattr(panel, "missing_lookup", None)

    pills = getattr(grid, "_pills", None) or []

    for pill in pills:
        if getattr(pill, _WIRED_FLAG, False):
            continue

        info_signal = getattr(pill, "info_clicked", None)
        diag_signal = getattr(pill, "diagnostic_clicked", None)

        # ``_pill=pill`` binds one pill per lambda. A plain ``pill``
        # would give every lambda the last one.
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
            # Defence only. A lost flag means duplicate connections,
            # which Qt tolerates for show_info and show_log.
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
    """Handle a pill's info-button click and open the Info tab."""
    name = _plugin_name_from_pill(pill)
    if not name:
        return
    detail = build_info_detail(name, plugin_lookup=plugin_lookup)
    try:
        side_panel.show_info(detail)
    except Exception:
        # A Qt failure must never leave a signal handler. A broken side
        # panel must not raise into Nuke.
        pass


def _on_diagnostic_clicked(
    pill: Any,
    side_panel: Any,
    *,
    load_result_lookup: Optional[LoadResultLookup],
    missing_lookup: Optional[MissingLookup],
) -> None:
    """Handle a pill's diagnostic-button click and open the Log tab."""
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
