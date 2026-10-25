#!/usr/bin/env python3
"""Build the v2 eval skeleton without calling an LLM.

--migrate-v1 rewrites the v1-* rows of data/gold/kaoyan_eval_v2.jsonl from the old
18-question file plus the facts and reviewer in data/gold/v1_review.json, and keeps
every other row.
--candidates writes data/gold/candidates_m0.jsonl: at most one score line and one
plan question per program, capped at 60, from the local kaoyan.db.
--apply-review replaces the reviewed template rows of the eval file with the
accepted and edited candidates listed in data/gold/review_m0.json.
--specs replaces the handwritten rows (adv-* from adversarial_m1.json, sup-* from
supplement_m1.json, doc-* from docq_m1.json), reading each fact's value and source from
verified rows of the kaoyan DB; an unreviewed spec is refused.
--freeze-test draws the test split once (30% of the unsplit rows, stratified by school x
category) into data/gold/split_m1.json; every later write puts those ids in test and the
other unsplit rows in dev. --freeze-append PREFIX does the same once for a spec added later
(stratified by question shape x bigram overlap), leaving earlier test rows untouched.
--rehash-test re-records the frozen rows after a deliberate edit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD_V1 = ROOT / "data" / "gold" / "kaoyan_qa.json"
EVAL_V2 = ROOT / "data" / "gold" / "kaoyan_eval_v2.jsonl"
CANDIDATES = ROOT / "data" / "gold" / "candidates_m0.jsonl"
REVIEW = ROOT / "data" / "gold" / "review_m0.json"
V1_REVIEW = ROOT / "data" / "gold" / "v1_review.json"
ADVERSARIAL = ROOT / "data" / "gold" / "adversarial_m1.json"
SUPPLEMENT = ROOT / "data" / "gold" / "supplement_m1.json"
DOCQ = ROOT / "data" / "gold" / "docq_m1.json"
SPECS = (ADVERSARIAL, SUPPLEMENT, DOCQ)
SPLIT = ROOT / "data" / "gold" / "split_m1.json"
DB_PATH = ROOT / "data" / "kaoyan.db"
CANDIDATE_CAP = 60
TEST_SHARE = 0.3
TEST_SEED = "kaoyan-test-m1"
# Rows already named as fix targets; tuning against them would leak into test.
TEST_EXCLUDE = {
    "sup-07": "L1 修复目标：一句里两个代码被丢",
    "sup-10": "导师试点会给它补事实",
    "sup-29": "L1 修复目标：精简视图丢口径",
    "sup-36": "L1 修复目标：不考数学的排除筛选",
}

_SCHOOLS = (
    ("华南师范大学", "scnu"),
    ("华南理工大学", "scut"),
    ("中山大学", "sysu"),
    ("暨南大学", "jnu"),
    ("华师", "scnu"),
    ("华工", "scut"),
    ("中大", "sysu"),
    ("暨大", "jnu"),
    ("暨南", "jnu"),
)
_ALL_SCHOOLS = ["sysu", "scut", "jnu", "scnu"]
_CATEGORY = {
    "score_line": "score_line",
    "no_exam": "tm",
    "unknown": "unknown_data",
    "subjects": "subjects",
    "pool": "plan",
    "conflict": "plan",
    "filter": "filter",
    "compare": "compare",
    "year": "year_mismatch",
    "scope": "unknown_data",
    "upper_bound": "plan",
    "boundary": "subjects",
    "privacy": "privacy",
    "export": "export",
}
_OPERATION = {
    "compare": "compare",
    "filter": "filter",
    "export": "export",
}
_PLAN_RANK = {
    "catalog_total": 0,
    "public_exam": 1,
    "college_exam_plan": 2,
    "available_exam": 3,
    "rules_total": 4,
}
_SCOPE_RANK = {"college": 0, "school_baseline": 1}


def _schools(question: str) -> list[str]:
    if "四校" in question:
        return list(_ALL_SCHOOLS)
    found = []
    for label, sid in _SCHOOLS:
        if label in question and sid not in found:
            found.append(sid)
    return found


def _codes(question: str) -> list[str]:
    six = re.findall(r"(?<![0-9A-Za-z])(\d{4}[0-9A-Z]{2})(?![0-9A-Za-z])", question)
    four = [c for c in re.findall(r"(?<![0-9A-Za-z])(\d{4})(?![0-9A-Za-z])", question) if not c.startswith("20")]
    out = []
    for code in six + four:
        if code not in out:
            out.append(code)
    return out


def _years(question: str) -> list[int]:
    return [int(y) for y in re.findall(r"20\d{2}", question)]


def migrate_v1_item(item: dict) -> dict:
    category = _CATEGORY[item["category"]]
    question = item["question"]
    years = _years(question)
    refusal = None
    if item["category"] == "privacy":
        refusal = "privacy"
    elif item.get("expect_unknown"):
        refusal = "unknown_data"
    notes = [f"migrated from kaoyan_qa.json id={item['id']}; split=dev because this set was used for tuning."]
    if item.get("must_not_match"):
        notes.append("must_not_match=" + json.dumps(item["must_not_match"], ensure_ascii=False))
    if item.get("export_columns"):
        notes.append("export_columns=" + "、".join(item["export_columns"]))
    return {
        "id": f"v1-{int(item['id']):02d}",
        "split": "dev",
        "category": category,
        "turns": [question],
        "school": _schools(question),
        "program_ids": [],
        "year": years[0] if len(years) == 1 else None,
        "expected_intent": {
            "operation": _OPERATION.get(category, "lookup"),
            "metrics": [],
            "schools": _schools(question),
            "codes": _codes(question),
            "year": years[0] if len(years) == 1 else None,
            "follow_up": False,
        },
        "expected_facts": [],
        "expected_refusal": refusal,
        "expected_sources": [s["url"] for s in item.get("sources") or [] if s.get("url")],
        "must_include": item.get("must_include") or [],
        "must_not_include": item.get("must_not_include") or [],
        "judge_rubric": "",
        "origin": "handwritten",
        "reviewed_by": "",
        "reviewed_at": "",
        "notes": " ".join(notes),
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def row_digest(row: dict) -> str:
    body = {k: v for k, v in row.items() if k != "split"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def stratum(row: dict) -> str:
    schools = row["expected_intent"]["schools"]
    school = schools[0] if len(schools) == 1 else ("multi" if schools else "none")
    return f"{school}/{row['category']}"


def doc_stratum(row: dict) -> str:
    """Retrieval questions: question shape x how much of the question's wording the evidence repeats."""
    sys.path.insert(0, str(ROOT / "src"))
    from kaoyan.eval.drafts import bigram_overlap, overlap_layer, question_shape

    if row["expected_facts"]:
        return "structured"
    overlap = bigram_overlap(row["turns"][0], [f["text"] for f in row.get("evidence") or []])
    return f"{question_shape(row)}/{overlap_layer(overlap)}"


