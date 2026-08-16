# Fakturama Image-to-Cash Automation - Design

## 1. Goal

The system receives one purchase-order image and drives Fakturama to a saved Order and a linked Invoice while collecting evidence and verifying persisted state.

The core rule is **ground -> act -> verify**. The automation does not store fixed screen coordinates and does not assume that an SWT control remains at a particular pixel location. Where UIA metadata is incomplete, clicks are derived from the current control tree, current rectangles, or OCR boxes from the current window/dialog.

The intended state flow is:

```text
Extract
  -> New Order
  -> Debtor / address selection or creation
  -> payment definition resolution
  -> VAT / Product resolution
  -> Order verification + Save
  -> linked Invoice
  -> payment state
  -> final persistence verification
```

The original Order editor remains open while master data is resolved.

## 2. Source-image extraction

Tesseract is run on several source-image variants such as RGB, grayscale and enhanced contrast. The strongest pass is selected using required-anchor coverage and OCR quality. OCR tokens retain bounding boxes so extraction can use geometry rather than only a flattened text string.

The assessment image is parsed around anchors such as:

- EXTERNAL REFERENCE
- ORDER DATE
- COMPANY
- BILLING ADDRESS
- DELIVERY ADDRESS
- PAYMENT METHOD
- SKU
- total labels

Item rows are reconstructed from token order/geometry and mapped into validated models. Monetary values use Decimal-based calculations. Source line totals, total net, VAT and gross total are checked before Fakturama is modified.

## 3. UI architecture

### 3.1 Main session

`FakturamaSession` connects to exactly one visible main Fakturama window through `pywinauto` with the UIA backend. The main `Grounder` represents this window and is used for normal editor/navigation operations.

### 3.2 Runtime semantic grounding

The primary locator inputs are live UIA properties:

- Name
- AutomationId
- ControlType
- visibility/enabled state
- parent/child relationships
- current rectangles

A diagnostic string printed by `print_control_identifiers()` is not automatically treated as a real UIA identifier. For example, pywinauto may print `DateComboBox` or `ComboBox0` even though the underlying SWT ComboBox has an empty UIA Name. Those generated aliases are unsuitable as stable selectors.

When a semantic label exists but its associated control is unnamed, the control is selected from current relative geometry. The Order Net/Gross ComboBox is an example: it is found on the same row and to the right of **Date**. The VAT mode ComboBox, by contrast, has the accessible title **VAT** and can be selected semantically.

### 3.3 Child-dialog scoping

A major calibration finding is that `Select the address` is exposed as a UIA **Dialog child inside the main Fakturama window**. It should not be approximated by returning the whole main window merely because that window contains the dialog title.

The address-selection path therefore locates the actual child Dialog and creates a dedicated `Grounder` for it. All Search, OCR, row-click and OK operations then use the child-dialog rectangle.

This is important for both accuracy and coordinate conversion:

```text
Desktop
  -> Fakturama main Window
       -> Dialog "Select the address"   <- dedicated Grounder
            -> Search
            -> SWT result table
            -> OK / Cancel
```

OCR boxes are local to the child dialog, and mouse coordinates are obtained by adding the child dialog's current screen origin.

### 3.4 OCR fallback for SWT tables

Fakturama's SWT tables may be painted rather than exposed as useful UIA DataItems. Tesseract is therefore used as a fallback.

There are two deliberately different table strategies.

#### Generic Data views

Payment-method and VAT views use the generic OCR row finder. These screens place Search above the result table. The algorithm:

1. OCR the current Fakturama/Data view.
2. identify visible Search/Filter OCR lines;
3. never allow the Search query itself to count as a record;
4. accept candidate rows only below the Search area;
5. match the required payment/VAT text;
6. click the OCR row using runtime window-relative coordinates.

This fixes the observed case where the Search box visibly contained `Bank Transfer` while the result table was empty: the query must not be interpreted as an existing payment record.

#### Address selector

The address selector is treated separately because:

- it is a child Dialog;
- Search is positioned inside the dialog header area;
- table text is very small;
- Company can be visually truncated;
- ordinary `ocr_lines()` at native resolution can miss the result row.

The address-specific OCR path:

1. captures only the actual child Dialog;
2. finds the live Search and OK controls;
3. defines the table OCR region dynamically between those controls rather than assuming a fixed percentage of the dialog is empty;
4. enlarges the table image before OCR;
5. groups OCR words into visual rows;
6. converts row centers back to dialog-relative coordinates before clicking.

The current Windows calibration uses a tolerant debtor row match based on First Name, Last Name, and the first meaningful Company word because the SWT grid can display `Northstar Office GmbH` as a truncated string such as `Northstar Offic...`. After Search has narrowed the table, the first matching OCR row is selected if multiple tolerant matches remain.

This behavior is intentionally isolated to the address selector. It is a pragmatic calibration tradeoff and is weaker than the assessment's ideal exact Company + First Name + Name + ZIP + City identity rule. A production/accounting version should restore strict ambiguity handling by reading complete cell values, using accessible DataItems if available, or requesting manual review when more than one full identity is possible.

## 4. SWT-specific control behavior

### 4.1 Text controls that require real keyboard input

