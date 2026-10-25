"""考研 structured tools over data/kaoyan.db.

Every tool returns JSON; every fact inside carries its 口径 and a ``source`` (doc_id,
title, url, page, year). ``citations_from_output`` turns that JSON into agent citations.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from kaoyan.config import get_settings
from kaoyan.db import get_kaoyan_store
from kaoyan.query import KaoyanQuery

KAOYAN_TOOL_NAMES = frozenset(
    {"search_programs", "get_score_lines", "get_exam_subjects", "compare_programs", "get_document", "list_sources"}
)
_MAX_CITATIONS_PER_CALL = 40


def get_query() -> KaoyanQuery:
    return KaoyanQuery(get_kaoyan_store())


def kaoyan_available() -> bool:
    """True when data/kaoyan.db exists and holds programs (never creates the file)."""
    if not get_settings().kaoyan_db_path.exists():
        return False
    return get_query().store.count("programs") > 0


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.replace("，", ",").split(",") if p.strip()]


@tool
def search_programs(
    school: str = "",
    college: str = "",
    code: str = "",
    name_kw: str = "",
    degree_type: str = "",
    study_mode: str = "",
    exam_subject: str = "",
    exclude_subject: str = "",
    is_408: bool | None = None,
    min_public_plan: int | None = None,
    year: int = 0,
) -> str:
    """Filter 考研 programs (中大/华工/暨大/华师 计算机类) and return their plans, 统招 estimates,
    score lines, exam subjects and sources.

    school: sysu/scut/jnu/scnu or 中大/华工/暨大/华师; college: 学院名或代码 (e.g. 670, 计算机学院);
    code: 6-char program code (085404) or 4-digit prefix (0854); name_kw: keyword in program or
    direction names (e.g. 人工智能); degree_type: 学硕/专硕; study_mode: 全日制/非全日制;
    exam_subject: subject code or name (e.g. 408); exclude_subject: drop programs whose exam has this
    subject (不考数学 → 数学); is_408: only programs whose exam includes 408;
    min_public_plan: 统招 >= N (for "统招>20" pass 21); year: intake year (2026/2027), 0 = all.
    Programs that cannot be decided (unknown subjects, 推免上限) are listed under "unknown" with reasons;
    filtered-out ones are under "excluded_programs" with the reason and the subjects or plan behind it.
    """
    result = get_query().search_programs(
        school=school or None,
        college=college or None,
        code=code or None,
        name_kw=name_kw or None,
        degree_type=degree_type or None,
        study_mode=study_mode or None,
        exam_subject=exam_subject or None,
        exclude_subject=exclude_subject or None,
        is_408=is_408,
        min_public_plan=min_public_plan or None,
        year=year or None,
    )
    return _dump(result)


@tool
def get_score_lines(
    school: str = "",
    code: str = "",
    college: str = "",
    year: int = 0,
    include_special: bool = True,
) -> str:
    """Return 复试线 of matching programs, each labelled with its scope: 学院复试线, 学校复试基本线,
    退役大学生士兵专项线, 少数民族骨干计划线. year: intake year; 0 = all years."""
    result = get_query().get_score_lines(
        school=school or None,
        code=code or None,
        college=college or None,
        year=year or None,
        include_special=include_special,
    )
    return _dump(result)


@tool
def get_exam_subjects(school: str = "", code: str = "", college: str = "") -> str:
    """Return 初试科目 and 研究方向 of matching programs. status is known / no_exam (仅招推免) /
    unknown (with unknown_reason, e.g. 目录未取得)."""
    return _dump(get_query().get_exam_subjects(school=school or None, code=code or None, college=college or None))


@tool
def compare_programs(
    program_ids: str = "",
    school: str = "",
    college: str = "",
    code: str = "",
    fields: str = "",
    year: int = 0,
) -> str:
    """Compare programs side by side. Select them by comma-separated program_ids
    (e.g. sysu-670-085404,scnu-019-085404) or by school/college/code filters (code=085404 compares
    that program across schools). fields: comma-separated subset of score_lines, plans, public_plan,
    subjects (default all). Returns flat table rows (ready for export_excel) plus full facts."""
    result = get_query().compare(
        program_ids=_split(program_ids) or None,
        school=school or None,
        college=college or None,
        code=code or None,
        fields=_split(fields) or None,
        year=year or None,
    )
    return _dump(result)


@tool
def get_document(doc_id: str) -> str:
    """Return metadata of one official source document (title, URL, publish date, intake year, type)
    and how many facts were extracted from it. Content of 名单类 documents is never returned."""
    doc = get_query().document(doc_id.strip())
    return _dump(doc or {"error": "document_not_found", "doc_id": doc_id})


@tool
def list_sources(school: str = "", doc_type: str = "", year: int = 0) -> str:
    """List official source documents, optionally filtered by school, doc_type (catalog, retest_rules,
    score_line, tm_catalog, notice, brochure, ...) and intake year."""
    docs = get_query().list_sources(school=school or None, doc_type=doc_type or None, year=year or None)
    return _dump({"count": len(docs), "documents": docs})


KAOYAN_TOOLS = [search_programs, get_score_lines, get_exam_subjects, compare_programs, get_document, list_sources]


# -- citations ----------------------------------------------------------------


def _program_label(p: dict[str, Any]) -> str:
    college = f"{p.get('college_code') or ''}{p.get('college') or ''}"
    return f"{p.get('school_name', '')} {college} {p.get('code', '')} {p.get('name', '')}（{p.get('study_mode', '')}）"


def _cite(source: dict[str, Any] | None, text: str, fact_id: str | None = None) -> dict[str, Any] | None:
    if not source or not source.get("doc_id"):
        return None
    cite = {
        "doc_id": source["doc_id"],
        "doc_name": source.get("title") or source["doc_id"],
        "title": source.get("title"),
        "page": source.get("page"),
        "url": source.get("url"),
        "year": source.get("year"),
        "text": text,
        "source_type": "kaoyan_db",
    }
    if fact_id:
        cite["fact_id"] = fact_id
    return cite


def _program_citations(p: dict[str, Any]) -> list[dict[str, Any] | None]:
    label = _program_label(p)
    out: list[dict[str, Any] | None] = []
    for f in p.get("plans") or []:
        out.append(_cite(f["source"], f"{label}：{f['year']} {f['label']} {f['value_text']}；口径：{f['definition']}", f.get("fact_id")))
    for e in p.get("public_plan") or []:
        formula = f"，{e['formula']}" if e.get("formula") else ""
        for src in e.get("sources") or []:
            out.append(_cite(src, f"{label}：{e['year']} 统招 {e['text']}（{e['label']}{formula}）", e.get("fact_id")))
    for ln in (p.get("score_lines") or []) + (p.get("lines") or []):
        singles = f"（单科 {ln['singles']}）" if ln.get("singles") else ""
        out.append(_cite(ln["source"], f"{label}：{ln['year']} {ln['label']} 总分 {ln['total']}{singles}；口径：{ln['definition']}", ln.get("fact_id")))
    groups = list(p.get("exam_subjects") or [])
    if p.get("subjects"):
        groups.append(p["subjects"])
    for g in groups:
        if g["status"] == "known":
            subj = " ".join(f"{s['code']}{s['name']}" for s in g.get("subjects") or []) or g.get("codes", "")
            text = f"{label}：{g['year']} 初试科目 {subj}"
        elif g["status"] == "no_exam":
            text = f"{label}：{g['year']} {g.get('unknown_reason') or '仅招收推免生'}"
        else:
            text = f"{label}：初试科目未取得（{g.get('unknown_reason') or '原因未记录'}）"
        out.append(_cite(g.get("source"), text, g.get("fact_id")))
    for s in p.get("admission_stats") or []:
        out.append(_cite(s["source"], f"{label}：{s['year']} {s['label']} {s['value']}（仅统计，不含个人信息）", s.get("fact_id")))
    for d in p.get("directions") or []:
        out.append(_cite(d.get("source"), f"{label}：{d['year']} 研究方向 {d['code'] or ''} {d['name']}"))
    return out


def citations_from_output(tool_name: str, output: Any) -> list[dict[str, Any]]:
    """Build agent citations (doc_name/title, page, url, doc_id, one evidence sentence)."""
    if tool_name not in KAOYAN_TOOL_NAMES:
        return []
    try:
        data = json.loads(str(output))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw: list[dict[str, Any] | None] = []
    if tool_name == "get_document":
        if data.get("doc_id"):
            raw.append(_cite({**data, "url": data.get("page_url") or data.get("attachment_url"),
                              "year": data.get("intake_year")}, f"{data['title']}（{data.get('publish_date') or '日期未知'}）"))
    elif tool_name == "list_sources":
        for d in (data.get("documents") or [])[:10]:
            raw.append(_cite({**d, "url": d.get("page_url") or d.get("attachment_url"), "year": d.get("intake_year")},
                             f"{d['title']}（{d.get('doc_type')}，{d.get('publish_date') or '日期未知'}）"))
    else:
        for p in (data.get("programs") or []) + (data.get("unknown") or []):
            if isinstance(p, dict):
                raw.extend(_program_citations(p))
    seen: set[tuple[str, str]] = set()
    out = []
    for c in raw:
        if c is None or (c["doc_id"], c["text"]) in seen:
            continue
        seen.add((c["doc_id"], c["text"]))
        out.append(c)
    return out[:_MAX_CITATIONS_PER_CALL]


_LLM_DROP_KEYS = frozenset({"evidence", "methods", "attachment_url", "doc_type", "publish_date", "pool_code"})


def _prune(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _prune(v)
            for k, v in value.items()
            if v is not None and k not in _LLM_DROP_KEYS and not (k == "verified" and v is True)
        }
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def _fact_value_text(obj: dict[str, Any]) -> str:
    if obj.get("value_text") not in (None, ""):
        return str(obj["value_text"])
    if obj.get("total") is not None:
        return str(obj["total"])
    if obj.get("text") not in (None, ""):
        return str(obj["text"])
    if obj.get("value") is not None:
        return str(obj["value"])
    if obj.get("codes"):
        return str(obj["codes"])
    return ""


def collect_facts(
    data: Any, start: int = 0, mapping: dict[str, dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """Walk tool JSON and assign F1, F2, … to every object that has a fact_id.

    ``mapping`` is updated in place so the same fact_id keeps one alias across tools.
    """
    mapping = mapping if mapping is not None else {}
    known = {v["fact_id"]: k for k, v in mapping.items()}
    n = max([int(k[1:]) for k in mapping if k[1:].isdigit()] + [start])

    def walk(obj: Any, parent: dict[str, Any] | None) -> None:
        nonlocal n
        if isinstance(obj, dict):
            ident = dict(parent or {})
            if obj.get("program_id") or obj.get("school"):
                ident = {
                    "program_id": obj.get("program_id") or ident.get("program_id"),
                    "school": obj.get("school") or ident.get("school"),
                    "school_name": obj.get("school_name") or ident.get("school_name"),
                    "code": obj.get("code") or ident.get("code"),
                }
            text = _fact_value_text(obj)
            fid = obj.get("fact_id")
            if fid and text and fid not in known:
                n += 1
                alias = f"F{n}"
                src = obj.get("source")
                if not src and obj.get("sources"):
                    src = obj["sources"][0]
                mapping[alias] = {
                    "alias": alias,
                    "fact_id": fid,
                    "value_text": text,
                    "value": obj.get("value", obj.get("total")),
                    "year": obj.get("year"),
                    "label": obj.get("label"),
                    "school": ident.get("school"),
                    "school_name": ident.get("school_name"),
                    "code": ident.get("code"),
                    "program_id": ident.get("program_id"),
                    "source": src,
                    "upper_bound": bool(obj.get("upper_bound")),
                }
                known[fid] = alias
            for v in obj.values():
                walk(v, ident)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, parent)

    walk(data, None)
    return mapping


def _stamp_aliases(obj: Any, by_fid: dict[str, str]) -> Any:
    if isinstance(obj, dict):
        out = {k: _stamp_aliases(v, by_fid) for k, v in obj.items()}
        fid = obj.get("fact_id")
        if fid in by_fid:
            out["alias"] = by_fid[fid]
        return out
    if isinstance(obj, list):
        return [_stamp_aliases(v, by_fid) for v in obj]
    return obj


def llm_view_and_facts(
    tool_name: str,
    output: Any,
    limit: int = 12000,
    start: int = 0,
    mapping: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Compact JSON plus {F1: fact} for placeholder rendering."""
    try:
        data = json.loads(str(output))
    except json.JSONDecodeError:
        return str(output)[:limit], mapping or {}
    before = set((mapping or {}).keys())
    mapping = collect_facts(data, start=start, mapping=mapping)
    by_fid = {v["fact_id"]: k for k, v in mapping.items()}
    if tool_name == "compare_programs" and isinstance(data, dict):
        data = {k: v for k, v in data.items() if k != "programs"}
        data["facts"] = [
            {
                "alias": alias,
                "fact_id": fact["fact_id"],
                "value_text": fact["value_text"],
                "year": fact.get("year"),
                "label": fact.get("label"),
                "school": fact.get("school_name"),
                "code": fact.get("code"),
            }
            for alias, fact in mapping.items()
        ]
    text = json.dumps(_stamp_aliases(_prune(data), by_fid), ensure_ascii=False, separators=(",", ":"))
    new = {k: v for k, v in mapping.items() if k not in before} if before else mapping
    return (text if len(text) <= limit else text[:limit] + "…[截断]"), new


def llm_view(tool_name: str, output: Any, limit: int = 12000) -> str:
    """Compact JSON of a kaoyan tool output for the model (full output stays in tool_results)."""
    text, _ = llm_view_and_facts(tool_name, output, limit)
    return text


def rows_to_markdown(title: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"# {title}\n\n官方资料中未取得可导出的数据。\n"
    cols = list(dict.fromkeys(k for r in rows for k in r))

    def cell(v: Any) -> str:
        return str(v if v not in (None, "") else "").replace("|", "\\|").replace("\n", " ")

    lines = [f"# {title}", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(cell(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def export_rows(output: Any) -> list[dict[str, Any]]:
    """Excel rows (学校/学院/专业/年份/复试线/口径/计划/来源URL/doc_id …) from a kaoyan tool output."""
    try:
        data = json.loads(str(output))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    if data.get("rows"):
        return list(data["rows"])
    programs = [p for p in data.get("programs") or [] if isinstance(p, dict) and "plans" in p]
    fields = {"score_lines", "plans", "public_plan", "subjects"}
    return [KaoyanQuery.table_row(p, fields) for p in programs]
