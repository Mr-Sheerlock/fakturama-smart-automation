from decimal import Decimal
from pathlib import Path

from extractor import extract_order
from models import PaidStatus


def test_assessment_sample_image(tmp_path):
    image = Path(__file__).resolve().parents[1] / "samples" / "order.png"
    order = extract_order(image, tmp_path)

    assert order.external_reference == "WEB-2026-0714-A17"
    assert order.order_date.isoformat() == "2026-07-14"
    assert order.customer_id == "CUST-1007"
    assert order.currency == "EUR"
    assert order.debtor.company == "Northstar Office GmbH"
    assert order.debtor.first_name == "Marta"
    assert order.debtor.last_name == "Klein"
    assert order.debtor.alias == "NORTHSTAR-BERLIN"
    assert order.debtor.billing.street == "Friedrichstrasse 88"
    assert order.debtor.billing.zip == "10117"
    assert order.debtor.billing.city == "Berlin"
    assert order.debtor.delivery_address.street == "Beusselstrasse 44"
    assert order.debtor.delivery_address.zip == "10553"
    assert order.debtor.payment_method == "Bank Transfer"
    assert order.payment.status == PaidStatus.PAID
    assert order.payment.payment_date.isoformat() == "2026-07-18"
    assert len(order.items) == 2
    assert order.items[0].sku == "CHR-ERG-01"
    assert order.items[0].source_total == Decimal("450.00")
    assert order.items[1].sku == "MAT-DESK-02"
    assert order.items[1].source_total == Decimal("120.00")
    assert order.source_total_net == Decimal("570.00")
    assert order.source_vat == Decimal("108.30")
    assert order.source_total == Decimal("678.30")
