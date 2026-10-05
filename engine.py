"""
Core processing engine:
- Reads secrets from .env (OPENAI_API_BASE_URL, OPENAI_API_KEY, AI_DEFAULT_MODEL, OUTPUT_PATH)
- Converts PDF or image inputs to base64
- Sends pages to AI model for structured Markdown + LaTeX + SVGs
- Runs converter to generate DOCX, extract SVGs, and package ZIP
"""

import os
import io
import re
import json
import base64
import zipfile
import shutil
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional
import dotenv
import openai
from PIL import Image
import pypdfium2 as pdfium

from converter import markdown_to_docx
import db

# Load environment configuration
dotenv.load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
ENV_CONFIG = dotenv.dotenv_values(BASE_DIR / ".env")

def get_config(key: str, default: str = "") -> str:
    val = db.get_setting(key)
    if val:
        return val
    return os.getenv(key) or ENV_CONFIG.get(key) or default

OPENAI_API_BASE_URL = get_config("OPENAI_API_BASE_URL", "http://localhost:20128/v1")
OPENAI_API_KEY = get_config("OPENAI_API_KEY", "")
AI_DEFAULT_MODEL = get_config("AI_DEFAULT_MODEL", "ag/gemini-3.8-flash-high")
OUTPUT_DIR = get_config("OUTPUT_PATH", str(BASE_DIR / "output"))

MAIN_PROMPT = """Turn pdf to markdown
- write formulas in latex
- made visuals like shapes in svg
"""

SYSTEM_PROMPT = """Turn pdf to markdown
- write formulas in latex
- made visuals like shapes in svg

Strict Guidelines:
1. Exact content extraction ONLY. Output MUST contain ONLY the exact content from the document.
2. DO NOT add any "Visual Descriptions", "Visuals Explanation", recreation instructions, summary, commentary, or extra notes at the end or anywhere in the document.
3. Output MUST be pure standard Markdown (MD).
4. DO NOT use HTML tags (NO <br>, NO <div>, NO <span>, NO <b>, NO <u>, NO <font>, NO <table>).
5. For line breaks and paragraphs, use standard markdown blank lines and spaces.
6. XML / HTML is ONLY permitted inside SVG code blocks (```xml or ```svg). Outside of SVG visuals, never use HTML/XML tags.
7. All formulas and math expressions must be written in standard LaTeX ($...$ for inline, $$...$$ for display equations).
8. Made visuals like shapes, diagrams, coordinate planes, and geometric figures in standalone SVG blocks (```xml or ```svg) directly where they appear in the content.
9. NEVER wrap the whole exam or questions list in a table. Do NOT create a 3-column table (ردیف / سوال / بارم). Write questions as standard sequential Markdown (**۱-** سوال... بارم: ۱.۵). Tables are strictly for genuine tabular data only.
10. Persian & Arabic: retain Persian/Arabic question numbering (e.g. ۱-, ۲-) and keep embedded formulas standard LTR LaTeX.
"""

def get_ai_client():
    url = db.get_setting("OPENAI_API_BASE_URL") or OPENAI_API_BASE_URL
    key = db.get_setting("OPENAI_API_KEY") or OPENAI_API_KEY or "dummy_key"
    return openai.OpenAI(
        base_url=url,
        api_key=key
    )

