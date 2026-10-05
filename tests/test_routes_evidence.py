import re
from pathlib import Path

import pytest

from app import create_app
from db.connection import set_database_path
from db.migrate_runner import apply_migrations
from db.repositories import projects_repo
from services import research_task_service, tools


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_database_path(str(tmp_path / "test.db"))
    apply_migrations()
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            c.post("/api/projects", json={"name": "P"})
            yield c
    set_database_path(None)


def _seed(project="P", facts=None):
    pid = projects_repo.get_or_create_id(project)
    card = research_task_service.create_task(pid, "4", "Fees", research_method="TARGETED_WEB")
    facts = facts or [
        {"claim": "Deakin charges $3,000 per unit", "source_url": "https://deakin.example/fees",
         "source_title": "Deakin fees", "as_of": "2026"},
        {"claim": "Monash runs three intakes", "quote": "three intakes a year"},
    ]
    data = tools.run_tool("record_research_facts", "supervisor", project, {"task_id": card, "facts": facts,
                          "conclusions": [{"text": "Fees and intakes differ", "fact_numbers": [1, 2]}]}).data
    return card, data


def test_list_groups_facts_and_conclusions_by_phase(client):
    card, data = _seed()
    body = client.get("/api/evidence?project=P").get_json()
    assert body["success"] is True and set(body["phases"]) == {"4"}
    phase = body["phases"]["4"]
    assert [f["claim"] for f in phase["facts"]] == ["Deakin charges $3,000 per unit", "Monash runs three intakes"]
    assert phase["facts"][0]["source"] == {"url": "https://deakin.example/fees", "title": "Deakin fees",
                                           "publisher": "", "published_date": ""}
    assert (phase["facts"][0]["card_title"], phase["facts"][0]["as_of"]) == ("Fees", "2026")
    assert phase["facts"][1]["source"] is None and phase["facts"][1]["quote"] == "three intakes a year"
    (conclusion,) = phase["conclusions"]
    assert conclusion["text"] == "Fees and intakes differ"
    assert conclusion["fact_ids"] == data["fact_ids"] and conclusion["rejected_fact_count"] == 0


def test_list_for_one_phase(client):
    _seed()
    assert set(client.get("/api/evidence?project=P&phase=4").get_json()["phases"]) == {"4"}
    assert client.get("/api/evidence?project=P&phase=2").get_json()["phases"] == {}
    assert client.get("/api/evidence?project=P&phase=9").status_code == 400


def test_a_project_with_no_facts_lists_nothing(client):
    assert client.get("/api/evidence?project=P").get_json()["phases"] == {}


def test_search(client):
    _seed()
    body = client.get("/api/evidence/search?project=P&q=deakin").get_json()
    assert [f["claim"] for f in body["facts"]] == ["Deakin charges $3,000 per unit"]
    assert client.get("/api/evidence/search?project=P&q=" + "x" * 500).status_code == 200
    assert client.get("/api/evidence/search?project=P&q=deakin&phase=2").get_json()["facts"] == []
    assert client.get("/api/evidence/search?project=P&q=deakin&phase=9").status_code == 400


def test_reject_and_restore_a_fact(client):
    _, data = _seed()
    fact_id = data["fact_ids"][0]
    resp = client.post(f"/api/evidence/fact/{fact_id}/reject", json={"project": "P"})
    assert resp.status_code == 200 and resp.get_json() == {"success": True, "id": fact_id, "status": "rejected"}
    phase = client.get("/api/evidence?project=P").get_json()["phases"]["4"]
    assert phase["facts"][0]["status"] == "rejected"
    assert phase["conclusions"][0]["rejected_fact_count"] == 1
    client.post(f"/api/evidence/fact/{fact_id}/restore", json={"project": "P"})
    assert client.get("/api/evidence?project=P").get_json()["phases"]["4"]["facts"][0]["status"] == "active"