def draw_test(rows: list[dict], exclude: set[str], share: float = TEST_SHARE, seed: str = TEST_SEED,
              key=stratum) -> list[str]:
    """Proportional stratified sample; leftover seats go to the largest remainders."""
    strata: dict[str, list[str]] = {}
    for r in rows:
        if r["split"] == "unsplit" and r["id"] not in exclude:
            strata.setdefault(key(r), []).append(r["id"])

    def order(key: str) -> str:
        return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()

    quota = {k: share * len(ids) for k, ids in strata.items()}
    take = {k: int(q) for k, q in quota.items()}
    target = round(share * sum(len(ids) for ids in strata.values()))
    for k in sorted(strata, key=lambda k: (take[k] - quota[k], order(k)))[: target - sum(take.values())]:
        take[k] += 1
    return sorted(i for k, ids in strata.items() for i in sorted(ids, key=order)[: take[k]])


def assign_splits(rows: list[dict]) -> list[dict]:
    if not SPLIT.exists():
        return rows
    frozen = json.loads(SPLIT.read_text(encoding="utf-8"))["test"]
    for r in rows:
        if r["id"] in frozen:
            r["split"] = "test"
        elif r["split"] == "test":
            raise SystemExit(f"{r['id']}: split=test but not in {SPLIT.name}; the test split is frozen")
        elif r["split"] == "unsplit":
            r["split"] = "dev"
    return rows


