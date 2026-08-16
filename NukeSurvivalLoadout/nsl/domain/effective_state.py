"""Sparse-diff resolver - per-Plugin effective (`enabled`, `gui_only`).

Both fields resolve independently against the same layer stack:

    1. active Loadout entry
    2. Global Loadout entry
    3. default

A Loadout entry that overrides only `gui_only` inherits `enabled` from
Global, and the other way round.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from nsl.data.loadout_file import LoadoutFile, PluginEntry


__all__ = [
    "Layer",
    "EffectiveState",
    "resolve_effective",
]


class Layer:
    """Provenance values surfaced in ``EffectiveState``."""

    LOADOUT = "loadout"
    GLOBAL = "global"
    DEFAULT = "default"


@dataclass(frozen=True)
class EffectiveState:
    """The resolved per-Plugin state plus provenance.

    Attributes:
        enabled: Resolved enabled flag.
        gui_only: Resolved gui_only flag.
        enabled_source: Which ``Layer`` supplied ``enabled``.
        gui_only_source: Which ``Layer`` supplied ``gui_only``.
        diverges_from_global: True when a field comes from the active
            Loadout and differs from what Global would give. Drives the
            purple divergence border on Global Plugins.
        plugin: The Plugin Name the state was resolved for.
        source: Plugins Folder origin tag, passed through untouched.
            ``None`` when a Loadout names a Plugin that was not scanned.
    """

    enabled: bool
    gui_only: bool
    enabled_source: str
    gui_only_source: str
    diverges_from_global: bool
    plugin: str
    source: Optional[str]


def _entry_for(
    container: Optional[LoadoutFile],
    plugin: str,
) -> Optional[PluginEntry]:
    if container is None:
        return None
    return container.plugins.get(plugin)


def _resolve_field(
    field_name: str,
    loadout_entry: Optional[PluginEntry],
    global_entry: Optional[PluginEntry],
    *,
    is_global_active: bool = False,
) -> tuple[bool, str, Optional[bool]]:
    """Return ``(value, source_layer, global_value_if_any)`` for one field.

    ``global_value_if_any`` is what Global would have supplied, or
    ``None``. The caller uses it for ``diverges_from_global``.

    ``is_global_active`` flips the ``enabled`` default when neither
    layer has an entry.
    """
    global_value: Optional[bool] = (
        getattr(global_entry, field_name) if global_entry is not None else None
    )

    if loadout_entry is not None:
        return (
            bool(getattr(loadout_entry, field_name)),
            Layer.LOADOUT,
            global_value,
        )
    if global_entry is not None:
        return (bool(global_value), Layer.GLOBAL, global_value)
    # No entry in either layer. The default depends on the view:
    #   * user Loadout active - enabled True. A sparse diff is silent
    #     about the plugins it does not change.
    #   * Global active       - enabled False. Global is the TD's
    #     read-only set and user plugins are not in it.
    #   * gui_only            - always False, in both views.
    # A False default under a user Loadout brings back phantom "+N pending".
    if field_name == "enabled":
        default_value = False if is_global_active else True
    else:
        default_value = False
    return (default_value, Layer.DEFAULT, global_value)


def resolve_effective(
    plugin: str,
    loadout: Optional[LoadoutFile],
    global_loadout: Optional[LoadoutFile],
    source: Optional[str] = None,
    *,
    is_global_active: bool = False,
) -> EffectiveState:
    """Resolve the effective state of ``plugin`` for the active session.

    Args:
        plugin: Plugin Name, the key used in loadout plugins maps.
        loadout: The active user Loadout, or ``None``.
        global_loadout: The Global layer collapsed to one Loadout, or
            ``None`` when no Global layer is configured.
        source: Plugins Folder origin tag, passed into the result.
        is_global_active: ``True`` in the read-only Global view. Flips
            the enabled default, see ``_resolve_field``.

    Returns:
        An ``EffectiveState`` with both flags and their provenance.
    """
    loadout_entry = _entry_for(loadout, plugin)
    global_entry = _entry_for(global_loadout, plugin)

    enabled, enabled_source, global_enabled = _resolve_field(
        "enabled", loadout_entry, global_entry,
        is_global_active=is_global_active,
    )
    gui_only, gui_only_source, global_gui_only = _resolve_field(
        "gui_only", loadout_entry, global_entry,
        is_global_active=is_global_active,
    )

    diverges = False
    if enabled_source == Layer.LOADOUT and global_enabled is not None:
        diverges = diverges or (enabled != global_enabled)
    if gui_only_source == Layer.LOADOUT and global_gui_only is not None:
        diverges = diverges or (gui_only != global_gui_only)

    return EffectiveState(
        enabled=enabled,
        gui_only=gui_only,
        enabled_source=enabled_source,
        gui_only_source=gui_only_source,
        diverges_from_global=diverges,
        plugin=plugin,
        source=source,
    )
