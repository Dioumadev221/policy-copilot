import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.db import pool
from app.ingest.chunker import chunk_markdown
from app.ingest.loaders import SUPPORTED, load_text, title_from_filename
from app.retrieval.embeddings import get_embedder

log = logging.getLogger(__name__)
CHUNKER_VERSION = "2"  # bump when chunking logic changes


@dataclass
class IngestResult:
    source: str
    title: str
    status: str  # created | updated | unchanged
    chunks: int


def ingest_bytes(source: str, data: bytes) -> IngestResult:
    """Idempotent: same content -> no-op; changed content -> chunks replaced atomically."""
    settings = get_settings()
    text = load_text(source, data)
    # The fingerprint covers the indexing config too: changing the chunker or the embedding
    # model re-indexes a document on next ingest instead of silently keeping stale vectors.
    fingerprint = "|".join([
        text, CHUNKER_VERSION, str(settings.chunk_max_tokens), str(settings.chunk_overlap_tokens),
        settings.embedding_provider, settings.openai_embedding_model, str(settings.embedding_dim),
    ])
    sha = hashlib.sha256(fingerprint.encode()).hexdigest()

    with pool().connection() as conn:
        existing = conn.execute(
            "SELECT id, sha256, title FROM documents WHERE source = %s", (source,)
        ).fetchone()
        if existing and existing["sha256"] == sha:
            n = conn.execute(
                "SELECT count(*) AS n FROM chunks WHERE document_id = %s", (existing["id"],)
            ).fetchone()["n"]
            return IngestResult(source, existing["title"], "unchanged", n)

    title, chunks = chunk_markdown(
        text, title_from_filename(source), settings.chunk_max_tokens, settings.chunk_overlap_tokens
    )
    if not chunks:
        raise ValueError(f"{source}: no text to index")
    # Embed outside the transaction: it is the slow, network-bound step.
    vectors = get_embedder().embed([c.embedding_text for c in chunks])

    with pool().connection() as conn, conn.transaction():
        if existing:
            conn.execute("DELETE FROM documents WHERE id = %s", (existing["id"],))
        doc_id = conn.execute(
            "INSERT INTO documents (source, title, sha256) VALUES (%s, %s, %s) RETURNING id",
            (source, title, sha),
        ).fetchone()["id"]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (document_id, ord, section_path, content, token_count, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s::vector)",
                [
                    (doc_id, c.ord, c.section_path, c.content, c.token_count, str(v))
                    for c, v in zip(chunks, vectors)
                ],
            )
    status = "updated" if existing else "created"
    log.info("ingested %s (%s, %d chunks)", source, status, len(chunks))
    return IngestResult(source, title, status, len(chunks))


def ingest_directory(path: str | Path) -> list[IngestResult]:
    files = sorted(p for p in Path(path).iterdir() if p.suffix.lower() in SUPPORTED)
    return [ingest_bytes(p.name, p.read_bytes()) for p in files]


def delete_document(source: str) -> bool:
    with pool().connection() as conn:
        return conn.execute("DELETE FROM documents WHERE source = %s", (source,)).rowcount > 0
