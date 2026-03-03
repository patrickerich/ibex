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

This integration keeps the generator environment local, then emits `vendor_out/<CFG>_ibex/` as the consumable artifact.

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
    │   └─ <CFG>_ibex/
    │       ├─ rtl/                 # copied sources
    │       ├─ include/             # copied headers (only if any)
    │       ├─ lowrisc_<CFG>_ibex.core
    │       └─ METADATA.json
    └─ wrappers/
        ├─ lowrisc_ibex_wrapper.core   # wrapper core you invoke via FuseSoC
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

    integration/vendor_out/socrates_ibex/
    ├─ rtl/
    ├─ include/                      # present only if headers were copied
    ├─ lowrisc_socrates_ibex.core    # core name: "lowrisc:ibex:socrates:1.0"
    └─ METADATA.json

Defaults:
- Wrapper core invoked: `WRAPPER_CORE = lowrisc:ibex:wrapper`
- Exported core name: `lowrisc:ibex:<CFG>:<CORE_VER>` (`CORE_VER=1.0`)
- Exported file name: `lowrisc_<CFG>_ibex.core`
- Toplevel inside export: `<CFG>_ibex_wrapper`

---

## Creating custom configurations

Place company-specific configs in:

    integration/configs/ibex_configs.yml

The exporter prefers this overlay; if absent, it falls back to upstream `ibex_configs.yaml`.

Sanity check a config (from repo root):

    ./util/ibex_config.py --config_filename integration/configs/ibex_configs.yml <CFG> fusesoc_opts

---

## Wrapper core & module

- Core file on disk: `integration/wrappers/lowrisc_ibex_wrapper.core`
  Inside it, set:

  name: "lowrisc:ibex:wrapper:1.0"

- Wrapper RTL: `integration/wrappers/ibex_wrapper.sv`
  The module (`ibex_wrapper`) exposes ibex_top’s native ports (instr/data, IRQs, debug, crash-dump, DFT, scrambling, RVFI under `RVFI`, etc.). It’s a zero-logic pass-through; trim or extend as needed.
  Config parameters forwarded by the wrapper/core include RV32E, RV32M, RV32B, RV32ZC, RegFile, BranchTargetALU, WritebackStage, ICache, ICacheECC, ICacheScramble, BranchPredictor, DbgTriggerEn, SecureIbex, PMPEnable, PMPGranularity, PMPNumRegions, MHPMCounterNum, and MHPMCounterWidth.

Wrapper/core naming is fixed to `lowrisc` in this integration flow for consistency.

---

## SecureIbex chassis checklist

If `SecureIbex=1`, simulation can stall or timeout when the surrounding chassis/TB doesn't drive
Ibex security-facing signals correctly. Use this checklist for bring-up:

- Drive `fetch_enable_i` to the **multi-bit ON encoding** (`ibex_pkg::IbexMuBiOn`), not `1'b1`.
- Keep `scan_rst_ni` deasserted (`1'b1`) in normal operation.
- Provide valid integrity bits for memory return data:
  - `instr_rdata_intg_i` must match `instr_rdata_i`
  - `data_rdata_intg_i` must match `data_rdata_i`
- Ensure instruction/data handshake signals make forward progress (`*_gnt_i`, `*_rvalid_i`).
- Keep `instr_err_i` / `data_err_i` low unless intentionally injecting faults.

Reference wiring is available in
`examples/simple_system/rtl/ibex_simple_system.sv` (see the `SecureIbex` integrity generation block).

Notes:
- `integration/wrappers/ibex_wrapper.sv` now uses explicit named port mapping (no `.*`).
- Lockstep/shadow outputs are exported by the wrapper for visibility; leaving these outputs
  unconnected in a chassis is generally fine.

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

- **With FuseSoC**: add `--cores-root integration/vendor_out/<CFG>_ibex` and depend on
  `name: "lowrisc:ibex:<CFG>:<CORE_VER>"`.
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
