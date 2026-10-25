from kaoyan.db import KaoyanStore
from kaoyan.query import KaoyanQuery, make_fact_id


def test_make_fact_id_is_stable_and_source_aware() -> None:
    a = make_fact_id(
        table="score_lines", program_id="sysu-670-085404", kind="college", year=2026,
        value=379, source_doc_ids=["sysu-078"],
    )
    b = make_fact_id(
        table="score_lines", program_id="sysu-670-085404", kind="college", year=2026,
        value=379, source_doc_ids=["sysu-078"],
    )
    assert a == b
    assert a.startswith("score_lines:sysu-670-085404:college:2026:-:")
    assert a != make_fact_id(
        table="score_lines", program_id="sysu-670-085404", kind="college", year=2026,
        value=380, source_doc_ids=["sysu-078"],
    )
    assert a != make_fact_id(
        table="score_lines", program_id="sysu-670-085404", kind="college", year=2026,
        value=379, source_doc_ids=["sysu-078", "sysu-001"],
    )


def test_merged_plans_keep_the_same_fact_id(kaoyan_store: KaoyanStore) -> None:
    q = KaoyanQuery(kaoyan_store)
    first = q.plans("sysu-670-085404")
    second = q.plans("sysu-670-085404")
    assert [p["fact_id"] for p in first] == [p["fact_id"] for p in second]
    assert all(p["fact_id"].startswith("plans:sysu-670-085404:") for p in first)
    catalog = next(p for p in first if p["kind"] == "catalog_total" and p["year"] == 2026)
    assert catalog["value"] == 210
    again = make_fact_id(
        table="plans",
        program_id="sysu-670-085404",
        kind="catalog_total",
        year=2026,
        value=210,
        source_doc_ids=[catalog["source"]["doc_id"]],
    )
    assert catalog["fact_id"] == again


def test_score_line_fact_id_and_latest_year(kaoyan_store: KaoyanStore) -> None:
    q = KaoyanQuery(kaoyan_store)
    lines = q.score_lines(q.find_programs(school="sysu", code="085404")[0], 2026)
    college = next(ln for ln in lines if ln["scope"] == "college")
    assert college["fact_id"].startswith("score_lines:sysu-670-085404:college:2026:")
    assert college["value_text"] == "379"
    assert q.latest_year("score_lines") == 2026
    assert q.resolve_year(2027, "score_lines") == 2027
    assert q.resolve_year(None, "score_lines") == 2026


def test_llm_view_assigns_aliases(kaoyan_store: KaoyanStore, monkeypatch) -> None:
    from kaoyan.tools import kaoyan as ky

    monkeypatch.setattr(ky, "get_kaoyan_store", lambda: kaoyan_store)
    out = ky.get_score_lines.invoke({"school": "中大", "code": "085404", "year": 2026})
    view, mapping = ky.llm_view_and_facts("get_score_lines", out)
    assert mapping
    assert all(k.startswith("F") for k in mapping)
    first = next(iter(mapping))
    assert f'"alias":"{first}"' in view
    assert mapping[first]["school"] == "sysu"
    assert mapping[first]["code"] == "085404"
    cites = ky.citations_from_output("get_score_lines", out)
    assert any(c.get("fact_id") for c in cites)
