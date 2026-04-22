#!/bin/bash
# Double-click: opens the newest digest*.md under ./data (by modification time).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DATA="${REPO_ROOT}/data"

if [[ ! -d "${DATA}" ]]; then
  echo "ERROR: data directory missing: ${DATA}" >&2
  exit 1
fi

cd "${DATA}"
shopt -s nullglob
candidates=(digest*.md)
shopt -u nullglob

latest=""
if [[ "${#candidates[@]}" -gt 0 ]]; then
  latest="$(ls -t "${candidates[@]}" 2>/dev/null | head -1 || true)"
fi
if [[ -z "${latest}" && -f "digest.md" ]]; then
  latest="digest.md"
fi

if [[ -n "${latest}" && -f "${latest}" ]]; then
  echo "Opening ${DATA}/${latest}"
  open "${DATA}/${latest}"
  if [[ -t 0 ]]; then
    read -r -p "Press Enter to close… " || true
  fi
  exit 0
fi

echo "ERROR: No digest*.md found under ${DATA}" >&2
if command -v osascript >/dev/null 2>&1; then
  osascript -e 'display dialog "No digest*.md found under data/. Run the daily workflow first." buttons {"OK"} default button "OK"' || true
fi
if [[ -t 0 ]]; then
  read -r -p "Press Enter to close… " || true
fi
exit 1
