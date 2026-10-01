import io
import re
from datetime import datetime

from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor, Emu
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml


# ---------------------------------------------------------------------------
# Brand colours
# ---------------------------------------------------------------------------
CLR_PRIMARY_ACCENT = RGBColor(0xFF, 0x8A, 0x00)    # #FF8A00
CLR_PRIMARY_TEXT = RGBColor(0x00, 0x17, 0x38)       # #001738
CLR_SECONDARY_ACCENT = RGBColor(0x82, 0xCB, 0xD4)  # #82CBD4
CLR_SECONDARY_DEEP = RGBColor(0x5B, 0xA8, 0xB5)    # #5BA8B5
CLR_LIGHT_GREY = RGBColor(0xF5, 0xF7, 0xFA)        # #F5F7FA
CLR_MID_GREY = RGBColor(0xE8, 0xEC, 0xF0)          # #E8ECF0
CLR_DARK_GREY = RGBColor(0x6B, 0x7B, 0x8D)         # #6B7B8D
CLR_WHITE = RGBColor(0xFF, 0xFF, 0xFF)

HEX_PRIMARY_ACCENT = "FF8A00"
HEX_PRIMARY_TEXT = "001738"
HEX_SECONDARY_ACCENT = "82CBD4"
HEX_LIGHT_GREY = "F5F7FA"
HEX_MID_GREY = "E8ECF0"
HEX_WHITE = "FFFFFF"


def read_docx(file_storage):
    try:
        doc = Document(file_storage)
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        return "\n".join(full_text)
    except Exception as e:
        return f"[Error reading DOCX: {str(e)}]"


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _set_cell_shading(cell, hex_colour):
    shading = parse_xml(
        f'<w:shd {nsdecls("w")} w:fill="{hex_colour}" w:val="clear"/>'
    )
    cell._tc.get_or_add_tcPr().append(shading)


def _set_cell_margins(cell, top=60, bottom=60, left=100, right=100):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'  <w:top w:w="{top}" w:type="dxa"/>'
        f'  <w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'  <w:start w:w="{left}" w:type="dxa"/>'
        f'  <w:end w:w="{right}" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tcPr.append(tcMar)


def _set_cell_vertical_align(cell, val="top"):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    vAlign = parse_xml(f'<w:vAlign {nsdecls("w")} w:val="{val}"/>')
    tcPr.append(vAlign)


def _set_cell_borders(cell, colour=HEX_MID_GREY, size="4"):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'  <w:top w:val="single" w:sz="{size}" w:space="0" w:color="{colour}"/>'
        f'  <w:start w:val="single" w:sz="{size}" w:space="0" w:color="{colour}"/>'
        f'  <w:bottom w:val="single" w:sz="{size}" w:space="0" w:color="{colour}"/>'
        f'  <w:end w:val="single" w:sz="{size}" w:space="0" w:color="{colour}"/>'
        f'</w:tcBorders>'
    )
    tcPr.append(borders)


def _add_bottom_border(paragraph, colour=HEX_PRIMARY_ACCENT, size="48", space="4"):
    pPr = paragraph._p.get_or_add_pPr()
    borders = parse_xml(
        f'<w:pBorders {nsdecls("w")}>'
        f'  <w:bottom w:val="single" w:sz="{size}" w:space="{space}" w:color="{colour}"/>'
        f'</w:pBorders>'
    )
    pPr.append(borders)


def _add_top_border(paragraph, colour=HEX_SECONDARY_ACCENT, size="6"):
    pPr = paragraph._p.get_or_add_pPr()
    borders = parse_xml(
        f'<w:pBorders {nsdecls("w")}>'
        f'  <w:top w:val="single" w:sz="{size}" w:space="4" w:color="{colour}"/>'
        f'</w:pBorders>'
    )
    pPr.append(borders)


def _run(paragraph, text, size=9, bold=False, italic=False, colour=None, font="Arial"):
    r = paragraph.add_run(text)
    r.font.name = font
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    if colour:
        r.font.color.rgb = colour
    return r


