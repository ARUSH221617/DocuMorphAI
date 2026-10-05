"""
DocuMorph AI Agent
Autonomous agent loop using function calling tools:
- view_page_image
- update_markdown
- edit_markdown_snippet
- validate_latex
- validate_svg
- build_docx_output
"""

import os
import json
import base64
import io
import re
import time
from typing import List, Dict, Any, Optional, Callable
import dotenv
import openai
from PIL import Image
import pypdfium2 as pdfium
from lxml import etree
import latex2mathml.converter

import converter
import db
import skills
import mcp_manager

dotenv.load_dotenv()
config = dotenv.dotenv_values(".env")

def get_config(key: str, default: str = "") -> str:
    val = db.get_setting(key)
    if val:
        return val
    return os.getenv(key) or config.get(key) or default

OPENAI_API_BASE_URL = get_config("OPENAI_API_BASE_URL", "http://localhost:20128/v1")
OPENAI_API_KEY = get_config("OPENAI_API_KEY", "")
AI_DEFAULT_MODEL = get_config("AI_DEFAULT_MODEL", "ag/gemini-3.8-flash-high")
OUTPUT_DIR = get_config("OUTPUT_PATH", "output")

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "view_page_image",
            "description": "View rendered image of document page (1-based index). Returns base64 image and dimensions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_number": {
                        "type": "integer",
                        "description": "Page number to view (starts at 1)."
                    }
                },
                "required": ["page_number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_markdown",
            "description": "Set or replace whole markdown document content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Full structured markdown text."
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_markdown_snippet",
            "description": "Find exact substring in markdown and replace it. Best for fixing typos, math formulas, or SVG blocks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Exact text to find."
                    },
                    "replace": {
                        "type": "string",
                        "description": "New replacement text."
                    }
                },
                "required": ["search", "replace"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_latex",
            "description": "Validate list of LaTeX math formulas. Returns whether each formula successfully parses into Word OMML.",
            "parameters": {
                "type": "object",
                "properties": {
                    "formulas": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of LaTeX formula strings to test."
                    }
                },
                "required": ["formulas"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_svg",
            "description": "Validate SVG XML markup. Checks XML syntax and presence of width/height/viewBox attributes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "svg_code": {
                        "type": "string",
                        "description": "SVG XML code."
                    }
                },
                "required": ["svg_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "build_docx_output",
            "description": "Compile current markdown into final Word .docx document with OMML math equations and SVG pictures.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_title": {
                        "type": "string",
                        "description": "Optional title for document."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List all available domain skills with descriptions (e.g. math formulas, SVG diagrams, table styling, Persian typography).",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "use_skill",
            "description": "Load and apply the specialized instructions of a skill by name (e.g. 'math-formula-extractor', 'svg-diagram-architect', 'persian-rtl-typesetter').",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the skill to retrieve instructions for."
                    }
                },
                "required": ["skill_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "call_mcp_tool",
            "description": "Call an external tool on a configured Model Context Protocol (MCP) server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_id": {
                        "type": "string",
                        "description": "Configured MCP server identifier (e.g. 'doc_utils')."
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the tool on the MCP server."
                    },
                    "arguments": {
                        "type": "object",
                        "description": "JSON arguments matching tool input schema."
                    }
                },
                "required": ["server_id", "tool_name"]
            }
        }
    }
]

