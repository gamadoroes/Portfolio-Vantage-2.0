"""Parsing of OpenAI deep-research responses: text, inline citations and source lists.

Pure functions with no Flask dependency, so both the routes and the supervisor can use them.
"""
import re
from html import escape as html_escape


def _response_attr(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _register_deep_research_citation(annotation, citation_numbers, citation_order):
    ann_type = (_response_attr(annotation, "type", "") or "").strip()

    if ann_type == "url_citation":
        url = (_response_attr(annotation, "url", "") or "").strip()
        if not url:
            return None
        key = ("url", url)
        entry = {
            "type": "url",
            "title": (_response_attr(annotation, "title", "") or url).strip(),
            "url": url,
        }
    elif ann_type in ("file_citation", "container_file_citation", "file_path"):
        file_id = (_response_attr(annotation, "file_id", "") or "").strip()
        filename = (_response_attr(annotation, "filename", "") or file_id or "File").strip()
        key = ("file", file_id or filename)
        entry = {
            "type": "file",
            "filename": filename,
        }
    else:
        return None

    existing = citation_numbers.get(key)
    if existing:
        return existing

    index = len(citation_order) + 1
    citation_numbers[key] = index
    entry["index"] = index
    citation_order.append(entry)
    return index


def _deep_research_inline_citation(index, annotation):
    url = (_response_attr(annotation, "url", "") or "").strip()
    label = f"[{index}]"
    if not url:
        return f"<sup>{label}</sup>"
    return (
        f'<sup><a href="{html_escape(url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer">{label}</a></sup>'
    )


def _format_deep_research_text_block(text, annotations, citation_numbers, citation_order):
    source_text = text or ""
    if not source_text:
        return ""

    ranged_annotations = []
    for annotation in annotations or []:
        start = _response_attr(annotation, "start_index", None)
        end = _response_attr(annotation, "end_index", None)
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(source_text):
            ranged_annotations.append((start, end, annotation))
        else:
            _register_deep_research_citation(annotation, citation_numbers, citation_order)

    output = []
    cursor = 0
    marker_hint = re.compile(r"(source|citation|reference|[【〖†])", re.IGNORECASE)

    for start, end, annotation in sorted(ranged_annotations, key=lambda item: (item[0], item[1])):
        if start < cursor:
            continue

        output.append(source_text[cursor:start])
        span = source_text[start:end]
        citation_index = _register_deep_research_citation(annotation, citation_numbers, citation_order)

        if citation_index is None:
            output.append(span)
        elif span.strip() and len(span.strip()) <= 96 and marker_hint.search(span):
            output.append(_deep_research_inline_citation(citation_index, annotation))
        elif span.strip():
            output.append(span)
            output.append(_deep_research_inline_citation(citation_index, annotation))
        else:
            output.append(_deep_research_inline_citation(citation_index, annotation))

        cursor = end

    output.append(source_text[cursor:])
    return "".join(output)


def _append_deep_research_sources(markdown_text, citation_order):
    if not citation_order:
        return markdown_text

    text = markdown_text or ""
    if re.search(r"(?im)^\s{0,3}#{1,6}\s+sources\b", text) or re.search(
        r"(?im)^\s*\*\*sources?\*\*\s*:", text
    ):
        return text

    lines = ["", "", "## Sources", ""]
    for entry in citation_order:
        index = entry["index"]
        if entry["type"] == "url":
            title = html_escape(entry.get("title") or entry.get("url") or f"Source {index}")
            url = html_escape(entry["url"], quote=True)
            lines.append(
                f'{index}. <a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>'
            )
        else:
            filename = html_escape(entry.get("filename") or f"File {index}")
            lines.append(f"{index}. {filename}")

    if not text.strip():
        return "\n".join(lines).strip() + "\n"
    return text.rstrip() + "\n" + "\n".join(lines).rstrip() + "\n"


def extract_deep_research_output(response):
    output_text = _response_attr(response, "output_text", None)
    output_parts = []
    formatted_parts = []
    citation_numbers = {}
    citation_order = []

    try:
        for item in _response_attr(response, "output", []) or []:
            if _response_attr(item, "type", "") != "message":
                continue

            for content in _response_attr(item, "content", []) or []:
                c_type = _response_attr(content, "type", "")
                if c_type not in ("output_text", "text"):
                    continue

                text = _response_attr(content, "text", "") or ""
                if not text:
                    continue

                output_parts.append(text)
                annotations = _response_attr(content, "annotations", []) or []
                formatted_parts.append(
                    _format_deep_research_text_block(
                        text,
                        annotations,
                        citation_numbers,
                        citation_order,
                    )
                )
    except Exception as ex:
        print(f"[deep-research] output extraction error for {getattr(response, 'id', '?')}: {ex}")

    plain_text = output_text or "\n".join([p for p in output_parts if p]).strip() or None
    markdown_text = "\n".join([p for p in formatted_parts if p]).strip() or plain_text
    markdown_text = _append_deep_research_sources(markdown_text, citation_order)

    citations = [
        {
            "index": entry["index"],
            "type": entry["type"],
            "title": entry.get("title"),
            "url": entry.get("url"),
            "filename": entry.get("filename"),
        }
        for entry in citation_order
    ]

    return plain_text, markdown_text, citations
