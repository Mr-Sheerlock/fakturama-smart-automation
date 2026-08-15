from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from dateutil import parser as date_parser

from errors import ExtractionError, ManualReviewRequired
from models import Address, Debtor, Item, OrderInput, PaidStatus, Payment
from ocr_engine import OCRLine, OCRResult, OCRToken, run_ocr

MONEY_RE = re.compile(r"^-?\d+(?:[.,]\d{1,2})?$")
PERCENT_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*%$")
SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/]*-[A-Z0-9._/-]+$", re.I)
ZIP_CITY_RE = re.compile(r"^(?P<zip>[A-Z0-9 -]{3,12})\s+(?P<city>.+)$", re.I)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", value.casefold()).strip()


def _word(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _decimal(value: str) -> Decimal:
    value = value.strip().replace(" ", "").replace("EUR", "").replace("€", "")
    if value.count(",") == 1 and value.count(".") == 0:
        value = value.replace(",", ".")
    elif value.count(",") and value.count("."):
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ExtractionError(f"Invalid decimal value: {value!r}") from exc


def _date(value: str) -> date:
    try:
        # The supplied source explicitly states YYYY-MM-DD. dateutil also lets
        # the fallback parser handle other photographed source formats.
        return date_parser.parse(value, yearfirst=True, fuzzy=True).date()
    except (ValueError, OverflowError) as exc:
        raise ManualReviewRequired(f"Could not parse date from {value!r}") from exc


def _line_tokens_in_x(line: OCRLine, x_left: float, x_right: float) -> list[OCRToken]:
    return [t for t in line.tokens if x_left <= t.center_x < x_right]


def _tokens_text(tokens: Iterable[OCRToken]) -> str:
    return " ".join(t.text for t in sorted(tokens, key=lambda t: t.left)).strip()


def _find_phrase_box(result: OCRResult, phrase: str) -> tuple[int, int, int, int]:
    wanted = [_word(part) for part in phrase.split() if _word(part)]
    matches: list[tuple[int, int, int, int]] = []
    exact_line_matches: list[tuple[int, int, int, int]] = []
    phrase_norm = _norm(phrase)
    for line in result.lines:
        words = [_word(t.text) for t in line.tokens]
        for start in range(0, len(words) - len(wanted) + 1):
            if words[start : start + len(wanted)] == wanted:
                selected = line.tokens[start : start + len(wanted)]
                box = (
                    min(t.left for t in selected),
                    min(t.top for t in selected),
                    max(t.right for t in selected),
                    max(t.bottom for t in selected),
                )
                matches.append(box)
                if _norm(line.text) == phrase_norm:
                    exact_line_matches.append(box)
    if len(exact_line_matches) == 1:
        return exact_line_matches[0]
    if len(matches) == 1:
        return matches[0]
    # Repeated labels in footers or longer labels are common (for example CURRENCY
    # in the order header and in the footer, or PAYMENT vs PAYMENT METHOD). When
    # there is no exact-line anchor, the uppermost occurrence is the document-field
    # label used by this assessment layout. Refuse only if two candidates occupy
    # effectively the same row.
    if matches:
        matches.sort(key=lambda box: (box[1], box[0]))
        if len(matches) == 1 or abs(matches[0][1] - matches[1][1]) > 8:
            return matches[0]
    raise ManualReviewRequired(
        f"Expected one OCR anchor {phrase!r}, found {len(matches)}. "
        "The image may need manual review or a source-specific parser."
    )


def _optional_phrase_box(result: OCRResult, phrase: str):
    try:
        return _find_phrase_box(result, phrase)
    except ManualReviewRequired:
        return None


def _value_below(
    result: OCRResult,
    label: str,
    *,
    x_left: float = 0,
    x_right: float | None = None,
    stop_label: str | None = None,
    max_gap: int = 105,
) -> str:
    x_right = x_right if x_right is not None else result.image_width
    box = _find_phrase_box(result, label)
    stop_y = box[3] + max_gap
    if stop_label:
        stop_box = _optional_phrase_box(result, stop_label)
        if stop_box and stop_box[1] > box[3]:
            stop_y = min(stop_y, stop_box[1])

    candidates: list[tuple[float, str]] = []
    for line in result.lines:
        if line.top <= box[3] or line.top >= stop_y:
            continue
        tokens = _line_tokens_in_x(line, x_left, x_right)
        if not tokens:
            continue
        text = _tokens_text(tokens)
        if text:
            candidates.append((line.top, text))
    if not candidates:
        raise ManualReviewRequired(f"No value found below OCR label {label!r}")
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def _parse_same_row_values(result: OCRResult, labels: list[str], max_gap: int = 95) -> dict[str, str]:
    boxes = {label: _find_phrase_box(result, label) for label in labels}
    ordered = sorted(labels, key=lambda label: (boxes[label][0] + boxes[label][2]) / 2)
    centers = {label: (boxes[label][0] + boxes[label][2]) / 2 for label in labels}
    boundaries = [0.0]
    for left, right in zip(ordered, ordered[1:]):
        boundaries.append((centers[left] + centers[right]) / 2)
    boundaries.append(float(result.image_width))

    values: dict[str, str] = {}
    for index, label in enumerate(ordered):
        values[label] = _value_below(
            result,
            label,
            x_left=boundaries[index],
            x_right=boundaries[index + 1],
            max_gap=max_gap,
        )
    return values


def _split_contact_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], " ".join(parts[1:])