def _para(doc, text="", size=9, bold=False, italic=False, colour=None,
          alignment=None, space_before=0, space_after=0):
    p = doc.add_paragraph()
    if alignment is not None:
        p.alignment = alignment
    fmt = p.paragraph_format
    fmt.space_before = Pt(space_before)
    fmt.space_after = Pt(space_after)
    if text:
        _run(p, text, size=size, bold=bold, italic=italic, colour=colour or CLR_PRIMARY_TEXT)
    return p


def _set_table_full_width(table):
    """Set a table to 100% page width using OOXML (5000 = 100% in pct units)."""
    tbl = table._tbl
    tblPr = tbl.tblPr if tbl.tblPr is not None else parse_xml(f'<w:tblPr {nsdecls("w")}/>')
    tblW = parse_xml(f'<w:tblW {nsdecls("w")} w:type="pct" w:w="5000"/>')
    tblPr.append(tblW)
    if tbl.tblPr is None:
        tbl.insert(0, tblPr)


def _set_row_height(row, height_cm):
    tr = row._tr
    trPr = tr.get_or_add_trPr()
    trHeight = parse_xml(
        f'<w:trHeight {nsdecls("w")} w:val="{int(height_cm * 567)}" w:hRule="atLeast"/>'
    )
    trPr.append(trHeight)


def _merge_cells(table, row, col_start, col_end):
    cell_start = table.cell(row, col_start)
    cell_end = table.cell(row, col_end)
    cell_start.merge(cell_end)
    return cell_start


def _strip_markdown(text):
    """Light-touch strip of common markdown tokens for plain-text rendering."""
    if not text:
        return ""
    s = text
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'__(.+?)__', r'\1', s)
    s = re.sub(r'\*(.+?)\*', r'\1', s)
    s = re.sub(r'_(.+?)_', r'\1', s)
    s = re.sub(r'`([^`]+?)`', r'\1', s)
    s = re.sub(r'^#{1,6}\s+', '', s, flags=re.MULTILINE)
    s = re.sub(r'^\s*[-*+]\s+', '  --> ', s, flags=re.MULTILINE)
    s = re.sub(r'^\s*\d+\.\s+', '  --> ', s, flags=re.MULTILINE)
    s = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', s)
    return s.strip()


def _split_into_paragraphs(text):
    """Split markdown-ish text into paragraph blocks."""
    if not text:
        return [""]
    blocks = re.split(r'\n{2,}', text.strip())
    return [_strip_markdown(b.strip()) for b in blocks if b.strip()]


def _add_rich_runs(paragraph, text, base_size=9, base_colour=None):
    """Add runs to a paragraph, handling **bold** and *italic* inline markdown."""
    base_colour = base_colour or CLR_PRIMARY_TEXT
    # Split on bold and italic patterns, preserving the markers
    parts = re.split(r'(\*\*[^*]+?\*\*|\*[^*]+?\*|__[^_]+?__|_[^_]+?_|`[^`]+?`)', text)
    for part in parts:
        if not part:
            continue
        if part.startswith('**') and part.endswith('**'):
            _run(paragraph, part[2:-2], size=base_size, bold=True, colour=base_colour)
        elif part.startswith('__') and part.endswith('__'):
            _run(paragraph, part[2:-2], size=base_size, bold=True, colour=base_colour)
        elif part.startswith('*') and part.endswith('*'):
            _run(paragraph, part[1:-1], size=base_size, italic=True, colour=base_colour)
        elif part.startswith('_') and part.endswith('_'):
            _run(paragraph, part[1:-1], size=base_size, italic=True, colour=base_colour)
        elif part.startswith('`') and part.endswith('`'):
            _run(paragraph, part[1:-1], size=base_size, colour=CLR_DARK_GREY, font="Consolas")
        else:
            # Strip markdown links: [text](url) -> text
            cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', part)
            _run(paragraph, cleaned, size=base_size, colour=base_colour)


def _split_markdown_table_row(row_text):
    if row_text is None:
        return []
    row = row_text.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    if not row:
        return []
    cells = re.split(r'(?<!\\)\|', row)
    return [c.replace(r"\|", "|").strip() for c in cells]


def _is_markdown_table_separator_line(line):
    cells = _split_markdown_table_row(line)
    if len(cells) < 2:
        return False
    for c in cells:
        token = re.sub(r"\s+", "", c)
        if not re.fullmatch(r":?-+:?", token):
            return False
    return True


