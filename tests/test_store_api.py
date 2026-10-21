import pytest
from fastapi.testclient import TestClient

from store_api import seed
from store_api.main import app


@pytest.fixture
def client():
    seed.reset()
    return TestClient(app)


def test_search_by_keyword(client):
    ids = [p["id"] for p in client.get("/catalog", params={"q": "wireless"}).json()]
    assert "prod_1" in ids and "prod_5" in ids


def test_search_filters(client):
    results = client.get("/catalog", params={"category": "audio", "max_price": 100}).json()
    assert {p["id"] for p in results} == {"prod_1", "prod_3"}
    assert all(p["price"] <= 100 for p in results)
    assert "price_cents" not in results[0]


def test_unknown_product(client):
    assert client.get("/catalog/nope").status_code == 404


def test_checkout_creates_order_and_decrements_stock(client):
    stock = client.get("/catalog/prod_1").json()["stock"]
    resp = client.post("/checkout", json={"items": [{"product_id": "prod_1", "qty": 2}], "ship_to": "1 Main St"})
    assert resp.status_code == 200
    order = resp.json()
    assert order["status"] == "placed"
    assert order["total"] == 159.98
    assert client.get("/catalog/prod_1").json()["stock"] == stock - 2

    fetched = client.get(f"/orders/{order['id']}").json()
    assert fetched["id"] == order["id"] and fetched["ship_to"] == "1 Main St"


def test_checkout_insufficient_stock(client):
    resp = client.post("/checkout", json={"items": [{"product_id": "prod_6", "qty": 999}]})
    assert resp.status_code == 409


def test_checkout_unknown_product_leaves_stock_untouched(client):
    stock = client.get("/catalog/prod_1").json()["stock"]
    resp = client.post("/checkout", json={"items": [{"product_id": "prod_1"}, {"product_id": "nope"}]})
    assert resp.status_code == 404
    assert client.get("/catalog/prod_1").json()["stock"] == stock


def test_unknown_order(client):
    assert client.get("/orders/order_999").status_code == 404


def test_refund_full(client):
    order = client.get("/orders/order_101").json()
    resp = client.post("/refund", json={"order_id": "order_101"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "refunded"
    assert resp.json()["refunded_amount"] == order["total"]


def test_refund_partial(client):
    resp = client.post("/refund", json={"order_id": "order_101", "amount": 10})
    assert resp.json()["refunded_amount"] == 10.0


def test_refund_over_total(client):
    assert client.post("/refund", json={"order_id": "order_101", "amount": 10_000}).status_code == 422


def test_refund_twice(client):
    client.post("/refund", json={"order_id": "order_101"})
    assert client.post("/refund", json={"order_id": "order_101"}).status_code == 409


def test_refund_unknown_order(client):
    assert client.post("/refund", json={"order_id": "order_999"}).status_code == 404
