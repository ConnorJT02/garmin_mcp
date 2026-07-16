"""Reusable Claude tool-use loop, shared by the dashboard's two chat
surfaces (chat.py's read-only Q&A, plan_chat.py's plan-building chat).

Each surface gets its own AgenticChat instance with its own system prompt,
tool allowlist, and conversation history — they are otherwise identical.
"""
import asyncio
import datetime
import json
from typing import Any, Dict, List, Optional

import anthropic
from mcp.server.fastmcp import FastMCP

from garmin_mcp.dashboard import insights


class ChatError(Exception):
    """Raised when a chat turn can't be completed (missing key, API error, tool-round limit)."""


async def _invoke_tool(app: FastMCP, tool_name: str, arguments: Dict[str, Any]) -> dict:
    """Call an already-registered MCP tool in-process and unwrap its JSON result."""
    try:
        content, _structured = await app.call_tool(tool_name, arguments)
    except Exception as exc:
        return {"error": str(exc)}

    if not content:
        return {"error": f"{tool_name} returned no content"}

    text = getattr(content[0], "text", None)
    if text is None:
        return {"error": f"{tool_name} returned non-text content"}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


class AgenticChat:
    """One independent conversation loop: a system prompt, a fixed tool
    allowlist (never every tool registered on the app — see the security
    notes in chat.py/plan_chat.py), and its own in-memory history."""

    def __init__(
        self,
        system_prompt: str,
        allowed_tools: frozenset,
        *,
        max_tool_rounds: int = 6,
        max_tokens: int = 1024,
    ):
        self._system_prompt = system_prompt
        self._allowed_tools = allowed_tools
        self._max_tool_rounds = max_tool_rounds
        self._max_tokens = max_tokens
        self._client: Optional[anthropic.AsyncAnthropic] = None
        self._history: List[Dict[str, Any]] = []
        # Serializes turns against _history — guards against a second
        # tab/request racing a turn already in flight, which would
        # otherwise interleave writes to _history and corrupt it.
        self._lock = asyncio.Lock()

    def configure(self, client: Optional[anthropic.AsyncAnthropic] = None) -> None:
        """Same seam as every other garmin_mcp module: real client if
        ANTHROPIC_API_KEY is set, explicit fake client in tests, otherwise
        unconfigured."""
        if client is not None:
            self._client = client
            return
        import os

        if os.getenv("ANTHROPIC_API_KEY"):
            self._client = anthropic.AsyncAnthropic()
        else:
            self._client = None

    def is_configured(self) -> bool:
        return self._client is not None

    async def reset(self) -> None:
        async with self._lock:
            self._history.clear()

    async def _claude_tools(self, app: FastMCP) -> List[Dict[str, Any]]:
        mcp_tools = await app.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in mcp_tools
            if tool.name in self._allowed_tools
        ]

    async def ask(self, app: FastMCP, message: str) -> str:
        """Send one user turn through the tool-use loop and return Claude's
        final text reply. Conversation history persists across calls until
        reset() is called."""
        if self._client is None:
            raise ChatError(insights.NOT_CONFIGURED_MESSAGE)

        tools = await self._claude_tools(app)
        system = f"{self._system_prompt}\n\nToday's date is {datetime.date.today().isoformat()}."

        async with self._lock:
            snapshot = list(self._history)
            self._history.append({"role": "user", "content": message})
            try:
                for _ in range(self._max_tool_rounds):
                    try:
                        response = await self._client.messages.create(
                            model=insights.MODEL,
                            max_tokens=self._max_tokens,
                            system=system,
                            thinking={"type": "disabled"},
                            tools=tools,
                            messages=self._history,
                        )
                    except anthropic.APIError as exc:
                        raise ChatError(f"Chat failed: {exc}") from exc

                    self._history.append({"role": "assistant", "content": response.content})

                    if response.stop_reason != "tool_use":
                        text = "".join(
                            block.text for block in response.content if block.type == "text"
                        ).strip()
                        if not text:
                            raise ChatError("Chat returned no text.")
                        return text

                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue
                        payload = await _invoke_tool(app, block.name, block.input or {})
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(payload),
                            }
                        )
                    self._history.append({"role": "user", "content": tool_results})

                raise ChatError("Chat couldn't finish within the tool-call limit — try a narrower question.")
            except ChatError:
                self._history[:] = snapshot
                raise
