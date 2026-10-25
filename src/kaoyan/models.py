from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExtractionMethod = Literal["manual_seed", "seed_note_regex", "rule", "llm", "ocr"]
SubjectStatus = Literal["known", "unknown", "no_exam"]
LineScope = Literal["school_baseline", "college", "special_veteran", "special_minority"]
StatKind = Literal["retest_count", "admit_count", "score_min", "score_max"]
PlanKind = Literal[
    "catalog_total",
    "tm",
    "rules_total",
    "public_exam",
    "available_exam",
    "college_exam_plan",
    "special_veteran",
    "special_minority",
]

SEED_METHODS: tuple[str, ...] = ("manual_seed", "seed_note_regex")
EXTRACTED_METHODS: tuple[str, ...] = ("rule", "llm", "ocr")


class School(BaseModel):
    id: str
    name: str
    short_name: str | None = None
    domains_json: str = "[]"


class College(BaseModel):
    id: str
    school_id: str
    code: str | None = None
    name: str
    slug: str | None = None
    campus: str | None = None
    site: str | None = None


class Document(BaseModel):
    id: str
    school_id: str
    college_id: str | None = None
    college_text: str | None = None
    title: str
    publish_date: str | None = None
    intake_year: int | None = None
    doc_type: str | None = None
    format: str | None = None
    page_url: str | None = None
    attachment_url: str | None = None
    article_id: str | None = None
    local_path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    pages: int | None = None
    has_table: bool = False
    contains_personal_data: bool = False
    status: str = "active"
    first_seen: str | None = None
    last_seen: str | None = None
    parent_doc_id: str | None = None
    notes: str | None = None


class Program(BaseModel):
    id: str
    school_id: str
    college_id: str
    code: str
    name: str
    degree_type: str | None = None
    study_mode: str
    pool_code: str | None = None
    is_boundary: bool = False
    notes: str | None = None


class Direction(BaseModel):
    program_id: str
    year: int | None = None
    code: str | None = None
    name: str
    advisors: str | None = None
    note: str | None = None
    source_doc_id: str | None = None
    evidence_text: str | None = None
    extraction_method: ExtractionMethod
    verified: bool = False


class ExamSubject(BaseModel):
    program_id: str
    year: int | None = None
    slot: int | None = None
    code: str | None = None
    name: str | None = None
    status: SubjectStatus
    unknown_reason: str | None = None
    source_doc_id: str | None = None
    page: int | None = None
    evidence_text: str | None = None
    extraction_method: ExtractionMethod
    verified: bool = False


class Plan(BaseModel):
    program_id: str
    year: int
    kind: PlanKind
    value: int | None = None
    value_text: str | None = None
    is_upper_bound: bool = False
    pool_scope: str | None = None
    definition: str | None = None
    source_doc_id: str | None = None
    page: int | None = None
    evidence_text: str | None = None
    extraction_method: ExtractionMethod
    verified: bool = False


class ScoreLine(BaseModel):
    school_id: str
    program_id: str | None = None
    year: int
    scope: LineScope
    discipline_code: str | None = None
    total: int | None = None
    politics: int | None = None
    foreign_lang: int | None = None
    subject1: int | None = None
    subject2: int | None = None
    raw_text: str | None = None
    definition: str | None = None
    source_doc_id: str | None = None
    page: int | None = None
    evidence_text: str | None = None
    extraction_method: ExtractionMethod
    verified: bool = False


class AdmissionStat(BaseModel):
    program_id: str
    year: int
    kind: StatKind
    value: int
    pool_scope: str | None = None
    definition: str | None = None
    source_doc_id: str | None = None
    evidence_text: str | None = None
    extraction_method: ExtractionMethod
    verified: bool = False


class DataGap(BaseModel):
    school_id: str
    program_id: str | None = None
    year: int | None = None
    metric: str
    status: str
    reason: str
    verify_at: str | None = None
    source_doc_id: str | None = None
    updated_at: str


class SeedReport(BaseModel):
    schools: int = 0
    colleges: int = 0
    documents: int = 0
    programs: int = 0
    directions: int = 0
    exam_subjects: int = 0
    plans: int = 0
    score_lines: int = 0
    admission_stats: int = 0
    data_gaps: int = 0
    seed_only_documents: int = 0
    warnings: list[str] = Field(default_factory=list)
