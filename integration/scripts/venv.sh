#!/usr/bin/env bash
set -euo pipefail

# Python version (override by exporting PYEXE before sourcing this script)
PYEXE="${PYEXE:-python3.12}"

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${THIS_DIR}/../.." && pwd)"
INTEGRATION_DIR="${ROOT_DIR}/integration"

# venv at integration dir to keep it isolated
VENV_DIR="${INTEGRATION_DIR}/.venv"
PYREQS_FILE="${ROOT_DIR}/python-requirements.txt"
VENV_NAME="$(basename "${ROOT_DIR}")"
export VENV_ACT="${VENV_DIR}/bin/activate"

create_venv() {
  echo ">> Creating venv at ${VENV_DIR} using ${PYEXE}"
  "${PYEXE}" -m venv --prompt "${VENV_NAME}" "${VENV_DIR}"
  . "${VENV_ACT}"
  python -m pip install --upgrade pip wheel
  if [ -f "${PYREQS_FILE}" ]; then
    echo ">> Installing packages from ${PYREQS_FILE}"
    pip install -r "${PYREQS_FILE}"
  else
    echo ">> WARNING: ${PYREQS_FILE} not found, skipping package install"
  fi
}

venv_setup() {
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    echo ">> Deactivating existing venv at ${VIRTUAL_ENV}"
    deactivate || true
  fi

  if [ ! -d "${VENV_DIR}" ]; then
    echo ">> venv not found, creating..."
    create_venv
  else
    echo ">> venv found, activating..."
    . "${VENV_ACT}"
  fi
}

venv_setup

# Finish cleanly whether sourced or executed
return 0 2>/dev/null || exit 0
