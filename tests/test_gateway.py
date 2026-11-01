import json

import pytest
from fastapi.testclient import TestClient

from gateway import audit
from gateway import main as gateway
from store_api import seed
from store_api.main import app as store_app
from tests.conftest import SERVICE_KEY
from tests.helpers import bearer, mandate, signed


@pytest.fixture
def gw(monkeypatch):
    seed.reset()
    monkeypatch.setattr(gateway, "store", TestClient(store_app))
    return TestClient(gateway.app)


def buy(gw, items, ship_to="1 Main St", agent="shop-agent", client=None, sub="user_1", mandate_=None, action="purchase"):
    args = {"items": [{"product_id": p, "qty": q} for p, q in items], "ship_to": ship_to}
    body = signed(agent, action, args, mandate=mandate_ if mandate_ is not None else mandate(sub=sub))
    return gw.post("/purchase", json=body, headers=bearer(sub=sub, client=client or agent))


def refund(gw, order_id, sub="user_1", role="customer", agent="care-agent", amount=None):
    body = signed(agent, "refund", {"order_id": order_id, "amount": amount})
    return gw.post("/refund", json=body, headers=bearer(sub=sub, role=role, client=agent))


def approve(gw, approval_id, sub="user_1", client="customer-app"):
    return gw.post(f"/approve/{approval_id}", headers=bearer(sub=sub, client=client))


def denied(resp, status, rule):
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert detail["decision"] == "deny" and detail["rule"] == rule
    return detail


def last_audit():
    return audit.entries()[-1]


# --- Purchases ---------------------------------------------------------------

def test_small_purchase_is_approved_and_logged(gw):
    resp = buy(gw, [("prod_1", 1)])
    assert resp.status_code == 200
    body = resp.json()
    order = body["order"]
    assert body["decision"] == "approve" and order["user_id"] == "user_1" and order["total"] == 79.99
    entry = last_audit()
    assert entry["id"] == body["decision_id"]
    assert (entry["outcome"], entry["agent"], entry["sub"], entry["amount"], entry["order_id"]) == \
        ("approve", "shop-agent", "user_1", 79.99, order["id"])


def test_big_purchase_steps_up_until_the_user_approves(gw):
    orders_before = len(seed.ORDERS)
    resp = buy(gw, [("prod_9", 1)])
    assert resp.status_code == 202
    approval_id = resp.json()["approval_id"]
    assert len(seed.ORDERS) == orders_before
    assert last_audit()["outcome"] == "step_up"

    shown = gw.get(f"/approvals/{approval_id}", headers=bearer(client="customer-app")).json()
    assert shown["amount"] == 199.99 and shown["agent"] == "shop-agent"

    resp = approve(gw, approval_id)
    assert resp.status_code == 200
    assert resp.json()["order"]["total"] == 199.99
    assert last_audit()["approved_by"] == "customer-app"

    denied(approve(gw, approval_id), 404, "approve.unknown")  # single use


def test_agent_cannot_approve_its_own_step_up(gw):
    approval_id = buy(gw, [("prod_9", 1)]).json()["approval_id"]
    denied(approve(gw, approval_id, client="shop-agent"), 403, "approve.client")
    assert approval_id in gateway._pending


def test_other_user_cannot_approve(gw):
    approval_id = buy(gw, [("prod_9", 1)]).json()["approval_id"]
    denied(approve(gw, approval_id, sub="user_2"), 404, "approve.unknown")
    assert approve(gw, approval_id).status_code == 200


def test_expired_approval(gw):
    approval_id = buy(gw, [("prod_9", 1)]).json()["approval_id"]
    gateway._pending[approval_id]["expires"] = 0
    denied(approve(gw, approval_id), 410, "approve.expired")


def test_mandate_cap_spans_orders(gw):
    m = mandate(max_amount=100)
    assert buy(gw, [("prod_1", 1)], mandate_=m).status_code == 200      # $79.99
    denied(buy(gw, [("prod_5", 1)], mandate_=m), 403, "mandate.max_amount")  # +$29.99 > $100


def test_step_up_is_rechecked_when_approved(gw):
    m = mandate(max_amount=300)
    approval_id = buy(gw, [("prod_9", 1)], mandate_=m).json()["approval_id"]  # $199.99, held
    assert buy(gw, [("prod_1", 1)], mandate_=m).status_code == 200  # meanwhile $79.99
    assert buy(gw, [("prod_7", 1)], mandate_=m).status_code == 200  # and $39.99
    detail = denied(approve(gw, approval_id), 403, "mandate.max_amount")
    assert detail["reason"].startswith("No longer allowed")


def test_over_mandate_is_denied(gw):
    denied(buy(gw, [("prod_6", 1)]), 403, "mandate.max_amount")  # $349.99 > $300


def test_bulk_buy_from_injected_text_is_denied(gw):
    denied(buy(gw, [("prod_5", 50)], ship_to="666 Attacker Ave"), 403, "mandate.ship_to")
    denied(buy(gw, [("prod_5", 50)]), 403, "mandate.max_amount")


@pytest.mark.parametrize("mandate_, rule", [
    pytest.param("", "mandate", id="none"),
    pytest.param("not-a-jwt", "mandate", id="garbage"),
    pytest.param(lambda: mandate(sub="user_2"), "mandate", id="other-user"),
    pytest.param(lambda: mandate(agent="care-agent"), "mandate", id="other-agent"),
    pytest.param(lambda: mandate(ttl=-10), "mandate", id="expired"),
])
def test_bad_mandate(gw, mandate_, rule):
    denied(buy(gw, [("prod_1", 1)], mandate_=mandate_() if callable(mandate_) else mandate_), 403, rule)