The Company field was observed through Accessibility Insights as a focusable UIA `Document` with a writable ValuePattern. Direct UIA value assignment can still fail semantically: the text may appear but Fakturama may not fire SWT modify/focus listeners, may not mark the editor dirty, and may later discard the value.

For this control the safe interaction is real keyboard input:

```text
click field
Ctrl+A
Backspace
type text
Tab
```

`Tab` deliberately causes normal focus-out/commit behavior.

This special handling is kept narrow rather than replacing every ordinary Edit operation.

### 4.2 Multiple controls on one labeled row

Accessibility can expose a combined label with multiple Editors:

```text
First Name Last Name    [Edit 0] [Edit 1]
ZIP - City              [Edit 0] [Edit 1]
```

These values are located by:

1. finding the visible combined label;
2. collecting enabled Edit controls on the same visual row and to the right;
3. sorting by x-position;
4. selecting by index.

This remains layout-independent because the index is based on live row geometry, not stored pixel coordinates.

### 4.3 ComboBoxes

Country is a ComboBox, not a text field. Likewise, the Order Net/Gross selector is an unnamed ComboBox found relative to Date.

The design preference is:

1. identify the ComboBox itself through UIA/relative geometry;
2. use native ComboBox selection when available;
3. use keyboard/OCR only as a fallback when SWT does not expose dropdown items;
4. verify the final displayed value.

## 5. Icon-only controls

The assessment screenshots and the real UIA tree distinguish two small controls beside **Addresses**:

- the upper control selects an existing address/debtor;
- the lower green `+` adds a new address.

On the tested build they can be exposed as `Image` rather than `Button`. The automation therefore treats the control type as an implementation detail and grounds the intended action relative to the **Addresses** semantic anchor.

The same principle applies to other icon-only actions: discover from the live UI tree or current image geometry, then rank relative to a semantic anchor. No absolute click location is persisted.

## 6. Master-data and document workflow

### 6.1 Order first

A new Order is opened first and kept open. The generated document number is not replaced. Source reference/date and Order modes are set using the calibrated control-grounding methods.

### 6.2 Debtor

The Order's own address selector is used to search for an existing Debtor/address. If none is selectable, the Debtor creation editor is opened while preserving the Order tab.

Debtor creation has required special handling for:

- Company as an SWT `Document` requiring keyboard commit;
- First Name / Last Name on one combined row;
- ZIP / City on one combined row;
- Country as a ComboBox;
- address-role selection through the address-type selector;
- separate delivery address creation when billing and delivery differ.

After creation, the Order's address selector is reopened and the new Debtor must be selectable before continuing.

### 6.3 Payment method

The assessment mappings are restricted to:

```text
Bank Transfer       -> Credit transfer
Credit Card         -> Credit card
SEPA Direct Debit   -> SEPA direct debit
```

The Data > terms of payment view is searched first. Generic OCR matching excludes the Search field itself. If no existing definition is found, a new payment definition is created with the mapped payment code and required zero-day/zero-discount values.

### 6.4 VAT and Product

For each item, the Order's Product selector is searched by SKU. Missing Products trigger VAT resolution first. An existing VAT is reused only when its definition agrees with the requested VAT identity/value/code. New Product gross master price is derived from unit net price and VAT; the transaction-line discount is not baked into Product master price.

After creation, the Product must be reselectable from the still-open Order.

### 6.5 Order verification and save

Before saving, the workflow verifies the selected addresses, source item rows, shipping/discount behavior and totals. The saved Order is then checked under Documents > Orders.

### 6.6 Linked Invoice

The Invoice is created from the saved Order's **Create a follow-up document** area. The global toolbar Invoice action is intentionally not used because it could create an unrelated document.

Copied reference, addresses, Order Date, items and totals are checked. For PAID input, the Invoice's paid state, payment date and full value are set. The saved Invoice is verified under Documents > Invoices while the source Order remains present under Documents > Orders. Reopening the Invoice can be used to prove that payment fields persisted.

## 7. Failure policy and calibration tradeoffs

The architecture prefers runtime grounding and explicit verification over brittle coordinate macros. In most master-data cases, ambiguity should cause `ManualReviewRequired` rather than a guess.

The current address-selector calibration is the explicit exception: after Search filtering, OCR uses a deliberately looser identity and selects the first tolerant match because the visible Company cell can be truncated and full ZIP/City values may not be OCR-readable. This behavior is isolated and documented so it can later be replaced with a stricter cell-level identity check without changing the rest of the workflow.

Other failures that should stop automation include:

- inability to find the required child dialog/control;
- duplicate/ambiguous SKU selection;
- conflicting VAT/payment definitions;
- financial totals that disagree with the source;
- a newly created master record that cannot be reselected;
- inability to verify a persisted Order/Invoice state.

## 8. Evidence and debugging

Evidence is part of the design, not only a debugging convenience. A run may persist:

- preprocessed source image
- OCR text/strategy
- structured extracted JSON
- UIA tree dump
- workflow screenshots/checkpoints
- final run status

Accessibility Insights and `print_control_identifiers()` are used during calibration to learn the real UIA ControlType/Name/AutomationId relationships. Generated pywinauto best-match aliases are treated as diagnostics, not as authoritative automation IDs.
