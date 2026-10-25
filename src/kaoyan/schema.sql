-- Kaoyan (硕士研招) structured store. Every numeric fact carries its source
-- document, year, definition (口径), extraction method and verification flag.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schools (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    short_name TEXT,
    domains_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS colleges (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    code TEXT,
    name TEXT NOT NULL,
    slug TEXT,
    campus TEXT,
    site TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    college_id TEXT REFERENCES colleges(id),
    college_text TEXT,
    title TEXT NOT NULL,
    publish_date TEXT,
    intake_year INTEGER,
    doc_type TEXT,
    format TEXT,
    page_url TEXT,
    attachment_url TEXT,
    article_id TEXT,
    local_path TEXT,
    sha256 TEXT,
    bytes INTEGER,
    pages INTEGER,
    has_table INTEGER NOT NULL DEFAULT 0,
    contains_personal_data INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    first_seen TEXT,
    last_seen TEXT,
    parent_doc_id TEXT REFERENCES documents(id),
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_school ON documents(school_id, doc_type, intake_year);
CREATE INDEX IF NOT EXISTS idx_documents_page_url ON documents(page_url);
CREATE INDEX IF NOT EXISTS idx_documents_attachment_url ON documents(attachment_url);

CREATE TABLE IF NOT EXISTS programs (
    id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL REFERENCES schools(id),
    college_id TEXT NOT NULL REFERENCES colleges(id),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    degree_type TEXT,
    study_mode TEXT NOT NULL,
    pool_code TEXT,
    is_boundary INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    UNIQUE (school_id, college_id, code, study_mode)
);

CREATE TABLE IF NOT EXISTS directions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id TEXT NOT NULL REFERENCES programs(id),
    year INTEGER,
    code TEXT,
    name TEXT NOT NULL,
    advisors TEXT,
    note TEXT,
    source_doc_id TEXT REFERENCES documents(id),
    evidence_text TEXT,
    extraction_method TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS exam_subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id TEXT NOT NULL REFERENCES programs(id),
    year INTEGER,
    slot INTEGER,
    code TEXT,
    name TEXT,
    status TEXT NOT NULL CHECK (status IN ('known', 'unknown', 'no_exam')),
    unknown_reason TEXT,
    source_doc_id TEXT REFERENCES documents(id),
    page INTEGER,
    evidence_text TEXT,
    extraction_method TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id TEXT NOT NULL REFERENCES programs(id),
    year INTEGER NOT NULL,
    kind TEXT NOT NULL,
    value INTEGER,
    value_text TEXT,
    is_upper_bound INTEGER NOT NULL DEFAULT 0,
    pool_scope TEXT,
    definition TEXT,
    source_doc_id TEXT REFERENCES documents(id),
    page INTEGER,
    evidence_text TEXT,
    extraction_method TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_plans_program ON plans(program_id, year, kind);

CREATE TABLE IF NOT EXISTS score_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id TEXT NOT NULL REFERENCES schools(id),
    program_id TEXT REFERENCES programs(id),
    year INTEGER NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('school_baseline', 'college', 'special_veteran', 'special_minority')),
    discipline_code TEXT,
    total INTEGER,
    politics INTEGER,
    foreign_lang INTEGER,
    subject1 INTEGER,
    subject2 INTEGER,
    raw_text TEXT,
    definition TEXT,
    source_doc_id TEXT REFERENCES documents(id),
    page INTEGER,
    evidence_text TEXT,
    extraction_method TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_score_lines_program ON score_lines(program_id, year, scope);
CREATE INDEX IF NOT EXISTS idx_score_lines_school ON score_lines(school_id, year, scope);

-- Aggregate statistics only; never candidate-level rows.
CREATE TABLE IF NOT EXISTS admission_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id TEXT NOT NULL REFERENCES programs(id),
    year INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('retest_count', 'admit_count', 'score_min', 'score_max')),
    value INTEGER NOT NULL,
    pool_scope TEXT,
    definition TEXT,
    source_doc_id TEXT REFERENCES documents(id),
    evidence_text TEXT,
    extraction_method TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS data_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id TEXT NOT NULL REFERENCES schools(id),
    program_id TEXT REFERENCES programs(id),
    year INTEGER,
    metric TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    verify_at TEXT,
    source_doc_id TEXT REFERENCES documents(id),
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_data_gaps_school ON data_gaps(school_id, year, metric);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    school_ids TEXT,
    mode TEXT,
    status TEXT NOT NULL,
    stats_json TEXT,
    error TEXT
);

