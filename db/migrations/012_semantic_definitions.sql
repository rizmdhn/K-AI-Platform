-- Apply through db/migrate.sql. Semantic layer: metric definitions and verified queries the CH agent reuses.
-- Field names follow OpenMetadata (Metric: expression, metricType, unitOfMeasurement; Glossary: synonyms) so a later
-- sync with the client's catalog only maps fields; external_id links a row to its catalog entity.
-- Only 'approved' rows reach the agent. Drafts come from the agent (source 'ai') or the Admin Panel ('manual').
CREATE TABLE kai.semantic_definitions (
    definition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('metric', 'verified_query')),
    name TEXT NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
    tables TEXT[] NOT NULL CHECK (cardinality(tables) BETWEEN 1 AND 20),
    definition JSONB NOT NULL CHECK (jsonb_typeof(definition) = 'object'),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected')),
    source TEXT NOT NULL CHECK (source IN ('ai', 'manual', 'catalog')),
    external_id TEXT CHECK (external_id ~ '[^[:space:]]'),
    proposed_by TEXT CHECK (proposed_by ~ '^[0-9a-f]{24}$'),
    updated_by TEXT CHECK (updated_by ~ '^[0-9a-f]{24}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, name)
);
CREATE INDEX semantic_definitions_status_idx ON kai.semantic_definitions (status, kind);
REVOKE ALL ON kai.semantic_definitions FROM PUBLIC;
-- Existing installs already have the catalog role (fresh installs get the grant from grant_catalog_access.sql).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kai_catalog') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON kai.semantic_definitions TO kai_catalog;
    END IF;
END
$$;
