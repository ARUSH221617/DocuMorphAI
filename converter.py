"""
Markdown to DOCX Converter with:
- Word OMML Math equations (LaTeX -> MathML -> OMML / Alt+=)
- Direct SVG vector insertion (asvg:svgBlip with fallback for 'Convert to Shape' in Word)
- Full handling of HTML tags (<br>, <u>, <b>, <span>, <div>, nested <table>) and XML code fences (```xml, ```svg)
- Markdown tables with multiline cells, nested tables, cell formatting, borders, and RTL support
- RTL (Persian/Arabic) and LTR detection and typography
"""

import html
import os
import re
import time
import uuid
import zipfile

import docx
import latex2mathml.converter
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.shared import Inches, Pt
from lxml import etree

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_XSLT = os.path.join(CURRENT_DIR, "assets", "MML2OMML.XSL")
OFFICE_XSLT = r"C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL"

XSLT_PATH = BUNDLED_XSLT if os.path.exists(BUNDLED_XSLT) else OFFICE_XSLT
_XSLT_TRANSFORM = None


def get_xslt():
    global _XSLT_TRANSFORM
    if _XSLT_TRANSFORM is None:
        if os.path.exists(XSLT_PATH):
            _XSLT_TRANSFORM = etree.XSLT(etree.parse(XSLT_PATH))
    return _XSLT_TRANSFORM


def is_persian_or_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]", text))


def set_rtl_paragraph(p):
    pPr = p._element.get_or_add_pPr()
    pPr.append(parse_xml(r'<w:bidi xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'))


def set_rtl_table(table):
    tblPr = table._tbl.tblPr
    tblPr.append(parse_xml(r'<w:bidiVisual xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'))


def latex_to_omml(latex_code: str):
    xslt = get_xslt()
    if not xslt:
        return None
    try:
        code = latex_code.strip()
        code = re.sub(r"\\text\{\\rlap\{\$\\checkmark\$\}\}\\square", "☑", code)
        code = re.sub(r"\\square", "☐", code)
        code = re.sub(r"\\checkmark", "✓", code)
        code = re.sub(r"\\color\{[a-zA-Z]+\}\{(.*?)\}", r"\1", code)

        # Transform spacing commands into non-breaking spaces so Word OMML renders spacing correctly
        code = re.sub(r"\\qquad(?![a-zA-Z])", r"\\text{    }", code)
        code = re.sub(r"\\quad(?![a-zA-Z])", r"\\text{  }", code)
        code = re.sub(r"\\enspace(?![a-zA-Z])", r"\\text{  }", code)
        code = re.sub(r"\\(?:thickspace|medspace)(?![a-zA-Z])", r"\\text{ }", code)
        code = re.sub(r"\\thinspace(?![a-zA-Z])", r"\\text{ }", code)

        mml = latex2mathml.converter.convert(code)
        clean_mml = re.sub(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)", "&amp;", mml)
        mml_root = etree.fromstring(clean_mml.encode("utf-8"))
        omml_elem = xslt(mml_root).getroot()
        return omml_elem
    except Exception:
        return None


def strip_visual_descriptions(md_text: str) -> str:
    """
    Remove any trailing Visual Descriptions / Visuals Explanation section
    added by the AI (e.g. ## Visual Descriptions, ### Visuals Explanation, etc.)
    """
    pattern = re.compile(
        r"\n+(?:#{1,4}\s*(?:Visual(?:\s+Descriptions?|\s+Explanations?|s?\s+Details?)|توضیحات\s*(?:تصاویر|اشکال|شکل‌ها))|<visual_recreation_instructions>)[\s\S]*$",
        re.IGNORECASE,
    )
    return pattern.sub("", md_text).strip()


