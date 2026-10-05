"""
DocuMorph AI - Built-in MCP Server (JSON-RPC 2.0 over stdio)
Provides standard document and math analysis tools via Model Context Protocol.
"""

import json
import re
import sys

TOOLS = [
    {
        "name": "analyze_math_formulas",
        "description": "Scan markdown content to count and validate inline ($) and display ($$) LaTeX formulas. Reports unclosed delimiters.",
        "inputSchema": {
            "type": "object",
            "properties": {"markdown": {"type": "string", "description": "Markdown text to analyze."}},
            "required": ["markdown"],
        },
    },
    {
        "name": "generate_svg_grid",
        "description": "Generate clean SVG vector markup for an X/Y coordinate axis and grid system.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "width": {"type": "integer", "description": "Width in px (default: 300)"},
                "height": {"type": "integer", "description": "Height in px (default: 300)"},
                "step": {"type": "integer", "description": "Grid step spacing in px (default: 30)"},
            },
        },
    },
    {
        "name": "math_term_dictionary",
        "description": "Translate or look up Persian/English mathematical terms (e.g., derivative, integral, vector, matrix, chord, hypotenuse).",
        "inputSchema": {
            "type": "object",
            "properties": {"term": {"type": "string", "description": "Term in English or Persian to look up."}},
            "required": ["term"],
        },
    },
]

DICT_TERMS = {
    "derivative": "مشتق",
    "integral": "انتگرال",
    "matrix": "ماتریس",
    "vector": "بردار",
    "fraction": "کسر",
    "hypotenuse": "وتر",
    "circle": "دایره",
    "triangle": "مثلث",
    "rectangle": "مستطیل",
    "square": "مربع",
    "domain": "دامنه",
    "range": "برد",
    "function": "تابع",
    "limit": "حد",
    "equation": "معادله",
    "inequality": "نامعادله",
    "polynomial": "چندجمله‌ای",
    "system of equations": "دستگاه معادلات",
    "مشتق": "derivative",
    "انتگرال": "integral",
    "ماتریس": "matrix",
    "بردار": "vector",
    "وتر": "hypotenuse",
    "دامنه": "domain",
    "برد": "range",
    "تابع": "function",
}


def handle_analyze_math(markdown: str):
    # Find $$...$$
    blocks = re.findall(r"\$\$(.+?)\$\$", markdown, re.DOTALL)
    # Find $...$
    inlines = re.findall(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", markdown)
    # Check parity of $
    total_dollars = markdown.count("$")
    unclosed = (total_dollars % 2) != 0

    return {
        "block_equations_count": len(blocks),
        "inline_formulas_count": len(inlines),
        "total_dollar_signs": total_dollars,
        "syntax_balanced": not unclosed,
        "warning": "Unclosed dollar sign detected! Formula may be corrupted." if unclosed else "OK",
    }


def handle_generate_grid(width: int = 300, height: int = 300, step: int = 30):
    w = width or 300
    h = height or 300
    s = step or 30
    cx = w // 2
    cy = h // 2

    lines = []
    # Grid
    for x in range(s, w, s):
        lines.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="#e5e7eb" stroke-width="1" />')
    for y in range(s, h, s):
        lines.append(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" stroke="#e5e7eb" stroke-width="1" />')

    # Axes
    lines.append(
        f'<line x1="0" y1="{cy}" x2="{w}" y2="{cy}" stroke="#111827" stroke-width="2" marker-end="url(#arrow)" />'
    )
    lines.append(
        f'<line x1="{cx}" y1="{h}" x2="{cx}" y2="0" stroke="#111827" stroke-width="2" marker-end="url(#arrow)" />'
    )
    lines.append(f'<text x="{w - 15}" y="{cy - 8}" font-family="Arial" font-size="12" font-weight="bold">x</text>')
    lines.append(f'<text x="{cx + 8}" y="15" font-family="Arial" font-size="12" font-weight="bold">y</text>')

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-reverse">
      <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="#111827" />
    </marker>
  </defs>
  {"".join(lines)}
</svg>"""
    return {"svg": svg}


def handle_dict(term: str):
    t = (term or "").strip().lower()
    match = DICT_TERMS.get(t)
    if match:
        return {"term": term, "translation": match, "found": True}
    for k, v in DICT_TERMS.items():
        if t in k or t in v:
            return {"term": term, "translation": v, "matched_entry": k, "found": True}
    return {"term": term, "found": False, "message": "Term not found in dictionary"}


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        method = req.get("method")
        msg_id = req.get("id")

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "documorph-builtin-mcp", "version": "1.0.0"},
                },
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            pass

        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})

            if name == "analyze_math_formulas":
                res = handle_analyze_math(args.get("markdown", ""))
            elif name == "generate_svg_grid":
                res = handle_generate_grid(args.get("width", 300), args.get("height", 300), args.get("step", 30))
            elif name == "math_term_dictionary":
                res = handle_dict(args.get("term", ""))
            else:
                res = {"error": f"Unknown tool '{name}'"}

            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}]},
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
