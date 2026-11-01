import json

import httpx
import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.utilities.tests import run_server_async

from idp.issue_token import issue_token
from mcp_server import server
from store_api import seed
from store_api.main import app

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    # Module-scoped so every test shares one event loop and one running server:
    # a FastMCP instance can't be restarted on a fresh loop.
    return "asyncio"


@pytest.fixture(autouse=True)
def store(monkeypatch):
    seed.reset()
    # Route the MCP tools' HTTP calls into the FastAPI app in-process.
    monkeypatch.setattr(server, "store", TestClient(app))


@pytest.fixture(scope="module")
async def url():
    # Real HTTP: tokens only reach the server over an HTTP transport.
    async with run_server_async(server.mcp) as url:
        yield url


def as_agent(url, client="shop-agent", sub="user_1", role="customer", scopes=None):
    return Client(url, auth=issue_token(sub, role, client, scopes=scopes))


def data(result):
    return json.loads(result.content[0].text)


async def tool_names(mcp):
    return {t.name for t in await mcp.list_tools()}


@pytest.mark.parametrize("headers", [
    pytest.param({}, id="no-token"),
    pytest.param({"Authorization": "Bearer not-a-jwt"}, id="bad-token"),
])
async def test_unauthenticated_request_gets_401(url, headers):
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"},
    }}
    async with httpx.AsyncClient() as http:
        resp = await http.post(url, json=initialize, headers={**headers, "Accept": "application/json, text/event-stream"})
    assert resp.status_code == 401


async def test_each_agent_sees_only_its_allowlist(url):
    async with as_agent(url, "shop-agent") as mcp:
        assert await tool_names(mcp) == {"search_catalog", "checkout"}
    async with as_agent(url, "care-agent") as mcp:
        assert await tool_names(mcp) == {"get_order", "refund"}
    async with as_agent(url, "unknown-agent") as mcp:
        assert await tool_names(mcp) == set()


async def test_shop_agent_cannot_refund(url):
    async with as_agent(url, "shop-agent") as mcp:
        result = await mcp.call_tool("refund", {"order_id": "order_101"}, raise_on_error=False)
    assert result.is_error
    assert seed.ORDERS["order_101"].status == "placed"


async def test_checkout_needs_payment_scope(url):
    async with as_agent(url, "shop-agent", scopes=["orders:read"]) as mcp:
        assert await tool_names(mcp) == {"search_catalog"}
        result = await mcp.call_tool("checkout", {"items": [{"product_id": "prod_1"}]}, raise_on_error=False)
    assert result.is_error
    assert seed.PRODUCTS["prod_1"].stock == 25


async def test_checkout_records_buyer_and_handoff(url):
    async with as_agent(url, "shop-agent", sub="user_1") as mcp:
        products = data(await mcp.call_tool("search_catalog", {"query": "headphones", "max_price": 100}))
        assert [p["id"] for p in products] == ["prod_1"]
        order = data(await mcp.call_tool("checkout", {"items": [{"product_id": "prod_1", "qty": 1}], "ship_to": "1 Main St"}))
    assert order["user_id"] == "user_1" and order["total"] == 79.99

    async with as_agent(url, "care-agent", sub="user_1") as mcp:
        fetched = data(await mcp.call_tool("get_order", {"order_id": order["id"]}))
    assert fetched["id"] == order["id"] and fetched["status"] == "placed"


async def test_customer_cannot_see_others_orders(url):
    async with as_agent(url, "care-agent", sub="user_1") as mcp:
        assert data(await mcp.call_tool("get_order", {"order_id": "order_101"}))["user_id"] == "user_1"
        other = await mcp.call_tool("get_order", {"order_id": "order_102"}, raise_on_error=False)
        missing = await mcp.call_tool("get_order", {"order_id": "order_999"}, raise_on_error=False)
    assert other.is_error and missing.is_error
    # Someone else's order looks exactly like one that doesn't exist.
    assert other.content[0].text.replace("order_102", "X") == missing.content[0].text.replace("order_999", "X")


async def test_staff_can_see_any_order(url):
    async with as_agent(url, "care-agent", sub="staff_1", role="staff") as mcp:
        for order_id in ("order_101", "order_102"):
            assert data(await mcp.call_tool("get_order", {"order_id": order_id}))["id"] == order_id


async def test_customer_refunds_only_own_orders(url):
    async with as_agent(url, "care-agent", sub="user_1") as mcp:
        other = await mcp.call_tool("refund", {"order_id": "order_102"}, raise_on_error=False)
        own = data(await mcp.call_tool("refund", {"order_id": "order_101"}))
    assert other.is_error and seed.ORDERS["order_102"].status == "placed"
    assert own["status"] == "refunded"


async def test_store_error_becomes_tool_error(url):
    async with as_agent(url, "care-agent") as mcp:
        result = await mcp.call_tool("get_order", {"order_id": "order_999"}, raise_on_error=False)
    assert result.is_error
    assert "404" in result.content[0].text
