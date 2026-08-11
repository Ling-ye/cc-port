"""Exercise a packaged CC Port executable as a CLI and stdio MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from typing import TextIO

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def smoke(agent: Path, *, timeout_seconds: float = 30.0) -> dict[str, object]:
    help_result = subprocess.run(
        [str(agent), "--help"],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if help_result.returncode != 0 or "CC Port" not in help_result.stdout:
        raise RuntimeError("Packaged CLI help smoke failed.")
    names, status_call_ok = _run_mcp_smoke(agent, timeout_seconds=timeout_seconds)
    if "asset_inventory" not in names:
        raise RuntimeError("Packaged MCP tool manifest is missing asset_inventory.")
    if not status_call_ok:
        raise RuntimeError("Packaged MCP cc_port_status call failed.")
    return {
        "ok": True,
        "tool_count": len(names),
        "tools": names,
        "status_call_ok": True,
    }


def _run_mcp_smoke(agent: Path, *, timeout_seconds: float) -> tuple[list[str], bool]:
    async def session_flow() -> tuple[list[str], bool]:
        parameters = StdioServerParameters(
            command=str(agent),
            args=["mcp", "--stdio"],
        )
        # MCP server diagnostics belong to this probe, not its machine-readable
        # stdout/stderr contract consumed by the Windows release orchestrator.
        # Windows subprocess creation requires errlog to expose a real fileno;
        # io.StringIO therefore cannot be used here.
        with _open_error_log() as error_log:
            async with stdio_client(parameters, errlog=error_log) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    called = await session.call_tool("cc_port_status", {})
                    return [tool.name for tool in listed.tools], called.isError is False

    async def with_timeout() -> tuple[list[str], bool]:
        return await asyncio.wait_for(session_flow(), timeout=timeout_seconds)

    return asyncio.run(with_timeout())


def _open_error_log() -> TextIO:
    return tempfile.TemporaryFile(mode="w+", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent", type=Path)
    args = parser.parse_args()
    result = smoke(args.agent)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