class DocuMorphAgent:
    def __init__(self, file_path: str, model: str = None, output_dir: str = None):
        self.file_path = file_path
        self.model = model or AI_DEFAULT_MODEL
        self.output_dir = output_dir or OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)
        
        url = db.get_setting("OPENAI_API_BASE_URL") or OPENAI_API_BASE_URL
        key = db.get_setting("OPENAI_API_KEY") or OPENAI_API_KEY or "dummy_key"
        self.client = openai.OpenAI(
            base_url=url,
            api_key=key
        )
        
        # Load pages
        self.pages: List[Image.Image] = []
        self._load_document()
        
        # Discover and prepare MCP tools
        self.mcp_tools, self.mcp_routing = mcp_manager.mcp_mgr.discover_all_tools()
        self.all_tools = AGENT_TOOLS + self.mcp_tools
        self.active_skills_loaded: List[str] = []
        self.steps: List[Dict[str, Any]] = [
            {
                "step": 1,
                "type": "init",
                "title": "DOCUMENT VISION INGESTED",
                "description": f"Loaded {len(self.pages)} page(s) at 2x render scale for multimodal vision inspection.",
                "status": "success",
                "time": time.strftime("%H:%M:%S")
            }
        ]

        self.markdown = ""
        self.base_name = os.path.splitext(os.path.basename(file_path))[0]
        self.docx_path = os.path.join(self.output_dir, f"{self.base_name}.docx")
        self.svgs_dir = os.path.join(self.output_dir, f"{self.base_name}_svgs")
        self.exported_svgs: List[str] = []
        self.history: List[Dict[str, Any]] = []
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0

    def _load_document(self):
        ext = os.path.splitext(self.file_path)[1].lower()
        if ext == ".pdf":
            pdf = pdfium.PdfDocument(self.file_path)
            for p in pdf:
                self.pages.append(p.render(scale=2.0).to_pil())
        elif ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
            img = Image.open(self.file_path)
            if img.mode != "RGB":
                img = img.convert("RGB")
            self.pages.append(img)
        else:
            raise ValueError(f"Unsupported format: {ext}")

    def execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Execute agent tool and return JSON result string."""
        if name == "view_page_image":
            p_num = args.get("page_number", 1)
            if 1 <= p_num <= len(self.pages):
                img = self.pages[p_num - 1]
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                b64 = base64.b64encode(buf.getvalue()).decode()
                return json.dumps({
                    "page_number": p_num,
                    "total_pages": len(self.pages),
                    "width": img.width,
                    "height": img.height,
                    "image_data_uri": f"data:image/jpeg;base64,{b64[:200]}...[truncated]"
                })
            return json.dumps({"error": f"Invalid page number {p_num}. Total: {len(self.pages)}"})

        elif name == "update_markdown":
            self.markdown = args.get("content", "")
            return json.dumps({
                "status": "success",
                "length": len(self.markdown),
                "contains_svg": "<svg" in self.markdown.lower()
            })

        elif name == "edit_markdown_snippet":
            search = args.get("search", "")
            replace = args.get("replace", "")
            if search in self.markdown:
                self.markdown = self.markdown.replace(search, replace, 1)
                return json.dumps({"status": "success", "message": "Text replaced successfully."})
            return json.dumps({"status": "error", "message": "search string not found in markdown."})

        elif name == "validate_latex":
            formulas = args.get("formulas", [])
            results = []
            for f in formulas:
                omml = converter.latex_to_omml(f)
                results.append({"formula": f, "valid": omml is not None})
            return json.dumps({"results": results})

        elif name == "validate_svg":
            svg = args.get("svg_code", "").strip()
            try:
                elem = etree.fromstring(svg.encode("utf-8"))
                has_viewbox = "viewBox" in elem.attrib or "viewbox" in elem.attrib
                return json.dumps({
                    "valid_xml": True,
                    "tag": elem.tag,
                    "has_viewbox": has_viewbox,
                    "width": elem.attrib.get("width"),
                    "height": elem.attrib.get("height")
                })
            except Exception as e:
                return json.dumps({"valid_xml": False, "error": str(e)})

        elif name == "build_docx_output":
            if not self.markdown:
                return json.dumps({"error": "No markdown content to compile."})
            os.makedirs(self.svgs_dir, exist_ok=True)
            self.exported_svgs = converter.markdown_to_docx(
                self.markdown,
                self.docx_path,
                output_dir=self.svgs_dir
            )
            return json.dumps({
                "status": "success",
                "docx_path": self.docx_path,
                "exported_svgs_count": len(self.exported_svgs),
                "svg_files": [os.path.basename(s) for s in self.exported_svgs]
            })

        elif name == "list_skills":
            avail = skills.list_skills()
            summary = [
                {"name": s["name"], "scope": s["scope"], "description": s["description"]}
                for s in avail
            ]
            return json.dumps({"skills_count": len(summary), "skills": summary})

        elif name == "use_skill":
            s_name = args.get("skill_name", "").strip()
            sk = skills.get_skill(s_name)
            if sk:
                if s_name not in self.active_skills_loaded:
                    self.active_skills_loaded.append(s_name)
                return json.dumps({
                    "status": "loaded",
                    "skill_name": sk["name"],
                    "scope": sk["scope"],
                    "description": sk["description"],
                    "instructions": sk["instructions"]
                })
            return json.dumps({"error": f"Skill '{s_name}' not found. Call 'list_skills' for available skills."})

        elif name == "call_mcp_tool":
            server_id = args.get("server_id", "")
            t_name = args.get("tool_name", "")
            t_args = args.get("arguments") or {}
            try:
                out = mcp_manager.mcp_mgr.call_tool(server_id, t_name, t_args)
                return json.dumps({"status": "success", "output": out})
            except Exception as e:
                return json.dumps({"status": "error", "message": str(e)})

        elif name in self.mcp_routing:
            server_id, orig_tool = self.mcp_routing[name]
            try:
                out = mcp_manager.mcp_mgr.call_tool(server_id, orig_tool, args)
                return json.dumps({"status": "success", "output": out})
            except Exception as e:
                return json.dumps({"status": "error", "message": str(e)})

        return json.dumps({"error": f"Unknown tool: {name}"})

    def run_agent_workflow(
        self,
        user_prompt: str = None,
        max_iterations: int = 10,
        progress_callback: Optional[Callable[[str, float], None]] = None
    ) -> Dict[str, Any]:
        """
        Run multi-turn agent loop:
        1. Agent sees page images & requirements
        2. Agent produces markdown, validates math/SVGs
        3. Agent calls edit_markdown_snippet / validate_latex as needed
        4. Compiles DOCX
        """
        system_instruction = f"""Turn pdf to markdown
