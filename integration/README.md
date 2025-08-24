# Ibex Integration & Vendoring

This `integration/` directory is a **self-contained exporter** that turns an Ibex configuration into a **frozen, tool-agnostic snapshot** (plain RTL + a single CAPI2 core). The goal is to let downstream projects consume Ibex **without** running FuseSoC generators or relying on lowRISC-specific toolchain forks.

---

## Rationale

Two common ways to reuse Ibex:

1. Reuse upstream cores & generators
   • Pros: stays close to upstream targets and parameters
   • Cons: every consumer must install the exact Python deps and run generators; reproducibility depends on the host environment

2. Export a frozen snapshot (**this approach**)
   • Pros: downstream gets **plain SystemVerilog** + one `.core`; portable and reproducible; no generators required
   • Cons: you regenerate when configs change (scripted here)

This integration keeps the generator environment local, then emits `vendor_out/<MYCORP>_ibex_<CFG>/` as the consumable artifact.

---

## Directory layout

    integration/
    ├─ .venv/                       # local virtualenv (ignored by VCS)
    ├─ Makefile                     # thin wrapper around gen_ibex.sh
    ├─ README.md                    # this file
    ├─ configs/
    │   └─ ibex_configs.yml         # overlay with your custom configs (optional)
    ├─ scripts/
    │   ├─ gen_ibex.sh              # main exporter (setup/build + harvest)
    │   ├─ toollist_to_monocore.py  # turns tool filelist -> single .core
    │   └─ venv.sh                  # creates/activates integration/.venv
    ├─ vendor_out/
    │   └─ <MYCORP>_ibex_<CFG>/
    │       ├─ rtl/                 # copied sources
    │       ├─ include/             # copied headers (only if any)
    │       ├─ <MYCORP>_ibex_<CFG>.core
    │       └─ METADATA.json
    └─ wrappers/
        ├─ <MYCORP>_ibex_wrapper.core  # wrapper core you invoke via FuseSoC
        └─ ibex_wrapper.sv             # wrapper module around ibex_top

Note: The exporter still uses upstream `util/ibex_config.py` and the lowRISC core library during generation; the **output** does not.

---

## One-time setup

From the **repo root**:

    make -C integration venv
    # (or later)
    source integration/.venv/bin/activate

`integration/.venv` lives under `integration/` so it’s easy to ignore via `integration/.gitignore`.

---

## Quick start: generate a snapshot

Pick or create a config name (e.g. `small`, `opentitan`, or your own in the overlay), then:

    FORCE=1 make -C integration vendor CFG=socrates

This produces:

    integration/vendor_out/<MYCORP>_ibex_socrates/
    ├─ rtl/
    ├─ include/                      # present only if headers were copied
    ├─ <MYCORP>_ibex_socrates.core   # core name: "<MYCORP>:ibex:socrates:1.0"
    └─ METADATA.json

Defaults:
- Wrapper core invoked: `WRAPPER_CORE = <MYCORP>:ibex:wrapper`
- Exported core name: `<MYCORP>:ibex:<CFG>:<CORE_VER>` (`CORE_VER=1.0`)
- Exported file name: `<MYCORP>_ibex_<CFG>.core`
- Toplevel inside export: `ibex_wrapper`

---

## Creating custom configurations

Place company-specific configs in:

    integration/configs/ibex_configs.yml

The exporter prefers this overlay; if absent, it falls back to upstream `ibex_configs.yaml`.

Sanity check a config (from repo root):

    ./util/ibex_config.py --config_filename integration/configs/ibex_configs.yml <CFG> fusesoc_opts

---

## Wrapper core & module

- Core file on disk: `integration/wrappers/<MYCORP>_ibex_wrapper.core`
  Inside it, set:

        name: "<MYCORP>:ibex:wrapper:1.0"

- Wrapper RTL: `integration/wrappers/ibex_wrapper.sv`
  The module (`ibex_wrapper`) exposes ibex_top’s native ports (instr/data req/gnt/rvalid, IRQs, debug, etc.). Delete what you don’t need.