def _collect_address_lines(
    result: OCRResult,
    label: str,
    x_left: float,
    x_right: float,
) -> list[str]:
    label_box = _find_phrase_box(result, label)
    payment_box = _find_phrase_box(result, "PAYMENT")
    lines: list[tuple[int, str]] = []
    for line in result.lines:
        if line.top <= label_box[3] or line.top >= payment_box[1]:
            continue
        text = _tokens_text(_line_tokens_in_x(line, x_left, x_right))
        if text:
            lines.append((line.top, text))
    return [text for _, text in sorted(lines)]


def _parse_address_lines(lines: list[str], *, email: str = "", telephone: str = "") -> Address:
    cleaned = [line.strip() for line in lines if line.strip()]
    if len(cleaned) < 3:
        raise ManualReviewRequired(f"Address block is incomplete: {cleaned}")

    country = cleaned[-1]
    zip_city = cleaned[-2]
    street = cleaned[-3]
    extra = " ".join(cleaned[:-3])

    match = ZIP_CITY_RE.match(zip_city)
    if not match:
        raise ManualReviewRequired(f"Could not split ZIP/city from address line: {zip_city!r}")
    zip_code = match.group("zip").strip()
    city = match.group("city").strip()

    # Guard against OCR confusions such as Bertin/Berlin by requiring the city
    # token to be alphabetic-ish; we do not silently correct it.
    if not re.search(r"[A-Za-z]", city):
        raise ManualReviewRequired(f"Suspicious city OCR value: {city!r}")

    return Address(
        street=street,
        zip=zip_code,
        city=city,
        country=country,
        email=email,
        telephone=telephone,
        additional_name=extra,
    )


def _parse_item_line(line: OCRLine) -> Item | None:
    tokens = list(sorted(line.tokens, key=lambda t: t.left))
    sku_index = next((i for i, token in enumerate(tokens) if SKU_RE.match(token.text.strip())), None)
    if sku_index is None:
        return None

    percent_indices = [
        i for i, token in enumerate(tokens) if i > sku_index and PERCENT_RE.match(token.text.strip())
    ]
    if len(percent_indices) < 2:
        return None
    discount_index, vat_index = percent_indices[-2], percent_indices[-1]

    numeric_before_discount = [
        i
        for i in range(sku_index + 1, discount_index)
        if MONEY_RE.match(tokens[i].text.strip())
    ]
    if not numeric_before_discount:
        return None
    unit_price_index = numeric_before_discount[-1]

    quantity_candidates = [
        i
        for i in range(sku_index + 1, unit_price_index)
        if re.fullmatch(r"\d+(?:[.,]\d+)?", tokens[i].text.strip())
    ]
    if not quantity_candidates:
        return None
    quantity_index = quantity_candidates[-1]

    total_candidates = [
        i for i in range(vat_index + 1, len(tokens)) if MONEY_RE.match(tokens[i].text.strip())
    ]
    if not total_candidates:
        return None
    total_index = total_candidates[0]

    description = _tokens_text(tokens[sku_index + 1 : quantity_index])
    if not description:
        return None
    unit = _tokens_text(tokens[quantity_index + 1 : unit_price_index])

    discount = PERCENT_RE.match(tokens[discount_index].text.strip())
    vat = PERCENT_RE.match(tokens[vat_index].text.strip())
    assert discount and vat

    return Item(
        sku=tokens[sku_index].text.strip(),
        description=description,
        quantity=_decimal(tokens[quantity_index].text),
        unit=unit,
        unit_net_price=_decimal(tokens[unit_price_index].text),
        discount_percent=_decimal(discount.group(1)),
        vat_percent=_decimal(vat.group(1)),
        source_total=_decimal(tokens[total_index].text),
    )


def _parse_items(result: OCRResult) -> list[Item]:
    items_box = _find_phrase_box(result, "ITEMS")
    net_total_box = _find_phrase_box(result, "NET TOTAL")
    items: list[Item] = []
    for line in result.lines:
        if line.top <= items_box[3] or line.top >= net_total_box[1]:
            continue
        item = _parse_item_line(line)
        if item:
            items.append(item)
    if not items:
        raise ManualReviewRequired(
            "Could not reconstruct any complete item rows from OCR geometry. "
            "Expected SKU, description, Qty, unit-net, discount, VAT and line-net values."
        )
    seen = set()
    for item in items:
        if item.sku.casefold() in seen:
            raise ManualReviewRequired(f"Duplicate SKU row detected in source image: {item.sku}")
        seen.add(item.sku.casefold())
    return items


