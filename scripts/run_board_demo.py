# scripts/run_board_demo.py
"""Dev only: run the real app on http://127.0.0.1:5055 against a throwaway database and projects
folder, with Anthropic and OpenAI replaced by fakes, to exercise the Research tab end to end.

    python scripts/run_board_demo.py

Nothing touches your real database, projects or API accounts. Stop with Ctrl+C.
"""
import itertools
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = Path(tempfile.mkdtemp(prefix="board-demo-"))
os.environ["DATABASE_PATH"] = str(WORK / "demo.db")
os.environ["BACKUP_DIR"] = str(WORK / "backups")
os.environ.setdefault("ANTHROPIC_API_KEY", "demo")
os.environ.setdefault("OPENAI_API_KEY", "demo")
os.chdir(WORK)

from app import create_app  # noqa: E402
from services import llm_service, research_execution_service, supervisor_service  # noqa: E402

DRAFTED = ("ROLE: Higher-education market analyst working for OES.\nQUESTION: {title}\n"
           "COVER: providers, pricing, delivery, accreditation.\nOUTPUT: a comparison table and five insights. Cite every claim.")


def fake_completion(system_prompt, user_message, max_tokens=4000):
    if "RESEARCH TITLE:" in user_message:
        title = user_message.split("RESEARCH TITLE:", 1)[1].splitlines()[0].strip()
        return DRAFTED.format(title=title)
    return "## Findings\n\n- A finding from your files.\n- Another finding.\n"


class Block:
    def __init__(self, name, payload):
        self.type, self.name, self.input = "tool_use", name, payload


review_outcomes = itertools.cycle(["COMPLETE", "FOLLOW_UP_REQUIRED", "NEEDS_HUMAN"])


class FakeAnthropic:
    def __init__(self, api_key=None):
        self.messages = self

    def create(self, **kwargs):
        names = {t["name"] for t in kwargs["tools"]}
        if "propose_tasks" in names:
            return SimpleNamespace(content=[Block("propose_tasks", {"reason": "Phases with no research yet", "tasks": [
                {"phase_key": "1", "title": "Overview of online Psychology postgraduate programs", "research_method": "TARGETED_WEB",
                 "focus": ["Programs", "Providers"], "rationale": "Phase 1 has no findings yet."},
                {"phase_key": "2", "title": "Who enrols in online Psychology postgraduate study", "research_method": "TARGETED_WEB",
                 "focus": ["Career changers"], "rationale": "Phase 2 explains who enrols and why."},
                {"phase_key": "5", "title": "Curriculum structure from uploaded handbooks", "research_method": "FILE_ANALYSIS",
                 "focus": ["Units"], "rationale": "Curriculum detail is usually in your files."},
            ]})])
        outcome = next(review_outcomes)
        payload = {"completeness_score": 0.62, "evidence_score": 0.55, "identified_gaps": ["Intake dates not on an official page."],
                   "outcome": outcome, "reason": "Demo review."}
        if outcome == "FOLLOW_UP_REQUIRED":
            payload["followup"] = {"title": "Verify intake dates on official pages", "focus": ["Intakes"],
                                   "research_method": "TARGETED_WEB", "rationale": "Intake dates had no official source."}
        return SimpleNamespace(content=[Block("review_outcome", payload)])


started = {}


def fake_start(prompt):
    response_id = f"resp_{len(started) + 1}"
    started[response_id] = time.time()
    return SimpleNamespace(id=response_id, status="queued")


def fake_retrieve(response_id):
    if time.time() - started.get(response_id, 0) < 20:
        return SimpleNamespace(id=response_id, status="in_progress", output=[], output_text=None, error=None, last_error=None)
    text = "# Demo report\n\nProviders offer graduate certificates and masters.\n\nSee the provider page."
    annotation = SimpleNamespace(type="url_citation", start_index=0, end_index=0, title="Provider page", url="https://example.edu/psych")
    content = [SimpleNamespace(type="output_text", text=text, annotations=[annotation])]
    return SimpleNamespace(id=response_id, status="completed", output_text=text,
                           output=[SimpleNamespace(type="message", content=content)], error=None, last_error=None)


llm_service.prompt_completion = fake_completion
supervisor_service.anthropic.Anthropic = FakeAnthropic
research_execution_service.openai_service.start_deep_research = fake_start
research_execution_service.openai_service.retrieve_deep_research = fake_retrieve

if __name__ == "__main__":
    print(f"Board demo running from {WORK}")
    create_app().run(host="127.0.0.1", port=5055, debug=False)
