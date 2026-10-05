-- Research Board (2026-10-05). Fully additive: seven new nullable columns.
-- See docs/superpowers/specs/2026-10-05-research-board-design.md section 3.

ALTER TABLE research_work_items ADD COLUMN prompt_text TEXT;
ALTER TABLE research_work_items ADD COLUMN framework_key TEXT;
ALTER TABLE research_work_items ADD COLUMN rationale TEXT;
ALTER TABLE research_work_items ADD COLUMN suggested_from_work_item_id INTEGER REFERENCES research_work_items(id);

-- The full prompt as sent (prompt_preview is clipped to 200 characters), the report
-- file saved from the run's output, and when that file was linked to its phase.
ALTER TABLE research_runs ADD COLUMN prompt_text TEXT;
ALTER TABLE research_runs ADD COLUMN report_stable_file_id TEXT;
ALTER TABLE research_runs ADD COLUMN report_linked_at TEXT;
