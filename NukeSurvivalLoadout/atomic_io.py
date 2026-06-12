"""Atomic filesystem primitives for NSL.

All NSL writes use atomic replace, and the target folder is ensured to exist
before every write.

Public surface:
  - ``write_atomic(path, content)``
  - ``ensure_parent_dir(path)``
  - ``sweep_orphan_tmp(folder)``

OSError is propagated unchanged; callers wrap.
"""

from __future__ import annotations

import os
from typing import Union

__all__ = ["write_atomic", "ensure_parent_dir", "sweep_orphan_tmp"]

PathLike = Union[str, "os.PathLike[str]"]


def ensure_parent_dir(path: PathLike) -> None:
    """Create the parent directory of ``path`` if missing.

    Idempotent. No-op when the parent already exists or when ``path`` has
    no parent component (empty parent string).
    """
    parent = os.path.dirname(os.fspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def write_atomic(path: PathLike, content: Union[str, bytes]) -> None:
    """Write ``content`` to ``path`` via write-to-temp-then-rename.

    Steps:
      1. ``ensure_parent_dir(path)`` so callers get lazy folder creation.
      2. Write the full payload to a sibling ``<path>.tmp``.
      3. ``os.replace`` the temp file over the target - same-dir, atomic
         on POSIX and NTFS.

    If the write to the temp file raises, the temp file is removed and the
    original target is left untouched. ``OSError`` from any step propagates.
    """
    target = os.fspath(path)
    ensure_parent_dir(target)

    tmp = target + ".tmp"

    if isinstance(content, bytes):
        mode = "wb"
        payload: Union[str, bytes] = content
        open_kwargs: dict = {}
    else:
        mode = "w"
        payload = content
        # Text writes are pinned to UTF-8 + LF so the bytes never depend
        # on the host locale (LANG=C farm sessions resolve the default
        # encoding to ASCII) and are identical on every platform.
        # Rendered loadout files are Python source; Python 3 parses
        # source as UTF-8, so the write side must guarantee UTF-8.
        open_kwargs = {"encoding": "utf-8", "newline": "\n"}

    try:
        with open(tmp, mode, **open_kwargs) as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    os.replace(tmp, target)


def sweep_orphan_tmp(folder: PathLike) -> int:
    """Delete direct ``.tmp`` siblings inside ``folder``.

    Non-recursive. Only removes regular files whose name ends with
    ``.tmp``; symlinks, subdirectories, and any non-``.tmp`` files are
    left untouched. Returns the count of files deleted.

    Returns 0 (without error) when ``folder`` does not exist - first-run
    and post-deletion paths are normal.
    """
    root = os.fspath(folder)
    if not os.path.isdir(root):
        return 0

    removed = 0
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.name.endswith(".tmp"):
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            os.remove(entry.path)
            removed += 1
    return removed
