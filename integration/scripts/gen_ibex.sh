#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# Usage:
#   gen_ibex.sh [--force] [CFG]
#
# Env overrides:
#   WRAPPER_CORE=<vendor:lib:core>  (default: mycorp:ibex:wrapper)
#   TARGET=sim|lint                 (default: sim)
#   OUTDIR=<path>                   (default: integration/vendor_out/<MYCORP>_ibex_<CFG>)
#   BUILDROOT=<path>                (default: build/ibex_<CFG>)
#   FORCE=0|1                       (default: 0; 1 = wipe build/out before regenerating)
#   CORE_VER=<ver>                  (default: 1.0; exported core version)
#
# Notes:
#   - <MYCORP> is derived from WRAPPER_CORE (text before first ':').
#   - Configs are taken from integration/configs/ibex_configs.yml if present,
#     otherwise from ibex_configs.yaml at repo root.
# -----------------------------------------------------------------------------

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${THIS_DIR}/../.." && pwd)"

# Defaults / args
WRAPPER_CORE="${WRAPPER_CORE:-mycorp:ibex:wrapper}"

# ensure/activate venv (idempotent)
source "${THIS_DIR}/venv.sh"

# parse positional args
FORCE="${FORCE:-0}"
if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
  shift
fi
CFG="${1:-small}"

TARGET="${TARGET:-sim}"
CORE_VER="${CORE_VER:-1.0}"

# ---------------------------------------------------------------------------
# Derive <MYCORP> from wrapper core name, with fallbacks
# ---------------------------------------------------------------------------
MYCORP="${WRAPPER_CORE%%:*}"  # e.g. "mycorp" from "mycorp:ibex:wrapper"

if [[ -z "${MYCORP}" || "${MYCORP}" == "${WRAPPER_CORE}" ]]; then
  # Try reading the name: field from a local wrapper core file
  WC_DIR="${THIS_DIR}/../wrappers"
  CFILE="$(ls -1 "${WC_DIR}"/*_ibex_wrapper.core 2>/dev/null | head -n1 || true)"
  if [[ -n "${CFILE}" ]]; then
    CORE_NAME_LINE="$(grep -E '^name:' "${CFILE}" | head -n1 || true)"
    CORE_NAME="$(echo "${CORE_NAME_LINE}" | sed -n 's/.*"\(.*\)".*/\1/p')"
    if [[ -n "${CORE_NAME}" ]]; then
      # Drop trailing version if present, keep vendor:lib:core
      WRAPPER_CORE="${CORE_NAME%:*}"
      MYCORP="${WRAPPER_CORE%%:*}"
    else
      # Fallback to filename prefix: <MYCORP>_ibex_wrapper.core
      B="$(basename "${CFILE}")"
      MYCORP="${B%_ibex_wrapper.core}"
    fi
  fi
fi

# Sanity default if still empty
if [[ -z "${MYCORP}" ]]; then
  MYCORP="mycorp"
fi

# ---------------------------------------------------------------------------
# Naming for outputs and exported core
# ---------------------------------------------------------------------------
OUT_BASENAME="${OUT_BASENAME:-${MYCORP}_ibex_${CFG}}"
OUT="${OUTDIR:-${ROOT}/integration/vendor_out/${OUT_BASENAME}}"
BUILD="${BUILDROOT:-${ROOT}/build/ibex_${CFG}}"

VENDOR_NS="${VENDOR_NS:-${MYCORP}:ibex}"   # e.g. "mycorp:ibex"
CORE_ID="${CORE_ID:-${CFG}}"               # tail part in exported core name
VENDOR_CORE_NAME="${VENDOR_NS}:${CORE_ID}" # e.g. "mycorp:ibex:small"

mkdir -p "$(dirname "${OUT}")" "$(dirname "${BUILD}")"

# If dirs exist, either wipe (force) or bail early with hint
if [[ -d "${BUILD}" || -d "${OUT}" ]]; then
  if [[ "${FORCE}" == "1" ]]; then
    echo ">> --force: removing existing directories:"
    [[ -d "${BUILD}" ]] && echo "   rm -rf ${BUILD}" && rm -rf "${BUILD}"
    [[ -d "${OUT}"   ]] && echo "   rm -rf ${OUT}"   && rm -rf "${OUT}"
  else
    [[ -d "${BUILD}" ]] && echo "!! WARNING: build directory exists: ${BUILD}"
    [[ -d "${OUT}"   ]] && echo "!! WARNING: output directory exists: ${OUT}"
    echo "   Aborting. Re-run with either:"
    echo "     FORCE=1 make -C integration vendor CFG=${CFG}         # via Make (recommended)"
    echo "   or"
    echo "     ${THIS_DIR}/gen_ibex.sh --force ${CFG}                # call script directly"
    exit 2
  fi