def write_eval(rows: list[dict]) -> None:
    write_jsonl(EVAL_V2, assign_splits(rows))


def freeze_test() -> list[str]:
    if SPLIT.exists():
        raise SystemExit(f"{SPLIT.relative_to(ROOT)} exists; the test split is drawn only once")
    rows = read_jsonl(EVAL_V2)
    ids = draw_test(rows, set(TEST_EXCLUDE))
    by_id = {r["id"]: r for r in rows}
    payload = {
        "frozen_at": dt.date.today().isoformat(),
        "rule": f"{TEST_SHARE:.0%} of unsplit rows, stratified by school x category, seed {TEST_SEED!r}",
        "excluded": TEST_EXCLUDE,
        "test": {i: row_digest(by_id[i]) for i in ids},
    }
    SPLIT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_eval(rows)
    return ids


def freeze_append(prefix: str, db_path: Path = DB_PATH) -> list[str]:
    """Freeze a test share of one new spec's rows; rows frozen earlier are left alone. Once per prefix."""
    payload = json.loads(SPLIT.read_text(encoding="utf-8"))
    appended = payload.setdefault("appended", {})
    if prefix in appended:
        raise SystemExit(f"{prefix}* rows were frozen on {appended[prefix]['frozen_at']}; appending is once only")
    rows = spec_rows(db_path)
    new = [r for r in rows if r["id"].startswith(prefix)]
    if not new or any(r["id"] in payload["test"] for r in new):
        raise SystemExit(f"no new {prefix}* rows to freeze")
    seed = f"{TEST_SEED}:{prefix}"
    ids = draw_test(new, set(), seed=seed, key=doc_stratum)
    by_id = {r["id"]: r for r in new}
    payload["test"].update({i: row_digest(by_id[i]) for i in ids})
    appended[prefix] = {
        "frozen_at": dt.date.today().isoformat(),
        "rule": f"{TEST_SHARE:.0%} of {prefix}* rows, stratified by question shape x bigram overlap, seed {seed!r}",
        "test": ids,
    }
    SPLIT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_eval(rows)
    return ids


def rehash_test() -> list[str]:
    payload = json.loads(SPLIT.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in read_jsonl(EVAL_V2)}
    changed = [i for i, old in payload["test"].items() if row_digest(by_id[i]) != old]
    payload["test"] = {i: row_digest(by_id[i]) for i in payload["test"]}
    SPLIT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def apply_v1_review(row: dict, decision: dict, reviewer: str, reviewed_at: str) -> dict:
    facts = decision.get("expected_facts") or []
    row["expected_facts"] = facts
    row["program_ids"] = list(dict.fromkeys(f["program_id"] for f in facts))
    row["judge_rubric"] = decision.get("judge_rubric", "")
    row["reviewed_by"] = reviewer
    row["reviewed_at"] = reviewed_at
    if decision.get("note"):
        row["notes"] += " " + decision["note"]
    return row


def migrate_v1() -> list[dict]:
    payload = json.loads(GOLD_V1.read_text(encoding="utf-8"))
    rows = [migrate_v1_item(item) for item in payload["items"]]
    if V1_REVIEW.exists():
        review = json.loads(V1_REVIEW.read_text(encoding="utf-8"))
        rows = [
            apply_v1_review(r, review["rows"][r["id"]], review["reviewer"], review["reviewed_at"])
            if r["id"] in review["rows"] else r
            for r in rows
        ]
    kept = [r for r in read_jsonl(EVAL_V2) if not r["id"].startswith("v1-")]
    write_eval(rows + kept)
    return rows


