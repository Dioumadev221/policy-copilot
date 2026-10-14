from pathlib import Path

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def _configure(conn: Connection) -> None:
    register_vector(conn)


def init_db() -> None:
    """Create the pool and apply the schema. Safe to call more than once."""
    global _pool
    if _pool is not None:
        return
    settings = get_settings()
    # The extension must exist before register_vector can look up the type.
    with Connection.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(SCHEMA_PATH.read_text().replace("{dim}", str(settings.embedding_dim)))
        _check_dim(conn, settings.embedding_dim)
    _pool = ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        configure=_configure,
        kwargs={"row_factory": dict_row},
        open=True,
    )


def _check_dim(conn: Connection, expected: int) -> None:
    row = conn.execute(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
    ).fetchone()
    if row and row[0] != expected:
        raise RuntimeError(
            f"chunks.embedding has dimension {row[0]} but EMBEDDING_DIM={expected}. "
            "Switching embedding models needs a re-index: drop the chunks/documents tables and restart."
        )


def pool() -> ConnectionPool:
    if _pool is None:
        init_db()
    assert _pool is not None
    return _pool


def close_db() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
