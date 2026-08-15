# Fakturama Image-to-Cash Automation - Design

## 1. Goal and operating principle

The system receives one order image and must leave Fakturama with a saved Order and a linked, verified Invoice. The central design rule is **ground, act, verify**: every irreversible action is preceded by runtime evidence and followed by a persisted-state check. The automation never stores screen coordinates and never assumes that an icon remains at a particular pixel location.

The workflow is an explicit state machine: Extract -> New Order -> Debtor -> Products/VAT -> verify/save Order -> linked Invoice -> payment -> final persistence verification. The original Order editor remains open while master data is resolved.

## 2. Source-image extraction

OCR is run on several image variants (RGB, grayscale and enhanced contrast) using Tesseract. The pass with the strongest required-anchor coverage and OCR confidence is selected. Tokens retain their bounding boxes, so extraction is spatial rather than relying on a brittle flat text dump.

For the assessment layout, labels such as **EXTERNAL REFERENCE**, **ORDER DATE**, **COMPANY**, **BILLING ADDRESS**, **PAYMENT METHOD**, **SKU**, and the total labels become anchors. Values are read from the corresponding region below/beside each anchor. Billing and delivery blocks are separated from the detected label positions, not from fixed x-coordinates. Item rows are reconstructed from token order: SKU, description, Qty, Unit, unit net, Discount, VAT, and line net.

The structured model validates every item line, total net, VAT total and gross total before opening Fakturama. A disagreement becomes a review condition rather than being carried into accounting records.

## 3. Fakturama control grounding

### Primary: Microsoft UI Automation

`pywinauto` uses the UIA backend. Controls are selected by runtime Name, AutomationId, ControlType, relationships to labels, and their current rectangles. Editor tabs are discovered dynamically and remembered when a new Order, Debtor, Product, or Invoice editor appears.

### Secondary: OCR grounding

Eclipse/SWT can expose painted tables or icon-only actions poorly through UIA. When semantic UIA metadata is unavailable, a screenshot of the *current Fakturama window* is OCRed. Multi-word phrases are located as live bounding boxes and clicks are computed from those boxes.

Selector rows use UIA DataItems when available and OCR row reconstruction otherwise. Exact Debtor matching requires the source Company, First Name, Name, ZIP and City. Product matching requires exact SKU. Multiple matches always stop.

### Icon-only controls

The screenshots distinguish an upper existing-record selector icon from a lower green `+`. The automation first uses accessible button names. If the button is unnamed, it finds buttons relative to semantic anchors such as **Addresses** or live item headers. For green `+` actions, a final computer-vision fallback detects green controls in the current window and chooses the candidate nearest the relevant semantic anchor. No absolute location is persisted.

For item-grid editing, the live SKU row and live column header are grounded and their intersection is clicked. This makes Qty/U.Price/VAT/Discount editing independent of window size or column position.

## 4. Master data and document flow

The Order is opened first; its generated number is never changed. Date, Cust.Ref., Net mode and With VAT are set and verified.

The Order's own address selector is the Debtor existence check. If no exact row exists, a Debtor is created in a separate editor while the Order stays open. The billing address receives Invoice role. If delivery differs, a second address is created and receives Delivery role. Alias, 0% discount, Net mode and payment method are set. Missing payment methods are resolved under Data > terms of payment and validated against the required payment-code mapping before reuse.

For each item, the Order's product selector is the Product existence check. Missing products trigger VAT resolution first. An existing VAT is reusable only when Name, Value and E-Invoice Standard-rate code agree. Product gross master price is calculated from unit net price and VAT; transaction-line discount is deliberately excluded. After saving, the Product must be reselectable from the still-open Order.

The completed Order verifies addresses, all source rows, shipping/discount and totals before one Save. Data > Documents > Orders then proves persistence and open state.

The Invoice is created only from the saved Order's **Create a follow-up document** area, anchored locally so the global toolbar Invoice action cannot be selected. Copied reference, addresses, Order Date, items and totals are checked. Payment method is set; PAID input sets paid, Pay Date and full invoice Value. After Save, Documents > Invoices verifies Invoice state/total while Documents > Orders verifies the source Order remains open. The Invoice is reopened because that is the only way to prove the payment fields persisted, then paid state/date/value are checked again.

## 5. Failure policy and tradeoffs

The system is intentionally strict about accounting identity. OCR uncertainty can be retried through another preprocessing pass, but conflicting master records are not fuzzy-matched. Likewise, UI grounding is retried through UIA, OCR and visual-relative fallbacks, but the automation stops if multiple controls remain equally plausible.

This costs completion rate compared with coordinate macros, but it gives three properties that matter more here: portability across window layouts, auditability through screenshots/JSON/UIA dumps, and protection against silently selecting the wrong customer or product.
