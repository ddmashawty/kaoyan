"""L2: does retrieval reach the official text that answers a narrative question?

Each question is searched twice. "filtered" passes the gold schools as the `school`
filter (one search per school, merged rank by rank); "unfiltered" passes none, as
`rag_search` does by default, so the store still pins a single school the question names.

A source document counts as hit when some top-k chunk comes from it. An evidence
fragment counts as hit when one top-k chunk of its document holds at least 80% of the
fragment's character bigrams, which tolerates a fragment split by a chunk boundary.

Reports carry document and chunk ids only, never chunk text: the local index also
holds redacted name lists.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kaoyan.eval.drafts import bigram_overlap, bigrams, overlap_layer, question_shape
from kaoyan.eval.metrics import ratio

KS = (3, 5, 10)
MODES = ("filtered", "unfiltered")
DOC_METRICS = ("doc_any", "doc_all")
CHUNK_METRICS = ("chunk_any", "chunk_all")
FRAGMENT_COVER = 0.8
HIDDEN_SPLIT = "test"

# (question, school or None, k) -> hits with .chunk_id, .text and .metadata["doc_id"], best first
Search = Callable[[str, str | None, int], list[Any]]


def load_spec_sources(gold_dir: Path) -> dict[str, list[str]]:
    """Question id -> source doc ids listed in the handwritten specs (data/gold/*.json)."""
    out: dict[str, list[str]] = {}
    for path in sorted(gold_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        for item in items or []:
            if isinstance(item, dict) and item.get("id") and item.get("sources"):
                out[item["id"]] = list(item["sources"])
    return out


def source_docs(row: dict[str, Any], spec_sources: dict[str, list[str]]) -> list[str]:
    docs = [f["source_doc_id"] for f in row.get("expected_facts") or [] if f.get("source_doc_id")]
    docs += spec_sources.get(row["id"], [])
    docs += [f["doc"] for f in row.get("evidence") or []]
    return list(dict.fromkeys(docs))


def _interleave(lists: list[list[Any]], k: int) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for rank in range(max((len(hits) for hits in lists), default=0)):
        for hits in lists:
            if rank < len(hits) and hits[rank].chunk_id not in seen:
                seen.add(hits[rank].chunk_id)
                out.append(hits[rank])
    return out[:k]


def retrieve(row: dict[str, Any], mode: str, search: Search, k: int) -> list[Any]:
    question = row["turns"][-1]
    schools = row["expected_intent"]["schools"] if mode == "filtered" else []
    if not schools:
        return search(question, None, k)
    return _interleave([search(question, school, k) for school in schools], k)


def _doc_id(hit: Any) -> str | None:
    return (hit.metadata or {}).get("doc_id")


def score_hits(hits: list[Any], docs: list[str], fragments: list[dict[str, str]]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for k in KS:
        top = hits[:k]
        found = {_doc_id(h) for h in top}
        doc_hits = [d in found for d in docs]
        out[f"doc_any@{k}"] = any(doc_hits)
        out[f"doc_all@{k}"] = all(doc_hits)
        if fragments:
            grams = [(_doc_id(h), bigrams(h.text)) for h in top]
            frag_hits = []
            for frag in fragments:
                need = bigrams(frag["text"])
                frag_hits.append(bool(need) and any(
                    doc == frag["doc"] and len(need & have) >= FRAGMENT_COVER * len(need) for doc, have in grams
                ))
            out[f"chunk_any@{k}"] = any(frag_hits)
            out[f"chunk_all@{k}"] = all(frag_hits)
    return out


def l2_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["category"] == "narrative"]


def run_l2(
    rows: list[dict[str, Any]],
    search: Search,
    indexed_docs: set[str],
    spec_sources: dict[str, list[str]],
) -> dict[str, Any]:
    """Questions whose source documents are not all indexed are skipped, not counted as misses."""
    per_row = []
    for row in l2_rows(rows):
        fragments = row.get("evidence") or []
        overlap = bigram_overlap(row["turns"][-1], [f["text"] for f in fragments]) if fragments else None
        base = {
            "id": row["id"],
            "split": row["split"],
            "shape": question_shape(row),
            "overlap": "none" if overlap is None else overlap_layer(overlap),
        }
        docs = source_docs(row, spec_sources)
        missing = [d for d in docs if d not in indexed_docs]
        if not docs or missing:
            per_row.append({**base, "status": "skipped", "missing": missing})
            continue
        hits: dict[str, bool] = {}
        retrieved: dict[str, list[str | None]] = {}
        for mode in MODES:
            top = retrieve(row, mode, search, max(KS))
            retrieved[mode] = [_doc_id(h) for h in top]
            hits.update({f"{mode} {name}": ok for name, ok in score_hits(top, docs, fragments).items()})
        per_row.append({**base, "status": "scored", "docs": docs, "fragments": len(fragments),
                        "hits": hits, "retrieved": retrieved})
    return _groups(per_row)


def _summary(per_row: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in per_row if r["status"] == "scored"]
    with_evidence = [r for r in scored if r["fragments"]]
    out: dict[str, Any] = {"rows": len(scored), "evidence_rows": len(with_evidence),
                           "skipped": len(per_row) - len(scored)}
    for mode in MODES:
        for k in KS:
            for name in DOC_METRICS:
                key = f"{mode} {name}@{k}"
                out[key] = ratio(sum(r["hits"][key] for r in scored), len(scored))
            for name in CHUNK_METRICS:
                key = f"{mode} {name}@{k}"
                out[key] = ratio(sum(r["hits"][key] for r in with_evidence), len(with_evidence))
    return out


def _groups(per_row: list[dict[str, Any]]) -> dict[str, Any]:
    """Like the L0/L1 report: test rows only count toward the totals and their split total."""
    by_split: dict[str, list] = {}
    by_shape: dict[str, list] = {}
    by_overlap: dict[str, list] = {}
    for r in per_row:
        by_split.setdefault(r["split"], []).append(r)
        if r["split"] != HIDDEN_SPLIT:
            by_shape.setdefault(r["shape"], []).append(r)
            by_overlap.setdefault(r["overlap"], []).append(r)
    return {
        "summary": _summary(per_row),
        "by_split": {k: _summary(v) for k, v in sorted(by_split.items())},
        "by_shape": {k: _summary(v) for k, v in sorted(by_shape.items())},
        "by_overlap": {k: _summary(v) for k, v in sorted(by_overlap.items())},
        "rows": [r for r in per_row if r["split"] != HIDDEN_SPLIT],
    }


def known_misses(layer: dict[str, Any]) -> dict[str, list[str]]:
    """Scored row id -> the mode / metric / k it misses."""
    return {
        r["id"]: sorted(key for key, ok in r["hits"].items() if not ok)
        for r in layer["rows"]
        if r["status"] == "scored" and not all(r["hits"].values())
    }


def _count(summary: dict[str, Any], key: str) -> str:
    value = summary.get(key)
    total = summary["evidence_rows"] if key.split(" ")[-1].startswith("chunk") else summary["rows"]
    return "—" if value is None else f"{round(value * total)}/{total}"


SHAPE_NAMES = {"single": "单校", "cross": "跨校", "no_school": "不带校名", "unknown": "原文无答案"}
OVERLAP_NAMES = {"low": "低 [0, 0.1)", "mid": "中 [0.1, 0.3)", "high": "高 [0.3, 1]", "none": "无证据片段"}
MODE_NAMES = {"filtered": "过滤", "unfiltered": "不过滤"}
METRIC_NAMES = {"doc_any": "文档 any", "doc_all": "文档 all", "chunk_any": "片段 any", "chunk_all": "片段 all"}


def render_l2(name: str, layer: dict[str, Any]) -> list[str]:
    s = layer["summary"]
    lines = [
        f"## {name} 检索",
        "",
        f"计分 {s['rows']} 道（其中带证据片段 {s['evidence_rows']} 道），"
        f"{s['skipped']} 道的来源文档不在索引里、跳过。只报命中数。",
        "",
    ]
    for mode in MODES:
        label = "按 gold 学校过滤" if mode == "filtered" else "不传学校（检索库会按题里唯一的校名自动过滤）"
        header = " | ".join(METRIC_NAMES[m] for m in (*DOC_METRICS, *CHUNK_METRICS))
        lines += [f"### {label}", "", f"| k | {header} |", "|---|---|---|---|---|"]
        for k in KS:
            cells = [_count(s, f"{mode} {m}@{k}") for m in (*DOC_METRICS, *CHUNK_METRICS)]
            lines.append(f"| {k} | " + " | ".join(cells) + " |")
        lines.append("")
    groups = [(f"切分 {k}", v) for k, v in layer["by_split"].items()]
    groups += [(f"题型 {SHAPE_NAMES.get(k, k)}", v) for k, v in layer["by_shape"].items()]
    groups += [(f"重合度 {OVERLAP_NAMES.get(k, k)}", v) for k, v in layer["by_overlap"].items()]
    pairs = [(mode, m) for mode in MODES for m in (*DOC_METRICS, *CHUNK_METRICS)]
    cols = [f"{mode} {m}@5" for mode, m in pairs]
    lines += [
        "### 分组（k=5）",
        "",
        "| 分组 | 计分题数 | " + " | ".join(f"{MODE_NAMES[mode]} {METRIC_NAMES[m]}" for mode, m in pairs) + " |",
        "|---|---|" + "---|" * len(cols),
    ]
    for label, g in groups:
        lines.append(f"| {label} | {g['rows']} | " + " | ".join(_count(g, c) for c in cols) + " |")
    lines.append("")
    misses = [r for r in layer["rows"] if r["status"] == "scored"
              and not all(ok for key, ok in r["hits"].items() if key.endswith("@5"))]
    if misses:
        lines += ["### k=5 没全中的题", "", "| 题号 | 题型 | 重合度 | 没中的 | 不过滤时前 5 的文档 |", "|---|---|---|---|---|"]
        for r in misses:
            missed = ", ".join(key.removesuffix("@5") for key, ok in r["hits"].items() if key.endswith("@5") and not ok)
            top = ", ".join(str(d) for d in r["retrieved"]["unfiltered"][:5])
            shape, overlap = SHAPE_NAMES.get(r["shape"], r["shape"]), OVERLAP_NAMES.get(r["overlap"], r["overlap"])
            lines.append(f"| {r['id']} | {shape} | {overlap} | {missed} | {top} |")
        lines.append("")
    skipped = [r for r in layer["rows"] if r["status"] == "skipped"]
    if skipped:
        lines += ["跳过的题：" + "；".join(f"{r['id']}（缺 {', '.join(r['missing']) or '来源'}）" for r in skipped), ""]
    return lines
