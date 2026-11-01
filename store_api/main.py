"""Mock merchant API.

Stage 2: orders, checkout and refunds only answer the trust gateway, which
proves itself with the shared X-Service-Key header. The catalog stays public.
The checks themselves live in the gateway; the store just keeps the records.
"""

import hmac
import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException

from store_api import seed
from store_api.models import CheckoutRequest, Order, OrderItem, Product, RefundRequest

load_dotenv()

app = FastAPI(title="Store API")


def gateway_only(x_service_key: Optional[str] = Header(None)) -> None:
    expected = os.getenv("STORE_SERVICE_KEY")
    if not expected:
        raise HTTPException(503, "STORE_SERVICE_KEY is not set; the store refuses order calls without it")
    if x_service_key is None or not hmac.compare_digest(x_service_key, expected):
        raise HTTPException(401, "Only the trust gateway may call this endpoint")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/catalog", response_model=list[Product])
def search_catalog(q: Optional[str] = None, category: Optional[str] = None, max_price: Optional[float] = None):
    results = list(seed.PRODUCTS.values())
    if q:
        terms = q.lower().split()
        results = [
            p for p in results
            if all(t in f"{p.name} {p.description} {p.category}".lower() for t in terms)
        ]
    if category:
        results = [p for p in results if p.category.lower() == category.lower()]
    if max_price is not None:
        results = [p for p in results if p.price_cents <= round(max_price * 100)]
    return results


@app.get("/catalog/{product_id}", response_model=Product)
def get_product(product_id: str):
    product = seed.PRODUCTS.get(product_id)
    if product is None:
        raise HTTPException(404, f"Unknown product: {product_id}")
    return product


@app.get("/orders/{order_id}", response_model=Order, dependencies=[Depends(gateway_only)])
def get_order(order_id: str):
    order = seed.ORDERS.get(order_id)
    if order is None:
        raise HTTPException(404, f"Unknown order: {order_id}")
    return order


@app.post("/checkout", response_model=Order, dependencies=[Depends(gateway_only)])
def checkout(req: CheckoutRequest):
    items = []
    for line in req.items:
        product = seed.PRODUCTS.get(line.product_id)
        if product is None:
            raise HTTPException(404, f"Unknown product: {line.product_id}")
        if product.stock < line.qty:
            raise HTTPException(409, f"Insufficient stock for {product.id}: {product.stock} left")
        items.append(OrderItem(product_id=product.id, name=product.name, qty=line.qty, unit_price_cents=product.price_cents))

    for item in items:
        seed.PRODUCTS[item.product_id].stock -= item.qty

    order = Order(
        id=seed.new_order_id(),
        user_id=req.user_id,
        items=items,
        ship_to=req.ship_to,
        total_cents=sum(i.unit_price_cents * i.qty for i in items),
        created_at=datetime.now(timezone.utc),
    )
    seed.ORDERS[order.id] = order
    return order


@app.post("/refund", response_model=Order, dependencies=[Depends(gateway_only)])
def refund(req: RefundRequest):
    order = seed.ORDERS.get(req.order_id)
    if order is None:
        raise HTTPException(404, f"Unknown order: {req.order_id}")
    if order.status == "refunded":
        raise HTTPException(409, f"Order {order.id} is already refunded")
    amount_cents = order.total_cents if req.amount is None else round(req.amount * 100)
    if amount_cents > order.total_cents:
        raise HTTPException(422, f"Refund {amount_cents / 100:.2f} exceeds order total {order.total:.2f}")
    order.refunded_cents = amount_cents
    order.status = "refunded"
    return order
