-- Apply through db/migrate.sql so DDL and its ledger entry commit together.
CREATE TABLE kai.catalog_assets (
    asset_id UUID PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('clickhouse', 'document')),
    source_ref TEXT NOT NULL CHECK (source_ref ~ '[^[:space:]]'),
    title TEXT NOT NULL,
    verified_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(verified_metadata) = 'object'),
    proposed_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(proposed_metadata) = 'object'),
    approval_status TEXT NOT NULL DEFAULT 'pending',
    source_version TEXT,
    extractor_version TEXT,
    model_id TEXT,
    indexed_embedding_version TEXT,
    acl_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_ref)
);

CREATE TABLE kai.metadata_reviews (
    review_id UUID PRIMARY KEY,
    asset_id UUID NOT NULL REFERENCES kai.catalog_assets(asset_id),
    reviewer_principal UUID NOT NULL REFERENCES kai.ai_principals(principal_id),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'corrected')),
    approved_payload JSONB CHECK (jsonb_typeof(approved_payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX metadata_reviews_asset_created_idx
    ON kai.metadata_reviews (asset_id, created_at);

CREATE TABLE kai.ingestion_jobs (
    job_id UUID PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('clickhouse', 'document')),
    source_ref TEXT NOT NULL CHECK (source_ref ~ '[^[:space:]]'),
    source_version TEXT NOT NULL CHECK (source_version ~ '[^[:space:]]'),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'dead_letter')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_until TIMESTAMPTZ,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_ref, source_version)
);
CREATE INDEX ingestion_jobs_available_idx
    ON kai.ingestion_jobs (available_at, job_id) WHERE status IN ('queued', 'failed');
CREATE INDEX ingestion_jobs_lease_idx
    ON kai.ingestion_jobs (locked_until, job_id) WHERE status = 'running';

-- Runtime permissions are granted by the consuming service's deployment,
-- after its worker/reviewer/ACL contract is implemented.
REVOKE ALL ON kai.catalog_assets, kai.metadata_reviews, kai.ingestion_jobs FROM PUBLIC;
