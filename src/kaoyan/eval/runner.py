"""Run the no-LLM layers of the kaoyan eval.

L0 scores the rule-based intent detector against `expected_intent`.
L1 calls the structured tools with the gold intent and checks that every
`expected_facts` entry is in the tool output, which separates "the database
lacks it" from "the model wrote it wrong".
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kaoyan.agent.guardrails import detect_kaoyan_intent
from kaoyan.eval.metrics import intent_fields, match_fact, ratio, tool_facts
from kaoyan.eval.retrieval import known_misses, render_l2

Invoke = Callable[[str, dict[str, Any]], str]

L0_FIELDS = ("schools", "codes", "year", "operation", "metrics", "privacy")


HIDDEN_SPLIT = "test"


def load_rows(path: Path, splits: set[str] | None = None) -> list[dict[str, Any]]:
    """Rows of the given splits; by default every split except the frozen test split."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not splits:
        return [r for r in rows if r["split"] != HIDDEN_SPLIT]
    return [r for r in rows if r["split"] in splits]


def _groups(per_row: list[dict[str, Any]], summarize: Callable[[list[dict[str, Any]]], dict[str, Any]]) -> dict:
    """Overall summary plus one per split and one per trap.

    Test rows only count toward the totals: they get no trap group and no per-row entry,
    so a report never shows which test question failed.
    """
    by_split: dict[str, list] = {}
    by_trap: dict[str, list] = {}
    for r in per_row:
        by_split.setdefault(r["split"], []).append(r)
        if r.get("trap") and r["split"] != HIDDEN_SPLIT:
            by_trap.setdefault(r["trap"], []).append(r)
    return {
        "summary": summarize(per_row),
        "by_split": {k: summarize(v) for k, v in sorted(by_split.items())},
        "by_trap": {k: summarize(v) for k, v in sorted(by_trap.items())},
        "rows": [r for r in per_row if r["split"] != HIDDEN_SPLIT],
    }


def _l0_summary(per_row: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"rows": len(per_row), "exact": ratio(sum(r["exact"] for r in per_row), len(per_row))}
    for name in L0_FIELDS:
        scored = [r["fields"][name] for r in per_row if name in r["fields"]]
        summary[name] = ratio(sum(scored), len(scored))
    return summary


def run_l0(rows: list[dict[str, Any]]) -> dict[str, Any]:
    per_row = []
    for row in rows:
        predicted = detect_kaoyan_intent(row["turns"][-1])
        fields = intent_fields(row["expected_intent"], predicted, row.get("expected_refusal"))
        per_row.append({
            "id": row["id"],
            "split": row["split"],
            "trap": row.get("trap", ""),
            "fields": fields,
            "exact": all(fields.values()),
            "predicted": {k: predicted.get(k) for k in ("schools", "codes", "prefixes", "year", "kinds", "privacy")},
        })
    return _groups(per_row, _l0_summary)


def gold_intent(row: dict[str, Any]) -> dict[str, Any]:
    """Detector output with schools, codes, year and question kinds replaced by the gold intent."""
    gold = row["expected_intent"]
    intent = detect_kaoyan_intent(row["turns"][-1])
    intent.update(schools=gold["schools"], codes=gold["codes"], year=gold["year"])
    kinds = list(gold.get("metrics") or [])
    if gold["operation"] in {"compare", "filter"}:
        kinds.append(gold["operation"])
    if kinds:
        intent["kinds"] = kinds
    intent["export"] = gold["operation"] == "export"
    return intent


def default_invoke(name: str, args: dict[str, Any]) -> str:
    from kaoyan.tools.kaoyan import KAOYAN_TOOLS

    tools = {t.name: t for t in KAOYAN_TOOLS}
    return str(tools[name].invoke(args))


