"""MCP server for the store. Stage 0: every tool calls the store API directly.

Gaps (closed in later stages): no user token, no per-agent allowlist (any
agent sees every tool, including refund), no limits, no audit log.
"""

import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

load_dotenv()

STORE_API_URL = os.getenv("STORE_API_URL", "http://localhost:8001")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

mcp = FastMCP("store", instructions="Tools for searching the store catalog, placing orders and refunds.")
store = httpx.Client(base_url=STORE_API_URL, timeout=10.0)


class LineItem(BaseModel):
    product_id: str = Field(description="Product ID from search_catalog, e.g. prod_1")
    qty: int = Field(default=1, ge=1)


def _call(method: str, path: str, **kwargs) -> dict | list:
    try:
        resp = store.request(method, path, **kwargs)
    except httpx.HTTPError as e:
        raise ToolError(f"Store API unreachable: {e}") from e
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ToolError(f"Store API error {resp.status_code}: {detail}")
    return resp.json()


@mcp.tool
def search_catalog(query: str = "", category: Optional[str] = None, max_price: Optional[float] = None) -> list[dict]:
    """Search the product catalog by keywords. Optionally filter by category or max price in dollars."""
    params = {"q": query, "category": category, "max_price": max_price}
    return _call("GET", "/catalog", params={k: v for k, v in params.items() if v not in (None, "")})


@mcp.tool
def get_order(order_id: str) -> dict:
    """Look up an order by ID, e.g. order_101."""
    return _call("GET", f"/orders/{order_id}")


@mcp.tool
def checkout(items: list[LineItem], ship_to: Optional[str] = None) -> dict:
    """Place an order for one or more products. Returns the created order, including its order ID and total."""
    body = {"items": [i.model_dump() for i in items], "ship_to": ship_to}
    return _call("POST", "/checkout", json=body)


@mcp.tool
def refund(order_id: str, amount: Optional[float] = None) -> dict:
    """Refund an order. Amount is in dollars and defaults to the full order total."""
    body = {"order_id": order_id, "amount": amount}
    return _call("POST", "/refund", json=body)


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=MCP_PORT)
