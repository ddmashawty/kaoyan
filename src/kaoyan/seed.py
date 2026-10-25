"""Seed data/kaoyan.db from the hand-verified bundle (majors.csv + sources.json).

Idempotent: seed facts (manual_seed / seed_note_regex) are deleted and rewritten;
schools / colleges / documents / programs are upserted.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kaoyan.db import KaoyanStore
from kaoyan.models import (
    AdmissionStat,
    College,
    DataGap,
    Direction,
    Document,
    ExamSubject,
    Plan,
    Program,
    School,
    ScoreLine,
    SeedReport,
)
from kaoyan.normalize import (
    CollegeName,
    build_school_aliases,
    clean,
    parse_college,
    parse_count,
    parse_subject,
    parse_subject_lines,
    parse_year,
    resolve_school,
    split_source_urls,
)

SCORE_LINE_YEAR = 2026

# Default 口径 of the 推免 column per school (see data/kaoyan/README.md §4): (source pref, definition).
TM_COLUMN: dict[str, tuple[str, str]] = {
    "sysu": ("rules_plan", "学院2026复试细则“已招推免”"),
    "scnu": ("catalog", "招生专业目录“总(推免)”中的推免数"),
    "jnu": ("tm_policy", "2027推免生复试方案中的推免数（“≤N”为上限）"),
}
TM_COLUMN_DEFAULT: tuple[str, str] = ("catalog", "推免人数")


@dataclass(frozen=True)
class SourcePref:
    doc_types: tuple[str, ...]
    keywords: tuple[str, ...] = ()
    require_keyword: bool = False
    prefer_attachment: bool = False


PREFS: dict[str, SourcePref] = {
    "catalog": SourcePref(("catalog", "tm_catalog", "brochure"), ("目录",)),
    "subjects": SourcePref(("catalog", "subject_change", "tm_catalog", "brochure"), ("目录", "科目"), prefer_attachment=True),
    "college_line": SourcePref(("retest_rules", "score_line"), ("分数线",), prefer_attachment=True),
    "school_baseline": SourcePref(("score_line",), ("基本要求", "基本分数线", "分数线"), prefer_attachment=True),
    "rules_plan": SourcePref(("retest_rules",), ("细则", "招生人数"), prefer_attachment=True),
    "retest_plan": SourcePref(("retest_rules",), ("复试方案",)),
    "special": SourcePref(("retest_rules", "score_line"), ("分数线", "细则", "复试方案")),
    "college_exam_plan": SourcePref(("plan_quota", "notice", "retest_rules"), ("统考计划", "招生计划")),
    "available_exam": SourcePref(("plan_quota",), ("统考可用计划",), require_keyword=True, prefer_attachment=True),
    "tm_catalog": SourcePref(("tm_catalog",)),
    "tm_policy": SourcePref(("tm_policy",), ("推免",)),
    "admission": SourcePref(("admission_list",), ("拟录取",)),
    "retest_count": SourcePref(("retest_list", "retest_rules", "admission_list"), ("复试名单", "复试方案")),
}


@dataclass
class NoteFact:
    table: str  # plan | score | stat | baseline
    kind: str
    pref: str
    evidence: str
    definition: str
    year: int = SCORE_LINE_YEAR
    value: int | None = None
    is_upper_bound: bool = False
    pool_scope: str | None = None
    lines: tuple[int | None, int | None, int | None, int | None] = (None, None, None, None)
    discipline_code: str | None = None
    url: str | None = None


def _clause(note: str, start: int, end: int) -> str:
    seps = "。；;"
    left = max(note.rfind(s, 0, start) for s in seps) + 1
    rights = [i for i in (note.find(s, end) for s in seps) if i != -1]
    right = min(rights) if rights else len(note)
    return note[left:right].strip()


def _url_after(note: str, end: int) -> str | None:
    """URL in a bracket directly after the match, e.g. '拟录取8（https://…）' or '23人（含南特5）（https://…）'."""
    m = re.match(r"(?:[（(][^）)]*[）)])?\s*[（(](https?://[^\s）)]+)", note[end:])
    return m.group(1) if m else None


def _four(m: re.Match[str], first: int) -> tuple[int | None, ...]:
    vals = [m.group(i) for i in range(first, first + 4)]
    return tuple(int(v) if v else None for v in vals)


def extract_note_facts(note: str, code: str) -> list[NoteFact]:
    """Narrow regexes over the 备注 column. Anything not matched stays only in notes."""
    facts: list[NoteFact] = []
    if not note:
        return facts

    def add(m: re.Match[str], **kw: Any) -> None:
        facts.append(NoteFact(evidence=_clause(note, m.start(), m.end()), url=_url_after(note, m.end()), **kw))

    # 学院细则计划：总X/已招推免Y/公开Z（含各种写法）
    for m in re.finditer(r"细则[^总。；]{0,8}总(\d+)/(?:已招)?推免(\d+)/公开(?:招考)?(\d+)", note):
        add(m, table="plan", kind="rules_total", pref="rules_plan", value=int(m.group(1)), definition="学院复试细则“总计划”")
        add(m, table="plan", kind="tm", pref="rules_plan", value=int(m.group(2)), definition="学院复试细则“已招推免”")
        add(m, table="plan", kind="public_exam", pref="rules_plan", value=int(m.group(3)), definition="学院复试细则“公开招考”")

    # 华师复试方案
    for m in re.finditer(r"复试方案中计划为(\d+)（推免(\d+)", note):
        add(m, table="plan", kind="rules_total", pref="retest_plan", value=int(m.group(1)), definition="学院2026复试方案中的计划")
        add(m, table="plan", kind="tm", pref="retest_plan", value=int(m.group(2)), definition="学院2026复试方案中的推免数")
    for m in re.finditer(r"复试方案[^：:。；]{0,14}[：:]\s*拟招(\d+)[、/](?:已招)?推免(\d+)", note):
        add(m, table="plan", kind="rules_total", pref="retest_plan", value=int(m.group(1)), definition="学院2026复试方案“拟招”人数")
        add(m, table="plan", kind="tm", pref="retest_plan", value=int(m.group(2)), definition="学院2026复试方案“已招推免”")
    for m in re.finditer(r"复试方案[：:]\s*已招推免(\d+)", note):
        add(m, table="plan", kind="tm", pref="retest_plan", value=int(m.group(1)), definition="学院2026复试方案“已招推免”")
    for m in re.finditer(r"2027推免目录[：:]\s*推免(\d+)", note):
        add(m, table="plan", kind="tm", pref="tm_catalog", year=2027, value=int(m.group(1)), definition="2027年推免硕士招生专业目录中的推免数")

    # 专项：退役大学生士兵计划
    for m in re.finditer(
        r"(?:另有?)?退役(?:大学生)?(?:士兵)?(?:计划)?(\d+)名?\s*[，,（(]?\s*(?:复试)?线(\d{3})(?:（(\d+)/(\d+)/(\d+)/(\d+)）)?",
        note,
    ):
        add(m, table="plan", kind="special_veteran", pref="special", value=int(m.group(1)), definition="退役大学生士兵专项计划")
        add(m, table="score", kind="special_veteran", pref="special", value=int(m.group(2)), lines=_four(m, 3), definition="退役大学生士兵专项计划复试线")
    for m in re.finditer(r"退役线(\d{3})（(\d+)/(\d+)/(\d+)/(\d+)）", note):
        add(m, table="score", kind="special_veteran", pref="special", value=int(m.group(1)), lines=_four(m, 2), definition="退役大学生士兵专项计划复试线")
    for m in re.finditer(r"另退役(\d+)(?=[；;])|含退役大学生士兵计划(\d+)", note):
        add(m, table="plan", kind="special_veteran", pref="special", value=int(m.group(1) or m.group(2)), definition="退役大学生士兵专项计划")

    # 专项：少数民族高层次骨干人才计划
    for m in re.finditer(
        r"(?:少数民族骨干|少干)(?:计划)?(\d+)名\s*[，,（(]?\s*线(\d{3})(?:（(\d+)/(\d+)/(\d+)/(\d+)）)?",
        note,
    ):
        add(m, table="plan", kind="special_minority", pref="special", value=int(m.group(1)), definition="少数民族高层次骨干人才计划")
        add(m, table="score", kind="special_minority", pref="special", value=int(m.group(2)), lines=_four(m, 3), definition="少数民族高层次骨干人才计划复试线")

    # 华工计划口径
    for m in re.finditer(r"统考可用计划(?:PDF)?也?写?(\d+)", note):
        add(m, table="plan", kind="available_exam", pref="available_exam", value=int(m.group(1)), definition="学校“统考可用计划”（2025-10-22 公布）")
    for m in re.finditer(r"统考(招生)?计划(\d+)", note):
        label = "统考招生计划" if m.group(1) else "统考计划"
        add(m, table="plan", kind="college_exam_plan", pref="college_exam_plan", value=int(m.group(2)), definition=f"学院通知“{label}”")

    # 暨南 2026 口径
    for m in re.finditer(r"2026[：:]\s*目录(\d+)(?:（[^）]*）)?[；;]\s*统招计划(\d+)[，,]\s*复试(\d+)人", note):
        add(m, table="plan", kind="catalog_total", pref="catalog", year=2026, value=int(m.group(1)), definition="2026年招生专业目录拟招生人数（含推免）")
        add(m, table="plan", kind="public_exam", pref="retest_plan", year=2026, value=int(m.group(2)), definition="2026年学院复试方案“统招计划”")
        add(m, table="stat", kind="retest_count", pref="retest_count", year=2026, value=int(m.group(3)), definition="2026年复试人数")
    for m in re.finditer(r"2026[：:]\s*(\d{4})按一级学科复试[，,]\s*统招计划(\d+)人[，,]\s*复试(\d+)人", note):
        pool = m.group(1)
        if code.startswith(pool):
            add(m, table="plan", kind="public_exam", pref="retest_plan", year=2026, value=int(m.group(2)), pool_scope=pool, definition=f"2026年{pool}一级学科统筹“统招计划”（二级学科不单列）")
            add(m, table="stat", kind="retest_count", pref="retest_count", year=2026, value=int(m.group(3)), pool_scope=pool, definition=f"2026年{pool}一级学科复试人数")
    for m in re.finditer(r"2026目录中分专业计划[：:]([^）)。]*)", note):
        for code_m in re.finditer(r"(\d{4}[0-9A-Z]{2})\s+(\d+)", m.group(1)):
            if code_m.group(1) == code:
                add(m, table="plan", kind="catalog_total", pref="catalog", year=2026, value=int(code_m.group(2)), definition="2026年招生专业目录拟招生人数（含推免）")
    for m in re.finditer(r"2027目录只给出(\d{4})合计(\d+)人（含推免）", note):
        pool = m.group(1)
        if code.startswith(pool):
            add(m, table="plan", kind="catalog_total", pref="catalog", year=2027, value=int(m.group(2)), pool_scope=pool, definition=f"2027年招生专业目录{pool}一级学科合计（含推免，二级学科不单列）")

    # 学校复试基本线（记在学校层面，不挂专业）
    for m in re.finditer(r"([\u4e00-\u9fff]{2,6})\[(\d{2,4})\](学硕|专硕)?\s*(\d{3})/(\d+)/(\d+)", note):
        s1, s2 = int(m.group(5)), int(m.group(6))
        add(m, table="baseline", kind="school_baseline", pref="school_baseline", value=int(m.group(4)), lines=(s1, s1, s2, s2), discipline_code=m.group(2), definition=f"学校2026年复试基本分数线（{m.group(1)}[{m.group(2)}]{m.group(3) or ''}）")
    for m in re.finditer(r"(\d{2})工学[：:]\s*总分(\d{3})[，,]\s*单科满分100的(\d+)[，,]\s*满分>100的(\d+)", note):
        s1, s2 = int(m.group(3)), int(m.group(4))
        add(m, table="baseline", kind="school_baseline", pref="school_baseline", value=int(m.group(2)), lines=(s1, s1, s2, s2), discipline_code=m.group(1), definition=f"学校2026年复试初试成绩基本要求（{m.group(1)}工学）")
    for m in re.finditer(r"(\d{2})交叉学科(\d{3})/(\d+)/(\d+)", note):
        s1, s2 = int(m.group(3)), int(m.group(4))
        add(m, table="baseline", kind="school_baseline", pref="school_baseline", value=int(m.group(2)), lines=(s1, s1, s2, s2), discipline_code=m.group(1), definition=f"学校2026年复试初试成绩基本要求（{m.group(1)}交叉学科）")

    # 人数统计（只存统计，不存个人信息）
    for m in re.finditer(r"统考拟录取(\d+)(?!\d)人?(?!\s*[（(]普通)(?!\s*\+)(?:[（(](?:初试)?(\d{3})[–-](\d{3}))?", note):
        counted = "由名单计数" if "计数" in _clause(note, m.start(), m.end()) else "官方公示"
        add(m, table="stat", kind="admit_count", pref="admission", value=int(m.group(1)), definition=f"2026年统考拟录取人数（{counted}）")
        if m.group(2):
            add(m, table="stat", kind="score_min", pref="admission", value=int(m.group(2)), definition="2026年统考拟录取初试成绩最低分")
            add(m, table="stat", kind="score_max", pref="admission", value=int(m.group(3)), definition="2026年统考拟录取初试成绩最高分")
    for m in re.finditer(r"复试名单(\d+)人(?:（(?:初试)?(\d{3})[–-](\d{3})）)?", note):
        add(m, table="stat", kind="retest_count", pref="retest_count", value=int(m.group(1)), definition="2026年复试名单人数")
        if m.group(2):
            add(m, table="stat", kind="score_min", pref="retest_count", value=int(m.group(2)), definition="2026年复试名单初试成绩最低分")
            add(m, table="stat", kind="score_max", pref="retest_count", value=int(m.group(3)), definition="2026年复试名单初试成绩最高分")
    for m in re.finditer(r"复试名单(\d+)人?[，,]\s*拟录取(\d+)(?![\d+])", note):
        add(m, table="stat", kind="retest_count", pref="retest_count", value=int(m.group(1)), definition="2026年复试名单人数")
        add(m, table="stat", kind="admit_count", pref="admission", value=int(m.group(2)), definition="2026年拟录取人数（官方公示）")
    return facts


def _sentence_with(note: str, needles: tuple[str, ...], must: tuple[str, ...] = ()) -> str | None:
    """First sentence containing a needle (needles tried in priority order) and all of `must`."""
    parts = re.split(r"[。；;]", note)
    for needle in needles:
        for part in parts:
            if needle in part and all(x in part for x in must):
                return part.strip()
    return None


@dataclass(frozen=True)
class NoteGap:
    metric: str
    status: str
    reason: str
    year: int | None = None
    verify_at: str | None = None
    school_level: bool = False


def _first_url(text: str) -> str | None:
    m = re.search(r"https?://[^\s）)】,，;；]+", text)
    return m.group(0).rstrip("。.)）") if m else None


def extract_note_gaps(
    note: str,
    *,
    code: str = "",
    is408: str = "",
    has_subjects: bool = False,
    has_catalog: bool = False,
) -> list[NoteGap]:
    """Gaps stated in 备注 / 是否408. School-wide phrases are marked school_level."""
    gaps: list[NoteGap] = []
    if not note and not is408:
        return gaps

    unpublished = _sentence_with(note, ("未发布",))
    if unpublished:
        ym = re.search(r"(20\d{2})", unpublished)
        gaps.append(NoteGap(
            metric="catalog", status="not_published", reason=unpublished,
            year=int(ym.group(1)) if ym else None,
            verify_at=_first_url(unpublished),
            school_level="简章" in unpublished or "目录截至" in unpublished,
        ))

    blocked = _sentence_with(note, ("拦截", "统一认证"))
    if blocked:
        url = _first_url(blocked) or _first_url(note)
        year_m = re.search(r"(20\d{2})目录", blocked) or re.search(r"(20\d{2})", blocked)
        year = int(year_m.group(1)) if year_m else None
        if not has_catalog:
            gaps.append(NoteGap(metric="catalog", status="blocked", reason=blocked, year=year, verify_at=url))
        if not has_subjects:
            gaps.append(NoteGap(metric="subjects", status="blocked", reason=blocked, year=year, verify_at=url))

    pooled = _sentence_with(note, ("一级学科统筹", "不分二级学科", "二级学科不单列"))
    if pooled and (code.startswith("0812") or "0812" in pooled):
        ym = re.search(r"(20\d{2})", pooled)
        gaps.append(NoteGap(
            metric="plan", status="pooled", reason=pooled,
            year=int(ym.group(1)) if ym else None,
        ))

    tm = _sentence_with(note, ("仅招收推免", "仅招推免")) or ("仅招推免" in is408 and "仅招收推免生，无统考科目")
    if tm:
        reason = tm if isinstance(tm, str) else "仅招收推免生，无统考科目"
        gaps.append(NoteGap(metric="subjects", status="tm_only", reason=reason))
        gaps.append(NoteGap(metric="score_line", status="tm_only", reason=reason))

    boundary = _sentence_with(note, ("边界项",))
    if boundary:
        gaps.append(NoteGap(metric="plan", status="boundary", reason=boundary))
    return gaps


def extract_source_gaps(school: dict[str, Any]) -> list[NoteGap]:
    """School-level gaps from sources.json (catalog_systems + publish hint)."""
    gaps: list[NoteGap] = []
    for system in school.get("catalog_systems") or []:
        status = str(system.get("status") or "")
        if not status.startswith("blocked"):
            continue
        reason = status.split(":", 1)[-1].strip() or status
        issues = "；".join(school.get("access_issues") or [])
        if "统一认证" in issues or "拦截" in issues:
            reason = _sentence_with(issues, ("统一认证", "拦截")) or reason
        gaps.append(NoteGap(
            metric="catalog", status="blocked", reason=reason,
            year=system.get("year"), verify_at=system.get("url"), school_level=True,
        ))
    hint = school.get("last_year_publish_hint") or ""
    if "未发布" in hint:
        ym = re.search(r"(20\d{2})", hint)
        gaps.append(NoteGap(
            metric="catalog", status="not_published", reason=hint,
            year=int(ym.group(1)) if ym else None, school_level=True,
        ))
    return gaps


@dataclass
class Bundle:
    data_dir: Path
    meta: dict[str, Any]
    rows: list[dict[str, str]]

    @property
    def schools(self) -> list[dict[str, Any]]:
        return list(self.meta.get("schools") or [])

    @property
    def documents(self) -> list[dict[str, Any]]:
        return list(self.meta.get("documents") or [])


def load_bundle(data_dir: Path) -> Bundle:
    meta = json.loads((data_dir / "sources.json").read_text(encoding="utf-8"))
    with (data_dir / "majors.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = [{k: clean(v) for k, v in r.items()} for r in csv.DictReader(f)]
    return Bundle(data_dir=data_dir, meta=meta, rows=rows)


@dataclass
class RowContext:
    school_id: str
    college: CollegeName
    college_id: str
    study_mode: str
    candidates: list[dict[str, Any]]
    note: str
    program_code: str = ""


_TITLE_PROGRAM_CODE = re.compile(r"(?<![0-9A-Z])(\d{4}[0-9A-Z]{2})(?![0-9A-Z])")

@dataclass
class Seeder:
    bundle: Bundle
    store: KaoyanStore
    report: SeedReport = field(default_factory=SeedReport)

    def __post_init__(self) -> None:
        self.aliases = build_school_aliases(self.bundle.schools)
        self.docs = self.bundle.documents
        self.by_attachment: dict[str, list[dict[str, Any]]] = {}
        self.by_page: dict[str, list[dict[str, Any]]] = {}
        for d in self.docs:
            if d.get("attachment_url"):
                self.by_attachment.setdefault(d["attachment_url"], []).append(d)
            if d.get("page_url"):
                self.by_page.setdefault(d["page_url"], []).append(d)
        self.college_ids: dict[tuple[str, str], str] = {}
        self.seed_only: dict[str, dict[str, Any]] = {}
        self._baselines: set[tuple[str, str | None, int]] = set()
        self._fact_keys: set[tuple[Any, ...]] = set()
        stamp = str(self.bundle.meta.get("collected_at") or "")
        m = re.match(r"(20\d{2}-\d{2}-\d{2})", stamp)
        self._gap_stamp = m.group(1) if m else "2026-09-25"

    # -- colleges ---------------------------------------------------------

    @staticmethod
    def _college_key(school_id: str, college: CollegeName) -> tuple[str, str]:
        return (school_id, college.code or college.name)

    def _slug_for(self, school_id: str, college: CollegeName) -> str | None:
        tokens: Counter[str] = Counter()
        for d in self.docs:
            if d["school"] != school_id or not d.get("local_path"):
                continue
            dc = parse_college(d.get("college"))
            if (college.code and dc.code == college.code) or (not college.code and dc.name == college.name):
                parts = Path(d["local_path"]).stem.split("_")
                if len(parts) > 1 and not parts[1].isdigit():
                    tokens[parts[1]] += 1
        return tokens.most_common(1)[0][0] if tokens else None

    def _site_for(self, school_id: str, college: CollegeName) -> str | None:
        hosts: Counter[str] = Counter()
        for d in self.docs:
            if d["school"] != school_id or not d.get("page_url"):
                continue
            dc = parse_college(d.get("college"))
            if (college.code and dc.code == college.code) or (not college.code and dc.name == college.name):
                hosts[urlparse(d["page_url"]).netloc] += 1
        return hosts.most_common(1)[0][0] if hosts else None

    def ensure_college(self, conn: Any, school_id: str, text: str) -> str | None:
        if not text or "校级" in text:
            return None
        college = parse_college(text)
        key = self._college_key(school_id, college)
        if key in self.college_ids:
            return self.college_ids[key]
        slug = self._slug_for(school_id, college)
        cid = f"{school_id}-{college.code or slug or college.name}"
        self.store.upsert(
            conn,
            "colleges",
            College(
                id=cid,
                school_id=school_id,
                code=college.code,
                name=college.name,
                slug=slug,
                campus=college.campus,
                site=self._site_for(school_id, college),
            ),
        )
        self.college_ids[key] = cid
        return cid

    # -- documents & source resolution -----------------------------------

    def row_candidates(self, conn: Any, school_id: str, urls: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for url in urls:
            matched = self.by_attachment.get(url, []) + self.by_page.get(url, [])
            if not matched:
                matched = [self._seed_only_doc(conn, school_id, url)]
            for d in matched:
                if d not in out:
                    out.append(d)
        return out

    def _seed_only_doc(self, conn: Any, school_id: str, url: str) -> dict[str, Any]:
        if url in self.seed_only:
            return self.seed_only[url]
        doc_id = f"seed-{hashlib.sha1(url.encode()).hexdigest()[:10]}"
        d = {"doc_id": doc_id, "school": school_id, "college": "", "title": url, "doc_type": None,
             "intake_year": None, "page_url": url, "attachment_url": None, "contains_personal_data": False}
        self.store.upsert(conn, "documents", Document(id=doc_id, school_id=school_id, title=url, page_url=url, status="seed_only"))
        self.seed_only[url] = d
        self.report.seed_only_documents += 1
        self.report.warnings.append(f"source URL not in sources.json, created seed_only document: {url}")
        return d

    def _score(self, d: dict[str, Any], pref: SourcePref, ctx: RowContext, year: int | None, in_row: bool) -> int | None:
        doc_type = d.get("doc_type")
        if doc_type not in pref.doc_types:
            return None
        title = str(d.get("title") or "")
        has_kw = any(k in title for k in pref.keywords)
        if pref.require_keyword and not has_kw:
            return None
        score = 100 - 10 * pref.doc_types.index(doc_type)
        if in_row:
            score += 20
        dc = parse_college(d.get("college"))
        if "校级" not in str(d.get("college") or ""):
            same = (ctx.college.code and dc.code == ctx.college.code) or (not ctx.college.code and dc.name == ctx.college.name)
            score += 30 if same else -50
        if year is not None and d.get("intake_year") is not None:
            score += 10 if int(d["intake_year"]) == year else -40
        if has_kw:
            score += 15
        title_codes = set(_TITLE_PROGRAM_CODE.findall(title))
        if title_codes and ctx.program_code and ctx.program_code not in title_codes:
            score -= 50
        mode_text = title + str(d.get("local_path") or "")
        if "非全日制" in mode_text:
            score += 10 if ctx.study_mode == "非全日制" else -20
        if pref.prefer_attachment and d.get("attachment_url"):
            score += 2
        if d.get("contains_personal_data"):
            score -= 3
        return score

    def pick_source(self, pref_name: str, ctx: RowContext, year: int | None, url: str | None = None) -> str | None:
        pref = PREFS[pref_name]
        if url:
            explicit = self.by_attachment.get(url, []) + self.by_page.get(url, [])
            scored = [(self._score(d, pref, ctx, year, True), d) for d in explicit]
            scored = [(s, d) for s, d in scored if s is not None]
            if scored:
                return max(scored, key=lambda x: x[0])[1]["doc_id"]
        best: tuple[int, dict[str, Any]] | None = None
        row_ids = {d["doc_id"] for d in ctx.candidates}
        pool = list(ctx.candidates) + [d for d in self.docs if d["school"] == ctx.school_id and d["doc_id"] not in row_ids]
        for d in pool:
            s = self._score(d, pref, ctx, year, d["doc_id"] in row_ids)
            if s is not None and (best is None or s > best[0]):
                best = (s, d)
        if best:
            return best[1]["doc_id"]
        fallback = next((d for d in ctx.candidates if not d.get("contains_personal_data")), None)
        if fallback is None and ctx.candidates:
            fallback = ctx.candidates[0]
        return fallback["doc_id"] if fallback else None

    def load_documents(self, conn: Any) -> None:
        seen_at = str(self.bundle.meta.get("collected_at") or "")
        for d in self.docs:
            school_id = d["school"]
            college_id = self.ensure_college(conn, school_id, d.get("college") or "")
            self.store.upsert(
                conn,
                "documents",
                Document(
                    id=d["doc_id"],
                    school_id=school_id,
                    college_id=college_id,
                    college_text=d.get("college"),
                    title=d["title"],
                    publish_date=d.get("publish_date"),
                    intake_year=d.get("intake_year"),
                    doc_type=d.get("doc_type"),
                    format=d.get("format"),
                    page_url=d.get("page_url"),
                    attachment_url=d.get("attachment_url"),
                    article_id=str(d["article_id"]) if d.get("article_id") is not None else None,
                    local_path=d.get("local_path"),
                    sha256=d.get("sha256"),
                    bytes=d.get("bytes"),
                    pages=d.get("pages"),
                    has_table=bool(d.get("has_table")),
                    contains_personal_data=bool(d.get("contains_personal_data")),
                    first_seen=seen_at or None,
                    last_seen=seen_at or None,
                    notes=d.get("notes"),
                ),
            )

    # -- facts --------------------------------------------------------------

    def _once(self, key: tuple[Any, ...]) -> bool:
        if key in self._fact_keys:
            return False
        self._fact_keys.add(key)
        return True

    def add_plan(self, conn: Any, plan: Plan) -> None:
        key = ("plan", plan.program_id, plan.year, plan.kind, plan.value, plan.pool_scope, plan.source_doc_id)
        if self._once(key):
            self.store.insert(conn, "plans", plan)
            self.report.plans += 1

    def add_line(self, conn: Any, line: ScoreLine) -> None:
        key = ("line", line.school_id, line.program_id, line.year, line.scope, line.discipline_code, line.total)
        if self._once(key):
            self.store.insert(conn, "score_lines", line)
            self.report.score_lines += 1

    def add_stat(self, conn: Any, stat: AdmissionStat) -> None:
        key = ("stat", stat.program_id, stat.year, stat.kind, stat.value)
        if self._once(key):
            self.store.insert(conn, "admission_stats", stat)
            self.report.admission_stats += 1

    def seed_row(self, conn: Any, row: dict[str, str]) -> None:
        school_id = resolve_school(row["学校"], self.aliases)
        if not school_id:
            self.report.warnings.append(f"unknown school: {row['学校']}")
            return
        college = parse_college(row["学院"])
        college_id = self.ensure_college(conn, school_id, row["学院"])
        assert college_id
        code = row["专业代码"]
        study_mode = row["学习方式(全日制/非全)"] or "全日制"
        note = row["备注"]
        year = parse_year(row["数据年份"]) or SCORE_LINE_YEAR
        tm_count = parse_count(row["其中推免人数"])
        program_id = f"{college_id}-{code}" + ("-pt" if study_mode == "非全日制" else "")
        self.store.upsert(
            conn,
            "programs",
            Program(
                id=program_id,
                school_id=school_id,
                college_id=college_id,
                code=code,
                name=row["专业名称"],
                degree_type=row["学位类型(学硕/专硕)"] or None,
                study_mode=study_mode,
                pool_code=tm_count.pool_scope,
                is_boundary="边界项" in note,
                notes=note or None,
            ),
        )
        self.report.programs += 1
        ctx = RowContext(
            school_id=school_id,
            college=college,
            college_id=college_id,
            study_mode=study_mode,
            candidates=self.row_candidates(conn, school_id, split_source_urls(row["来源URL"])),
            note=note,
            program_code=code,
        )
        seed = dict(extraction_method="manual_seed", verified=True)

        # directions
        dir_text = row["研究方向"]
        dir_note = None
        m = re.match(r"^[（(]([^）)]*)[）)]\s*", dir_text)
        if m:
            dir_note, dir_text = m.group(1), dir_text[m.end():]
        dir_src = self.pick_source("admission" if dir_note and "拟录取" in dir_note else "subjects", ctx, year)
        for part in [p.strip() for p in dir_text.split("；") if p.strip()]:
            dm = re.match(r"^(\d{2})\s*(.+)$", part)
            self.store.insert(conn, "directions", Direction(
                program_id=program_id, year=year, code=dm.group(1) if dm else None,
                name=dm.group(2).strip() if dm else part, note=dir_note, source_doc_id=dir_src, **seed))
            self.report.directions += 1

        # exam subjects
        subj_src = self.pick_source("subjects", ctx, year)
        subjects = [parse_subject(row[c]) for c in ("考试科目1政治", "考试科目2外语", "考试科目3", "考试科目4")]
        if any(subjects):
            for slot, s in enumerate(subjects, start=1):
                if s:
                    self.store.insert(conn, "exam_subjects", ExamSubject(
                        program_id=program_id, year=year, slot=slot, code=s.code, name=s.name,
                        status="known", source_doc_id=subj_src, **seed))
                    self.report.exam_subjects += 1
        else:
            is408 = row["是否408"]
            if "仅招推免" in is408:
                status = "no_exam"
                reason = _sentence_with(note, ("仅招收推免",)) or "仅招收推免生，无统考科目"
            else:
                status = "unknown"
                reason = (
                    _sentence_with(note, ("拦截", "要等", "未取得", "未发布"), ("目录",))
                    or "官方资料中未取得初试科目"
                )
            self.store.insert(conn, "exam_subjects", ExamSubject(
                program_id=program_id, year=year, status=status, unknown_reason=reason,
                source_doc_id=subj_src, **seed))
            self.report.exam_subjects += 1

        # plans from columns
        total = parse_count(row["拟招生人数(总)"])
        if not total.is_empty:
            self.add_plan(conn, Plan(
                program_id=program_id, year=year, kind="catalog_total", value=total.value,
                value_text=total.text, definition=f"{year}年招生专业目录拟招生人数（含推免）",
                source_doc_id=self.pick_source("catalog", ctx, year),
                evidence_text=f"majors.csv 拟招生人数(总)={total.text}", **seed))
        if not tm_count.is_empty:
            tm_pref, tm_def = TM_COLUMN.get(school_id, TM_COLUMN_DEFAULT)
            if "推免目录" in row["数据年份"]:
                tm_pref, tm_def = "tm_catalog", f"{year}年推免硕士招生专业目录中的推免数"
            if tm_count.pool_scope:
                tm_def += (f"；原文在{tm_count.pool_scope}各二级学科行都写“{tm_count.text}”，"
                           f"按一级学科统筹记（原文没写“合计”）")
            self.add_plan(conn, Plan(
                program_id=program_id, year=year, kind="tm", value=tm_count.value, value_text=tm_count.text,
                is_upper_bound=tm_count.is_upper_bound, pool_scope=tm_count.pool_scope, definition=tm_def,
                source_doc_id=self.pick_source(tm_pref, ctx, year),
                evidence_text=f"majors.csv 其中推免人数={row['其中推免人数']}", **seed))

        # score line from columns
        line_total = parse_count(row["2026复试线总分"])
        if not line_total.is_empty:
            single_text = row["复试线单科(政治/外语/业务课)"]
            singles = parse_subject_lines(single_text)
            baseline = "学校" in single_text or "学校基本线" in note
            disc = None
            if baseline:
                dm = re.search(r"(\d{2})(?:交叉学科|工学)", single_text) or re.search(r"(\d{2})(?:交叉学科|工学)", note)
                disc = dm.group(1) if dm else code[:2]
            self.add_line(conn, ScoreLine(
                school_id=school_id, program_id=program_id, year=SCORE_LINE_YEAR,
                scope="school_baseline" if baseline else "college", discipline_code=disc,
                total=line_total.value, politics=singles.politics, foreign_lang=singles.foreign_lang,
                subject1=singles.subject1, subject2=singles.subject2,
                raw_text=f"{line_total.text} {single_text}".strip(),
                definition=(f"学校2026年复试初试成绩基本要求（{disc}，该专业适用）" if baseline else "学院公布的2026年复试分数线"),
                source_doc_id=self.pick_source("school_baseline" if baseline else "college_line", ctx, SCORE_LINE_YEAR),
                evidence_text=f"majors.csv 2026复试线总分={line_total.text}；单科={single_text}", **seed))

        # facts from 备注
        note_seed = dict(extraction_method="seed_note_regex", verified=True)
        for f in extract_note_facts(note, code):
            src = self.pick_source(f.pref, ctx, f.year, url=f.url)
            if f.table == "plan":
                self.add_plan(conn, Plan(
                    program_id=program_id, year=f.year, kind=f.kind, value=f.value, value_text=str(f.value),
                    is_upper_bound=f.is_upper_bound, pool_scope=f.pool_scope, definition=f.definition,
                    source_doc_id=src, evidence_text=f.evidence, **note_seed))
            elif f.table == "score":
                p, fl, s1, s2 = f.lines
                self.add_line(conn, ScoreLine(
                    school_id=school_id, program_id=program_id, year=f.year, scope=f.kind, total=f.value,
                    politics=p, foreign_lang=fl, subject1=s1, subject2=s2, raw_text=f.evidence,
                    definition=f.definition, source_doc_id=src, evidence_text=f.evidence, **note_seed))
            elif f.table == "baseline":
                key = (school_id, f.discipline_code, f.value or 0)
                if key in self._baselines:
                    continue
                self._baselines.add(key)
                p, fl, s1, s2 = f.lines
                self.add_line(conn, ScoreLine(
                    school_id=school_id, program_id=None, year=f.year, scope="school_baseline",
                    discipline_code=f.discipline_code, total=f.value, politics=p, foreign_lang=fl,
                    subject1=s1, subject2=s2, raw_text=f.evidence, definition=f.definition,
                    source_doc_id=src, evidence_text=f.evidence, **note_seed))
            elif f.table == "stat" and f.value is not None:
                self.add_stat(conn, AdmissionStat(
                    program_id=program_id, year=f.year, kind=f.kind, value=f.value, pool_scope=f.pool_scope,
                    definition=f.definition, source_doc_id=src, evidence_text=f.evidence, **note_seed))

        for gap in extract_note_gaps(
            note, code=code, is408=row["是否408"],
            has_subjects=any(subjects), has_catalog=not total.is_empty,
        ):
            self.add_gap(conn, DataGap(
                school_id=school_id,
                program_id=None if gap.school_level else program_id,
                year=gap.year, metric=gap.metric, status=gap.status, reason=gap.reason,
                verify_at=gap.verify_at, updated_at=self._gap_stamp,
            ))

    def add_gap(self, conn: Any, gap: DataGap) -> None:
        key = ("gap", gap.school_id, gap.program_id, gap.year, gap.metric, gap.status)
        if self._once(key):
            self.store.insert(conn, "data_gaps", gap)
            self.report.data_gaps += 1

    def seed_source_gaps(self, conn: Any) -> None:
        for school in self.bundle.schools:
            for gap in extract_source_gaps(school):
                self.add_gap(conn, DataGap(
                    school_id=school["id"], program_id=None,
                    year=gap.year, metric=gap.metric, status=gap.status, reason=gap.reason,
                    verify_at=gap.verify_at, updated_at=self._gap_stamp,
                ))

    def run(self) -> SeedReport:
        with self.store.transaction() as conn:
            self.store.delete_seed_facts(conn)
            conn.execute("DELETE FROM data_gaps")
            conn.execute("DELETE FROM documents WHERE status = 'seed_only'")
            for s in self.bundle.schools:
                self.store.upsert(conn, "schools", School(
                    id=s["id"], name=s["name"], short_name=s.get("short"),
                    domains_json=json.dumps(s.get("official_domains") or [], ensure_ascii=False)))
            for row in self.bundle.rows:
                school_id = resolve_school(row["学校"], self.aliases)
                if school_id:
                    self.ensure_college(conn, school_id, row["学院"])
            self.load_documents(conn)
            self.seed_source_gaps(conn)
            for row in self.bundle.rows:
                self.seed_row(conn, row)
        r = self.report
        r.schools = self.store.count("schools")
        r.colleges = self.store.count("colleges")
        r.documents = self.store.count("documents")
        r.programs = self.store.count("programs")
        return r


def seed_kaoyan(store: KaoyanStore, data_dir: Path) -> SeedReport:
    return Seeder(load_bundle(data_dir), store).run()
