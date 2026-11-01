"""Risk rules. Stage 2.

Pure functions: facts in, Decision out, always with a reason. No I/O, no
clock, no model, so the same request always gets the same answer and the
rules are tested like any other code.

| Action   | Approve                         | Step-up              | Deny                           |
|----------|---------------------------------|----------------------|--------------------------------|
| Purchase | under $100, inside mandate      | $100 to mandate cap  | outside mandate                |
| Refund   | under $50, inside return window | $50 and above        | outside window, already refunded |
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from gateway.mandate import Mandate

PURCHASE_STEP_UP_CENTS = 100_00
REFUND_STEP_UP_CENTS = 50_00
RETURN_WINDOW = timedelta(days=30)

Outcome = Literal["approve", "step_up", "deny"]


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    rule: str


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _same_address(a: str | None, b: str) -> bool:
    return a is not None and " ".join(a.casefold().split()) == " ".join(b.casefold().split())


def decide_purchase(total_cents: int, ship_to: str | None, merchant_id: str,
                    mandate: Mandate, spent_cents: int) -> Decision:
    """`spent_cents` is what earlier orders under this mandate already used."""
    if merchant_id != mandate.merchant_id:
        return Decision("deny", f"Mandate covers {mandate.merchant_id}, not {merchant_id}", "mandate.merchant")
    if not _same_address(ship_to, mandate.ship_to):
        return Decision("deny", f"Mandate only allows shipping to {mandate.ship_to!r}, got {ship_to!r}", "mandate.ship_to")
    remaining = mandate.max_cents - spent_cents
    if total_cents > remaining:
        return Decision(
            "deny",
            f"{_dollars(total_cents)} exceeds the mandate: {_dollars(remaining)} left of {_dollars(mandate.max_cents)}",
            "mandate.max_amount",
        )
    if total_cents >= PURCHASE_STEP_UP_CENTS:
        return Decision("step_up", f"{_dollars(total_cents)} is {_dollars(PURCHASE_STEP_UP_CENTS)} or more: user must approve", "purchase.step_up")
    return Decision("approve", f"{_dollars(total_cents)} is inside the mandate and under {_dollars(PURCHASE_STEP_UP_CENTS)}", "purchase.approve")


def decide_refund(amount_cents: int, order_total_cents: int, order_status: str,
                  order_created_at: datetime, now: datetime) -> Decision:
    if order_status == "refunded":
        return Decision("deny", "Order is already refunded", "refund.already_refunded")
    if amount_cents > order_total_cents:
        return Decision("deny", f"{_dollars(amount_cents)} exceeds the order total {_dollars(order_total_cents)}", "refund.over_total")
    age = now - order_created_at
    if age > RETURN_WINDOW:
        return Decision("deny", f"Order is {age.days} days old; the return window is {RETURN_WINDOW.days} days", "refund.window")
    if amount_cents >= REFUND_STEP_UP_CENTS:
        return Decision("step_up", f"{_dollars(amount_cents)} is {_dollars(REFUND_STEP_UP_CENTS)} or more: user must approve", "refund.step_up")
    return Decision("approve", f"{_dollars(amount_cents)} is inside the return window and under {_dollars(REFUND_STEP_UP_CENTS)}", "refund.approve")
