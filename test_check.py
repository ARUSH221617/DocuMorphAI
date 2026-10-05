"""
Self-check validation test for DocuMorph AI.
Tests:
1. LaTeX to OMML (Alt+= equation) conversion
2. SVG vector packaging into Word
3. Table parsing with RTL support
4. Agent tool execution (validate_latex, validate_svg, edit_markdown_snippet)
5. Parsing and conversion of HTML (<br>, <b>, <u>) and XML/SVG code fences in markdown
"""

import io
import json
import os

import docx

import agent
import mcp_manager
import skills
from converter import add_svg_to_doc, is_persian_or_arabic, latex_to_omml, markdown_to_docx


def test_latex_to_omml():
    omml = latex_to_omml(r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}")
    assert omml is not None, "LaTeX to OMML conversion failed"
    tag = omml.tag
    assert "oMath" in tag, f"Unexpected OMML tag: {tag}"
    print("✓ test_latex_to_omml passed")


def test_persian_detection():
    assert is_persian_or_arabic("سلام دنیا") is True
    assert is_persian_or_arabic("Hello world") is False
    print("✓ test_persian_detection passed")


def test_svg_to_docx():
    doc = docx.Document()
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><circle cx="50" cy="50" r="40" fill="red"/></svg>'
    add_svg_to_doc(doc, svg)
    buf = io.BytesIO()
    doc.save(buf)
    assert len(buf.getvalue()) > 0
    print("✓ test_svg_to_docx passed")


def test_agent_tools():
    test_pdf = "input/jahat8-1 31.pdf"
    if not os.path.exists(test_pdf):
        os.makedirs("input", exist_ok=True)
        import pypdfium2 as pdfium

        d = pdfium.PdfDocument.new()
        d.new_page(200, 200)
        d.save(test_pdf)
    ag = agent.DocuMorphAgent(test_pdf)
    res_latex = json.loads(ag.execute_tool("validate_latex", {"formulas": [r"E = mc^2", r"\frac{a}{b}"]}))
    assert all(r["valid"] for r in res_latex["results"]), "Agent validate_latex failed"

    res_svg = json.loads(
        ag.execute_tool("validate_svg", {"svg_code": '<svg width="200" height="200" viewBox="0 0 200 200"></svg>'})
    )
    assert res_svg["valid_xml"] is True and res_svg["has_viewbox"] is True

    ag.execute_tool("update_markdown", {"content": "# Header\nQuestion 1: x = 2"})
    ag.execute_tool("edit_markdown_snippet", {"search": "x = 2", "replace": "x = 5"})
    assert "x = 5" in ag.markdown, "Agent edit_markdown_snippet failed"
    print("✓ test_agent_tools passed")


def test_xml_html_svg_conversion():
    sample_snippet = """# Question with HTML & XML

Line 1<br><br>```xml
<svg id="fig-q12" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
  <circle cx="50" cy="50" r="40" fill="#3b82f6" />
</svg>
```<br>Line 2 with <b>bold</b> and <u>underline</u> and $x^2 + y^2 = 1$
"""
    out_docx = "output/_check_xml_html.docx"
    svgs = markdown_to_docx(sample_snippet, out_docx, output_dir="output/_check_xml_html_svgs")
    assert os.path.exists(out_docx)
    assert len(svgs) == 1
    assert os.path.exists(svgs[0])

    # Clean up
    if os.path.exists(out_docx):
        os.remove(out_docx)
    if os.path.exists(svgs[0]):
        os.remove(svgs[0])
    if os.path.exists("output/_check_xml_html_svgs"):
        os.rmdir("output/_check_xml_html_svgs")
    print("✓ test_xml_html_svg_conversion passed")


def test_skills_system():
    all_skills = skills.list_skills()
    assert len(all_skills) >= 4, f"Expected at least 4 skills, got {len(all_skills)}"
    names = [s["name"] for s in all_skills]
    assert "math-formula-extractor" in names
    assert "svg-diagram-architect" in names
    assert "page-layout-architect" in names

    sk = skills.get_skill("page-layout-architect")
    assert sk is not None
    assert "NEVER USE TABLES FOR PAGE OR EXAM LAYOUT" in sk["instructions"]
    print("✓ test_skills_system passed")


def test_mcp_system():
    # Test MCP client tool discovery
    tools, routing = mcp_manager.mcp_mgr.discover_all_tools()
    assert len(tools) >= 3, f"Expected at least 3 MCP tools from built-in server, got {len(tools)}"
    tool_names = [t["function"]["name"] for t in tools]
    assert any("analyze_math_formulas" in tn for tn in tool_names)

    # Test MCP tool call
    res_raw = mcp_manager.mcp_mgr.call_tool("doc_utils", "math_term_dictionary", {"term": "derivative"})
    res = json.loads(res_raw)
    assert res.get("found") is True and res.get("translation") == "مشتق"

    # Test Agent tool call integration
    test_pdf = "input/jahat8-1 31.pdf"
    ag = agent.DocuMorphAgent(test_pdf)
    skills_json = json.loads(ag.execute_tool("list_skills", {}))
    assert skills_json.get("skills_count", 0) >= 4

    use_skill_json = json.loads(ag.execute_tool("use_skill", {"skill_name": "math-formula-extractor"}))
    assert use_skill_json.get("status") == "loaded"

    mcp_call_json = json.loads(
        ag.execute_tool(
            "call_mcp_tool",
            {"server_id": "doc_utils", "tool_name": "math_term_dictionary", "arguments": {"term": "integral"}},
        )
    )
    assert mcp_call_json.get("status") == "success"
    assert "انتگرال" in mcp_call_json.get("output", "")
    print("✓ test_mcp_system passed")


