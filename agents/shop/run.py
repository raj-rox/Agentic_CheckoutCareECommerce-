"""Shop agent: Claude chat loop wired to the store MCP server.

Stage 0: the agent sees every tool the MCP server exposes and nothing checks
what it buys.

    python -m agents.shop.run                       # interactive
    python -m agents.shop.run --once "buy a mouse"  # single request
"""

import argparse
import asyncio
import json
import os

import anthropic
from dotenv import load_dotenv
from fastmcp import Client

from agents.shop.prompt import SYSTEM_PROMPT

load_dotenv()

MODEL = "claude-opus-5-5"
MCP_URL = os.getenv("MCP_URL", "http://localhost:8000/mcp")


async def load_tools(mcp: Client) -> list[dict]:
    tools = await mcp.list_tools()
    return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema} for t in tools]


async def run_tool(mcp: Client, block) -> dict:
    print(f"  → {block.name} {json.dumps(block.input)}")
    try:
        result = await mcp.call_tool(block.name, block.input, raise_on_error=False)
        text = "\n".join(c.text for c in result.content if c.type == "text")
        is_error = result.is_error
    except Exception as e:  # transport failure: report it to the model rather than crash
        text, is_error = f"Tool call failed: {e}", True
    if is_error:
        print(f"    ✗ {text}")
    return {"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error}


async def turn(claude: anthropic.AsyncAnthropic, mcp: Client, tools: list[dict], messages: list) -> None:
    """Run one user turn to completion, appending everything to messages."""
    while True:
        response = await claude.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\nagent: {block.text}")

        if response.stop_reason == "tool_use":
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            results = await asyncio.gather(*(run_tool(mcp, b) for b in tool_uses))
            messages.append({"role": "user", "content": list(results)})
            continue
        if response.stop_reason == "refusal":
            print("\nagent: [request declined]")
        elif response.stop_reason == "max_tokens":
            print("\nagent: [response cut off at max_tokens]")
        return


async def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 0 shop agent")
    parser.add_argument("--once", metavar="REQUEST", help="run a single request and exit")
    args = parser.parse_args()

    claude = anthropic.AsyncAnthropic()
    async with Client(MCP_URL) as mcp:
        tools = await load_tools(mcp)
        print(f"Connected to {MCP_URL}. Tools: {', '.join(t['name'] for t in tools)}")
        messages: list = []

        if args.once:
            messages.append({"role": "user", "content": args.once})
            await turn(claude, mcp, tools, messages)
            return

        while True:
            try:
                user = input("\nyou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not user:
                continue
            if user in {"exit", "quit"}:
                return
            messages.append({"role": "user", "content": user})
            await turn(claude, mcp, tools, messages)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except anthropic.AuthenticationError:
        raise SystemExit("Claude API authentication failed. Set ANTHROPIC_API_KEY in .env.")
    except anthropic.APIError as e:
        raise SystemExit(f"Claude API error: {e}")
