#!/bin/bash
# Double-click in Finder: runs the full daily workflow and opens the digest when successful.
# Same underlying runner as launchd: scripts/macos/run-mail-assistant-daily.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

export MAIL_KANBAN_REPO_ROOT="${REPO_ROOT}"
export MAIL_KANBAN_DIGEST_OUT="${REPO_ROOT}/data/digest.md"
unset MAIL_KANBAN_RUN_LOG || true

mkdir -p "${REPO_ROOT}/data/logs"
TS="$(date +"%Y%m%d-%H%M%S")"
LOG_FILE="${REPO_ROOT}/data/logs/manual-run-${TS}.log"
ln -sf "manual-run-${TS}.log" "${REPO_ROOT}/data/logs/manual-run-latest.log" 2>/dev/null || true

DAILY_SH="${REPO_ROOT}/scripts/macos/run-mail-assistant-daily.sh"

{
  echo "==== Mail Kanban Assistant — manual run ${TS} ===="
  echo "repo=${REPO_ROOT}"
  echo "digest_out=${MAIL_KANBAN_DIGEST_OUT}"
  echo ""
} | tee -a "${LOG_FILE}"

echo "" | tee -a "${LOG_FILE}"
echo "Running daily workflow (see log: data/logs/manual-run-${TS}.log) …" | tee -a "${LOG_FILE}"

set +e
bash "${DAILY_SH}" run-daily 2>&1 | tee -a "${LOG_FILE}"
RC="${PIPESTATUS[0]}"
set -uo pipefail

DIGEST_PATH="${MAIL_KANBAN_DIGEST_OUT}"
if [[ "${RC}" -eq 0 && -f "${DIGEST_PATH}" ]]; then
  open "${DIGEST_PATH}" || true
fi

echo "" | tee -a "${LOG_FILE}"
if [[ "${RC}" -eq 0 ]]; then
  echo "SUCCESS: daily workflow finished (exit ${RC})." | tee -a "${LOG_FILE}"
  if [[ -f "${DIGEST_PATH}" ]]; then
    echo "Digest: ${DIGEST_PATH}" | tee -a "${LOG_FILE}"
  else
    echo "Note: digest file was not created at ${DIGEST_PATH}." | tee -a "${LOG_FILE}"
  fi
else
  echo "ERROR: daily workflow failed (exit ${RC}). Check the log and LM Studio." | tee -a "${LOG_FILE}"
fi

echo ""
if [[ -t 0 ]]; then
  read -r -p "Press Enter to close… " || true
fi

exit "${RC}"