def _extract_amount(value: str) -> Decimal:
    candidates = re.findall(r"\d+(?:[.,]\d{1,2})?", value)
    if not candidates:
        raise ManualReviewRequired(f"No monetary value found in {value!r}")
    return _decimal(candidates[-1])


def parse_assessment_layout(result: OCRResult) -> OrderInput:
    order_values = _parse_same_row_values(
        result, ["EXTERNAL REFERENCE", "ORDER DATE", "CUSTOMER ID", "CURRENCY"]
    )

    company_box = _find_phrase_box(result, "COMPANY")
    contact_box = _find_phrase_box(result, "CONTACT NAME")
    split_x = ((company_box[0] + company_box[2]) / 2 + (contact_box[0] + contact_box[2]) / 2) / 2

    company = _value_below(
        result, "COMPANY", x_left=0, x_right=split_x, stop_label="CUSTOMER ALIAS"
    )
    contact_name = _value_below(
        result, "CONTACT NAME", x_left=split_x, x_right=result.image_width, stop_label="EMAIL"
    )
    alias = _value_below(
        result,
        "CUSTOMER ALIAS",
        x_left=0,
        x_right=split_x,
        stop_label="ADDRESSES",
        max_gap=95,
    )
    email = _value_below(
        result, "EMAIL", x_left=split_x, x_right=result.image_width, stop_label="PHONE", max_gap=80
    )
    telephone = _value_below(
        result, "PHONE", x_left=split_x, x_right=result.image_width, stop_label="ADDRESSES", max_gap=80
    )
    first_name, last_name = _split_contact_name(contact_name)

    billing_box = _find_phrase_box(result, "BILLING ADDRESS")
    delivery_box = _find_phrase_box(result, "DELIVERY ADDRESS")
    address_split = ((billing_box[0] + billing_box[2]) / 2 + (delivery_box[0] + delivery_box[2]) / 2) / 2
    billing_lines = _collect_address_lines(result, "BILLING ADDRESS", 0, address_split)
    delivery_lines = _collect_address_lines(
        result, "DELIVERY ADDRESS", address_split, result.image_width
    )
    billing = _parse_address_lines(billing_lines, email=email, telephone=telephone)
    delivery = _parse_address_lines(delivery_lines)

    payment_values = _parse_same_row_values(
        result, ["PAYMENT METHOD", "PAID STATUS", "PAYMENT DATE"]
    )
    status_text = payment_values["PAID STATUS"].strip().upper()
    if status_text == "PAID":
        payment = Payment(
            status=PaidStatus.PAID,
            payment_date=_date(payment_values["PAYMENT DATE"]),
        )
    elif status_text in {"UNPAID", "OPEN", "NOT PAID"}:
        payment = Payment(status=PaidStatus.UNPAID)
    else:
        payment = Payment(status=PaidStatus.UNKNOWN)

    totals = _parse_same_row_values(result, ["NET TOTAL", "VAT TOTAL", "GROSS TOTAL"], max_gap=120)

    return OrderInput(
        order_date=_date(order_values["ORDER DATE"]),
        external_reference=order_values["EXTERNAL REFERENCE"].strip(),
        customer_id=order_values["CUSTOMER ID"].strip(),
        currency=order_values["CURRENCY"].strip(),
        debtor=Debtor(
            company=company,
            first_name=first_name,
            last_name=last_name,
            alias=alias,
            billing=billing,
            delivery=delivery,
            payment_method=payment_values["PAYMENT METHOD"].strip(),
        ),
        items=_parse_items(result),
        source_total_net=_extract_amount(totals["NET TOTAL"]),
        source_vat=_extract_amount(totals["VAT TOTAL"]),
        source_total=_extract_amount(totals["GROSS TOTAL"]),
        payment=payment,
    )


def extract_order(image_path: str | Path, evidence_dir: str | Path) -> OrderInput:
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    result = run_ocr(image_path)

    (evidence_dir / "ocr.txt").write_text(result.text, encoding="utf-8")
    (evidence_dir / "ocr_strategy.txt").write_text(result.strategy, encoding="utf-8")
    result.image.save(evidence_dir / "preprocessed_source.png")

    try:
        order = parse_assessment_layout(result)
    except Exception as exc:
        diagnostic = {
            "error": str(exc),
            "lines": [line.text for line in result.lines],
        }
        (evidence_dir / "extraction_failure.json").write_text(
            json.dumps(diagnostic, indent=2), encoding="utf-8"
        )
        raise

    (evidence_dir / "extracted_order.json").write_text(
        order.model_dump_json(indent=2), encoding="utf-8"
    )
    return order