def _is_markdown_table_start(lines, idx):
    if idx < 0 or idx + 1 >= len(lines):
        return False
    header_line = (lines[idx] or "").strip()
    sep_line = (lines[idx + 1] or "").strip()
    if "|" not in header_line:
        return False
    header_cells = _split_markdown_table_row(header_line)
    if len(header_cells) < 2:
        return False
    return _is_markdown_table_separator_line(sep_line)


def _markdown_align_from_separator_cell(cell):
    token = re.sub(r"\s+", "", cell or "")
    if token.startswith(":") and token.endswith(":"):
        return WD_ALIGN_PARAGRAPH.CENTER
    if token.endswith(":"):
        return WD_ALIGN_PARAGRAPH.RIGHT
    return WD_ALIGN_PARAGRAPH.LEFT


def _normalize_table_cells(cells, col_count):
    values = list(cells or [])
    if len(values) < col_count:
        values.extend([""] * (col_count - len(values)))
    elif len(values) > col_count:
        values = values[:col_count - 1] + [" | ".join(values[col_count - 1:])]
    return values


def _clean_table_cell_text(text):
    s = (text or "").strip()
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    return s


def _add_inline_markdown_with_breaks(paragraph, text, base_size=8, base_colour=None):
    chunks = re.split(r"(?i)<br\s*/?>", text or "")
    for idx, chunk in enumerate(chunks):
        if idx > 0:
            paragraph.add_run().add_break()
        _add_rich_runs(paragraph, chunk, base_size=base_size, base_colour=base_colour)


def _render_markdown_table_to_docx(doc, lines, start_idx, base_size=8):
    header_cells = _split_markdown_table_row(lines[start_idx])
    sep_cells = _split_markdown_table_row(lines[start_idx + 1])
    col_count = max(len(header_cells), len(sep_cells), 2)
    header_cells = _normalize_table_cells(header_cells, col_count)
    alignments = [
        _markdown_align_from_separator_cell(c) for c in _normalize_table_cells(sep_cells, col_count)
    ]

    rows = []
    i = start_idx + 2
    while i < len(lines):
        line = (lines[i] or "").strip()
        if not line or "|" not in line:
            break
        cells = _split_markdown_table_row(line)
        if len(cells) < 2:
            break
        rows.append(_normalize_table_cells(cells, col_count))
        i += 1

    table = doc.add_table(rows=1 + len(rows), cols=col_count)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    _set_table_full_width(table)

    # Header row
    hdr = table.rows[0]
    for c_idx, label in enumerate(header_cells):
        cell = hdr.cells[c_idx]
        _set_cell_shading(cell, HEX_PRIMARY_TEXT)
        _set_cell_borders(cell)
        _set_cell_margins(cell, top=70, bottom=70, left=100, right=100)
        _set_cell_vertical_align(cell)
        p = cell.paragraphs[0]
        p.text = ""
        p.alignment = alignments[c_idx]
        _add_inline_markdown_with_breaks(
            p,
            _clean_table_cell_text(label),
            base_size=max(base_size, 8),
            base_colour=CLR_WHITE,
        )
        for run in p.runs:
            run.font.bold = True

    # Body rows
    for r_idx, row_values in enumerate(rows, start=1):
        for c_idx, value in enumerate(row_values):
            cell = table.cell(r_idx, c_idx)
            bg = HEX_LIGHT_GREY if (r_idx % 2 == 0) else HEX_WHITE
            _set_cell_shading(cell, bg)
            _set_cell_borders(cell)
            _set_cell_margins(cell, top=60, bottom=60, left=100, right=100)
            _set_cell_vertical_align(cell)
            p = cell.paragraphs[0]
            p.text = ""
            p.alignment = alignments[c_idx]
            _add_inline_markdown_with_breaks(
                p,
                _clean_table_cell_text(value),
                base_size=base_size,
                base_colour=CLR_PRIMARY_TEXT,
            )

    # Add a small spacer after the table for readability.
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(2)
    spacer.paragraph_format.space_after = Pt(4)
    return i


