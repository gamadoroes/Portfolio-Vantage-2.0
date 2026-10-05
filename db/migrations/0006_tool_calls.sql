-- Phase 4a (Supervisor tool layer). Fully additive: one new table.
-- Every tool call is logged here (who called what, with which inputs, and the outcome).
-- See docs/superpowers/specs/2026-10-05-supervisor-tool-layer-design.md section 2.4.

CREATE TABLE tool_calls (
    id                    INTEGER PRIMARY KEY,
    project_id            INTEGER NOT NULL REFERENCES projects(id),
    tool                  TEXT NOT NULL,
    caller                TEXT NOT NULL,
    research_work_item_id INTEGER REFERENCES research_work_items(id),
    parent_call_id        INTEGER REFERENCES tool_calls(id),
    input_json            TEXT,
    ok                    INTEGER NOT NULL,
    error_code            TEXT,
    error_message         TEXT,
    result_json           TEXT,
    started_at            TEXT NOT NULL,
    duration_ms           INTEGER
);

CREATE INDEX idx_tool_calls_project ON tool_calls(project_id, started_at);