-- One row per program: latest year per plan kind / score scope, with sources.
-- Multiple sources for the same kind are concatenated as "value@doc_id" (never overwritten);
-- the same value from the same document (seed + rule extraction) is listed once.
DROP VIEW IF EXISTS v_program_facts;
CREATE VIEW v_program_facts AS
WITH latest_plan AS (
    SELECT program_id, kind, MAX(year) AS year FROM plans GROUP BY program_id, kind
),
plan_items AS (
    SELECT p.program_id, p.kind, p.year,
           CASE WHEN p.is_upper_bound = 1 THEN '≤' ELSE '' END
           || COALESCE(CAST(p.value AS TEXT), '?')
           || CASE WHEN p.pool_scope IS NOT NULL THEN '[' || p.pool_scope || ']' ELSE '' END
           || '@' || COALESCE(p.source_doc_id, '-') AS item,
           MIN(p.id) AS first_id
    FROM plans p
    JOIN latest_plan l ON l.program_id = p.program_id AND l.kind = p.kind AND l.year = p.year
    GROUP BY p.program_id, p.kind, p.year, item
),
plan_agg AS (
    SELECT program_id, kind, year, GROUP_CONCAT(item, '; ') AS facts
    FROM (SELECT * FROM plan_items ORDER BY first_id)
    GROUP BY program_id, kind, year
),
line_items AS (
    SELECT s.program_id, s.scope, s.year,
           COALESCE(CAST(s.total AS TEXT), '?') || '@' || COALESCE(s.source_doc_id, '-') AS item,
           MIN(s.id) AS first_id
    FROM score_lines s
    WHERE s.program_id IS NOT NULL
      AND s.year = (SELECT MAX(year) FROM score_lines x WHERE x.program_id = s.program_id AND x.scope = s.scope)
    GROUP BY s.program_id, s.scope, s.year, item
),
line_agg AS (
    SELECT program_id, scope, year, GROUP_CONCAT(item, '; ') AS facts
    FROM (SELECT * FROM line_items ORDER BY first_id)
    GROUP BY program_id, scope, year
)
SELECT
    pr.id AS program_id,
    pr.school_id,
    pr.college_id,
    pr.code,
    pr.name,
    pr.degree_type,
    pr.study_mode,
    pr.is_boundary,
    (SELECT year || ':' || facts FROM plan_agg WHERE program_id = pr.id AND kind = 'catalog_total') AS catalog_total,
    (SELECT year || ':' || facts FROM plan_agg WHERE program_id = pr.id AND kind = 'tm') AS tm,
    (SELECT year || ':' || facts FROM plan_agg WHERE program_id = pr.id AND kind = 'rules_total') AS rules_total,
    (SELECT year || ':' || facts FROM plan_agg WHERE program_id = pr.id AND kind = 'public_exam') AS public_exam,
    (SELECT year || ':' || facts FROM plan_agg WHERE program_id = pr.id AND kind = 'college_exam_plan') AS college_exam_plan,
    (SELECT year || ':' || facts FROM plan_agg WHERE program_id = pr.id AND kind = 'available_exam') AS available_exam,
    (SELECT year || ':' || facts FROM line_agg WHERE program_id = pr.id AND scope = 'college') AS college_line,
    (SELECT year || ':' || facts FROM line_agg WHERE program_id = pr.id AND scope = 'school_baseline') AS school_baseline_line,
    (SELECT GROUP_CONCAT(DISTINCT status) FROM exam_subjects WHERE program_id = pr.id) AS subject_status,
    (SELECT GROUP_CONCAT(code, '/') FROM (
        SELECT code FROM exam_subjects e
        WHERE e.program_id = pr.id AND e.status = 'known'
          AND e.year IS (SELECT MAX(year) FROM exam_subjects x WHERE x.program_id = pr.id AND x.status = 'known')
        GROUP BY e.slot, e.code ORDER BY e.slot
    )) AS subject_codes
FROM programs pr;