def _render_markdown_to_docx(doc, markdown_text, base_size=9):
    """Render structured markdown into a Word document with proper formatting.

    Handles: ## headings, ### sub-headings, bullet lists, numbered lists,
    bold/italic inline, blockquotes, horizontal rules, and regular paragraphs.
    """
    if not markdown_text:
        return

    lines = markdown_text.strip().split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if re.match(r'^-{3,}$|^\*{3,}$|^_{3,}$', stripped):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(6)
            _add_bottom_border(p, colour=HEX_MID_GREY, size="4", space="2")
            i += 1
            continue

        # Markdown table block
        if _is_markdown_table_start(lines, i):
            i = _render_markdown_table_to_docx(doc, lines, i, base_size=max(8, base_size))
            continue

        # Heading: ## or ###
        h_match = re.match(r'^(#{1,4})\s+(.+)$', stripped)
        if h_match:
            level = len(h_match.group(1))
            heading_text = h_match.group(2).strip()
            # Strip inline markdown for heading text
            heading_clean = re.sub(r'\*\*(.+?)\*\*', r'\1', heading_text)
            heading_clean = re.sub(r'\*(.+?)\*', r'\1', heading_clean)

            if level <= 2:
                # Major section heading
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(16)
                p.paragraph_format.space_after = Pt(4)
                _run(p, heading_clean, size=12, bold=True, colour=CLR_PRIMARY_TEXT)
                _add_bottom_border(p, colour=HEX_SECONDARY_ACCENT, size="6", space="2")
            elif level == 3:
                # Sub-section heading
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(12)
                p.paragraph_format.space_after = Pt(3)
                _run(p, heading_clean, size=10, bold=True, colour=CLR_PRIMARY_TEXT)
            else:
                # Minor heading
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(8)
                p.paragraph_format.space_after = Pt(2)
                _run(p, heading_clean, size=9, bold=True, colour=CLR_PRIMARY_TEXT)
            i += 1
            continue

        # Blockquote
        bq_match = re.match(r'^>\s*(.*)$', stripped)
        if bq_match:
            bq_text = bq_match.group(1)
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(4)
            p.paragraph_format.left_indent = Cm(0.8)
            # Add a left border effect via indentation and italic styling
            _add_rich_runs(p, bq_text, base_size=base_size, base_colour=CLR_DARK_GREY)
            for run in p.runs:
                run.font.italic = True
            i += 1
            continue

        # Bullet list item
        ul_match = re.match(r'^(\s*)[-*+]\s+(.+)$', stripped)
        if ul_match:
            indent_level = len(line) - len(line.lstrip())
            bullet_text = ul_match.group(2)
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            indent = Cm(1.0 + (indent_level // 2) * 0.5)
            p.paragraph_format.left_indent = indent
            _run(p, "\u2022  ", size=base_size, colour=CLR_PRIMARY_ACCENT, bold=True)
            _add_rich_runs(p, bullet_text, base_size=base_size)
            i += 1
            continue

        # Numbered list item
        ol_match = re.match(r'^(\s*)\d+\.\s+(.+)$', stripped)
        if ol_match:
            num_text = ol_match.group(2)
            # Extract the number
            num_match = re.match(r'^(\d+)\.', stripped)
            num = num_match.group(1) if num_match else "1"
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.left_indent = Cm(1.0)
            _run(p, f"{num}.  ", size=base_size, bold=True, colour=CLR_PRIMARY_ACCENT)
            _add_rich_runs(p, num_text, base_size=base_size)
            i += 1
            continue

        # Regular paragraph — collect consecutive non-special lines
        para_lines = [stripped]
        i += 1
        while i < len(lines):
            next_line = lines[i].strip()
            if not next_line:
                break
            if _is_markdown_table_start(lines, i):
                break
            if re.match(r'^#{1,4}\s+', next_line):
                break
            if re.match(r'^\s*[-*+]\s+', next_line):
                break
            if re.match(r'^\s*\d+\.\s+', next_line):
                break
            if re.match(r'^>\s*', next_line):
                break
            if re.match(r'^-{3,}$|^\*{3,}$|^_{3,}$', next_line):
                break
            para_lines.append(next_line)
            i += 1

        para_text = ' '.join(para_lines)
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(4)
        _add_rich_runs(p, para_text, base_size=base_size)


# ---------------------------------------------------------------------------
# Header / Footer
# ---------------------------------------------------------------------------

def _setup_header_footer(section, title="Strategic Insights Report", org="OES"):
    header = section.header
    header.is_linked_to_previous = False
    hp = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    hp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    hp.paragraph_format.space_after = Pt(4)

    r1 = hp.add_run(title)
    r1.font.name = "Arial"
    r1.font.size = Pt(7)
    r1.font.color.rgb = CLR_DARK_GREY

    # Tab stop for right-aligned org text
    tab_run = hp.add_run("\t\t\t\t\t\t\t\t\t\t\t")
    tab_run.font.size = Pt(7)
    r2 = hp.add_run(org)
    r2.font.name = "Arial"
    r2.font.size = Pt(7)
    r2.font.color.rgb = CLR_DARK_GREY

    _add_bottom_border(hp, colour=HEX_MID_GREY, size="4", space="2")

    footer = section.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    fp.paragraph_format.space_before = Pt(4)
    _add_top_border(fp, colour=HEX_MID_GREY, size="4")

    r_page = fp.add_run("Page ")
    r_page.font.name = "Arial"
    r_page.font.size = Pt(7)
    r_page.font.color.rgb = CLR_DARK_GREY

    fld_xml = (
        '<w:fldSimple {} w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>'
    ).format(nsdecls("w"))
    fp._p.append(parse_xml(fld_xml))


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------

def _add_cover_page(doc, title_line1, title_line2, subtitle, author, date_str):
    # Thick orange bar at top
    bar = doc.add_paragraph()
    bar.paragraph_format.space_before = Pt(0)
    bar.paragraph_format.space_after = Pt(0)
    _add_bottom_border(bar, colour=HEX_PRIMARY_ACCENT, size="192", space="0")

    # Vertical whitespace
    for _ in range(8):
        spacer = doc.add_paragraph()
        spacer.paragraph_format.space_before = Pt(0)
        spacer.paragraph_format.space_after = Pt(0)

    # Title line 1
    p1 = doc.add_paragraph()
    p1.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p1.paragraph_format.space_after = Pt(2)
    _run(p1, title_line1, size=28, bold=True, colour=CLR_PRIMARY_TEXT)

    # Title line 2
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p2.paragraph_format.space_before = Pt(0)
    p2.paragraph_format.space_after = Pt(12)
    _run(p2, title_line2, size=28, bold=True, colour=CLR_PRIMARY_ACCENT)

    # Thin separator
    sep = doc.add_paragraph()
    sep.paragraph_format.space_before = Pt(0)
    sep.paragraph_format.space_after = Pt(12)
    _add_top_border(sep, colour=HEX_SECONDARY_ACCENT, size="6")

    # Subtitle
    _para(doc, subtitle, size=12, colour=CLR_DARK_GREY, space_after=8)

    # Author line
    _para(doc, author, size=10, bold=True, colour=CLR_PRIMARY_TEXT, space_after=4)

    # Date / classification
    _para(doc, date_str, size=9, colour=CLR_DARK_GREY, space_after=0)

    doc.add_page_break()


# ---------------------------------------------------------------------------
# Section title
# ---------------------------------------------------------------------------

def _add_section_title(doc, title):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(6)
    _run(p, title, size=16, bold=True, colour=CLR_PRIMARY_TEXT)
    _add_bottom_border(p, colour=HEX_PRIMARY_ACCENT, size="48", space="4")
    return p


# ---------------------------------------------------------------------------
# Competitor table
# ---------------------------------------------------------------------------

def _add_competitor_table(doc, competitors):
    if not competitors:
        _para(doc, "No specific competitor data found in the source material.",
              size=9, italic=True, colour=CLR_DARK_GREY, space_before=8, space_after=8)
        return

    cols = ["Competitor", "Fee", "Duration", "USP", "Details"]
    table = doc.add_table(rows=1, cols=len(cols))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    _set_table_full_width(table)

    # Header row
    hdr = table.rows[0]
    for i, label in enumerate(cols):
        cell = hdr.cells[i]
        _set_cell_shading(cell, HEX_PRIMARY_TEXT)
        _set_cell_borders(cell)
        _set_cell_margins(cell, top=70, bottom=70, left=110, right=110)
        _set_cell_vertical_align(cell)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p, label, size=9, bold=True, colour=CLR_WHITE)

    # Data rows
    for idx, comp in enumerate(competitors):
        row = table.add_row()
        has_details = bool(comp.get("details", "").strip())
        values = [
            comp.get("name", "Unknown"),
            comp.get("price", "MISSING"),
            comp.get("duration", "MISSING"),
            comp.get("usp", "MISSING"),
            "\u2713" if has_details else "\u2014",
        ]
        bg = HEX_LIGHT_GREY if idx % 2 == 1 else HEX_WHITE
        for i, val in enumerate(values):
            cell = row.cells[i]
            _set_cell_shading(cell, bg)
            _set_cell_borders(cell)
            _set_cell_margins(cell, top=60, bottom=60, left=110, right=110)
            _set_cell_vertical_align(cell)
            p = cell.paragraphs[0]
            if i == 4:
                # Details column: centered checkmark or dash
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                clr = CLR_PRIMARY_ACCENT if has_details else CLR_DARK_GREY
                _run(p, val, size=10, bold=has_details, colour=clr)
            else:
                display = _strip_markdown(val) if val and val != "MISSING" else "MISSING"
                clr = CLR_DARK_GREY if display == "MISSING" else CLR_PRIMARY_TEXT
                _run(p, display, size=9, colour=clr, italic=(display == "MISSING"))


# ---------------------------------------------------------------------------
# Detailed competitor profiles
# ---------------------------------------------------------------------------

def _add_competitor_details(doc, competitors):
    """Add a section with detailed profiles for competitors that have details."""
    enriched = [c for c in (competitors or []) if c.get("details", "").strip()]
    if not enriched:
        return

    _add_section_title(doc, "Detailed Competitor Profiles")
    _para(doc,
          "The following profiles contain additional intelligence gathered for select competitors.",
          size=9, colour=CLR_PRIMARY_TEXT, space_before=4, space_after=12)

    for comp in enriched:
        # Competitor name as sub-heading
        p = _para(doc, "", space_before=12, space_after=4)
        _run(p, comp.get("name", "Unknown"), size=12, bold=True, colour=CLR_PRIMARY_TEXT)
        _add_bottom_border(p, colour=HEX_SECONDARY_ACCENT, size="6", space="2")

        # Quick stats line
        stats_parts = []
        if comp.get("price") and comp["price"] != "MISSING":
            stats_parts.append(f"Fee: {_strip_markdown(comp['price'])}")
        if comp.get("duration") and comp["duration"] != "MISSING":
            stats_parts.append(f"Duration: {_strip_markdown(comp['duration'])}")
        if comp.get("usp") and comp["usp"] != "MISSING":
            stats_parts.append(f"USP: {_strip_markdown(comp['usp'])}")
        if stats_parts:
            _para(doc, "  |  ".join(stats_parts), size=8, italic=True,
                  colour=CLR_DARK_GREY, space_before=2, space_after=6)

        # Details body — render structured markdown
        _render_markdown_to_docx(doc, comp["details"])


# ---------------------------------------------------------------------------
# Phase / process cards
# ---------------------------------------------------------------------------

def _add_phase_section(doc, phase_num, title, summary):
    # Phase banner row
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    _set_table_full_width(table)
    cell = table.rows[0].cells[0]
    _set_cell_shading(cell, HEX_MID_GREY)
    _set_cell_margins(cell, top=80, bottom=80, left=120, right=120)
    _set_cell_borders(cell, colour=HEX_MID_GREY)

    p = cell.paragraphs[0]
    _run(p, f"Phase {phase_num}:  ", size=12, bold=True, colour=CLR_PRIMARY_ACCENT)
    _run(p, title, size=12, bold=True, colour=CLR_PRIMARY_TEXT)

    # Content
    if not summary or summary == "MISSING":
        _para(doc,
              "No data available for this phase. Use the Research Prompt button in the dashboard to generate content.",
              size=9, italic=True, colour=CLR_DARK_GREY, space_before=8, space_after=12)
    else:
        _render_markdown_to_docx(doc, summary)

    # Small spacer after phase
    _para(doc, "", space_after=8)


# ---------------------------------------------------------------------------
# Rating visuals (filled/unfilled blocks)
# ---------------------------------------------------------------------------

def _add_rating_visual(paragraph, filled, total=3, label=""):
    if label:
        _run(paragraph, f"{label}  ", size=8, colour=CLR_DARK_GREY)
    for i in range(total):
        clr = CLR_PRIMARY_ACCENT if i < filled else CLR_MID_GREY
        _run(paragraph, "\u25A0 ", size=10, colour=clr)


# ---------------------------------------------------------------------------
# Data completeness summary
# ---------------------------------------------------------------------------

def _confidence_to_level(confidence_str):
    """Map an AI confidence string to a numeric rating (0-3)."""
    return {"high": 3, "medium": 2, "low": 1, "none": 0}.get(
        (confidence_str or "").lower(), 0
    )


def _add_completeness_summary(doc, data):
    _add_section_title(doc, "Data Completeness Overview")
    _para(doc,
          "This section summarises the completeness of the data extracted from the source material. "
          "Dimensions marked as incomplete should be addressed through targeted research.",
          size=9, colour=CLR_PRIMARY_TEXT, space_before=4, space_after=12)

    # Competitor data completeness
    competitors = data.get("competitors", [])
    dimensions = []
    if competitors:
        fields = ["price", "duration", "usp"]
        for f in fields:
            # Use AI confidence if available
            conf_key = f"{f}_confidence"
            has_ai_confidence = any(c.get(conf_key) for c in competitors)
            if has_ai_confidence:
                avg = sum(_confidence_to_level(c.get(conf_key))
                          for c in competitors) / len(competitors)
                level = 3 if avg >= 2.5 else (2 if avg >= 1.5 else (1 if avg > 0 else 0))
            else:
                filled = sum(1 for c in competitors
                             if c.get(f) and c[f] != "MISSING"
                             and "not mentioned" not in c[f].lower())
                total = len(competitors)
                ratio = filled / total if total else 0
                level = 3 if ratio >= 0.8 else (2 if ratio >= 0.4 else (1 if ratio > 0 else 0))
            dimensions.append((f"Competitor {f.title()}", level))
    else:
        dimensions.append(("Competitor Data", 0))

    # Phase completeness
    phases = data.get("phases", {})
    for key in sorted(phases.keys(), key=lambda k: int(k) if k.isdigit() else 0):
        phase = phases[key]
        # Use AI confidence if available, fall back to heuristic
        if phase.get("confidence"):
            level = _confidence_to_level(phase["confidence"])
        else:
            s = phase.get("summary", "")
            has_data = bool(s and s != "MISSING")
            level = 3 if has_data and len(s) > 200 else (2 if has_data else 0)
        dimensions.append((f"Phase {key}: {phase.get('title', 'Unknown')}", level))

    table = doc.add_table(rows=len(dimensions), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_full_width(table)
    for i, (label, level) in enumerate(dimensions):
        bg = HEX_LIGHT_GREY if i % 2 == 1 else HEX_WHITE
        # Label cell
        c0 = table.cell(i, 0)
        _set_cell_shading(c0, bg)
        _set_cell_borders(c0)
        _set_cell_margins(c0, top=50, bottom=50, left=110, right=60)
        p0 = c0.paragraphs[0]
        _run(p0, label, size=8, colour=CLR_DARK_GREY)
        # Rating cell
        c1 = table.cell(i, 1)
        _set_cell_shading(c1, bg)
        _set_cell_borders(c1)
        _set_cell_margins(c1, top=50, bottom=50, left=60, right=110)
        p1 = c1.paragraphs[0]
        _add_rating_visual(p1, level)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_insights_report(data, project_name="Research Project"):
    """Generate a professionally formatted Word document from insights JSON.

    Returns an io.BytesIO buffer containing the .docx file.
    """
    doc = Document()

    # -- Page setup: A4, 2 cm margins --
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2)
    section.right_margin = Cm(2)

    # -- Default font --
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(9)
    style.font.color.rgb = CLR_PRIMARY_TEXT

    generated_at = data.get("generated_at", datetime.now().isoformat())
    try:
        dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now()
    date_display = dt.strftime("%B %Y")

    # ===== COVER PAGE =====
    _add_cover_page(
        doc,
        title_line1="Strategic Insights",
        title_line2=project_name,
        subtitle="Competitive Intelligence and Strategic Analysis Report",
        author="Online Education Services",
        date_str=f"{date_display} | Confidential",
    )

    # -- Header / Footer (applied to body section) --
    body_section = doc.add_section()
    body_section.page_width = Cm(21)
    body_section.page_height = Cm(29.7)
    body_section.top_margin = Cm(2)
    body_section.bottom_margin = Cm(2)
    body_section.left_margin = Cm(2)
    body_section.right_margin = Cm(2)
    _setup_header_footer(body_section, title="Strategic Insights Report", org="OES | Confidential")

    # ===== SECTION 1: COMPETITOR LANDSCAPE =====
    _add_section_title(doc, "Competitor Landscape")
    _para(doc,
          "The following table summarises the competitive landscape extracted from the uploaded source data. "
          "Fields marked MISSING were not explicitly stated in the provided materials.",
          size=9, colour=CLR_PRIMARY_TEXT, space_before=4, space_after=12)
    _add_competitor_table(doc, data.get("competitors", []))

    # Fallback: render competitor_landscape_markdown when no structured competitors
    if not data.get("competitors") and data.get("competitor_landscape_markdown"):
        _render_markdown_to_docx(doc, data["competitor_landscape_markdown"])

    # ===== DETAILED COMPETITOR PROFILES (conditional) =====
    competitors = data.get("competitors", [])
    if any(c.get("details", "").strip() for c in competitors):
        doc.add_page_break()
        _add_competitor_details(doc, competitors)

    # ===== SECTIONS 2-7: STRATEGIC PHASES =====
    phases = data.get("phases", {})
    sorted_keys = sorted(phases.keys(), key=lambda k: int(k) if k.isdigit() else 0)

    confidence_labels = {"high": "High", "medium": "Medium", "low": "Low", "none": "No Data"}
    confidence_colours = {
        "high": RGBColor(0x1E, 0x7E, 0x34),
        "medium": RGBColor(0xCC, 0x6D, 0x00),
        "low": RGBColor(0xC6, 0x28, 0x28),
        "none": CLR_DARK_GREY,
    }

    for key in sorted_keys:
        phase = phases[key]
        title = phase.get("title", f"Phase {key}")
        summary = phase.get("summary", "MISSING")
        confidence = (phase.get("confidence") or "none").lower()
        evidence_sources = phase.get("evidence_sources", [])
        gaps = phase.get("gaps", [])

        doc.add_page_break()
        _add_section_title(doc, f"Phase {key}: {title}")

        # Confidence label
        conf_p = _para(doc, "", space_before=2, space_after=6)
        _run(conf_p, "Confidence: ", size=8, bold=True, colour=CLR_DARK_GREY)
        _run(conf_p, confidence_labels.get(confidence, "No Data"),
             size=8, bold=True, colour=confidence_colours.get(confidence, CLR_DARK_GREY))

        if not summary or summary == "MISSING":
            _para(doc,
                  "No data available for this phase. Use the Research Prompt feature in the dashboard "
                  "to generate targeted content for this section.",
                  size=9, italic=True, colour=CLR_DARK_GREY, space_before=8, space_after=12)
        else:
            _render_markdown_to_docx(doc, summary)

        # Evidence sources
        if evidence_sources:
            src_p = _para(doc, "", space_before=4, space_after=2)
            _run(src_p, "Sources: ", size=8, bold=True, colour=CLR_DARK_GREY)
            _run(src_p, ", ".join(evidence_sources), size=8, italic=True, colour=CLR_DARK_GREY)

        # Data gaps
        if gaps:
            gap_p = _para(doc, "", space_before=4, space_after=2)
            _run(gap_p, "Data Gaps:", size=8, bold=True, colour=RGBColor(0x8A, 0x6D, 0x00))
            for gap in gaps:
                gp = _para(doc, "", space_before=1, space_after=1)
                _run(gp, f"  \u2022  {gap}", size=8, colour=RGBColor(0x6B, 0x59, 0x00))

    # ===== DATA COMPLETENESS =====
    doc.add_page_break()
    _add_completeness_summary(doc, data)

    # ===== SAVE TO BUFFER =====
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
