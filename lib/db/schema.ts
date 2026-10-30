export const schema = `
CREATE TABLE installation (
 id INTEGER PRIMARY KEY CHECK(id=1), instance_id TEXT NOT NULL UNIQUE,
 installed_at INTEGER NOT NULL, deployment_month_start INTEGER NOT NULL,
 initial_sync_completed_at INTEGER, timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
 content_version INTEGER NOT NULL DEFAULT 1, progress_version INTEGER NOT NULL DEFAULT 1,
 schedule_hour INTEGER NOT NULL DEFAULT 8, schedule_minute INTEGER NOT NULL DEFAULT 0,
 schedule_version INTEGER NOT NULL DEFAULT 1, next_scheduled_at INTEGER NOT NULL,
 worker_heartbeat_at INTEGER
);
CREATE TABLE journal (
 id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id INTEGER UNIQUE, name TEXT NOT NULL, metadata TEXT NOT NULL
);
CREATE TABLE journal_issn (issn TEXT PRIMARY KEY, journal_id INTEGER NOT NULL REFERENCES journal(id));
CREATE TABLE journal_follow (
 journal_id INTEGER PRIMARY KEY REFERENCES journal(id), active INTEGER NOT NULL DEFAULT 1,
 followed_at INTEGER NOT NULL, unfollowed_at INTEGER, filter_enabled INTEGER NOT NULL DEFAULT 0,
 filter_version INTEGER NOT NULL DEFAULT 1, initial_fetch_completed_at INTEGER, last_successful_sync_at INTEGER
);
CREATE TABLE journal_stats (
 journal_id INTEGER PRIMARY KEY REFERENCES journal(id), visible_count INTEGER NOT NULL DEFAULT 0,
 favorite_count INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE paper (
 id INTEGER PRIMARY KEY AUTOINCREMENT, doi TEXT UNIQUE, journal_id INTEGER NOT NULL REFERENCES journal(id),
 title_original TEXT NOT NULL, title_hash TEXT NOT NULL, metadata_hash TEXT NOT NULL,
 published TEXT, date_precision TEXT NOT NULL, date_field TEXT, published_sort TEXT NOT NULL,
 date_eligible INTEGER NOT NULL, url TEXT NOT NULL, source TEXT NOT NULL,
 fetched_at INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX paper_journal ON paper(journal_id,id);
CREATE TABLE paper_external_id (source TEXT NOT NULL, external_id TEXT NOT NULL, paper_id INTEGER NOT NULL REFERENCES paper(id), PRIMARY KEY(source,external_id));
CREATE TABLE paper_state (
 paper_id INTEGER PRIMARY KEY REFERENCES paper(id), archived_at INTEGER, favorited_at INTEGER,
 manual_filter_override TEXT, version INTEGER NOT NULL DEFAULT 1, archive_operation TEXT
);
CREATE TABLE paper_ai_result (
 id INTEGER PRIMARY KEY AUTOINCREMENT, paper_id INTEGER NOT NULL REFERENCES paper(id),
 kind TEXT NOT NULL CHECK(kind IN ('filter','translate')), cache_key TEXT NOT NULL,
 decision TEXT CHECK(decision IN ('keep','exclude','uncertain')), reason TEXT, title_zh TEXT,
 created_at INTEGER NOT NULL, UNIQUE(paper_id,kind,cache_key)
);
CREATE INDEX ai_paper ON paper_ai_result(paper_id,kind,cache_key);
CREATE TABLE paper_listing (
 paper_id INTEGER PRIMARY KEY REFERENCES paper(id), journal_id INTEGER NOT NULL REFERENCES journal(id),
 published_sort TEXT NOT NULL, inbox_visible INTEGER NOT NULL, favorited INTEGER NOT NULL,
 title_zh TEXT, decision TEXT, reason TEXT, filter_key TEXT, translation_key TEXT, ai_status TEXT NOT NULL
);
CREATE INDEX inbox_page ON paper_listing(journal_id,published_sort DESC,paper_id DESC) WHERE inbox_visible=1;
CREATE INDEX inbox_all_page ON paper_listing(published_sort DESC,paper_id DESC) WHERE inbox_visible=1;
CREATE INDEX favorites_page ON paper_listing(published_sort DESC,paper_id DESC) WHERE favorited=1;
CREATE TABLE ai_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), base_url TEXT NOT NULL DEFAULT '', encrypted_key TEXT,
 model TEXT NOT NULL DEFAULT '', filter_prompt TEXT NOT NULL, provider_version INTEGER NOT NULL DEFAULT 1,
 filter_prompt_version INTEGER NOT NULL DEFAULT 1, config_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE sync_run (
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, window_start INTEGER NOT NULL,
 window_end INTEGER NOT NULL, targets TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 created_at INTEGER NOT NULL, completed_at INTEGER, schedule_key TEXT UNIQUE
);
CREATE TABLE task (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER REFERENCES sync_run(id), journal_id INTEGER REFERENCES journal(id),
 paper_id INTEGER REFERENCES paper(id), kind TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE,
 payload TEXT NOT NULL, checkpoint TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'queued',
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until INTEGER, fence INTEGER NOT NULL DEFAULT 0,
 completed_count INTEGER NOT NULL DEFAULT 0, source_count INTEGER NOT NULL DEFAULT 0,
 new_count INTEGER NOT NULL DEFAULT 0, duplicate_count INTEGER NOT NULL DEFAULT 0,
 error_code TEXT, updated_at INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX task_kind_state ON task(kind,state,next_attempt_at);
CREATE INDEX filtered_listing ON paper_listing(paper_id,journal_id) WHERE decision='exclude';
CREATE INDEX task_claim ON task(kind,id,next_attempt_at,lease_until) WHERE state IN ('queued','retry','running');
CREATE INDEX task_ready ON task(state,next_attempt_at,kind,id);
CREATE INDEX task_paper ON task(paper_id,kind,state);
CREATE INDEX task_run ON task(run_id,kind,state);
CREATE TABLE operation (
 token TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, result TEXT NOT NULL,
 created_at INTEGER NOT NULL, expires_at INTEGER
);
CREATE TABLE archive_item (
 operation_token TEXT NOT NULL REFERENCES operation(token), paper_id INTEGER NOT NULL REFERENCES paper(id),
 PRIMARY KEY(operation_token,paper_id)
);
PRAGMA user_version=1;
`;
