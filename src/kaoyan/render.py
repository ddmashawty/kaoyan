"""Bind answer numbers to structured facts via [[Fk]] placeholders."""

from __future__ import annotations

import re
from typing import Any

from kaoyan.normalize import DEFAULT_SCHOOL_ALIASES

PLACEHOLDER_RE = re.compile(r"\[\[(F\d+)\]\]")
_CODE_RE = re.compile(r"\b\d{6}\b")
_PREFIX_RE = re.compile(r"\b\d{4}\b")


def source_label(source: dict[str, Any] | None) -> str:
    if not source:
        return ""
    title = source.get("title") or source.get("doc_id") or ""
    url = source.get("url") or ""
    date = source.get("publish_date") or ""
    return "，".join(p for p in (title, url, date) if p)


def schools_mentioned(text: str) -> set[str]:
    found: set[str] = set()
    for sid, aliases in DEFAULT_SCHOOL_ALIASES.items():
        for name in (sid, *aliases):
            if len(name) >= 2 and name in text:
                found.add(sid)
                break
    return found


def codes_mentioned(text: str) -> list[str]:
    return list(dict.fromkeys(_CODE_RE.findall(text) + _PREFIX_RE.findall(text)))


def _code_matches(fact_code: str | None, mentioned: list[str]) -> bool:
    if not fact_code or not mentioned:
        return True
    return any(
        c == fact_code or fact_code.startswith(c) or (len(c) == 6 and c.startswith(fact_code[:4]))
        for c in mentioned
    )


def iter_segments(text: str) -> list[str]:
    """Sentences, plus each Markdown table row as its own segment."""
    segs: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("|"):
            segs.append(line)
            continue
        parts = re.findall(r"[^。！？\n]+[。！？]?", line)
        segs.extend(parts if parts else [line])
    return segs or [text]


def check_attribution(text: str, fact_map: dict[str, dict[str, Any]]) -> list[str]:
    """Aliases whose sentence/row names a school or code that is not this fact's program."""
    bad: list[str] = []
    for seg in iter_segments(text):
        refs = PLACEHOLDER_RE.findall(seg)
        if not refs:
            continue
        schools = schools_mentioned(seg)
        codes = codes_mentioned(seg)
        for fk in refs:
            fact = fact_map.get(fk)
            if not fact:
                continue
            if schools and fact.get("school") and fact["school"] not in schools:
                bad.append(fk)
            elif codes and not _code_matches(fact.get("code"), codes):
                bad.append(fk)
    return list(dict.fromkeys(bad))


def render_placeholders(text: str, fact_map: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Replace [[Fk]] with value_text, append the source, flag missing or misplaced aliases."""
    missing: list[str] = []
    misattributed = check_attribution(text, fact_map)

    def repl(match: re.Match[str]) -> str:
        fk = match.group(1)
        fact = fact_map.get(fk)
        if fact is None:
            missing.append(fk)
            return "依据不足"
        return str(fact.get("value_text") or "")

    out: list[str] = []
    for seg in iter_segments(text):
        refs = PLACEHOLDER_RE.findall(seg)
        rendered = PLACEHOLDER_RE.sub(repl, seg)
        if refs:
            labels: list[str] = []
            seen: set[str] = set()
            for fk in refs:
                fact = fact_map.get(fk)
                if not fact:
                    continue
                label = source_label(fact.get("source"))
                if label and label not in seen:
                    seen.add(label)
                    labels.append(label)
            if labels and not any(label in rendered for label in labels):
                cite = "；".join(labels)
                ends_nl = rendered.endswith("\n")
                body = rendered[:-1] if ends_nl else rendered
                if body.rstrip().endswith("|"):
                    body = body.rstrip()[:-1].rstrip() + f" {cite} |"
                else:
                    body = body.rstrip() + f"（{cite}）"
                rendered = body + ("\n" if ends_nl else "")
        out.append(rendered)
    return "".join(out), {"missing": list(dict.fromkeys(missing)), "misattributed": misattributed}


def fact_table(fact: dict[str, Any]) -> str:
    fid = fact.get("fact_id") or ""
    return fid.split(":")[0] if fid else ""


def relevant_facts(intent: dict[str, Any], fact_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fact in fact_map.values():
        fid = fact.get("fact_id") or ""
        if fid in seen:
            continue
        if fid:
            seen.add(fid)
        facts.append(fact)
    kinds = set(intent.get("kinds") or [])
    if kinds <= {"score_line"} and kinds:
        return [
            f for f in facts
            if fact_table(f) == "score_lines" and "special_" not in (f.get("fact_id") or "")
        ]
    if kinds <= {"plan"} and kinds:
        return [f for f in facts if fact_table(f) == "plans"]
    if kinds <= {"subjects"} and kinds:
        return [f for f in facts if fact_table(f) == "exam_subjects"]
    return facts


def lookup_template(intent: dict[str, Any], fact_map: dict[str, dict[str, Any]]) -> str | None:
    """Single-program lookup with at most 3 relevant facts: answer without the LLM."""
    kinds = set(intent.get("kinds") or [])
    if kinds & {"compare", "filter", "narrative"} or intent.get("export") or intent.get("privacy"):
        return None
    if len(intent.get("schools") or []) > 1 or len(intent.get("codes") or []) > 1:
        return None
    facts = relevant_facts(intent, fact_map)
    if not facts or len(facts) > 3:
        return None
    programs = {f.get("program_id") for f in facts if f.get("program_id")}
    if len(programs) > 1:
        return None
    head = facts[0]
    title = " ".join(p for p in (head.get("school_name"), head.get("code")) if p)
    lines = [title] if title else []
    for fact in facts:
        year = fact.get("year")
        label = fact.get("label") or ""
        value = fact.get("value_text") or ""
        piece = " ".join(str(p) for p in (f"{year}年" if year else "", label, value) if p)
        src = source_label(fact.get("source"))
        if src:
            piece = f"{piece}。来源：{src}"
        if piece:
            lines.append(piece)
    return "\n".join(lines) if lines else None
