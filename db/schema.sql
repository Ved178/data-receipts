-- digin.db contract (frozen, docs/team-brief.md §5). Change only after posting in the team group.
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS raw_lookups (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, device TEXT NOT NULL, session_id INTEGER,
  qname TEXT NOT NULL, qtype TEXT, cname_chain TEXT,
  answer_ips TEXT,
  blocked INTEGER NOT NULL DEFAULT 0, block_reason TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id INTEGER PRIMARY KEY, device TEXT, app_name TEXT NOT NULL,
  start_ts REAL NOT NULL, end_ts REAL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ml_flags (
  domain TEXT PRIMARY KEY, score REAL NOT NULL, reasons TEXT NOT NULL,
  status TEXT NOT NULL,
  model_version TEXT NOT NULL, flagged_ts REAL NOT NULL, updated_ts REAL
);
CREATE TABLE IF NOT EXISTS raw_flows (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, device TEXT NOT NULL, session_id INTEGER,
  dst_ip TEXT NOT NULL, dst_port INTEGER, proto TEXT,
  bytes_up INTEGER, bytes_down INTEGER, pkts_up INTEGER, pkts_down INTEGER, duration_s REAL
);
CREATE INDEX IF NOT EXISTS ix_lookups_ts ON raw_lookups(ts);
CREATE INDEX IF NOT EXISTS ix_lookups_qname ON raw_lookups(qname);
CREATE INDEX IF NOT EXISTS ix_flows_ts ON raw_flows(ts);
