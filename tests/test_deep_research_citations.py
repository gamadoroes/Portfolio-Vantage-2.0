from types import SimpleNamespace

from routes.ai import _extract_deep_research_output


def _url_annotation(text, marker, title, url):
    start = text.index(marker)
    end = start + len(marker)
    return SimpleNamespace(
        type="url_citation",
        start_index=start,
        end_index=end,
        title=title,
        url=url,
    )


def test_extract_deep_research_output_formats_inline_citations_and_sources():
    text = "Claim one and claim two."
    response = SimpleNamespace(
        id="resp_1",
        output_text=text,
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text=text,
                        annotations=[
                            _url_annotation(
                                text,
                                "",
                                "Source One",
                                "https://example.com/source-1",
                            ),
                            _url_annotation(
                                text,
                                "",
                                "Source Two",
                                "https://example.com/source-2",
                            ),
                        ],
                    )
                ],
            )
        ],
    )

    plain_text, markdown_text, citations = _extract_deep_research_output(response)

    assert plain_text == text
    assert '<sup><a href="https://example.com/source-1"' in markdown_text
    assert '<sup><a href="https://example.com/source-2"' in markdown_text
    assert "## Sources" in markdown_text
    assert '1. <a href="https://example.com/source-1"' in markdown_text
    assert '2. <a href="https://example.com/source-2"' in markdown_text
    assert citations == [
        {
            "index": 1,
            "type": "url",
            "title": "Source One",
            "url": "https://example.com/source-1",
            "filename": None,
        },
        {
            "index": 2,
            "type": "url",
            "title": "Source Two",
            "url": "https://example.com/source-2",
            "filename": None,
        },
    ]


def test_extract_deep_research_output_reuses_duplicate_url_citations():
    text = "Repeated point and follow-up."
    shared_url = "https://example.com/shared"
    response = SimpleNamespace(
        id="resp_2",
        output_text=text,
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text=text,
                        annotations=[
                            _url_annotation(text, "", "Shared Source", shared_url),
                            _url_annotation(text, "", "Shared Source", shared_url),
                        ],
                    )
                ],
            )
        ],
    )

    _, markdown_text, citations = _extract_deep_research_output(response)

    assert len(citations) == 1
    assert citations[0]["index"] == 1
    assert markdown_text.count(">[1]</a></sup>") == 2
