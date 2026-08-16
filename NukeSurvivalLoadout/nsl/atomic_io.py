"""Atomic filesystem primitives for NSL.

Every write goes to a temp file and is then renamed over the target. The
parent folder is created first. OSError propagates, so callers wrap it.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Union

__all__ = ["write_atomic", "ensure_parent_dir", "sweep_orphan_tmp"]

PathLike = Union[str, "os.PathLike[str]"]

# Windows replace-retry. The four sleeps add up to 0.75s
# (0.05 + 0.1 + 0.2 + 0.4) before the last attempt propagates.
_REPLACE_RETRIES = 4
_REPLACE_INITIAL_DELAY = 0.05


def ensure_parent_dir(path: PathLike) -> None:
    """Create the parent directory of ``path`` if missing.

    Does nothing when ``path`` has no parent component.
    """
    parent = os.path.dirname(os.fspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _replace_with_retry(tmp: str, target: str) -> None:
    """``os.replace`` with a bounded ``PermissionError`` retry on Windows.

    On Windows an antivirus scanner or sync client can briefly lock the
    target, so a replace that always works on POSIX can raise. The retry
    rides that out and the last failure still propagates.
    """
    if os.name != "nt":
        os.replace(tmp, target)
        return
    delay = _REPLACE_INITIAL_DELAY
    for _ in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            time.sleep(delay)
            delay *= 2
    os.replace(tmp, target)


def _fsync_parent_dir(target: str) -> None:
    """Best-effort fsync of ``target``'s parent directory (POSIX only).

    After the rename, the new directory entry only survives a power loss
    once the parent directory is fsync'd too. Windows has no equivalent
    and some network mounts refuse it, so every failure here is ignored.
    """
    if os.name != "posix":
        return
    parent = os.path.dirname(target)
    if not parent:
        parent = "."
    try:
        dir_fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except (OSError, AttributeError):
        pass
    finally:
        os.close(dir_fd)


def write_atomic(path: PathLike, content: Union[str, bytes]) -> None:
    """Write ``content`` to ``path`` via write-to-temp-then-rename.

    The temp file is a unique sibling made by ``tempfile.mkstemp``, so two
    Nuke sessions writing the same target never share a temp path and
    cannot promote or delete each other's half-written bytes.

    A failed write removes its own temp and leaves the target untouched.
    ``OSError`` propagates. A temp orphaned by a failed rename is
    reclaimed later by ``sweep_orphan_tmp``.
    """
    target = os.fspath(path)
    ensure_parent_dir(target)

    if isinstance(content, bytes):
        mode = "wb"
        payload: Union[str, bytes] = content
        open_kwargs: dict = {}
    else:
        mode = "w"
        payload = content
        # Pinned to UTF-8 and LF so the bytes never depend on the host
        # locale. A LANG=C farm session would otherwise default to ASCII.
        open_kwargs = {"encoding": "utf-8", "newline": "\n"}

    # The ``.tmp`` suffix is what ``sweep_orphan_tmp`` matches. The
    # ``<basename>.`` prefix keeps an orphan next to its own target.
    parent = os.path.dirname(target)
    fd, tmp = tempfile.mkstemp(
        dir=parent if parent else ".",
        prefix=os.path.basename(target) + ".",
        suffix=".tmp",
    )

    try:
        # ``os.fdopen`` takes ownership of ``fd``. Do not close ``fd`` as
        # well, the ``with`` block already closes it exactly once.
        with os.fdopen(fd, mode, **open_kwargs) as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    _replace_with_retry(tmp, target)

    _fsync_parent_dir(target)


def sweep_orphan_tmp(folder: PathLike) -> int:
    """Delete direct ``.tmp`` siblings inside ``folder``.

    Not recursive. Symlinks and subfolders are left alone. Returns the
    number of files deleted, or 0 when ``folder`` does not exist.
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
