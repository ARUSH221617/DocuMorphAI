"""
DocuMorph AI - SQLite Database Layer
Handles:
- Persistent key/value settings (save/load)
- Persistent chat history for agent refinement
- Persistent job queue & execution tracking
"""

import os
import json
import time
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
import dotenv

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "documorph.db"

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables and initialize defaults from .env if empty."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base_name TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL, -- queued, processing, done, error, cancelled
                percent INTEGER DEFAULT 0,
                message TEXT,
                mode TEXT DEFAULT 'agent',
                model TEXT,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                result_json TEXT,
                error TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_basename ON chat_history (base_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (created_at DESC)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT DEFAULT 'stdio', -- 'stdio' or 'http'
                command TEXT,
                args TEXT DEFAULT '[]',
                env TEXT DEFAULT '{}',
                url TEXT,
                enabled INTEGER DEFAULT 1,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()

        # Migrate jobs table with duration, token counts, and cost
        for col, col_type in [
            ("duration_sec", "REAL DEFAULT 0.0"),
            ("cost_usd", "REAL DEFAULT 0.0"),
            ("prompt_tokens", "INTEGER DEFAULT 0"),
            ("completion_tokens", "INTEGER DEFAULT 0")
        ]:
            try:
                cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
        conn.commit()

        # Seed settings from .env if not yet set
        env_path = BASE_DIR / ".env"
        env_vals = dotenv.dotenv_values(env_path) if env_path.exists() else {}
        
        defaults = {
            "OPENAI_API_BASE_URL": os.getenv("OPENAI_API_BASE_URL") or env_vals.get("OPENAI_API_BASE_URL") or "http://localhost:20128/v1",
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY") or env_vals.get("OPENAI_API_KEY") or "",
            "AI_DEFAULT_MODEL": os.getenv("AI_DEFAULT_MODEL") or env_vals.get("AI_DEFAULT_MODEL") or "ag/gemini-3.8-flash-high",
            "OUTPUT_PATH": os.getenv("OUTPUT_PATH") or env_vals.get("OUTPUT_PATH") or str(BASE_DIR / "output"),
            "MAX_CONCURRENT_JOBS": "3"
        }
        
        for k, v in defaults.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
        conn.commit()

def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate estimated API cost in USD based on model token rates."""
    m = (model or "").lower()
    if "flash" in m or "mini" in m:
        rate_in = 0.15 / 1_000_000
        rate_out = 0.60 / 1_000_000
    elif "opus" in m:
        rate_in = 15.00 / 1_000_000
        rate_out = 75.00 / 1_000_000
    elif "sonnet" in m or "gpt-4o" in m:
        rate_in = 3.00 / 1_000_000
        rate_out = 15.00 / 1_000_000
    else:
        rate_in = 0.50 / 1_000_000
        rate_out = 1.50 / 1_000_000
    return round((prompt_tokens * rate_in) + (completion_tokens * rate_out), 6)

def format_duration(seconds: float) -> str:
    """Format duration in human-readable seconds or minutes."""
    if not seconds or seconds <= 0:
        return "0.0s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}m {secs}s"

def format_cost(cost_usd: float) -> str:
    """Format cost nicely in USD."""
    if not cost_usd or cost_usd <= 0:
        return "$0.0000"
    if cost_usd < 0.0001:
        return f"${cost_usd:.6f}"
    return f"${cost_usd:.4f}"

def clean_db():
    """Wipe jobs and chat_history tables, run VACUUM. Settings preserved."""
    with get_connection() as conn:
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM chat_history")
        conn.commit()
        conn.execute("VACUUM")

# --- SETTINGS REPOSITORY ---

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key: str, value: str):
    with get_connection() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
        conn.commit()

def get_all_settings() -> Dict[str, str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

# --- CHAT HISTORY REPOSITORY ---

def add_chat_message(base_name: str, role: str, content: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO chat_history (base_name, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (base_name, role, content, time.time())
        )
        conn.commit()
        return cur.lastrowid

def get_chat_history(base_name: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, base_name, role, content, timestamp FROM chat_history WHERE base_name = ? ORDER BY id ASC",
            (base_name,)
        ).fetchall()
        return [
            {
                "id": r["id"],
                "base_name": r["base_name"],
                "role": r["role"],
                "content": r["content"],
                "timestamp": r["timestamp"],
                "formatted_time": time.strftime("%H:%M:%S", time.localtime(r["timestamp"]))
            }
            for r in rows
        ]

# --- MCP SERVERS REPOSITORY ---

def list_mcp_servers() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM mcp_servers ORDER BY created_at ASC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["args"] = json.loads(d["args"]) if d.get("args") else []
            except Exception:
                d["args"] = []
            try:
                d["env"] = json.loads(d["env"]) if d.get("env") else {}
            except Exception:
                d["env"] = {}
            result.append(d)
        return result

def get_mcp_server(server_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["args"] = json.loads(d["args"]) if d.get("args") else []
        except Exception:
            d["args"] = []
        try:
            d["env"] = json.loads(d["env"]) if d.get("env") else {}
        except Exception:
            d["env"] = {}
        return d

def save_mcp_server(
    server_id: str,
    name: str,
    server_type: str = "stdio",
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    url: Optional[str] = None,
    enabled: int = 1
):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO mcp_servers (id, name, type, command, args, env, url, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name = excluded.name,
                   type = excluded.type,
                   command = excluded.command,
                   args = excluded.args,
                   env = excluded.env,
                   url = excluded.url,
                   enabled = excluded.enabled""",
            (
                server_id,
                name,
                server_type,
                command or "",
                json.dumps(args or []),
                json.dumps(env or {}),
                url or "",
                enabled,
                time.time()
            )
        )
        conn.commit()

def delete_mcp_server(server_id: str):
    with get_connection() as conn:
        conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        conn.commit()

# --- JOBS REPOSITORY ---

def create_job(job_id: str, filename: str, file_path: str, mode: str = "agent", model: Optional[str] = None):
    now = time.time()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO jobs (id, filename, file_path, status, percent, message, mode, model, created_at)
               VALUES (?, ?, ?, 'queued', 0, 'In queue waiting for worker...', ?, ?, ?)""",
            (job_id, filename, file_path, mode, model or "", now)
        )
        conn.commit()

def update_job(job_id: str, **kwargs):
    fields = []
    values = []
    for k, v in kwargs.items():
        if k == "result":
            fields.append("result_json = ?")
            values.append(json.dumps(v) if v is not None else None)
        else:
            fields.append(f"{k} = ?")
            values.append(v)
    if not fields:
        return
    values.append(job_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", tuple(values))
        conn.commit()

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("result_json"):
            try:
                d["result"] = json.loads(d["result_json"])
            except Exception:
                d["result"] = None
        else:
            d["result"] = None
        return d

def get_job_by_basename(base_name: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ? OR filename LIKE ? OR file_path LIKE ? ORDER BY created_at DESC LIMIT 1",
            (base_name, f"%{base_name}%", f"%{base_name}%")
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("result_json"):
            try:
                d["result"] = json.loads(d["result_json"])
            except Exception:
                d["result"] = None
        else:
            d["result"] = None
        return d

def get_next_queued_job() -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return dict(row)

def count_active_jobs() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT count(*) as cnt FROM jobs WHERE status = 'processing'").fetchone()
        return row["cnt"] if row else 0

def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            if d.get("result_json"):
                try:
                    d["result"] = json.loads(d["result_json"])
                except Exception:
                    d["result"] = None
            else:
                d["result"] = None
            res.append(d)
        return res

# Initialize on import
init_db()
