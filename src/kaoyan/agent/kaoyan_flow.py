"""考研 path of the agent: deterministic tool plan from the intent, reflection without
LLM, and a final answer whose numbers are checked against the tool evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from kaoyan.agent.guardrails import (
    find_unsupported_numbers,
    infer_export_filename,
    strip_unsupported_numbers,
    tool_call_count,
    wants_excel,
)
from kaoyan.agent.prompts import KAOYAN_FINAL_SYSTEM, KAOYAN_RETRY_PROMPT
from kaoyan.config import get_settings
from kaoyan.render import lookup_template, render_placeholders
from kaoyan.tools import kaoyan as ky

RunTool = Callable[[str, dict[str, Any]], None]

_EVIDENCE_BUDGET = 28000
_RAG_SNIPPETS = 6
PRIVACY_NOTICE = "按隐私规则，不能提供名单中的个人信息（姓名、考生编号、个人成绩），只能给统计数字。"
UNSUPPORTED_NOTE = "（注：部分数字在官方资料证据中找不到，已标为“依据不足”。）"
OUT_OF_SCOPE_REPLY = (
    "我只回答广东四校（中山大学、华南理工大学、暨南大学、华南师范大学）计算机类硕士招生的问题，"
    "比如招生计划、复试线、初试科目、推免人数。请换一个考研相关的问题。"
)
UNAVAILABLE_REPLY = (
    "考研结构化库还没建好，暂时答不了。"
    "请先运行 scripts/seed_kaoyan.py 和 scripts/extract_kaoyan.py 建库。"
)


def direct_reply(intent: dict[str, Any]) -> str:
    """Fixed answer for questions that never reach the tools: off-topic, or the 考研 DB is missing."""
    return UNAVAILABLE_REPLY if intent.get("is_kaoyan") else OUT_OF_SCOPE_REPLY


def plan_steps(intent: dict[str, Any]) -> list[str]:
    kinds = set(intent.get("kinds") or [])
    steps = []
    if intent.get("privacy"):
        steps.append("search_programs 只取统计（名单类不输出个人信息）")
    elif "filter" in kinds:
        steps.append(f"search_programs 按条件筛选 {intent.get('filters')}")
    elif kinds & {"compare"} or intent.get("export"):
        steps.append("compare_programs 并列对比")
    else:
        if "score_line" in kinds:
            steps.append("get_score_lines 查复试线（标口径）")
        if "subjects" in kinds:
            steps.append("get_exam_subjects 查初试科目")
        steps.append("search_programs 取计划/备注/来源")
    if "narrative" in kinds:
        steps.append("rag_search 检索官方原文")
    if intent.get("export"):
        steps.append("export_excel 导出（含年份/口径/来源URL/doc_id）")
    steps.append("按证据作答并校验数字")
    return steps[:4]


def _nonempty(**kw: Any) -> dict[str, Any]:
    return {k: v for k, v in kw.items() if v not in (None, "", 0)}


def structured_calls(intent: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Structured tool calls for iteration 0, derived from the intent only."""
    q = ky.get_query()
    schools: list[str] = intent.get("schools") or []
    codes: list[str] = intent.get("codes") or []
    prefixes: list[str] = intent.get("prefixes") or []
    school = schools[0] if len(schools) == 1 else ""
    code = codes[0] if len(codes) == 1 else (prefixes[0] if not codes and len(prefixes) == 1 else "")
    college = (intent.get("colleges") or [""])[0]
    if college and not q.find_programs(school=school or None, college=college):
        college = ""
    kinds = set(intent.get("kinds") or [])
    year = intent.get("year")
    scope = _nonempty(school=school, college=college, code=code)

    if intent.get("privacy"):
        return [("search_programs", scope)]
    if "filter" in kinds:
        return [("search_programs", {**scope, **(intent.get("filters") or {}), **_nonempty(year=year)})]
    if "compare" in kinds or intent.get("export"):
        fields: list[str] = []
        if not intent.get("export"):
            if "score_line" in kinds:
                fields.append("score_lines")
            if "plan" in kinds:
                fields += ["plans", "public_plan"]
            if "subjects" in kinds:
                fields.append("subjects")
        args: dict[str, Any] = _nonempty(fields=",".join(fields), year=year or 0)
        if len(codes) > 1 or len(schools) > 1:
            ids = [
                p["id"]
                for c in (codes or [code])
                for p in q.find_programs(code=c or None, college=college or None)
                if not schools or p["school_id"] in schools
            ]
            args["program_ids"] = ",".join(dict.fromkeys(ids))
        else:
            args.update(scope)
        return [("compare_programs", args)]

    calls: list[tuple[str, dict[str, Any]]] = []
    scopes = [_nonempty(school=school, college=college, code=c) for c in codes] if len(codes) > 1 else [scope]
    for s in (s for s in scopes if s):
        if "score_line" in kinds:
            calls.append(("get_score_lines", {**s, "year": year if year is not None else q.resolve_year(None, "score_lines")}))
        if "subjects" in kinds:
            calls.append(("get_exam_subjects", s))
        calls.append(("search_programs", s))
    if intent.get("name_kw"):
        calls.append(("search_programs", _nonempty(school=school, name_kw=intent["name_kw"])))
    return calls


