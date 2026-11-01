"""MCP server for the store. Stage 1: authenticated, with a per-agent allowlist.

Every request needs a valid user token (mcp_server/auth.py). Each agent sees
only the tools in its allowlist (mcp_server/allowlist.py), and each tool
needs its own user scope. Orders are filtered by the token's sub.

Gaps (closed in later stages): the store API itself is still open, and there
are no amount limits, no mandate, no return window and no audit log.

    python -m mcp_server.server
"""

import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import require_scopes
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import AuthMiddleware
from pydantic import BaseModel, Field

from mcp_server.allowlist import agent_may_use
from mcp_server.auth import StoreTokenVerifier

load_dotenv()

STORE_API_URL = os.getenv("STORE_API_URL", "http://localhost:8001")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

mcp = FastMCP(
    "store",
    instructions="Tools for searching the store catalog, placing orders and refunds.",
    auth=StoreTokenVerifier(),
    middleware=[AuthMiddleware(auth=agent_may_use)],
)
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


def _caller() -> tuple[str, str]:
    """The (sub, role) of the user whose token made this request."""
    token = get_access_token()
    if token is None:
        raise ToolError("Not authenticated")
    return token.claims["sub"], token.claims["role"]


def _owned_order(order_id: str) -> dict:
    """Fetch an order the caller may see: their own, or any order for staff."""
    sub, role = _caller()
    order = _call("GET", f"/orders/{order_id}")
    if role != "staff" and order.get("user_id") != sub:
        # Same message as a real 404, so customers can't probe for other users' orders.
        raise ToolError(f"Store API error 404: Unknown order: {order_id}")
    return order


@mcp.tool
def search_catalog(query: str = "", category: Optional[str] = None, max_price: Optional[float] = None) -> list[dict]:
    """Search the product catalog by keywords. Optionally filter by category or max price in dollars."""
    params = {"q": query, "category": category, "max_price": max_price}
    return _call("GET", "/catalog", params={k: v for k, v in params.items() if v not in (None, "")})


@mcp.tool(auth=require_scopes("orders:read"))
def get_order(order_id: str) -> dict:
    """Look up an order by ID, e.g. order_101."""
    return _owned_order(order_id)


@mcp.tool(auth=require_scopes("payment:execute"))
def checkout(items: list[LineItem], ship_to: Optional[str] = None) -> dict:
    """Place an order for one or more products. Returns the created order, including its order ID and total."""
    sub, _ = _caller()
    body = {"items": [i.model_dump() for i in items], "ship_to": ship_to, "user_id": sub}
    return _call("POST", "/checkout", json=body)


@mcp.tool(auth=require_scopes("refund:request"))
def refund(order_id: str, amount: Optional[float] = None) -> dict:
    """Refund an order. Amount is in dollars and defaults to the full order total."""
    _owned_order(order_id)
    body = {"order_id": order_id, "amount": amount}
    return _call("POST", "/refund", json=body)


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=MCP_PORT)
