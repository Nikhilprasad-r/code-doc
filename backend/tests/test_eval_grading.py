"""Unit tests for the eval graders and the case file (no database, no model calls)."""

import json

import pytest

from evals.grading import citation_scores, found_gold, grade_items, item_present, parse_citations
from evals.run import CASES_PATH, REPOS_PATH, cost_usd

LINES = {"src/a/Owner.java": 120, "backend/app/crud.py": 80, "pom.xml": 200}
GOLD = [{"path": "src/a/Owner.java", "start": 40, "end": 60}]


def test_parse_citations_handles_ranges_lists_and_noise():
    text = ("See [src/a/Owner.java:40-45] and [backend/app/crud.py:12]. Also [pom.xml:3–9, src/a/Owner.java:50]. "
            "Not citations: [Note:1], [link](http://x), [1].")
    cites = [(c.path, c.start, c.end) for c in parse_citations(text)]
    assert cites == [("src/a/Owner.java", 40, 45), ("backend/app/crud.py", 12, 12), ("pom.xml", 3, 9),
                     ("src/a/Owner.java", 50, 50)]


def test_citation_scores_valid_and_gold():
    scores = citation_scores("A [src/a/Owner.java:55-58] B [src/a/Missing.java:1-2] C [pom.xml:150-999]",
                             LINES, GOLD, absent=False)
    assert scores["scores"]["cite_valid"] == 1 / 3
    assert scores["scores"]["gold_cited"] == 1.0


def test_uncited_answer_scores_zero_unless_feature_is_absent():
    assert citation_scores("no citations here", LINES, GOLD, absent=False)["scores"] == {
        "cite_valid": 0.0, "gold_cited": 0.0}
    assert citation_scores("This feature does not exist.", LINES, [], absent=True)["scores"] == {"cite_valid": 1.0}


def test_found_gold_uses_overlap():
    assert found_gold([{"path": "src/a/Owner.java", "start_line": 1, "end_line": 40}], GOLD) == 1.0
    assert found_gold([{"path": "src/a/Owner.java", "start_line": 61, "end_line": 90}], GOLD) == 0.0
    assert found_gold([{"path": "other.java", "start_line": 40, "end_line": 60}], GOLD) == 0.0


def test_items_need_exact_routes_and_package_names():
    answer = "GET `/owners/{ownerId}/edit`, POST /api/v1/login/access-token, deps: com.h2database:h2, postgresql"
    assert item_present("/owners/{ownerId}/edit", answer)
    assert item_present("/api/v1", answer)  # a route prefix may continue with '/'
    assert not item_present("/vets", "only GET /vets.html is listed")
    assert item_present("/vets", "GET /vets and GET /vets.html.")
    assert item_present("/login/access-token", answer)  # prefixed with /api/v1 is fine
    assert item_present("h2", answer) and item_present("postgresql", answer)
    assert not item_present("h2", "uses h2database only")
    score, explanation = grade_items(answer, ["h2", "mysql-connector-j", "postgresql"])
    assert score == 2 / 3 and "mysql-connector-j" in explanation


def test_cost_uses_each_models_cache_prices():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 1_000_000,
             "cache_creation_input_tokens": 1_000_000}
    # input + cache read + 5-minute cache write + output, at the published $/MTok
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(4.0 + 0.20 + 5.00 + 2.0)
    assert cost_usd("claude-sonnet-5-5", usage) == pytest.approx(2.0 + 0.10 + 2.50 + 1.0)
    assert cost_usd("claude-haiku-5-5", usage) == pytest.approx(0.10 + 0.01 + 0.125 + 0.05)
    assert cost_usd("unknown-model", usage) is None


def test_case_file_is_well_formed():
    cases = [json.loads(line) for line in CASES_PATH.read_text().splitlines()]
    repos = json.loads(REPOS_PATH.read_text())
    assert len(cases) == 40 and len({c["id"] for c in cases}) == 40
    for c in cases:
        assert c["repo"] in repos
        assert c["grader"] in {"judge", "items"} and c["key_facts"]
        assert c["type"] in {"overview", "where", "how", "endpoints", "dependencies", "absent"}
        assert (c["type"] == "absent") == (not c["gold"]), c["id"]
        for g in c["gold"]:
            assert 1 <= g["start"] <= g["end"]
