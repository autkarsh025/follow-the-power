"""MCP server: plug follow-the-power's tools and skills into Claude Desktop, Claude Code or any MCP client.

Model Context Protocol = JSON-RPC 2.0 over stdio (one JSON message per line). Implemented here with the
standard library only, so it runs anywhere the project runs:

  initialize                handshake (capabilities: tools, prompts)
  tools/list, tools/call    the same point-in-time tools the built-in agent uses (read-only database)
  prompts/list, prompts/get the skills (variance analysis, earnings brief, claim check, ...) as prompts

Claude Desktop config (claude_desktop_config.json):
  {"mcpServers": {"follow-the-power": {"command": "python", "args": ["-m", "ftp.mcp_server"],
                                       "cwd": "/path/to/follow-the-power"}}}
Optional: FTP_AS_OF=2025-05-01 starts the server in time-travel mode (tools hide later data).
"""
from __future__ import annotations

import json
import os
import sys

from ftp.tools import Toolbox

SERVER = {"name": "follow-the-power", "version": "1.0.0"}
DEFAULT_PROTOCOL = "2025-06-18"


class Server:
    def __init__(self, db_path=None, as_of: str | None = None):
        self.tb = Toolbox(db_path, as_of=as_of)

    def handle(self, msg: dict) -> dict | None:
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if mid is None:                       # notifications (e.g. notifications/initialized) get no reply
            return None
        try:
            result = self.dispatch(method, params)
        except KeyError as exc:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {exc}"}}
        except Exception as exc:  # pragma: no cover - defensive
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def dispatch(self, method: str, p: dict) -> dict:
        if method == "initialize":
            return {"protocolVersion": p.get("protocolVersion", DEFAULT_PROTOCOL),
                    "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                    "serverInfo": SERVER,
                    "instructions": "Point-in-time data on the AI build-out: SEC financials for 26 companies, grid demand "
                                    "in 5 data-center zones, weather-normalized baseload and cited filing text. Every "
                                    "number you report should come from these tools."}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": s["name"], "description": s["description"], "inputSchema": s["input_schema"]}
                              for s in self.tb.specs() if s["name"] != "load_skill"]}
        if method == "tools/call":
            out = self.tb.run(p["name"], p.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(out, default=str)}], "isError": "error" in out}
        if method == "prompts/list":
            return {"prompts": [{"name": s["name"], "description": s["description"]} for s in self.tb.skills.values()]}
        if method == "prompts/get":
            s = self.tb.skills[p["name"]]
            return {"description": s["description"],
                    "messages": [{"role": "user", "content": {"type": "text", "text": s["body"]}}]}
        raise KeyError(method)


def main() -> None:  # pragma: no cover - exercised via subprocess in tests
    srv = Server(as_of=os.getenv("FTP_AS_OF") or None)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = srv.handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
