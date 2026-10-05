"""
DocuMorph AI - Model Context Protocol (MCP) Client Manager
Handles:
1. Connection to MCP servers via stdio or HTTP/SSE (JSON-RPC 2.0).
2. Dynamic discovery of MCP tools (`tools/list`).
3. Execution of MCP tools (`tools/call`).
4. Conversion to OpenAI function calling tool schemas for agent integration.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import db


class MCPClient:
    """Lightweight stdlib client for MCP (Model Context Protocol) JSON-RPC 2.0 servers."""

    def __init__(self, server_config: dict[str, Any]):
        self.config = server_config
        self.id = server_config.get("id", "default")
        self.name = server_config.get("name", self.id)
        self.server_type = server_config.get("type", "stdio")
        self.command = server_config.get("command", "")
        self.args = server_config.get("args") or []
        self.env = server_config.get("env") or {}
        self.url = server_config.get("url", "")
        self.enabled = bool(server_config.get("enabled", 1))

    def _call_http(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        """Perform JSON-RPC 2.0 request over HTTP."""
        if not self.url:
            raise ValueError(f"MCP server '{self.name}' has no URL configured.")
        payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 1000000, "method": method, "params": params or {}}
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            res_json = json.loads(data)
            if "error" in res_json:
                raise RuntimeError(res_json["error"].get("message", "MCP error"))
            return res_json.get("result", {})

    def _run_stdio_session(self, requests: list[dict[str, Any]], timeout: float = 12.0) -> list[dict[str, Any]]:
        """Run single-shot stdio MCP session for discovery or execution."""
        if not self.command:
            raise ValueError(f"MCP server '{self.name}' has no command configured.")

        cmd = [self.command] + [str(a) for a in self.args]
        proc_env = os.environ.copy()
        for k, v in self.env.items():
            proc_env[str(k)] = str(v)

        # On Windows, resolve command path if executable without .cmd or .bat
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=proc_env,
        )

        responses = []
        try:
            # 1. Initialize
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "DocuMorph", "version": "1.0.0"},
                },
            }
            proc.stdin.write(json.dumps(init_req) + "\n")
            proc.stdin.flush()

            # Read initialize response
            init_line = proc.stdout.readline()
            if not init_line:
                err = proc.stderr.read()
                raise RuntimeError(f"MCP server exited without init response: {err}")

            # Send initialized notification
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
            proc.stdin.flush()

            # Execute caller requests
            req_id = 2
            for req_item in requests:
                req_payload = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": req_item["method"],
                    "params": req_item.get("params", {}),
                }
                req_id += 1
                proc.stdin.write(json.dumps(req_payload) + "\n")
                proc.stdin.flush()

                # Read line
                resp_line = proc.stdout.readline()
                if not resp_line:
                    raise RuntimeError("MCP server closed stream before response.")
                parsed = json.loads(resp_line)
                responses.append(parsed)

        finally:
            try:
                proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                proc.kill()

        return responses

    def list_tools(self) -> list[dict[str, Any]]:
        """Query tools/list from server and return tools."""
        if not self.enabled:
            return []
        try:
            if self.server_type == "http":
                res = self._call_http("tools/list")
                return res.get("tools", [])
            else:
                responses = self._run_stdio_session([{"method": "tools/list", "params": {}}])
                if responses and "result" in responses[0]:
                    return responses[0]["result"].get("tools", [])
                elif responses and "error" in responses[0]:
                    raise RuntimeError(responses[0]["error"].get("message", "Unknown MCP error"))
                return []
        except Exception:
            return []

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a specific tool on the server and format content as string."""
        if self.server_type == "http":
            res = self._call_http("tools/call", {"name": tool_name, "arguments": arguments})
        else:
            responses = self._run_stdio_session(
                [{"method": "tools/call", "params": {"name": tool_name, "arguments": arguments}}]
            )
            if not responses:
                raise RuntimeError("No response from MCP server")
            resp = responses[0]
            if "error" in resp:
                raise RuntimeError(resp["error"].get("message", "MCP tool execution error"))
            res = resp.get("result", {})

        # Extract text content
        content_items = res.get("content", [])
        text_out = []
        for c in content_items:
            if isinstance(c, dict) and c.get("type") == "text":
                text_out.append(c.get("text", ""))
            elif isinstance(c, str):
                text_out.append(c)
            else:
                text_out.append(json.dumps(c))
        return "\n".join(text_out) if text_out else json.dumps(res)


class MCPManager:
    """Manages active MCP servers and translates tools into OpenAI agent schemas."""

    def __init__(self):
        self._ensure_default_servers()

    def _ensure_default_servers(self):
        """Seed a built-in helper MCP server if none exist so users can use MCP immediately."""
        existing = db.list_mcp_servers()
        if not existing:
            server_script = os.path.join(os.path.dirname(__file__), "mcp_builtin_server.py")
            db.save_mcp_server(
                server_id="doc_utils",
                name="DocuMorph Document Tools",
                server_type="stdio",
                command=sys.executable,
                args=[server_script],
                enabled=1,
            )

    def get_configured_servers(self) -> list[dict[str, Any]]:
        return db.list_mcp_servers()

    def discover_all_tools(self) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
        """
        Discovers tools from all enabled MCP servers.
        Returns:
        1. OpenAI formatted tools list: [{"type": "function", "function": {...}}]
        2. Routing map: full_tool_name -> (server_id, original_tool_name)
        """
        openai_tools = []
        routing_map: dict[str, tuple[str, str]] = {}

        servers = self.get_configured_servers()
        for s in servers:
            if not s.get("enabled", 1):
                continue
            client = MCPClient(s)
            tools = client.list_tools()
            for t in tools:
                orig_name = t.get("name", "")
                if not orig_name:
                    continue
                # OpenAI tool name constraint: [a-zA-Z0-9_-]+
                safe_server_id = s["id"].replace("-", "_")
                safe_name = f"mcp__{safe_server_id}__{orig_name.replace('-', '_')}"

                desc = f"[MCP: {s['name']}] {t.get('description', '')}".strip()
                input_schema = t.get("inputSchema") or {"type": "object", "properties": {}}

                openai_tools.append(
                    {
                        "type": "function",
                        "function": {"name": safe_name, "description": desc, "parameters": input_schema},
                    }
                )
                routing_map[safe_name] = (s["id"], orig_name)

        return openai_tools, routing_map

    def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on a specific MCP server."""
        s = db.get_mcp_server(server_id)
        if not s:
            raise ValueError(f"MCP server '{server_id}' not found.")
        client = MCPClient(s)
        return client.call_tool(tool_name, arguments)


mcp_mgr = MCPManager()
