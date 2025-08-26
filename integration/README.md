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
  The module (`ibex_wrapper`) exposes ibex_top’s native ports (instr/data, IRQs, debug, crash-dump, DFT, scrambling, RVFI under `RVFI`, etc.). It’s a zero-logic pass-through; trim or extend as needed.

If you rename the wrapper’s core name (e.g., change `<MYCORP>`), the exporter auto-derives `<MYCORP>` from `WRAPPER_CORE` and uses it in output paths and names.

---

## Usage notes

### Include handling

The vendoring script is *preprocessor-aware*: it only vendors headers from
`` `include`` directives that are **active** under the current `+define+…` set
found in the tool filelist.

- For normal sim/synth builds, formal headers (e.g. `formal_tb_frag.svh`) are ignored
  since `FORMAL` isn’t defined.
- If you *do* want to build with those headers (e.g. for formal verification),
  make sure your target passes `+define+FORMAL` so the vendoring picks them up.
- There’s an optional fallback (`INCLUDE_FALLBACK=project`) to search the repo
  for missing includes by basename, but it’s disabled by default to avoid
  accidentally pulling in formal-only files.

### Define hints

Some backends (notably Verilator) don’t put their `-D` flags into the filelist.
The exporter therefore supports **define hints**:

- Set `DEFINES=VERILATOR` (or `SYNTHESIS`, `YOSYS`, comma-separated) to seed the
  preprocessor model during vendoring.
- Heuristic: when the tool filelist looks like Verilator (`verilator.f` or `*.vc`),
  `VERILATOR` is auto-seeded. You can add more with `DEFINES=…`.

Example:

    # Synthesis-flavored snapshot (dummy assert macros)
    FORCE=1 DEFINES=SYNTHESIS make -C integration vendor CFG=socrates

### Assert macro family

When the script encounters one of:
- `prim_assert_dummy_macros.svh`
- `prim_assert_yosys_macros.svh`
- `prim_assert_standard_macros.svh`

…it automatically vendors the **siblings** too. This makes a single snapshot usable across
sim (Verilator), synth, and Yosys without regenerating, while keeping formal headers out
unless `FORMAL` is defined.

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
  Some backends don’t emit a filelist on `--setup`. The script triggers a minimal `--build`
  to flush it out. This may require Verilator (or change `TARGET` to a backend that emits on setup).

- Includes missing
  Ensure your defines match your intended use:
  - Add `DEFINES=VERILATOR` (or `SYNTHESIS`, `YOSYS`) when generating.
  - Optionally set `INCLUDE_FALLBACK=project` to allow repo-wide unique basename search.

- `ibex_config.py: error: unrecognized arguments`
  Flags must precede positionals. The script calls:
  `./util/ibex_config.py --config_filename <overlay> <CFG> fusesoc_opts`

---
