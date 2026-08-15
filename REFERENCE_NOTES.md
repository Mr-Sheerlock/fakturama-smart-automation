# Assessment PDF Visual Review Notes

The implementation was revised after inspecting the embedded screenshots, not only the PDF's extracted text.

- **Page 3 - source image:** the input is a card/column document rather than `Label: value` text. It contains a distinct billing address and delivery address, two item rows, and net/VAT/gross totals. This drove the token-geometry parser and second-address support.
- **Page 4 / Figure 1:** `Select the address` is a modal grid with a Search field at the upper-right and OK/Cancel. The existing-contact action is the upper icon beside Addresses, not the green +.
- **Pages 5-6 / Figures 2 and 4:** the Debtor editor has address-role controls plus Miscellaneous and Payment sections. The payment definition includes Name, Description, payment code, cash discount/days, net days and a Set as standard action that must remain untouched.
- **Page 7 / Figures 5 and 6:** `Select a product` is another searchable modal grid. VAT master data is edited in the Data > VATs view and uses `VAT code (E-Invoice)` plus Value.
- **Page 8 / Figure 7:** Product field names include `Item Number`, `Price (gross)`, `cost price (net)`, `VAT`, and `Stock`; optional fields stay unchanged.
- **Page 9 / Figure 8:** the saved Order contains a Documents panel with an Orders category and a separate `Create a follow-up document` area containing the linked Invoice action. This is why Invoice creation is anchor-scoped instead of clicking a global `Invoice` label.
- **Page 10 / Figures 9-10:** the Invoice payment strip visibly uses `paid`, a payment-method combo, `Pay Date`, and `Value`. The final Documents view separates Invoices and Orders; verification therefore selects each category before checking the row.

These observations are reflected directly in `extractor.py`, `ui.py`, and `fakturama.py`.
