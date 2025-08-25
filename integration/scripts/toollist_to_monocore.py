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
INCLUDE_FALLBACK = os.environ.get("INCLUDE_FALLBACK", "disabled")  # "disabled" | "project"

root_list = Path(sys.argv[1]).resolve()
export_dir = Path(sys.argv[2]).resolve()
core_name  = sys.argv[3]
core_ver   = sys.argv[4]

rtl_dir = export_dir / "rtl"
inc_dir = export_dir / "include"
export_dir.mkdir(parents=True, exist_ok=True)
rtl_dir.mkdir(parents=True, exist_ok=True)
# include/ is created lazily only when needed

visited_lists = set()
files_ordered = []   # list of dicts: {"rel","is_header","dst","orig"}
seen_rel = set()
incdirs = []         # absolute include dirs, in order
defines = set()      # macros defined via +define+FOO or -D FOO

HDL_EXTS = (".sv", ".svh", ".vh", ".v")
HEADER_EXTS = (".svh", ".vh")

RE_INCLUDE = re.compile(r'^\s*`include\s+"([^"]+)"')
RE_IFDEF   = re.compile(r'^\s*`ifdef\s+([a-zA-Z_]\w*)')
RE_IFNDEF  = re.compile(r'^\s*`ifndef\s+([a-zA-Z_]\w*)')
RE_ELSIF   = re.compile(r'^\s*`elsif\s+([a-zA-Z_]\w*)')
RE_ELSE    = re.compile(r'^\s*`else\b')
RE_ENDIF   = re.compile(r'^\s*`endif\b')

def _resolve(base: Path, p: Path) -> Path:
    return p if p.is_absolute() else (base / p).resolve()

def add_define_token(tok: str):
    """Capture +define+FOO or +define+FOO=VAL or -D FOO or -D FOO=VAL"""
    if tok.startswith("+define+"):
        # +define+FOO or +define+FOO=VAL or +define+FOO+BAR
        payload = tok[len("+define+"):]
        # split on '+' (verilator style can chain)
        parts = payload.split("+")
        for part in parts:
            name = part.split("=", 1)[0]
            if name:
                defines.add(name)
    elif tok == "-D":
        # next token is the macro
        return "EXPECT_MACRO"  # sentinel for caller
    elif tok.startswith("-D") and len(tok) > 2:
        name = tok[2:].split("=", 1)[0]
        if name:
            defines.add(name)
    return None

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
    rel = os.path.relpath(dst, export_dir)
    if rel in seen_rel:
        return None
    seen_rel.add(rel)
    ent = {"rel": rel, "is_header": is_header, "dst": dst.resolve(), "orig": orig}
    files_ordered.append(ent)
    return ent

def add_src(base: Path, token: str):
    orig = _resolve(base, Path(token))
    if not orig.exists():
        return
    if orig.suffix.lower() not in HDL_EXTS:
        return

    is_header = is_under_incdir(orig) or (orig.suffix.lower() in HEADER_EXTS)

    dst_root = inc_dir if is_header else rtl_dir
    if is_header:
        ensure_dir(inc_dir)

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

    i_expect_macro = False
    for raw in lp.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        toks = shlex.split(line)
        i = 0
        while i < len(toks):
            t = toks[i]

            # defines
            res = add_define_token(t)
            if res == "EXPECT_MACRO":
                if i + 1 < len(toks):
                    name = toks[i+1].split("=", 1)[0]
                    if name:
                        defines.add(name)
                    i += 1
                i += 1
                continue

            if t.startswith("+incdir+"):
                add_incdir(base, t)
            elif t in ("-f", "-F"):
                i += 1
                if i < len(toks):
                    parse_list(_resolve(base, Path(toks[i])))
            elif t.startswith("-f") and len(t) > 2:
                parse_list(_resolve(base, Path(t[2:])))
            elif t.startswith(("+libext", "+librescan", "+notimingchecks")):
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
    name_path = Path(include_name)
    roots = []
    if includer_ent.get("orig") is not None:
        roots.append(includer_ent["orig"].parent)
    for d in incdirs:
        roots.append(Path(d))
    for root in roots:
        cand = (root / name_path).resolve()
        if cand.exists():
            return cand

    # Optional fallback: repo-wide unique basename search
    if INCLUDE_FALLBACK == "project":
        # search from repo root (two levels up from integration/scripts)
        repo_root = Path(__file__).resolve().parents[2]
        matches = list(repo_root.rglob(name_path.name))
        if len(matches) == 1:
            return matches[0]
    return None

