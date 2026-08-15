from decimal import Decimal

from models import Item


def test_discounted_line_and_product_gross_price():
    item = Item(
        sku="CHR-ERG-01",
        description="Ergonomic Desk Chair",
        quantity=Decimal("2"),
        unit="pcs",
        unit_net_price=Decimal("250.00"),
        vat_percent=Decimal("19"),
        discount_percent=Decimal("10"),
        source_total=Decimal("450.00"),
    )
    assert item.expected_line_net == Decimal("450.00")
    assert item.expected_line_vat == Decimal("85.50")
    # Master price ignores line discount per the assignment.
    assert item.product_gross_price == Decimal("297.50")
    assert item.vat_name == "VAT 19%"
