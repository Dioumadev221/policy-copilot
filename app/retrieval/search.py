"""Hybrid retrieval: pgvector (semantic) + Postgres full-text (exact terms), fused with
Reciprocal Rank Fusion, then reranked.

Why hybrid: embeddings miss exact identifiers ("PIP", "0.6 FTE", "Tier 1") and keyword
search misses paraphrases ("holiday days" vs "annual leave"). RRF needs no score
calibration between the two, so it is robust when the embedding model changes.
"""

import logging
import math
from dataclasses import dataclass, field

import httpx

from app.config import get_settings
from app.db import pool
from app.retrieval.embeddings import get_embedder
from app.retrieval.text import keywords, terms

log = logging.getLogger(__name__)
RRF_K = 60


@dataclass
class Passage:
    chunk_id: int
    source: str
    title: str
    section_path: str
    content: str
    vector_sim: float = 0.0
    rrf: float = 0.0
    relevance: float = 0.0
    ranks: dict[str, int] = field(default_factory=dict)


_SELECT = """
    SELECT c.id, d.source, d.title, c.section_path, c.content, 1 - (c.embedding <=> %(q)s::vector) AS vector_sim
    FROM chunks c JOIN documents d ON d.id = c.document_id
"""


def _vector_search(conn, qvec: list[float], n: int) -> list[dict]:
    return conn.execute(
        _SELECT + " ORDER BY c.embedding <=> %(q)s::vector LIMIT %(n)s", {"q": str(qvec), "n": n}
    ).fetchall()


def _keyword_search(conn, qvec: list[float], query: str, n: int) -> list[dict]:
    words = sorted({w for w in keywords(query) if len(w) > 1})
    if not words:
        return []
    # OR-query (Postgres stems each word); ts_rank_cd rewards chunks matching several terms close together.
    tsq = " | ".join(words)
    return conn.execute(
        _SELECT
        + """, to_tsquery('english', %(tsq)s) AS tq
        WHERE c.tsv @@ tq ORDER BY ts_rank_cd(c.tsv, tq, 32) DESC LIMIT %(n)s""",
        {"q": str(qvec), "tsq": tsq, "n": n},
    ).fetchall()


_df_cache: dict = {"key": None, "df": {}, "n": 0}


def _doc_freq(conn) -> tuple[dict[str, int], int]:
    """Term document-frequencies over all chunks, cached until the corpus changes."""
    key = tuple(conn.execute("SELECT count(*) AS n, coalesce(max(id), 0) AS m FROM chunks").fetchone().values())
    if _df_cache["key"] != key:
        df: dict[str, int] = {}
        for row in conn.execute("SELECT section_path || ' ' || content AS t FROM chunks"):
            for t in set(terms(row["t"])):
                df[t] = df.get(t, 0) + 1
        _df_cache.update(key=key, df=df, n=key[0])
    return _df_cache["df"], _df_cache["n"]


def _heuristic_rerank(query: str, passages: list[Passage], df: dict[str, int], n_docs: int) -> None:
    """Feature reranker: IDF-weighted query-term coverage + section-title match + semantic
    similarity + fused rank.

    IDF-weighted coverage is what makes the refusal gate meaningful: "stock option vesting
    schedule" shares the common word "schedule" with the working-time policy, but none of
    the rare, informative words, so it scores low and the assistant declines to answer.
    """
    q = set(terms(query))
    if not q:
        return
    idf = {t: math.log((n_docs + 1) / (df.get(t, 0) + 0.5)) for t in q}
    total = sum(idf.values()) or 1.0
    max_rrf = max(p.rrf for p in passages) or 1.0
    for p in passages:
        body = set(terms(p.content)) | set(terms(p.section_path))
        title = set(terms(p.section_path))
        coverage = sum(idf[t] for t in q & body) / total
        title_hit = sum(idf[t] for t in q & title) / total
        p.relevance = round(
            0.55 * coverage + 0.15 * title_hit + 0.15 * max(p.vector_sim, 0) + 0.15 * p.rrf / max_rrf, 4
        )


def _cohere_rerank(query: str, passages: list[Passage], api_key: str) -> bool:
    try:
        r = httpx.post(
            "https://api.cohere.com/v2/rerank",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "rerank-v3.5",
                "query": query,
                "documents": [f"{p.section_path}\n{p.content}" for p in passages],
            },
            timeout=15,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:  # degrade gracefully, never fail the request on a reranker outage
        log.warning("cohere rerank failed, falling back to heuristic: %s", exc)
        return False
    for item in r.json()["results"]:
        passages[item["index"]].relevance = round(item["relevance_score"], 4)
    return True


def search(query: str, top_k: int | None = None) -> list[Passage]:
    s = get_settings()
    top_k = top_k or s.top_k
    qvec = get_embedder().embed([query])[0]
    with pool().connection() as conn:
        lists = {
            "vector": _vector_search(conn, qvec, s.retrieval_candidates),
            "keyword": _keyword_search(conn, qvec, query, s.retrieval_candidates),
        }
        df, n_docs = _doc_freq(conn)

    fused: dict[int, Passage] = {}
    for name, rows in lists.items():
        for rank, row in enumerate(rows, start=1):
            p = fused.setdefault(
                row["id"],
                Passage(
                    row["id"], row["source"], row["title"], row["section_path"], row["content"],
                    vector_sim=float(row["vector_sim"]),
                ),
            )
            p.rrf += 1.0 / (RRF_K + rank)
            p.ranks[name] = rank
    if not fused:
        return []

    candidates = sorted(fused.values(), key=lambda p: p.rrf, reverse=True)[: s.retrieval_candidates]
    if not (s.cohere_api_key and _cohere_rerank(query, candidates, s.cohere_api_key)):
        _heuristic_rerank(query, candidates, df, n_docs)
    candidates.sort(key=lambda p: p.relevance, reverse=True)
    return candidates[:top_k]