def sanitize_svg_dimensions(svg_content: str) -> str:
    """Ensure SVG XML has explicit width, height, viewBox, and required namespace attributes."""
    svg = svg_content.strip()
    m = re.search(r"<svg[\s\S]*?</svg>", svg, re.IGNORECASE)
    if m:
        svg = m.group(0)

    # Ensure root xmlns
    if 'xmlns="http://www.w3.org/2000/svg"' not in svg and "xmlns='http://www.w3.org/2000/svg'" not in svg:
        svg = re.sub(r"<svg\b", '<svg xmlns="http://www.w3.org/2000/svg"', svg, count=1, flags=re.I)

    # Ensure width and height from viewBox if missing
    root_tag_match = re.search(r"<svg\b([^>]*)>", svg, re.I)
    if root_tag_match:
        attrs = root_tag_match.group(1)
        has_width = bool(re.search(r"\bwidth\s*=", attrs, re.I))
        has_height = bool(re.search(r"\bheight\s*=", attrs, re.I))
        vb_match = re.search(
            r"\bviewBox\s*=\s*[\"']\s*([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s*[\"']", attrs, re.I
        )

        if not has_width and vb_match:
            w_val = vb_match.group(3)
            svg = re.sub(r"<svg\b", f'<svg width="{w_val}"', svg, count=1, flags=re.I)
        if not has_height and vb_match:
            h_val = vb_match.group(4)
            svg = re.sub(r"<svg\b", f'<svg height="{h_val}"', svg, count=1, flags=re.I)

    return svg


def compute_svg_emus(svg_clean: str, target_width_inches: float = 3.8) -> tuple[int, int]:
    """Compute exact cx and cy in EMUs preserving SVG aspect ratio."""
    w_match = re.search(r'\bwidth\s*=\s*["\']\s*([0-9.]+)', svg_clean, re.I)
    h_match = re.search(r'\bheight\s*=\s*["\']\s*([0-9.]+)', svg_clean, re.I)
    vb_match = re.search(
        r'\bviewBox\s*=\s*["\']\s*([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)', svg_clean, re.I
    )

    w, h = None, None
    if w_match and h_match:
        try:
            w, h = float(w_match.group(1)), float(h_match.group(1))
        except Exception:
            pass
    if (not w or not h) and vb_match:
        try:
            w, h = float(vb_match.group(3)), float(vb_match.group(4))
        except Exception:
            pass

    if not w or not h or w <= 0 or h <= 0:
        w, h = 300.0, 200.0

    aspect = h / w
    cx = int(target_width_inches * 914400)
    cy = int(cx * aspect)
    return cx, cy


