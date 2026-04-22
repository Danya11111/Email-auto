#!/usr/bin/env bash
set -euo pipefail

# launchd-friendly wrapper: absolute paths only, no interactive shell assumptions.
# Environment (set by generated plist or your shell):
#   MAIL_KANBAN_REPO_ROOT  — path to mail-kanban-assistant checkout
# Optional:
#   MAIL_KANBAN_VENV_PYTHON — default: $MAIL_KANBAN_REPO_ROOT/.venv/bin/python
#   MAIL_KANBAN_DIGEST_OUT  — passed to run-daily --digest-out
#   MAILDROP_ROOT           — passed to ingest-apple-mail-drop --path
#   MAIL_KANBAN_RUN_LOG     — when set, append this run's stdout/stderr to this file (launchd parity)
#   MAIL_KANBAN_PREFLIGHT_LM — when 1 (default), best-effort curl to LM Studio /v1/models (warn only)

CMD="${1:-run-daily}"
ROOT="${MAIL_KANBAN_REPO_ROOT:?MAIL_KANBAN_REPO_ROOT is required}"
cd "$ROOT"

PYTHON="${MAIL_KANBAN_VENV_PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "mail-assistant wrapper: Python not executable: $PYTHON" >&2
  exit 1
fi

DIGEST_OUT="${MAIL_KANBAN_DIGEST_OUT:-$ROOT/data/digest.md}"
MAILDROP="${MAILDROP_ROOT:-$ROOT/data/maildrop}"

MAIL_KANBAN_PREFLIGHT_LM="${MAIL_KANBAN_PREFLIGHT_LM:-1}"

# Keep URL rules aligned with app.application.lm_studio_probe.lm_studio_models_probe_url
_lm_studio_models_probe_url() {
  local base="$1"
  base="${base%/}"
  echo "${base}/models"
}

_read_lm_studio_base_from_dotenv() {
  local env_file="$ROOT/.env"
  local base="http://localhost:1234/v1"
  if [[ -f "$env_file" ]]; then
    local line
    line="$(grep -E '^[[:space:]]*LM_STUDIO_BASE_URL=' "$env_file" 2>/dev/null | tail -n1 || true)"
    if [[ -n "$line" ]]; then
      base="${line#*=}"
      base="${base%%#*}"
      base="${base%"${base##*[![:space:]]}"}"
      base="${base#"${base%%[![:space:]]*}"}"
      base="${base%\"}"
      base="${base#\"}"
      base="${base%\'}"
      base="${base#\'}"
    fi
  fi
  printf '%s' "$base"
}

_preflight_lm_studio() {
  [[ "${MAIL_KANBAN_PREFLIGHT_LM}" == "1" ]] || return 0
  local base probe
  base="$(_read_lm_studio_base_from_dotenv)"
  probe="$(_lm_studio_models_probe_url "$base")"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 3 "$probe" >/dev/null 2>&1; then
      echo "mail-assistant wrapper: LM Studio reachable (${probe})" >&2
      return 0
    fi
  else
    echo "mail-assistant wrapper: curl not found; skipping LM Studio preflight" >&2
    return 0
  fi
  echo "mail-assistant wrapper: WARN — LM Studio not reachable at ${probe}." >&2
  echo "mail-assistant wrapper:        Start LM Studio and load a model, or fix LM_STUDIO_BASE_URL in .env" >&2
  if [[ -t 2 ]]; then
    echo "mail-assistant wrapper:        (continuing; triage may fail until the server is up)" >&2
  fi
}

_preflight_lm_studio

_run_cli_append_log() {
  local log_path="$1"
  shift
  mkdir -p "$(dirname "$log_path")"
  {
    echo "---- $(date -u +"%Y-%m-%dT%H:%M:%SZ") mail-assistant $* (pid=$$) ----"
    "$@"
  } >>"$log_path" 2>&1
}

case "$CMD" in
  run-daily)
    if [[ -n "${MAIL_KANBAN_RUN_LOG:-}" ]]; then
      _run_cli_append_log "$MAIL_KANBAN_RUN_LOG" "$PYTHON" -m app.interfaces.cli run-daily --digest-out "$DIGEST_OUT"
      exit $?
    else
      exec "$PYTHON" -m app.interfaces.cli run-daily --digest-out "$DIGEST_OUT"
    fi
    ;;
  ingest-drop)
    if [[ -n "${MAIL_KANBAN_RUN_LOG:-}" ]]; then
      _run_cli_append_log "$MAIL_KANBAN_RUN_LOG" "$PYTHON" -m app.interfaces.cli ingest-apple-mail-drop --path "$MAILDROP"
      exit $?
    else
      exec "$PYTHON" -m app.interfaces.cli ingest-apple-mail-drop --path "$MAILDROP"
    fi
    ;;
  *)
    echo "mail-assistant wrapper: unknown command: $CMD" >&2
    exit 2
    ;;
esac
