"""End-to-end through HTTP against a real Postgres + pgvector (skipped if unreachable)."""

import os

import psycopg
import pytest

DB = os.environ.get("DATABASE_URL")


def _db_up() -> bool:
    if not DB:
        return False
    try:
        psycopg.connect(DB, connect_timeout=2).close()
        return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="needs DATABASE_URL pointing at Postgres with pgvector")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health_after_seed(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["chunks"] > 30


def test_rule_answer_is_guaranteed_and_cited(client):
    r = client.post("/ask", json={"question": "I worked 45 hours this week and my hourly rate is $22. What is my pay?"}).json()
    assert r["route"] == "rule" and r["guaranteed"] is True
    assert r["rule"]["result"]["total_pay"] == 1017.5
    assert r["citations"][0]["section"].endswith("Overtime Rates")


def test_rag_answer_is_grounded(client):
    r = client.post("/ask", json={"question": "How long is parental leave for the secondary caregiver?"}).json()
    assert r["route"] == "rag" and r["grounded"] and r["guaranteed"] is False
    assert "6 weeks" in r["answer"] and "[1]" in r["answer"]


def test_out_of_scope_is_refused(client):
    r = client.post("/ask", json={"question": "What is the 401k employer match?"}).json()
    assert r["route"] == "refused" and r["citations"] == []


def test_structured_rule_endpoint(client):
    r = client.post("/rules/notice_period/evaluate",
                    json={"level": "executive", "start_date": "2018-06-01", "as_of": "2025-06-02"})
    assert r.status_code == 200 and r.json()["result"]["notice_months"] == 6
    bad = client.post("/rules/notice_period/evaluate", json={"level": "intern"})
    assert bad.status_code == 422


def test_upload_is_idempotent_and_searchable(client):
    doc = b"# Parking Policy\n\n## Spaces\n\nEmployees get free parking in the north garage, level 2."
    first = client.post("/documents", files={"file": ("parking.md", doc, "text/markdown")}).json()
    again = client.post("/documents", files={"file": ("parking.md", doc, "text/markdown")}).json()
    assert first["status"] in {"created", "updated"} and again["status"] == "unchanged"
    r = client.post("/ask", json={"question": "Where is the free parking garage for employees?"}).json()
    assert r["route"] == "rag" and "north garage" in r["answer"]
    assert client.delete("/documents/parking.md").status_code == 200


def test_stats_logs_every_answer(client):
    assert client.get("/stats").json()["total"] >= 4
