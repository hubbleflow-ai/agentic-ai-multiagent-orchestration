"""planner · lazy MCP tool registry shared across @tool wrappers.

The Planner calls 3 MCP servers DIRECTLY (no agent layer): mcp-trip-state,
mcp-payment, mcp-calendar. Each one's tools are loaded via a single
MultiServerMCPClient on first use and cached for the process lifetime.

The @tool wrappers in tools.py use `_call_mcp(name, args)` to invoke
specific MCP tools by name. This module owns the registry + the
normalisation of MCP TextContent envelopes back into plain Python dicts.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from services.planner.config import MCP_CALENDAR_URL, MCP_PAYMENT_URL, MCP_TRIP_STATE_URL

log = logging.getLogger(__name__)


_mcp_tool_registry: dict[str, Any] | None = None
_mcp_client: MultiServerMCPClient | None = None


async def ensure_mcp_tools() -> dict[str, Any]:
    """Connect to all 3 MCP servers on first call · cache the merged tool list.

    Names from the 3 servers are FLAT in this registry (no server prefix).
    There's no name collision in our setup; if a future MCP server adds
    a duplicate name we'd need to either pass `tool_name_prefix=True`
    to MultiServerMCPClient or namespace lookups manually.
    """
    global _mcp_tool_registry, _mcp_client
    if _mcp_tool_registry is not None:
        return _mcp_tool_registry
    _mcp_client = MultiServerMCPClient({
        "trip_state": {"transport": "streamable_http", "url": MCP_TRIP_STATE_URL},
        "payment":    {"transport": "streamable_http", "url": MCP_PAYMENT_URL},
        "calendar":   {"transport": "streamable_http", "url": MCP_CALENDAR_URL},
    })
    tools = await _mcp_client.get_tools()
    _mcp_tool_registry = {t.name: t for t in tools}
    log.info("planner.mcp_tools.loaded count=%d names=%s",
             len(_mcp_tool_registry), list(_mcp_tool_registry.keys()))
    return _mcp_tool_registry


async def call_mcp(tool_name: str, args: dict) -> Any:
    """Call an MCP-bound tool and normalise the return to a plain dict.

    langchain-mcp-adapters returns ToolMessage-shaped content. MCP wraps
    every tool return as one-or-more TextContent blocks of the form
    `{"type": "text", "text": "<JSON>"}`. We have to UNWRAP those blocks
    BEFORE falling back to "list-of-one-dict → return-the-dict" rules,
    otherwise the caller receives the transport envelope (with keys
    `type` and `text`) instead of the actual payload (`ok`, `limit_inr`,
    etc.) and downstream handlers can't find the fields they need.
    """
    tools = await ensure_mcp_tools()
    if tool_name not in tools:
        raise KeyError(f"MCP tool {tool_name!r} not loaded · available: {list(tools)}")
    raw = await tools[tool_name].ainvoke(args)

    # Normalise to a parsed Python value.
    if isinstance(raw, str):
        try:
            parsed: Any = json.loads(raw)
        except (ValueError, TypeError):
            return {"_raw": raw}
    else:
        parsed = raw

    # Step 1 · if parsed is a list of MCP TextContent blocks, unwrap each.
    if (
        isinstance(parsed, list)
        and parsed
        and all(
            isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
            for b in parsed
        )
    ):
        unwrapped: list[Any] = []
        for block in parsed:
            try:
                unwrapped.append(json.loads(block["text"]))
            except (ValueError, TypeError):
                unwrapped.append(block["text"])
        # Single-block tools return a single value (the wrapped dict/list).
        if len(unwrapped) == 1:
            return unwrapped[0]
        return unwrapped

    # Step 2 · plain list with one dict element (some adapter variants
    # auto-unwrap text blocks before us) · take the dict.
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        return parsed[0]

    return parsed