fi

mkdir -p "${BUILD}" "${OUT}"

# ---------------------------------------------------------------------------
# Resolve configuration file:
#   1) Prefer integration/configs/ibex_configs.yml  (overlay)
#   2) Else rely on upstream root ibex_configs.yaml (default behavior)
# ---------------------------------------------------------------------------
CUSTOM_CONF="${ROOT}/integration/configs/ibex_configs.yml"
ROOT_CONF="${ROOT}/ibex_configs.yaml"

if [[ -f "${CUSTOM_CONF}" ]]; then
  echo ">> Using custom config file: ${CUSTOM_CONF}"
  # Flag must come before positionals
  OPTS="$(cd "${ROOT}" && ./util/ibex_config.py --config_filename "${CUSTOM_CONF}" "${CFG}" fusesoc_opts)"
else
  echo ">> Using upstream default config file: ${ROOT_CONF}"
  OPTS="$(cd "${ROOT}" && ./util/ibex_config.py "${CFG}" fusesoc_opts)"
fi

echo ">> Generating (TARGET=${TARGET}) with config '${CFG}'"
echo "   MYCORP=${MYCORP}"
echo "   WRAPPER_CORE=${WRAPPER_CORE}"
echo "   BUILD=${BUILD}"
echo "   OUT=${OUT}"
echo "   EXPORTED CORE NAME=${VENDOR_CORE_NAME}:${CORE_VER}"
echo "   EXPORTED CORE FILE=${OUT_BASENAME}.core"

# ---------------------------------------------------------------------------
# Setup (and minimal build if needed) on a tool-backed target so filelists exist
# ---------------------------------------------------------------------------
fusesoc --cores-root "${ROOT}" run \
  --target="${TARGET}" --setup --build-root "${BUILD}" \
  "${WRAPPER_CORE}" ${OPTS}

# Find a tool filelist produced during setup
TOOL_LIST=""
for pat in "verilator.f" "*.vc" "*.f"; do
  hit=$(find "${BUILD}" -type f -name "${pat}" | head -n 1 || true)
  if [[ -n "${hit}" ]]; then TOOL_LIST="${hit}"; break; fi
done

# If none, do a minimal --build to flush one out, then try again
if [[ -z "${TOOL_LIST}" ]]; then
  echo ">> No tool filelist found at setup time; trying a minimal build…"
  fusesoc --cores-root "${ROOT}" run \
    --target="${TARGET}" --build --build-root "${BUILD}" \
    "${WRAPPER_CORE}" ${OPTS}
  TOOL_LIST=$(find "${BUILD}" -type f \( -name 'verilator.f' -o -name '*.vc' -o -name '*.f' \) | head -n 1 || true)
fi

if [[ -z "${TOOL_LIST}" ]]; then
  echo "ERROR: No tool filelist (verilator.f / *.vc / *.f) found under ${BUILD}" >&2
  echo "Debug: first few directories:" >&2
  (cd "${BUILD}" && find . -maxdepth 4 -type d -print | sed 's/^/  /' | head -n 200) >&2
  exit 1
fi

echo ">> Using tool filelist: ${TOOL_LIST}"

# Make the exported core filename '<MYCORP>_ibex_<CFG>.core'
export CORE_FILE_BASENAME="${OUT_BASENAME}"

# Convert the tool filelist into a frozen monocore and copy sources
python3 "${THIS_DIR}/toollist_to_monocore.py" \
  "${TOOL_LIST}" "${OUT}" "${VENDOR_CORE_NAME}" "${CORE_VER}"

# Provenance
UP_SHA="$(git -C "${ROOT}" rev-parse HEAD)"
DATE="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
cat > "${OUT}/METADATA.json" <<EOF
{
  "ibex_git_sha": "${UP_SHA}",
  "config": "${CFG}",
  "origin": "tool-filelist",
  "tool_list": "$(basename "${TOOL_LIST}")",
  "opts": "$(echo "${OPTS}" | sed 's/\"/\\\\\"/g')",
  "exported_core_name": "${VENDOR_CORE_NAME}:${CORE_VER}",
  "exported_core_file": "${OUT_BASENAME}.core",
  "generated_utc": "${DATE}"
}
EOF

echo ">> Exported '${CFG}' to ${OUT}"