def test_reject_a_conclusion(client):
    _, data = _seed()
    conclusion_id = data["conclusion_ids"][0]
    assert client.post(f"/api/evidence/conclusion/{conclusion_id}/reject", json={"project": "P"}).get_json()["status"] == "rejected"


@pytest.mark.parametrize("url", ["/api/evidence/table/1/reject", "/api/evidence/fact/1/delete", "/api/evidence/fact/99999/reject"])
def test_unknown_things_are_404(client, url):
    _seed()
    assert client.post(url, json={"project": "P"}).status_code == 404


OTHER_FACTS = [{"claim": "Torrens charges $2,500 per unit"}, {"claim": "Federation runs two intakes"}]


def _other_project(client):
    """Project Q with its own facts and conclusion, and project P with its own."""
    client.post("/api/projects", json={"name": "Q"})
    _, mine = _seed("P")
    _, theirs = _seed("Q", OTHER_FACTS)
    return mine, theirs


def _q_rows(client):
    phase = client.get("/api/evidence?project=Q").get_json()["phases"]["4"]
    return {f["id"]: f["status"] for f in phase["facts"]}, {c["id"]: c["status"] for c in phase["conclusions"]}


@pytest.mark.parametrize("action", ["reject", "restore"])
def test_a_fact_or_conclusion_of_another_project_cannot_be_changed(client, action):
    _, theirs = _other_project(client)
    facts_before, conclusions_before = _q_rows(client)
    for kind, item in (("fact", theirs["fact_ids"][0]), ("conclusion", theirs["conclusion_ids"][0])):
        resp = client.post(f"/api/evidence/{kind}/{item}/{action}", json={"project": "P"})
        assert resp.status_code == 404 and resp.get_json()["success"] is False
    assert _q_rows(client) == (facts_before, conclusions_before)
    assert set(facts_before.values()) == {"active"}


def test_the_other_project_changes_only_through_its_own_name(client):
    _, theirs = _other_project(client)
    fact_id = theirs["fact_ids"][0]
    assert client.post(f"/api/evidence/fact/{fact_id}/reject", json={"project": "Q"}).status_code == 200
    assert _q_rows(client)[0][fact_id] == "rejected"


def test_list_and_search_never_include_another_projects_facts(client):
    mine, theirs = _other_project(client)
    listed = client.get("/api/evidence?project=P").get_json()["phases"]["4"]
    assert [f["id"] for f in listed["facts"]] == mine["fact_ids"]
    assert [c["id"] for c in listed["conclusions"]] == mine["conclusion_ids"]
    assert not set(theirs["fact_ids"]) & {f["id"] for f in listed["facts"]}
    assert client.get("/api/evidence/search?project=P&q=torrens").get_json()["facts"] == []
    assert client.get("/api/evidence/search?project=P&q=intakes").get_json()["facts"][0]["claim"] == "Monash runs three intakes"
    found = client.get("/api/evidence/search?project=Q&q=intakes").get_json()["facts"]
    assert [f["claim"] for f in found] == ["Federation runs two intakes"]


def test_no_project_is_400(client):
    responses = [
        client.get("/api/evidence?project=Nope"),
        client.get("/api/evidence/search?project=Nope&q=x"),
        client.post("/api/evidence/fact/1/reject", json={"project": "Nope"}),
    ]
    for response in responses:
        assert response.status_code == 400
        assert response.get_json() == {"success": False, "error": "No project selected."}


def test_the_page_loads_the_evidence_script_and_search_box(client):
    html = client.get("/").get_data(as_text=True)
    assert 'src="/static/evidence.js' in html and 'id="evidence-search-input"' in html


def test_facts_are_not_drawn_under_a_past_insights_version():
    # app.js cannot run under Node, so this checks the gate in the source: the slot that evidence.js fills
    # is only written when the tab is not showing a frozen history entry (facts are not versioned).
    source = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    assert re.search(r"\$\{viewingHistoricalVersion \? '' : `<div class=\"ev-slot\" data-evidence-phase=", source)
