"""Sprint 1 — Prevent Data Loss in Core Workflows

Tests grouped by the bug fix they verify:
  C3  Chat session ID collision
  C1  Chat history lost-update during SSE streaming
  C2  Artifact save race — content overwrite
  H1  No guard against concurrent deep research runs
"""

import threading
from datetime import datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(tmp_path, name="test_project"):
    """Create a minimal project directory and patch get_project_dir to use it."""
    project_dir = tmp_path / "projects" / name
    project_dir.mkdir(parents=True)
    (project_dir / "files").mkdir()
    return project_dir


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """Yield a (project_name, project_dir) tuple with patched project root."""
    name = "test_project"
    project_dir = _make_project(tmp_path, name)
    monkeypatch.setattr(
        "services.project_service.get_projects_dir",
        lambda: tmp_path / "projects",
    )
    return name, project_dir


# ===========================================================================
# C3 — Chat session ID collision (second-precision timestamps)
# ===========================================================================

class TestC3_ChatIdCollision:

    def test_chat_id_contains_random_suffix(self, project, temp_db):
        """New chat IDs must include a random hex suffix after the timestamp."""
        from db.repositories import projects_repo
        from services.chat_service import create_new_chat

        projects_repo.get_or_create_id(project[0])
        chat_id = create_new_chat(project[0])
        parts = chat_id.split("_")
        # Format: YYYYMMDD_HHMMSS_<hex>
        assert len(parts) == 3, f"Expected 3 parts in chat_id, got {parts}"
        assert len(parts[2]) == 8, f"Hex suffix should be 8 chars, got '{parts[2]}'"

    def test_ten_rapid_creates_all_unique(self, project, temp_db):
        """10 create_new_chat calls in a tight loop must all produce distinct IDs."""
        from db.repositories import projects_repo
        from services.chat_service import create_new_chat

        projects_repo.get_or_create_id(project[0])
        ids = [create_new_chat(project[0]) for _ in range(10)]
        assert len(set(ids)) == 10, f"Duplicate IDs found: {ids}"

    def test_concurrent_creates_all_unique(self, project, temp_db):
        """Threads creating chats simultaneously must never collide."""
        from db.repositories import projects_repo
        from services.chat_service import create_new_chat

        # Pre-create the project row: get_or_create_id's SELECT-then-INSERT is not
        # itself race-safe, but create_new_chat is only ever called for a project
        # that already exists by the time a chat is created in the real app, so we
        # mirror that here rather than exercising an unrelated repo-level race.
        projects_repo.get_or_create_id(project[0])

        ids = []
        lock = threading.Lock()

        def create():
            cid = create_new_chat(project[0])
            with lock:
                ids.append(cid)

        threads = [threading.Thread(target=create) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(ids)) == 10, f"Duplicate IDs in concurrent creation: {ids}"

    def test_old_format_chat_ids_still_work(self, project, temp_db):
        """Chats with legacy ID format (YYYYMMDD_HHMMSS, no hex suffix) must load
        and accept appends just like current-format IDs — the ID is just a string
        primary key to the service, so no migration/special-casing is needed."""
        from db.repositories import chat_repo, projects_repo
        from services.chat_service import append_message_to_chat, load_chat_sessions

        project_id = projects_repo.get_or_create_id(project[0])
        old_id = "20250115_143022"
        chat_repo.create_session(old_id, project_id, "Legacy chat")
        chat_repo.append_message(old_id, "user", "hello")

        sessions = load_chat_sessions(project[0])
        assert old_id in sessions
        assert len(sessions[old_id]["messages"]) == 1

        # Appending to an old-format chat must also work
        append_message_to_chat(project[0], old_id, {"role": "assistant", "content": "hi"})
        sessions = load_chat_sessions(project[0])
        assert len(sessions[old_id]["messages"]) == 2


# ===========================================================================
# C1 — Chat history lost-update during SSE streaming
# ===========================================================================

