-- Denormalize filename/stable_file_id onto the evidence row at creation time.
-- Without this, reconstructing a historical insight version's linked_files
-- requires a live join against sources(id) via evidence.source_id -- which
-- breaks once sources_repo.delete() nulls source_id (necessary to avoid an
-- FK crash on file deletion). This snapshot survives that, and survives a
-- later rename too, matching "historical versions show content as it was."
ALTER TABLE evidence ADD COLUMN filename TEXT;
ALTER TABLE evidence ADD COLUMN stable_file_id TEXT;
