from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

MONEY = Decimal("0.01")
TOLERANCE = Decimal("0.02")


def money(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def normalized_decimal_text(value: Decimal) -> str:
    value = value.normalize()
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class PaidStatus(str, Enum):
    PAID = "PAID"
    UNPAID = "UNPAID"
    UNKNOWN = "UNKNOWN"


class Address(BaseModel):
    street: str
    zip: str
    city: str
    country: str
    email: str = ""
    telephone: str = ""
    additional_name: str = ""
    address_specification: str = ""
    district: str = ""

    def exact_key(self) -> tuple[str, str, str, str]:
        return (
            self.street.strip().casefold(),
            self.zip.strip().casefold(),
            self.city.strip().casefold(),
            self.country.strip().casefold(),
        )


class Debtor(BaseModel):
    company: str
    first_name: str = ""
    last_name: str = ""
    alias: str = ""
    billing: Address
    delivery: Optional[Address] = None
    payment_method: str

    @property
    def delivery_address(self) -> Address:
        return self.delivery or self.billing

    @property
    def same_delivery_address(self) -> bool:
        return self.delivery is None or self.delivery.exact_key() == self.billing.exact_key()

    def selector_required_values(self) -> list[str]:
        # These are the exact fields required by the assignment's selector rule.
        return [
            self.company,
            self.first_name,
            self.last_name,
            self.billing.zip,
            self.billing.city,
        ]


class Item(BaseModel):
    sku: str
    description: str
    quantity: Decimal = Field(gt=0)
    unit: str = ""
    unit_net_price: Decimal = Field(ge=0)
    vat_percent: Decimal = Field(ge=0)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    source_total: Decimal = Field(ge=0)

    @property
    def expected_line_net(self) -> Decimal:
        return money(
            self.quantity
            * self.unit_net_price
            * (Decimal("1") - self.discount_percent / Decimal("100"))
        )

    @property
    def expected_line_vat(self) -> Decimal:
        return money(self.expected_line_net * self.vat_percent / Decimal("100"))

    @property
    def product_gross_price(self) -> Decimal:
        return money(self.unit_net_price * (Decimal("1") + self.vat_percent / Decimal("100")))

    @property
    def vat_name(self) -> str:
        return f"VAT {normalized_decimal_text(self.vat_percent)}%"

    @model_validator(mode="after")
    def validate_source_line_total(self) -> "Item":
        if abs(self.expected_line_net - money(self.source_total)) > TOLERANCE:
            raise ValueError(
                f"Line {self.sku}: source total {money(self.source_total)} does not match "
                f"qty x unit net x discount = {self.expected_line_net}"
            )
        return self


class Payment(BaseModel):
    status: PaidStatus = PaidStatus.UNKNOWN
    payment_date: Optional[date] = None

    @model_validator(mode="after")
    def validate_payment_date(self) -> "Payment":
        if self.status == PaidStatus.PAID and self.payment_date is None:
            raise ValueError("payment_date is required when paid status is PAID")
        return self


class OrderInput(BaseModel):
    order_date: date
    external_reference: str
    customer_id: str = ""
    currency: str = ""
    debtor: Debtor
    items: list[Item] = Field(min_length=1)
    source_total_net: Decimal = Field(ge=0)
    source_vat: Decimal = Field(ge=0)
    source_total: Decimal = Field(ge=0)
    order_discount_percent: Decimal = Decimal("0")
    shipping_net: Decimal = Decimal("0")
    payment: Payment

    @model_validator(mode="after")
    def validate_totals(self) -> "OrderInput":
        line_net = sum((item.expected_line_net for item in self.items), Decimal("0"))
        # Source images in this task are net-priced. Order-level discount is represented
        # explicitly for future layouts; the supplied assessment image uses 0%.
        net_after_discount = line_net * (
            Decimal("1") - self.order_discount_percent / Decimal("100")
        )
        computed_net = money(net_after_discount + self.shipping_net)
        computed_vat = money(sum((item.expected_line_vat for item in self.items), Decimal("0")))
        computed_total = money(computed_net + computed_vat)

        if abs(computed_net - money(self.source_total_net)) > TOLERANCE:
            raise ValueError(
                f"Net total mismatch: computed={computed_net} source={money(self.source_total_net)}"
            )
        if abs(computed_vat - money(self.source_vat)) > TOLERANCE:
            raise ValueError(
                f"VAT total mismatch: computed={computed_vat} source={money(self.source_vat)}"
            )
        if abs(computed_total - money(self.source_total)) > TOLERANCE:
            raise ValueError(
                f"Gross total mismatch: computed={computed_total} source={money(self.source_total)}"
            )
        return self
