"""In-memory loadout shapes used by the panel layer.

Public surface:
    - ``PluginEntry`` - dataclass for one plugin's per-Loadout state.
    - ``LoadoutFile`` - dataclass for one loadout's resolved state.

These started life as the ``.loadout`` JSON document model. The JSON era
is over - loadout files on disk are chain-format ``init.py`` files read
by ``nsl.boot.loadout_file`` - but the panel layer still
uses these two dataclasses as its in-memory shapes (active model, Global
model, baselines, diff math), so they live on here without any file I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

__all__ = [
    "PluginEntry",
    "LoadoutFile",
]


@dataclass
class PluginEntry:
    """One Plugin's state inside a loadout's sparse-diff ``plugins`` map.

    Both fields are part of the contract: every entry carries the full
    two-field form, always.
    """

    enabled: bool
    gui_only: bool


@dataclass
class LoadoutFile:
    """One loadout's resolved in-memory state, keyed by Plugin Name."""

    name: str
    plugins: Dict[str, PluginEntry] = field(default_factory=dict)
