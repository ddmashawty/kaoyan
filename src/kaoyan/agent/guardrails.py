from __future__ import annotations

import re
from typing import Any


EXPORT_HINTS = ("导出", "excel", "Excel", "xlsx", "markdown", "Markdown", "报告文件", "落盘")
EXCEL_HINTS = ("excel", "xlsx", "Excel", "电子表格")


def wants_export(goal: str) -> bool:
    return any(k in goal for k in EXPORT_HINTS)


def wants_excel(goal: str) -> bool:
    g = goal.lower()
    return any(k.lower() in g for k in EXCEL_HINTS) or ("表格" in goal and "导出" in goal)


def infer_export_filename(goal: str) -> str:
    m = re.search(r"文件名\s*[：:]?\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)", goal)
    if m:
        return m.group(1)
    if wants_excel(goal):
        return "export_table"
    return "export_report"


def tool_call_count(results: list[dict[str, Any]], name: str) -> int:
    return sum(1 for r in results if r.get("tool") == name)


def recent_tools(results: list[dict[str, Any]], n: int = 5) -> list[str]:
    return [str(r.get("tool") or "") for r in results[-n:]]


# -- 考研 intent ------------------------------------------------------------

PROGRAM_CODE_RE = re.compile(r"(?<![0-9A-Za-z])((?:0[1-9]|1[0-4])\d{2}[0-9A-Z]{2})(?![0-9A-Za-z])")
DISCIPLINE_RE = re.compile(r"(?<![0-9A-Za-z])((?:0[1-9]|1[0-4])\d{2})(?![0-9A-Za-z])")
YEAR_RE = re.compile(r"(?<!\d)(20[2-3]\d)(?!\d)")
COLLEGE_RE = re.compile(r"([\u4e00-\u9fff]{2,14}?学院)")
KAOYAN_STRONG_HINTS = (
    "复试", "推免", "统招", "统考", "考研", "招生", "研究生", "硕士", "408", "初试", "拟录取",
    "专硕", "学硕", "调剂", "分数线", "研招", "招多少", "考什么",
)
_KIND_HINTS: dict[str, tuple[str, ...]] = {
    "score_line": ("复试线", "分数线", "国家线", "校线", "基本线", "多少分", "进复试", "单科线", "线多少"),
    "plan": ("招多少", "招几", "招得", "多少人", "几个人", "计划", "名额", "统招", "统考招", "推免多少", "公开招考", "拟招", "目录"),
    "subjects": ("考什么", "考不考", "考408", "考 408", "初试科目", "科目", "专业课", "考哪", "考数学", "数学考"),
    "compare": ("对比", "比较", "相比", "差多少", "一样吗", "vs", "VS"),
    "filter": ("哪些", "筛选", "有哪些", "哪几个", "列出所有"),
    "directions": ("有没有", "方向"),
}
_PRIVACY_RE = re.compile(
    r"(名单|录取|复试名单|拟录取).{0,12}(有没有|是否有|查|找|姓名|名字|编号|某某)"
    r"|考生编号|准考证|姓名|个人成绩|是谁|叫什么|名单上|他进复试|他考了|他被"
    r"|叫[\u4e00-\u9fff]{2,4}的考生|同学.{0,40}他"
)
_COMPARE_TWO_RE = re.compile(r"各(?!占)|哪个.{0,12}(?:好考|更好|招得)|能直接比")
FOUR_SCHOOLS = ("sysu", "scut", "jnu", "scnu")
_THRESHOLD_RE = re.compile(
    r"(统招|统考|公开招考)[^0-9]{0,6}?(>=|≥|>|＞|大于等于|大于|超过|多于|不少于|至少)\s*(\d+)"
)
_SKIP_SUBJECT_RE = re.compile(r"不(?:用|需要)?考\s*(\d{3}|数学[一二]?|英语[一二]?|政治|日语|俄语)")
_SUBJECT_CODES = {"数学一": "301", "数学二": "302", "英语一": "201", "英语二": "204"}
_NAME_KW_RE = re.compile(r"有没有([\u4e00-\u9fffA-Za-z]{2,12}?)(?:学硕|专硕|专业|方向|学位|[（(？?，,]|$)")
_COLLEGE_PREFIX_STRIP = "的在查看问请帮我"


def _strip_school_aliases(text: str) -> str:
    from kaoyan.normalize import DEFAULT_SCHOOL_ALIASES

    out = text
    for names in DEFAULT_SCHOOL_ALIASES.values():
        for alias in sorted(names, key=len, reverse=True):
            out = out.replace(alias, " ")
    return out