- write formulas in latex
- made visuals like shapes in svg

Document has {len(self.pages)} page(s).
Available tools:
- `view_page_image`: view page images (1 to {len(self.pages)}).
- `update_markdown`: save complete structured markdown content.
- `edit_markdown_snippet`: search-and-replace snippet in markdown.
- `validate_latex`: test LaTeX formulas for valid Word OMML conversion.
- `validate_svg`: check SVG vector XML and viewBox.
- `build_docx_output`: build final Word (.docx) document.
- `list_skills`: list specialized domain skills (math formulas, SVG geometry, page layout, tables, Persian RTL).
- `use_skill`: read and apply specialized domain guidelines and rules.
- MCP Server Tools: external tools from connected Model Context Protocol servers ({', '.join([s['name'] for s in mcp_manager.mcp_mgr.get_configured_servers()])}).

MANDATORY SKILL EXECUTION:
You MUST read relevant skills via `use_skill` before or during document extraction:
- If document has mathematical formulas or equations: call `use_skill` with skill_name="math-formula-extractor".
- If document has geometric shapes, diagrams, vectors, or coordinate graphs: call `use_skill` with skill_name="svg-diagram-architect".
- For exam layout (headers, question numbers, scores): call `use_skill` with skill_name="page-layout-architect".
- If document has Persian or Arabic text/numbers: call `use_skill` with skill_name="persian-rtl-typesetter".
- If document has real data tables or matrices: call `use_skill` with skill_name="table-and-matrix-styler".
- Call `list_skills` to discover any additional user skills.

Strict Guidelines:
1. Exact content extraction ONLY. Extract ONLY what is on the pages.
2. DO NOT add any "Visual Descriptions", "Visuals Explanation", recreation guides, or commentary at the bottom.
3. Write ONLY pure standard Markdown (MD).
4. DO NOT use HTML tags (NO <br>, NO <div>, NO <span>, NO <b>, NO <u>, NO <table>).
5. XML is ONLY permitted inside SVG blocks (```xml or ```svg). Outside of SVG code blocks, HTML/XML is strictly forbidden.
6. Write all formulas in LaTeX ($...$ and $$...$$).
7. Made visuals like shapes, diagrams, and figures in clean SVG blocks (```xml or ```svg) directly in their place.
8. NEVER WRAP EXAM PAGES OR QUESTIONS IN A TABLE. Do not replicate the physical exam 3-column grid (`ردیف / سوال / بارم`) as a table. Write questions as standard sequential Markdown (`**۱-** سوال... (۱.۵ نمره)`). Tables are strictly for actual tabular data.
9. Use `update_markdown` or `edit_markdown_snippet` to update content.
10. Use `build_docx_output` to produce final Word docx.
"""

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_instruction}
        ]

        # Initial prompt includes page 1 image
        first_page_buf = io.BytesIO()
        self.pages[0].save(first_page_buf, format="JPEG", quality=85)
        first_page_b64 = base64.b64encode(first_page_buf.getvalue()).decode()

        prompt_text = user_prompt or """Turn pdf to markdown
