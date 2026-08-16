"""In-memory loadout shapes used by the panel layer.

Despite the name, nothing here reads or writes files. The on-disk
``init.py`` format is handled by ``nsl.boot.loadout_file``.
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
    """One Plugin's state inside a loadout's sparse-diff ``plugins`` map."""

    enabled: bool
    gui_only: bool


@dataclass
class LoadoutFile:
    """One loadout's resolved in-memory state, keyed by Plugin Name."""

    name: str
    plugins: Dict[str, PluginEntry] = field(default_factory=dict)
