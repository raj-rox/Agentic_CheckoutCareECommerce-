from datetime import datetime, timedelta, timezone

import pytest

from gateway.mandate import Mandate
from gateway.rules import decide_purchase, decide_refund

MANDATE = Mandate(id="mnd_1", sub="user_1", agent="shop-agent", max_cents=300_00, merchant_id="merchant_1", ship_to="1 Main St")
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("total, ship_to, merchant, spent, outcome, rule", [
    (79_99, "1 Main St", "merchant_1", 0, "approve", "purchase.approve"),
    (99_99, "1 Main St", "merchant_1", 0, "approve", "purchase.approve"),
    (100_00, "1 Main St", "merchant_1", 0, "step_up", "purchase.step_up"),
    (300_00, "1 Main St", "merchant_1", 0, "step_up", "purchase.step_up"),
    (300_01, "1 Main St", "merchant_1", 0, "deny", "mandate.max_amount"),
    (50_00, "1 Main St", "merchant_1", 260_00, "deny", "mandate.max_amount"),
    (40_00, "1 Main St", "merchant_1", 260_00, "approve", "purchase.approve"),
    (10_00, "  1 MAIN   st ", "merchant_1", 0, "approve", "purchase.approve"),
    (10_00, "9 Elsewhere Rd", "merchant_1", 0, "deny", "mandate.ship_to"),
    (10_00, None, "merchant_1", 0, "deny", "mandate.ship_to"),
    (10_00, "1 Main St", "merchant_2", 0, "deny", "mandate.merchant"),
])
def test_purchase(total, ship_to, merchant, spent, outcome, rule):
    decision = decide_purchase(total, ship_to, merchant, MANDATE, spent)
    assert (decision.outcome, decision.rule) == (outcome, rule)
    assert decision.reason


@pytest.mark.parametrize("amount, total, status, age_days, outcome, rule", [
    (29_99, 29_99, "placed", 10, "approve", "refund.approve"),
    (49_99, 199_99, "placed", 10, "approve", "refund.approve"),
    (50_00, 199_99, "placed", 10, "step_up", "refund.step_up"),
    (29_99, 29_99, "placed", 30, "approve", "refund.approve"),
    (29_99, 29_99, "placed", 31, "deny", "refund.window"),
    (199_99, 199_99, "placed", 45, "deny", "refund.window"),
    (29_99, 29_99, "refunded", 1, "deny", "refund.already_refunded"),
    (30_00, 29_99, "placed", 1, "deny", "refund.over_total"),
])
def test_refund(amount, total, status, age_days, outcome, rule):
    decision = decide_refund(amount, total, status, NOW - timedelta(days=age_days), NOW)
    assert (decision.outcome, decision.rule) == (outcome, rule)
    assert decision.reason
