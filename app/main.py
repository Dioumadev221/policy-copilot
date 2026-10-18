import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app.assistant import AskResponse, ask
from app.config import get_settings
from app.db import close_db, init_db, pool
from app.ingest.pipeline import delete_document, ingest_bytes, ingest_directory
from app.retrieval.search import search
from app.rules import REGISTRY, get_rule, params

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    init_db()
    if s.seed_on_startup and Path(s.seed_dir).is_dir():
        for r in ingest_directory(s.seed_dir):
            logging.info("seed %s: %s (%d chunks)", r.source, r.status, r.chunks)
    yield
    close_db()


app = FastAPI(
    title="Policy Copilot: RAG + deterministic rules",
    description="Answers policy questions with cited retrieval, and computes guaranteed answers with a rules engine.",
    version="1.0.0",
    lifespan=lifespan,
)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().api_key
    if expected and x_api_key != expected:
        raise HTTPException(401, "Invalid or missing X-API-Key")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


@app.get("/", include_in_schema=False)
def landing() -> HTMLResponse:
    html = (STATIC / "landing.html").read_text(encoding="utf-8")
    if base := get_settings().public_url:
        # Link previews (LinkedIn, Upwork, WhatsApp) need an absolute image URL.
        html = html.replace('content="/static/', f'content="{base.rstrip("/")}/static/')
    return HTMLResponse(html)


@app.get("/app", include_in_schema=False)
def web_app() -> FileResponse:
    return FileResponse(STATIC / "app.html")


@app.get("/meta")
def meta() -> dict[str, Any]:
    """Workspace info for the web app header."""
    s = get_settings()
    with pool().connection() as conn:
        docs = conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"]
    return {"workspace": s.workspace_name, "demo": s.demo_mode, "contact_url": s.contact_url,
            "llm": s.llm_provider, "embeddings": s.embedding_provider, "documents": docs,
            "rules": len(REGISTRY), "params_version": params()["version"]}


@app.get("/activity")
def activity(limit: int = 50) -> list[dict[str, Any]]:
    """Most recent answers from the audit log."""
    with pool().connection() as conn:
        return conn.execute(
            """SELECT id, question, route, rule_name, latency_ms, created_at
               FROM query_log ORDER BY id DESC LIMIT %s""",
            (min(max(limit, 1), 200),),
        ).fetchall()


@app.get("/health")
def health() -> dict[str, Any]:
    with pool().connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
    s = get_settings()
    return {"status": "ok", "chunks": n, "llm": s.llm_provider, "embeddings": s.embedding_provider}


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(body: AskRequest) -> AskResponse:
    return await run_in_threadpool(ask, body.question.strip())


@app.get("/search")
async def search_endpoint(q: str, k: int = 5) -> list[dict[str, Any]]:
    """Retrieval debugging: see exactly what the generator would see, with scores and ranks."""
    passages = await run_in_threadpool(search, q, min(k, 20))
    return [p.__dict__ for p in passages]


@app.get("/rules")
def list_rules() -> dict[str, Any]:
    return {
        "params_version": params()["version"],
        "rules": [{"name": r.name, "title": r.title, "description": r.description, "input_schema": r.schema()}
                  for r in REGISTRY.values()],
    }


@app.post("/rules/{name}/evaluate")
def evaluate_rule(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Structured access for other systems (HRIS, payroll, Slack bot): no LLM involved at all."""
    try:
        outcome = get_rule(name).evaluate(inputs)
    except KeyError:
        raise HTTPException(404, f"Unknown rule {name!r}")
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False))
    return {"rule": name, "params_version": params()["version"], **outcome.__dict__}


@app.get("/documents")
def list_documents() -> list[dict[str, Any]]:
    with pool().connection() as conn:
        return conn.execute(
            """SELECT d.source, d.title, d.created_at, count(c.id) AS chunks
               FROM documents d LEFT JOIN chunks c ON c.document_id = d.id GROUP BY d.id ORDER BY d.source"""
        ).fetchall()


@app.post("/documents", dependencies=[Depends(require_key)])
async def upload_document(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (10 MB max)")
    try:
        result = await run_in_threadpool(ingest_bytes, Path(file.filename or "upload.txt").name, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result.__dict__


@app.delete("/documents/{source}", dependencies=[Depends(require_key)])
def remove_document(source: str) -> dict[str, Any]:
    if not delete_document(source):
        raise HTTPException(404, "Document not found")
    return {"deleted": source}


@app.get("/stats")
def stats() -> dict[str, Any]:
    with pool().connection() as conn:
        rows = conn.execute(
            """SELECT route, count(*) AS n, round(avg(latency_ms)) AS avg_latency_ms,
                      percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
               FROM query_log GROUP BY route ORDER BY n DESC"""
        ).fetchall()
    return {"by_route": rows, "total": sum(r["n"] for r in rows)}
