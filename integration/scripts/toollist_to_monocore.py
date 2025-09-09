#!/usr/bin/env python3
import os
import sys
import shlex
import shutil
import re
from pathlib import Path

USAGE = "Usage: toollist_to_monocore.py <filelist> <export_dir> <core_name> <core_version>"

if len(sys.argv) != 5:
    print(USAGE, file=sys.stderr); sys.exit(2)

TOPLEVEL = os.environ.get("TOPLEVEL", "ibex_wrapper")
CORE_FILE_BASENAME = os.environ.get("CORE_FILE_BASENAME")  # optional override for on-disk .core name

root_list = Path(sys.argv[1]).resolve()
export_dir = Path(sys.argv[2]).resolve()
core_name  = sys.argv[3]
core_ver   = sys.argv[4]

rtl_dir = export_dir / "rtl"
inc_dir = export_dir / "include"
export_dir.mkdir(parents=True, exist_ok=True)
rtl_dir.mkdir(parents=True, exist_ok=True)
# include/ is created lazily only if/when we copy a header

visited_lists = set()
# files_ordered: list of dicts with keys:
#   rel (str)        : path relative to export_dir
#   is_header (bool) : whether to mark as include file
#   dst (Path)       : absolute path in export tree
#   orig (Path)      : absolute source path from toollist (if known)
files_ordered = []
seen_rel = set()
incdirs = []  # absolute include dirs, in order

HDL_EXTS = (".sv", ".svh", ".vh", ".v")
HEADER_EXTS = (".svh", ".vh")
# Basic regex to catch: `include "foo/bar.sv"`  (ignores comments/block noise heuristically)
RE_INCLUDE = re.compile(r'^\s*`include\s+"([^"]+)"')

def _resolve(base: Path, p: Path) -> Path:
    return p if p.is_absolute() else (base / p).resolve()

def add_incdir(base: Path, incspec: str):
    if incspec.startswith("+incdir+"):
        incspec = incspec[len("+incdir+"):]
    p = _resolve(base, Path(incspec)).resolve()
    s = str(p)
    if s not in incdirs:
        incdirs.append(s)

def is_under_incdir(p: Path) -> bool:
    ap = p.resolve()
    for d in incdirs:
        try:
            ap.relative_to(Path(d))
            return True
        except ValueError:
            continue
    return False

def ensure_dir(d: Path):
    if not d.exists():
        d.mkdir(parents=True, exist_ok=True)

def _add_entry(dst: Path, is_header: bool, orig: Path | None):
    """Register a copied file into files_ordered iff not already present."""
    rel = os.path.relpath(dst, export_dir)
    if rel in seen_rel:
        return None
    seen_rel.add(rel)
    ent = {"rel": rel, "is_header": is_header, "dst": dst.resolve(), "orig": orig}
    files_ordered.append(ent)
    return ent

def add_src(base: Path, token: str):
    """Copy a source referenced in the tool filelist."""
    orig = _resolve(base, Path(token))
    if not orig.exists():
        return
    if orig.suffix.lower() not in HDL_EXTS:
        return

    # Heuristic header classification for toollist-provided entries:
    is_header = is_under_incdir(orig) or (orig.suffix.lower() in HEADER_EXTS)

    # Destination: headers -> include/, others -> rtl/
    dst_root = inc_dir if is_header else rtl_dir
    if is_header:
        ensure_dir(inc_dir)  # lazily create include/ only if needed

    # Preserve basename for normal sources; avoid collisions
    dst = dst_root / orig.name
    if dst.exists():
        i = 1
        while True:
            cand = dst_root / f"{i}_{orig.name}"
            if not cand.exists():
                dst = cand
                break
            i += 1

    shutil.copy2(orig, dst)
    _add_entry(dst, is_header, orig)

def parse_list(list_path: Path):
    lp = list_path.resolve()
    if not lp.exists() or lp in visited_lists:
        return
    visited_lists.add(lp)
    base = lp.parent

    for raw in lp.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        toks = shlex.split(line)
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.startswith("+incdir+"):
                add_incdir(base, t)
            elif t in ("-f", "-F"):
                i += 1
                if i < len(toks):
                    parse_list(_resolve(base, Path(toks[i])))
            elif t.startswith("-f") and len(t) > 2:
                parse_list(_resolve(base, Path(t[2:])))
            elif t.startswith(("+define+", "+libext+", "+librescan", "+notimingchecks")):
                pass
            elif t in ("-v", "-sv", "-y", "-Y", "-timescale"):
                if i + 1 < len(toks):
                    if t in ("-v", "-sv"):
                        add_src(base, toks[i+1])
                    i += 1
            else:
                if t.lower().endswith(HDL_EXTS):
                    add_src(base, t)
            i += 1

