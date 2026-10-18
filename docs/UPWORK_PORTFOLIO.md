# Upwork portfolio entry: Policy Copilot

Paste these fields into Upwork > Profile > Portfolio > Add project. Keep them in English: Upwork clients
search and read in English.

## Project title (max 70 chars)

Policy Copilot: AI HR Assistant, Cited Answers & Exact Calculations

## Your role

Founder & AI Engineer: product, architecture, retrieval, rules engine, deployment

## Project description (Upwork field, under 600 characters)

Policy Copilot is an AI assistant I built and run for HR, Finance and Compliance teams. Employees ask in
plain English; policy questions get answers cited to the exact handbook section, while leave balances,
notice periods, overtime and expense limits are calculated by a tested rules engine, never guessed by the
AI. Out-of-scope questions are refused and every answer is audit-logged. 50-case quality gate in CI.
FastAPI, PostgreSQL + pgvector, OpenAI or Claude, Docker.

## Skills (tags)

Retrieval Augmented Generation · AI Chatbot · LLM · FastAPI · PostgreSQL · pgvector · OpenAI API ·
Anthropic Claude · Python · Docker · REST API · SaaS

## Media (in this order)

All images are 2880x1800, in `app/static/img/` (and the landing shot in the delivered files).

1. `answer-rule.png`: overtime pay calculated with the steps and the policy it implements (cover image)
2. `landing-hero.png`: the product landing page
3. `answer-rag.png`: cited answer with its sources and match scores
4. `audit.png`: audit log with routing outcomes and latency
5. `rules.png`: calculations available directly (also as an API for HRIS / payroll)
6. `knowledge-base.png`: indexed documents and retrieval inspector
7. Links: https://policy-copilot-5s7n.onrender.com (live product) and https://github.com/Dioumadev221/policy-copilot

## Longer case study (GitHub, LinkedIn, or a proposal attachment)

**Problem.** HR and finance teams answer the same policy questions every day: how many days do I have
left, what's my notice period, will this expense be reimbursed. A plain RAG chatbot handles the wording
questions but makes up numbers, and a wrong leave balance or overtime payout is a payroll and legal
problem.

**Product.** Two engines behind one question box. A router (LLM tool calling, with an offline fallback)
sends calculations to a deterministic rules engine: typed inputs, versioned policy parameters, step-by-step
output, assumptions made explicit, and a citation to the policy section each rule implements. Everything
else goes to a RAG pipeline: structure-aware chunking that never splits tables, hybrid retrieval (pgvector +
Postgres full-text) fused with Reciprocal Rank Fusion, reranking, a relevance gate that refuses
out-of-scope questions before any LLM call, and citation verification that drops any source the model
invented. A web app (ask, knowledge base, rules, audit log, API) and a landing page sit on top.

**Results on the demo policy set.**
- 21/21 calculations exact, right source in the top 5 for every retrieval case, 7/7 out-of-scope
  questions refused, 100% routing accuracy. The 50-case eval runs in CI and blocks any regression.
- 53 automated tests, including end-to-end HTTP tests against real Postgres + pgvector.
- One Docker image + Postgres: runs in the client's own cloud, with OpenAI, Claude, or fully offline.

**Stack.** Python, FastAPI, PostgreSQL 16, pgvector, psycopg 3, OpenAI, Anthropic Claude, Cohere Rerank
(optional), Pydantic, Docker, GitHub Actions.

## How to use this project in proposals

For any "RAG chatbot", "chat with documents", "HR bot", "policy/compliance assistant" or "AI that must not
hallucinate" job, open with:

> I run Policy Copilot, an assistant that splits questions into "answer from the documents, with
> citations" and "calculate exactly with a rules engine", because a chatbot that guesses a number is a
> liability. You can try it here: https://policy-copilot-5s7n.onrender.com/app For your project I'd start with the same split: which of
> your answers must be exact?

Then ask one specific question about their data. Short proposals with a working link get answers.

What not to claim: no client names, user counts or "deployed at company X". The demo data is fictional
(Helios Robotics) and the product says so. What you can say truthfully: you designed, built, deployed and
operate it, and you can deploy the same thing on their documents.