def reviewed_row(candidate: dict, decision: dict, reviewer: str, reviewed_at: str) -> dict:
    row = json.loads(json.dumps(candidate))
    row["id"] = decision.get("new_id") or candidate["id"].replace("cand-", "rv-", 1)
    row["split"] = "unsplit"
    for key in ("turns", "program_ids", "expected_facts", "must_include", "must_not_include"):
        if key in decision:
            row[key] = decision[key]
    if "codes" in decision:
        row["expected_intent"]["codes"] = decision["codes"]
    row["expected_sources"] = row["expected_sources"] + [
        u for u in decision.get("extra_sources") or [] if u not in row["expected_sources"]
    ]
    row["reviewed_by"] = reviewer
    row["reviewed_at"] = reviewed_at
    note = f"from {candidate['id']}; {decision['action']}; evidence: {decision['evidence']}"
    if decision.get("reason"):
        note += f"; {decision['reason']}"
    row["notes"] = note
    ordered = {}
    for key, value in row.items():
        ordered[key] = value
        if key == "must_not_include":
            ordered["judge_rubric"] = decision.get("judge_rubric", "")
    return ordered


def apply_review() -> list[dict]:
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    candidates = {r["id"]: r for r in read_jsonl(CANDIDATES)}
    decisions = review["decisions"]
    decided = {d["id"] for d in decisions}
    if decided != set(candidates):
        raise SystemExit(
            "review_m0.json and candidates_m0.jsonl disagree; "
            f"undecided: {sorted(set(candidates) - decided)}, unknown: {sorted(decided - set(candidates))}"
        )
    rows = [
        reviewed_row(candidates[d["id"]], d, review["reviewer"], review["reviewed_at"])
        for d in decisions
        if d["action"] in {"accept", "edit"}
    ]
    rows += [addition_row(a, review["reviewer"], review["reviewed_at"]) for a in review.get("additions") or []]
    kept = [r for r in read_jsonl(EVAL_V2) if not r["id"].startswith("rv-")]
    write_eval(kept + rows)
    return rows


_FACT_SQL = {
    "score_lines": "SELECT DISTINCT {field}, source_doc_id FROM score_lines WHERE program_id=? AND scope=? AND year=?",
    "plans": "SELECT DISTINCT value, source_doc_id FROM plans WHERE program_id=? AND kind=? AND year=?",
    "admission_stats": "SELECT DISTINCT value, source_doc_id FROM admission_stats WHERE program_id=? AND kind=? AND year=?",
    "exam_subjects": "SELECT DISTINCT code, source_doc_id FROM exam_subjects WHERE program_id=? AND 'slot'||slot=? AND year=?",
    "directions": "SELECT DISTINCT advisors, source_doc_id FROM directions "
                  "WHERE program_id=? AND code=? AND year=? AND advisors IS NOT NULL",
}
_LINE_FIELDS = ("total", "politics", "foreign_lang", "subject1", "subject2")


def resolve_fact(conn: sqlite3.Connection, key: str) -> dict:
    """`program_id|table|kind|year[|source_doc_id][|field=politics][|checked]` -> an expected fact.

    Only verified rows count, unless the key has `checked`: the row was read
    against the official file by hand because no seed exists to verify it.
    `field=` picks a single-subject score line instead of the total.
    """
    pid, table, kind, year, *rest = key.split("|")
    checked = "checked" in rest
    field = next((r.removeprefix("field=") for r in rest if r.startswith("field=")), "total")
    doc = [r for r in rest if r != "checked" and not r.startswith("field=")]
    if field not in _LINE_FIELDS or (field != "total" and table != "score_lines"):
        raise SystemExit(f"fact {key!r}: bad field {field!r}")
    sql = _FACT_SQL[table].format(field=field) + ("" if checked else " AND verified=1")
    sql += " AND source_doc_id=?" if doc else ""
    hits = conn.execute(sql, (pid, kind, int(year), *doc)).fetchall()
    if len(hits) != 1:
        raise SystemExit(f"fact {key!r} resolves to {len(hits)} verified rows: {hits}")
    value, source = hits[0]
    fact = {"table": table, "program_id": pid, "kind": kind, "year": int(year), "value": value,
            "source_doc_id": source}
    if field != "total":
        fact = {**fact, "field": field}
    return fact


