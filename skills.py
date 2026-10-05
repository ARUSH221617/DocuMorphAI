"""
DocuMorph AI - Skills System
Discovers, parses, and provides agent skills from:
1. Local project skills directory: `toword/skills/`
2. Global user skills directory: `~/.agents/skills/`
"""

import os
import re
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
LOCAL_SKILLS_DIR = BASE_DIR / "skills"
GLOBAL_SKILLS_DIR = Path(os.path.expanduser("~/.agents/skills"))


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from markdown without external dependencies."""
    meta = {}
    content = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            raw_meta = parts[1].strip()
            content = parts[2].strip()
            for line in raw_meta.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip("\"'")
    return meta, content


def get_skills_dirs() -> list[tuple[str, Path]]:
    dirs = [("local", LOCAL_SKILLS_DIR)]
    if GLOBAL_SKILLS_DIR.exists():
        dirs.append(("global", GLOBAL_SKILLS_DIR))
    return dirs


def list_skills() -> list[dict[str, Any]]:
    """Return all available skills across local and global directories."""
    skills = []
    seen_names = set()

    for scope, s_dir in get_skills_dirs():
        if not s_dir.exists():
            continue
        for item in s_dir.iterdir():
            if item.is_dir():
                skill_md = item / "SKILL.md"
                if skill_md.is_file():
                    try:
                        text = skill_md.read_text(encoding="utf-8")
                        meta, body = parse_frontmatter(text)
                        name = meta.get("name") or item.name
                        desc = meta.get("description") or (body[:180].replace("\n", " ").strip() + "...")
                        skills.append(
                            {
                                "name": name,
                                "folder": item.name,
                                "description": desc,
                                "scope": scope,
                                "path": str(skill_md),
                                "preview": body[:200],
                            }
                        )
                        seen_names.add(name)
                    except Exception:
                        pass
    return sorted(skills, key=lambda s: (0 if s["scope"] == "local" else 1, s["name"]))


def get_skill(name: str) -> dict[str, Any] | None:
    """Retrieve full skill metadata and instructions content by name."""
    name_clean = name.strip().lower()
    for scope, s_dir in get_skills_dirs():
        if not s_dir.exists():
            continue
        for item in s_dir.iterdir():
            if item.is_dir():
                skill_md = item / "SKILL.md"
                if skill_md.is_file():
                    try:
                        text = skill_md.read_text(encoding="utf-8")
                        meta, body = parse_frontmatter(text)
                        skill_name = (meta.get("name") or item.name).lower()
                        if skill_name == name_clean or item.name.lower() == name_clean:
                            return {
                                "name": meta.get("name") or item.name,
                                "folder": item.name,
                                "description": meta.get("description", ""),
                                "scope": scope,
                                "path": str(skill_md),
                                "content": text,
                                "instructions": body,
                            }
                    except Exception:
                        pass
    return None


def save_local_skill(name: str, description: str, instructions: str) -> str:
    """Save or update a local project skill."""
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "-", name.strip().lower()).strip("-")
    if not safe_name:
        raise ValueError("Invalid skill name")

    skill_dir = LOCAL_SKILLS_DIR / safe_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"

    frontmatter = f"---\nname: {safe_name}\ndescription: {description.strip()}\n---\n\n"
    full_text = frontmatter + instructions.strip() + "\n"
    skill_md.write_text(full_text, encoding="utf-8")
    return str(skill_md)


def delete_local_skill(name: str) -> bool:
    """Delete a local project skill directory."""
    import shutil

    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "-", name.strip().lower()).strip("-")
    skill_dir = LOCAL_SKILLS_DIR / safe_name
    if skill_dir.is_dir() and skill_dir.resolve().is_relative_to(LOCAL_SKILLS_DIR.resolve()):
        shutil.rmtree(skill_dir, ignore_errors=True)
        return True
    return False


def init_default_skills():
    """Bootstrap core default skills for document conversion."""
    LOCAL_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    defaults = [
        (
            "math-formula-extractor",
            "Expert rules for converting complex mathematical expressions, matrices, piecewise functions, and fractions to Word OMML-compatible LaTeX.",
            """# Math Formula Extractor Skill

## Core Principles
1. Always write inline math wrapped in `$ ... $` and display/block equations in `$$ ... $$`.
2. Word OMML compatibility:
   - Use standard `\\frac{numerator}{denominator}`.
   - Use standard symbols: `\\pm`, `\\times`, `\\div`, `\\cdot`, `\\le`, `\\ge`, `\\approx`, `\\neq`.
   - Greek letters: `\\alpha`, `\\beta`, `\\theta`, `\\pi`, `\\Delta`, `\\lambda`, `\\mu`, `\\sigma`.
   - Vectors: use `\\vec{a}` or `\\mathbf{v}`.
   - Square roots: `\\sqrt{x}` or `\\sqrt[n]{x}`.
