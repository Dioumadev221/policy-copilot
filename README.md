<p align="center"><img src="app/static/logo.svg" width="56" alt="Policy Copilot"></p>

<h1 align="center">Policy Copilot</h1>

<p align="center"><b>Policy answers your employees can trust. Down to the last cent.</b><br>
An AI assistant for HR, Finance and Compliance teams: cited answers from your handbook, exact calculations from a rules engine, and a full audit trail.</p>

<p align="center">
<a href="#run-it">Run it</a> · <a href="#evals-the-regression-gate">Quality gate</a> · <a href="#api">API</a> · <a href="docs/DEPLOY_FR.md">Deploy</a>
</p>

![Policy Copilot: guaranteed calculation with steps and the policy it implements](app/static/img/answer-rule.png)

## What it does

Employees ask HR the same questions every day. A chatbot that answers "how many days of leave do I have
left?" with a plausible guess is a payroll and legal liability. Policy Copilot routes every question to the
right engine:

| Question | Engine | What the employee gets |
|---|---|---|
| "How long is parental leave for the secondary caregiver?" | **From your documents** (hybrid retrieval, rerank, cited generation) | A short answer, every sentence cited to a policy section |
| "I worked 52 h at $30/h plus 8 h on a public holiday. What's my pay?" | **Calculated, guaranteed** (typed, versioned, unit-tested rules) | The exact figure, the step-by-step calculation, assumptions, and the policy section it implements |
| "What's the stock option vesting schedule?" | **Not in your documents** | A clear refusal and who to contact. No guessing |
| "What's my leave balance? I took 4 days." | **Needs more detail** | Asks for the start date instead of inventing one |

The AI routes and writes; **it never computes a number that has to be right**.

| Cited answer with sources | Audit log |
|---|---|
| ![Cited answer](app/static/img/answer-rag.png) | ![Audit log](app/static/img/audit.png) |
| **Rules you can run directly** | **Knowledge base and retrieval inspector** |
| ![Rules](app/static/img/rules.png) | ![Knowledge base](app/static/img/knowledge-base.png) |

The product ships with a landing page (`/`), the web app (`/app`) and a REST API (`/docs`). It runs
in your own cloud (one Docker image + Postgres), with OpenAI, Claude, or fully offline.

The demo workspace uses sample policies for **Helios Robotics**, a fictional company (leave, termination,
working time, travel and expenses, remote work, code of conduct and data protection). It is tagged
"Sample data" in the app.

---

## Architecture

```
                         ┌────────────────────────── POST /ask ───────────────────────────┐
                         │                                                                 │
question ──► Router ─────┤ LLM tool calling (OpenAI / Claude)  or  offline keyword+regex     │
                         │                                                                 │
          ┌──────────────┴─────────────┐                                ┌──────────────────┴──────────┐
          │ rule matched                │                                │ policy question              │
          ▼                             │                                ▼                              │
  Pydantic validation ── missing? ──► "needs_input"            Hybrid retrieval (Postgres)              │
          │                                                     ├─ pgvector HNSW cosine                 │
  Rules engine (policy_params.yaml, versioned)                  ├─ full-text tsvector (weighted)        │
  → result + steps + warnings + assumptions                     └─ Reciprocal Rank Fusion               │
  → citation to the implemented policy section                  Rerank (IDF coverage / Cohere Rerank)   │
                                                                Refusal gate (min relevance)            │
                                                                Generate with [n] citations             │
                                                                Citation verification (drop invalid,    │
                                                                fall back to extractive if ungrounded)  │
          └──────────────────────────────┬──────────────────────────────────────────┘
                                         ▼
                           query_log (route, rule, sources, latency) → /stats
```

**Stack:** Python 3.12 · FastAPI · PostgreSQL 16 + pgvector (HNSW) · psycopg 3 · OpenAI / Anthropic Claude
(pluggable) · optional Cohere Rerank · Docker · GitHub Actions.

## Design decisions (and why)

- **Rules for anything numeric or binding.** An LLM that is right 97% of the time on a payout is wrong on
  3 payslips in 100. The rules engine (`app/rules/`) is plain Python over Pydantic-typed inputs; every
  parameter (25 days/year, 125% from 40 h, 75 USD meal cap in Tier 1 cities…) lives in
  [`policy_params.yaml`](app/rules/policy_params.yaml) with the policy section it implements, so HR changes
  a number in a reviewed PR, not in a prompt.
- **The LLM only extracts and routes.** With a key configured, rules are exposed as tools; the model picks
  one and fills arguments. Arguments are re-validated; unknown values are never guessed (the prompt
  forbids it, the validator enforces it, and defaults that were applied are shown as *assumptions*).
- **Hybrid retrieval.** Embeddings miss exact terms ("PIP", "Tier 1", "0.6 FTE"); keyword search misses
  paraphrases. Both run in one Postgres, fused with RRF (no score calibration needed).