def extract_images_from_file(file_path: str) -> List[Image.Image]:
    """Convert input file (PDF or Image) into a list of PIL Images."""
    ext = os.path.splitext(file_path)[1].lower()
    images = []
    
    if ext == ".pdf":
        pdf = pdfium.PdfDocument(file_path)
        for page_idx in range(len(pdf)):
            page = pdf[page_idx]
            # 2.0 scale gives ~144-150 DPI for crisp visual and text reading
            pil_image = page.render(scale=2.0).to_pil()
            images.append(pil_image)
    elif ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"]:
        img = Image.open(file_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        images.append(img)
    else:
        raise ValueError(f"Unsupported file format: {ext}")
        
    return images

def pil_to_base64_jpeg(img: Image.Image, quality: int = 85) -> str:
    """Convert PIL image to base64 JPEG string."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def process_page_with_ai(img: Image.Image, page_num: int, total_pages: int, model: str = None) -> tuple:
    """Send single page image to AI model to get structured markdown and token usage."""
    client = get_ai_client()
    use_model = model or AI_DEFAULT_MODEL
    b64 = pil_to_base64_jpeg(img)
    
    user_prompt = f"""Turn pdf to markdown (page {page_num} of {total_pages})
- write formulas in latex
- made visuals like shapes in svg

Note:
- Exact content extraction ONLY.
- DO NOT write any visual descriptions or explanations at the end.
- Write ONLY in pure markdown without HTML tags (no <br>). XML is ONLY allowed inside SVG blocks.
"""
    
    resp = client.chat.completions.create(
        model=use_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                ]
            }
        ],
        max_tokens=4096
    )
    content = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    if usage:
        p_tok = getattr(usage, "prompt_tokens", 0) or 0
        c_tok = getattr(usage, "completion_tokens", 0) or 0
    else:
        p_tok = (len(user_prompt) // 4) + 1000
        c_tok = len(content) // 4
    return content, p_tok, c_tok

def process_file_to_docx(
    file_path: str,
    output_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
    model: Optional[str] = None
) -> Dict[str, Any]:
    """
    End-to-end pipeline:
    1. Extract page images
    2. Run AI digitization
    3. Generate structured markdown
    4. Convert to docx with OMML LaTeX & SVG shapes
    5. Package into ZIP
    """
    target_out_dir = output_dir or OUTPUT_DIR
    os.makedirs(target_out_dir, exist_ok=True)
    
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    if progress_callback:
        progress_callback("Reading file and rendering pages...", 0.1)
        
    images = extract_images_from_file(file_path)
    total_pages = len(images)
    
    page_markdowns = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    for idx, img in enumerate(images):
        page_num = idx + 1
        pct = 0.15 + (0.65 * (idx / total_pages))
        if progress_callback:
            progress_callback(f"Digitizing page {page_num}/{total_pages} with AI...", pct)
            
        md_part, p_tok, c_tok = process_page_with_ai(img, page_num, total_pages, model=model)
        page_markdowns.append(md_part)
        total_prompt_tokens += p_tok
        total_completion_tokens += c_tok
        
    if progress_callback:
        progress_callback("Combining pages and formatting LaTeX equations...", 0.82)
        
    # Combine with page breaks
    combined_md = "\n\n---\n\n".join(page_markdowns)
    
    # Save markdown file
    md_filename = f"{base_name}.md"
    md_path = os.path.join(target_out_dir, md_filename)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(combined_md)
        
    if progress_callback:
        progress_callback("Extracting SVGs and generating Word (.docx)...", 0.90)
        
    docx_filename = f"{base_name}.docx"
    docx_path = os.path.join(target_out_dir, docx_filename)
    
    # Subfolder for this conversion's SVGs
    svg_subfolder = os.path.join(target_out_dir, f"{base_name}_svgs")
    os.makedirs(svg_subfolder, exist_ok=True)
    
    exported_svgs = markdown_to_docx(combined_md, docx_path, output_dir=svg_subfolder)
    
    # Create ZIP bundle with docx, md, and all svgs
    zip_filename = f"{base_name}_bundle.zip"
    zip_path = os.path.join(target_out_dir, zip_filename)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(docx_path, arcname=docx_filename)
        z.write(md_path, arcname=md_filename)
        for svg_f in exported_svgs:
            z.write(svg_f, arcname=f"svgs/{os.path.basename(svg_f)}")
            
    if progress_callback:
        progress_callback("Completed!", 1.0)
        
    return {
        "success": True,
        "base_name": base_name,
        "total_pages": total_pages,
        "docx_path": docx_path,
        "docx_name": docx_filename,
        "md_path": md_path,
        "md_name": md_filename,
        "zip_path": zip_path,
        "zip_name": zip_filename,
        "svgs": [os.path.basename(s) for s in exported_svgs],
        "svg_paths": exported_svgs,
        "markdown_content": combined_md,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens
    }
