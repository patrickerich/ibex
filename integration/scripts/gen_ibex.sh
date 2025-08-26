#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Ibex vendor snapshot generator
#
# Features:
# - Local venv bootstrap (via integration/scripts/venv.sh)
# - Config overlay: integration/configs/ibex_configs.yml (falls back to ibex_configs.yaml)
# - Force rebuild wipes build/* and integration/vendor_out/*
# - Runs fusesoc --setup (and minimal --build fallback) to obtain a tool filelist
# - Accepts define hints via DEFINES= (e.g., DEFINES="VERILATOR,SYNTHESIS")
# - Heuristically seeds VERILATOR if the tool list looks like Verilator (*.vc/verilator.f)
# - Calls toollist_to_monocore.py to produce a flat .core + copy SV/SVH/VH (+ recursive includes)
# - Emits METADATA.json with commit, config, tool list, options, define hints, timestamp
#
# Usage:
#   gen_ibex.sh [--force] [CFG]
#
# Environment overrides:
#   TARGET=sim|lint              (default: sim)
#   OUTDIR=<path>                (default: integration/vendor_out/<MYCORP>_ibex_<CFG>)
#   BUILDROOT=<path>             (default: build/ibex_<CFG>)
#   FORCE=0|1                    (default: 0; 1 = wipe build/out before regenerating)
#   WRAPPER_CORE=<ns>:ibex:wrapper   (default: mycorp:ibex:wrapper)
#   CORE_VER=<ver>               (default: 1.0)
#   TOPLEVEL=<module>            (default: ibex_wrapper; passed to python via env)
#   DEFINES=<comma list>         (optional; e.g. "VERILATOR,SYNTHESIS,YOSYS")
#   INCLUDE_FALLBACK=project     (optional; off by default)
#
# -----------------------------------------------------------------------------
set -euo pipefail

# ---- helpers ---------------------------------------------------------------
THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${THIS_DIR}/../.." && pwd)"

info() { echo ">> $*"; }
warn() { echo "!! $*" >&2; }
die()  { echo "ERROR: $*" >&2; exit 1; }

# ---- venv (idempotent) -----------------------------------------------------
# Sources integration/scripts/venv.sh which creates or activates integration/.venv
# and installs upstream python-requirements.txt
source "${THIS_DIR}/venv.sh"

# ---- args/env --------------------------------------------------------------
FORCE="${FORCE:-0}"
if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
  shift
fi
CFG="${1:-small}"

WRAPPER_CORE="${WRAPPER_CORE:-mycorp:ibex:wrapper}"
CORE_VER="${CORE_VER:-1.0}"
TARGET="${TARGET:-sim}"

# Pull <MYCORP> from WRAPPER_CORE, e.g., mycorp:ibex:wrapper
MYCORP="$(echo "${WRAPPER_CORE}" | awk -F: '{print $1}')"
[[ -z "${MYCORP}" ]] && die "Failed to derive vendor prefix from WRAPPER_CORE='${WRAPPER_CORE}'"

# Default OUT/BUILD paths (caller may override via OUTDIR/BUILDROOT)
OUT="${OUTDIR:-${ROOT}/integration/vendor_out/${MYCORP}_ibex_${CFG}}"
BUILD="${BUILDROOT:-${ROOT}/build/ibex_${CFG}}"
VENDOR_CORE_NAME="${MYCORP}:ibex:${CFG}"

# Optional knobs passed through to Python via environment:
TOPLEVEL="${TOPLEVEL:-ibex_wrapper}"
INCLUDE_FALLBACK="${INCLUDE_FALLBACK:-disabled}"   # "disabled" | "project"
USER_DEFINES="${DEFINES:-}"                         # e.g. "VERILATOR,SYNTHESIS"

# ---- force handling / directory prep ---------------------------------------
mkdir -p "$(dirname "${OUT}")" "$(dirname "${BUILD}")"
if [[ -d "${BUILD}" || -d "${OUT}" ]]; then
  if [[ "${FORCE}" == "1" ]]; then
    info "--force: removing existing directories:"
    if [[ -d "${BUILD}" ]]; then
      echo "   rm -rf ${BUILD}"
      rm -rf "${BUILD}"
    fi
    # IMPORTANT: Wipe entire vendor_out/ to avoid stale cores confusing discovery
    VROOT="${ROOT}/integration/vendor_out"
    if [[ -d "${VROOT}" ]]; then
      echo "   rm -rf ${VROOT}"
      rm -rf "${VROOT}"
    fi
  else
    [[ -d "${BUILD}" ]] && warn "build directory exists: ${BUILD}"
    [[ -d "${OUT}"   ]] && warn "output directory exists: ${OUT}"
    echo "   Aborting. Re-run with either:"
    echo "     FORCE=1 make -C integration vendor CFG=${CFG}"
    echo "   or"
    echo "     ${THIS_DIR}/gen_ibex.sh --force ${CFG}"
    exit 2
  fi
fi
mkdir -p "${BUILD}" "${OUT}"

