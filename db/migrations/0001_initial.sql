CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE sources (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    stable_file_id  TEXT NOT NULL,
    filename        TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'upload',
    retrieved_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(project_id, stable_file_id)
);

CREATE TABLE project_selected_sources (
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, source_id)
);

CREATE TABLE artefacts (
    id          TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    source_id   INTEGER REFERENCES sources(id),
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE chat_sessions (
    id          TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE project_messages (
    id              INTEGER PRIMARY KEY,
    chat_session_id TEXT NOT NULL REFERENCES chat_sessions(id),
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    artefact_id     TEXT REFERENCES artefacts(id),
    created_at      TEXT NOT NULL,
    UNIQUE(chat_session_id, seq)
);

CREATE TABLE research_runs (
    id              TEXT PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    response_id     TEXT,
    chat_session_id TEXT REFERENCES chat_sessions(id),
    status          TEXT NOT NULL,
    artefact_id     TEXT REFERENCES artefacts(id),
    prompt_preview  TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE research_tasks (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    phase_key   TEXT,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'not_started',
    confidence  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(project_id, phase_key)
);

CREATE TABLE project_insight_versions (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    version         INTEGER NOT NULL,
    generated_at    TEXT NOT NULL,
    competitors_json TEXT,
    competitor_landscape_markdown TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(project_id, version)
);

CREATE TABLE findings (
    id                  INTEGER PRIMARY KEY,
    research_task_id    INTEGER NOT NULL REFERENCES research_tasks(id),
    insight_version_id  INTEGER NOT NULL REFERENCES project_insight_versions(id),
    content             TEXT NOT NULL,
    gaps_notes          TEXT,
    confidence          TEXT,
    effective_date      TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(research_task_id, insight_version_id)
);

CREATE TABLE evidence (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    source_id       INTEGER REFERENCES sources(id),
    raw_text        TEXT,
    retrieved_at    TEXT,
    effective_date  TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE finding_evidence (
    finding_id  INTEGER NOT NULL REFERENCES findings(id),
    evidence_id INTEGER NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (finding_id, evidence_id)
);

CREATE TABLE agent_decisions (
    id               INTEGER PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES projects(id),
    research_task_id INTEGER REFERENCES research_tasks(id),
    decision_type    TEXT NOT NULL,
    detail           TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE excluded_competitors (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    competitor_name TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(project_id, competitor_name)
);
