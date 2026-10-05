-- Phase 4b-1 (evidence and findings). Fully additive: four new tables and one nullable column.
-- The older findings / evidence / finding_evidence tables (the Insights phase write-ups and their
-- linked files) are not touched. See docs/superpowers/specs/2026-10-05-evidence-and-findings-design.md
-- section 2.

CREATE TABLE cited_sources (
    id             INTEGER PRIMARY KEY,
    project_id     INTEGER NOT NULL REFERENCES projects(id),
    url            TEXT NOT NULL,
    title          TEXT NOT NULL,
    publisher      TEXT,
    published_date TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE(project_id, url)
);

CREATE TABLE facts (
    id                    INTEGER PRIMARY KEY,
    project_id            INTEGER NOT NULL REFERENCES projects(id),
    phase_key             TEXT NOT NULL,
    claim                 TEXT NOT NULL,
    claim_key             TEXT NOT NULL,
    quote                 TEXT,
    cited_source_id       INTEGER REFERENCES cited_sources(id),
    as_of                 TEXT,
    research_work_item_id INTEGER NOT NULL REFERENCES research_work_items(id),
    run_id                TEXT REFERENCES research_runs(id),
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    UNIQUE(project_id, claim_key)
);

CREATE INDEX idx_facts_project_phase ON facts(project_id, phase_key, status);

CREATE TABLE conclusions (
    id                    INTEGER PRIMARY KEY,
    project_id            INTEGER NOT NULL REFERENCES projects(id),
    phase_key             TEXT NOT NULL,
    text                  TEXT NOT NULL,
    research_work_item_id INTEGER NOT NULL REFERENCES research_work_items(id),
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX idx_conclusions_project_phase ON conclusions(project_id, phase_key);

CREATE TABLE conclusion_facts (
    conclusion_id INTEGER NOT NULL REFERENCES conclusions(id),
    fact_id       INTEGER NOT NULL REFERENCES facts(id),
    PRIMARY KEY (conclusion_id, fact_id)
);

-- Set when facts have been taken from this run's report (by the extraction after a review, or the "Extract facts" button),
-- and used as an atomic claim so a double-click cannot pay for the same report twice.
ALTER TABLE research_runs ADD COLUMN facts_extracted_at TEXT;
