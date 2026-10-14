-- Applied idempotently at startup. {dim} is replaced by EMBEDDING_DIM.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL UNIQUE,         -- file name or upload name
    title       TEXT NOT NULL,
    sha256      TEXT NOT NULL,                -- content hash: re-ingesting identical content is a no-op
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id            BIGSERIAL PRIMARY KEY,
    document_id   BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord           INT NOT NULL,
    section_path  TEXT NOT NULL,               -- "Leave Policy > Annual Leave > Carryover"
    content       TEXT NOT NULL,
    token_count   INT NOT NULL,
    embedding     vector({dim}) NOT NULL,
    tsv           tsvector GENERATED ALWAYS AS (
                      setweight(to_tsvector('english', section_path), 'A') ||
                      setweight(to_tsvector('english', content), 'B')
                  ) STORED
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_document_id ON chunks (document_id);

-- Audit trail: every answer is traceable to its route, rule version and sources.
CREATE TABLE IF NOT EXISTS query_log (
    id          BIGSERIAL PRIMARY KEY,
    question    TEXT NOT NULL,
    route       TEXT NOT NULL,                 -- rule | rag | refused | needs_input
    rule_name   TEXT,
    answer      TEXT NOT NULL,
    sources     JSONB NOT NULL DEFAULT '[]',
    latency_ms  INT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