def run_l1(rows: list[dict[str, Any]], invoke: Invoke = default_invoke) -> dict[str, Any]:
    from kaoyan.agent.kaoyan_flow import structured_calls

    per_row = []
    for row in rows:
        if not row.get("expected_facts"):
            continue
        calls = structured_calls(gold_intent(row))
        facts = tool_facts([invoke(name, args) for name, args in calls])
        base = {"id": row["id"], "split": row["split"], "trap": row.get("trap", ""), "calls": calls}
        if not facts and any(name == "compare_programs" for name, _ in calls):
            per_row.append({**base, "status": "unscorable"})
            continue
        checks = []
        for expected in row["expected_facts"]:
            found, source_ok = match_fact(expected, facts)
            checks.append({**expected, "found": found, "source_ok": source_ok})
        per_row.append({**base, "status": "pass" if all(c["found"] for c in checks) else "fail", "facts": checks})
    return _groups(per_row, _l1_summary)


def _l1_summary(per_row: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in per_row if r["status"] != "unscorable"]
    checks = [c for r in scored for c in r["facts"]]
    found = [c for c in checks if c["found"]]
    return {
        "rows": len(scored),
        "unscorable": len(per_row) - len(scored),
        "row_pass": ratio(sum(r["status"] == "pass" for r in scored), len(scored)),
        "fact_recall": ratio(len(found), len(checks)),
        "source_match": ratio(sum(c["source_ok"] for c in found), len(found)),
    }


def regressions(current: dict[str, Any], baseline: dict[str, Any], max_drop: float) -> list[str]:
    """Metrics (overall, per split, per trap) that fell more than `max_drop` below the baseline."""
    out = []
    for layer, base in baseline.get("layers", {}).items():
        now = current.get("layers", {}).get(layer)
        if not now:
            continue
        groups = [("", base["summary"], now["summary"])]
        for key in (k for k in base if k.startswith("by_")):
            for name, old in base[key].items():
                groups.append((f"[{name}]", old, now.get(key, {}).get(name, {})))
        for tag, old_summary, new_summary in groups:
            for name, old in old_summary.items():
                new = new_summary.get(name)
                if isinstance(old, float) and isinstance(new, float) and old - new > max_drop:
                    out.append(f"{layer}{tag}.{name}: {old:.2%} -> {new:.2%}")
    return out


