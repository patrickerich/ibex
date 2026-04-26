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
WRAPPER_PARAM_OVERRIDES = os.environ.get("WRAPPER_PARAM_OVERRIDES", "")

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
FORCED_INCLUDE_SOURCES = (
    "prim_secded",
)

KNOWN_OPTIONAL_INCLUDES = {
    "formal_tb_frag.svh",
}

CFG_PARAM_NAMES = {
    "RV32E", "RV32M", "RV32B", "RV32ZC", "RegFile", "BranchTargetALU", "WritebackStage",
    "ICache", "ICacheECC", "ICacheScramble", "BranchPredictor", "DbgTriggerEn",
    "SecureIbex", "PMPEnable", "PMPGranularity", "PMPNumRegions", "MHPMCounterNum",
    "MHPMCounterWidth"
}

CFG_BOOL_NAMES = {
    "RV32E", "BranchTargetALU", "WritebackStage", "ICache", "ICacheECC", "ICacheScramble",
    "BranchPredictor", "DbgTriggerEn", "SecureIbex", "PMPEnable"
}

def parse_wrapper_param_overrides(raw: str):
    """Parse '--Name=Value' options into a dict of known wrapper parameter values."""
    parsed = {}
    if not raw.strip():
        return parsed

    try:
        toks = shlex.split(raw)
    except ValueError:
        return parsed

    for tok in toks:
        if not tok.startswith("--") or "=" not in tok:
            continue
        name, val = tok[2:].split("=", 1)
        if name in CFG_PARAM_NAMES:
            parsed[name] = val
    return parsed

def _format_param_default(name: str, value: str) -> str:
    if name in CFG_BOOL_NAMES:
        return "1'b1" if value in ("1", "true", "True") else "1'b0"
    return value

def update_wrapper_defaults(wrapper_path: Path, param_overrides) -> None:
    """Rewrite ibex_wrapper parameter defaults to match selected config values."""
    if not wrapper_path.exists() or not param_overrides:
        return

    lines = wrapper_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    out_lines = []

    for line in lines:
        replaced = False
        for name, value in param_overrides.items():
            pattern = rf'^(\s*parameter\s+[^=]+\b{name}\b\s*=\s*)([^,]+)(,.*)$'
            m = re.match(pattern, line)
            if m:
                new_line = f"{m.group(1)}{_format_param_default(name, value)}{m.group(3)}"
                if line.endswith("\n") and not new_line.endswith("\n"):
                    new_line += "\n"
                out_lines.append(new_line)
                replaced = True
                break
        if not replaced:
            out_lines.append(line)

    wrapper_path.write_text("".join(out_lines), encoding="utf-8")

def rename_wrapper_to_toplevel(toplevel: str):
    """Rename copied ibex_wrapper.sv to <toplevel>.sv and update module name."""
    if toplevel == "ibex_wrapper":
        return rtl_dir / "ibex_wrapper.sv"

    src = rtl_dir / "ibex_wrapper.sv"
    dst = rtl_dir / f"{toplevel}.sv"
    if not src.exists():
        return dst

    text = src.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r'\bmodule\s+ibex_wrapper\b', f'module {toplevel}', text, count=1)
    dst.write_text(text, encoding="utf-8")
    src.unlink()

    old_rel = os.path.relpath(src, export_dir)
    new_rel = os.path.relpath(dst, export_dir)
    if old_rel in seen_rel:
        seen_rel.remove(old_rel)
    seen_rel.add(new_rel)

    for ent in files_ordered:
        if ent["dst"] == src.resolve():
            ent["dst"] = dst.resolve()
            ent["rel"] = new_rel
            break

    return dst

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

def is_forced_include_source(p: Path | str) -> bool:
    """
    Some upstream Ibex helper modules are pulled through include paths but are
    real compilation units, not textual macro/header includes.

    In particular, the SECDED encoder/decoder files under include/ define
    packages/modules. Marking them as is_include_file makes Vivado skip them as
    sources, leaving instantiated SECDED modules unresolved.
    """
    name = Path(p).name
    return name.endswith(".sv") and any(name.startswith(prefix) for prefix in FORCED_INCLUDE_SOURCES)

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

    # Heuristic header classification for toollist-provided entries. A few .sv
    # files are found through include dirs but must still be compiled as sources.
    is_header = (is_under_incdir(orig) or (orig.suffix.lower() in HEADER_EXTS)) and \
        not is_forced_include_source(orig)

    # Destination: headers and forced include-sources -> include/, normal sources -> rtl/.
    # Keeping forced include-sources in include/ preserves any relative include paths.
    dst_root = inc_dir if (is_header or is_forced_include_source(orig)) else rtl_dir
    if dst_root == inc_dir:
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
    # Register (or get) entry. Some included .sv files are real compilation
    # units and must not be emitted as CAPI2 include-only files.
    ent = _add_entry(dst, not is_forced_include_source(src_abs), src_abs)
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
                    if Path(inc_name).name in KNOWN_OPTIONAL_INCLUDES:
                        continue
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

# 2b) Rename copied wrapper to match toplevel and align defaults with selected config values.
wrapper_path = rename_wrapper_to_toplevel(TOPLEVEL)
wrapper_defaults = parse_wrapper_param_overrides(WRAPPER_PARAM_OVERRIDES)
update_wrapper_defaults(wrapper_path, wrapper_defaults)

# 3) Emit CAPI2 core using explicit include files with include_path
file_base = CORE_FILE_BASENAME if CORE_FILE_BASENAME else core_name.split(":")[-1]
core_path = export_dir / f"{file_base}.core"

with core_path.open("w") as core:
    core.write("CAPI=2:\n\n")
    core.write(f'name: "{core_name}:{core_ver}"\n')
    core.write('description: "Self-contained Ibex snapshot (from tool filelist; includes resolved recursively; no generators)"\n\n')
    core.write("filesets:\n")
    core.write("  files_all:\n")
    core.write("    files:\n")

    # Emit include-tree compilation units first. These are files that upstream
    # exposes via include paths, but which define packages/modules and must be
    # visible to synthesis/simulation as real sources.
    for ent in files_ordered:
        if ent["is_header"] or not is_forced_include_source(ent["rel"]):
            continue
        rel = ent["rel"]
        core.write(f"      - {rel}: {{file_type: systemVerilogSource}}\n")

    # Emit harvested textual headers next. They live under export_dir/include/...
    # and are marked as include files with include_path: include.
    for ent in files_ordered:
        if not ent["is_header"]:
            continue
        rel = ent["rel"]
        core.write(f"      - {rel}:\n")
        core.write("          file_type: systemVerilogSource\n")
        core.write("          is_include_file: true\n")
        core.write("          include_path: include\n")

    # Then emit non-header sources.
    for ent in files_ordered:
        if ent["is_header"]:
            continue
        if is_forced_include_source(ent["rel"]):
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
