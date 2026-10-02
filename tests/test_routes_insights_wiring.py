import json

import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations


@pytest.fixture
def client(tmp_path):
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            yield c
    set_database_path(None)


def _create_project(client, name):
    client.post("/api/projects", json={"name": name})


def test_posting_insights_json_does_not_create_a_disk_file(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _create_project(client, "P")
    payload = {"generated_at": "2026-01-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "",
               "phases": {str(i): {"title": f"Phase {i}", "summary": "MISSING", "confidence": "none",
                                     "evidence_sources": [], "gaps": [], "suggested_topics": [],
                                     "linked_files": [], "linked_file_ids": []} for i in range(1, 8)}}
    resp = client.post("/api/files", json={
        "project": "P", "filename": "insights.json", "content": json.dumps(payload)
    })
    assert resp.status_code == 200
    assert not (tmp_path / "projects" / "P" / "files" / "insights.json").exists()


def test_get_project_returns_insights_json_content_from_db(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _create_project(client, "P")
    payload = {"generated_at": "2026-01-01T00:00:00", "competitors": [], "competitor_landscape_markdown": "",
               "phases": {str(i): {"title": f"Phase {i}", "summary": "A summary" if i == 1 else "MISSING",
                                     "confidence": "none", "evidence_sources": [], "gaps": [],
                                     "suggested_topics": [], "linked_files": [], "linked_file_ids": []}
                          for i in range(1, 8)}}
    client.post("/api/files", json={"project": "P", "filename": "insights.json", "content": json.dumps(payload)})

    resp = client.get("/api/projects/P")
    data = resp.get_json()
    insights = json.loads(data["files"]["insights.json"])
    assert insights["phases"]["1"]["summary"] == "A summary"


def test_other_filenames_still_write_to_disk(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _create_project(client, "P")
    resp = client.post("/api/files", json={"project": "P", "filename": "notes.txt", "content": "hello"})
    assert resp.status_code == 200
    assert (tmp_path / "projects" / "P" / "files" / "notes.txt").read_text(encoding="utf-8") == "hello"
