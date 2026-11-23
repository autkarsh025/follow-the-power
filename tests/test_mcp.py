"""MCP server over real stdio (subprocess): handshake, tool listing/calls, skills as prompts, errors."""
import json
import os
import subprocess
import sys
from pathlib import Path

from fixtures import load_synthetic
from ftp import config, db

ROOT = Path(__file__).resolve().parent.parent


def test_mcp_stdio_roundtrip():
    conn = db.connect()
    load_synthetic(conn, True)
    conn.close()
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "quarterly_financials", "arguments": {"last_n": 2}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "company_financials", "arguments": {"ticker": "ZZZ", "metric": "revenue"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "prompts/get", "params": {"name": "claim-check"}},
            {"jsonrpc": "2.0", "id": 6, "method": "nope"}]
    env = {**os.environ, "FTP_DB": str(config.DB_PATH), "PYTHONPATH": str(ROOT)}
    p = subprocess.run([sys.executable, "-m", "ftp.mcp_server"], input="\n".join(json.dumps(m) for m in msgs) + "\nnot json\n",
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    out = {r.get("id"): r for r in map(json.loads, p.stdout.strip().splitlines())}
    assert out[1]["result"]["serverInfo"]["name"] == "follow-the-power"
    names = {t["name"] for t in out[2]["result"]["tools"]}
    assert {"quarterly_financials", "search_filings", "zone_overview", "sql"} <= names and "load_skill" not in names
    data = json.loads(out[3]["result"]["content"][0]["text"])
    assert len(data["rows"]) == 2 and out[3]["result"]["isError"] is False
    assert out[4]["result"]["isError"] is True
    assert "CLAIM" in out[5]["result"]["messages"][0]["content"]["text"]
    assert out[6]["error"]["code"] == -32601 and out[None]["error"]["code"] == -32700
