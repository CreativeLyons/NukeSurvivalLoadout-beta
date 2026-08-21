# Changelog

All notable changes to Nuke Survival Loadout are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Plugins Folder priority was inverted. The top folder now wins a
  duplicate Plugin Name, at boot and in the grid, as the panel says.

## [0.2.1] - 2026-08-18

### Changed

- Comments and docstrings rewritten across all 62 modules to a shorter,
  plainer standard. Comment volume is down by more than half.

Documentation only. The executable code is unchanged, verified by
comparing the syntax tree of every file before and after, so panel
behaviour, loadout files, and the on-disk format are identical to 0.2.0.

## [0.2.0] - 2026-08-15

### Added

- **GUI-only sort mode** in the Plugins grid sort dropdown. Groups the
  Plugins that load everywhere above those marked GUI-only, alphabetical
  within each group, with a divider naming each group. Sits under
  `Status` in the dropdown, and like every sort mode it is per-session
  and resets on Nuke quit.
- **Toggle GUI-only** bulk action on the Plugins grid toolbar, between
  the Enable / Disable buttons and Select All / Deselect All. Syncs
  GUI-only across the selected Plugins in one click: turns them all on
  unless they are already all on, in which case it turns them all off.
  So one click syncs a mixed selection and a second clears it. Acts on
  the whole selection, records a single undo entry, and skips Global
  Plugins silently. Does nothing when nothing is selected.

### Fixed

- Bulk actions on a saved Loadout no longer commit themselves silently.
  `Enable Selected`, `Disable Selected`, and the new GUI-only toggle
  wrote straight to disk for any named Loadout that already existed,
  which advanced the saved baseline and left the Save button greyed out
  even though the Loadout had just changed. Bulk edits are now held in
  memory and persist on an explicit Save, matching single-Plugin
  toggles. This also fixes a desync where undoing a bulk edit reverted
  memory but left the change on disk, so a restart resurrected it.
- Re-ordering or filtering the Plugins grid no longer clears the
  change indicators. Switching sort mode, typing in the search field,
  or toggling a folder's visibility rebuilds the grid, and the rebuild
  was dropping the GUI-only change markers on each pill, the coloured
  cell backdrops behind them, and the panic dim.

## [0.1.0] - 2026-06-18

### Added

- Initial beta release: Loadout Panel for saving, naming, and switching
  between loadouts of enabled Plugins, with plain-Python loadout files,
  panic mode, and a Global layer for studio/TD shared baselines.

### Changed

- Release packaging is now self-contained inside the `NukeSurvivalLoadout/`
  install folder: Nuke entrypoints live at the top level, implementation
  code lives in `nsl/`, and the Global baseline lives in `Global/`.

### Fixed

- File IO no longer depends on the host locale. Loadout and dispatcher
  writes are pinned to UTF-8 with LF newlines, and the boot-time
  dispatcher read is pinned to UTF-8. This fixes save failures and
  unparseable loadout files on Linux sessions with a non-UTF-8 locale
  (for example `LANG=C` render-farm launches).
- Terminal logging no longer raises when stdout cannot encode the
  status glyphs or a non-ASCII plugin name. Log lines degrade
  gracefully instead of aborting the Global layer load at boot.
- Right-click context menus (plugin pills, folder cards) and the
  remove-folder confirmation dialog now work on PySide2-based Nuke
  versions (13 to 15). These code paths called Qt's `exec()` alias,
  which the PySide2 builds bundled with Nuke do not provide; all modal
  calls now route through a PySide2/PySide6 compatibility shim.
- Generated loadout and dispatcher files now serialize folder paths
  (and plugin names) as proper Python literals. On Windows, backslash
  paths such as `C:\Users\...` previously rendered as broken string
  escapes: a `SyntaxError` at Nuke boot in the common case, silently
  corrupted paths (`\t` becoming a tab) in others, and a corrupt
  dispatcher could drop the entire Plugins Folder list on its next
  write. Existing files are read back unchanged.
- The loadouts folder now resolves under `HOME` when that variable is
  set, matching how Nuke itself locates `~/.nuke` on Windows. On
  Windows machines with `HOME` defined (common in studio pipelines),
  NSL previously saved loadouts into a different `.nuke` directory
  than the one Nuke boots from, so saves never loaded.
- Loadout names that differ only by letter case no longer collide
  destructively on case-insensitive filesystems (Windows, default
  macOS volumes). Creating or saving `Foo` when `foo` exists now picks
  the next free suffix instead of silently overwriting the existing
  loadout, and renaming a loadout only to change its capitalization
  works instead of self-colliding.
- Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`,
  `COM1`-`COM9`, `LPT1`-`LPT9`) are rejected as loadout names on every
  platform, so a loadout created on macOS or Linux stays usable when
  synced to a Windows machine.
- Plugins Folder identity checks (duplicate detection, removal
  matching, reorder validation, the panel's Loaded status, and
  session-load deduplication) now compare paths case-insensitively on
  Windows, so the same folder picked or typed with different casing is
  recognized as one folder.
- "Open menu.py / init.py" on Windows now opens the file in the
  registered text editor (falling back to Notepad) instead of the
  default `.py` association, which on machines with Python installed
  would execute the script rather than open it.
- Saving is more robust on Windows: the atomic file replace retries
  briefly through transient antivirus/indexer locks, deleting a
  loadout clears the read-only attribute where needed, and refused
  rename, delete, or duplicate operations are handled as structured
  filesystem errors instead of raw tracebacks.
