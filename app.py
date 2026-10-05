"""
DocuMorph AI Application
- Web interface (FastAPI + Light Theme UI)
- Autonomous AI Agent with Tool Calling (agent.py)
- Direct CLI converter support
- Reads configuration and secrets from .env
"""

import os
import sys
import webbrowser
import threading
import argparse
import zipfile
import asyncio
import uuid
import time
import shutil
import concurrent.futures
from pathlib import Path
from typing import Optional, Dict, Any, List
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

import engine
import agent
import converter
import db
import skills
import mcp_manager
import tokenizer

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = Path(engine.OUTPUT_DIR)

app = FastAPI(title="DocuMorph AI")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Background Task Registry & Parallel Job Queue
# taskId -> { status, percent, message, events: [], result, error }
TASKS: Dict[str, Dict[str, Any]] = {}
TASK_LISTENERS: Dict[str, List[asyncio.Queue]] = {}

MAIN_EVENT_LOOP: Optional[asyncio.AbstractEventLoop] = None
EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
ACTIVE_FUTURES: Dict[str, concurrent.futures.Future] = {}
QUEUE_LOCK = threading.Lock()
DISPATCHER_THREAD: Optional[threading.Thread] = None
STOP_DISPATCHER = False

def get_or_create_listeners(task_id: str) -> List[asyncio.Queue]:
    if task_id not in TASK_LISTENERS:
        TASK_LISTENERS[task_id] = []
    return TASK_LISTENERS[task_id]

def broadcast_task_event(task_id: str, event_data: dict, loop: Optional[asyncio.AbstractEventLoop] = None):
    task = TASKS.get(task_id)
    if not task:
        db_job = db.get_job(task_id)
        if db_job:
            TASKS[task_id] = {
                "id": task_id,
                "status": db_job["status"],
                "percent": db_job.get("percent", 0),
                "message": db_job.get("message", ""),
                "events": [],
                "result": db_job.get("result"),
                "error": db_job.get("error")
            }
            task = TASKS[task_id]

    if task:
        task["events"].append(event_data)
        if event_data.get("type") == "progress":
            task["percent"] = event_data.get("percent", 0)
            task["message"] = event_data.get("message", "")
            db.update_job(task_id, percent=task["percent"], message=task["message"])
        elif event_data.get("type") == "done":
            task["status"] = "done"
            task["percent"] = 100
            task["message"] = "Completed!"
            task["result"] = event_data.get("result")
            db.update_job(task_id, status="done", percent=100, message="Completed!", result=event_data.get("result"), finished_at=time.time())
        elif event_data.get("type") == "error":
            task["status"] = "error"
            task["error"] = event_data.get("message", "Error")
            db.update_job(task_id, status="error", message=task["error"], error=task["error"], finished_at=time.time())

    target_loop = loop or MAIN_EVENT_LOOP
    listeners = TASK_LISTENERS.get(task_id, [])
    for q in list(listeners):
        if target_loop and not target_loop.is_closed():
            target_loop.call_soon_threadsafe(q.put_nowait, event_data)
        else:
            q.put_nowait(event_data)

