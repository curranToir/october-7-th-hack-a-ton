import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from rag.config import PROJECT_ROOT, settings


def connect() -> psycopg.Connection:
    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
    try:
        register_vector(conn)
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def migrate() -> list[str]:
    applied = []
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
        for path in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
            with conn.transaction():
                if conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s", (path.stem,)
                ).fetchone():
                    continue
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
                )
            applied.append(path.stem)
    return applied
