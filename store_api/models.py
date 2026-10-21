"""Store data models. Money is held as integer cents; responses show dollars."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field


def to_dollars(cents: int) -> float:
    return round(cents / 100, 2)


class Product(BaseModel):
    id: str
    name: str
    description: str
    category: str
    price_cents: int = Field(exclude=True)
    stock: int

    @computed_field
    @property
    def price(self) -> float:
        return to_dollars(self.price_cents)


class OrderItem(BaseModel):
    product_id: str
    name: str
    qty: int
    unit_price_cents: int = Field(exclude=True)

    @computed_field
    @property
    def unit_price(self) -> float:
        return to_dollars(self.unit_price_cents)


class Order(BaseModel):
    id: str
    user_id: Optional[str] = None
    merchant_id: str = "merchant_1"
    items: list[OrderItem]
    ship_to: Optional[str] = None
    total_cents: int = Field(exclude=True)
    refunded_cents: int = Field(default=0, exclude=True)
    status: Literal["placed", "refunded"] = "placed"
    created_at: datetime

    @computed_field
    @property
    def total(self) -> float:
        return to_dollars(self.total_cents)

    @computed_field
    @property
    def refunded_amount(self) -> float:
        return to_dollars(self.refunded_cents)


class CheckoutItem(BaseModel):
    product_id: str
    qty: int = Field(default=1, ge=1)


class CheckoutRequest(BaseModel):
    items: list[CheckoutItem] = Field(min_length=1)
    ship_to: Optional[str] = None
    user_id: Optional[str] = None


class RefundRequest(BaseModel):
    order_id: str
    amount: Optional[float] = Field(default=None, gt=0, description="Dollars; defaults to the full order total")
