import json
from pathlib import Path
from types import SimpleNamespace

from kaoyan.eval import runner
from kaoyan.eval.retrieval import load_spec_sources, retrieve, run_l2, score_hits, source_docs

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "gold" / "kaoyan_eval_v2.jsonl"


def _hit(chunk_id, doc, text="", school=""):
    return SimpleNamespace(chunk_id=chunk_id, text=text, metadata={"doc_id": doc, "school": school})


def _row(rid="doc-90", split="dev", schools=("jnu",), evidence=(), facts=(), refusal=None):
    return {
        "id": rid, "split": split, "category": "narrative", "turns": ["暨大电子信息学费多少"],
        "expected_intent": {"schools": list(schools)}, "expected_refusal": refusal,
        "expected_facts": list(facts), "evidence": list(evidence),
    }


def test_document_hits_count_any_and_all_sources_within_k():
    hits = [_hit("c1", "a"), _hit("c2", "x"), _hit("c3", "x"), _hit("c4", "b")]
    got = score_hits(hits, ["a", "b"], [])
    assert got["doc_any@3"] and not got["doc_all@3"]
    assert got["doc_all@5"] and got["doc_all@10"]
    assert "chunk_any@3" not in got


def test_fragment_hit_needs_its_document_and_most_of_its_bigrams_in_one_chunk():
    fragment = {"doc": "a", "text": "电子信息 15000 3 年"}
    same_text_other_doc = [_hit("c1", "b", "电子信息 15000 3 年")]
    assert not score_hits(same_text_other_doc, ["a"], [fragment])["chunk_any@3"]
    split_by_boundary = [_hit("c1", "a", "全日制 电子信息 150"), _hit("c2", "a", "00 3 年 材料与化工")]
    assert not score_hits(split_by_boundary, ["a"], [fragment])["chunk_any@3"]
    mostly_inside = [_hit("c1", "a", "学费如下：电子信息 15000 3")]
    assert score_hits(mostly_inside, ["a"], [fragment])["chunk_all@3"]


def test_filtered_mode_searches_each_gold_school_and_interleaves():
    calls = []

    def search(question, school, k):
        calls.append(school)
        return [_hit(f"{school}{i}", f"{school}-doc{i}") for i in range(k)]

    row = _row(schools=("jnu", "scut"))
    top = retrieve(row, "filtered", search, 4)
    assert calls == ["jnu", "scut"]
    assert [h.chunk_id for h in top] == ["jnu0", "scut0", "jnu1", "scut1"]
    calls.clear()
    retrieve(row, "unfiltered", search, 4)
    assert calls == [None]


def test_sources_come_from_facts_specs_and_evidence():
    row = _row(rid="sup-90", facts=[{"source_doc_id": "jnu-003"}], evidence=[{"doc": "jnu-007", "text": "x"}])
    assert source_docs(row, {"sup-90": ["jnu-016", "jnu-003"]}) == ["jnu-003", "jnu-016", "jnu-007"]


def test_unindexed_sources_skip_and_test_rows_only_count_in_totals():
    rows = [
        _row("doc-91", evidence=[{"doc": "a", "text": "电子信息学费"}]),
        _row("doc-92", split="test", evidence=[{"doc": "a", "text": "电子信息学费"}]),
        _row("doc-93", evidence=[{"doc": "missing", "text": "电子信息学费"}]),
        {**_row("sup-91"), "category": "plan"},
    ]

    def search(question, school, k):
        return [_hit("c1", "a", "暨大电子信息学费")]

    report = run_l2(rows, search, {"a"}, {})
    s = report["summary"]
    assert (s["rows"], s["evidence_rows"], s["skipped"]) == (2, 2, 1)
    assert s["filtered doc_all@5"] == 1.0 and s["unfiltered chunk_all@3"] == 1.0
    assert [r["id"] for r in report["rows"]] == ["doc-91", "doc-93"]
    assert (report["rows"][1]["status"], report["rows"][1]["missing"]) == ("skipped", ["missing"])
    assert report["by_split"]["test"]["rows"] == 1
    assert report["by_shape"]["single"]["rows"] == 1


def test_l2_misses_join_the_known_failure_list_and_gate():
    rows = [_row("doc-91", evidence=[{"doc": "a", "text": "电子信息学费"}])]
    miss = {"layers": {"L2": run_l2(rows, lambda q, s, k: [_hit("c1", "b")], {"a", "b"}, {})}}
    known = runner.known_failures(miss)
    assert "filtered doc_any@3" in known["L2"]["doc-91"]
    hit = {"layers": {"L2": run_l2(rows, lambda q, s, k: [_hit("c1", "a", "电子信息学费")], {"a"}, {})}}
    new, fixed = runner.failure_changes(hit, known)
    assert not new and fixed
    new, fixed = runner.failure_changes(miss, {"L2": {}})
    assert new and not fixed


def test_regressions_check_l2_groups():
    base = {"layers": {"L2": {"summary": {"filtered doc_any@5": 0.9},
                              "by_shape": {"single": {"filtered doc_any@5": 1.0}}}}}
    now = {"layers": {"L2": {"summary": {"filtered doc_any@5": 0.9},
                             "by_shape": {"single": {"filtered doc_any@5": 0.8}}}}}
    assert runner.regressions(now, base, 0.02) == ["L2[single].filtered doc_any@5: 100.00% -> 80.00%"]


def test_every_narrative_question_has_source_documents():
    rows = [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]
    specs = load_spec_sources(GOLD.parent)
    narrative = [r for r in rows if r["category"] == "narrative"]
    assert len(narrative) == 37
    assert all(source_docs(r, specs) for r in narrative)