class TestC1_ChatHistoryLostUpdate:

    def test_append_message_persists(self, project, temp_db):
        """append_message_to_chat must persist a message atomically."""
        from db.repositories import projects_repo
        from services.chat_service import append_message_to_chat, create_new_chat, load_chat_sessions

        projects_repo.get_or_create_id(project[0])
        chat_id = create_new_chat(project[0])
        append_message_to_chat(project[0], chat_id, {"role": "user", "content": "q1"})
        append_message_to_chat(project[0], chat_id, {"role": "assistant", "content": "a1"})

        sessions = load_chat_sessions(project[0])
        msgs = sessions[chat_id]["messages"]
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "q1", "artifact_id": None}
        assert msgs[1] == {"role": "assistant", "content": "a1", "artifact_id": None}

    def test_concurrent_appends_no_lost_messages(self, project, temp_db):
        """Two threads appending to the same chat must both persist."""
        from db.repositories import projects_repo
        from services.chat_service import append_message_to_chat, create_new_chat, load_chat_sessions

        projects_repo.get_or_create_id(project[0])
        chat_id = create_new_chat(project[0])
        errors = []

        def append_n(start, count):
            try:
                for i in range(count):
                    append_message_to_chat(
                        project[0], chat_id,
                        {"role": "user", "content": f"msg-{start + i}"},
                    )
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=append_n, args=(0, 10))
        t2 = threading.Thread(target=append_n, args=(100, 10))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors during concurrent appends: {errors}"
        sessions = load_chat_sessions(project[0])
        msgs = sessions[chat_id]["messages"]
        assert len(msgs) == 20, f"Expected 20 messages, got {len(msgs)}"

    def test_append_to_nonexistent_chat_is_noop(self, project, temp_db):
        """Appending to a deleted/missing chat must not crash or create a ghost entry."""
        from db.repositories import projects_repo
        from services.chat_service import append_message_to_chat, load_chat_sessions

        projects_repo.get_or_create_id(project[0])
        append_message_to_chat(project[0], "no_such_chat", {"role": "user", "content": "x"})
        sessions = load_chat_sessions(project[0])
        assert "no_such_chat" not in sessions

    def test_simulated_two_tab_streaming(self, project, temp_db):
        """Simulate C1 test scenario: two browser tabs sending messages to
        the same chat concurrently, with streaming delays.

        Tab A sends user_A, streams for a while, then saves assistant_A.
        While Tab A streams, Tab B sends user_B, streams briefly, saves assistant_B.
        All 4 messages must be present afterward.
        """
        import time

        from db.repositories import projects_repo
        from services.chat_service import (
            append_message_to_chat,
            create_new_chat,
            load_chat_sessions,
            load_chat_sessions_locked,
        )

        projects_repo.get_or_create_id(project[0])
        chat_id = create_new_chat(project[0])
        errors = []

        def tab_a():
            try:
                # Tab A reads sessions, appends user message
                sessions = load_chat_sessions_locked(project[0])
                assert chat_id in sessions
                append_message_to_chat(project[0], chat_id,
                                       {"role": "user", "content": "user_A"})
                # Simulate long streaming delay
                time.sleep(0.15)
                # Tab A finishes streaming, appends assistant message
                append_message_to_chat(project[0], chat_id,
                                       {"role": "assistant", "content": "assistant_A"})
            except Exception as e:
                errors.append(("tab_a", e))

        def tab_b():
            try:
                # Small delay so Tab A starts first
                time.sleep(0.05)
                # Tab B reads sessions (should see user_A already persisted)
                sessions = load_chat_sessions_locked(project[0])
                assert chat_id in sessions
                append_message_to_chat(project[0], chat_id,
                                       {"role": "user", "content": "user_B"})
                # Tab B streams briefly
                time.sleep(0.05)
                # Tab B finishes streaming, appends assistant message
                append_message_to_chat(project[0], chat_id,
                                       {"role": "assistant", "content": "assistant_B"})
            except Exception as e:
                errors.append(("tab_b", e))

        t1 = threading.Thread(target=tab_a)
        t2 = threading.Thread(target=tab_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors: {errors}"

        # Verify all 4 messages are present in chat_history.json
        sessions = load_chat_sessions(project[0])
        msgs = sessions[chat_id]["messages"]
        contents = [m["content"] for m in msgs]
        assert "user_A" in contents, f"user_A missing from {contents}"
        assert "user_B" in contents, f"user_B missing from {contents}"
        assert "assistant_A" in contents, f"assistant_A missing from {contents}"
        assert "assistant_B" in contents, f"assistant_B missing from {contents}"
        assert len(msgs) == 4, f"Expected 4 messages, got {len(msgs)}: {contents}"


# ===========================================================================
# C2 — Artifact save race — content overwrite
# ===========================================================================

class TestC2_ArtifactSaveRace:

    def test_unique_md_filename_reserves_file(self, project):
        """_unique_md_filename must create a placeholder to prevent collisions."""
        from routes.artifacts import _unique_md_filename

        files_dir = project[1] / "files"
        name1 = _unique_md_filename(project[0], "Report")
        assert (files_dir / name1).exists(), "Placeholder file should exist"

    def test_unique_md_filename_no_collision(self, project):
        """Two calls with the same base name must return different filenames."""
        from routes.artifacts import _unique_md_filename

        name1 = _unique_md_filename(project[0], "Report")
        name2 = _unique_md_filename(project[0], "Report")
        assert name1 != name2, f"Both calls returned '{name1}'"

    def test_concurrent_unique_filenames(self, project):
        """Threads calling _unique_md_filename simultaneously get distinct names."""
        from routes.artifacts import _unique_md_filename

        names = []
        lock = threading.Lock()

        def get_name():
            n = _unique_md_filename(project[0], "Analysis")
            with lock:
                names.append(n)

        threads = [threading.Thread(target=get_name) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(names)) == 8, f"Duplicate filenames: {names}"

    def test_artifact_lock_serialises_saves(self):
        """_get_artifact_lock returns the same lock for the same project."""
        from routes.artifacts import _get_artifact_lock

        lock_a = _get_artifact_lock("proj_a")
        lock_a2 = _get_artifact_lock("proj_a")
        lock_b = _get_artifact_lock("proj_b")

        assert lock_a is lock_a2, "Same project should get the same lock"
        assert lock_a is not lock_b, "Different projects should get different locks"


# ===========================================================================
# H1 — Duplicate deep research run prevention (prompt-based debounce)
# ===========================================================================

class TestH1_DeepResearchGuard:

    def test_duplicate_prompt_blocked(self, project, temp_db):
        """Same prompt submitted twice within debounce window is rejected."""
        from db.repositories import projects_repo, research_runs_repo
        from services.research_run_service import is_duplicate_run

        project_id = projects_repo.get_or_create_id(project[0])
        research_runs_repo.create(
            "run_abc", project_id, None, None, "Analyse competitor landscape", status="running"
        )

        assert is_duplicate_run(project[0], "Analyse competitor landscape") is True

    def test_different_prompt_allowed(self, project, temp_db):
        """A different prompt is allowed even while another run is active."""
        from db.repositories import projects_repo, research_runs_repo
        from services.research_run_service import is_duplicate_run

        project_id = projects_repo.get_or_create_id(project[0])
        research_runs_repo.create(
            "run_abc", project_id, None, None, "Analyse competitor landscape", status="running"
        )

        assert is_duplicate_run(project[0], "Student demand analysis") is False

    def test_no_runs_not_duplicate(self, project, temp_db):
        """No runs exist — any prompt is allowed."""
        from db.repositories import projects_repo
        from services.research_run_service import is_duplicate_run

        projects_repo.get_or_create_id(project[0])
        assert is_duplicate_run(project[0], "Any prompt") is False

    def test_completed_run_same_prompt_allowed(self, project, temp_db):
        """A completed run with the same prompt does not block a re-run."""
        from db.repositories import projects_repo, research_runs_repo
        from services.research_run_service import is_duplicate_run

        project_id = projects_repo.get_or_create_id(project[0])
        research_runs_repo.create(
            "run_done", project_id, None, None, "Analyse competitor landscape", status="completed"
        )

        assert is_duplicate_run(project[0], "Analyse competitor landscape") is False

    def test_old_run_same_prompt_allowed(self, project, temp_db):
        """A run outside the debounce window with the same prompt is allowed (re-run)."""
        from db.repositories import projects_repo, research_runs_repo
        from services.research_run_service import is_duplicate_run

        project_id = projects_repo.get_or_create_id(project[0])
        research_runs_repo.create(
            "run_old", project_id, None, None, "Analyse competitor landscape", status="running"
        )
        old_time = (datetime.now() - timedelta(seconds=60)).isoformat()
        research_runs_repo.update("run_old", created_at=old_time)

        assert is_duplicate_run(project[0], "Analyse competitor landscape") is False

    def test_queued_duplicate_blocked(self, project, temp_db):
        """A queued run with the same prompt also counts as a duplicate."""
        from db.repositories import projects_repo, research_runs_repo
        from services.research_run_service import is_duplicate_run

        project_id = projects_repo.get_or_create_id(project[0])
        research_runs_repo.create("run_q", project_id, None, None, "Market sizing", status="queued")

        assert is_duplicate_run(project[0], "Market sizing") is True

    def test_cancelled_run_does_not_block(self, project, temp_db):
        """A cancelled run with the same prompt does not block."""
        from db.repositories import projects_repo, research_runs_repo
        from services.research_run_service import is_duplicate_run

        project_id = projects_repo.get_or_create_id(project[0])
        research_runs_repo.create(
            "run_c", project_id, None, None, "Analyse competitor landscape", status="cancelled"
        )

        assert is_duplicate_run(project[0], "Analyse competitor landscape") is False

    def test_failed_run_does_not_block(self, project, temp_db):
        """A failed run with the same prompt does not block a retry."""
        from db.repositories import projects_repo, research_runs_repo
        from services.research_run_service import is_duplicate_run

        project_id = projects_repo.get_or_create_id(project[0])
        research_runs_repo.create(
            "run_f", project_id, None, None, "Analyse competitor landscape", status="failed"
        )

        assert is_duplicate_run(project[0], "Analyse competitor landscape") is False

    def test_parallel_different_prompts_both_allowed(self, project, temp_db):
        """Two active runs with different prompts — a third different prompt is allowed."""
        from db.repositories import projects_repo, research_runs_repo
        from services.research_run_service import is_duplicate_run

        project_id = projects_repo.get_or_create_id(project[0])
        research_runs_repo.create("run_1", project_id, None, None, "Competitor analysis", status="running")
        research_runs_repo.create("run_2", project_id, None, None, "Student demand", status="running")

        assert is_duplicate_run(project[0], "Marketing channels") is False
        assert is_duplicate_run(project[0], "Competitor analysis") is True

    def test_concurrent_run_creation_persists_all_runs(self, project, temp_db):
        """Concurrent create_run calls must not overwrite each other in the database."""
        from db.repositories import projects_repo
        from services.research_run_service import create_run, load_runs

        projects_repo.get_or_create_id(project[0])
        created_ids = []
        lock = threading.Lock()

        def create(idx):
            run_id = create_run(project[0], f"resp-{idx}", None, f"prompt {idx}")
            with lock:
                created_ids.append(run_id)

        threads = [threading.Thread(target=create, args=(idx,)) for idx in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        runs = load_runs(project[0])
        assert len(created_ids) == 12
        assert len(runs) == 12, f"Expected 12 persisted runs, found {len(runs)}"
        assert set(created_ids) == set(runs.keys())