def copy_include(include_name: str, src_abs: Path):
    ensure_dir(inc_dir)
    dst = inc_dir / include_name  # preserve include subpath if any
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(src_abs, dst)
    ent = _add_entry(dst, True, src_abs)
    return ent

def scan_active_includes(ent: dict):
    """
    Yield include names from lines that are active under current 'defines'.
    We model a simple preprocessor: `ifdef/ifndef/elsif/else/endif`.
    """
    active_stack = [True]  # outermost default: active
    try:
        with open(ent["dst"], "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                m = RE_IFDEF.match(line)
                if m:
                    macro = m.group(1)
                    active_stack.append(active_stack[-1] and (macro in defines))
                    continue
                m = RE_IFNDEF.match(line)
                if m:
                    macro = m.group(1)
                    active_stack.append(active_stack[-1] and (macro not in defines))
                    continue
                if RE_ELSE.match(line):
                    if len(active_stack) > 1:
                        prev = active_stack.pop()
                        # flip only if parent is active
                        flipped = active_stack[-1] and (not prev)
                        active_stack.append(flipped)
                    continue
                m = RE_ELSIF.match(line)
                if m and len(active_stack) > 1:
                    prev = active_stack.pop()
                    cond = (m.group(1) in defines)
                    new_state = active_stack[-1] and cond and (not prev)
                    active_stack.append(new_state)
                    continue
                if RE_ENDIF.match(line):
                    if len(active_stack) > 1:
                        active_stack.pop()
                    continue

                if not active_stack[-1]:
                    continue

                m = RE_INCLUDE.match(line)
                if m:
                    yield m.group(1)
    except Exception:
        return

# 1) Parse the top-level tool filelist (recurses into -f chains) and copy those files
parse_list(root_list)

# 2) Scan copied files for ACTIVE `include "..."`, resolve and recursively copy
queue = list(files_ordered)
seen_includes = set()

while queue:
    ent = queue.pop(0)
    for inc_name in scan_active_includes(ent):
        # normalize leading ./ in include path
        while inc_name.startswith("./"):
            inc_name = inc_name[2:]
        key = inc_name
        if key in seen_includes:
            continue
        src_abs = resolve_include(inc_name, ent)
        if src_abs is None:
            # Keep this quiet unless you want to debug; inactive branches are already filtered
            print(f"WARNING: Unable to resolve include '{inc_name}' referenced by {ent['rel']}", file=sys.stderr)
            continue
        new_ent = copy_include(inc_name, src_abs)
        seen_includes.add(key)
        if new_ent is not None:
            queue.append(new_ent)

# 3) Emit CAPI2 core (headers first, then sources; order preserved within buckets)
file_base = CORE_FILE_BASENAME if CORE_FILE_BASENAME else core_name.split(":")[-1]
core_path = export_dir / f"{file_base}.core"

hdrs = [e for e in files_ordered if e["is_header"]]
srcs = [e for e in files_ordered if not e["is_header"]]

with core_path.open("w") as core:
    core.write("CAPI=2:\n\n")
    core.write(f'name: "{core_name}:{core_ver}"\n')
    core.write('description: "Self-contained Ibex snapshot (from tool filelist; preproc-aware includes; no generators)"\n\n')
    core.write("filesets:\n")
    core.write("  files_all:\n")
    core.write("    files:\n")

    def emit(ent):
        rel = ent["rel"]
        core.write(f"      - {rel}: {{file_type: systemVerilogSource")
        if ent["is_header"]:
            core.write(", is_include_file: true")
        core.write("}\n")

    for ent in hdrs + srcs:
        emit(ent)

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
