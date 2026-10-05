"""Small text helpers shared by the Supervisor, the board and the tools."""


def clip_text(text, limit):
    text = text if isinstance(text, str) else str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def as_text_list(value):
    """A list of non-empty strings from a field that should be a list.

    The tool schemas ask for arrays, but the live model sometimes sends one string instead,
    often a bulleted block ("\\n- first\\n- second"). Split that into its lines.
    """
    if value is None:
        return []
    if isinstance(value, str):
        lines = (line.strip().lstrip("-*•").strip() for line in value.splitlines())
        return [line for line in lines if line]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]