If you rename the wrapper’s core name (e.g., change `<MYCORP>`), the exporter auto-derives `<MYCORP>` from `WRAPPER_CORE` (or from the wrapper `.core` file) and uses it in output paths and names.

---

## Command reference

Most common:

    FORCE=1 make -C integration vendor CFG=<cfg>

Useful environment knobs:
- `CFG` – configuration key (e.g. `small`, `opentitan`, `socrates`)
- `FORCE=1` – wipe `build/ibex_<cfg>` and `integration/vendor_out/*` before regenerating
- `TARGET=sim` – FuseSoC target for setup/build (default `sim`)
- `WRAPPER_CORE="<MYCORP>:ibex:wrapper"` – which wrapper core to run
- `CORE_VER=1.0` – version embedded in exported core name
- `TOPLEVEL=ibex_top` – override exported toplevel (default `ibex_wrapper`)
- `OUTDIR=<path>` – change export root (default `integration/vendor_out/<MYCORP>_ibex_<CFG>`)
- `BUILDROOT=<path>` – change build dir (default `build/ibex_<CFG>`)

Examples:

    TOPLEVEL=ibex_top FORCE=1 make -C integration vendor CFG=small
    WRAPPER_CORE=acme:ibex:wrapper FORCE=1 make -C integration vendor CFG=opentitan
    OUTDIR=/tmp/exports FORCE=1 make -C integration vendor CFG=socrates

---

## How it works (nutshell)

1. `scripts/venv.sh` creates/activates `integration/.venv` and installs upstream requirements.
2. `scripts/gen_ibex.sh` resolves the config via `integration/configs/ibex_configs.yml` (if present) or `ibex_configs.yaml`.
3. It runs FuseSoC on the wrapper core (`<MYCORP>:ibex:wrapper`) with `--target=sim --setup` (and a minimal `--build` if needed) to obtain a **tool filelist** (`verilator.f`, `.vc`, or `.f`).
4. `scripts/toollist_to_monocore.py` parses that list, copies HDL into `rtl/` and headers into `include/`, and emits a single `.core`:
   - headers are detected via `+incdir+…` and by extension (`.svh`/`.vh`)
   - `is_include_file: true` is used; `include_dirs:` is not emitted
5. `METADATA.json` records the Ibex commit SHA, config, tool list type, and UTC timestamp.

The result is a **standalone** `vendor_out/<MYCORP>_ibex_<CFG>/` suitable for direct consumption.

---

## Consuming the snapshot downstream

- **With FuseSoC**: add `--cores-root integration/vendor_out/<MYCORP>_ibex_<CFG>` and depend on
  `name: "<MYCORP>:ibex:<CFG>:<CORE_VER>"`.
- **Without FuseSoC**: point your tool at `rtl/` and `include/` (or parse the `.core` to generate a flat filelist).

---

## Troubleshooting

- YAML parse error during generation
  Remove stale outputs (they’re scanned by FuseSoC while generating):

        rm -rf integration/vendor_out
        FORCE=1 make -C integration vendor CFG=<cfg>

- “No tool filelist found”
  Some backends don’t emit a filelist on `--setup`. The script triggers a minimal `--build` to flush it out. This may require Verilator (or change `TARGET` to a backend that emits on setup in your environment).

- `ibex_config.py: error: unrecognized arguments`
  Flags must precede positionals. The script calls:
  `./util/ibex_config.py --config_filename <overlay> <CFG> fusesoc_opts`

- Headers not found
  The exporter marks headers by include dir membership and by extension. If your tree uses unusual header conventions, ensure your core/wrapper contributes correct `+incdir+…` in the tool filelist.

---

## Versioning & licensing

- Snapshots are Apache-2.0–licensed Ibex sources; preserve headers.
- `METADATA.json` records the Ibex commit SHA and config for traceability.
- Tag this repo when you cut a snapshot (e.g., `ibex-vendor/<cfg>/<YYYYMMDD>`).

---



