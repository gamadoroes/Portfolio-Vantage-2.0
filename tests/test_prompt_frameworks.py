import pytest

from services import prompt_frameworks


def test_all_ten_frameworks_present_with_text_and_fields():
    keys = set(prompt_frameworks.FRAMEWORK_LABELS)
    assert len(keys) == 10
    for key in keys:
        framework = prompt_frameworks.get_framework(key)
        assert framework["system_prompt"].strip()
        assert framework["fields"]
        assert framework["label"] == prompt_frameworks.FRAMEWORK_LABELS[key]


def test_every_framework_belongs_to_exactly_one_phase():
    mapped = [k for keys in prompt_frameworks.PHASE_FRAMEWORKS.values() for k in keys]
    assert sorted(mapped) == sorted(prompt_frameworks.FRAMEWORK_LABELS)
    assert len(mapped) == len(set(mapped))


def test_framework_text_was_copied_verbatim():
    assert "Phase 1 — LANDSCAPE deep research prompt" in prompt_frameworks.get_framework("oes-landscape")["system_prompt"]
    assert "WHAT / SO WHAT / NOW WHAT FRAMEWORK" in prompt_frameworks.get_framework("oes-options-whitespace")["system_prompt"]
    assert prompt_frameworks.get_framework("oes-landscape")["fields"][0] == "Market / Program Type"


def test_resolve_framework_defaults_to_the_phase_first_framework():
    assert prompt_frameworks.resolve_framework("3") == "oes-marketing-comparative"
    assert prompt_frameworks.resolve_framework("3", "oes-marketing-website") == "oes-marketing-website"
    assert prompt_frameworks.resolve_framework("3", "oes-landscape") == "oes-marketing-comparative"
    assert prompt_frameworks.get_framework("oes-academic-unitdive")["phase_key"] == "5"


def test_unknown_phase_or_framework_raises():
    with pytest.raises(ValueError):
        prompt_frameworks.resolve_framework("9")
    with pytest.raises(ValueError):
        prompt_frameworks.get_framework("deep-research")
