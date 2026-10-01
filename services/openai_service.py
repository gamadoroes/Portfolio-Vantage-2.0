from openai import OpenAI
from flask import current_app


def _client():
    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=api_key)


def start_deep_research(prompt):
    model = current_app.config.get("OPENAI_DEEP_RESEARCH_MODEL", "o4-mini-deep-research")
    response = _client().responses.create(
        model=model,
        input=prompt,
        background=True,
        store=True,
        tools=[{"type": "web_search_preview"}],
    )
    return response


def retrieve_deep_research(response_id):
    return _client().responses.retrieve(response_id)
