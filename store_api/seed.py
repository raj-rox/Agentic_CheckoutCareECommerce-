"""In-memory seed data. Call reset() to restore the initial state."""

from datetime import datetime, timedelta, timezone

from store_api.models import Order, OrderItem, Product

PRODUCTS: dict[str, Product] = {}
ORDERS: dict[str, Order] = {}
_next_order = 0

_SEED_PRODUCTS = [
    ("prod_1", "Wireless Headphones", "Over-ear Bluetooth headphones with noise cancelling.", "audio", 7999, 25),
    ("prod_2", "Earbuds Pro", "True wireless earbuds with charging case.", "audio", 12999, 40),
    ("prod_3", "Portable Speaker", "Waterproof Bluetooth speaker, 12-hour battery.", "audio", 4999, 30),
    ("prod_4", "Mechanical Keyboard", "Tenkeyless keyboard with brown switches.", "computing", 8999, 15),
    ("prod_5", "Wireless Mouse", "Ergonomic wireless mouse, USB-C charging.", "computing", 2999, 50),
    ("prod_6", "27in 4K Monitor", "27-inch 4K IPS monitor with USB-C input.", "computing", 34999, 8),
    ("prod_7", "USB-C Hub", "7-in-1 hub: HDMI, USB-A, SD card, power passthrough.", "accessories", 3999, 60),
    ("prod_8", "Laptop Stand", "Adjustable aluminium laptop stand.", "accessories", 2499, 35),
    ("prod_9", "Smart Watch", "Fitness tracking smart watch with GPS.", "wearables", 19999, 12),
    ("prod_10", "Phone Charger 65W", "65W GaN USB-C fast charger.", "accessories", 3499, 80),
]


def new_order_id() -> str:
    global _next_order
    _next_order += 1
    return f"order_{_next_order}"


def reset() -> None:
    global _next_order
    PRODUCTS.clear()
    ORDERS.clear()
    _next_order = 100
    for pid, name, desc, cat, price, stock in _SEED_PRODUCTS:
        PRODUCTS[pid] = Product(id=pid, name=name, description=desc, category=cat, price_cents=price, stock=stock)

    now = datetime.now(timezone.utc)
    for user_id, pid, qty, age_days in [("user_1", "prod_5", 1, 10), ("user_2", "prod_9", 1, 45)]:
        product = PRODUCTS[pid]
        oid = new_order_id()
        ORDERS[oid] = Order(
            id=oid,
            user_id=user_id,
            items=[OrderItem(product_id=pid, name=product.name, qty=qty, unit_price_cents=product.price_cents)],
            ship_to="1 Main St",
            total_cents=product.price_cents * qty,
            created_at=now - timedelta(days=age_days),
        )


reset()
