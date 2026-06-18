"""Sparse-diff resolver - per-Plugin effective (`enabled`, `gui_only`).

Public surface:
    - ``Layer`` - string enum of provenance values (``LOADOUT`` / ``GLOBAL``
      / ``DEFAULT``).
    - ``EffectiveState`` - dataclass returned by ``resolve_effective``.
    - ``resolve_effective(plugin, loadout, global_loadout, source)`` - pure
      function: takes the Plugin Name, the active user Loadout (or None),
      the resolved Global Loadout (or None), and the Plugins Folder origin
      tag, and returns an ``EffectiveState``.

Both fields resolve independently against the same layer stack with
identical precedence:

    1. active Loadout entry (if the Plugin appears in `loadout.plugins`)
    2. Global Loadout entry (if the Plugin appears in `global_loadout.plugins`)
    3. default `(false, false)`

The resolution is field-by-field: a Loadout entry that overrides only
`gui_only` inherits `enabled` from Global, and vice versa. (The on-disk
schema always writes both fields in the same entry, but a future entry
shape that carried just one is handled the same way; current consumers
build entries from the existing ``PluginEntry`` dataclass which carries
both.)
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
        enabled_source: Which layer supplied ``enabled``
            (``Layer.LOADOUT`` / ``Layer.GLOBAL`` / ``Layer.DEFAULT``).
        gui_only_source: Which layer supplied ``gui_only``.
        diverges_from_global: True iff at least one resolved field comes
            from the active Loadout entry **and** differs from the value
            Global would have supplied for that field. Drives the panel's
            purple divergence border on Global Plugins.
        plugin: The Plugin Name the state was resolved for.
        source: The Plugins Folder origin tag carried through from the
            caller (the scanner / inventory layer). Opaque to the resolver
 - left to the caller (panel UI, snapshot writer) to interpret.
            ``None`` when the Plugin is not in the scanned inventory but
            is referenced by a Loadout entry (orphan-deviation case).
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

    ``global_value_if_any`` is the value Global would have supplied
    (``None`` if Global had no entry for this Plugin). Used by the caller
    to compute ``diverges_from_global``.

    ``is_global_active`` flips the default for ``enabled`` when no
    entry exists in either layer - see the default-value branch below.
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
    # No entry in either layer → defaulting branch.
    #
    # ``enabled`` default depends on which view the user is in:
    #
    # * ``is_global_active=False`` (a user loadout is active) →
    #   default True. Mirrors the panel resolver's
    #   ``PluginEntry(enabled=True, gui_only=False)`` fallback so the
    #   sparse-diff contract holds: the user
    #   file is silent on a plugin → load it with default behaviour.
    #   Without this, sparse loadouts produced phantom "+N pending"
    #   on every restart (panel said enable, loader skipped).
    # * ``is_global_active=True`` (Global is active - the read-only
    #   Global view) → default False. Global is "what the TD shipped";
    #   user-added plugins are not part of that view. Defaulting them
    #   to enabled would graft the user's plugins onto Global and
    #   surface a "+N would load on restart" against a slot the user
    #   can't save. In the Global view, all user-added plugins are
    #   treated as off; only plugins the Global loadout names carry an
    #   assumed on/off state.
    #
    # ``gui_only`` always defaults False (permissive default - load
    # everywhere unless told otherwise).
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
        plugin: Plugin Name (the key used in loadout plugins maps).
        loadout: The active user Loadout, or ``None`` when no user Loadout
            is active (rare; the panel always has an active Loadout in v1,
            but the resolver is robust to either).
        global_loadout: The resolved Global Loadout (the Global layer
            collapsed to a single in-memory Loadout), or ``None`` when no
            Global layer is configured.
        source: The Plugins Folder origin tag for ``plugin``, carried
            through into ``EffectiveState.source``. Opaque to the resolver.
        is_global_active: ``True`` when the active "loadout" is the
            read-only Global view (no user loadout overlay). Flips the
            default-enabled behaviour for plugins with no entry in
            either layer - see ``_resolve_field``.

    Returns:
        An ``EffectiveState`` with both flags resolved field-by-field
        and provenance recorded per field.
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
