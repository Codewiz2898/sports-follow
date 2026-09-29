"""Web tools the agent runs locally: search (DuckDuckGo) and fetch (any page or JSON API).

The model goes through the llm-providers gateway, which carries plain client-side tools only, so
web access lives here rather than in a provider's server-side tools.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
import lxml.html
import trafilatura
from ddgs import DDGS

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
DEFAULT_CHARS = 12_000
MAX_CHARS = 40_000

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": (
            "Search the web. kind='news' returns recent articles with publish dates (use it for the "
            "latest news); kind='web' returns ordinary results (fixtures pages, live score pages, "
            "profiles). Returns title, url, snippet and, for news, date and source."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {"type": "string", "enum": ["web", "news"]},
                "max_results": {"type": "integer", "description": "1-10, default 6"},
            },
            "required": ["query", "kind"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch a URL and return its main text (HTML pages) or compact JSON (APIs). Use it to "
            "read a live score page, fixtures list, article or a public JSON sports API. "
            f"Output is cut at max_chars (default {DEFAULT_CHARS}, max {MAX_CHARS})."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
]


class ToolError(Exception):
    pass


def web_search(query: str, kind: str = "web", max_results: int = 6) -> str:
    max_results = max(1, min(int(max_results or 6), 10))
    try:
        with DDGS() as ddgs:
            if kind == "news":
                rows = ddgs.news(query, max_results=max_results)
                results = [
                    {"title": r.get("title"), "url": r.get("url"), "date": r.get("date"),
                     "source": r.get("source"), "snippet": r.get("body")}
                    for r in rows
                ]
            else:
                rows = ddgs.text(query, max_results=max_results)
                results = [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in rows]
    except Exception as exc:  # ddgs raises its own exception types for rate limits and timeouts
        raise ToolError(f"search failed: {exc}") from exc
    return json.dumps(results, ensure_ascii=False) if results else "No results."


def _check_public(url: httpx.URL) -> None:
    """Refuse loopback/private targets: the model picks the URLs, and local services live on this machine."""
    host = url.host
    try:
        infos = socket.getaddrinfo(host, url.port or (443 if url.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ToolError(f"cannot resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ToolError(f"refusing to fetch non-public address {host}")


def _on_request(request: httpx.Request) -> None:
    _check_public(request.url)  # runs for the first request and every redirect hop


_http = httpx.Client(
    headers={"user-agent": USER_AGENT, "accept-language": "en"},
    timeout=20,
    follow_redirects=True,
    event_hooks={"request": [_on_request]},
)


def _html_text(html: str) -> str:
    text = trafilatura.extract(html, include_tables=True, include_links=False, favor_recall=True) or ""
    if len(text) >= 400:
        return text
    # Score/fixture pages are often tables and widgets that article extraction drops.
    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return text
    for bad in doc.xpath("//script|//style|//noscript|//svg"):
        bad.drop_tree()
    lines = (line.strip() for line in doc.text_content().splitlines())
    return "\n".join(line for line in lines if line)


def fetch_url(url: str, max_chars: int = DEFAULT_CHARS) -> str:
    if urlparse(url).scheme not in ("http", "https"):
        raise ToolError("only http(s) URLs can be fetched")
    max_chars = max(1000, min(int(max_chars or DEFAULT_CHARS), MAX_CHARS))
    try:
        res = _http.get(url)
    except httpx.HTTPError as exc:
        raise ToolError(f"fetch failed: {exc}") from exc
    if res.status_code >= 400:
        raise ToolError(f"HTTP {res.status_code} from {res.url}")
    ctype = res.headers.get("content-type", "")
    if "json" in ctype:
        try:
            text = json.dumps(res.json(), separators=(",", ":"), ensure_ascii=False)
        except ValueError:
            text = res.text
    elif "html" in ctype:
        text = _html_text(res.text)
    else:
        text = res.text
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n…[cut at {max_chars} of {len(text)} chars]"
    return f"URL: {res.url}\n\n{text}"


def run_tool(name: str, args: dict[str, Any]) -> str:
    if name == "web_search":
        return web_search(str(args.get("query", "")), str(args.get("kind", "web")), args.get("max_results", 6))
    if name == "fetch_url":
        return fetch_url(str(args.get("url", "")), args.get("max_chars", DEFAULT_CHARS))
    raise ToolError(f"unknown tool {name}")


def describe(name: str, args: dict[str, Any]) -> str:
    if name == "web_search":
        return f"{'News' if args.get('kind') == 'news' else 'Searching'}: {args.get('query', '')}"
    if name == "fetch_url":
        return f"Reading: {args.get('url', '')}"
    return "Compiling the report…"