def detect_kaoyan_intent(goal: str) -> dict[str, Any]:
    """Rule-based 考研 intent: schools, program codes, colleges, year, question kinds, filters."""
    from kaoyan.normalize import find_schools

    text = goal or ""
    schools = find_schools(text)
    if "四校" in text:
        schools = list(dict.fromkeys([*schools, *FOUR_SCHOOLS]))
    codes = list(dict.fromkeys(PROGRAM_CODE_RE.findall(text)))
    masked = PROGRAM_CODE_RE.sub(" ", text)
    prefixes = [p for p in dict.fromkeys(DISCIPLINE_RE.findall(masked)) if not YEAR_RE.fullmatch(p)]
    colleges = []
    for m in COLLEGE_RE.finditer(_strip_school_aliases(text)):
        name = m.group(1).lstrip(_COLLEGE_PREFIX_STRIP)
        if len(name) > 2:
            colleges.append(name)
    years = [int(y) for y in YEAR_RE.findall(text)]
    is_kaoyan = bool(schools or codes or any(k in text for k in KAOYAN_STRONG_HINTS) or "四校" in text)

    kinds = [k for k, hints in _KIND_HINTS.items() if any(h in text for h in hints)]
    if "导师" in text and "filter" in kinds:
        kinds.remove("filter")
    if re.search(r"推免(?!面试)", text) and "plan" not in kinds:
        kinds.append("plan")
    if "非全" in text:
        for kind in ("plan", "score_line"):
            if kind not in kinds:
                kinds.append(kind)
    two_targets = len(schools) >= 2 or len(codes) >= 2
    if "compare" not in kinds and (
        (two_targets and _COMPARE_TWO_RE.search(text)) or ("最高" in text and "最低" in text)
    ):
        kinds.append("compare")
    if "四校" in text and "compare" not in kinds and ("复试线" in text or "计划" in text):
        kinds.append("compare")
    privacy = bool(_PRIVACY_RE.search(text))
    if privacy:
        kinds.append("privacy")
    if wants_export(text):
        kinds.append("export")
    if not kinds:
        kinds.append("narrative")

    filters: dict[str, Any] = {}
    if "filter" in kinds or "compare" in kinds:
        skip = _SKIP_SUBJECT_RE.search(text)
        if skip and skip.group(1) == "408":
            filters["is_408"] = False
        elif skip:
            filters["exclude_subject"] = _SUBJECT_CODES.get(skip.group(1), skip.group(1))
        elif "408" in text:
            filters["is_408"] = True
        if "非全日制" in text or "非全" in text:
            filters["study_mode"] = "非全日制"
        elif "全日制" in text:
            filters["study_mode"] = "全日制"
        if "学硕" in text and "专硕" not in text:
            filters["degree_type"] = "学硕"
        elif "专硕" in text and "学硕" not in text:
            filters["degree_type"] = "专硕"
    m = _THRESHOLD_RE.search(text)
    if m:
        n = int(m.group(3))
        strict = m.group(2) in (">", "＞", "大于", "超过", "多于")
        filters["min_public_plan"] = n + 1 if strict else n
        if "filter" not in kinds:
            kinds.append("filter")
    name_kw = None
    if not privacy:
        nm = _NAME_KW_RE.search(text)
        if nm:
            name_kw = nm.group(1)

    numeric = bool(set(kinds) & {"score_line", "plan", "filter", "compare"})
    return {
        "is_kaoyan": is_kaoyan,
        "schools": schools,
        "codes": codes,
        "prefixes": prefixes,
        "colleges": colleges,
        "year": years[0] if len(years) == 1 else None,
        "kinds": kinds,
        "numeric": numeric,
        "privacy": privacy,
        "export": "export" in kinds,
        "filters": filters,
        "name_kw": name_kw,
    }


# -- number validation --------------------------------------------------------

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_PROTECTED_RES = (
    PROGRAM_CODE_RE,
    re.compile(r"(?<![0-9A-Za-z.])0[1-9]\d{2}(?![0-9A-Za-z])"),
    re.compile(r"https?://\S+"),
    re.compile(r"[A-Za-z0-9.-]+\.(?:edu|com|cn|org|net)(?:\.cn)?(?:/[^\s，。；）)]*)?"),
    re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*(?:\.[A-Za-z]{2,5})?"),
    re.compile(r"(?m)^\s*\d+[.、)）]\s"),
    re.compile(r"第\s*\d+"),
)


def _canon(token: str) -> str:
    if "." in token:
        return token.rstrip("0").rstrip(".") or "0"
    return str(int(token))


def _is_exempt_number(token: str) -> bool:
    return len(token) == 4 and "." not in token and 1990 <= int(token) <= 2035


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for rx in _PROTECTED_RES:
        spans.extend(m.span() for m in rx.finditer(text))
    return spans


def _unsupported_matches(answer: str, evidence: str) -> list[re.Match[str]]:
    known = {_canon(t) for t in _NUMBER_RE.findall(evidence)}
    spans = _protected_spans(answer)
    out = []
    for m in _NUMBER_RE.finditer(answer):
        if any(s <= m.start() and m.end() <= e for s, e in spans):
            continue
        tok = m.group()
        if _is_exempt_number(tok) or _canon(tok) in known:
            continue
        out.append(m)
    return out


def find_unsupported_numbers(answer: str, evidence: str) -> list[str]:
    """Numbers in the answer that do not occur in the evidence. Years, program codes,
    discipline codes (0812), URLs/file names and list ordinals are exempt."""
    return list(dict.fromkeys(m.group() for m in _unsupported_matches(answer, evidence)))


def strip_unsupported_numbers(answer: str, evidence: str, marker: str = "（依据不足）") -> str:
    out = answer
    for m in reversed(_unsupported_matches(answer, evidence)):
        out = out[: m.start()] + marker + out[m.end() :]
    return out