# ---- resolve configuration file --------------------------------------------
CUSTOM_CONF="${ROOT}/integration/configs/ibex_configs.yml"
ROOT_CONF="${ROOT}/ibex_configs.yaml"

if [[ -f "${CUSTOM_CONF}" ]]; then
  info "Using custom config file: ${CUSTOM_CONF}"
  # Option must precede positionals:
  OPTS="$(cd "${ROOT}" && ./util/ibex_config.py --config_filename "${CUSTOM_CONF}" "${CFG}" fusesoc_opts)"
else
  info "Using upstream default config file: ${ROOT_CONF}"
  OPTS="$(cd "${ROOT}" && ./util/ibex_config.py "${CFG}" fusesoc_opts)"
fi

info "Generating (TARGET=${TARGET}) with config '${CFG}'"
echo "   MYCORP=${MYCORP}"
echo "   WRAPPER_CORE=${WRAPPER_CORE}"
echo "   BUILD=${BUILD}"
echo "   OUT=${OUT}"
echo "   EXPORTED CORE NAME=${VENDOR_CORE_NAME}:${CORE_VER}"
echo "   EXPORTED CORE FILE=${MYCORP}_ibex_${CFG}.core"
[[ -n "${USER_DEFINES}" ]] && echo "   USER DEFINES=${USER_DEFINES}"
[[ "${INCLUDE_FALLBACK}" != "disabled" ]] && echo "   INCLUDE_FALLBACK=${INCLUDE_FALLBACK}"

# ---- run fusesoc to produce a tool filelist --------------------------------
fusesoc --cores-root "${ROOT}" run \
  --target="${TARGET}" --setup --build-root "${BUILD}" \
  ${WRAPPER_CORE} ${OPTS}

# find a tool filelist produced during setup
TOOL_LIST=""
for pat in "verilator.f" "*.vc" "*.f"; do
  hit=$(find "${BUILD}" -type f -name "${pat}" | head -n 1 || true)
  if [[ -n "${hit}" ]]; then TOOL_LIST="${hit}"; break; fi
done

# if none, do a minimal --build to flush one out, then try again
if [[ -z "${TOOL_LIST}" ]]; then
  info "No tool filelist found at setup time; trying a minimal build…"
  fusesoc --cores-root "${ROOT}" run \
    --target="${TARGET}" --build --build-root "${BUILD}" \
    ${WRAPPER_CORE} ${OPTS}
  TOOL_LIST=$(find "${BUILD}" -type f \( -name 'verilator.f' -o -name '*.vc' -o -name '*.f' \) | head -n 1 || true)
fi

if [[ -z "${TOOL_LIST}" ]]; then
  die "No tool filelist (verilator.f / *.vc / *.f) found under ${BUILD}
Debug: first few directories:
$(cd "${BUILD}" && find . -maxdepth 4 -type d -print | sed 's/^/  /' | head -n 200)"
fi

info "Using tool filelist: ${TOOL_LIST}"

# ---- define hints: seed VERILATOR if filelist looks like Verilator ----------
DEF_HINTS=""
case "$(basename "${TOOL_LIST}")" in
  verilator.f|*.vc) DEF_HINTS="VERILATOR" ;;
esac

# Merge user-provided DEFINES (comma-separated), if any
if [[ -n "${USER_DEFINES}" ]]; then
  if [[ -n "${DEF_HINTS}" ]]; then
    DEF_HINTS="${DEF_HINTS},${USER_DEFINES}"
  else
    DEF_HINTS="${USER_DEFINES}"
  fi
fi

# ---- run exporter: tool-filelist -> monocore snapshot -----------------------
# Pass env knobs to Python:
#   TOPLEVEL, DEFINES (seeded), INCLUDE_FALLBACK (optional),
#   CORE_FILE_BASENAME override to control the on-disk .core filename.
export TOPLEVEL
export INCLUDE_FALLBACK
export CORE_FILE_BASENAME="${MYCORP}_ibex_${CFG}"

DEFINES="${DEF_HINTS}" \
python3 "${THIS_DIR}/toollist_to_monocore.py" \
  "${TOOL_LIST}" "${OUT}" "${VENDOR_CORE_NAME}" "${CORE_VER}"

# ---- provenance -------------------------------------------------------------
UP_SHA="$(git -C "${ROOT}" rev-parse HEAD || echo unknown)"
DATE="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
cat > "${OUT}/METADATA.json" <<EOF
{
  "ibex_git_sha": "${UP_SHA}",
  "config": "${CFG}",
  "origin": "tool-filelist",
  "tool_list": "$(basename "${TOOL_LIST}")",
  "opts": "$(echo "${OPTS}" | sed 's/\"/\\\\\"/g')",
  "defines_hints": "$(echo "${DEF_HINTS}" | sed 's/\"/\\\\\"/g')",
  "include_fallback": "${INCLUDE_FALLBACK}",
  "generated_utc": "${DATE}"
}
EOF

info "Exported '${CFG}' to ${OUT}"
