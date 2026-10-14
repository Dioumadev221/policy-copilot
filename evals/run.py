"""Regression evals. Usage:  python -m evals.run [--dataset evals/dataset.yaml] [--json out.json]

Seeds the policy corpus, runs every case through the real pipeline (same code path as the
API), prints a per-metric report and exits 1 if any threshold is missed, so CI blocks a
change that makes retrieval, routing or a guaranteed answer worse.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from app.assistant import ask
from app.config import get_settings
from app.db import close_db, init_db
from app.ingest.pipeline import ingest_directory
from app.retrieval.search import search


def _norm(s: str) -> str:
    return " ".join(s.lower().replace(",", "").split())


def run(dataset: Path) -> dict:
    spec = yaml.safe_load(dataset.read_text())
    init_db()
    ingest_directory(get_settings().seed_dir)

    tally = {k: [0, 0] for k in ("route", "rule", "hit1", "hit5", "facts", "refusal")}
    mrr, failures, rows = [], [], []

    def score(metric: str, ok: bool) -> bool:
        tally[metric][0] += ok
        tally[metric][1] += 1
        return ok

    for case in spec["cases"]:
        r = ask(case["q"])
        ok = score("route", r.route == case["route"])
        detail = ""
        if case["route"] == "rule":
            got = r.rule.result if r.rule else {}
            exact = r.route == "rule" and r.rule.name == case["rule"] and all(
                got.get(k) == v for k, v in case["result"].items()
            )
            ok &= score("rule", exact)
            detail = json.dumps(got, default=str)
        elif case["route"] == "needs_input":
            ok &= set(case["missing"]) <= set(r.missing_fields)
            detail = str(r.missing_fields)
        elif case["route"] == "rag":
            # Retrieval is scored on the retriever itself, independent of the refusal gate.
            sections = [p.section_path for p in search(case["q"], top_k=5)]
            rank = next((i for i, s in enumerate(sections, 1) if case["section"].lower() in s.lower()), None)
            score("hit1", rank == 1)
            ok &= score("hit5", rank is not None and rank <= 5)
            mrr.append(1 / rank if rank else 0.0)
            ok &= score("facts", all(_norm(f) in _norm(r.answer) for f in case["facts"]))
            detail = f"rank={rank}"
        if case["route"] == "refused" or r.route == "refused":
            score("refusal", (r.route == "refused") == (case["route"] == "refused"))
        rows.append({"id": case["id"], "ok": bool(ok), "route": r.route, "answer": r.answer, "detail": detail})
        if not ok:
            failures.append(rows[-1])
    close_db()

    def ratio(k):
        n, d = tally[k]
        return round(n / d, 3) if d else None

    metrics = {
        "route_accuracy": ratio("route"),
        "rule_exact_match": ratio("rule"),
        "retrieval_hit_at_1": ratio("hit1"),
        "retrieval_hit_at_5": ratio("hit5"),
        "retrieval_mrr": round(sum(mrr) / len(mrr), 3) if mrr else None,
        "answer_fact_recall": ratio("facts"),
        "refusal_accuracy": ratio("refusal"),
        "cases": len(spec["cases"]),
    }
    breaches = {k: (metrics[k], v) for k, v in spec["thresholds"].items() if metrics[k] is None or metrics[k] < v}
    return {"metrics": metrics, "breaches": breaches, "failures": failures, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="evals/dataset.yaml", type=Path)
    ap.add_argument("--json", type=Path, help="write the full report here")
    args = ap.parse_args()
    s = get_settings()
    report = run(args.dataset)

    print(f"\nEval: llm={s.llm_provider} embeddings={s.embedding_provider}\n")
    for k, v in report["metrics"].items():
        print(f"  {k:<22} {v}")
    for f in report["failures"]:
        print(f"\n  FAIL {f['id']} -> {f['route']} {f['detail']}\n       {f['answer'][:160]}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str))
    if report["breaches"]:
        print("\nThreshold breaches:", report["breaches"])
        sys.exit(1)
    print("\nAll thresholds met.")


if __name__ == "__main__":
    main()