def _page_url(conn: sqlite3.Connection, doc_id: str) -> str | None:
    row = conn.execute("SELECT page_url FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        raise SystemExit(f"unknown source document {doc_id!r}")
    return row[0]


def spec_row(item: dict, conn: sqlite3.Connection, spec: dict) -> dict:
    """One eval row from a handwritten spec item; facts and source URLs come from the DB."""
    facts = [resolve_fact(conn, key) for key in item["facts"]]
    docs = list(dict.fromkeys([f["source_doc_id"] for f in facts] + item.get("sources", [])))
    urls = [_page_url(conn, d) for d in docs]
    row = {
        "id": item["id"],
        "split": spec.get("split", "adversarial"),
        "category": item["category"],
        "trap": item["trap"],
        "turns": item["turns"],
        "school": item["schools"],
        "program_ids": list(dict.fromkeys(f["program_id"] for f in facts)),
        "year": item["year"],
        "expected_intent": {
            "operation": item["operation"],
            "metrics": item["metrics"],
            "schools": item["schools"],
            "codes": item["codes"],
            "year": item["year"],
            "follow_up": len(item["turns"]) > 1,
        },
        "expected_facts": facts,
        "expected_refusal": item["refusal"],
        "expected_sources": [u for u in urls if u],
        "must_include": item["must_include"],
        "must_not_include": item.get("must_not_include") or [],
        "judge_rubric": item["judge_rubric"],
        "origin": "handwritten",
        "reviewed_by": spec.get("reviewed_by", ""),
        "reviewed_at": spec.get("reviewed_at", ""),
        "notes": f"{item.get('label', spec.get('label', 'adversarial'))} trap={item['trap']}",
    }
    for key in ("judge_flags", "evidence"):
        if item.get(key):
            row[key] = item[key]
    return row


def spec_rows(db_path: Path = DB_PATH) -> list[dict]:
    """The eval rows with every handwritten spec (adv-*, sup-*, doc-*) rebuilt; splits not yet assigned."""
    rows = read_jsonl(EVAL_V2)
    conn = sqlite3.connect(db_path)
    try:
        for path in SPECS:
            spec = json.loads(path.read_text(encoding="utf-8"))
            prefix = spec.get("id_prefix", "adv-")
            if not all(item["id"].startswith(prefix) for item in spec["items"]):
                raise SystemExit(f"{path.name}: every id must start with {prefix!r}")
            if not spec.get("reviewed_by"):
                raise SystemExit(f"{path.name}: unreviewed specs stay out of the eval set")
            new = [spec_row(item, conn, spec) for item in spec["items"]]
            rows = [r for r in rows if not r["id"].startswith(prefix)] + new
    finally:
        conn.close()
    return rows


def build_specs(db_path: Path = DB_PATH) -> dict[str, int]:
    """Replace the rows of every handwritten spec in the eval file."""
    rows = spec_rows(db_path)
    write_eval(rows)
    prefixes = {path.name: json.loads(path.read_text(encoding="utf-8")).get("id_prefix", "adv-") for path in SPECS}
    return {name: sum(r["id"].startswith(p) for r in rows) for name, p in prefixes.items()}


def addition_row(addition: dict, reviewer: str, reviewed_at: str) -> dict:
    """A question written during review for a fact the template generator skips."""
    metric = "score_line" if addition["category"] == "score_line" else "plan"
    return {
        "id": addition["id"],
        "split": "unsplit",
        "category": addition["category"],
        "turns": addition["turns"],
        "school": addition["school"],
        "program_ids": addition["program_ids"],
        "year": addition["year"],
        "expected_intent": {
            "operation": "lookup",
            "metrics": [metric],
            "schools": addition["school"],
            "codes": addition["codes"],
            "year": addition["year"],
            "follow_up": False,
        },
        "expected_facts": addition["expected_facts"],
        "expected_refusal": None,
        "expected_sources": addition["expected_sources"],
        "must_include": addition["must_include"],
        "must_not_include": addition.get("must_not_include") or [],
        "judge_rubric": addition.get("judge_rubric", ""),
        "origin": "handwritten",
        "reviewed_by": reviewer,
        "reviewed_at": reviewed_at,
        "notes": f"review addition; evidence: {addition['evidence']}; {addition['reason']}",
    }


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _programs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT p.id AS program_id, p.school_id, p.code, p.name AS program_name,
                   s.short_name, c.name AS college_name
            FROM programs p
            JOIN schools s ON s.id = p.school_id
            JOIN colleges c ON c.id = p.college_id
            ORDER BY p.school_id, p.code, p.id
            """
        )
    )


def _best_score_line(conn: sqlite3.Connection, program_id: str) -> sqlite3.Row | None:
    rows = list(
        conn.execute(
            """
            SELECT sl.program_id, sl.year, sl.scope, sl.total, sl.source_doc_id,
                   sl.verified, d.page_url, d.title
            FROM score_lines sl
            LEFT JOIN documents d ON d.id = sl.source_doc_id
            WHERE sl.program_id = ? AND sl.total IS NOT NULL
              AND sl.scope IN ('college', 'school_baseline')
            """,
            (program_id,),
        )
    )
    if not rows:
        return None
    rows.sort(key=lambda r: (_SCOPE_RANK.get(r["scope"], 9), -r["year"], -r["verified"], r["source_doc_id"] or ""))
    return rows[0]


def _best_plan(conn: sqlite3.Connection, program_id: str) -> sqlite3.Row | None:
    rows = list(
        conn.execute(
            """
            SELECT pl.program_id, pl.year, pl.kind, pl.value, pl.pool_scope, pl.source_doc_id,
                   pl.verified, pl.definition, d.page_url, d.title
            FROM plans pl
            LEFT JOIN documents d ON d.id = pl.source_doc_id
            WHERE pl.program_id = ? AND pl.value IS NOT NULL
              AND pl.kind IN ('catalog_total', 'public_exam', 'college_exam_plan', 'available_exam', 'rules_total')
            """,
            (program_id,),
        )
    )
    if not rows:
        return None
    rows = [r for r in rows if not r["pool_scope"]]
    if not rows:
        return None
    rows.sort(key=lambda r: (_PLAN_RANK.get(r["kind"], 9), -r["year"], -r["verified"], r["source_doc_id"] or ""))
    return rows[0]


def _candidate(program: sqlite3.Row, fact: sqlite3.Row, metric: str) -> dict:
    year = int(fact["year"])
    if metric == "score_line":
        question = (
            f"{program['short_name']}{program['college_name']} {program['code']} "
            f"{program['program_name']}的 {year} 复试线是多少？"
        )
        kind = fact["scope"]
        value = int(fact["total"])
        table = "score_lines"
    else:
        question = (
            f"{program['short_name']}{program['college_name']} {program['code']} "
            f"{program['program_name']}的 {year} 招生计划是多少？"
        )
        kind = fact["kind"]
        value = int(fact["value"])
        table = "plans"
    url = fact["page_url"] or ""
    return {
        "id": f"cand-{program['program_id']}-{metric}-{year}",
        "split": "candidate",
        "category": metric,
        "turns": [question],
        "school": [program["school_id"]],
        "program_ids": [program["program_id"]],
        "year": year,
        "expected_intent": {
            "operation": "lookup",
            "metrics": [metric],
            "schools": [program["school_id"]],
            "codes": [program["code"]],
            "year": year,
            "follow_up": False,
        },
        "expected_facts": [
            {
                "table": table,
                "program_id": program["program_id"],
                "kind": kind,
                "year": year,
                "value": value,
                "source_doc_id": fact["source_doc_id"],
            }
        ],
        "expected_refusal": None,
        "expected_sources": [url] if url else [],
        "must_include": [str(value), str(year)],
        "must_not_include": [],
        "origin": "db_template",
        "reviewed_by": "",
        "reviewed_at": "",
        "notes": "unreviewed template; not part of kaoyan_eval_v2.jsonl",
    }


def build_candidates(db_path: Path = DB_PATH, cap: int = CANDIDATE_CAP) -> list[dict]:
    conn = _connect(db_path)
    try:
        programs = _programs(conn)
        lines = []
        plans = []
        for program in programs:
            line = _best_score_line(conn, program["program_id"])
            if line is not None:
                lines.append(_candidate(program, line, "score_line"))
            plan = _best_plan(conn, program["program_id"])
            if plan is not None:
                plans.append(_candidate(program, plan, "plan"))
    finally:
        conn.close()
    # Score lines first, then plans, until the cap. One of each per program before the slice.
    return (lines + plans)[:cap]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--migrate-v1", action="store_true", help="only rewrite kaoyan_eval_v2.jsonl")
    parser.add_argument("--candidates", action="store_true", help="only rewrite candidates_m0.jsonl")
    parser.add_argument("--apply-review", action="store_true", help="only merge review_m0.json into the eval file")
    parser.add_argument("--specs", action="store_true",
                        help="only rebuild handwritten rows (adversarial_m1.json, supplement_m1.json)")
    parser.add_argument("--freeze-test", action="store_true", help="draw and freeze the test split (once)")
    parser.add_argument("--rehash-test", action="store_true",
                        help="re-record frozen test rows after a deliberate edit; lists the changed ids")
    parser.add_argument("--freeze-append", metavar="PREFIX",
                        help="freeze a test share of a newly reviewed spec (e.g. doc-), once per prefix")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    if args.freeze_append:
        ids = freeze_append(args.freeze_append, Path(args.db))
        print(f"froze {len(ids)} {args.freeze_append}* test rows: {', '.join(ids)}")
        return 0
    if args.freeze_test:
        ids = freeze_test()
        print(f"froze {len(ids)} test rows in {SPLIT.relative_to(ROOT)}")
        return 0
    if args.rehash_test:
        print("changed test rows:", ", ".join(rehash_test()) or "none")
        return 0
    if args.specs:
        for name, count in build_specs(Path(args.db)).items():
            print(f"wrote {count} rows from {name} to {EVAL_V2.relative_to(ROOT)}")
        return 0
    if args.apply_review:
        rows = apply_review()
        print(f"wrote {len(rows)} reviewed rows to {EVAL_V2.relative_to(ROOT)}")
        return 0
    do_v1 = args.migrate_v1 or not args.candidates
    do_candidates = args.candidates or not args.migrate_v1
    if args.migrate_v1 and args.candidates:
        do_v1 = do_candidates = True

    if do_v1:
        rows = migrate_v1()
        print(f"wrote {len(rows)} dev rows to {EVAL_V2.relative_to(ROOT)}")

    if not do_candidates:
        return 0
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"{db_path} missing; candidates not written", file=sys.stderr)
        return 2
    rows = build_candidates(db_path)
    write_jsonl(CANDIDATES, rows)
    n_line = sum(r["category"] == "score_line" for r in rows)
    n_plan = sum(r["category"] == "plan" for r in rows)
    print(f"wrote {len(rows)} candidates ({n_line} score_line, {n_plan} plan) to {CANDIDATES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
