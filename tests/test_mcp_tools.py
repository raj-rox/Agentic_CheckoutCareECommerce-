import json

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client

from mcp_server import server
from store_api import seed
from store_api.main import app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def store(monkeypatch):
    seed.reset()
    # Route the MCP tools' HTTP calls into the FastAPI app in-process.
    monkeypatch.setattr(server, "store", TestClient(app))


def data(result):
    return json.loads(result.content[0].text)


async def test_lists_all_tools():
    async with Client(server.mcp) as mcp:
        names = {t.name for t in await mcp.list_tools()}
    assert names == {"search_catalog", "get_order", "checkout", "refund"}


async def test_search_checkout_get_order_roundtrip():
    async with Client(server.mcp) as mcp:
        products = data(await mcp.call_tool("search_catalog", {"query": "headphones", "max_price": 100}))
        assert [p["id"] for p in products] == ["prod_1"]

        order = data(await mcp.call_tool("checkout", {"items": [{"product_id": "prod_1", "qty": 1}], "ship_to": "1 Main St"}))
        assert order["total"] == 79.99

        fetched = data(await mcp.call_tool("get_order", {"order_id": order["id"]}))
        assert fetched["id"] == order["id"] and fetched["status"] == "placed"


async def test_refund_tool():
    async with Client(server.mcp) as mcp:
        order = data(await mcp.call_tool("refund", {"order_id": "order_101"}))
    assert order["status"] == "refunded"


async def test_store_error_becomes_tool_error():
    async with Client(server.mcp) as mcp:
        result = await mcp.call_tool("get_order", {"order_id": "order_999"}, raise_on_error=False)
    assert result.is_error
    assert "404" in result.content[0].text