def needs_rag(intent: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]) -> bool:
    if intent.get("privacy"):
        return False
    return not calls or "narrative" in (intent.get("kinds") or [])


def rag_args(intent: dict[str, Any], goal: str) -> dict[str, Any]:
    schools = intent.get("schools") or []
    return {
        "query": goal,
        "top_k": get_settings().top_k,
        "profile": "kaoyan",
        **({"school": schools[0]} if len(schools) == 1 else {}),
    }


def _loads(output: Any) -> Any:
    try:
        return json.loads(str(output))
    except json.JSONDecodeError:
        return None


def structured_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in results if r.get("tool") in ky.KAOYAN_TOOL_NAMES]


def has_structured_evidence(results: list[dict[str, Any]]) -> bool:
    for r in structured_results(results):
        data = _loads(r["output"])
        if isinstance(data, dict) and (data.get("programs") or data.get("unknown") or data.get("rows")
                                       or data.get("documents") or data.get("doc_id")):
            return True
    return False


def gap_notes(intent: dict[str, Any], results: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    outs = structured_results(results)
    years = {int(y) for r in outs for y in re.findall(r'"year": (20\d{2})', str(r["output"]))}
    want = intent.get("year")
    if want and years and want not in years:
        notes.append(
            f"结构化库中没有 {want} 年的相关数据（这些专业库内最新为 {max(years)} 年）；"
            f"{want} 年的数字应写“官方资料中未取得”，其他年份数据只能标明年份作补充。"
        )
    if outs and not has_structured_evidence(results):
        notes.append("结构化库中没有匹配的专业（数据范围：中大/华工/暨大/华师 计算机类 37 个专业）。")
    if intent.get("privacy"):
        notes.append("用户在查名单中的个人：" + PRIVACY_NOTICE)
    return notes


def bootstrap(state: dict[str, Any], run: RunTool) -> None:
    """Iteration 0: structured tools by intent, rag for narrative questions, export when asked."""
    intent = state["intent"]
    goal = state["user_goal"]
    calls = structured_calls(intent)
    for name, args in calls:
        run(name, args)
    if needs_rag(intent, calls):
        run("rag_search", rag_args(intent, goal))


def export(state: dict[str, Any], results: list[dict[str, Any]], run: RunTool) -> None:
    goal = state["user_goal"]
    src = next(
        (r for r in reversed(results) if r["tool"] in {"compare_programs", "search_programs", "get_score_lines"}), None
    )
    rows = ky.export_rows(src["output"]) if src else []
    if src and src["tool"] == "get_score_lines":
        rows = []
    if not rows:
        rows = [{"说明": "官方资料中未取得可导出的结构化数据"}]
    filename = infer_export_filename(goal)
    if filename in {"export_table", "export_report"}:
        codes = (state["intent"].get("codes") or []) + (state["intent"].get("prefixes") or [])
        filename = "kaoyan_" + ("_".join(codes[:3]) if codes else "programs")
    title = f"考研数据导出 {filename}"
    if wants_excel(goal) or "markdown" not in goal.lower():
        run("export_excel", {"title": title, "rows_json": json.dumps(rows, ensure_ascii=False), "filename": filename})
    else:
        run("export_markdown", {"title": title, "content": ky.rows_to_markdown(title, rows), "filename": filename})


def continue_act(state: dict[str, Any], results: list[dict[str, Any]], exports: list[dict[str, Any]], run: RunTool) -> None:
    """Later iterations: export if still missing, otherwise one rag_search fallback."""
    intent = state["intent"]
    if intent.get("export") and not exports:
        export(state, results, run)
    elif tool_call_count(results, "rag_search") == 0:
        run("rag_search", rag_args(intent, state["user_goal"]))


def reflect(state: dict[str, Any]) -> dict[str, Any]:
    intent = state.get("intent") or {}
    results = state.get("tool_results") or []
    exports = state.get("exports") or []
    notes = gap_notes(intent, results)
    facts = {**(state.get("facts") or {}), "notes": notes}
    if intent.get("export") and not exports:
        return {"reflection": "要求导出但尚未生成文件。", "should_retry": True,
                "retry_hint": "export_excel", "facts": facts, "status": "reflected"}
    if has_structured_evidence(results):
        return {"reflection": "已有结构化证据（含 unknown 说明），结束工具循环。", "should_retry": False,
                "retry_hint": "", "facts": facts, "status": "reflected"}
    if tool_call_count(results, "rag_search") == 0 and not intent.get("privacy"):
        hint = "结构化库没有匹配，调用 rag_search 检索官方原文"
        if intent.get("numeric"):
            hint += "；数字题请带学校/专业代码再问，或用 search_programs / get_score_lines 查询"
        return {"reflection": "数字题缺少结构化证据。", "should_retry": True, "retry_hint": hint,
                "facts": facts, "status": "reflected"}
    return {"reflection": "证据已尽，按现有证据作答（缺失处写官方资料中未取得）。", "should_retry": False,
            "retry_hint": "", "facts": facts, "status": "reflected"}


def _evidence_blocks(state: dict[str, Any]) -> tuple[str, str, dict[str, dict[str, Any]]]:
    """(text shown to the model, raw text numbers are validated against, Fk → fact)."""
    results = state.get("tool_results") or []
    outs = structured_results(results)
    per_tool = _EVIDENCE_BUDGET // max(1, len(outs))
    parts = ["【结构化证据】"]
    fact_map: dict[str, dict[str, Any]] = {}
    for r in outs:
        args = json.dumps(r.get("args") or {}, ensure_ascii=False)
        view, _ = ky.llm_view_and_facts(r["tool"], r["output"], per_tool, mapping=fact_map)
        parts.append(f"- {r['tool']}({args}):\n{view}")
    if len(outs) == 0:
        parts.append("（无）")
    rag = [c for c in state.get("citations") or [] if c.get("source_type") != "kaoyan_db"][:_RAG_SNIPPETS]
    if rag:
        parts.append("【检索片段】")
        for c in rag:
            text = (c.get("text") or "").replace("\n", " ")[:500]
            parts.append(f"- ({c.get('doc_name')} p.{c.get('page')}) {text}")
    notes = (state.get("facts") or {}).get("notes") or []
    if notes:
        parts.append("【提示】\n" + "\n".join(f"- {n}" for n in notes))
    exports = state.get("exports") or []
    if exports:
        parts.append("【导出文件】\n" + "\n".join(f"- {e.get('path')}（{e.get('rows', '')} 行）" for e in exports))
    shown = "\n".join(parts)
    raw = "\n".join(
        [state["user_goal"], *(str(r["output"]) for r in outs), *((c.get("text") or "") for c in rag), *notes,
         *(json.dumps(e, ensure_ascii=False) for e in exports)]
    )
    return shown, raw, fact_map


def _retry_problems(validation: dict[str, Any]) -> str:
    bits: list[str] = []
    if validation.get("unsupported"):
        bits.append("证据中没有的数字：" + "、".join(validation["unsupported"]))
    if validation.get("missing"):
        bits.append("不存在的编号：" + "、".join(validation["missing"]))
    if validation.get("misattributed"):
        bits.append("写到别的学校或专业名下的编号：" + "、".join(validation["misattributed"]))
    return "；".join(bits)


def _bind_answer(answer: str, raw: str, fact_map: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    rendered, attr = render_placeholders(answer, fact_map)
    unsupported = find_unsupported_numbers(rendered, raw)
    return rendered, {**attr, "unsupported": unsupported}


def finalize(state: dict[str, Any], llm: Any) -> tuple[str, dict[str, Any]]:
    shown, raw, fact_map = _evidence_blocks(state)
    intent = state.get("intent") or {}
    templated = lookup_template(intent, fact_map)
    empty = {"unsupported": [], "regenerated": False, "stripped": [], "missing": [], "misattributed": []}
    if templated:
        validation = {**empty, "template": True, "fact_map": {k: v["fact_id"] for k, v in fact_map.items()}}
        return templated, validation

    messages = [
        SystemMessage(content=KAOYAN_FINAL_SYSTEM),
        HumanMessage(content=f"用户问题：{state['user_goal']}\n\n{shown}"),
    ]
    answer = str(llm.invoke(messages).content).strip()
    bound, checks = _bind_answer(answer, raw, fact_map)
    validation: dict[str, Any] = {
        **empty,
        **checks,
        "template": False,
        "fact_map": {k: v["fact_id"] for k, v in fact_map.items()},
    }
    problems = validation["unsupported"] or validation["missing"] or validation["misattributed"]
    if problems:
        retry = messages + [
            AIMessage(content=answer),
            HumanMessage(content=KAOYAN_RETRY_PROMPT.format(problems=_retry_problems(validation))),
        ]
        answer = str(llm.invoke(retry).content).strip()
        bound, retry_checks = _bind_answer(answer, raw, fact_map)
        validation["regenerated"] = True
        still = retry_checks["unsupported"] or retry_checks["missing"] or retry_checks["misattributed"]
        if still:
            if retry_checks["unsupported"]:
                validation["stripped"] = list(retry_checks["unsupported"])
                bound = strip_unsupported_numbers(bound, raw).rstrip()
            bound = bound.rstrip() + "\n\n" + UNSUPPORTED_NOTE
    if intent.get("privacy") and "个人信息" not in bound:
        bound = PRIVACY_NOTICE + "\n\n" + bound
    return bound, validation