def known_failures(report: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Per layer, each failing row id -> its wrong L0 fields or missing L1 facts."""
    out: dict[str, dict[str, list[str]]] = {}
    l0 = report.get("layers", {}).get("L0")
    if l0:
        out["L0"] = {r["id"]: sorted(k for k, ok in r["fields"].items() if not ok) for r in l0["rows"] if not r["exact"]}
    l1 = report.get("layers", {}).get("L1")
    if l1:
        out["L1"] = {
            r["id"]: sorted(
                f"{c['program_id']} {c['kind']} {c['year']} {c.get('field', 'total')}={c['value']}"
                for c in r["facts"] if not c["found"]
            )
            for r in l1["rows"] if r["status"] == "fail"
        }
    for name, layer in report.get("layers", {}).items():
        if name.startswith("L2"):
            out[name] = known_misses(layer)
    return out


def failure_changes(current: dict[str, Any], known: dict[str, dict[str, list[str]]]) -> tuple[list[str], list[str]]:
    """(new failures, listed failures that now pass) among the rows this report scored.

    Compared per field / per fact, so the list neither hides a second bug in a failing
    row nor keeps excusing a part that has been fixed.
    """
    now = known_failures(current)
    new, fixed = [], []
    for layer, rows in current.get("layers", {}).items():
        scored = {r["id"] for r in rows["rows"]}
        listed = known.get(layer, {})
        failing = now.get(layer, {})
        for rid in sorted(scored):
            was, is_ = set(listed.get(rid, [])), set(failing.get(rid, []))
            if is_ - was:
                new.append(f"{layer} {rid}: {', '.join(sorted(is_ - was))}")
            if was - is_:
                fixed.append(f"{layer} {rid}: {', '.join(sorted(was - is_))}")
    return new, fixed


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _group_table(layer: dict[str, Any], metrics: tuple[str, ...]) -> list[str]:
    groups = [(f"切分 {k}", v) for k, v in layer.get("by_split", {}).items()]
    groups += [(f"陷阱 {k}", v) for k, v in layer.get("by_trap", {}).items()]
    if len(groups) < 2:
        return []
    lines = ["| 分组 | 题数 | " + " | ".join(metrics) + " |", "|---|---|" + "---|" * len(metrics)]
    for name, s in groups:
        lines.append(f"| {name} | {s['rows']} | " + " | ".join(_pct(s.get(m)) for m in metrics) + " |")
    return [*lines, ""]


def render_markdown(report: dict[str, Any]) -> str:
    meta = report["meta"]
    lines = [
        f"# 考研评测报告 {meta['date']}",
        "",
        f"- commit：`{meta['commit']}`",
        f"- 评测集：`{meta['gold']}`，切分 {meta['splits'] or f'除 {HIDDEN_SPLIT} 外全部'}，{meta['rows']} 道",
        f"- 层：{', '.join(report['layers'])}（不调用 LLM）",
        "",
    ]
    if HIDDEN_SPLIT in meta["splits"]:
        lines += [f"{HIDDEN_SPLIT} 切分已冻结，只报总分，不列逐题结果，也不进陷阱分组。", ""]
    l0 = report["layers"].get("L0")
    if l0:
        s = l0["summary"]
        lines += [
            "## L0 意图",
            "",
            "| 指标 | 结果 |",
            "|---|---|",
            f"| 全部字段都对 | {_pct(s['exact'])} |",
            *[f"| {name} | {_pct(s[name])} |" for name in L0_FIELDS],
            "",
            *_group_table(l0, ("exact", *L0_FIELDS)),
        ]
        misses = [r for r in l0["rows"] if not r["exact"]]
        if misses:
            lines += ["### L0 没对上的题", "", "| 题号 | 错的字段 | 识别结果 |", "|---|---|---|"]
            for r in misses:
                wrong = ", ".join(k for k, ok in r["fields"].items() if not ok)
                lines.append(f"| {r['id']} | {wrong} | `{json.dumps(r['predicted'], ensure_ascii=False)}` |")
            lines.append("")
    l1 = report["layers"].get("L1")
    if l1:
        s = l1["summary"]
        lines += [
            "## L1 工具",
            "",
            f"有标准答案事实的题 {s['rows']} 道，另有 {s['unscorable']} 道工具输出没有 program_id、暂不可评。",
            "",
            "| 指标 | 结果 |",
            "|---|---|",
            f"| 整题事实全命中 | {_pct(s['row_pass'])} |",
            f"| 事实召回 | {_pct(s['fact_recall'])} |",
            f"| 命中事实的来源一致 | {_pct(s['source_match'])} |",
            "",
            *_group_table(l1, ("row_pass", "fact_recall", "source_match")),
        ]
        fails = [r for r in l1["rows"] if r["status"] == "fail"]
        if fails:
            lines += ["### L1 缺事实的题", "", "| 题号 | 缺的事实 | 工具调用 |", "|---|---|---|"]
            for r in fails:
                missing = "; ".join(
                    f"{c['program_id']} {c['kind']} {c['year']}={c['value']}" for c in r["facts"] if not c["found"]
                )
                calls = "; ".join(f"{n}({json.dumps(a, ensure_ascii=False)})" for n, a in r["calls"])
                lines.append(f"| {r['id']} | {missing} | `{calls}` |")
            lines.append("")
    for name, layer in report["layers"].items():
        if name.startswith("L2"):
            backend = meta.get("backends", {}).get(name, "")
            lines += render_l2(f"{name}（{backend}）" if backend else name, layer)
    return "\n".join(lines)