def execute_conversion_job(task_id: str, file_path: str, filename: str, mode: str, model: str):
    """Worker task executed inside ThreadPoolExecutor."""
    start_time = time.time()
    def progress_cb(message: str, pct: float):
        elapsed = round(time.time() - start_time, 1)
        event = {
            "type": "progress",
            "message": message,
            "percent": int(pct * 100),
            "elapsed_sec": elapsed,
            "formatted_time": db.format_duration(elapsed)
        }
        broadcast_task_event(task_id, event)

    try:
        progress_cb(f"Processing '{filename}'...", 0.05)
        out_dir = Path(db.get_setting("OUTPUT_PATH", str(OUTPUT_DIR)))
        os.makedirs(out_dir, exist_ok=True)
        chosen_model = model or db.get_setting("AI_DEFAULT_MODEL", engine.AI_DEFAULT_MODEL)

        if mode == "agent":
            doc_agent = agent.DocuMorphAgent(
                str(file_path),
                model=chosen_model,
                output_dir=str(out_dir)
            )
            res = doc_agent.run_agent_workflow(progress_callback=progress_cb)

            if not os.path.exists(res["docx_path"]):
                converter.markdown_to_docx(res.get("markdown_content", ""), res["docx_path"], output_dir=doc_agent.svgs_dir)

            zip_filename = f"{res['base_name']}_bundle.zip"
            zip_path = os.path.join(str(out_dir), zip_filename)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                if os.path.exists(res["docx_path"]):
                    z.write(res["docx_path"], arcname=res["docx_name"])
                if os.path.exists(res["md_path"]):
                    z.write(res["md_path"], arcname=res["md_name"])
                for svg_f in res.get("svg_paths", []):
                    if os.path.exists(svg_f):
                        z.write(svg_f, arcname=f"svgs/{os.path.basename(svg_f)}")
            res["zip_path"] = zip_path
            res["zip_name"] = zip_filename
        else:
            res = engine.process_file_to_docx(
                str(file_path),
                output_dir=str(out_dir),
                progress_callback=progress_cb,
                model=chosen_model
            )

        finish_time = time.time()
        duration_sec = round(finish_time - start_time, 2)
        prompt_tokens = res.get("prompt_tokens", 0)
        completion_tokens = res.get("completion_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens
        cost_usd = db.calculate_cost(chosen_model, prompt_tokens, completion_tokens)
        ctx_usage = db.calculate_context_usage(chosen_model, prompt_tokens, completion_tokens)

        res["duration_sec"] = duration_sec
        res["formatted_duration"] = db.format_duration(duration_sec)
        res["cost_usd"] = cost_usd
        res["formatted_cost"] = db.format_cost(cost_usd)
        res["prompt_tokens"] = prompt_tokens
        res["completion_tokens"] = completion_tokens
        res["total_tokens"] = total_tokens
        res["context_limit"] = ctx_usage["context_limit"]
        res["context_percent"] = ctx_usage["context_percent"]
        res["formatted_context_usage"] = ctx_usage["formatted_summary"]
        res["context_usage"] = ctx_usage

        svg_data = []
        for svg_p in res.get("svg_paths", []):
            try:
                with open(svg_p, "r", encoding="utf-8") as sf:
                    svg_data.append({
                        "name": os.path.basename(svg_p),
                        "content": sf.read()
                    })
            except Exception:
                pass
        res["svg_data"] = svg_data

        # Add initial conversation entry to SQLite chat history with timing & cost
        db.add_chat_message(
            res["base_name"],
            "assistant",
            f"Extracted document '{filename}' in {res['formatted_duration']} (est. {res['formatted_cost']}, {res.get('total_pages', 1)} pages, {len(svg_data)} SVGs)."
        )

        db.update_job(
            task_id,
            status="done",
            percent=100,
            message=f"Completed in {res['formatted_duration']} ({res['formatted_cost']})",
            result=res,
            finished_at=finish_time,
            duration_sec=duration_sec,
            cost_usd=cost_usd,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens
        )

        broadcast_task_event(task_id, {
            "type": "done",
            "result": res,
            "duration_sec": duration_sec,
            "formatted_duration": res["formatted_duration"],
            "cost_usd": cost_usd,
            "formatted_cost": res["formatted_cost"],
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "context_usage": ctx_usage
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        finish_time = time.time()
        duration_sec = round(finish_time - start_time, 2)
        db.update_job(task_id, status="error", message=str(e), error=str(e), finished_at=finish_time, duration_sec=duration_sec)
        broadcast_task_event(task_id, {"type": "error", "message": str(e), "duration_sec": duration_sec})
    finally:
        with QUEUE_LOCK:
            ACTIVE_FUTURES.pop(task_id, None)

def queue_dispatcher_loop():
    """Background dispatcher pulling queued jobs and running them up to MAX_CONCURRENT_JOBS in parallel."""
    global EXECUTOR
    max_workers = int(db.get_setting("MAX_CONCURRENT_JOBS", "3"))
    EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="documorph_worker")

    while not STOP_DISPATCHER:
        try:
            current_max = int(db.get_setting("MAX_CONCURRENT_JOBS", "3"))
            with QUEUE_LOCK:
                active_count = len(ACTIVE_FUTURES)
                available_slots = max(0, current_max - active_count)

            if available_slots > 0:
                next_job = db.get_next_queued_job()
                if next_job:
                    job_id = next_job["id"]
                    file_path = next_job["file_path"]
                    filename = next_job["filename"]
                    mode = next_job["mode"]
                    model = next_job["model"]

                    with QUEUE_LOCK:
                        db.update_job(job_id, status="processing", message="Worker assigned. Processing...", started_at=time.time())
                        if job_id in TASKS:
                            TASKS[job_id]["status"] = "processing"
                            TASKS[job_id]["message"] = "Worker assigned. Processing..."
                        broadcast_task_event(job_id, {"type": "progress", "message": "Worker assigned. Processing...", "percent": 5})

                        future = EXECUTOR.submit(execute_conversion_job, job_id, file_path, filename, mode, model)
                        ACTIVE_FUTURES[job_id] = future
                    continue

            time.sleep(0.4)
        except Exception as e:
            time.sleep(1.0)

@app.on_event("startup")
async def on_startup():
    global MAIN_EVENT_LOOP, DISPATCHER_THREAD
    MAIN_EVENT_LOOP = asyncio.get_running_loop()
    if DISPATCHER_THREAD is None or not DISPATCHER_THREAD.is_alive():
        DISPATCHER_THREAD = threading.Thread(target=queue_dispatcher_loop, daemon=True)
        DISPATCHER_THREAD.start()

class TokenEstimateRequest(BaseModel):
    text: Optional[str] = ""
    model: Optional[str] = None
    page_count: Optional[int] = 0
    expected_output_tokens: Optional[int] = None

class AgentEditRequest(BaseModel):
    base_name: str
    prompt: str
    markdown: str
    model: Optional[str] = None

class RecompileRequest(BaseModel):
    base_name: str
    markdown: str

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    current_model = db.get_setting("AI_DEFAULT_MODEL", engine.AI_DEFAULT_MODEL)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "default_model": current_model
        }
    )

