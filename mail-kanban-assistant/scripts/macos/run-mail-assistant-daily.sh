#!/usr/bin/env bash
set -euo pipefail

# launchd-friendly wrapper: absolute paths only, no interactive shell assumptions.
# Environment (set by generated plist or your shell):
#   MAIL_KANBAN_REPO_ROOT  — path to mail-kanban-assistant checkout
# Optional:
#   MAIL_KANBAN_VENV_PYTHON — default: $MAIL_KANBAN_REPO_ROOT/.venv/bin/python
#   MAIL_KANBAN_DIGEST_OUT  — passed to run-daily --digest-out
#   MAILDROP_ROOT           — passed to ingest-apple-mail-drop --path

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

case "$CMD" in
  run-daily)
    exec "$PYTHON" -m app.interfaces.cli run-daily --digest-out "$DIGEST_OUT"
    ;;
  ingest-drop)
    exec "$PYTHON" -m app.interfaces.cli ingest-apple-mail-drop --path "$MAILDROP"
    ;;
  *)
    echo "mail-assistant wrapper: unknown command: $CMD" >&2
    exit 2
    ;;
esac
