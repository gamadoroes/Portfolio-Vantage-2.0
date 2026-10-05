# services/prompt_frameworks.py
"""The OES phase research frameworks, moved server-side from static/app.js (PROMPT_TEMPLATES).

prompt_frameworks.json holds each framework's system prompt and form-field labels exactly as
they were in app.js. It was generated mechanically, so do not hand-edit the prompt text.
"""
import json
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("prompt_frameworks.json").read_text(encoding="utf-8"))

# Phase key -> its frameworks, in display order. The first is the phase's default.
PHASE_FRAMEWORKS = {
    "1": ["oes-landscape"],
    "2": ["oes-student-persona"],
    "3": ["oes-marketing-comparative", "oes-marketing-website", "oes-marketing-sentiment"],
    "4": ["oes-product-features"],
    "5": ["oes-academic-structure", "oes-academic-unitdive"],
    "6": ["oes-industry-engagement"],
    "7": ["oes-options-whitespace"],
}

FRAMEWORK_LABELS = {
    "oes-landscape": "Phase 1: The Landscape",
    "oes-student-persona": "Phase 2: The Student",
    "oes-marketing-comparative": "3a: Comparative Marketing Analysis",
    "oes-marketing-website": "3b: Website Review",
    "oes-marketing-sentiment": "3c: Sentiment Analysis & Social Listening",
    "oes-product-features": "Phase 4: Product Features",
    "oes-academic-structure": "5a: Course Structure & Academic Differentiators",
    "oes-academic-unitdive": "5b: Unit-by-Unit Deep Dive",
    "oes-industry-engagement": "Phase 6: Industry Engagement",
    "oes-options-whitespace": "Phase 7: Options for OES",
}

_PHASE_OF = {key: phase for phase, keys in PHASE_FRAMEWORKS.items() for key in keys}


def frameworks_for_phase(phase_key):
    return list(PHASE_FRAMEWORKS.get(str(phase_key), []))


def resolve_framework(phase_key, framework_key=None):
    options = frameworks_for_phase(phase_key)
    if not options:
        raise ValueError(f"Unknown phase: {phase_key!r}")
    return framework_key if framework_key in options else options[0]


def get_framework(framework_key):
    if framework_key not in _DATA or framework_key not in FRAMEWORK_LABELS:
        raise ValueError(f"Unknown framework: {framework_key!r}")
    return {
        "key": framework_key,
        "label": FRAMEWORK_LABELS[framework_key],
        "phase_key": _PHASE_OF[framework_key],
        "fields": list(_DATA[framework_key]["fields"]),
        "system_prompt": _DATA[framework_key]["system_prompt"],
    }
