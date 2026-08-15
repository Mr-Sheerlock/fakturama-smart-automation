# Written Questions

## 1. If you had 3 more hours, what would you do for this task?

I would spend the additional time on empirical hardening against the exact Windows/Fakturama build rather than adding more abstract code. First I would run several end-to-end fixtures covering an existing Debtor/Product, missing Debtor with distinct delivery address, missing VAT/Product, each supported payment method, PAID and unpaid invoices, and ambiguous duplicates. I would capture the UIA tree and screenshots for every transition and tighten selectors where SWT exposes weak accessibility metadata.

Second, I would add a structured-LLM extraction fallback behind the deterministic OCR parser. OCR token geometry would remain the source of evidence, while the LLM would only map ambiguous layouts into the typed schema; all monetary relationships and required fields would still be deterministically validated before UI execution.

Finally, I would add failure-injection/restart tests: interrupted saves, dialogs opening slowly, duplicate selector rows, OCR mistakes and unexpected Fakturama state. The runner would persist its current state and document numbers so a failed run can be inspected or safely resumed without creating duplicate accounting records.