def resolve_include(include_name: str, includer_ent: dict) -> Path | None:
    """
    Resolve an include name against:
      1) including file's ORIGINAL directory
      2) each +incdir+ directory from the tool filelist
    Return the first existing absolute path or None.
    """
    # if include has directories, preserve them
    name_path = Path(include_name)
    # prefer original source dir of includer if known
    search_roots = []
    if includer_ent.get("orig") is not None:
        search_roots.append(includer_ent["orig"].parent)
    # then toollist-given +incdir+ dirs
    for d in incdirs:
        search_roots.append(Path(d))
    # now try to resolve
    for root in search_roots:
        cand = (root / name_path).resolve()
        if cand.exists():
            return cand
    return None

def copy_include(include_name: str, src_abs: Path):
    """
    Copy an include file to export include/ tree, preserving subpath if include_name has directories.
    Mark as header and enqueue for further scanning.
    """
    ensure_dir(inc_dir)
    # Preserve the include's path (e.g. "prim/prim_assert.sv" -> include/prim/prim_assert.sv)
    dst = inc_dir / include_name
    dst_parent = dst.parent
    dst_parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(src_abs, dst)
    # Register (or get) entry
    ent = _add_entry(dst, True, src_abs)
    return ent

# 1) Parse the top-level tool filelist (recurses via -f chains) and copy those files
parse_list(root_list)

# 2) Scan copied files for `include "..."`, resolve and recursively copy missing includes
queue = list(files_ordered)  # shallow copy; we'll append as we discover more
seen_includes = set()        # keys are normalized include_name strings relative to inc_dir layout

while queue:
    ent = queue.pop(0)
    # Only parse HDL text files
    try:
        with open(ent["dst"], "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                m = RE_INCLUDE.match(raw)
                if not m:
                    continue
                inc_name = m.group(1)
                # collapse any leading ./ in include_name
                while inc_name.startswith("./"):
                    inc_name = inc_name[2:]
                key = inc_name  # normalized include path as it will be placed under include/
                if key in seen_includes:
                    continue
                # resolve in original space
                src_abs = resolve_include(inc_name, ent)
                if src_abs is None:
                    # couldn't resolve; warn and continue
                    print(f"WARNING: Unable to resolve include '{inc_name}' referenced by {ent['rel']}", file=sys.stderr)
                    continue
                # copy to include tree and enqueue for nested scanning
                new_ent = copy_include(inc_name, src_abs)
                seen_includes.add(key)
                if new_ent is not None:
                    queue.append(new_ent)
    except Exception:
        # ignore unreadable/binary
        continue

# 3) Emit CAPI2 core (no include_dirs; use is_include_file instead)
file_base = CORE_FILE_BASENAME if CORE_FILE_BASENAME else core_name.split(":")[-1]
core_path = export_dir / f"{file_base}.core"

with core_path.open("w") as core:
    core.write("CAPI=2:\n\n")
    core.write(f'name: "{core_name}:{core_ver}"\n')
    core.write('description: "Self-contained Ibex snapshot (from tool filelist; includes resolved recursively; no generators)"\n\n')
    core.write("filesets:\n")
    core.write("  files_all:\n")
    core.write("    files:\n")

    # Collapse include headers into one dummy per unique root directory under export tree
    # Example: headers under "include/..." -> emit "include/include" once.
    header_roots = []
    for ent in files_ordered:
        if not ent["is_header"]:
            continue
        p = Path(ent["rel"])
        parts = p.parts
        if not parts:
            continue
        root = parts[0]
        if root and root not in header_roots:
            header_roots.append(root)

    # Intentionally do not create on-disk dummy "include" files.
    # We still emit entries for <root>/include in the core filelist below.
    # Emit include-dir entries FIRST (at top of the file list), one per unique root
    # Keep a deterministic order
    for root in sorted(header_roots):
        core.write(f"      - {root}/include: {{file_type: systemVerilogSource, is_include_file: true}}\n")

    # Then emit non-header sources
    for ent in files_ordered:
        if ent["is_header"]:
            continue
        rel = ent["rel"]
        core.write(f"      - {rel}: {{file_type: systemVerilogSource}}\n")

    core.write("\n")
    core.write("targets:\n")
    core.write("  default:\n")
    core.write("    filesets: [files_all]\n")
    core.write(f"    toplevel: {TOPLEVEL}\n")

# Remove include/ if unused
try:
    if inc_dir.exists() and not any(inc_dir.iterdir()):
        inc_dir.rmdir()
except Exception:
    pass

print(f"Wrote monocore to {core_path}")

