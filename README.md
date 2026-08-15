# Fakturama Image-to-Cash Automation

A flat Python project: **no package install and no `src/` layout**. Run `main.py` directly.

The implementation follows the supplied assessment PDF and its screenshots: OCR/geometry extraction from the order image, UIA-first Fakturama control discovery, exact Debtor/Product matching, conditional master-data creation, Order-first document flow, linked Invoice generation, payment application, and persisted verification.

## Files

```text
main.py             <- run this
runner.py           orchestration / status
extractor.py        structured image parser
ocr_engine.py       OCR + preprocessing
models.py           validated order model and calculations
ui.py               UIA/OCR/CV grounding primitives
fakturama.py        Fakturama business workflow
errors.py           domain errors
requirements.txt
samples/order.png   sample image extracted from the assessment PDF
```

There is intentionally no `pyproject.toml` and no editable package install. Imports are ordinary files in the same directory.

## Requirements

- Windows 10/11
- Python 3.11+
- Fakturama open in a normal visible desktop session
- Tesseract OCR installed

Install the Python dependencies into whichever Python environment you use (global Python is fine):

```powershell
python -m pip install -r requirements.txt
```

Check Tesseract:

```powershell
where.exe tesseract
tesseract --version
```

If it is installed but not on PATH:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## First: test extraction without touching Fakturama

From the project root:

```powershell
python main.py --extract-only
```

`main.py` defaults to `samples/order.png`, so this also works from VS Code's normal **Run Python File** button.

For your own image:

```powershell
python main.py "F:\path\to\order.png" --extract-only
```

The supplied sample should extract:

- external reference `WEB-2026-0714-A17`
- order date `2026-07-14`
- Northstar Office GmbH / Marta Klein
- distinct billing and delivery addresses
- Bank Transfer / PAID / 2026-07-18
- `CHR-ERG-01`: 2 x 250.00, 10% discount, 19% VAT, line net 450.00
- `MAT-DESK-02`: 3 x 40.00, 0% discount, 19% VAT, line net 120.00
- net 570.00, VAT 108.30, gross 678.30

## Run the full Fakturama automation

1. Start Fakturama.
2. Open a disposable/test company/workspace.
3. Keep exactly one main Fakturama window visible and do not lock/minimize the desktop.
4. Run:

```powershell
python main.py samples\order.png
```

For a separate evidence directory:

```powershell
python main.py samples\order.png --evidence-dir artifacts\demo-001
```

For the first run on a Fakturama build, I recommend also saving its accessibility tree:

```powershell
python main.py samples\order.png --dump-uia --evidence-dir artifacts\demo-001
```

This does not change the grounding strategy; it simply leaves `uia_tree.txt` in the evidence folder for debugging/version calibration.

## What changed after reviewing the assessment screenshots

The original starter was intentionally conservative. This revision is not limited by that timebox:

- The OCR parser now handles the actual **card/column layout** shown in the source image instead of expecting `Label: value` lines.
- OCR uses the original/grayscale/contrast variants and chooses the pass with the strongest anchor coverage and confidence.
- The item table is reconstructed from OCR token geometry; it supports multiple lines and the sample's Unit column.
- Both **billing and distinct delivery addresses** are modeled and the Debtor creation flow can create a second address and assign the Delivery role.
- Debtor/Product selector tables have an **OCR-row fallback** when SWT rows are not exposed as UIA DataItems.
- The green `+` controls have a semantic UIA fallback and an anchor-relative computer-vision fallback; no absolute coordinates are stored.
- Order item cells are edited by grounding the live SKU row and live column header, not by fixed screen positions.
- The Order/Invoice editor tabs are tracked dynamically instead of assuming a tab is literally named `Order` or `Debtor`.
- Existing VAT/payment definitions are selected and validated before reuse.
- The Documents view is treated as the screenshot shows it: select **Orders** or **Invoices** in the Documents tree, then verify the corresponding row.
- The linked Invoice action is anchored to **Create a follow-up document** so it cannot accidentally use the global toolbar Invoice button.
- Invoice payment handling includes the screenshot's `Pay Date` label and a payment-method combo fallback near the `paid` checkbox.
- The final Invoice is reopened to verify that paid state/date/value persisted.

## Evidence

Each run writes evidence such as:

```text
artifacts/<run>/
  preprocessed_source.png
  ocr.txt
  ocr_strategy.txt
  extracted_order.json
  uia_tree.txt                 # when --dump-uia is used
  <timestamp>-01-new-order.png
  <timestamp>-02-debtor-selected.png
  ...
  <timestamp>-08-invoice-persistence-verified.png
  run_status.json
```

## Tests

```powershell
python -m pytest -q
```

The integration-style extraction test runs OCR against the actual sample image included in this repo, rather than only testing a fabricated text string.

## Safe stopping

Ambiguity is still intentionally a stop condition because the assignment itself requires manual review for conflicting Debtors, duplicate SKUs, or conflicting master definitions. `MANUAL_REVIEW` therefore means the automation refused to guess; it is not a missing timebox branch.

Exit codes:

- `0`: completed, or extraction-only completed
- `2`: manual review required
- `1`: unexpected failure
