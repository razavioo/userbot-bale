from __future__ import annotations

import os
import sys

import pytest

from userbot_bale.control.auth import AuthStore


@pytest.mark.asyncio
async def test_mcp_stdio_handshake_lists_and_calls_tools(tmp_path, monkeypatch) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    monkeypatch.setenv("USERBOT_BALE_HOME", str(tmp_path))
    monkeypatch.setenv("USERBOT_BALE_SECRET_BACKEND", "file")
    AuthStore().save_jwt("header.payload.signature", user_id=42, phone="+989121234567")

    env = os.environ.copy()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "userbot_bale", "mcp", "serve"],
        env=env,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tools_by_name = {tool.name: tool for tool in tools.tools}
            assert set(tools_by_name) == {
                "account_status",
                "list_messages",
                "search_messages",
                "search_contacts",
                "resolve_phone",
                "list_dialogs",
                "send_text",
                "mark_read",
            }
            assert tools_by_name["list_messages"].annotations.readOnlyHint is True
            assert tools_by_name["send_text"].annotations.readOnlyHint is False
            assert tools_by_name["send_text"].annotations.idempotentHint is False
            assert tools_by_name["mark_read"].annotations.idempotentHint is True

            # Every tool must expose a top-level object schema (dict-shaped
            # structuredContent), never a bare array under a magic key.
            for tool in tools.tools:
                schema = tool.outputSchema or {}
                assert schema.get("type") == "object", tool.name

            # send_text takes an optional second-phase confirmation token.
            send_props = (tools_by_name["send_text"].inputSchema or {}).get("properties", {})
            assert "confirm_token" in send_props

            result = await session.call_tool("account_status")
            assert result.isError is False
            assert result.structuredContent == {
                "state": "configured",
                "user_id": "42",
                "expires_in": "unknown",
                "allowed_peers": [],
            }
