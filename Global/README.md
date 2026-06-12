# NSL Global layer

This folder is the studio-wide baseline for Nuke Survival Loadout. Plugins
here load for every artist, before any personal loadout. NSL never writes
inside this folder; filesystem permissions on it are the access control.

## Layout

```
Global/
├── init.py              # chain head - the only file here that EXECUTES
├── plugins/             # drop plugin folders in here
└── Global_Loadout/      # optional curated loadout - PARSED, never executed
    └── init.py          # (copy from ~/.nuke/loadouts/Global_Loadout/)
```

## Zero-config default

Drop plugin folders into `Global/plugins/` and relaunch Nuke. With no
`Global_Loadout/init.py` present, every folder loads (names starting with
`_` or `.` are skipped). Nothing else to author.

## Head vs loadout: who runs, who is read

- `Global/init.py` is the **head**: a real Python file Nuke executes at
  boot. It declares two redefinable folder paths (`global_plugins`,
  `global_loadout`) and calls the NSL loader. Custom boot code belongs
  here, under the `Custom boot code` label.
- `Global_Loadout/init.py` is **declarative config in this role**: NSL
  parses it for plugin on/off and GUI-only directives and never executes
  it here. Helper functions or custom code inside it only run when the
  same file lives in user-land as a normal loadout.

## Curating with a Global Loadout (save-and-copy)

1. Drop plugins into `Global/plugins/` and launch Nuke. They appear in
   the NSL panel's read-only Global row.
2. Toggle plugins on/off as desired, then **Save As** with the name
   `Global_Loadout` (an ordinary save).
3. Quit Nuke and copy the saved folder into this one:
   `~/.nuke/loadouts/Global_Loadout/`  →  `Global/Global_Loadout/`
4. Relaunch. The head parses the copy and the panel's Global row shows
   the curated loadout. The staged copy under `~/.nuke/loadouts/` can be
   deleted afterwards, or kept for future edits.

### `Global_Loadout` name rules

- While no `Global/Global_Loadout/init.py` exists (case A), a user-land
  loadout named `Global_Loadout` behaves like any normal loadout: listed,
  activatable, loads at boot. Use this to test-drive before the copy.
- Once the copy exists in this folder (case B), the user-land
  `Global_Loadout` is hidden from the loadout list and never activatable.
  Save As `Global_Loadout` still works as the staging save: the file is
  written to `~/.nuke/loadouts/Global_Loadout/` and the panel explains
  the copy step.
- Only `Global` and `Custom` stay reserved names.

## Path rules

The head's two folder declarations accept:

- `./relative` - resolved against this folder (the head file's location).
- `~/path` - home-expanded.
- `/absolute/path` - used as-is.

The head is plain Python, so a TD can compute paths instead, e.g.:

```python
import os
global_plugins = os.environ.get("MY_STUDIO_PLUGINS", "./plugins")
```

## Portability note

Saved loadout files carry absolute paths. For the Global plugins folder
the panel writes the var name `global_plugins`, and at every boot the
loader binds that NAME to the head's freshly resolved value in memory.
The absolute string written in the file is ignored in the Global role, so
the same `Global/` folder works after relocating the NSL install or
deploying to machines with different mount points. Files on disk are
never rewritten.

## User overrides

A plugin name the artist's active loadout will touch belongs to the
artist's file: the Global loader skips it so each plugin loads from
exactly one place per session. "Will touch" covers names the loadout
explicitly mentions (enabled or disabled) AND names visible in the user
plugin folders it declares - so a user folder shadowing a Global plugin
by name wins even without an explicit line. Panic mode disables only
user-added plugins; the Global layer still loads.
