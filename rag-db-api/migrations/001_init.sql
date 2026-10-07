CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE documents (
  id bigserial PRIMARY KEY,
  source text NOT NULL,
  title text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  content_sha256 text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE chunks (
  id bigserial PRIMARY KEY,
  document_id bigint NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ord int NOT NULL,
  content text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  embedding halfvec(2048) NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  UNIQUE (document_id, ord)
);
CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv);
