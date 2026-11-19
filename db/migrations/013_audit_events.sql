-- Data access audit as a table, so the Admin Panel log pages by cursor and the "needs attention" summary is an
-- indexed aggregate instead of a file scan. The MCP still writes audit.jsonl (backup, SIEM); this is the query copy.
-- Same fields as the audit line: never SQL text, result values or secrets.
CREATE TABLE kai.audit_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    librechat_user TEXT NOT NULL CHECK (librechat_user ~ '^[0-9a-f]{24}$'),
    tool TEXT CHECK (length(tool) <= 64),
    ch_user TEXT CHECK (length(ch_user) <= 256),
    database TEXT CHECK (length(database) <= 256),
    outcome TEXT NOT NULL CHECK (length(outcome) <= 32),
    error_code TEXT CHECK (length(error_code) <= 64),
    request_id TEXT CHECK (length(request_id) <= 64),
    rows BIGINT,
    truncated BOOLEAN,
    duration_ms INTEGER
);
-- Pages are newest first by id (insert order); filters use the matching (column, id) index. ts drives the period
-- bounds, the counts and retention deletes.
-- ponytail: one table, batched retention deletes; partition by month if a 90-day window passes ~50M rows.
CREATE INDEX audit_events_ts_idx ON kai.audit_events (ts);
CREATE INDEX audit_events_user_idx ON kai.audit_events (librechat_user, id DESC);
CREATE INDEX audit_events_outcome_idx ON kai.audit_events (outcome, id DESC);
REVOKE ALL ON kai.audit_events FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kai_catalog') THEN
        GRANT SELECT, INSERT, DELETE ON kai.audit_events TO kai_catalog;
    END IF;
END
$$;