- write formulas in latex
- made visuals like shapes in svg

Requirements:
- Read relevant skills (`use_skill`) for formulas, visuals, page layout, and language rules before outputting.
- Exact content extraction ONLY.
- DO NOT add any visual descriptions or explanations at the end.
- Write only in pure Markdown without HTML tags (no <br>). XML is only allowed inside SVG blocks.
- NEVER put the whole exam page or questions inside a table. Extract questions as standard Markdown headings and lists.
"""
        
        user_content = [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{first_page_b64}"}}
        ]
        
        # If multipage, also attach remaining pages up front so model has full document vision immediately
        for p_idx in range(1, len(self.pages)):
            p_buf = io.BytesIO()
            self.pages[p_idx].save(p_buf, format="JPEG", quality=85)
            p_b64 = base64.b64encode(p_buf.getvalue()).decode()
            user_content.append({"type": "text", "text": f"Page {p_idx + 1}:"})
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{p_b64}"}})

        messages.append({"role": "user", "content": user_content})

        for iteration in range(max_iterations):
            pct = 0.1 + (0.8 * (iteration / max_iterations))
            if progress_callback:
                progress_callback(f"Agent thinking (iteration {iteration + 1})...", pct)

            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.all_tools,
                tool_choice="auto",
                max_tokens=4096
            )

            # Accumulate token usage
            usage = getattr(resp, "usage", None)
            if usage:
                self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            else:
                self.prompt_tokens += (len(str(messages)) // 4) + (len(self.pages) * 800)
                self.completion_tokens += len(resp.choices[0].message.content or "") // 4

            choice = resp.choices[0]
            msg = choice.message
            messages.append(msg)

            if msg.content:
                self.steps.append({
                    "step": len(self.steps) + 1,
                    "type": "thought",
                    "title": f"ITERATION {iteration + 1} // AGENT REASONING",
                    "description": msg.content.strip(),
                    "status": "info",
                    "time": time.strftime("%H:%M:%S")
                })

            # If no tool calls and finish_reason is stop, agent is done
            if not msg.tool_calls:
                # If markdown was generated in message text, capture it
                if msg.content and not self.markdown:
                    self.markdown = msg.content
                break

            # Process all tool calls
            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args_raw = tool_call.function.arguments or "{}"
                try:
                    fn_args = json.loads(fn_args_raw)
                except Exception:
                    fn_args = {}

                if progress_callback:
                    tool_desc = fn_name
                    if fn_name == "view_page_image":
                        tool_desc = f"Viewing page {fn_args.get('page_number', '')}"
                    elif fn_name == "validate_latex":
                        tool_desc = f"Validating {len(fn_args.get('formulas', []))} LaTeX formulas"
                    elif fn_name == "validate_svg":
                        tool_desc = "Validating SVG shape XML"
                    elif fn_name == "update_markdown":
                        tool_desc = "Saving generated Markdown"
                    elif fn_name == "edit_markdown_snippet":
                        tool_desc = "Applying edit to Markdown"
                    elif fn_name == "build_docx_output":
                        tool_desc = "Building final Word DOCX with Alt+= & SVGs"
                    elif fn_name == "list_skills":
                        tool_desc = "Inspecting available Agent Skills"
                    elif fn_name == "use_skill":
                        tool_desc = f"Applying skill '{fn_args.get('skill_name', '')}'"
                    elif fn_name == "call_mcp_tool":
                        tool_desc = f"Calling MCP tool '{fn_args.get('tool_name', '')}'"
                    elif fn_name.startswith("mcp__"):
                        tool_desc = f"Calling MCP tool '{fn_name}'"
                    progress_callback(f"Agent tool: {tool_desc}", pct)

                tool_result = self.execute_tool(fn_name, fn_args)

                # Record structured step
                step_title = f"TOOL // {fn_name.upper()}"
                step_type = "tool_call"
                step_desc = ""

                if fn_name == "use_skill":
                    sk_name = fn_args.get("skill_name", "")
                    step_type = "skill"
                    step_title = f"SKILL LOADED // {sk_name.upper()}"
                    step_desc = f"Loaded specialized guidelines and constraints for '{sk_name}'."
                elif fn_name == "list_skills":
                    step_type = "skill"
                    step_title = "SKILLS DISCOVERY"
                    step_desc = "Probed available domain skills from local and global directories."
                elif fn_name == "validate_latex":
                    step_title = "LATEX FORMULAS VALIDATED"
                    step_desc = f"Validated {len(fn_args.get('formulas', []))} LaTeX expression(s) for Word OMML conversion."
                elif fn_name == "validate_svg":
                    step_title = "SVG VECTOR XML VALIDATED"
                    step_desc = "Checked SVG XML markup, viewBox coordinates, and shape tags."
                elif fn_name == "view_page_image":
                    step_title = f"PAGE {fn_args.get('page_number', 1)} VISUAL INSPECTED"
                    step_desc = "Inspected high-resolution visual rendering of the target page."
                elif fn_name == "update_markdown":
                    step_title = "MARKDOWN CONTENT COMMITTED"
                    step_desc = f"Saved structured Markdown document ({len(self.markdown)} characters)."
                elif fn_name == "edit_markdown_snippet":
                    step_title = "MARKDOWN SNIPPET REPLACED"
                    step_desc = f"Replaced snippet: \"{fn_args.get('search', '')[:60]}\""
                elif fn_name == "build_docx_output":
                    step_type = "output"
                    step_title = "WORD DOCX COMPILED"
                    step_desc = "Synthesized native Word (.docx) document with OMML math and vector SVGs."
                elif fn_name.startswith("mcp__") or fn_name == "call_mcp_tool":
                    step_type = "mcp"
                    step_title = f"MCP TOOL // {fn_name.upper()}"
                    step_desc = f"Executed external Model Context Protocol tool with arguments: {json.dumps(fn_args, ensure_ascii=False)[:100]}"
                else:
                    step_desc = f"Executed tool {fn_name}"

                self.steps.append({
                    "step": len(self.steps) + 1,
                    "type": step_type,
                    "title": step_title,
                    "description": step_desc,
                    "tool": fn_name,
                    "args": fn_args,
                    "result": tool_result[:400] if len(tool_result) > 400 else tool_result,
                    "status": "warning" if "error" in tool_result.lower() else "success",
                    "time": time.strftime("%H:%M:%S")
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result
                })

        # Always ensure markdown content exists; fallback if agent produced none
        if not self.markdown:
            # Gather any text content from assistant messages
            text_parts = [m.content for m in messages if getattr(m, "role", None) == "assistant" and getattr(m, "content", None)]
            self.markdown = "\n\n".join(text_parts).strip() if text_parts else f"# {self.base_name}\n\nNo text extracted."

        # Compile docx if not already built or file missing
        if not os.path.exists(self.docx_path):
            self.execute_tool("build_docx_output", {})

        # If file STILL does not exist (e.g. empty markdown), create minimal valid document
        if not os.path.exists(self.docx_path):
            converter.markdown_to_docx(self.markdown, self.docx_path, output_dir=self.svgs_dir)

        # Save markdown file
        md_path = os.path.join(self.output_dir, f"{self.base_name}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(self.markdown)

        # Add completion step
        self.steps.append({
            "step": len(self.steps) + 1,
            "type": "output",
            "title": "CONVERSION PIPELINE COMPLETED",
            "description": f"Generated final Word document ({os.path.basename(self.docx_path)}) with {len(self.exported_svgs)} extracted SVG shape(s).",
            "status": "success",
            "time": time.strftime("%H:%M:%S")
        })

        return {
            "success": True,
            "base_name": self.base_name,
            "total_pages": len(self.pages),
            "docx_path": self.docx_path,
            "docx_name": os.path.basename(self.docx_path),
            "md_path": md_path,
            "md_name": os.path.basename(md_path),
            "zip_path": getattr(self, "zip_path", None),
            "zip_name": getattr(self, "zip_name", None),
            "svgs": [os.path.basename(s) for s in self.exported_svgs],
            "svg_paths": self.exported_svgs,
            "markdown_content": self.markdown,
            "skills_used": self.active_skills_loaded,
            "steps": self.steps,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens
        }
