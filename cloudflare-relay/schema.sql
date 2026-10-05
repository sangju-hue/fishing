CREATE TABLE IF NOT EXISTS requests (
 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, message_hash TEXT NOT NULL,
 message TEXT, result TEXT, created_at INTEGER NOT NULL, done_at INTEGER
);
CREATE INDEX IF NOT EXISTS requests_pending ON requests(result, created_at);
CREATE TABLE IF NOT EXISTS rate_limits (
 key TEXT PRIMARY KEY, count INTEGER NOT NULL, bucket INTEGER NOT NULL
);
