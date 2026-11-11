"""Minimal client for NSE's official MCP servers (Streamable HTTP, no auth).

  bhavcopy  https://mcp.nseindia.in/bhavcopy/cm/mcp   end-of-day history (~5 years, unadjusted)
  live      https://mcp.nseindia.in/cmmkt/mcp         live market snapshot (1-3 min delayed)

NSE labels this data informational/educational: not for real-time trading, commercial use or
training AI models. Everything here is best effort; callers must fall back on failure.

Run `python -m scanner.nse_mcp reports/mcp_probe.json` to list every tool's schema and a sample
response from each server.
"""
import json
import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

SERVERS = {
    "bhavcopy": "https://mcp.nseindia.in/bhavcopy/cm/mcp",
    "live": "https://mcp.nseindia.in/cmmkt/mcp",
}
PROTOCOL = "2025-06-18"


class MCPError(RuntimeError):
    pass


class NSEMCP:
    """One session per server. call() returns the tool's result parsed from JSON when possible."""

    def __init__(self, server: str = "bhavcopy", timeout: int = 30, delay: float = 0.3,
                 retries: int = 2):
        self.url = SERVERS.get(server, server)
        self.timeout, self.delay, self.retries = timeout, delay, retries
        self.s = requests.Session()
        self.s.headers.update({"Content-Type": "application/json",
                               "Accept": "application/json, text/event-stream"})
        self._id, self._ready, self._last = 0, False, 0.0
        self._tools: list[dict] | None = None

    # ---- transport -------------------------------------------------------------------------
    def _post(self, payload: dict) -> dict | None:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.s.post(self.url, data=json.dumps(payload), timeout=self.timeout)
        self._last = time.monotonic()
        sid = r.headers.get("Mcp-Session-Id")
        if sid:
            self.s.headers["Mcp-Session-Id"] = sid
        if r.status_code == 202 or not r.content:
            return None
        if r.status_code >= 400:
            raise MCPError(f"HTTP {r.status_code}: {r.text[:200]}")
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            msg = None
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data:
                        obj = json.loads(data)
                        if obj.get("id") == payload.get("id"):
                            msg = obj
            return msg
        return r.json()

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        last = None
        for attempt in range(self.retries + 1):
            try:
                msg = self._post(payload)
                if msg is None:
                    raise MCPError(f"no response to {method}")
                if "error" in msg:
                    raise MCPError(f"{method}: {msg['error']}")
                return msg.get("result")
            except (requests.RequestException, ValueError, MCPError) as e:
                last = e
                if isinstance(e, MCPError) and "HTTP 4" in str(e) and "429" not in str(e):
                    break
                time.sleep(1.5 * (attempt + 1))
        raise MCPError(str(last))

    def _ensure(self) -> None:
        if self._ready:
            return
        self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                 "clientInfo": {"name": "stock-scanner", "version": "1"}})
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:  # some servers don't care about the notification
            pass
        self._ready = True

    # ---- public ----------------------------------------------------------------------------
    def tools(self) -> list[dict]:
        if self._tools is None:
            self._ensure()
            self._tools = (self._rpc("tools/list") or {}).get("tools", [])
        return self._tools

    def call(self, name: str, **args) -> Any:
        """Calls a tool. Returns structuredContent, else the text content parsed as JSON,
        else the raw text. Raises MCPError if the tool reports an error."""
        self._ensure()
        res = self._rpc("tools/call", {"name": name, "arguments": args}) or {}
        if res.get("isError"):
            raise MCPError(f"{name}: {_text(res)[:300]}")
        if res.get("structuredContent") is not None:
            sc = res["structuredContent"]
            # FastMCP wraps non-dict returns as {"result": ...}
            return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
        text = _text(res)
        try:
            return json.loads(text)
        except ValueError:
            return text


def _text(res: dict) -> str:
    return "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")


def rows(result: Any, *keys: str) -> list[dict]:
    """Pulls the list of records out of a tool result, whatever envelope it came in."""
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if isinstance(result, dict):
        for k in keys + ("data", "rows", "records", "results", "items", "history", "stocks"):
            v = result.get(k)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        for v in result.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def pick(d: dict, *names: str, default=None):
    """First present key, case- and punctuation-insensitive."""
    norm = {k.lower().replace("_", "").replace(" ", ""): v for k, v in d.items()}
    for n in names:
        v = norm.get(n.lower().replace("_", "").replace(" ", ""))
        if v not in (None, ""):
            return v
    return default


# ---- probe ---------------------------------------------------------------------------------
def _sample_args(schema: dict, day: str) -> dict:
    props = (schema or {}).get("properties", {})
    req = set((schema or {}).get("required", []))
    out = {}
    for k, p in props.items():
        kl, t = k.lower(), p.get("type")
        if "symbols" in kl or t == "array":
            v = ["RELIANCE", "TCS"]
        elif "symbol" in kl or kl in ("ticker", "keyword", "query", "name", "search", "company"):
            v = "RELIANCE"
        elif "date" in kl:
            v = day
        elif "index" in kl:
            v = "NIFTY 50"
        elif t in ("integer", "number"):
            v = 5
        elif "enum" in p:
            v = p["enum"][0]
        else:
            continue
        if k in req or kl in ("symbol", "symbols", "date", "days", "n", "top", "limit"):
            out[k] = v
    return out


def probe(day: str) -> dict:
    out: dict = {}
    for name in SERVERS:
        info: dict = {"url": SERVERS[name]}
        try:
            c = NSEMCP(name)
            tools = c.tools()
            info["tools"] = {t["name"]: {"description": (t.get("description") or "")[:400],
                                         "inputSchema": t.get("inputSchema"),
                                         "outputSchema": t.get("outputSchema")} for t in tools}
            samples = {}
            for t in tools:
                args = _sample_args(t.get("inputSchema"), day)
                try:
                    res = c.call(t["name"], **args)
                    txt = json.dumps(res, default=str)
                    samples[t["name"]] = {"args": args, "type": type(res).__name__,
                                          "len": len(txt), "sample": txt[:1500]}
                except Exception as e:
                    samples[t["name"]] = {"args": args, "error": str(e)[:300]}
            info["samples"] = samples
        except Exception as e:
            info["error"] = f"{type(e).__name__}: {e}"
        out[name] = info
    return out


if __name__ == "__main__":
    import sys
    from datetime import date, timedelta
    from pathlib import Path
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    result = probe(d.isoformat())
    text = json.dumps(result, indent=2, default=str)
    print(text[:20000])
    if len(sys.argv) > 1:
        Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)
        Path(sys.argv[1]).write_text(text + "\n")
