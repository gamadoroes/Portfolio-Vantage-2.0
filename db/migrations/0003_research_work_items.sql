-- Structured research-task state (Phase 2). Fully additive: two new tables,
-- two new nullable columns on existing tables. Does NOT touch research_tasks,
-- findings, evidence, or project_insight_versions -- those keep anchoring the
-- versioned insights-history mechanism exactly as today. See
-- docs/superpowers/specs/2026-10-04-research-task-state-design.md section 2
-- for why this is a new table rather than a rebuild of research_tasks.

CREATE TABLE research_work_items (
    id                       INTEGER PRIMARY KEY,
    project_id               INTEGER NOT NULL REFERENCES projects(id),
    phase_key                TEXT,
    title                    TEXT NOT NULL,
    objective                TEXT,
    status                   TEXT NOT NULL DEFAULT 'PROPOSED',
    priority                 TEXT,
    research_method          TEXT,
    entities_json            TEXT,
    expected_output          TEXT,
    source_requirements_json TEXT,
    completeness_score       REAL,
    evidence_score           REAL,
    identified_gaps_json     TEXT,
    retry_count              INTEGER NOT NULL DEFAULT 0,
    max_retries              INTEGER NOT NULL DEFAULT 3,
    human_review_required    INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE INDEX idx_research_work_items_project_phase
    ON research_work_items(project_id, phase_key);

CREATE TABLE research_work_item_dependencies (
    work_item_id            INTEGER NOT NULL REFERENCES research_work_items(id),
    depends_on_work_item_id INTEGER NOT NULL REFERENCES research_work_items(id),
    created_at              TEXT NOT NULL,
    PRIMARY KEY (work_item_id, depends_on_work_item_id)
);

ALTER TABLE research_runs ADD COLUMN research_work_item_id INTEGER;
ALTER TABLE artefacts ADD COLUMN research_work_item_id INTEGER;
