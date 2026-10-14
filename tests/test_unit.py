from datetime import date

from app.assistant import _units, _verify_citations
from app.ingest.chunker import chunk_markdown, count_tokens
from app.retrieval.embeddings import HashingEmbedder
from app.rules.extract import extract_expense, extract_leave, extract_overtime, heuristic_route

DOC = """# Travel Policy

Intro paragraph.

## Limits

| Category | Tier 1 | Tier 2 |
|---|---|---|
| Meals | 75 USD | 50 USD |

### Hotels

""" + " ".join(f"Sentence number {i} about hotels and nightly rates." for i in range(60))


class TestChunker:
    def test_heading_paths_and_title(self):
        title, chunks = chunk_markdown(DOC, "fallback")
        assert title == "Travel Policy"
        paths = {c.section_path for c in chunks}
        assert "Travel Policy > Limits" in paths
        assert "Travel Policy > Limits > Hotels" in paths

    def test_tables_are_never_split(self):
        _, chunks = chunk_markdown(DOC, "fallback", max_tokens=10)
        table = [c for c in chunks if "| Meals |" in c.content]
        assert len(table) == 1 and "| Category |" in table[0].content

    def test_long_sections_respect_budget_with_overlap(self):
        _, chunks = chunk_markdown(DOC, "fallback", max_tokens=80, overlap_tokens=20)
        hotel = [c for c in chunks if c.section_path.endswith("Hotels")]
        assert len(hotel) > 3
        assert all(c.token_count <= 80 for c in hotel)
        # consecutive chunks share text (overlap)
        last_sentence = hotel[0].content.split(". ")[-1][:30]
        assert last_sentence in hotel[1].content

    def test_embedding_text_carries_context(self):
        _, chunks = chunk_markdown(DOC, "fallback")
        assert chunks[-1].embedding_text.startswith("Travel Policy > Limits > Hotels\n")

    def test_count_tokens(self):
        assert count_tokens("Hello, world!") == 4


def test_hashing_embedder_is_normalised_and_deterministic():
    e = HashingEmbedder(64)
    a, b = e.embed(["annual leave carryover", "annual leave carryover"])
    assert a == b
    assert abs(sum(x * x for x in a) - 1) < 1e-9


def test_table_rows_become_readable_units():
    units = _units("| Category | Tier 1 | Tier 2 |\n|---|---|---|\n| Hotel | 300 USD | 180 USD |")
    assert units == ["Hotel: Tier 1 300 USD, Tier 2 180 USD."]


def test_citation_verification_strips_invented_sources():
    text, grounded = _verify_citations("Up to 5 days [1]. Also 10 days [7].", n_passages=3)
    assert "[7]" not in text and grounded
    assert _verify_citations("No citations here.", 3) == ("No citations here.", False)


class TestExtraction:
    def test_leave(self):
        a = extract_leave("I started on 15 March 2021, took 6 days and have 2 days carried over. Balance as of 2025-09-30?")
        assert a == {"start_date": date(2021, 3, 15), "as_of": date(2025, 9, 30), "days_taken": 6, "carryover_days": 2}

    def test_overtime_holiday_hours_are_not_weekly_hours(self):
        a = extract_overtime("I worked 52 hours this week at $30/hour plus 8 hours on a public holiday")
        assert a == {"hourly_rate": 30, "hours_worked": 52, "public_holiday_hours": 8}

    def test_expense(self):
        a = extract_expense("I spent $1,200 on a hotel for 3 nights in Tokyo without a receipt")
        assert a["amount"] == 1200 and a["units"] == 3 and a["city"] == "Tokyo" and a["has_receipt"] is False

    def test_general_question_is_not_routed_to_a_rule(self):
        assert heuristic_route("What is the overtime rate above 48 hours?") is None
        assert heuristic_route("How many vacation days do employees get?") is None
