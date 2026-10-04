-- Phase 3 (Research Supervisor). Fully additive: two new nullable columns.
-- Does NOT touch research_tasks, findings, evidence, or project_insight_versions.
-- agent_decisions.research_task_id (the legacy Phase-1 FK) is untouched and
-- unused by this phase -- decisions from the supervisor reference
-- research_work_items instead, via this new column.
-- See docs/superpowers/specs/2026-10-04-research-supervisor-design.md section 3.

ALTER TABLE agent_decisions ADD COLUMN research_work_item_id INTEGER REFERENCES research_work_items(id);

-- FILE_ANALYSIS research runs complete synchronously via Claude, not via
-- OpenAI's response_id-based polling, so there's nowhere today to store
-- that kind of run's output text.
ALTER TABLE research_runs ADD COLUMN output_text TEXT;
