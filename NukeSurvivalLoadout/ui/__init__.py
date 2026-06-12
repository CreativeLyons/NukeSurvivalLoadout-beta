"""NSL UI layer - PySide widgets composing the Loadout Panel.

Every widget submodule imports Qt through ``NukeSurvivalLoadout.compat`` and therefore
triggers PySide resolution at import time. This package does NOT
auto-import its submodules - that would pull Qt in on every ``import
NukeSurvivalLoadout`` (the headless boot path forbids Qt). Consumers import each widget
explicitly:

    from NukeSurvivalLoadout.ui.pill import Pill
    from NukeSurvivalLoadout.ui.grid import Grid
    from NukeSurvivalLoadout.ui.side_panel import SidePanel
    ... and so on.
"""
