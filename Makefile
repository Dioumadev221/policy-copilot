.PHONY: up down test eval eval-paraphrase dev

up:            ## start Postgres + API on http://localhost:8000
	docker compose up --build -d

down:
	docker compose down

dev:           ## run the API locally against DATABASE_URL
	uvicorn app.main:app --reload

test:
	pytest -q

eval:          ## regression gate (exit 1 on threshold breach)
	python -m evals.run --json eval-report.json

eval-paraphrase:
	python -m evals.run --dataset evals/paraphrase.yaml