def add_svg_to_doc(doc, svg_content: str, width=Inches(3.8), target_paragraph=None):
    """
    Directly insert SVG as pure vector media into Word document (asvg:svgBlip).
    Does NOT create, insert, or reference any PNG raster file.
    Enables Word's native 'Graphic Format' ribbon and 'Convert to Shape' / 'Convert to Object' context menu.
    """
    p = target_paragraph if target_paragraph is not None else doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    svg_clean = sanitize_svg_dimensions(svg_content)
    svg_bytes = svg_clean.encode("utf-8")

    # 1. Create pure SVG media part directly in document package
    svg_id = uuid.uuid4().hex[:8]
    part_name = f"/word/media/image_{svg_id}.svg"
    svg_part = docx.opc.part.Part(docx.opc.packuri.PackURI(part_name), "image/svg+xml", svg_bytes)
    svg_rId = doc.part.relate_to(svg_part, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image")

    # 2. Compute exact extent in EMUs
    width_inches = float(width) / 914400.0 if isinstance(width, int) else 3.8
    cx, cy = compute_svg_emus(svg_clean, width_inches)

    doc_pr_id = int(time.time() * 1000) % 1000000

    # 3. Construct DrawingML with asvg:svgBlip pointing directly to the SVG media part
    inline_xml = f"""
    <wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
               xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
               xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"
               xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
               xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main"
               distT="0" distB="0" distL="0" distR="0">
        <wp:extent cx="{cx}" cy="{cy}"/>
        <wp:docPr id="{doc_pr_id}" name="Vector Graphic {svg_id}" descr="SVG Vector Shape"/>
        <wp:cNvGraphicFramePr>
            <a:graphicFrameLocks noChangeAspect="1"/>
        </wp:cNvGraphicFramePr>
        <a:graphic>
            <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
                <pic:pic>
                    <pic:nvPicPr>
                        <pic:cNvPr id="0" name="image_{svg_id}.svg" descr="vector shape graphic"/>
                        <pic:cNvPicPr/>
                    </pic:nvPicPr>
                    <pic:blipFill>
                        <a:blip r:embed="{svg_rId}">
                            <a:extLst>
                                <a:ext uri="{{96DAC542-00C1-44C9-A059-E6573EC2B148}}">
                                    <asvg:svgBlip r:embed="{svg_rId}"/>
                                </a:ext>
                            </a:extLst>
                        </a:blip>
                        <a:stretch>
                            <a:fillRect/>
                        </a:stretch>
                    </pic:blipFill>
                    <pic:spPr>
                        <a:xfrm>
                            <a:off x="0" y="0"/>
                            <a:ext cx="{cx}" cy="{cy}"/>
                        </a:xfrm>
                        <a:prstGeom prst="rect">
                            <a:avLst/>
                        </a:prstGeom>
                    </pic:spPr>
                </pic:pic>
            </a:graphicData>
        </a:graphic>
    </wp:inline>
    """
    run = p.add_run()
    drawing = parse_xml(
        f'<w:drawing xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">{inline_xml}</w:drawing>'
    )
    run._r.append(drawing)
    return p


def patch_docx_for_svg_shapes(docx_path: str):
    """
    Post-process saved .docx package to ensure Microsoft Word fully enables:
    1. Modern Word 2016+ mode (compatibilityMode 16) instead of Word 2010 compatibility mode.
       (Crucial: Word 2010 compatibilityMode=14 disables 'Convert to Shape'!)
    2. Default SVG content type in [Content_Types].xml (<Default Extension="svg" ContentType="image/svg+xml"/>).
    3. asvg and a16 DrawingML namespaces declared in word/document.xml and added to mc:Ignorable.
    """
    if not os.path.exists(docx_path):
        return

    temp_path = docx_path + ".tmp"
    with zipfile.ZipFile(docx_path, "r") as zin, zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "[Content_Types].xml":
                text = data.decode("utf-8")
                if '<Default Extension="svg"' not in text:
                    text = re.sub(
                        r"(<Types\b[^>]*>)",
                        r'\1<Default Extension="svg" ContentType="image/svg+xml"/>',
                        text,
                        count=1,
                    )
                    data = text.encode("utf-8")
            elif item.filename == "word/settings.xml":
                text = data.decode("utf-8")
                # Upgrade compatibilityMode 14 to 16 to exit legacy compatibility mode
                text = re.sub(
                    r'(<w:compatSetting\s+[^>]*w:name="compatibilityMode"[^>]*w:val=")\d+(")',
                    r"\g<1>16\2",
                    text,
                )
                data = text.encode("utf-8")
            elif item.filename == "word/document.xml":
                text = data.decode("utf-8")
                # Ensure asvg and a16 namespaces are declared on <w:document>
                if "xmlns:asvg=" not in text:
                    text = re.sub(
                        r"(<w:document\b[^>]*)>",
                        r'\1 xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" xmlns:a16="http://schemas.microsoft.com/office/drawing/2014/main">',
                        text,
                        count=1,
                    )
                # Ensure asvg and a16 are included in mc:Ignorable so Word activates the SVG extension
                if "mc:Ignorable=" in text and "asvg" not in text:
                    text = re.sub(
                        r'mc:Ignorable="([^"]*)"',
                        r'mc:Ignorable="\1 asvg a16"',
                        text,
                        count=1,
                    )
                data = text.encode("utf-8")
            zout.writestr(item, data)

    os.replace(temp_path, docx_path)


def clean_html_tags(text: str) -> str:
    if not text:
        return ""
    s = html.unescape(text)
    s = s.replace("&emsp;", "    ").replace("&nbsp;", " ")
    s = re.sub(r"<\/?(?:u|ins)>", "", s, flags=re.I)
    s = re.sub(r"<\/?(?:b|strong)>", "**", s, flags=re.I)
    s = re.sub(r"<\/?(?:i|em)>", "*", s, flags=re.I)
    s = re.sub(r"<\/?(?:div|span|font|p)[^>]*>", "", s, flags=re.I)

    # Clean LaTeX spacing commands that leaked into regular text outside math
    s = re.sub(r"\\qquad(?![a-zA-Z])", "    ", s)
    s = re.sub(r"\\quad(?![a-zA-Z])", "   ", s)
    s = re.sub(r"\\enspace(?![a-zA-Z])", "  ", s)
    s = re.sub(r"\\(?:thickspace|medspace|thinspace)(?![a-zA-Z])", " ", s)
    return s


def append_formatted_text(p, text: str, font_name="Vazirmatn", font_size=11):
    cleaned = clean_html_tags(text)
    if not cleaned:
        return

    is_rtl = is_persian_or_arabic(cleaned)
    if is_rtl:
        set_rtl_paragraph(p)

    math_pattern = re.compile(r"(\$\$.*?\$\$|\$[^\$\n]+?\$)", re.DOTALL)
    tokens = math_pattern.split(cleaned)

    for token in tokens:
        if not token:
            continue
        if (token.startswith("$$") and token.endswith("$$")) or (token.startswith("$") and token.endswith("$")):
            is_block = token.startswith("$$")
            formula = token[2:-2].strip() if is_block else token[1:-1].strip()
            omml_elem = latex_to_omml(formula)
            if omml_elem is not None:
                p._element.append(omml_elem)
            else:
                r = p.add_run(f" {formula} ")
                r.font.name = "Cambria Math"
                r.italic = True
        else:
            sub_tokens = re.split(r"(\*\*.*?\*\*)", token)
            for st in sub_tokens:
                if not st:
                    continue
                is_bold = st.startswith("**") and st.endswith("**")
                raw_text = st[2:-2] if is_bold else st

                run = p.add_run(raw_text)
                run.bold = is_bold
                run.font.name = font_name
                run.font.size = Pt(font_size)
                if is_rtl:
                    rPr = run._element.get_or_add_rPr()
                    rPr.append(
                        parse_xml(r'<w:rtl xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
                    )


def process_table_cell(cell, content: str, doc, exported_svgs: list, output_dir: str):
    """Format table cell with split <br> lines, math, SVGs, and nested mini-tables."""
    p = cell.paragraphs[0]

    # Check if cell contains nested HTML table (<table ... </table>)
    if "<table" in content.lower() and "</table>" in content.lower():
        # Render nested sub-table
        tbl_match = re.search(r"<table[^>]*>([\s\S]*?)<\/table>", content, re.IGNORECASE)
        if tbl_match:
            pre_tbl = content[: tbl_match.start()].strip()
            tbl_html = tbl_match.group(1)
            post_tbl = content[tbl_match.end() :].strip()

            if pre_tbl:
                for line in re.split(r"<br\s*\/?>", pre_tbl, flags=re.I):
                    if line.strip():
                        append_formatted_text(p, line.strip())
                        p = cell.add_paragraph()

            # Parse <tr> and <td>
            tr_matches = re.findall(r"<tr[^>]*>([\s\S]*?)<\/tr>", tbl_html, re.IGNORECASE)
            if tr_matches:
                grid = []
                for tr in tr_matches:
                    tds = re.findall(r"<td[^>]*>([\s\S]*?)<\/td>", tr, re.IGNORECASE)
                    grid.append(tds)
                if grid:
                    sub_tbl = doc.add_table(rows=len(grid), cols=max(len(r) for r in grid))
                    sub_tbl.style = "Table Grid"
                    set_rtl_table(sub_tbl)
                    for r_i, r_data in enumerate(grid):
                        for c_i, c_val in enumerate(r_data):
                            append_formatted_text(sub_tbl.cell(r_i, c_i).paragraphs[0], c_val.strip())
                    # Move sub_tbl inside current cell xml
                    cell._tc.append(sub_tbl._tbl)

            if post_tbl:
                post_p = cell.add_paragraph()
                for line in re.split(r"<br\s*\/?>", post_tbl, flags=re.I):
                    if line.strip():
                        append_formatted_text(post_p, line.strip())
                        post_p = cell.add_paragraph()
            return

    lines = re.split(r"<br\s*\/?>", content, flags=re.I)
    first = True
    for line in lines:
        line = line.strip()
        if not line:
            continue
        cur_p = p if first else cell.add_paragraph()
        first = False

        svg_match = re.search(r"(?:```(?:xml|svg)\s*)?(<svg[\s\S]*?</svg>)(?:\s*```)?", line, re.IGNORECASE)
        if svg_match:
            svg_str = svg_match.group(1)
            svg_id = f"visual_{len(exported_svgs) + 1}"
            svg_path = os.path.join(output_dir, f"{svg_id}.svg")
            with open(svg_path, "w", encoding="utf-8") as sf:
                sf.write(svg_str)
            exported_svgs.append(svg_path)

            add_svg_to_doc(doc, svg_str, width=Inches(3.2), target_paragraph=cur_p)

            pre = line[: svg_match.start()].strip()
            post = line[svg_match.end() :].strip()
            if pre:
                append_formatted_text(cur_p, pre)
            if post:
                post_p = cell.add_paragraph()
                append_formatted_text(post_p, post)
        else:
            append_formatted_text(cur_p, line)


def normalize_markdown(md_text: str) -> str:
    """
    Standardize markdown structure:
    1. Unnest multi-line ```xml <svg>``` blocks inside table cells into single-line <svg>
    2. Convert inner markdown tables inside cells into HTML <table> to avoid column splitting
    3. Normalize raw ```xml and ```svg blocks
    """
    # 1. Convert nested mini markdown tables inside cells into HTML table
    nested_table_pat = re.compile(
        r"<br>\s*(\|\s*\$?[a-zA-Z0-9_\u0600-\u06FF]+.*\|)\s*<br>\s*(\|\s*:?-+:?.*\|)\s*<br>\s*(\|\s*\$?[a-zA-Z0-9_\u0600-\u06FF]+.*\|)\s*(?=\|\s*[^|]+\s*\|$)",
        re.DOTALL,
    )

    def repl_nested(m):
        r1, _, r2 = m.group(1), m.group(2), m.group(3)
        cols1 = [c.strip() for c in r1.strip("|").split("|")]
        cols2 = [c.strip() for c in r2.strip("|").split("|")]
        tr1 = "".join(f'<td style="border:1px solid #999; padding:4px 8px; text-align:center;">{c}</td>' for c in cols1)
        tr2 = "".join(f'<td style="border:1px solid #999; padding:4px 8px; text-align:center;">{c}</td>' for c in cols2)
        return f'<br><table style="border-collapse:collapse; margin:6px auto;"><tr>{tr1}</tr><tr>{tr2}</tr></table>'

    md_text = nested_table_pat.sub(repl_nested, md_text)

    # 2. Flatten ```xml ... ``` or ```svg ... ``` inside markdown tables
    def replace_xml_block(match):
        inner = match.group(1).strip()
        inline_svg = re.sub(r"\s*\n\s*", " ", inner)
        return "\n\n" + inline_svg + "\n\n"

    # Collapse ```xml <svg...>``` fences into standalone <svg>
    md_text = re.sub(r"```(?:xml|svg)\s*(<svg[\s\S]*?<\/svg>)\s*```", replace_xml_block, md_text, flags=re.IGNORECASE)

    # 3. Clean raw \qquad and \quad in markdown text
    md_text = re.sub(r"\\qquad(?![a-zA-Z])", "    ", md_text)
    md_text = re.sub(r"\\quad(?![a-zA-Z])", "   ", md_text)
    md_text = re.sub(r"\\enspace(?![a-zA-Z])", "  ", md_text)

    # 4. Join multi-line table rows
    lines = md_text.replace("\r\n", "\n").split("\n")
    merged_lines = []
    in_table_row = False

    for line in lines:
        stripped = line.strip()
        if in_table_row:
            if stripped.endswith("|") and not stripped.startswith("|"):
                merged_lines[-1] += "<br>" + stripped
                in_table_row = False
            elif stripped.startswith("|") and stripped.endswith("|"):
                in_table_row = True
                merged_lines.append(stripped)
            else:
                merged_lines[-1] += "<br>" + stripped
        else:
            if stripped.startswith("|") and not stripped.endswith("|"):
                in_table_row = True
                merged_lines.append(stripped)
            else:
                merged_lines.append(line)

    return "\n".join(merged_lines)


def markdown_to_docx(md_content: str, output_docx_path: str, output_dir: str = None) -> list:
    """
    Convert Markdown content to Word DOCX.
    Extracts all SVGs into standalone .svg files.
    Returns list of extracted SVG file paths.
    """
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(output_docx_path))
    os.makedirs(output_dir, exist_ok=True)

    doc = docx.Document()

    for section in doc.sections:
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)

    exported_svgs = []

    # Strip trailing visual descriptions if AI appended them
    clean_input_md = strip_visual_descriptions(md_content)

    # Pre-normalize markdown
    normalized_md = normalize_markdown(clean_input_md)
    lines = normalized_md.split("\n")
    i = 0
    total_lines = len(lines)

    while i < total_lines:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if re.match(r"^(?:---|\*\*\*|___)\s*$", stripped):
            p = doc.add_paragraph()
            p_border = parse_xml(
                r'<w:pBdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:bottom w:val="single" w:sz="6" w:space="1" w:color="CCCCCC"/></w:pBdr>'
            )
            p._element.get_or_add_pPr().append(p_border)
            i += 1
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            p = doc.add_heading(level=min(level, 4))
            append_formatted_text(p, heading_text, font_size=16 - level)
            i += 1
            continue

        # Standalone Code / SVG block
        if stripped.startswith("```"):
            code_lines = []
            i += 1
            while i < total_lines and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < total_lines and lines[i].strip().startswith("```"):
                i += 1
            raw_block = "\n".join(code_lines)

            if "<svg" in raw_block.lower():
                svg_id = f"visual_{len(exported_svgs) + 1}"
                svg_path = os.path.join(output_dir, f"{svg_id}.svg")
                with open(svg_path, "w", encoding="utf-8") as sf:
                    sf.write(raw_block)
                exported_svgs.append(svg_path)

                add_svg_to_doc(doc, raw_block, width=Inches(3.8))
            else:
                p = doc.add_paragraph()
                pPr = p._element.get_or_add_pPr()
                pBdr = parse_xml(
                    r'<w:shd xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:val="clear" w:color="auto" w:fill="F3F4F6"/>'
                )
                pPr.append(pBdr)
                r = p.add_run(raw_block)
                r.font.name = "Consolas"
                r.font.size = Pt(9.5)
            continue

        # Standalone SVG tag
        if stripped.startswith("<svg"):
            svg_lines = [line]
            while i + 1 < total_lines and "</svg>" not in svg_lines[-1].lower():
                i += 1
                svg_lines.append(lines[i])
            i += 1
            raw_svg = "\n".join(svg_lines)
            svg_id = f"visual_{len(exported_svgs) + 1}"
            svg_path = os.path.join(output_dir, f"{svg_id}.svg")
            with open(svg_path, "w", encoding="utf-8") as sf:
                sf.write(raw_svg)
            exported_svgs.append(svg_path)
            add_svg_to_doc(doc, raw_svg, width=Inches(3.8))
            continue

        # Table
        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines = []
            while i < total_lines and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                table_lines.append(lines[i].strip())
                i += 1

            parsed_rows = []
            for tl in table_lines:
                if re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", tl):
                    continue
                cols = [c.strip() for c in tl.strip("|").split("|")]
                parsed_rows.append(cols)

            if parsed_rows:
                num_cols = max(len(r) for r in parsed_rows)
                tbl = doc.add_table(rows=len(parsed_rows), cols=num_cols)
                tbl.style = "Table Grid"
                tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

                all_text = " ".join(" ".join(r) for r in parsed_rows)
                if is_persian_or_arabic(all_text):
                    set_rtl_table(tbl)

                for r_idx, row in enumerate(parsed_rows):
                    for c_idx, cell_text in enumerate(row):
                        if c_idx < num_cols:
                            cell = tbl.cell(r_idx, c_idx)
                            process_table_cell(cell, cell_text, doc, exported_svgs, output_dir)

                doc.add_paragraph()
            continue

        # Block math
        if stripped.startswith("$$"):
            math_lines = [line]
            if not (stripped.endswith("$$") and len(stripped) > 2):
                while i + 1 < total_lines and "$$" not in lines[i + 1]:
                    i += 1
                    math_lines.append(lines[i])
                if i + 1 < total_lines:
                    i += 1
                    math_lines.append(lines[i])
            i += 1
            formula_block = "\n".join(math_lines).strip("$").strip()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            omml_elem = latex_to_omml(formula_block)
            if omml_elem is not None:
                p._element.append(omml_elem)
            else:
                r = p.add_run(formula_block)
                r.font.name = "Cambria Math"
                r.italic = True
            continue

        # Regular paragraph - check if it has embedded <svg>
        svg_inline_match = re.search(r"(<svg[\s\S]*?<\/svg>)", stripped, re.IGNORECASE)
        if svg_inline_match:
            svg_raw = svg_inline_match.group(1)
            svg_id = f"visual_{len(exported_svgs) + 1}"
            svg_path = os.path.join(output_dir, f"{svg_id}.svg")
            with open(svg_path, "w", encoding="utf-8") as sf:
                sf.write(svg_raw)
            exported_svgs.append(svg_path)

            pre_t = stripped[: svg_inline_match.start()].strip()
            post_t = stripped[svg_inline_match.end() :].strip()
            if pre_t:
                p_pre = doc.add_paragraph()
                append_formatted_text(p_pre, pre_t)
            add_svg_to_doc(doc, svg_raw, width=Inches(3.8))
            if post_t:
                p_post = doc.add_paragraph()
                append_formatted_text(p_post, post_t)
            i += 1
            continue

        p = doc.add_paragraph()
        append_formatted_text(p, stripped)
        i += 1

    doc.save(output_docx_path)
    # Post-process package for modern Word 2016+ mode and SVG Convert to Shape support
    patch_docx_for_svg_shapes(output_docx_path)
    return exported_svgs