def test_user_token_is_not_a_mandate(gw):
    token = bearer()["Authorization"].removeprefix("Bearer ")
    denied(buy(gw, [("prod_1", 1)], mandate_=token), 403, "mandate")


def test_care_agent_cannot_buy(gw):
    detail = denied(buy(gw, [("prod_1", 1)], agent="care-agent", mandate_=mandate(agent="care-agent")), 403, "allowed_actions")
    assert "care-agent may not purchase" in detail["reason"]


def test_token_and_signature_must_be_the_same_agent(gw):
    # A shop-agent token replayed by another agent that signs with its own key.
    denied(buy(gw, [("prod_1", 1)], agent="care-agent", client="shop-agent"), 403, "signature.client_id")


def test_signature_for_another_action_is_refused(gw):
    denied(buy(gw, [("prod_1", 1)], action="refund"), 403, "signature.action")


def test_tampered_args_are_refused(gw):
    body = signed("shop-agent", "purchase", {"items": [{"product_id": "prod_1", "qty": 1}], "ship_to": "1 Main St"}, mandate())
    body["envelope"]["args"]["items"][0]["qty"] = 3
    denied(gw.post("/purchase", json=body, headers=bearer()), 401, "signature")


def test_replayed_request_is_refused(gw):
    body = signed("shop-agent", "purchase", {"items": [{"product_id": "prod_1", "qty": 1}], "ship_to": "1 Main St"}, mandate())
    assert gw.post("/purchase", json=body, headers=bearer()).status_code == 200
    denied(gw.post("/purchase", json=body, headers=bearer()), 401, "signature")


def test_unsigned_request_is_refused(gw):
    denied(gw.post("/purchase", json={"mandate": mandate()}, headers=bearer()), 401, "signature")


def test_missing_token(gw):
    body = signed("shop-agent", "purchase", {"items": [{"product_id": "prod_1"}]}, mandate())
    denied(gw.post("/purchase", json=body), 401, "token")


def test_missing_scope(gw):
    body = signed("shop-agent", "purchase", {"items": [{"product_id": "prod_1"}]}, mandate())
    denied(gw.post("/purchase", json=body, headers=bearer(scopes=["orders:read"])), 403, "token.scope")


def test_unknown_product(gw):
    denied(buy(gw, [("nope", 1)]), 404, "args")


def test_store_refusal_is_logged(gw):
    resp = buy(gw, [("prod_6", 9)], mandate_=mandate(max_amount=10_000))  # only 8 in stock
    assert resp.status_code == 202
    assert approve(gw, resp.json()["approval_id"]).status_code == 409
    assert last_audit()["outcome"] == "error"


# --- Refunds -----------------------------------------------------------------

def test_small_refund_in_window_is_approved(gw):
    resp = refund(gw, "order_101")  # $29.99, 10 days old
    assert resp.status_code == 200 and resp.json()["order"]["status"] == "refunded"


def test_refund_outside_window_is_denied(gw):
    denied(refund(gw, "order_102", sub="user_2"), 403, "refund.window")  # 45 days old
    assert seed.ORDERS["order_102"].status == "placed"


def test_refund_of_someone_elses_order_looks_missing(gw):
    other = denied(refund(gw, "order_102"), 404, "ownership")
    missing = denied(refund(gw, "order_999"), 404, "ownership")
    assert other["reason"].replace("order_102", "X") == missing["reason"].replace("order_999", "X")


def test_big_refund_steps_up(gw):
    store = TestClient(store_app, headers={"X-Service-Key": SERVICE_KEY})
    order = store.post("/checkout", json={"items": [{"product_id": "prod_9"}], "user_id": "user_1"}).json()
    resp = refund(gw, order["id"])
    assert resp.status_code == 202
    assert seed.ORDERS[order["id"]].status == "placed"
    assert approve(gw, resp.json()["approval_id"]).json()["order"]["status"] == "refunded"


def test_staff_can_refund_any_order(gw):
    assert refund(gw, "order_101", sub="staff_1", role="staff").status_code == 200


def test_shop_agent_cannot_refund(gw):
    denied(refund(gw, "order_101", agent="shop-agent"), 403, "allowed_actions")


def test_refund_twice_is_denied(gw):
    assert refund(gw, "order_101").status_code == 200
    denied(refund(gw, "order_101"), 403, "refund.already_refunded")


# --- Reads -------------------------------------------------------------------

def test_order_reads_are_filtered_by_user(gw):
    assert gw.get("/orders/order_101", headers=bearer(client="care-agent")).status_code == 200
    assert gw.get("/orders/order_102", headers=bearer(client="care-agent")).status_code == 404
    assert gw.get("/orders/order_102", headers=bearer(sub="staff_1", role="staff", client="care-agent")).status_code == 200
    assert gw.get("/orders/order_101").status_code == 401


# --- Audit log ---------------------------------------------------------------

def test_every_decision_is_chained_and_holds_no_secrets(gw):
    buy(gw, [("prod_1", 1)])
    buy(gw, [("prod_6", 1)])
    refund(gw, "order_102")
    assert [e["outcome"] for e in audit.entries()] == ["approve", "deny", "deny"]
    assert audit.verify_chain() is None
    assert "eyJ" not in audit.AUDIT_PATH.read_text()  # no JWTs

    lines = audit.AUDIT_PATH.read_text().splitlines()
    edited = json.loads(lines[1])
    edited["outcome"] = "approve"
    lines[1] = json.dumps(edited)
    audit.AUDIT_PATH.write_text("\n".join(lines) + "\n")
    assert audit.verify_chain() == 2
