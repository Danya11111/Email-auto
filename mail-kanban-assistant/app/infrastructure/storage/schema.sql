PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  command TEXT NOT NULL,
  status TEXT,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  rfc_message_id TEXT,
  subject TEXT,
  sender TEXT,
  recipients_json TEXT NOT NULL,
  received_at TEXT,
  body_plain TEXT NOT NULL,
  body_normalized TEXT NOT NULL,
  thread_hint TEXT,
  processing_status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  source_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_processing_status ON messages(processing_status);
CREATE INDEX IF NOT EXISTS idx_messages_received_at ON messages(received_at);

CREATE TABLE IF NOT EXISTS triage_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL UNIQUE,
  importance TEXT NOT NULL,
  reply_requirement TEXT NOT NULL,
  summary TEXT NOT NULL,
  actionable INTEGER NOT NULL,
  confidence REAL NOT NULL,
  reason_codes_json TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  human_confirmed INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS extracted_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  due_at TEXT,
  confidence REAL NOT NULL,
  status TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_extracted_tasks_message_id ON extracted_tasks(message_id);

CREATE TABLE IF NOT EXISTS morning_digests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_run_id INTEGER,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  markdown TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE SET NULL
);

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
);

CREATE INDEX IF NOT EXISTS idx_review_items_status ON review_items(status);
CREATE INDEX IF NOT EXISTS idx_review_items_kind ON review_items(review_kind);
CREATE INDEX IF NOT EXISTS idx_review_items_message ON review_items(related_message_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_review_pending_triage_message
ON review_items(related_message_id)
WHERE status = 'pending' AND review_kind = 'triage' AND related_task_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_review_pending_task
ON review_items(related_task_id)
WHERE status = 'pending' AND review_kind = 'task' AND related_task_id IS NOT NULL;

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
);

CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_status ON ingested_artifacts(status);
CREATE INDEX IF NOT EXISTS idx_ingested_artifacts_snapshot_id ON ingested_artifacts(snapshot_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_ingested_artifacts_snapshot_id
ON ingested_artifacts(snapshot_id)
WHERE snapshot_id IS NOT NULL;

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
  last_outbound_action TEXT,
  last_operation_note TEXT,
  previous_fingerprint TEXT,
  previous_external_card_url TEXT,
  record_updated_at TEXT,
  FOREIGN KEY(task_id) REFERENCES extracted_tasks(id) ON DELETE CASCADE,
  UNIQUE(task_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_kanban_sync_status ON kanban_sync_records(sync_status);
CREATE INDEX IF NOT EXISTS idx_kanban_sync_provider ON kanban_sync_records(provider);
