"""Structured queries over data/kaoyan.db, shared by the agent tools and the API.

Every fact carries its 口径 (``definition``) and a ``source`` (doc_id, title, url, page,
year, publish date). Seed and rule rows stating the same value from the same document
are merged into one fact; different values or documents are all kept side by side.
Derived numbers (统招 = 目录 − 推免) are returned with their formula and inputs so the
answer can quote them.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from kaoyan.db import KaoyanStore
from kaoyan.normalize import build_school_aliases, resolve_school

KIND_LABELS = {
    "catalog_total": "招生目录拟招生人数（含推免）",
    "tm": "推免数",
    "rules_total": "学院复试细则/方案总计划",
    "public_exam": "公开招考/统招计划",
    "available_exam": "学校统考可用计划",
    "college_exam_plan": "学院通知统考计划",
    "special_veteran": "退役大学生士兵专项计划",
    "special_minority": "少数民族高层次骨干人才计划",
}
KIND_ORDER = list(KIND_LABELS)
SCOPE_LABELS = {
    "college": "学院复试线",
    "school_baseline": "学校基本线",
    "special_veteran": "退役大学生士兵专项复试线",
    "special_minority": "少数民族骨干计划复试线",
}
SCOPE_ORDER = ["college", "special_veteran", "special_minority", "school_baseline"]
STAT_LABELS = {
    "retest_count": "复试名单人数",
    "admit_count": "拟录取人数",
    "score_min": "名单初试成绩最低分",
    "score_max": "名单初试成绩最高分",
}
EXPLICIT_PUBLIC_KINDS = ("public_exam", "college_exam_plan", "available_exam")
METHOD_RANK = {"manual_seed": 0, "seed_note_regex": 1, "rule": 2, "ocr": 3, "llm": 4}
NOTES_SOURCE = "majors.csv 人工核对备注"
ADVISORS_NOTE = (
    "招生目录该方向所列导师，原样照录；目录列出不代表每位导师当年都有该专业（含专硕）名额，"
    "库里没有导师的评价、主页或联系方式。只有暨大目录带导师列。"
)
PUBLIC_PLAN_RULE = (
    "统招取值：优先用明确的公开招考 / 学院统考计划 / 学校统考可用计划（逐条标口径）；"
    "否则用 目录拟招生人数 − 推免数（两者都是精确值）；推免为“≤N”上限时只能得出统招下限“≥X”，"
    "不能当作满足阈值；取不到的列入 unknown 并说明原因。"
)
_EVIDENCE_CHARS = 160


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _has_subject(group: dict[str, Any], keyword: str) -> bool:
    kw = _compact(keyword)
    return any(kw in (s["code"] or "") or kw in (s["name"] or "") for s in group["subjects"])


def is_professional(code: str) -> bool:
    """专业学位代码第 3 位是 5（0854 电子信息、1251 工商管理 …）."""
    return len(code) >= 3 and code[2] == "5"


def baseline_discipline(code: str) -> str:
    """The school-baseline discipline a program falls under: 0854 for 085404, 08 for 081200."""
    return code[:4] if is_professional(code) else code[:2]


def singles_text(line: dict[str, Any]) -> str:
    vals = [line.get(k) for k in ("politics", "foreign_lang", "subject1", "subject2")]
    return "/".join(str(v) for v in vals) if any(v is not None for v in vals) else ""


_METRIC_TABLE = {
    "score_line": "score_lines",
    "score_lines": "score_lines",
    "plan": "plans",
    "plans": "plans",
    "subjects": "exam_subjects",
    "exam_subjects": "exam_subjects",
    "stats": "admission_stats",
    "admission_stats": "admission_stats",
}


def make_fact_id(
    *,
    table: str,
    program_id: str | None,
    kind: str,
    year: int | None,
    pool_scope: str | None = None,
    value: Any,
    upper_bound: bool = False,
    source_doc_ids: list[str] | tuple[str, ...] = (),
) -> str:
    """Stable id for a merged fact. Same value + sources → same id."""
    sources = ",".join(sorted({s for s in source_doc_ids if s}))
    payload = f"{value}|{int(bool(upper_bound))}|{sources}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:6]
    return f"{table}:{program_id or '-'}:{kind}:{year if year is not None else '-'}:{pool_scope or '-'}:{digest}"


class KaoyanQuery:
    def __init__(self, store: KaoyanStore) -> None:
        self.store = store
        self._docs: dict[str, dict[str, Any]] | None = None
        self._programs: list[dict[str, Any]] | None = None
        self._schools: dict[str, dict[str, Any]] | None = None

    # -- lookups ----------------------------------------------------------

    @property
    def schools(self) -> dict[str, dict[str, Any]]:
        if self._schools is None:
            self._schools = {r["id"]: r for r in self.store.query("SELECT * FROM schools")}
        return self._schools

    @property
    def docs(self) -> dict[str, dict[str, Any]]:
        if self._docs is None:
            self._docs = {r["id"]: r for r in self.store.query("SELECT * FROM documents")}
        return self._docs

    @property
    def programs(self) -> list[dict[str, Any]]:
        if self._programs is None:
            self._programs = self.store.query(
                "SELECT p.*, c.code AS college_code, c.name AS college_name, c.slug AS college_slug, "
                "s.name AS school_name, s.short_name AS school_short "
                "FROM programs p JOIN colleges c ON c.id = p.college_id JOIN schools s ON s.id = p.school_id "
                "ORDER BY p.school_id, c.code, p.code, p.study_mode"
            )
        return self._programs

    def school_id(self, text: str | None) -> str | None:
        raw = (text or "").strip()
        if not raw:
            return None
        if raw in self.schools:
            return raw
        aliases = build_school_aliases(
            [{"id": s["id"], "name": s["name"], "short_name": s["short_name"]} for s in self.schools.values()]
        )
        return resolve_school(raw, aliases)

    def source(self, doc_id: str | None, page: int | None = None) -> dict[str, Any] | None:
        doc = self.docs.get(doc_id or "")
        if not doc:
            return None
        return {
            "doc_id": doc["id"],
            "title": doc["title"],
            "url": doc["page_url"] or doc["attachment_url"],
            "attachment_url": doc["attachment_url"] if doc["page_url"] else None,
            "page": page,
            "year": doc["intake_year"],
            "publish_date": doc["publish_date"],
            "doc_type": doc["doc_type"],
        }

    @staticmethod
    def brief(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "program_id": row["id"],
            "school": row["school_id"],
            "school_name": row["school_name"],
            "college": row["college_name"],
            "college_code": row["college_code"],
            "code": row["code"],
            "name": row["name"],
            "degree_type": row["degree_type"],
            "study_mode": row["study_mode"],
            "is_boundary": bool(row["is_boundary"]),
            "pool_code": row["pool_code"],
        }

    @staticmethod
    def _college_matches(row: dict[str, Any], text: str) -> bool:
        want = _compact(text)
        if want in {row["college_id"], row["college_code"], row["college_slug"]}:
            return True
        name = row["college_name"] or ""
        if want == name:
            return True
        core_q, core_n = want.removesuffix("学院"), name.removesuffix("学院")
        return bool(core_q and core_q in name) or bool(core_n and core_n in want)

    def find_programs(
        self,
        *,
        school: str | None = None,
        college: str | None = None,
        code: str | None = None,
        name_kw: str | None = None,
        degree_type: str | None = None,
        study_mode: str | None = None,
        program_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.programs
        if program_ids:
            wanted = [p.strip() for p in program_ids if p.strip()]
            return [r for r in rows if r["id"] in wanted]
        if school:
            sid = self.school_id(school)
            rows = [r for r in rows if r["school_id"] == sid]
        if college:
            rows = [r for r in rows if self._college_matches(r, college)]
        if code:
            c = _compact(code).upper()
            rows = [r for r in rows if (r["code"].startswith(c) if len(c) == 4 else r["code"] == c)]
        if degree_type:
            rows = [r for r in rows if (r["degree_type"] or "") == degree_type.strip()]
        if study_mode:
            rows = [r for r in rows if r["study_mode"] == study_mode.strip()]
        if name_kw:
            kw = _compact(name_kw)
            rows = [r for r in rows if kw in r["name"] or any(kw in d["name"] for d in self._directions(r["id"]))]
        return rows

    # -- facts ------------------------------------------------------------

    @staticmethod
    def _merge(rows: list[dict[str, Any]], key: tuple[str, ...]) -> list[dict[str, Any]]:
        """Collapse rows with the same key (seed + rule of one value@doc); seed row wins."""
        out: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in sorted(rows, key=lambda x: (METHOD_RANK.get(x["extraction_method"], 9), x["id"])):
            k = tuple(r.get(f) for f in key)
            if k in out:
                kept = out[k]
                kept["verified"] = bool(kept["verified"]) or bool(r["verified"])
                kept["_methods"].add(r["extraction_method"])
                for f in ("evidence_text", "advisors"):
                    if not kept.get(f) and r.get(f):
                        kept[f] = r[f]
                continue
            out[k] = {**r, "verified": bool(r["verified"]), "_methods": {r["extraction_method"]}}
        return list(out.values())

    def _meta(
        self,
        r: dict[str, Any],
        *,
        table: str,
        program_id: str | None,
        kind: str,
        value: Any,
        upper_bound: bool = False,
        pool_scope: str | None = None,
    ) -> dict[str, Any]:
        src_ids = list(r.get("_source_ids") or ([r.get("source_doc_id")] if r.get("source_doc_id") else []))
        return {
            "verified": r["verified"],
            "methods": sorted(r["_methods"], key=lambda m: METHOD_RANK.get(m, 9)),
            "evidence": (r.get("evidence_text") or "")[:_EVIDENCE_CHARS] or None,
            "source": self.source(r.get("source_doc_id"), r.get("page")),
            "fact_id": make_fact_id(
                table=table,
                program_id=program_id,
                kind=kind,
                year=r.get("year"),
                pool_scope=pool_scope if pool_scope is not None else r.get("pool_scope"),
                value=value,
                upper_bound=upper_bound,
                source_doc_ids=src_ids,
            ),
        }

    def latest_year(self, metric: str | None = None, program_id: str | None = None) -> int | None:
        """Newest intake year in the store for a metric, or across plans and score lines."""
        table = _METRIC_TABLE.get(metric or "", "")
        if table:
            sql = f"SELECT MAX(year) AS y FROM {table}"
            params: tuple[Any, ...] = ()
            if program_id:
                sql += " WHERE program_id = ?"
                params = (program_id,)
            row = self.store.query_one(sql, params)
        else:
            row = self.store.query_one(
                "SELECT MAX(y) AS y FROM (SELECT year AS y FROM score_lines UNION ALL SELECT year FROM plans)"
            )
        y = row["y"] if row else None
        return int(y) if y is not None else None

    def resolve_year(
        self, year: int | None, metric: str | None = None, program_id: str | None = None
    ) -> int | None:
        """User year, then settings.kaoyan_current_cycle, then latest year in the store."""
        if year is not None:
            return year
        from kaoyan.config import get_settings

        cycle = get_settings().kaoyan_current_cycle
        if cycle:
            return int(cycle)
        return self.latest_year(metric, program_id)

    def data_gaps(
        self,
        *,
        schools: list[str] | None = None,
        codes: list[str] | None = None,
        prefixes: list[str] | None = None,
        year: int | None = None,
        metrics: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Known holes (blocked catalog, unpublished year, pooled plan, 仅招推免, boundary)."""
        sql = (
            "SELECT g.*, p.code, p.name AS program_name, s.name AS school_name, s.short_name "
            "FROM data_gaps g "
            "JOIN schools s ON s.id = g.school_id "
            "LEFT JOIN programs p ON p.id = g.program_id "
            "WHERE 1=1"
        )
        params: list[Any] = []
        if schools:
            ids = [self.school_id(x) or x for x in schools]
            sql += f" AND g.school_id IN ({','.join('?' for _ in ids)})"
            params.extend(ids)
        needles = [c for c in (codes or []) + (prefixes or []) if c]
        if needles:
            clauses = ["g.program_id IS NULL"]
            for n in needles:
                clauses.append("p.code LIKE ?")
                params.append(n + "%" if len(n) < 6 else n)
            sql += " AND (" + " OR ".join(clauses) + ")"
        if year is not None:
            sql += " AND (g.year IS NULL OR g.year = ?)"
            params.append(year)
        if metrics:
            sql += f" AND g.metric IN ({','.join('?' for _ in metrics)})"
            params.extend(metrics)
        sql += " ORDER BY g.school_id, g.program_id, g.year, g.metric, g.status"
        return self.store.query(sql, tuple(params))

    @staticmethod
    def format_gap(gap: dict[str, Any]) -> str:
        metric = {"catalog": "目录人数", "subjects": "初试科目", "score_line": "复试线", "plan": "招生计划"}.get(
            gap["metric"], gap["metric"]
        )
        year = f"{gap['year']} 年 " if gap.get("year") else ""
        who = " ".join(p for p in (gap.get("school_name"), gap.get("code")) if p)
        prefix = f"{who} " if who else ""
        verify = f"；去 {gap['verify_at']} 核实" if gap.get("verify_at") else ""
        if gap["status"] in {"blocked", "not_published"}:
            return f"官方资料中未取得 — {prefix}{year}{metric}：{gap['reason']}{verify}"
        return f"{prefix}{year}{metric}：{gap['reason']}{verify}"

    def plans(self, program_id: str, year: int | None = None) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM plans WHERE program_id = ?", (program_id,))
        rows = [r for r in rows if year is None or r["year"] == year]
        merged = self._merge(rows, ("kind", "year", "value", "is_upper_bound", "pool_scope", "source_doc_id"))
        facts = []
        for r in merged:
            fact = {
                "kind": r["kind"],
                "label": KIND_LABELS.get(r["kind"], r["kind"]),
                "year": r["year"],
                "value": r["value"],
                "value_text": ("≤" if r["is_upper_bound"] else "") + str(r["value"]),
                "upper_bound": bool(r["is_upper_bound"]),
                "pool_scope": r["pool_scope"],
                "definition": r["definition"],
                **self._meta(
                    r,
                    table="plans",
                    program_id=program_id,
                    kind=r["kind"],
                    value=r["value"],
                    upper_bound=bool(r["is_upper_bound"]),
                    pool_scope=r["pool_scope"],
                ),
            }
            if r["pool_scope"]:
                fact["note"] = f"一级学科 {r['pool_scope']} 统筹合计，不是本专业单列人数"
            facts.append(fact)
        facts.sort(key=lambda f: (-f["year"], KIND_ORDER.index(f["kind"]) if f["kind"] in KIND_ORDER else 99))
        return facts

    def score_lines(self, row: dict[str, Any], year: int | None = None) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM score_lines WHERE program_id = ?", (row["id"],))
        if not self._no_exam(row["id"]):
            rows += self.store.query(
                "SELECT * FROM score_lines WHERE program_id IS NULL AND school_id = ? AND scope = 'school_baseline' "
                "AND discipline_code = ?",
                (row["school_id"], baseline_discipline(row["code"])),
            )
        rows = [r for r in rows if year is None or r["year"] == year]
        merged = self._merge(rows, ("scope", "year", "total", "discipline_code", "source_doc_id"))
        lines = []
        for r in merged:
            line = {
                "scope": r["scope"],
                "label": SCOPE_LABELS.get(r["scope"], r["scope"]),
                "year": r["year"],
                "total": r["total"],
                "politics": r["politics"],
                "foreign_lang": r["foreign_lang"],
                "subject1": r["subject1"],
                "subject2": r["subject2"],
                "discipline_code": r["discipline_code"],
                "definition": r["definition"],
                "value_text": str(r["total"]),
                **self._meta(
                    r,
                    table="score_lines",
                    program_id=row["id"],
                    kind=r["scope"],
                    value=r["total"],
                ),
            }
            line["singles"] = singles_text(line)
            if r["scope"] == "school_baseline" and r["program_id"] is None:
                line["note"] = "学校基本线（按学科门类/学位类别适用），不是学院线"
            lines.append(line)
        lines.sort(key=lambda x: (-x["year"], SCOPE_ORDER.index(x["scope"])))
        return lines

    def _subject_rows(self, program_id: str) -> list[dict[str, Any]]:
        return self.store.query("SELECT * FROM exam_subjects WHERE program_id = ?", (program_id,))

    def _no_exam(self, program_id: str) -> bool:
        return any(r["status"] == "no_exam" for r in self._subject_rows(program_id))

    def exam_subjects(self, program_id: str) -> list[dict[str, Any]]:
        rows = self._merge(self._subject_rows(program_id), ("year", "status", "slot", "code", "source_doc_id"))
        groups: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in sorted(rows, key=lambda x: (x["slot"] or 0)):
            k = (r["year"], r["status"], r["source_doc_id"])
            g = groups.setdefault(
                k,
                {
                    "year": r["year"],
                    "status": r["status"],
                    "subjects": [],
                    "unknown_reason": r["unknown_reason"],
                    **self._meta(
                        r,
                        table="exam_subjects",
                        program_id=program_id,
                        kind=r["status"],
                        value=r.get("code") or r.get("unknown_reason") or r["status"],
                    ),
                },
            )
            if r["status"] == "known":
                g["subjects"].append({"slot": r["slot"], "code": r["code"], "name": r["name"]})
            g["verified"] = g["verified"] and r["verified"]
        out = list(groups.values())
        for g in out:
            g["codes"] = "/".join(s["code"] or "?" for s in g["subjects"])
            g["is_408"] = any(s["code"] == "408" for s in g["subjects"]) if g["status"] == "known" else None
            g["value_text"] = g["codes"] or g.get("unknown_reason") or g["status"]
            src = (g.get("source") or {}).get("doc_id")
            g["fact_id"] = make_fact_id(
                table="exam_subjects",
                program_id=program_id,
                kind=g["status"],
                year=g["year"],
                value=g["value_text"],
                source_doc_ids=[src] if src else [],
            )
        # identical subject lists from several documents: keep the first (seed) one
        seen: set[tuple[Any, ...]] = set()
        uniq = []
        order = {"known": 0, "no_exam": 1, "unknown": 2}
        for g in sorted(out, key=lambda g: (-(g["year"] or 0), order[g["status"]])):
            sig = (g["year"], g["status"], g["codes"])
            if sig not in seen:
                seen.add(sig)
                uniq.append(g)
        return uniq

    @staticmethod
    def latest_subjects(groups: list[dict[str, Any]]) -> dict[str, Any] | None:
        return groups[0] if groups else None

    def _directions(self, program_id: str) -> list[dict[str, Any]]:
        return self.store.query("SELECT * FROM directions WHERE program_id = ? ORDER BY year DESC, code", (program_id,))

    def directions(self, program_id: str) -> list[dict[str, Any]]:
        merged = self._merge(self._directions(program_id), ("year", "code", "name", "source_doc_id"))
        seen: set[tuple[Any, ...]] = set()
        out = []
        for r in sorted(merged, key=lambda x: (-(x["year"] or 0), x["code"] or "")):
            if (r["year"], r["code"], r["name"]) in seen:
                continue
            seen.add((r["year"], r["code"], r["name"]))
            item = {"year": r["year"], "code": r["code"], "name": r["name"], "source": self.source(r["source_doc_id"])}
            if r.get("advisors"):
                item["advisors"] = r["advisors"]
                item["advisors_note"] = ADVISORS_NOTE
            out.append(item)
        return out

    def admission_stats(self, program_id: str, year: int | None = None) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM admission_stats WHERE program_id = ?", (program_id,))
        rows = [r for r in rows if year is None or r["year"] == year]
        merged = self._merge(rows, ("kind", "year", "value", "pool_scope", "source_doc_id"))
        return [
            {
                "kind": r["kind"],
                "label": STAT_LABELS.get(r["kind"], r["kind"]),
                "year": r["year"],
                "value": r["value"],
                "pool_scope": r["pool_scope"],
                "definition": r["definition"],
                "value_text": str(r["value"]),
                **self._meta(
                    r,
                    table="admission_stats",
                    program_id=program_id,
                    kind=r["kind"],
                    value=r["value"],
                    pool_scope=r.get("pool_scope"),
                ),
            }
            for r in sorted(merged, key=lambda x: (-x["year"], x["kind"]))
        ]

    @staticmethod
    def public_plan(plans: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """统招 candidates by the rule in ``PUBLIC_PLAN_RULE`` → (estimates, reasons when none)."""
        estimates: list[dict[str, Any]] = []
        reasons: list[str] = []
        for f in plans:
            if f["kind"] in EXPLICIT_PUBLIC_KINDS and f["value"] is not None:
                if f["pool_scope"]:
                    reasons.append(
                        f"{f['year']} {f['label']} {f['value_text']} 为一级学科 {f['pool_scope']} 合计，不是本专业单列人数"
                    )
                    continue
                estimates.append(
                    {
                        "value": f["value"],
                        "text": f["value_text"],
                        "lower_bound": False,
                        "basis": f["kind"],
                        "label": f["label"],
                        "year": f["year"],
                        "definition": f["definition"],
                        "formula": None,
                        "sources": [f["source"]],
                    }
                )
        tms = [f for f in plans if f["kind"] == "tm" and f["value"] is not None]
        for c in (f for f in plans if f["kind"] == "catalog_total" and f["value"] is not None):
            if c["pool_scope"]:
                reasons.append(f"{c['year']} 目录人数为一级学科 {c['pool_scope']} 合计，不能按本专业算统招")
                continue
            same_year = [t for t in tms if t["year"] == c["year"]]
            same_doc = [t for t in same_year if (t["source"] or {}).get("doc_id") == (c["source"] or {}).get("doc_id")]
            pairs = same_doc or same_year
            if not pairs:
                reasons.append(f"{c['year']} 有目录人数 {c['value']} 但没有同年推免数，不能相减")
            for t in pairs:
                if t["pool_scope"]:
                    reasons.append(f"{t['year']} 推免数为一级学科 {t['pool_scope']} 合计，不能按本专业相减")
                    continue
                value = c["value"] - t["value"]
                if t["upper_bound"]:
                    text, formula = f"≥{value}", f"{c['value']} − ≤{t['value']} = ≥{value}"
                else:
                    text, formula = str(value), f"{c['value']} − {t['value']} = {value}"
                estimates.append(
                    {
                        "value": value,
                        "text": text,
                        "lower_bound": t["upper_bound"],
                        "basis": "catalog_total-tm",
                        "label": "目录拟招生人数 − 推免数（派生）",
                        "year": c["year"],
                        "definition": f"{c['definition']} − {t['definition']}",
                        "formula": formula,
                        "sources": [c["source"], t["source"]],
                    }
                )
        seen: set[tuple[Any, ...]] = set()
        uniq = []
        for e in estimates:
            k = (e["value"], e["lower_bound"], e["year"], e["basis"])
            if k not in seen:
                seen.add(k)
                uniq.append(e)
        uniq.sort(key=lambda e: (-e["year"], e["basis"] == "catalog_total-tm"))
        if not uniq and not reasons:
            reasons.append("没有公开招考/统考计划，也没有目录人数与推免数")
        return uniq, reasons

    @staticmethod
    def public_plan_basis(estimates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Values a threshold is judged on: the latest year only (never mix years), explicit
        plans before the derived 目录 − 推免."""
        if not estimates:
            return []
        latest = max(e["year"] for e in estimates)
        same_year = [e for e in estimates if e["year"] == latest]
        explicit = [e for e in same_year if e["basis"] != "catalog_total-tm"]
        return explicit or same_year

    def program_facts(self, row: dict[str, Any], year: int | None = None) -> dict[str, Any]:
        plans = self.plans(row["id"], year)
        estimates, reasons = self.public_plan(plans)
        for e in estimates:
            src_ids = [s["doc_id"] for s in (e.get("sources") or []) if s and s.get("doc_id")]
            e["fact_id"] = make_fact_id(
                table="plans",
                program_id=row["id"],
                kind=e["basis"],
                year=e["year"],
                value=e["value"],
                upper_bound=bool(e.get("lower_bound")),
                source_doc_ids=src_ids,
            )
            e["value_text"] = e.get("text")
        return {
            **self.brief(row),
            "plans": plans,
            "public_plan": estimates,
            "public_plan_unknown": reasons if not estimates else [],
            "score_lines": self.score_lines(row, year),
            "exam_subjects": self.exam_subjects(row["id"]),
            "directions": self.directions(row["id"]),
            "admission_stats": self.admission_stats(row["id"], year),
            "notes": row["notes"],
            "notes_source": NOTES_SOURCE if row["notes"] else None,
        }

    def program(self, program_id: str) -> dict[str, Any] | None:
        rows = self.find_programs(program_ids=[program_id])
        return self.program_facts(rows[0]) if rows else None

    @staticmethod
    def compact_facts(facts: dict[str, Any]) -> dict[str, Any]:
        """Filter-result view: identity, every plan number with its 口径, 统招 candidates,
        latest subjects, notes. Drops definitions and evidence text, never the label of a number."""
        def slim(src: dict[str, Any] | None) -> dict[str, Any] | None:
            return src and {k: src[k] for k in ("doc_id", "title", "url", "page", "year")}

        subj = facts["exam_subjects"][0] if facts["exam_subjects"] else None
        keep = ("program_id", "school", "school_name", "college", "college_code", "code", "name",
                "degree_type", "study_mode", "is_boundary", "public_plan_unknown", "notes")
        out = {k: facts[k] for k in keep}
        plan_keys = ("kind", "label", "year", "value", "value_text", "upper_bound", "pool_scope", "note", "fact_id")
        out["plans"] = [
            {**{k: p[k] for k in plan_keys if p.get(k) is not None},
             "source": p["source"] and {k: p["source"][k] for k in ("doc_id", "url", "page")}}
            for p in facts["plans"]
        ]
        out["public_plan"] = [{**e, "sources": [slim(s) for s in e["sources"]]} for e in facts["public_plan"]]
        out["subjects"] = subj and {
            **{k: subj[k] for k in ("year", "status", "codes", "is_408", "unknown_reason")},
            **{k: subj[k] for k in ("fact_id", "value_text") if subj.get(k)},
            "source": slim(subj["source"]),
        }
        return out

    # -- tool / API level queries -------------------------------------------

    def search_programs(
        self,
        *,
        school: str | None = None,
        college: str | None = None,
        code: str | None = None,
        name_kw: str | None = None,
        degree_type: str | None = None,
        study_mode: str | None = None,
        exam_subject: str | None = None,
        exclude_subject: str | None = None,
        is_408: bool | None = None,
        min_public_plan: int | None = None,
        year: int | None = None,
        detail: bool | None = None,
    ) -> dict[str, Any]:
        rows = self.find_programs(
            school=school, college=college, code=code, name_kw=name_kw, degree_type=degree_type, study_mode=study_mode
        )
        matches: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for row in rows:
            facts = self.program_facts(row, year)
            ok, why, evidence = True, [], {}
            if is_408 is not None or exam_subject or exclude_subject:
                grp = self.latest_subjects(facts["exam_subjects"])
                if grp is None or grp["status"] == "unknown":
                    why.append("初试科目未知：" + ((grp or {}).get("unknown_reason") or "库中没有初试科目"))
                elif grp["status"] == "no_exam":
                    ok = False
                    why.append(f"{grp['year']} 不设初试科目")
                else:
                    if is_408 is not None and grp["is_408"] != is_408:
                        ok = False
                        why.append(f"{grp['year']} 初试科目 {grp['codes']}，{'不考' if is_408 else '考'} 408")
                    if exam_subject and not _has_subject(grp, exam_subject):
                        ok = False
                        why.append(f"{grp['year']} 初试科目 {grp['codes']}，不含“{exam_subject}”")
                    if exclude_subject and _has_subject(grp, exclude_subject):
                        ok = False
                        why.append(f"{grp['year']} 初试科目 {grp['codes']}，含“{exclude_subject}”")
                if not ok:
                    src = grp["source"] and {k: grp["source"][k] for k in ("doc_id", "title", "url", "page", "year")}
                    evidence["exam_subjects"] = [
                        {**{k: grp[k] for k in ("year", "status", "subjects", "codes")}, "source": src}
                    ]
            if min_public_plan is not None and ok:
                basis = self.public_plan_basis(facts["public_plan"])
                if any(e["value"] >= min_public_plan for e in basis):
                    pass
                elif any(not e["lower_bound"] for e in basis):
                    ok = False
                    texts = "、".join(f"{e['year']} {e['label']} {e['text']}" for e in basis)
                    why.append(f"统招 {texts}，不到 {min_public_plan}")
                    evidence["public_plan"] = basis
                elif basis:
                    lbs = "、".join(f"{e['year']} {e['text']}（{e['formula']}）" for e in basis)
                    why.append(f"推免数为“≤N”上限，统招只能得出下限 {lbs}，不能判断是否 ≥{min_public_plan}")
                else:
                    why.append("统招取不到：" + "；".join(facts["public_plan_unknown"]))
            if not ok:
                ident = ("program_id", "school", "college", "code", "name", "degree_type", "study_mode")
                excluded.append({**{k: facts[k] for k in ident}, "reasons": why, **evidence})
                continue
            if why:
                unknown.append({**self.compact_facts(facts), "reasons": why})
            else:
                matches.append(facts)
        full = detail if detail is not None else len(matches) <= 6
        return {
            "filters": {
                k: v
                for k, v in {
                    "school": school, "college": college, "code": code, "name_kw": name_kw,
                    "degree_type": degree_type, "study_mode": study_mode, "exam_subject": exam_subject,
                    "exclude_subject": exclude_subject, "is_408": is_408, "min_public_plan": min_public_plan, "year": year,
                }.items()
                if v not in (None, "")
            },
            "count": len(matches),
            "programs": matches if full else [self.compact_facts(f) for f in matches],
            "unknown": unknown,
            "excluded": len(excluded),
            "excluded_programs": excluded,
            "public_plan_rule": PUBLIC_PLAN_RULE if min_public_plan is not None else None,
            "scope_note": "数据范围：中大 / 华工 / 暨大 / 华师 的泛计算机类 37 个专业（种子包），范围外的专业库中没有记录。",
        }

    def get_score_lines(
        self,
        *,
        school: str | None = None,
        code: str | None = None,
        college: str | None = None,
        year: int | None = None,
        include_special: bool = True,
    ) -> dict[str, Any]:
        rows = self.find_programs(school=school, college=college, code=code)
        programs = []
        latest = self.latest_year("score_lines")
        latest_note = f"现有数据最新为 {latest} 年" if latest is not None else "库中暂无复试线"
        for row in rows:
            lines = [
                ln for ln in self.score_lines(row, year) if include_special or not ln["scope"].startswith("special_")
            ]
            entry = {**self.brief(row), "lines": lines, "notes": row["notes"]}
            if not lines:
                year_bit = f"{year} 年的" if year is not None else ""
                entry["unknown"] = (
                    "目录注明仅招收推免生，没有统考复试线"
                    if self._no_exam(row["id"])
                    else f"库中没有该专业 {year_bit}复试线（{latest_note}）".replace("  ", " ")
                )
            programs.append(entry)
        out: dict[str, Any] = {"year": year, "count": len(programs), "programs": programs}
        if not rows:
            out["unknown"] = "数据范围内没有匹配的专业"
        return out

    def get_exam_subjects(
        self, *, school: str | None = None, code: str | None = None, college: str | None = None
    ) -> dict[str, Any]:
        rows = self.find_programs(school=school, college=college, code=code)
        programs = []
        for row in rows:
            groups = self.exam_subjects(row["id"])
            entry = {
                **self.brief(row),
                "exam_subjects": groups,
                "directions": self.directions(row["id"]),
                "notes": row["notes"],
            }
            if not groups:
                entry["unknown"] = "库中没有初试科目"
            programs.append(entry)
        out: dict[str, Any] = {"count": len(programs), "programs": programs}
        if not rows:
            out["unknown"] = "数据范围内没有匹配的专业"
        return out

    def compare(
        self,
        *,
        program_ids: list[str] | None = None,
        school: str | None = None,
        college: str | None = None,
        code: str | None = None,
        fields: list[str] | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        wanted = set(fields or ["score_lines", "plans", "public_plan", "subjects"])
        rows = self.find_programs(program_ids=program_ids, school=school, college=college, code=code)
        table, programs = [], []
        for row in rows:
            facts = self.program_facts(row, year)
            programs.append(facts)
            table.append(self.table_row(facts, wanted))
        return {"fields": sorted(wanted), "count": len(table), "rows": table, "programs": programs}

    @staticmethod
    def table_row(facts: dict[str, Any], fields: set[str]) -> dict[str, Any]:
        """One flat row per program for comparison tables and Excel export; gaps say 未取得."""
        row: dict[str, Any] = {
            "学校": facts["school_name"],
            "学院": f"{facts['college_code'] or ''}{facts['college']}",
            "专业代码": facts["code"],
            "专业名称": facts["name"],
            "学习方式": facts["study_mode"],
        }
        remarks: list[str] = []
        docs: list[dict[str, Any]] = []

        def cite(src: dict[str, Any] | None) -> None:
            if src and src["doc_id"] not in {d["doc_id"] for d in docs}:
                docs.append(src)

        if "score_lines" in fields:
            lines = facts["score_lines"]
            main = next((ln for ln in lines if ln["scope"] == "college"), None) or next(
                (ln for ln in lines if ln["scope"] == "school_baseline"), None
            )
            if main:
                row["年份"] = main["year"]
                row["复试线"] = f"{main['total']}（{main['singles']}）" if main["singles"] else main["total"]
                row["线的口径"] = main["label"] + (f"：{main['definition']}" if main["definition"] else "")
                cite(main["source"])
            else:
                row["年份"] = ""
                row["复试线"] = "未取得"
                row["线的口径"] = ""
                no_exam = any(g["status"] == "no_exam" for g in facts["exam_subjects"])
                remarks.append("仅招收推免生，没有统考复试线" if no_exam else "库中没有复试线")
            specials = [ln for ln in lines if ln["scope"].startswith("special_")]
            row["专项线"] = "；".join(f"{ln['label']} {ln['total']}" for ln in specials) or "—"
            for ln in specials:
                cite(ln["source"])
        if "plans" in fields:
            plans = facts["plans"]
            if plans:
                row["计划"] = "；".join(f"{p['year']} {p['label']} {p['value_text']}" for p in plans)
                row["计划口径"] = "；".join(sorted({p["definition"] or p["label"] for p in plans}))
                for p in plans:
                    cite(p["source"])
            else:
                row["计划"] = "未取得"
                row["计划口径"] = ""
                remarks.append("库中没有招生计划")
        if "public_plan" in fields:
            est = facts["public_plan"]
            row["统招"] = "；".join(
                f"{e['year']} {e['label']} {e['text']}" + (f"（{e['formula']}）" if e["formula"] else "") for e in est
            ) or "未取得"
            if not est:
                remarks.extend(facts["public_plan_unknown"])
        if "subjects" in fields:
            grp = facts["exam_subjects"][0] if facts["exam_subjects"] else None
            if grp is None:
                row["初试科目"] = "未取得"
                remarks.append("库中没有初试科目")
            elif grp["status"] == "known":
                row["初试科目"] = f"{grp['year']}：" + " ".join(f"{s['code']}{s['name']}" for s in grp["subjects"])
                cite(grp["source"])
            elif grp["status"] == "no_exam":
                row["初试科目"] = "仅招收推免生（无统考科目）"
                cite(grp["source"])
            else:
                row["初试科目"] = "未取得"
                remarks.append(f"初试科目未取得：{grp['unknown_reason'] or '原因未记录'}")
        row["来源URL"] = " ; ".join(d["url"] for d in docs if d.get("url"))
        row["doc_id"] = " ; ".join(d["doc_id"] for d in docs)
        row["备注"] = "；".join(dict.fromkeys(remarks)) or ("边界项（非典型计算机类）" if facts["is_boundary"] else "")
        return row

    def document(self, doc_id: str) -> dict[str, Any] | None:
        doc = self.docs.get(doc_id)
        if not doc:
            return None
        counts = {
            t: self.store.count(t, "source_doc_id = ?", (doc_id,))
            for t in ("plans", "score_lines", "exam_subjects", "directions", "admission_stats")
        }
        return {**self._doc_meta(doc), "notes": doc["notes"], "fact_counts": counts}

    @staticmethod
    def _doc_meta(doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "doc_id": doc["id"],
            "school": doc["school_id"],
            "college": doc["college_text"],
            "title": doc["title"],
            "publish_date": doc["publish_date"],
            "intake_year": doc["intake_year"],
            "doc_type": doc["doc_type"],
            "format": doc["format"],
            "page_url": doc["page_url"],
            "attachment_url": doc["attachment_url"],
            "pages": doc["pages"],
            "contains_personal_data": bool(doc["contains_personal_data"]),
            "content_available": False,
            "status": doc["status"],
        }

    def list_sources(
        self, *, school: str | None = None, doc_type: str | None = None, year: int | None = None
    ) -> list[dict[str, Any]]:
        sid = self.school_id(school) if school else None
        out = []
        for doc in sorted(self.docs.values(), key=lambda d: d["id"]):
            if school and doc["school_id"] != sid:
                continue
            if doc_type and doc["doc_type"] != doc_type:
                continue
            if year and doc["intake_year"] != year:
                continue
            out.append(self._doc_meta(doc))
        return out
