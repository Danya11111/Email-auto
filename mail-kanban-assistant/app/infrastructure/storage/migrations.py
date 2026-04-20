from __future__ import annotations

import sqlite3


def upgrade_schema(conn: sqlite3.Connection) -> None:
    """Best-effort forward-compatible upgrades for existing local SQLite files."""

    cols = {row[1] for row in conn.execute("PRAGMA table_info(triage_results)").fetchall()}
    if cols and "human_confirmed" not in cols:
        conn.execute("ALTER TABLE triage_results ADD COLUMN human_confirmed INTEGER NOT NULL DEFAULT 0")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          review_kind TEXT NOT NULL,
          related_message_id INTEGER NOT NULL,
          related_task_id INTEGER,
          reason_code TEXT NOT NULL,
          reason_text TEXT NOT NULL,
          confidence REAL NOT NULL,
          payload_json TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          decided_at TEXT,
          decided_by TEXT,
          decision_note TEXT,
          FOREIGN KEY(related_message_id) REFERENCES messages(id) ON DELETE CASCADE,
          FOREIGN KEY(related_task_id) REFERENCES extracted_tasks(id) ON DELETE CASCADE
        )
        """
    )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_review_items_status ON review_items(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_review_items_kind ON review_items(review_kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_review_items_message ON review_items(related_message_id)")

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_review_pending_triage_message
        ON review_items(related_message_id)
        WHERE status = 'pending' AND review_kind = 'triage' AND related_task_id IS NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_review_pending_task
        ON review_items(related_task_id)
        WHERE status = 'pending' AND review_kind = 'task' AND related_task_id IS NOT NULL
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingested_artifacts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          content_hash TEXT NOT NULL UNIQUE,
          snapshot_id TEXT,
          source_type TEXT NOT NULL,
          original_filename TEXT NOT NULL,
          related_message_id INTEGER,
          status TEXT NOT NULL,
          first_seen_at TEXT NOT NULL,
          processed_at TEXT,
          error_text TEXT,
          FOREIGN KEY(related_message_id) REFERENCES messages(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_status ON ingested_artifacts(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_snapshot_id ON ingested_artifacts(snapshot_id)")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ingested_artifacts_snapshot_id
        ON ingested_artifacts(snapshot_id)
        WHERE snapshot_id IS NOT NULL
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kanban_sync_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id INTEGER NOT NULL,
          provider TEXT NOT NULL,
          sync_status TEXT NOT NULL,
          external_card_id TEXT,
          external_card_url TEXT,
          card_fingerprint TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          synced_at TEXT,
          last_attempt_at TEXT,
          last_error TEXT,
          retry_count INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY(task_id) REFERENCES extracted_tasks(id) ON DELETE CASCADE,
          UNIQUE(task_id, provider)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kanban_sync_status ON kanban_sync_records(sync_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kanban_sync_provider ON kanban_sync_records(provider)")

    conn.commit()