3. Gotchas to avoid:
   - Do NOT use unescaped ampersands `&` outside of `\\begin{matrix}` or aligned environments.
   - Do NOT use non-standard LaTeX packages or commands (e.g. `\\color`, `\\tikz`).
   - Do NOT use `\\qquad` or `\\quad` in regular text; use standard spaces.
   - Keep LaTeX pure without embedded HTML.
""",
        ),
        (
            "svg-diagram-architect",
            "Strict vector guidelines for geometry, coordinates, graphs, and shapes to enable Word 'Convert to Shape'.",
            """# SVG Diagram Architect Skill

## Core Principles
1. Output SVG inside code fences: ```xml ... ``` or ```svg ... ```.
2. SVG requirements for Word conversion:
   - Root `<svg>` MUST include `xmlns="http://www.w3.org/2000/svg"`.
   - MUST include `viewBox="0 0 W H"` attribute.
   - MUST specify explicit `width` and `height` attributes (e.g. `width="280" height="280"`).
3. Visual styling:
   - Use standard geometric elements: `<line>`, `<circle>`, `<rect>`, `<polygon>`, `<polyline>`, `<path>`.
   - Gridlines: `<line stroke="#d1d5db" stroke-width="1" stroke-dasharray="3,3" />`.
   - Vectors: define `<marker id="arrow" ...>` in `<defs>` and use `marker-end="url(#arrow)"`.
   - Labels: use `<text font-family="Arial" font-size="14" font-weight="bold">` placed with clear clearance.
4. Clean self-contained tags: close every tag properly. No external image links or external fonts.
""",
        ),
        (
            "table-and-matrix-styler",
            "Guidelines for structuring clean Markdown tables and matrices without line breaking or corrupted cell layout.",
            """# Table and Matrix Styler Skill

## Core Principles
1. Markdown table syntax:
   - Format: `| Column 1 | Column 2 | Column 3 |`.
   - Header separator: `| :--- | :---: | ---: |`.
2. Rules:
   - Every row MUST be on exactly ONE line.
   - Never use `<br>` or multiline text inside a single cell; use comma or punctuation instead.
   - Escape pipes inside cell content using `\\|`.
   - Math inside cells: `$x^2$` is fully supported.
""",
        ),
        (
            "persian-rtl-typesetter",
            "Best practices for Persian and Arabic academic questions, RTL direction, Persian numeral layout, and bilingual math phrasing.",
            """# Persian RTL Typesetter Skill

## Core Principles
1. Persian question headings:
   - Format questions cleanly: `**۱-** سوال...` or `### سوال ۱`.
   - Keep questions clearly separated with blank lines.
2. Formula handling:
   - Math formulas in Persian sentences remain standard LTR LaTeX `$x + y = 5$`.
   - Persian numbers inside regular text (e.g. `۱۲.۵`), but variable names in math remain standard English (`x`, `y`, `z`).
3. Layout:
   - Multiple choice options: use clean markdown list items or a single-row 4-column table:
     `| (الف) ... | (ب) ... | (ج) ... | (د) ... |`.
""",
        ),
        (
            "page-layout-architect",
            "Strictly forbids wrapping exam pages or questions inside tables. Reconstructs exams using clean sequential Markdown headings, paragraphs, and lists while preserving headers, questions, points, and visuals.",
            """# Page Layout Architect Skill

## CRITICAL RULE: NEVER USE TABLES FOR PAGE OR EXAM LAYOUT
- DO NOT wrap the whole page, exam questions, or options inside a table.
- Physical exam sheets print questions in a 3-column grid (ردیف / سوال / بارم). DO NOT REPLICATE THIS AS A TABLE.
- Reconstruct questions as clean Markdown: `**۱-** سوال... (۱.۵ نمره)`.
- SVGs MUST be standalone blocks placed directly where they appear. NEVER put SVGs inside table cells.
- Tables are ONLY permitted for real tabular data (e.g. data tables, coordinate values, function tables).
""",
        ),
    ]

    for name, desc, body in defaults:
        s_dir = LOCAL_SKILLS_DIR / name
        s_file = s_dir / "SKILL.md"
        if not s_file.exists():
            save_local_skill(name, desc, body)


init_default_skills()