- **Structure-aware chunking.** Split on headings, never split tables, pack paragraphs to a token budget
  with sentence overlap, and embed `section path + text` so "Carryover" is embedded as
  "Leave Policy > Annual Leave > Carryover".
- **Refusing is a feature.** A relevance gate based on IDF-weighted term coverage declines out-of-scope
  questions before any LLM call (cheaper, and no hallucination). Answers whose citations don't point to a
  retrieved passage are not shown.
- **Idempotent ingestion.** Documents are fingerprinted on content *and* indexing config (chunker version,
  embedding model, dimension); changing either re-indexes on next ingest instead of leaving stale vectors.
- **Auditability.** Every answer is logged with its route, rule, parameter version, sources and latency.

## Evals: the regression gate

`python -m evals.run` runs 50 cases through the real pipeline and fails CI if a metric drops below its
threshold ([`evals/dataset.yaml`](evals/dataset.yaml)):

| Metric | Threshold | Offline mode (no API key) |
|---|---|---|
| Route accuracy (rule / RAG / refuse / ask) | 0.95 | **1.00** |
| Rule exact match (guaranteed answers) | **1.00** | **1.00** (21/21) |
| Retrieval hit@1 / hit@5 / MRR | hit@5 ≥ 0.90 | **1.00 / 1.00 / 1.00** |
| Answer fact recall | 0.85 | **1.00** |
| Refusal accuracy | 0.85 | **1.00** (7/7 out-of-scope declined, 0 false refusals) |

Honest reading: this set was written alongside the system, so it's a **regression gate**, not a
benchmark. A second set, [`evals/paraphrase.yaml`](evals/paraphrase.yaml), uses questions that share almost
no words with the policies ("when my mum dies", "paste customer data into ChatGPT"). In offline lexical
mode retrieval still finds the right section in the top 5 for 7/8, but the gate (correctly, for lexical
scoring) refuses them: the safe failure mode. That set exists to measure the gain from semantic
embeddings (`EMBEDDING_PROVIDER=openai`) and to tune `MIN_RELEVANCE` per embedding model.

Plus 53 pytest tests: rule edge cases (anniversary month, mid-month joiners, inclusive thresholds, carryover
expiry, 60 h breach), chunker invariants, extraction, citation verification, and end-to-end HTTP tests
against a real Postgres + pgvector.

## Run it

```bash
cp .env.example .env          # works as-is: offline mode, no key needed
docker compose up --build     # Postgres+pgvector and the API
open http://localhost:8000    # landing page · /app web app · /docs OpenAPI
```

Branding: `WORKSPACE_NAME`, `DEMO_MODE` (shows the "Sample data" tag) and `CONTACT_URL` (where
"Talk to us" buttons point).

Production quality: set `LLM_PROVIDER=openai`, `EMBEDDING_PROVIDER=openai`, `OPENAI_API_KEY=...` (or
`LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`), then re-run the evals and tune `MIN_RELEVANCE`.

```bash
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/rag
pytest -q && python -m evals.run
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/ask` | `{"question": "..."}` → route, answer, `guaranteed`, `grounded`, rule trace, citations |
| POST | `/rules/{name}/evaluate` | Call a rule directly with JSON (for HRIS / payroll / Slack bots), no LLM involved |
| GET | `/rules` | Rules with their JSON Schemas and the parameter version |
| GET | `/search?q=` | Retrieval debugging: passages with vector similarity, ranks, relevance |
| POST / DELETE | `/documents` | Upload `.md` `.txt` `.pdf` (idempotent) / remove (protected by `X-API-Key` when `API_KEY` is set) |
| GET | `/documents`, `/stats`, `/health` | Indexed docs, answers by route with avg/p95 latency, health |
| GET | `/activity`, `/meta` | Recent answers from the audit log; workspace info for the web app |

## Adapting it to a client

1. Drop their documents into `data/policies/` (or upload through `/documents`).
2. Write the 5–20 questions whose answers must be exact; each becomes a rule + params + tests.
3. Add their real questions to `evals/dataset.yaml`; CI then protects every later change.

## Project layout

```
app/
  main.py               FastAPI app and endpoints
  assistant.py          routing, rule path, RAG path, citation checks, audit log
  ingest/               loaders (md/txt/pdf), structure-aware chunker, idempotent pipeline
  retrieval/            embeddings (OpenAI / offline hashing), hybrid search + RRF + rerank
  rules/                engine, 5 rules, policy_params.yaml, offline extractor
  llm/providers.py      OpenAI and Anthropic adapters (tool-calling router + cited generation)
  static/               landing page, web app, logo, fonts, screenshots
evals/                  regression + paraphrase eval sets and runner
tests/                  unit, rules, end-to-end API tests
sql/schema.sql          documents, chunks (vector + tsvector), query_log
```

License: MIT.
