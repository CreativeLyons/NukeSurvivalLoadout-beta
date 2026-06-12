# Nuke Survival Loadout

A Loadout Panel for Foundry Nuke that lets compositors and TDs name, save, and switch between loadouts of enabled Plugins. Drop your Plugins into a folder, point the panel at it, toggle the ones you want loaded, and save the selection as a named Loadout. Restart Nuke and only the Plugins you enabled are added to the NUKEPATH.

Each loadout is a small, plain-Python file you can open in any text editor. If a bad Plugin ever stops Nuke from starting, you can disable it by hand and recover without the panel.

**At a glance**

- Save and switch between named Loadouts of enabled Plugins
- Toggle Plugins on and off from a visual panel
- Loadouts are plain Python: editable and recoverable by hand
- Panic mode skips every managed Plugin for emergency recovery
- Works for solo setups and studio / TD shared bases

## Install

1. Unzip and place the `NukeSurvivalLoadout` folder anywhere stable on disk (e.g. inside `~/.nuke/`, or on a studio share).
2. Open (or create) `~/.nuke/init.py` in a text editor and add one line pointing at that folder:

   ```python
   nuke.pluginAddPath("/absolute/path/to/NukeSurvivalLoadout")
   ```

3. Restart Nuke.
4. Open the **Loadout Panel** from Nuke's **Edit** menu (or press **F11**).

That single `pluginAddPath` is the whole install. NSL creates `~/.nuke/loadouts/` automatically the first time you open the panel.

Supported Nuke versions: **Nuke 13 and later**

---

Created by Tony Lyons | May 2026
