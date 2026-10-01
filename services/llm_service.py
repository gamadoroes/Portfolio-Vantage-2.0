import anthropic
from flask import current_app


def _sanitize_messages(messages):
    """Strip non-API fields (e.g. artifact_id) before sending to Anthropic."""
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def stream_chat_completion(system_blocks, messages, max_tokens=40000, temperature=0.2):
    client = anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])
    with client.messages.stream(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_blocks,
        messages=_sanitize_messages(messages),
    ) as stream:
        for text in stream.text_stream:
            yield text


def edit_completion(edit_system, messages, max_tokens=4000):
    client = anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])
    return client.messages.create(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=max_tokens,
        system=edit_system,
        messages=_sanitize_messages(messages),
    )


def prompt_completion(system_prompt, user_message, max_tokens=4000):
    client = anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=current_app.config["ANTHROPIC_MODEL"],
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text
