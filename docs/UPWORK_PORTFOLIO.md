# Upwork portfolio entry: Policy Copilot

Paste these fields into Upwork > Profile > Portfolio > Add project. Write in English: Upwork clients
search and read in English.

## Project title (max 70 chars)

AI HR Policy Assistant: RAG + Deterministic Rules Engine (FastAPI)

## Your role

AI & Backend Engineer: architecture, retrieval, rules engine, evals, deployment

## Project description (Upwork field, under 600 characters)

Employees ask HR the same questions all day, and a chatbot that guesses a leave balance or a payout is a liability. I built an assistant that routes each question: policy questions get a RAG answer (pgvector + full-text, reranking) with every sentence cited; calculations (leave, notice, overtime, expenses) go to a tested rules engine that returns the exact figure and its steps. Out-of-scope questions are refused. 50-case eval gate in CI: 100% exact on calculations, 100% retrieval hit@5. FastAPI, Postgres, Docker.

## Skills (tags)

Retrieval Augmented Generation · FastAPI · PostgreSQL · pgvector · OpenAI API · Anthropic Claude · LLM ·
AI Chatbot · Python · Docker · REST API · Prompt Engineering

## Media (in this order)

1. `docs/screenshots/rule.png`: guaranteed answer with the calculation steps (cover image)
2. `docs/screenshots/rag.png`: cited answer with sources and relevance scores
3. `docs/screenshots/refused.png`: out-of-scope question declined
4. `docs/screenshots/rule-dark.png`
5. Links: live demo URL (Render) and the GitHub repo

## Longer case study (GitHub, LinkedIn, or a proposal attachment)

**Problem.** HR and finance teams answer the same policy questions every day: how many days do I have
left, what's my notice period, will this expense be reimbursed. A plain RAG chatbot answers the wording
questions well but makes up numbers, and a wrong leave balance or overtime payout is a payroll and legal
problem.

**Solution.** Two engines behind one question box. A router (LLM tool calling, with an offline fallback)
sends calculations to a deterministic rules engine: typed inputs, versioned policy parameters in YAML,
step-by-step output, assumptions made explicit, and a citation to the policy section each rule implements.
Everything else goes to a RAG pipeline: structure-aware chunking that never splits tables, hybrid retrieval
(pgvector HNSW + Postgres full-text) fused with Reciprocal Rank Fusion, reranking, a relevance gate that
refuses out-of-scope questions before any LLM call, and citation verification that drops any source the
model invented.

**Results.**
- 50-case regression eval runs in CI on every push: 100% exact match on 21 guaranteed calculations,
  100% retrieval hit@5, 100% correct refusals, 100% routing accuracy.
- 51 automated tests, including end-to-end HTTP tests against real Postgres + pgvector.
- Runs fully offline for demos and CI; switches to OpenAI or Claude with one environment variable.
- One `docker compose up` locally; deployed on Render + Neon for free.

**Stack.** Python, FastAPI, PostgreSQL 16, pgvector, psycopg 3, OpenAI, Anthropic Claude, Cohere Rerank
(optional), Pydantic, Docker, GitHub Actions.

## How to use this project in proposals

For any "RAG chatbot", "chat with documents", "HR bot", "policy/compliance assistant" or "AI that must not
hallucinate" job, open with a sentence like:

> I recently built a policy assistant that splits questions into "answer from documents with citations"
> and "calculate exactly with a rules engine", because a chatbot that guesses a number is a liability.
> Live demo: <URL>. For your project I'd start with the same split: which of your answers must be exact?

Then ask one specific question about their data. Short proposals with a live demo get answers.
