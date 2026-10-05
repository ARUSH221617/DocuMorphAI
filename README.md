# DocuMorph AI

Turn PDFs and images into Microsoft Word (`.docx`) documents with native editable equations and vector graphics.

---

## Features

- **Native Word Equations**: LaTeX formulas (`$math$`, `$$math$$`) convert to native Word OMML (`oMath`). Fully editable in Word via `Alt+=`.
- **Vector SVGs with Shape Conversion**: Embedded as vector graphics via `asvg:svgBlip` with high-DPI raster fallbacks. Right-click graphic in Word $\to$ **Convert to Shape** to edit lines and paths.
- **Autonomous AI Agent**: Multi-turn tool-calling agent (`agent.py`) inspects page renders, validates LaTeX and SVG XML syntax, edits Markdown diffs, loads specialized skills, calls MCP server tools, and compiles DOCX.
- **Agent Domain Skills (`skills.py`)**: Discover and inject modular guidance from local `skills/` and global `~/.agents/skills` (e.g. `math-formula-extractor`, `svg-diagram-architect`, `persian-rtl-typesetter`, `table-and-matrix-styler`).
- **Model Context Protocol (MCP) Integration (`mcp_manager.py`)**: Connect any stdio or HTTP MCP server via JSON-RPC 2.0. Discovered tools are dynamically mapped to OpenAI function schemas for agent use. Includes built-in `doc_utils` server with math delimiter analyzer and terminology translator.
- **SQLite Persistence (`documorph.db`)**: Persistent settings store, agent conversation/refinement logs, MCP server configs, and job status tracking.
- **Maintenance & Cache Purge**: 1-click purge button in Settings to delete uploaded inputs, output DOCX/MD/SVG artifacts, and clean SQLite tables while preserving API settings.
- **Job Timer & Price Calculation**: Live execution stopwatch timer for running jobs. Calculates elapsed compute time and estimated USD token cost based on model rates (Gemini Flash, Claude Opus, Claude Sonnet, etc.).
- **Multi-File Parallel Queue**: Upload multiple files at once. Background dispatcher runs jobs concurrently up to `MAX_CONCURRENT_JOBS` workers.
- **Resilient Background Execution**: Conversion runs in detached background threads. Browser refresh or tab closing does not interrupt progress. Reconnects via Server-Sent Events (SSE).
- **Interactive Markdown & Agent Chat Studio**: Edit extracted Markdown directly, chat with the AI agent to apply refinements, and view conversation logs with instant recompilation.
- **Dev Auto-Reload**: FastAPI server monitors Python, HTML, CSS, and JS files with automatic reload.
- **RTL & Persian/Arabic Typography**: Automatic script detection, right-to-left paragraph alignment, and table RTL grid layout.
- **No Summary Artifacts**: Automatically removes extra "Visual Descriptions" or appended commentary.

---

## Architecture

```
PDF / Image
    │
    ▼ (pypdfium2 / Pillow)
Page Renders (PNG)
    │
    ▼ (OpenAI Vision API / Gemini)
Markdown + LaTeX + SVGs
    │
    ▼ (Agent Tools: validate_latex, validate_svg, edit_markdown_snippet)
Verified Markdown
    │
    ▼ (converter.py)
┌──────────────────────────────────────────────┐
│  • LaTeX ──> MathML ──> OMML (MML2OMML.XSL)   │
│  • SVG ──> PySide6 raster fallback + asvg    │
│  • RTL / LTR paragraph formatting            │
└──────────────────────────────────────────────┘
    │
    ▼
Output: .docx + .md + .svg files + .zip bundle
```

---

## Requirements

- **Python 3.13+** (Windows: `py -3.13`)
- Microsoft Word (for viewing and editing generated `.docx`)

### Installation

```bash
pip install -r requirements.txt
```

---

## Configuration

Create `.env` in project root:

```env
OPENAI_API_BASE_URL="http://localhost:20128/v1"
OPENAI_API_KEY="your-api-key"
AI_DEFAULT_MODEL="ag/gemini-3.8-flash-high"
OUTPUT_PATH="D:\arush\toword\output"
```

---

## Usage

### 1. Web Application

Launch with launcher:
```bat
run.bat
```

Or run directly:
```bash
py -3.13 app.py
```
Starts on `http://localhost:8000` and automatically opens browser. Auto-reload is enabled by default.

Flags:
- `--port 8000`: Set custom port.
- `--no-reload`: Disable file watch auto-reload.
- `--no-browser`: Do not auto-launch default browser.

### 2. CLI Direct Conversion

Run autonomous agent pipeline from terminal:
```bash
py -3.13 app.py input/document.pdf --mode agent
```

Fast single-pass pipeline:
```bash
py -3.13 app.py input/document.pdf --mode fast
```

### 3. Run Self-Checks & Code Quality

Run verification suite (OMML equations, SVG vector embedding, Persian RTL detection, agent tools, HTML/XML normalization, Skills engine, MCP JSON-RPC protocol, and Ruff formatting/linting):
```bash
py -3.13 test_check.py
```

Lint and format codebase with Ruff:
```bash
# Check lint rules
py -3.13 -m ruff check .

# Auto-fix lint issues
py -3.13 -m ruff check --fix .

# Format code
py -3.13 -m ruff format .
```

---

## Skills & Model Context Protocol (MCP)

### Available Skills
Located in `skills/` (local) and `~/.agents/skills` (user global):
- `math-formula-extractor`: Strict LaTeX formatting for Word OMML conversion.
- `svg-diagram-architect`: Vector rules and viewBox requirements for Word shape conversion.
- `persian-rtl-typesetter`: RTL direction, Persian question numbering, bilingual formulas.
- `table-and-matrix-styler`: Robust Markdown tables and matrix alignments.

The AI agent invokes `use_skill(skill_name)` during workflow, or users can inject skills via the Studio UI pill buttons.

### MCP Server Management
DocuMorph connects to external tools using the official Model Context Protocol (JSON-RPC 2.0):
1. **Stdio Subprocess**: Runs CLI commands (e.g. `npx -y @modelcontextprotocol/server-...` or `python ...`).
2. **HTTP / SSE**: Connects to remote JSON-RPC servers.
3. **Built-in `doc_utils`**: Ships with native tools (`analyze_math_formulas`, `generate_svg_grid`, `math_term_dictionary`).
4. **Sandbox MCP Runner**: Test any tool interactively under `04 // SANDBOX`.

---

## Output Structure

All outputs are saved to `OUTPUT_PATH`:
- `<id>_<filename>.docx`: Word document with native math and vector shapes.
- `<id>_<filename>.md`: Cleaned Markdown source.
- `<id>_<filename>_svgs/`: Extracted vector SVG files.
- `<id>_<filename>_bundle.zip`: Downloadable ZIP archive containing all files.
