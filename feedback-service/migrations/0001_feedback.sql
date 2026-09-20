CREATE TABLE reports (
  number INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id TEXT NOT NULL UNIQUE,
  receipt_hash TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload TEXT NOT NULL,
  client_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('uploading','pending','sending','uncertain','synced')),
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt INTEGER NOT NULL DEFAULT 0,
  lease_until INTEGER NOT NULL DEFAULT 0,
  issue_number INTEGER,
  last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX reports_client_time ON reports(client_hash, created_at);
CREATE INDEX reports_time ON reports(created_at);
CREATE INDEX reports_status_retry ON reports(status, next_attempt);