@app.get("/api/models")
async def get_models():
    """Retrieve dynamic list of available models from OpenAI-compatible API endpoint and cache context limits."""
    current_default = db.get_setting("AI_DEFAULT_MODEL", engine.AI_DEFAULT_MODEL)
    try:
        client = engine.get_ai_client()
        resp = client.models.list()
        raw_models = []
        detailed_models = []

        if hasattr(resp, "data") and resp.data:
            for m in resp.data:
                mid = getattr(m, "id", None)
                if not mid:
                    continue
                raw_models.append(mid)

                # Extract context length from API response
                m_dump = m.model_dump() if hasattr(m, "model_dump") else getattr(m, "__dict__", {})
                caps = m_dump.get("capabilities") or {}
                ctx = (
                    m_dump.get("context_length")
                    or caps.get("contextWindow")
                    or m_dump.get("max_model_len")
                    or 0
                )
                max_out = caps.get("maxOutput") or m_dump.get("max_completion_tokens") or None

                if ctx and int(ctx) > 0:
                    db.cache_model_metadata(mid, int(ctx), int(max_out) if max_out else None)

                detailed_models.append({
                    "id": mid,
                    "context_window": int(ctx) if ctx else db.get_model_context_window(mid),
                    "max_output": max_out
                })

        # Deduplicate preserving order
        seen = set()
        model_list = []
        if current_default:
            model_list.append(current_default)
            seen.add(current_default)

        for mid in raw_models:
            if mid not in seen:
                seen.add(mid)
                model_list.append(mid)

        models_map = {m["id"]: m for m in detailed_models}

        return {
            "success": True,
            "models": model_list,
            "models_detail": detailed_models,
            "models_map": models_map,
            "current_model": current_default,
            "count": len(model_list),
            "source": "api"
        }
    except Exception as e:
        fallback = [current_default]
        unique_fallback = list(dict.fromkeys([m for m in fallback if m]))
        return {
            "success": False,
            "models": unique_fallback,
            "models_detail": [{"id": m, "context_window": db.get_model_context_window(m)} for m in unique_fallback],
            "models_map": {m: {"id": m, "context_window": db.get_model_context_window(m)} for m in unique_fallback},
            "current_model": current_default,
            "count": len(unique_fallback),
            "source": "fallback",
            "error": str(e)
        }

@app.post("/api/convert")
async def api_convert(
    files: Optional[List[UploadFile]] = File(None),
    file: Optional[UploadFile] = File(None),
    model: str = Form(None),
    mode: str = Form("agent")
):
    """Enqueue one or multiple files into the background job queue for parallel conversion."""
    upload_list = []
    if files:
        upload_list.extend(files)
    if file and file not in upload_list:
        upload_list.append(file)

    if not upload_list:
        raise HTTPException(status_code=400, detail="No files provided for conversion.")

    temp_input_dir = BASE_DIR / "input"
    os.makedirs(temp_input_dir, exist_ok=True)

    enqueued_tasks = []
    for f in upload_list:
        task_id = uuid.uuid4().hex
        file_path = temp_input_dir / f"{task_id}_{f.filename}"

        content = await f.read()
        with open(file_path, "wb") as out_f:
            out_f.write(content)

        # Enqueue in persistent SQLite jobs table
        db.create_job(task_id, f.filename, str(file_path), mode=mode, model=model)

        # Register in memory for SSE
        TASKS[task_id] = {
            "id": task_id,
            "filename": f.filename,
            "status": "queued",
            "percent": 0,
            "message": "In queue waiting for available worker...",
            "events": [],
            "result": None,
            "error": None,
            "created_at": time.time()
        }

        enqueued_tasks.append({
            "task_id": task_id,
            "filename": f.filename,
            "status": "queued"
        })

    return JSONResponse(content={
        "success": True,
        "tasks": enqueued_tasks,
        "count": len(enqueued_tasks),
        "task_id": enqueued_tasks[0]["task_id"]
    })

@app.get("/api/task/{task_id}/status")
async def get_task_status(task_id: str):
    """Poll task status, checking memory and falling back to SQLite."""
    task = TASKS.get(task_id)
    if not task:
        db_job = db.get_job(task_id)
        if not db_job:
            raise HTTPException(status_code=404, detail="Task not found")
        task = {
            "id": db_job["id"],
            "status": db_job["status"],
            "percent": db_job.get("percent", 0),
            "message": db_job.get("message", ""),
            "result": db_job.get("result"),
            "error": db_job.get("error")
        }
    return JSONResponse(content={
        "id": task["id"],
        "status": task["status"],
        "percent": task.get("percent", 0),
        "message": task.get("message", ""),
        "result": task.get("result"),
        "error": task.get("error")
    })

@app.get("/api/queue")
async def get_queue_status():
    """Return active parallel worker count, max concurrent setting, and jobs list with live duration & price."""
    with QUEUE_LOCK:
        active_count = len(ACTIVE_FUTURES)
    max_workers = int(db.get_setting("MAX_CONCURRENT_JOBS", "3"))
    jobs = db.list_jobs(limit=30)
    now = time.time()
    job_items = []
    for j in jobs:
        st = j.get("status")
        if st == "processing" and j.get("started_at"):
            duration = round(now - j["started_at"], 1)
        else:
            duration = j.get("duration_sec") or 0.0

        cost = j.get("cost_usd") or 0.0
        p_tok = j.get("prompt_tokens") or 0
        c_tok = j.get("completion_tokens") or 0
        t_tok = j.get("total_tokens") or (p_tok + c_tok)
        ctx_info = db.calculate_context_usage(j.get("model", ""), p_tok, c_tok)

        job_items.append({
            "id": j["id"],
            "filename": j["filename"],
            "status": j["status"],
            "percent": j["percent"],
            "message": j["message"],
            "mode": j["mode"],
            "model": j.get("model", ""),
            "duration_sec": duration,
            "formatted_duration": db.format_duration(duration),
            "cost_usd": cost,
            "formatted_cost": db.format_cost(cost),
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "total_tokens": t_tok,
            "context_limit": ctx_info["context_limit"],
            "context_percent": ctx_info["context_percent"],
            "formatted_tokens": ctx_info["formatted_tokens"],
            "formatted_context": ctx_info["formatted_summary"],
            "created_at": j["created_at"],
            "formatted_time": time.strftime("%H:%M:%S", time.localtime(j["created_at"]))
        })

    token_agg = db.get_total_tokens_consumed()
    return JSONResponse(content={
        "active_workers": active_count,
        "max_concurrent": max_workers,
        "total_jobs": len(jobs),
        "total_tokens_consumed": token_agg["total_tokens"],
        "formatted_total_tokens": f"{token_agg['total_tokens']:,}",
        "jobs": job_items
    })