def test_agent_steps():
    test_pdf = "input/jahat8-1 31.pdf"
    ag = agent.DocuMorphAgent(test_pdf)
    assert len(ag.steps) >= 1
    assert ag.steps[0]["type"] == "init"
    assert "DOCUMENT VISION" in ag.steps[0]["title"]
    print("✓ test_agent_steps passed")


def test_dynamic_models():
    from fastapi.testclient import TestClient

    import app

    client = TestClient(app.app)
    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) > 0
    assert data["current_model"] in data["models"]
    print("✓ test_dynamic_models passed")


def test_token_and_context_window():
    import db

    u = db.calculate_context_usage("ag/gemini-3.8-flash-high", 4000, 1000)
    assert u["total_tokens"] == 5000
    assert u["context_limit"] == 1_048_576
    assert u["context_percent"] == 0.48

    from fastapi.testclient import TestClient

    import app

    client = TestClient(app.app)
    r = client.get("/api/history")
    assert r.status_code == 200
    d = r.json()
    assert "total_tokens" in d
    assert "formatted_total_tokens" in d
    print("✓ test_token_and_context_window passed")


def test_api_context_window_and_tiktoken():
    from fastapi.testclient import TestClient

    import app
    import db
    import tokenizer

    client = TestClient(app.app)
    # Trigger model discovery & caching from API
    r_models = client.get("/api/models")
    assert r_models.status_code == 200
    m_data = r_models.json()
    assert m_data.get("count") > 0

    # Verify context windows were cached from API into SQLite
    cached_models = db.get_all_cached_models()
    assert len(cached_models) > 0

    # Verify tiktoken calculation
    est = tokenizer.estimate_preflight_budget(
        "Sample prompt text for token test", "ag/gemini-3.8-flash-high", page_count=1
    )
    assert est["text_tokens"] > 0
    assert est["image_tokens"] == 1200
    assert est["context_limit"] == 1048576

    # Verify endpoint
    r_est = client.post(
        "/api/tools/estimate-tokens",
        json={"text": "Hello world equation $x=1$", "model": "ag/gemini-3.8-flash-high", "page_count": 0},
    )
    assert r_est.status_code == 200
    res = r_est.json()
    assert res["text_tokens"] > 0
    assert res["context_limit"] == 1048576
    print("✓ test_api_context_window_and_tiktoken passed")


def test_latex_spacing_and_qquad():
    import os

    import docx

    import converter

    # 1. Test in latex_to_omml
    formula = r"x = 1 \qquad y = 2"
    omml = converter.latex_to_omml(formula)
    assert omml is not None, "Failed to convert formula with \\qquad"
    from lxml import etree

    xml = etree.tostring(omml).decode("utf-8")
    assert "&#160;" in xml or "\u00a0" in xml

    # 2. Test in markdown_to_docx
    test_md = r"Choice: الف) $x = 1$ \qquad ب) $x = 2$"
    out_docx = "output/_check_qquad.docx"
    converter.markdown_to_docx(test_md, out_docx)
    assert os.path.exists(out_docx)
    d = docx.Document(out_docx)
    text = " ".join(p.text for p in d.paragraphs)
    assert r"\qquad" not in text
    assert r"\quad" not in text
    if os.path.exists(out_docx):
        os.remove(out_docx)
    print("✓ test_latex_spacing_and_qquad passed")


def test_svg_word_compatibility():
    import os
    import zipfile

    import docx

    import converter

    test_docx = "output/_check_svg_compat.docx"
    doc = docx.Document()
    svg = '<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="purple"/></svg>'
    converter.add_svg_to_doc(doc, svg)
    doc.save(test_docx)
    converter.patch_docx_for_svg_shapes(test_docx)

    with zipfile.ZipFile(test_docx, "r") as z:
        ct = z.read("[Content_Types].xml").decode("utf-8")
        settings = z.read("word/settings.xml").decode("utf-8")
        doc_xml = z.read("word/document.xml").decode("utf-8")

        assert '<Default Extension="svg" ContentType="image/svg+xml"/>' in ct, "Missing Default svg content type"
        assert 'w:val="16"' in settings, "compatibilityMode not upgraded to 16"
        assert "xmlns:asvg=" in doc_xml, "xmlns:asvg not declared on w:document"
        assert "asvg" in doc_xml, "asvg not in mc:Ignorable"

        media_files = [n for n in z.namelist() if "media/" in n]
        assert not any(m.lower().endswith(".png") for m in media_files), f"PNG found in docx media: {media_files}"
        assert any(m.lower().endswith(".svg") for m in media_files), "No SVG found in docx media!"

    if os.path.exists(test_docx):
        os.remove(test_docx)
    print("✓ test_svg_word_compatibility passed")


def test_ruff_lint_and_format():
    import subprocess
    import sys

    res = subprocess.run([sys.executable, "-m", "ruff", "check", "."], capture_output=True, text=True)
    assert res.returncode == 0, f"Ruff lint failed:\n{res.stdout}\n{res.stderr}"
    res_fmt = subprocess.run([sys.executable, "-m", "ruff", "format", "--check", "."], capture_output=True, text=True)
    assert res_fmt.returncode == 0, f"Ruff format check failed:\n{res_fmt.stdout}\n{res_fmt.stderr}"
    print("✓ test_ruff_lint_and_format passed")


if __name__ == "__main__":
    test_latex_to_omml()
    test_persian_detection()
    test_svg_to_docx()
    test_agent_tools()
    test_xml_html_svg_conversion()
    test_skills_system()
    test_mcp_system()
    test_agent_steps()
    test_dynamic_models()
    test_token_and_context_window()
    test_api_context_window_and_tiktoken()
    test_latex_spacing_and_qquad()
    test_svg_word_compatibility()
    test_ruff_lint_and_format()
    print("\nAll self-checks passed successfully!")
