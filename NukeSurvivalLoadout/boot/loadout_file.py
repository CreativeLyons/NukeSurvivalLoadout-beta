"""User loadout init.py read/write/render.

Responsibilities:
  - Parse a user loadout file into a ``LoadoutModel`` (AST-driven, so a
    user's hand-edits don't have to be byte-perfect to round-trip).
  - Render a ``LoadoutModel`` back to canonical text: section ordering
    is fixed; the managed block AND the prologue (imports + folder vars +
    helper, wrapped in ``# === BEGIN/END NSL PROLOGUE ===`` markers) are
    authored entirely by NSL and regenerated on every write.
  - Preserve everything OUTSIDE the NSL-owned regions verbatim:
    ``user_prologue`` (hand-authored text above the prologue markers) and
    ``user_suffix`` (after the END-managed marker). Legacy files written
    before the prologue markers existed carry their whole head verbatim in
    ``user_prefix`` instead, and gain the prologue markers on next save.
  - Atomic write via ``NukeSurvivalLoadout.atomic_io.write_atomic``.

Non-goals:
  - Validating that ``folder`` variables resolve to real paths
    (loadout-time concern, not file-shape concern).
  - Catching SyntaxError when parsing: that's the dispatcher's job.
    ``read_loadout`` raises if the file isn't valid Python.
  - Managing aliased calls. Only literal ``nsl_pluginAddPath(...)`` calls
    are pulled into ``plugins``; anything else that lives inside the managed
    block is intentionally dropped on re-render (the markers say "NSL owns
    this region"). Aliased usage belongs *outside* the markers in
    user_prefix/user_suffix.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field, replace
from typing import Optional

from NukeSurvivalLoadout import atomic_io
from NukeSurvivalLoadout.constants import GLOBAL_PLUGINS_VAR_NAME

__all__ = [
    "BEGIN_MARKER",
    "END_MARKER",
    "BEGIN_PROLOGUE_MARKER",
    "END_PROLOGUE_MARKER",
    "FolderDecl",
    "PluginEntry",
    "LoadoutModel",
    "read_loadout",
    "write_loadout",
    "render",
    "sync_folders",
    "sync_folders_to_loadouts",
]


BEGIN_MARKER = "# === BEGIN NSL MANAGED PLUGINS ==="
END_MARKER = "# === END NSL MANAGED PLUGINS ==="

# Prologue markers wrap the NSL-authored file head (imports + folder vars +
# helper). Everything ABOVE the BEGIN-prologue marker is hand-authored
# user code that NSL must preserve verbatim across every rebuild; the body
# BETWEEN the prologue markers is owned by NSL and regenerated from the
# model's ``folders`` on each render. Files written before these markers
# existed have no prologue pair: their whole head is treated as one
# verbatim ``user_prefix`` blob (see ``_split_on_markers``), and the
# prologue markers get added on their next save.
BEGIN_PROLOGUE_MARKER = "# === BEGIN NSL PROLOGUE ==="
END_PROLOGUE_MARKER = "# === END NSL PROLOGUE ==="

# Pre-rename marker spelling. Accepted on read so loadout files written
# before the rename still parse; they pick up the new markers on their
# next save.
_LEGACY_BEGIN_MARKER = "# === BEGIN NSL MANAGED ==="
_LEGACY_END_MARKER = "# === END NSL MANAGED ==="

HELPER_NAME = "nsl_pluginAddPath"
LOAD_FOLDER_NAME = "nsl_load_folder"


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class PluginEntry:
    """One ``nsl_pluginAddPath(folder=<var>, name=<str>, ...)`` call.

    ``trailing_comment`` is the user-authored ``# foo`` text (including the
    leading ``#`` and any whitespace between code and comment, e.g.
    ``"  # broken on 16.0"``). Empty string when no trailing comment.
    """

    folder_var: str
    name: str
    gui: bool = False
    disabled: bool = False
    trailing_comment: str = ""


@dataclass
class FolderDecl:
    """One ``plugins_X = '<abs path>'`` string assignment at file top.

    Written with ``repr`` quoting; read back via AST, so either quote
    style (including pre-fix double-quoted files) round-trips.
    """

    var: str
    path: str


@dataclass
class LoadoutModel:
    docstring: str = ""
    folders: list[FolderDecl] = field(default_factory=list)
    plugins: list[PluginEntry] = field(default_factory=list)
    user_prefix: str = ""
    user_suffix: str = ""
    # Hand-authored text that sits ABOVE the NSL prologue markers - custom
    # imports, helpers, comments the user placed before the generated head.
    # Preserved verbatim across every Save / folder-sync rebuild. Empty for
    # legacy files (no prologue markers); their head rides in ``user_prefix``.
    user_prologue: str = ""


# ---------------------------------------------------------------------------
# Canonical text fragments
# ---------------------------------------------------------------------------


_IMPORTS_BLOCK = "import os\nimport nuke\n"

_FOLDERS_HEADER = "# ─── Plugin source folders ────────────────"

_HELPER_HEADER = "# ─── Plugin loading functions ─────────"

# Session recording is the one piece that is NOT inlined: it only feeds
# the NSL panel's Loaded counter, so a loadout running without NSL (file
# copied to another machine, panic mode) loses nothing by skipping it.
# The loaders below stay inline so the file is self-contained at boot.
_HELPER_DEF = (
    "import sys\n"
    "\n"
    "_NSL_HANDLED = set()\n"
    "\n"
    "try:\n"
    "    from NukeSurvivalLoadout.boot.session_record import record_loaded as _nsl_record\n"
    "except Exception:\n"
    "    def _nsl_record(name, path, gui=False):\n"
    "        pass\n"
    "\n"
    "def _nsl_log(text):\n"
    "    # Self-contained mirror of log._write_stdout: a copied loadout runs\n"
    "    # without the NSL package importable, so it can't reuse the in-process\n"
    "    # logger. An ASCII / LANG=C stdout would otherwise raise\n"
    "    # UnicodeEncodeError on a non-ASCII plugin name BEFORE pluginAddPath,\n"
    "    # silently skipping the plugin. Logging must never abort a boot pass,\n"
    "    # so every failure here is swallowed.\n"
    "    try:\n"
    "        try:\n"
    "            sys.stdout.write(text)\n"
    "        except UnicodeEncodeError:\n"
    '            enc = getattr(sys.stdout, "encoding", None) or "ascii"\n'
    '            degraded = text.encode(enc, "replace").decode(enc, "replace")\n'
    "            sys.stdout.write(degraded)\n"
    "        sys.stdout.flush()\n"
    "    except Exception:\n"
    "        pass\n"
    "\n"
    "def nsl_pluginAddPath(folder, name, gui=False, disabled=False):\n"
    "    _NSL_HANDLED.add((folder, name))\n"
    "    if disabled:\n"
    "        return\n"
    "    if gui and not nuke.GUI:\n"
    "        return\n"
    "    path = os.path.join(folder, name)\n"
    "    if os.path.isdir(path):\n"
    '        _nsl_log("NSL Loading... " + name + "\\n")\n'
    "        nuke.pluginAddPath(path)\n"
    "        _nsl_record(name, path, gui)\n"
    "\n"
    "def nsl_load_folder(folder):\n"
    "    try:\n"
    "        names = sorted(os.listdir(folder))\n"
    "    except OSError:\n"
    "        return\n"
    "    for name in names:\n"
    "        if (folder, name) in _NSL_HANDLED:\n"
    "            continue\n"
    '        if name.startswith(("_", ".")):\n'
    "            continue\n"
    "        nsl_pluginAddPath(folder, name)\n"
)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_loadout(path: str) -> LoadoutModel:
    """Parse a loadout file from disk into a ``LoadoutModel``."""
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    return _parse_source(source)


def _parse_source(source: str) -> LoadoutModel:
    """Parse the loadout source string. Public surface is ``read_loadout``."""
    user_prefix, managed_body, user_suffix = _split_on_markers(source)

    # Within the pre-MANAGED region, peel the NSL prologue (imports + folder
    # vars + helper) away from any hand-authored text that sits above it.
    # ``user_prologue`` is that hand-authored text, preserved verbatim on
    # render; ``prologue_body`` is the regenerable NSL head, parsed below for
    # docstring + folder decls. Legacy files (no prologue markers) keep the
    # whole region in ``user_prefix`` and leave ``user_prologue`` empty.
    user_prologue, prologue_body, has_prologue_markers = _split_prologue(user_prefix)

    # Outside the managed markers: parse with AST to pull docstring + folder
    # decls. ``managed_body`` is parsed only for plugin entries. The folder
    # decls and docstring come from the prologue body (or the whole prefix on
    # legacy files) - never from ``user_prologue``, so a user-authored
    # ``plugins_X = ...`` assignment above the prologue is left verbatim and
    # not mistaken for a managed folder decl.
    decl_source = prologue_body if has_prologue_markers else user_prefix
    prefix_tree = ast.parse(decl_source) if decl_source.strip() else ast.Module(body=[], type_ignores=[])

    docstring = ast.get_docstring(prefix_tree) or ""

    folders: list[FolderDecl] = []
    for node in prefix_tree.body:
        decl = _try_folder_decl(node)
        if decl is not None:
            folders.append(decl)

    plugins = _parse_managed_block(managed_body)

    # Guard B (parse-time tolerance): drop any managed call whose folder_var
    # isn't declared in this file. A dangling reference - e.g. left by a hand
    # edit that deleted a folder var but not its calls - would be a boot-time
    # NameError, and the dispatcher's compile() pre-check only catches
    # SyntaxError, not NameError. Filtering here means the panel never
    # round-trips such a reference back out, so write_loadout(read_loadout())
    # can't re-emit a file that crashes Nuke.
    declared = {f.var for f in folders}
    plugins = [entry for entry in plugins if entry.folder_var in declared]

    # When prologue markers ARE present the head is fully structured
    # (user_prologue + regenerable folders/docstring), so ``user_prefix`` is
    # cleared - render rebuilds the prologue from ``folders``. When they are
    # ABSENT (legacy file) the whole head rides verbatim in ``user_prefix``,
    # exactly as before, so nothing is lost on a file NSL has not re-saved yet.
    parsed_user_prefix = "" if has_prologue_markers else user_prefix

    return LoadoutModel(
        docstring=docstring,
        folders=folders,
        plugins=plugins,
        user_prefix=parsed_user_prefix,
        user_suffix=user_suffix,
        user_prologue=user_prologue,
    )


def _split_on_markers(source: str) -> tuple[str, str, str]:
    """Split source into (prefix, managed_body, suffix) on marker lines.

    Both markers must appear or both must be absent. A file with only one
    marker is treated as having no managed section; everything becomes
    ``user_prefix`` (defensive - we don't want to silently lose user code).
    """
    lines = source.splitlines(keepends=True)

    begin_idx = _find_marker_line(lines, BEGIN_MARKER, _LEGACY_BEGIN_MARKER)
    end_idx = _find_marker_line(lines, END_MARKER, _LEGACY_END_MARKER)

    if begin_idx is None or end_idx is None or end_idx <= begin_idx:
        return source, "", ""

    prefix = "".join(lines[:begin_idx])
    managed = "".join(lines[begin_idx + 1 : end_idx])
    suffix = "".join(lines[end_idx + 1 :])
    return prefix, managed, suffix


def _find_marker_line(lines: list[str], *markers: str) -> Optional[int]:
    """Return index of the first line whose stripped content is one of ``markers``."""
    for idx, line in enumerate(lines):
        if line.rstrip("\r\n").strip() in markers:
            return idx
    return None


def _split_prologue(prefix: str) -> tuple[str, str, bool]:
    """Split the pre-MANAGED region into (user_prologue, prologue_body, found).

    ``prefix`` is everything above the ``BEGIN_MARKER``. When it carries the
    NSL prologue markers, return:

      * ``user_prologue`` - hand-authored text ABOVE the BEGIN-prologue marker,
        kept verbatim on render,
      * ``prologue_body`` - the NSL-owned head BETWEEN the prologue markers
        (parsed for docstring + folder decls, regenerated on render),
      * ``True``.

    Any text BELOW the END-prologue marker (between it and BEGIN_MARKER) is
    appended onto ``prologue_body`` so its folder decls still parse; it is
    rare (NSL never writes there) and regenerating the prologue subsumes it.

    When the prologue markers are absent (legacy file, or a file the user
    stripped them from) return ``("", "", False)`` and the caller treats the
    whole ``prefix`` as one verbatim blob - no behaviour change for old files.
    """
    lines = prefix.splitlines(keepends=True)
    begin_idx = _find_marker_line(lines, BEGIN_PROLOGUE_MARKER)
    end_idx = _find_marker_line(lines, END_PROLOGUE_MARKER)

    if begin_idx is None or end_idx is None or end_idx <= begin_idx:
        return "", "", False

    user_prologue = "".join(lines[:begin_idx])
    body = "".join(lines[begin_idx + 1 : end_idx])
    trailing = "".join(lines[end_idx + 1 :])
    if trailing.strip():
        body = f"{body}{trailing}"
    return user_prologue, body, True


def _try_folder_decl(node: ast.AST) -> Optional[FolderDecl]:
    """Return a FolderDecl iff ``node`` is ``<name> = "<string>"`` literal."""
    if not isinstance(node, ast.Assign):
        return None
    if len(node.targets) != 1:
        return None
    target = node.targets[0]
    if not isinstance(target, ast.Name):
        return None
    value = node.value
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return None
    return FolderDecl(var=target.id, path=value.value)


def _parse_managed_block(managed_body: str) -> list[PluginEntry]:
    """Pull ``nsl_pluginAddPath(...)`` calls out of the managed block.

    Trailing comments come from a token-level scan because ``ast`` discards
    them. Lines that aren't a literal ``nsl_pluginAddPath(...)`` call are
    silently ignored: the rewriter will rebuild the section.
    """
    if not managed_body.strip():
        return []

    try:
        tree = ast.parse(managed_body)
    except SyntaxError:
        # The whole file is parsed by read_loadout's caller already in
        # practice (Nuke runs it), so a SyntaxError here is unexpected.
        # Surface it.
        raise

    trailing = _scan_trailing_comments(managed_body)

    plugins: list[PluginEntry] = []
    for node in tree.body:
        entry = _try_plugin_entry(node, trailing)
        if entry is not None:
            plugins.append(entry)
    return plugins


def _try_plugin_entry(
    node: ast.AST, trailing: dict[int, str]
) -> Optional[PluginEntry]:
    """Return a PluginEntry iff ``node`` is a literal ``nsl_pluginAddPath`` call."""
    if not isinstance(node, ast.Expr):
        return None
    call = node.value
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if not (isinstance(func, ast.Name) and func.id == HELPER_NAME):
        return None

    folder_var: Optional[str] = None
    name: Optional[str] = None
    gui = False
    disabled = False

    # Positional: folder, name, gui, disabled (matches helper signature).
    positional = list(call.args)
    if len(positional) >= 1:
        folder_var = _expect_name(positional[0])
    if len(positional) >= 2:
        name = _expect_str(positional[1])
    if len(positional) >= 3:
        gui = _expect_bool(positional[2], default=False)
    if len(positional) >= 4:
        disabled = _expect_bool(positional[3], default=False)

    for kw in call.keywords:
        if kw.arg == "folder":
            folder_var = _expect_name(kw.value)
        elif kw.arg == "name":
            name = _expect_str(kw.value)
        elif kw.arg == "gui":
            gui = _expect_bool(kw.value, default=False)
        elif kw.arg == "disabled":
            disabled = _expect_bool(kw.value, default=False)
        # Unknown kwargs ignored: NSL only manages the known call shape.

    if folder_var is None or name is None:
        return None

    trailing_comment = trailing.get(node.lineno, "")

    return PluginEntry(
        folder_var=folder_var,
        name=name,
        gui=gui,
        disabled=disabled,
        trailing_comment=trailing_comment,
    )


def _expect_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    return None


def _expect_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _expect_bool(node: ast.AST, default: bool) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return default


def _scan_trailing_comments(source: str) -> dict[int, str]:
    """Map line-number → trailing comment text (incl. leading whitespace + ``#``).

    Only comments that sit AFTER code on the same line are captured. Lines
    that are wholly comment (column 0 or pure-whitespace then ``#``) are
    excluded - those are NSL-written folder-path headers, not user trailing.
    """
    trailing: dict[int, str] = {}
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenizeError, IndentationError):
        return trailing

    # Track which lines have a non-comment, non-whitespace token before any
    # comment on that line. Walk in order; when we see a COMMENT token,
    # check whether any code token preceded it on the same line.
    code_seen_on_line: set[int] = set()
    for tok in tokens:
        ttype = tok.type
        srow = tok.start[0]
        if ttype in (
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.COMMENT,
            tokenize.ENCODING,
            tokenize.ENDMARKER,
        ):
            continue
        code_seen_on_line.add(srow)

    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        srow = tok.start[0]
        if srow not in code_seen_on_line:
            continue
        # Preserve the whitespace gap between code and the ``#``.
        # tok.line includes the full source line; slice from end-of-code.
        # Simpler approach: capture text from column where the comment
        # starts, then prepend the spaces that sat between code and ``#``.
        source_line = tok.line
        comment_col = tok.start[1]
        # walk left from comment_col to find non-space code character
        left = comment_col
        while left > 0 and source_line[left - 1] in (" ", "\t"):
            left -= 1
        gap = source_line[left:comment_col]
        comment_text = tok.string  # includes leading '#'
        trailing[srow] = f"{gap}{comment_text}"
    return trailing


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(model: LoadoutModel) -> str:
    """Render the model to canonical text.

    The file head is built in this order of precedence:

    * ``user_prefix`` non-empty (legacy file NSL has not re-saved): emit it
      verbatim, exactly as before. This branch round-trips an old file
      untouched and never appears on a model NSL itself just built.
    * otherwise: emit any hand-authored ``user_prologue`` verbatim, then a
      freshly synthesised NSL prologue (imports + folder vars + helper)
      wrapped in the prologue markers. Regenerating from ``folders`` keeps
      the ``plugins_X`` vars in lockstep with the managed block (no dangling
      reference) while the user's own prologue text survives every rebuild.
    """
    if model.user_prefix:
        prefix = model.user_prefix
        if not prefix.endswith("\n"):
            prefix = f"{prefix}\n"
    else:
        prefix = _render_prologue(model)

    managed = _render_managed_block(model)

    suffix = model.user_suffix

    return f"{prefix}{BEGIN_MARKER}\n{managed}{END_MARKER}\n{suffix}"


def _render_prologue(model: LoadoutModel) -> str:
    """Hand-authored ``user_prologue`` (verbatim) + the marked NSL head.

    The NSL head (imports + folder vars + helper) is wrapped in the prologue
    markers so a later parse can peel it back off and the user's own text
    above it survives. ``user_prologue`` is emitted first, verbatim.
    """
    parts: list[str] = []
    prologue = model.user_prologue
    if prologue:
        if not prologue.endswith("\n"):
            prologue = f"{prologue}\n"
        parts.append(prologue)
    parts.append(f"{BEGIN_PROLOGUE_MARKER}\n")
    parts.append(_render_canonical_prefix(model))
    parts.append(f"{END_PROLOGUE_MARKER}\n")
    return "".join(parts)


def _render_canonical_prefix(model: LoadoutModel) -> str:
    """Build the file head from scratch (imports → folders → helper).

    No generated docstring: the file should open straight on the imports.
    A user-authored docstring (parsed from a hand-edited file) is still
    re-emitted so it survives a canonical rebuild.
    """
    parts: list[str] = []
    if model.docstring:
        parts.append(f'"""{model.docstring}\n"""\n\n')
    parts.append(_IMPORTS_BLOCK)
    parts.append("\n\n")

    if model.folders:
        parts.append(f"{_FOLDERS_HEADER}\n")
        for folder in model.folders:
            # repr, not a hand-rolled quoted literal: Windows paths carry
            # backslashes ("C:\Users\..." is a SyntaxError as a plain
            # double-quoted literal; "C:\temp" silently becomes a tab).
            parts.append(f"{folder.var} = {folder.path!r}\n")
        parts.append("\n\n")

    parts.append(f"{_HELPER_HEADER}\n")
    parts.append(_HELPER_DEF)
    parts.append("\n\n")
    return "".join(parts)


def _render_managed_block(model: LoadoutModel) -> str:
    """Render the managed body: per-folder header, exception calls, scan.

    Sparse / exceptions-only format. For each declared folder, in
    declaration order, emit:

      1. a ``# Load plugins from <var>:`` header (only when the folder
         has explicit calls),
      2. the explicit ``nsl_pluginAddPath`` lines for that folder - only
         *exceptions* (disabled or gui-only plugins), in model order,
      3. one ``nsl_load_folder(<var>)`` that loads everything else in the
         folder (default on) at boot.

    Every declared folder gets a scan call even when it has zero
    exception lines, so a folder of all-default plugins still loads.
    """
    if not model.folders and not model.plugins:
        return ""

    folders_by_var = {folder.var: folder.path for folder in model.folders}

    # Exception entries grouped by folder_var (preserve model order).
    #
    # Guard A (write-time validation): only entries whose folder_var is
    # DECLARED in this file are emitted. An entry referencing an undeclared
    # var is dropped rather than written: emitting its call (or a
    # ``nsl_load_folder(undeclared)``) would be a boot-time NameError. Folder
    # removal prunes such entries up-front via ``sync_folders``; this is the
    # belt-and-suspenders that also neutralises a stray hand edit.
    entries_by_var: dict[str, list[PluginEntry]] = {}
    for entry in model.plugins:
        if entry.folder_var in folders_by_var:
            entries_by_var.setdefault(entry.folder_var, []).append(entry)

    ordered_vars: list[str] = [folder.var for folder in model.folders]

    blocks: list[str] = []
    for var in ordered_vars:
        call_lines = "".join(
            _render_plugin_call(entry) for entry in entries_by_var.get(var, [])
        )
        # Group header only when there are explicit calls to group; the
        # folder's absolute path already lives in its decl at file top.
        header = f"# Load plugins from {var}:\n" if call_lines else ""
        # The Global plugins folder gets NO scan call: the Global chain
        # head owns baseline loading of that folder and skips exactly the
        # names this file mentions. A scan line here would load the whole
        # Global folder from this file too - double-adding every name the
        # file doesn't mention.
        if var == GLOBAL_PLUGINS_VAR_NAME:
            if call_lines:
                blocks.append(f"\n{header}{call_lines}")
            continue
        # The scan call gets its own commented line so the file reads clearly:
        # explicit exceptions above, "everything else here, on" below. A blank
        # line separates it from the exception calls when there are any.
        scan_comment = f"# Auto-load every other plugin in {var}.\n"
        scan_line = f"{LOAD_FOLDER_NAME}({var})\n"
        sep = "\n" if call_lines else ""
        blocks.append(f"\n{header}{call_lines}{sep}{scan_comment}{scan_line}")
    # Trailing newline before END_MARKER for readability.
    return f"{''.join(blocks)}\n"


def _render_plugin_call(entry: PluginEntry) -> str:
    """Render one ``nsl_pluginAddPath(...)`` call with optional kwargs + comment.

    ``name`` is quoted with ``repr``: plugin names come from on-disk folder
    basenames, which can legally contain quotes on POSIX/macOS.
    """
    args = [f"folder={entry.folder_var}", f"name={entry.name!r}"]
    if entry.gui:
        args.append("gui=True")
    if entry.disabled:
        args.append("disabled=True")
    joined = ", ".join(args)
    line = f"{HELPER_NAME}({joined})"
    if entry.trailing_comment:
        line = f"{line}{entry.trailing_comment}"
    return f"{line}\n"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def write_loadout(path: str, model: LoadoutModel) -> None:
    """Render ``model`` and atomically replace ``path``."""
    atomic_io.write_atomic(path, render(model))


# ---------------------------------------------------------------------------
# Folder sync (dispatcher is the authority; loadouts hold a synced copy)
# ---------------------------------------------------------------------------


def sync_folders(
    model: LoadoutModel, canonical: list[FolderDecl]
) -> LoadoutModel:
    """Return a copy of ``model`` whose folder decls match ``canonical``.

    Folder **identity is the path**, not the var name. Each plugin entry's
    ``folder_var`` is remapped to the canonical var that holds the SAME
    path; entries whose path is no longer in ``canonical`` are dropped
    (prune-on-removal). This single primitive is correct for all three
    folder operations:

      * **add** - a new canonical var has no entries yet; ``nsl_load_folder``
        picks up its plugins at boot.
      * **remove** - entries for the gone path are pruned, so no managed
        call references an undeclared var (no boot-time NameError).
      * **reorder** - index-based var names (``plugins_A`` …) may shift
        which path a var holds; remapping by path keeps every entry
        pointing at its original folder.

    The dispatcher owns ``canonical``; this is how that authority fans out
    into each loadout file so the file stays self-contained at Nuke boot.

    The ``global_plugins`` decl + its entries sit OUTSIDE this authority:
    the dispatcher only carries user-added folders, so the Global folder
    var (written by Save for Global-plugin overrides) is preserved
    verbatim rather than pruned as "removed from canonical".
    """
    old_var_to_path = {f.var: f.path for f in model.folders}
    path_to_new_var = {f.path: f.var for f in canonical}

    global_decls = [f for f in model.folders if f.var == GLOBAL_PLUGINS_VAR_NAME]

    new_plugins: list[PluginEntry] = []
    for entry in model.plugins:
        if entry.folder_var == GLOBAL_PLUGINS_VAR_NAME:
            if global_decls:
                new_plugins.append(entry)
            continue
        old_path = old_var_to_path.get(entry.folder_var)
        if old_path is None:
            continue  # entry referenced an undeclared var - drop (Guard)
        new_var = path_to_new_var.get(old_path)
        if new_var is None:
            continue  # folder removed from canonical - prune this entry
        new_plugins.append(replace(entry, folder_var=new_var))

    # Reset user_prefix so render() rebuilds the NSL prologue from the NEW
    # folder decls. Without this, render() emits the stale user_prefix
    # verbatim (folder decls live there on a legacy round-trip), leaving the
    # managed block referencing vars the prefix no longer declares - a
    # dangling reference. Mirrors folder_ops._with_folders, which resets
    # user_prefix for the same reason; the docstring is preserved via
    # model.docstring.
    #
    # ``user_prologue`` is carried forward verbatim: it is the hand-authored
    # text that lives ABOVE the NSL prologue markers (custom imports/helpers),
    # NOT part of the regenerated head, so a folder add/remove must not drop
    # it (Issue 2 - the old code zeroed only user_prefix and silently lost
    # this text because legacy parses folded both into user_prefix).
    return replace(
        model,
        folders=[*canonical, *global_decls],
        plugins=new_plugins,
        user_prefix="",
        user_prologue=model.user_prologue,
    )


def sync_folders_to_loadouts(loadouts_dir, canonical: list[FolderDecl]) -> list[str]:
    """Rewrite every loadout ``init.py`` so its folder decls match ``canonical``.

    Walks ``<loadouts_dir>/*/init.py`` (each subdir is one loadout), applies
    :func:`sync_folders`, and writes the result back. The dispatcher itself
    (``<loadouts_dir>/init.py``) is never a subdir entry, so it's skipped
    naturally. Unreadable / malformed loadout files are skipped rather than
    raising - one bad loadout must not block syncing the others.

    Returns the list of loadout stems that were synced.
    """
    import os

    loadouts_dir = str(loadouts_dir)
    synced: list[str] = []
    try:
        names = sorted(os.listdir(loadouts_dir))
    except OSError:
        return synced

    for name in names:
        init_path = os.path.join(loadouts_dir, name, "init.py")
        if not os.path.isfile(init_path):
            continue
        try:
            model = read_loadout(init_path)
        except (OSError, SyntaxError):
            continue
        write_loadout(init_path, sync_folders(model, canonical))
        synced.append(name)
    return synced
