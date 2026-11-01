"""MCP server for the store. Stage 2: money moves through the trust gateway.

Every request needs a valid user token (mcp_server/auth.py), and each agent
sees only the tools in its allowlist (mcp_server/allowlist.py). search_catalog
reads the public catalog directly; everything else goes through the gateway,
which re-checks the token and owns every decision about orders.

checkout and refund carry the agent runtime's signed envelope (and, for
purchases, the user's mandate) in the call's _meta. This server forwards
them untouched: it holds no agent keys, and the gateway acts on the signed
args only, so nothing here can change what the agent asked for.

    python -m mcp_server.server
"""

import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import require_scopes
from fastmcp.server.dependencies import get_access_token, get_context
from fastmcp.server.middleware import AuthMiddleware
from pydantic import BaseModel, Field

from mcp_server.allowlist import agent_may_use
from mcp_server.auth import StoreTokenVerifier

load_dotenv()

STORE_API_URL = os.getenv("STORE_API_URL", "http://localhost:8001")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8002")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

mcp = FastMCP(
    "store",
    instructions="Tools for searching the store catalog, placing orders and refunds.",
    auth=StoreTokenVerifier(),
    middleware=[AuthMiddleware(auth=agent_may_use)],
)
store = httpx.Client(base_url=STORE_API_URL, timeout=10.0)
gateway = httpx.Client(base_url=GATEWAY_URL, timeout=10.0)


class LineItem(BaseModel):
    product_id: str = Field(description="Product ID from search_catalog, e.g. prod_1")
    qty: int = Field(default=1, ge=1)


def _call(client: httpx.Client, name: str, method: str, path: str, **kwargs) -> dict | list:
    try:
        resp = client.request(method, path, **kwargs)
    except httpx.HTTPError as e:
        raise ToolError(f"{name} unreachable: {e}") from e
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        if isinstance(detail, dict):  # a gateway decision: show its reason and audit ID
            detail = f"{detail['reason']} (decision {detail['decision_id']})"
        raise ToolError(f"{name} error {resp.status_code}: {detail}")
    return resp.json()


def _bearer() -> dict:
    token = get_access_token()
    if token is None:
        raise ToolError("Not authenticated")
    return {"Authorization": f"Bearer {token.token}"}


def _signed(*fields: str) -> dict:
    """The agent's signature (and mandate) from this call's _meta, passed on as-is."""
    meta = get_context().request_context.meta or {}
    return {f: meta.get(f) for f in ("envelope", "sig", *fields)}


@mcp.tool
def search_catalog(query: str = "", category: Optional[str] = None, max_price: Optional[float] = None) -> list[dict]:
    """Search the product catalog by keywords. Optionally filter by category or max price in dollars."""
    params = {"q": query, "category": category, "max_price": max_price}
    return _call(store, "Store API", "GET", "/catalog", params={k: v for k, v in params.items() if v not in (None, "")})


@mcp.tool(auth=require_scopes("orders:read"))
def get_order(order_id: str) -> dict:
    """Look up an order by ID, e.g. order_101."""
    return _call(gateway, "Gateway", "GET", f"/orders/{order_id}", headers=_bearer())


@mcp.tool(auth=require_scopes("payment:execute"), meta={"action": "purchase"})
def checkout(items: list[LineItem], ship_to: Optional[str] = None) -> dict:
    """Place an order for one or more products. The store's trust gateway decides: an approved
    order comes back with its ID and total; a step_up needs the customer to approve it first,
    using the approval_id."""
    return _call(gateway, "Gateway", "POST", "/purchase", json=_signed("mandate"), headers=_bearer())


@mcp.tool(auth=require_scopes("refund:request"), meta={"action": "refund"})
def refund(order_id: str, amount: Optional[float] = None) -> dict:
    """Refund an order. Amount is in dollars and defaults to the full order total. The gateway
    may approve it, deny it, or return a step_up the customer must approve."""
    return _call(gateway, "Gateway", "POST", "/refund", json=_signed(), headers=_bearer())


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=MCP_PORT)
