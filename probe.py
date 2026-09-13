"""Goi thu mot tool MCP tu dong lenh. Chi dung khi phat trien, khong vao Docker image.

    python probe.py                        # liet ke tool
    python probe.py get_positions
    python probe.py get_context '{"timeframes":["4h"]}'
    python probe.py --port 8849 get_positions

Script tu lam ca ba buoc bat tay (initialize -> notifications/initialized -> tools/call)
nen khong phai copy session id bang tay.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

import httpx2 as httpx

with contextlib.suppress(ImportError):
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)

PROTOCOL = "2025-06-18"


def _sse_json(text: str) -> dict:
    """Response cua streamable-http la SSE: lay dong `data:` dau tien."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


def main() -> int:
    args = sys.argv[1:]
    port = 8848
    if "--port" in args:
        i = args.index("--port")
        port = int(args[i + 1])
        del args[i:i + 2]

    tool = args[0] if args else ""
    payload = json.loads(args[1]) if len(args) > 1 else {}

    url = f"http://127.0.0.1:{port}/mcp"
    user = os.environ.get("BAMCP_USERNAME", "")
    password = os.environ.get("BAMCP_PASSWORD", "")
    auth = (user, password) if user else None

    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}

    with httpx.Client(timeout=60.0) as client:
        init = client.post(url, auth=auth, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                       "clientInfo": {"name": "probe", "version": "1"}}})
        if init.status_code != 200:
            print(f"initialize that bai: HTTP {init.status_code}\n{init.text[:400]}")
            return 1

        session = init.headers.get("mcp-session-id")
        if not session:
            print("khong nhan duoc mcp-session-id")
            return 1
        headers["mcp-session-id"] = session
        headers["mcp-protocol-version"] = PROTOCOL

        client.post(url, auth=auth, headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})

        if not tool:
            resp = client.post(url, auth=auth, headers=headers,
                               json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = _sse_json(resp.text)["result"]["tools"]
            for n, item in enumerate(tools, 1):
                print(f"{n:2}. {item['name']}")
            return 0

        resp = client.post(url, auth=auth, headers=headers, json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": tool, "arguments": payload}})

    body = _sse_json(resp.text)
    if "error" in body:
        print(f"LOI JSON-RPC: {body['error']}")
        return 1

    result = body.get("result", {})
    for block in result.get("content", []):
        if block.get("type") == "text":
            print(block["text"])
    if result.get("isError"):
        print("\n^ tool tra ve loi")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
