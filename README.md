# Fakturama Image-to-Cash Automation

A flat Python project for automating the supplied Fakturama Image-to-Cash assessment on Windows. There is no package install and no `src/` layout: run `main.py` directly.

The automation extracts an order from an image, keeps a new Fakturama Order open while resolving master data, creates or reuses Debtor/payment/VAT/Product records as needed, saves the Order, creates the linked Invoice from the Order, and verifies persisted document state.

## Project files

```text
main.py             run this
runner.py           orchestration / run status
extractor.py        structured order-image parser
ocr_engine.py       source-image OCR + preprocessing
models.py           validated order models and calculations
ui.py               UIA/OCR/runtime-geometry grounding primitives
fakturama.py        Fakturama business workflow
errors.py           domain/manual-review errors
requirements.txt
samples/order.png   assessment sample image
README.md
DESIGN.md
```

Imports are ordinary Python files in the same directory.

## Requirements

- Windows 10/11
- Python 3.11+
- Fakturama running in a normal visible desktop session
- Tesseract OCR installed
- Only one main Fakturama window visible

Install dependencies into the Python environment you use. A virtual environment is optional.

```powershell
python -m pip install -r requirements.txt
```

Check Tesseract:

```powershell
where.exe tesseract
tesseract --version
```

If Tesseract is installed but not on `PATH`:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## Test extraction first

Before touching Fakturama:

```powershell
python main.py --extract-only
```

`main.py` defaults to `samples/order.png`, so it can also be launched with VS Code's normal **Run Python File** action.

For another image:

```powershell
python main.py "F:\path\to\order.png" --extract-only
```

The supplied sample is expected to contain:

- external reference `WEB-2026-0714-A17`
- order date `2026-07-14`
- Northstar Office GmbH / Marta Klein
- distinct billing and delivery addresses
- Bank Transfer / PAID / 2026-07-18
- `CHR-ERG-01`: 2 x 250.00, 10% discount, 19% VAT, line net 450.00
- `MAT-DESK-02`: 3 x 40.00, 0% discount, 19% VAT, line net 120.00
- net 570.00, VAT 108.30, gross 678.30

## Run the full automation

1. Start Fakturama.
2. Open the disposable/test workspace.
3. Keep one main Fakturama window visible and do not lock or minimize the desktop.
4. Run:

```powershell
python main.py samples\order.png
```

Optional evidence directory:

```powershell
python main.py samples\order.png --evidence-dir artifacts\demo-001
```

For calibration/debugging, save the accessibility tree:

```powershell
python main.py samples\order.png --dump-uia --evidence-dir artifacts\demo-001
```

## Runtime grounding used by the current Windows calibration

The Fakturama UI is Eclipse/SWT based, so several controls do not behave like ordinary native Win32 controls. The current implementation therefore uses UIA first, then runtime geometry and OCR only where needed.

### UIA names vs pywinauto diagnostic aliases

Names such as `DateComboBox`, `ComboBox0`, and `ComboBox1` printed by `print_control_identifiers()` can be pywinauto-generated best-match aliases rather than real UIA `Name` or `AutomationId` values. They are not used as persistent selectors.

For example, the Order Net/Gross ComboBox is grounded from its live position on the same row as **Date**, while the VAT mode ComboBox is selected by its real accessible title **VAT**.

### Icon-only address actions

Beside **Addresses**, Fakturama exposes the two actions as UIA `Image` controls rather than reliably named Buttons:

- upper image: select an existing address/debtor
- lower green `+`: create/add an address

The selector is chosen relative to the live **Addresses** label rather than from a stored coordinate.

### `Select the address` child dialog

`Select the address` is exposed as a UIA **Dialog child of the main Fakturama window**, not necessarily as a separate desktop top-level window. The automation scopes a dedicated `Grounder` to that child dialog before searching or OCRing it. This prevents OCR from reading unrelated Fakturama toolbar, sidebar, Order, or Debtor text.

The address table has its own OCR path because its SWT grid text is very small and is not consistently exposed as UIA DataItems. The table region is determined from live controls (Search near the top and OK near the bottom), enlarged before OCR, and converted back to dialog-relative click coordinates. No fixed screen coordinates are stored.

Because the Company cell can be visibly truncated (for example `Northstar Offic...`), the current calibrated address selection uses a tolerant match on:

- First Name
- Last Name
- the first meaningful Company word

After the Search filter is applied, if several OCR rows satisfy that tolerant condition, the current implementation selects the first matching row. This is intentionally documented because it is less strict than the assessment's ideal exact Company/First Name/Name/ZIP/City identity check. For a production/accounting deployment, ambiguous matches should instead stop for manual review or be verified from full cell values.

### Generic data tables: payment and VAT

Payment-method and VAT Data views use the generic OCR row finder. The Search/Filter text itself is explicitly excluded from matching, and only OCR rows below the Search control are treated as table candidates. This prevents a query such as `Bank Transfer` from being mistaken for an existing table row when the table is actually empty.

### SWT text entry

Some Fakturama fields are exposed as UIA `Document` controls. Direct UIA value assignment can make text appear visually without firing SWT modify/focus listeners, so Fakturama may not mark the editor dirty and the value can disappear.

The Company field therefore uses real keyboard interaction:

```text
click -> Ctrl+A -> Backspace -> type -> Tab
```

The final `Tab` triggers normal focus-out/commit behavior.

### Combined row labels

Some fields share one accessible label while exposing multiple Edit controls on the same row:

- `First Name Last Name` -> first Edit = First Name, second Edit = Last Name
- `ZIP - City` -> first Edit = ZIP, second Edit = City

These are grounded by live row geometry and left-to-right index, not by absolute coordinates.

`Country` is handled as a ComboBox rather than as a text Edit.

## Main workflow

1. Extract and validate the source image.
2. Open a new Order first and leave it open.
3. Set Order reference/date/mode fields using semantic or relative grounding.
4. Open the Order's address selector and search for the Debtor.
5. Reuse the existing Debtor or create the missing Debtor while the Order remains open.
6. Resolve the requested payment method. Missing definitions are created only for the assessment mappings:
   - Bank Transfer -> Credit transfer
   - Credit Card -> Credit card
   - SEPA Direct Debit -> SEPA direct debit
7. For each source item, reuse or create the VAT/Product master data, then add the item to the open Order.
8. Verify addresses, item values and totals; save the Order.
9. Verify the saved Order under Documents > Orders.
10. Create the Invoice from **Create a follow-up document** on the saved Order, not from the global Invoice toolbar action.
11. Apply payment state/date/value as required and save.
12. Verify the Invoice under Documents > Invoices and re-open it when persistence verification is required.

## Evidence

A run can produce evidence such as:

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

The extraction test uses the actual sample image rather than only a fabricated OCR string.

## Current development status / known calibration points

The project is being calibrated against the real Windows/Fakturama accessibility tree rather than relying on screenshots alone. The following areas have required build-specific handling:

- unlabeled Order Net/Gross ComboBox
- Image rather than Button exposure for address icons
- `Document` rather than `Edit` exposure for Company
- combined First/Last Name and ZIP/City rows
- child-dialog scoping for `Select the address`
- tiny SWT table OCR and truncated Company display
- Search text being visible to OCR even when a result table is empty

After each grounding change, the full workflow should be rerun from a clean disposable workspace because master-data creation and document persistence are stateful.

## Exit codes

- `0` - completed, or extraction-only completed
- `2` - manual review required
- `1` - unexpected failure

See [DESIGN.md](DESIGN.md) for the architecture and grounding rationale.
