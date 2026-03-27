CREATE TABLE IF NOT EXISTS sites (
  slug TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  source_url TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  pages_project_name TEXT,
  pages_url TEXT,
  custom_domain TEXT,
  latest_job_id TEXT,
  last_error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  site_slug TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_type TEXT NOT NULL,
  status TEXT NOT NULL,
  submitted_by TEXT,
  submitter_ip TEXT,
  queue_time TEXT NOT NULL,
  started_at TEXT,
  heartbeat_at TEXT,
  finished_at TEXT,
  error_message TEXT,
  run_dir TEXT,
  pages_project_name TEXT,
  pages_url TEXT,
  custom_domain TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (site_slug) REFERENCES sites(slug) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  level TEXT NOT NULL DEFAULT 'info',
  message TEXT NOT NULL,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_queue_time ON jobs(status, queue_time);
CREATE INDEX IF NOT EXISTS idx_jobs_site_slug ON jobs(site_slug);
CREATE INDEX IF NOT EXISTS idx_job_events_job_id_created_at ON job_events(job_id, created_at);