@app.get("/api/task/{task_id}/events")
async def stream_task_events(task_id: str):
    """SSE endpoint for live progress. Can disconnect/reconnect freely."""
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    q = asyncio.Queue()
    listeners = get_or_create_listeners(task_id)
    listeners.append(q)

    # Replay historical events to reconnecting client
    for past_ev in task.get("events", []):
        q.put_nowait(past_ev)

    async def event_generator():
        try:
            while True:
                item = await q.get()
                import json
                yield {"data": json.dumps(item)}
                if item.get("type") in ("done", "error"):
                    break
        finally:
            if q in listeners:
                listeners.remove(q)

    return EventSourceResponse(event_generator())

@app.post("/api/agent-edit")
async def api_agent_edit(req: AgentEditRequest):
    """Allow AI agent to edit existing markdown via user prompt and log to SQLite chat history."""
    try:
        client = engine.get_ai_client()
        model = req.model or engine.AI_DEFAULT_MODEL

        # Log user request
        if req.base_name:
            db.add_chat_message(req.base_name, "user", req.prompt)

        # Detect if user requested specific skill or inject domain guidance
        skill_guidance = ""
        for sk in skills.list_skills():
            if sk["name"] in req.prompt:
                full_sk = skills.get_skill(sk["name"])
                if full_sk:
                    skill_guidance += f"\n\n--- APPLIED SKILL GUIDELINES: {full_sk['name']} ---\n{full_sk['instructions']}\n"

        system_prompt = f"""You are an expert document editor.
The user wants you to edit their structured markdown document according to their instructions.
Apply the exact requested edits (e.g. fix formulas, edit tables, adjust SVG markup, change text, adjust page layout).
Follow strict document formatting:
- Formulas in LaTeX ($...$ and $$...$$).
- Visuals and shapes in clean SVG blocks (```xml or ```svg).
- Page layout elements (side-by-side diagrams, headers, multiple choice) structured with Markdown tables.
- Return ONLY the updated markdown with no surrounding chatter or outer code fence.
{skill_guidance}"""
        start_time = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Instruction: {req.prompt}\n\nCurrent Markdown:\n{req.markdown}"}
            ],
            max_tokens=4096
        )
        duration_sec = round(time.time() - start_time, 2)
        new_md = resp.choices[0].message.content or req.markdown
        # Strip code fences if model wrapped entire response
        if new_md.startswith("```markdown"):
            new_md = new_md[11:]
        elif new_md.startswith("```"):
            new_md = new_md[3:]
        if new_md.endswith("```"):
            new_md = new_md[:-3]
        new_md = new_md.strip()

        usage = getattr(resp, "usage", None)
        p_tok = getattr(usage, "prompt_tokens", 0) if usage else len(req.markdown) // 4
        c_tok = getattr(usage, "completion_tokens", 0) if usage else len(new_md) // 4
        t_tok = p_tok + c_tok
        cost_usd = db.calculate_cost(model, p_tok, c_tok)
        ctx_usage = db.calculate_context_usage(model, p_tok, c_tok)

        # Update saved markdown file
        if req.base_name:
            md_path = OUTPUT_DIR / f"{req.base_name}.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(new_md)
            # Log assistant response to chat history with duration and price
            db.add_chat_message(
                req.base_name,
                "assistant",
                f"Applied edits in {db.format_duration(duration_sec)} ({db.format_cost(cost_usd)}): \"{req.prompt}\""
            )

        edit_steps = [
            {
                "step": 1,
                "type": "thought",
                "title": "USER REFINEMENT INSTRUCTION RECEIVED",
                "description": f"Prompt: \"{req.prompt}\"",
                "status": "info",
                "time": time.strftime("%H:%M:%S")
            },
            {
                "step": 2,
                "type": "thought",
                "title": "AGENT REASONING & DIFF SYNTHESIS",
                "description": f"Applied model '{model}' in {db.format_duration(duration_sec)} ({db.format_cost(cost_usd)}). Output size: {len(new_md)} chars.",
                "status": "success",
                "time": time.strftime("%H:%M:%S")
            },
            {
                "step": 3,
                "type": "output",
                "title": "DOCUMENT COMMITTED",
                "description": f"Updated markdown content on disk for '{req.base_name or 'document'}'.",
                "status": "success",
                "time": time.strftime("%H:%M:%S")
            }
        ]

        return JSONResponse(content={
            "markdown": new_md,
            "duration_sec": duration_sec,
            "formatted_duration": db.format_duration(duration_sec),
            "cost_usd": cost_usd,
            "formatted_cost": db.format_cost(cost_usd),
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "total_tokens": t_tok,
            "context_usage": ctx_usage,
            "formatted_context_usage": ctx_usage["formatted_summary"],
            "chat": db.get_chat_history(req.base_name) if req.base_name else [],
            "steps": edit_steps
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chat/{base_name}")
async def api_get_chat(base_name: str):
    """Retrieve chat conversation history for a specific document."""
    return JSONResponse(content={
        "base_name": base_name,
        "messages": db.get_chat_history(base_name)
    })

@app.post("/api/recompile")
async def api_recompile(req: RecompileRequest):
    """Rebuild DOCX, SVGs, and ZIP from current markdown."""
    try:
        base = req.base_name or "document"
        docx_path = str(OUTPUT_DIR / f"{base}.docx")
        svgs_dir = str(OUTPUT_DIR / f"{base}_svgs")
        os.makedirs(svgs_dir, exist_ok=True)

        exported_svgs = converter.markdown_to_docx(req.markdown, docx_path, output_dir=svgs_dir)

        md_path = str(OUTPUT_DIR / f"{base}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(req.markdown)

        zip_filename = f"{base}_bundle.zip"
        zip_path = str(OUTPUT_DIR / zip_filename)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(docx_path, arcname=f"{base}.docx")
            z.write(md_path, arcname=f"{base}.md")
            for svg_f in exported_svgs:
                z.write(svg_f, arcname=f"svgs/{os.path.basename(svg_f)}")

        svg_data = []
        for svg_p in exported_svgs:
            with open(svg_p, "r", encoding="utf-8") as sf:
                svg_data.append({"name": os.path.basename(svg_p), "content": sf.read()})

        return JSONResponse(content={
            "success": True,
            "base_name": base,
            "docx_path": docx_path,
            "docx_name": f"{base}.docx",
            "md_path": md_path,
            "md_name": f"{base}.md",
            "zip_path": zip_path,
            "zip_name": zip_filename,
            "svgs": [os.path.basename(s) for s in exported_svgs],
            "svg_data": svg_data,
            "markdown_content": req.markdown
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ValidateLatexRequest(BaseModel):
    latex: str

class ValidateSvgRequest(BaseModel):
    svg: str

class SkillCreateRequest(BaseModel):
    name: str
    description: str
    instructions: str

class MCPServerRequest(BaseModel):
    id: str
    name: str
    type: str = "stdio"
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    url: Optional[str] = None
    enabled: int = 1

class MCPToolCallRequest(BaseModel):
    server_id: str
    tool_name: str
    arguments: Optional[Dict[str, Any]] = None

class ValidateSvgRequest(BaseModel):
    svg: str

@app.get("/api/history")
async def get_history():
    """List all processed documents in OUTPUT_DIR with metadata."""
    if not OUTPUT_DIR.exists():
        return {"documents": [], "total_docs": 0, "total_size": 0}

    documents = []
    total_size = 0

    # Identify unique base names from .docx or .md files
    seen_bases = set()
    for file_path in OUTPUT_DIR.glob("*.docx"):
        base = file_path.stem
        seen_bases.add(base)
    for file_path in OUTPUT_DIR.glob("*.md"):
        base = file_path.stem
        seen_bases.add(base)

    for base in sorted(seen_bases):
        docx_f = OUTPUT_DIR / f"{base}.docx"
        md_f = OUTPUT_DIR / f"{base}.md"
        zip_f = OUTPUT_DIR / f"{base}_bundle.zip"
        svgs_dir = OUTPUT_DIR / f"{base}_svgs"

        doc_size = 0
        mtime = 0
        if docx_f.exists():
            st = docx_f.stat()
            doc_size += st.st_size
            mtime = max(mtime, st.st_mtime)
        if md_f.exists():
            st = md_f.stat()
            doc_size += st.st_size
            mtime = max(mtime, st.st_mtime)
        if zip_f.exists():
            st = zip_f.stat()
            doc_size += st.st_size
            mtime = max(mtime, st.st_mtime)

        svg_count = 0
        if svgs_dir.is_dir():
            for sf in svgs_dir.glob("*.svg"):
                svg_count += 1
                doc_size += sf.stat().st_size

        total_size += doc_size

        # Display title: strip uuid prefix if present
        display_title = base
        parts = base.split("_", 1)
        if len(parts) == 2 and len(parts[0]) == 32:
            display_title = parts[1]

        job_meta = db.get_job_by_basename(base)
        duration_sec = (job_meta.get("duration_sec") or 0.0) if job_meta else 0.0
        cost_usd = (job_meta.get("cost_usd") or 0.0) if job_meta else 0.0
        p_tokens = (job_meta.get("prompt_tokens") or 0) if job_meta else 0
        c_tokens = (job_meta.get("completion_tokens") or 0) if job_meta else 0
        t_tokens = (job_meta.get("total_tokens") or (p_tokens + c_tokens)) if job_meta else 0
        m_name = (job_meta.get("model") or "") if job_meta else ""
        ctx_info = db.calculate_context_usage(m_name, p_tokens, c_tokens)

        documents.append({
            "base_name": base,
            "display_title": display_title,
            "has_docx": docx_f.exists(),
            "has_md": md_f.exists(),
            "has_zip": zip_f.exists(),
            "docx_name": f"{base}.docx" if docx_f.exists() else None,
            "md_name": f"{base}.md" if md_f.exists() else None,
            "zip_name": f"{base}_bundle.zip" if zip_f.exists() else None,
            "svg_count": svg_count,
            "size": doc_size,
            "duration_sec": duration_sec,
            "formatted_duration": db.format_duration(duration_sec),
            "cost_usd": cost_usd,
            "formatted_cost": db.format_cost(cost_usd),
            "prompt_tokens": p_tokens,
            "completion_tokens": c_tokens,
            "total_tokens": t_tokens,
            "context_limit": ctx_info["context_limit"],
            "context_percent": ctx_info["context_percent"],
            "formatted_context_usage": ctx_info["formatted_summary"],
            "mtime": mtime,
            "formatted_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)) if mtime else "N/A"
        })

    documents.sort(key=lambda d: d["mtime"], reverse=True)
    total_cost = sum(d.get("cost_usd", 0.0) for d in documents)
    total_duration = sum(d.get("duration_sec", 0.0) for d in documents)
    token_agg = db.get_total_tokens_consumed()

    return {
        "documents": documents,
        "total_docs": len(documents),
        "total_size": total_size,
        "total_cost": total_cost,
        "formatted_total_cost": db.format_cost(total_cost),
        "total_duration": total_duration,
        "formatted_total_duration": db.format_duration(total_duration),
        "total_tokens": token_agg["total_tokens"],
        "formatted_total_tokens": f"{token_agg['total_tokens']:,}",
        "prompt_tokens": token_agg["prompt_tokens"],
        "completion_tokens": token_agg["completion_tokens"],
        "output_path": str(OUTPUT_DIR)
    }

@app.get("/api/history/{base_name}")
async def get_history_document(base_name: str):
    """Load an existing output document into the Studio."""
    md_file = OUTPUT_DIR / f"{base_name}.md"
    docx_file = OUTPUT_DIR / f"{base_name}.docx"
    zip_file = OUTPUT_DIR / f"{base_name}_bundle.zip"
    svgs_dir = OUTPUT_DIR / f"{base_name}_svgs"

    if not (md_file.exists() or docx_file.exists()):
        raise HTTPException(status_code=404, detail="Document not found")

    md_content = ""
    if md_file.exists():
        with open(md_file, "r", encoding="utf-8") as f:
            md_content = f.read()

    svg_data = []
    svgs = []
    if svgs_dir.is_dir():
        for sf in sorted(svgs_dir.glob("*.svg")):
            with open(sf, "r", encoding="utf-8") as sfp:
                svg_data.append({"name": sf.name, "content": sfp.read()})
                svgs.append(sf.name)

    job_meta = db.get_job_by_basename(base_name)
    duration_sec = (job_meta.get("duration_sec") or 0.0) if job_meta else 0.0
    cost_usd = (job_meta.get("cost_usd") or 0.0) if job_meta else 0.0
    p_tokens = (job_meta.get("prompt_tokens") or 0) if job_meta else 0
    c_tokens = (job_meta.get("completion_tokens") or 0) if job_meta else 0
    t_tokens = (job_meta.get("total_tokens") or (p_tokens + c_tokens)) if job_meta else 0
    m_name = (job_meta.get("model") or "") if job_meta else ""
    ctx_info = db.calculate_context_usage(m_name, p_tokens, c_tokens)

    steps = []
    if job_meta and job_meta.get("result"):
        steps = job_meta["result"].get("steps", [])
    if not steps:
        steps = [
            {"step": 1, "type": "init", "title": "DOCUMENT ARCHIVE LOADED", "description": f"Loaded '{base_name}' from library storage.", "status": "success", "time": "Archived"},
            {"step": 2, "type": "output", "title": "COMPILATION RECOVERED", "description": f"Word document, {len(svgs)} SVG shape(s), and Markdown recovered.", "status": "success", "time": "Ready"}
        ]

    return {
        "success": True,
        "base_name": base_name,
        "docx_path": str(docx_file),
        "docx_name": f"{base_name}.docx",
        "md_path": str(md_file),
        "md_name": f"{base_name}.md",
        "zip_path": str(zip_file),
        "zip_name": f"{base_name}_bundle.zip",
        "svgs": svgs,
        "svg_data": svg_data,
        "markdown_content": md_content,
        "duration_sec": duration_sec,
        "formatted_duration": db.format_duration(duration_sec),
        "cost_usd": cost_usd,
        "formatted_cost": db.format_cost(cost_usd),
        "prompt_tokens": p_tokens,
        "completion_tokens": c_tokens,
        "total_tokens": t_tokens,
        "context_limit": ctx_info["context_limit"],
        "context_percent": ctx_info["context_percent"],
        "formatted_context_usage": ctx_info["formatted_summary"],
        "context_usage": ctx_info,
        "total_pages": 1,
        "steps": steps
    }

@app.delete("/api/history/{base_name}")
async def delete_history_document(base_name: str):
    """Delete a generated document and all its assets."""
    deleted_files = []
    for ext in [".docx", ".md", "_bundle.zip"]:
        f = OUTPUT_DIR / f"{base_name}{ext}"
        if f.exists():
            f.unlink()
            deleted_files.append(f.name)

    svgs_dir = OUTPUT_DIR / f"{base_name}_svgs"
    if svgs_dir.is_dir():
        shutil.rmtree(svgs_dir, ignore_errors=True)
        deleted_files.append(f"{base_name}_svgs/")

    return {"success": True, "base_name": base_name, "deleted": deleted_files}

@app.post("/api/tools/estimate-tokens")
async def api_estimate_tokens(req: TokenEstimateRequest):
    """Accurately estimate tokens and pre-flight budget using tiktoken and API-loaded context window."""
    chosen_model = req.model or db.get_setting("AI_DEFAULT_MODEL", engine.AI_DEFAULT_MODEL)
    budget = tokenizer.estimate_preflight_budget(
        text=req.text or "",
        model_name=chosen_model,
        page_count=req.page_count or 0,
        expected_output_tokens=req.expected_output_tokens
    )
    return budget

@app.post("/api/tools/validate-latex")
async def api_validate_latex(req: ValidateLatexRequest):
    """Validate a LaTeX math formula and test OMML generation."""
    try:
        omml = converter.latex_to_omml(req.latex)
        if omml is not None:
            tag = omml.tag if hasattr(omml, "tag") else "oMath"
            return {"valid": True, "tag": tag, "error": None}
        return {"valid": False, "tag": None, "error": "LaTeX to OMML produced no output"}
    except Exception as e:
        return {"valid": False, "tag": None, "error": str(e)}

@app.post("/api/tools/validate-svg")
async def api_validate_svg(req: ValidateSvgRequest):
    """Validate SVG XML string for Word shape compatibility."""
    import xml.etree.ElementTree as ET
    try:
        cleaned = req.svg.strip()
        root = ET.fromstring(cleaned)
        viewbox = root.attrib.get("viewBox")
        width = root.attrib.get("width")
        height = root.attrib.get("height")
        elem_count = len(list(root.iter()))
        return {
            "valid": True,
            "has_viewbox": bool(viewbox),
            "viewbox": viewbox,
            "width": width,
            "height": height,
            "elements_count": elem_count,
            "error": None
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}

# --- SKILLS ENDPOINTS ---

@app.get("/api/skills")
async def get_skills_list():
    """List all available skills (built-in local and user global)."""
    items = skills.list_skills()
    return {"skills": items, "count": len(items)}

@app.get("/api/skills/{name}")
async def get_skill_detail(name: str):
    """Retrieve full content and guidelines of a skill."""
    sk = skills.get_skill(name)
    if not sk:
        raise HTTPException(status_code=404, detail="Skill not found")
    return sk

@app.post("/api/skills")
async def save_skill(req: SkillCreateRequest):
    """Create or update a local project skill."""
    try:
        path = skills.save_local_skill(req.name, req.description, req.instructions)
        return {"success": True, "path": path, "name": req.name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/skills/{name}")
async def delete_skill(name: str):
    """Delete a local project skill."""
    deleted = skills.delete_local_skill(name)
    if not deleted:
        raise HTTPException(status_code=400, detail="Cannot delete skill (only local skills can be removed).")
    return {"success": True, "name": name}

# --- MCP ENDPOINTS ---

@app.get("/api/mcp/servers")
async def get_mcp_servers():
    """List configured MCP servers and probe their available tools."""
    servers = db.list_mcp_servers()
    detailed = []
    for s in servers:
        client = mcp_manager.MCPClient(s)
        tools = []
        status = "disabled" if not s.get("enabled", 1) else "connecting"
        if s.get("enabled", 1):
            try:
                tools = client.list_tools()
                status = "connected"
            except Exception as e:
                status = f"error: {str(e)[:100]}"
        detailed.append({
            **s,
            "tools": tools,
            "tools_count": len(tools),
            "status": status
        })
    return {"servers": detailed, "count": len(detailed)}

@app.post("/api/mcp/servers")
async def save_mcp_server(req: MCPServerRequest):
    """Create or update an MCP server configuration in SQLite."""
    try:
        db.save_mcp_server(
            server_id=req.id.strip(),
            name=req.name.strip(),
            server_type=req.type.strip(),
            command=req.command,
            args=req.args or [],
            env=req.env or {},
            url=req.url,
            enabled=req.enabled
        )
        return {"success": True, "id": req.id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str):
    """Delete an MCP server from SQLite."""
    db.delete_mcp_server(server_id)
    return {"success": True, "id": server_id}

@app.post("/api/mcp/servers/{server_id}/test")
async def test_mcp_server(server_id: str):
    """Test connection to an MCP server and return discovered tools."""
    s = db.get_mcp_server(server_id)
    if not s:
        raise HTTPException(status_code=404, detail="Server not found")
    client = mcp_manager.MCPClient(s)
    try:
        tools = client.list_tools()
        return {"success": True, "status": "connected", "tools": tools, "tools_count": len(tools)}
    except Exception as e:
        return {"success": False, "status": "error", "error": str(e), "tools": []}

@app.post("/api/mcp/tools/call")
async def call_mcp_tool_api(req: MCPToolCallRequest):
    """Manually invoke an MCP tool from sandbox or agent."""
    try:
        output = mcp_manager.mcp_mgr.call_tool(req.server_id, req.tool_name, req.arguments or {})
        return {"success": True, "output": output}
    except Exception as e:
        return {"success": False, "error": str(e)}

class SettingsUpdateRequest(BaseModel):
    OPENAI_API_BASE_URL: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    AI_DEFAULT_MODEL: Optional[str] = None
    OUTPUT_PATH: Optional[str] = None
    MAX_CONCURRENT_JOBS: Optional[str] = None

@app.get("/api/settings")
async def get_settings():
    """Return runtime configuration and diagnostic information from SQLite."""
    settings = db.get_all_settings()
    key = settings.get("OPENAI_API_KEY", "")
    masked_key = f"{key[:5]}...{key[-4:]}" if len(key) >= 10 else ("***" if key else "NOT SET")
    return {
        "api_base_url": settings.get("OPENAI_API_BASE_URL", "http://localhost:20128/v1"),
        "api_key_configured": bool(key),
        "api_key_masked": masked_key,
        "api_key_raw": key,
        "default_model": settings.get("AI_DEFAULT_MODEL", "ag/gemini-3.8-flash-high"),
        "output_dir": settings.get("OUTPUT_PATH", str(OUTPUT_DIR)),
        "max_concurrent_jobs": settings.get("MAX_CONCURRENT_JOBS", "3"),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "system_status": "READY"
    }

@app.post("/api/settings")
async def save_settings(req: SettingsUpdateRequest):
    """Save updated parameters to SQLite and synchronize runtime engine state."""
    data = req.dict(exclude_none=True)
    for k, v in data.items():
        if v is not None:
            db.set_setting(k, str(v).strip())

    # Synchronize engine memory configs
    if req.OPENAI_API_BASE_URL:
        engine.OPENAI_API_BASE_URL = req.OPENAI_API_BASE_URL
    if req.OPENAI_API_KEY:
        engine.OPENAI_API_KEY = req.OPENAI_API_KEY
    if req.AI_DEFAULT_MODEL:
        engine.AI_DEFAULT_MODEL = req.AI_DEFAULT_MODEL
    if req.OUTPUT_PATH:
        engine.OUTPUT_DIR = req.OUTPUT_PATH

    return JSONResponse(content={"success": True, "message": "Settings saved to SQLite successfully."})

@app.post("/api/settings/clear-cache")
async def clear_cache():
    """Purge input files, output artifacts, and reset jobs/chat database tables."""
    deleted_inputs = 0
    deleted_outputs = 0

    # 1. Clear input files
    input_dir = BASE_DIR / "input"
    if input_dir.exists():
        for item in input_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    deleted_inputs += 1
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                    deleted_inputs += 1
            except Exception:
                pass

    # 2. Clear output files
    out_dir = Path(db.get_setting("OUTPUT_PATH", str(OUTPUT_DIR)))
    if out_dir.exists():
        for item in out_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    deleted_outputs += 1
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                    deleted_outputs += 1
            except Exception:
                pass

    # 3. Clean database (jobs and chat_history; settings preserved)
    db.clean_db()

    # 4. Clear in-memory task tracking
    with QUEUE_LOCK:
        TASKS.clear()
        ACTIVE_FUTURES.clear()

    return JSONResponse(content={
        "success": True,
        "deleted_inputs": deleted_inputs,
        "deleted_outputs": deleted_outputs,
        "message": f"Successfully removed {deleted_inputs} input files, {deleted_outputs} output files, and cleaned SQLite tables."
    })

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream"
    )

@app.get("/api/download/svg/{base_name}/{svg_name}")
async def download_svg(base_name: str, svg_name: str):
    file_path = OUTPUT_DIR / f"{base_name}_svgs" / svg_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="SVG file not found")
    return FileResponse(
        path=str(file_path),
        filename=svg_name,
        media_type="image/svg+xml"
    )

def open_browser(port: int = 8000):
    webbrowser.open(f"http://localhost:{port}")

def main():
    parser = argparse.ArgumentParser(description="DocuMorph AI - PDF/Image to Word (.docx)")
    parser.add_argument("file", nargs="?", help="Path to input PDF or Image for direct CLI conversion")
    parser.add_argument("--port", type=int, default=8000, help="Port for web server (default: 8000)")
    parser.add_argument("--mode", choices=["fast", "agent"], default="agent", help="Processing mode (default: agent)")
    parser.add_argument("--reload", action="store_true", default=True, help="Enable auto-reload on file change for local dev (default: True)")
    parser.add_argument("--no-reload", action="store_false", dest="reload", help="Disable auto-reload")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    if args.file:
        if args.mode == "agent":
            print(f"Running DocuMorph AI Agent on '{args.file}' with tools...")
            doc_agent = agent.DocuMorphAgent(args.file, output_dir=str(OUTPUT_DIR))
            res = doc_agent.run_agent_workflow(
                progress_callback=lambda msg, pct: print(f"[{int(pct*100)}%] {msg}")
            )
        else:
            print(f"Processing '{args.file}' with fast pipeline...")
            res = engine.process_file_to_docx(
                args.file,
                output_dir=str(OUTPUT_DIR),
                progress_callback=lambda msg, pct: print(f"[{int(pct*100)}%] {msg}")
            )
        print("\n--- Conversion Completed Successfully! ---")
        print(f"Word Document: {res['docx_path']}")
        print(f"Markdown File: {res['md_path']}")
        print(f"Extracted SVGs ({len(res['svgs'])}): {res['svgs']}")
    else:
        print(f"Starting DocuMorph AI Web App on http://localhost:{args.port} [Auto-Reload: {'ENABLED' if args.reload else 'DISABLED'}]")
        if not args.no_browser and os.environ.get("UVICORN_STARTED") != "1":
            threading.Timer(1.5, lambda: open_browser(args.port)).start()

        # Ensure base directory in sys.path for uvicorn module loader
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))

        if args.reload:
            os.environ["UVICORN_STARTED"] = "1"
            uvicorn.run(
                "app:app",
                host="127.0.0.1",
                port=args.port,
                reload=True,
                reload_dirs=[str(BASE_DIR)],
                reload_includes=["*.py", "*.html", "*.css", "*.js"],
                log_level="info"
            )
        else:
            uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")

if __name__ == "__main__":
    main()
