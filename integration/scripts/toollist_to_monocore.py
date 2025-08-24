#!/usr/bin/env python3
import os
import sys
import shlex
import shutil
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
# include/ is created lazily only if needed

visited_lists = set()
files_ordered = []        # list of (relpath_from_export, is_header)
seen_rel = set()
incdirs = []              # absolute include dirs, ordered

HDL_EXTS = (".sv", ".svh", ".vh", ".v")
HEADER_EXTS = (".svh", ".vh")

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

def add_src(base: Path, token: str):
    p = _resolve(base, Path(token))
    if not p.exists():
        return
    if p.suffix.lower() not in HDL_EXTS:
        return

    is_header = is_under_incdir(p) or (p.suffix.lower() in HEADER_EXTS)

    dst_root = inc_dir if is_header else rtl_dir
    if is_header:
        ensure_dir(inc_dir)  # lazily create include/ only if we actually have headers

    dst = dst_root / p.name
    if dst.exists():
        i = 1
        while True:
            cand = dst_root / f"{i}_{p.name}"
            if not cand.exists():
                dst = cand
                break
            i += 1

    shutil.copy2(p, dst)
    rel = os.path.relpath(dst, export_dir)
    if rel not in seen_rel:
        seen_rel.add(rel)
        files_ordered.append((rel, is_header))

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

# Parse the top-level list (recurses via -f chains)
parse_list(root_list)

# Decide .core filename
file_base = CORE_FILE_BASENAME if CORE_FILE_BASENAME else core_name.split(":")[-1]
core_path = export_dir / f"{file_base}.core"

# Emit CAPI2 core (no include_dirs; use is_include_file instead)
with core_path.open("w") as core:
    core.write("CAPI=2:\n\n")
    core.write(f'name: "{core_name}:{core_ver}"\n')
    core.write('description: "Self-contained Ibex snapshot (from tool filelist; no generators)"\n\n')
    core.write("filesets:\n")
    core.write("  files_all:\n")
    core.write("    files:\n")
    for rel, is_hdr in files_ordered:
        core.write(f"      - {rel}: {{file_type: systemVerilogSource")
        if is_hdr:
            core.write(", is_include_file: true")
        core.write("}\n")  # <-- single closing brace, NOT '}}'
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
