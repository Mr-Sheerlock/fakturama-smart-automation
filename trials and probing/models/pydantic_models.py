from decimal import Decimal
from datetime import date
from pydantic import BaseModel, Field, model_validator


class Address(BaseModel):
    company: str
    street: str
    postal_code: str
    city: str
    country: str


class Customer(BaseModel):
    company: str
    customer_id: str | None = None
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    alias: str | None = None


class Payment(BaseModel):
    method: str
    status: str
    date: date


class OrderItem(BaseModel):
    sku: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal = 0
    vat_rate: Decimal
    line_total: Decimal

    @model_validator(mode="after")
    def validate_line_total(self):
        expected = (
            self.quantity
            * self.unit_price
            * (Decimal("1") - self.discount_percent / Decimal("100"))
        )

        if abs(expected - self.line_total) > Decimal("0.01"):
            raise ValueError(
                f"{self.sku}: expected line total {expected}, "
                f"got {self.line_total}"
            )

        return self


class Totals(BaseModel):
    net: Decimal
    vat: Decimal
    gross: Decimal


class Order(BaseModel):
    order_reference: str
    order_date: date
    currency: str

    customer: Customer
    billing_address: Address
    delivery_address: Address

    payment: Payment

    items: list[OrderItem]

    totals: Totals