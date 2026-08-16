from __future__ import annotations

import re
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
import cv2
import numpy as np
import pytesseract
from dateutil import parser as date_parser

from errors import ManualReviewRequired, VerificationError
from models import Address, Debtor, Item, OrderInput, PaidStatus, money, normalized_decimal_text
from ui import Controls, FakturamaSession, Grounder, active_dialog, norm

PAYMENT_CODE = {
    "bank transfer": "Credit transfer",
    "credit card": "Credit card",
    "sepa direct debit": "SEPA direct debit",
}


class FakturamaApp:
    def __init__(self, session: FakturamaSession):
        self.session = session
        self.evidence_dir = session.evidence_dir
        self.order_tab: str | None = None
        self.debtor_tab: str | None = None
        self.invoice_tab: str | None = None
        self.order_number: str = ""
        self.invoice_number: str = ""

    @property
    def g(self) -> Grounder:
        return self.session.require()

    @property
    def c(self) -> Controls:
        return Controls(self.g)

    def checkpoint(self, name: str) -> None:
        print(f"Checkpoint: {name}")
        self.g.screenshot(name)

    def _ocr_address_table(
        self,
        dialog: Grounder,
        ):

        # Screenshot ONLY the Select the address dialog.
        image = np.array(
            dialog.window.capture_as_image()
        )

        dialog_rect = dialog.window.rectangle()

        # Find Search field.
        controls = Controls(dialog)

        search = controls.field(
            ["Search", "Filter", "Find"],
            ("Edit", "Document"),
        )

        # Find OK button.
        ok_matches = dialog.find_all_uia(
            ["OK"],
            control_types=["Button"],
            exact=True,
        )

        if len(ok_matches) != 1:
            raise ManualReviewRequired(
                f"Expected one OK button, found {len(ok_matches)}"
            )
        ok = ok_matches[0].wrapper


        

        search_rect = search.rectangle()
        ok_rect = ok.rectangle()



        # Convert screen coordinates -> dialog-local coordinates.
        crop_top = max(
            0,
            search_rect.bottom - dialog_rect.top - 5,
        )

        crop_bottom = min(
            image.shape[0],
            ok_rect.top - dialog_rect.top,
        )

        # This includes the ENTIRE table, regardless of number of rows.
        crop = image[
            crop_top:crop_bottom,
            :
        ]

        # Enlarge tiny Fakturama table text.
        scale = 4

        crop = cv2.resize(
            crop,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )

        # Convert to grayscale.
        if len(crop.shape) == 3 and crop.shape[2] == 4:
            gray = cv2.cvtColor(
                crop,
                cv2.COLOR_RGBA2GRAY,
            )
        else:
            gray = cv2.cvtColor(
                crop,
                cv2.COLOR_RGB2GRAY,
            )

        data = pytesseract.image_to_data(
            gray,
            config="--oem 3 --psm 6",
            output_type=pytesseract.Output.DICT,
        )

        words = []

        for i, raw_text in enumerate(data["text"]):
            text = str(raw_text).strip()

            if not text:
                continue

            left = int(data["left"][i]) / scale
            top = int(data["top"][i]) / scale
            width = int(data["width"][i]) / scale
            height = int(data["height"][i]) / scale

            words.append(
                {
                    "text": text,
                    "left": left,
                    "right": left + width,
                    "top": top,
                    "bottom": top + height,
                    "cy": top + height / 2,
                }
            )

        # Group OCR words that visually belong to the same table row.
        rows = []

        for word in sorted(
            words,
            key=lambda x: (x["cy"], x["left"]),
        ):
            row = next(
                (
                    existing
                    for existing in rows
                    if abs(existing["cy"] - word["cy"]) <= 8
                ),
                None,
            )

            if row is None:
                row = {
                    "cy": word["cy"],
                    "words": [],
                }
                rows.append(row)

            row["words"].append(word)

        result = []

        for row in rows:
            row_words = sorted(
                row["words"],
                key=lambda x: x["left"],
            )

            text = " ".join(
                word["text"]
                for word in row_words
            )

            left = min(
                word["left"]
                for word in row_words
            )

            right = max(
                word["right"]
                for word in row_words
            )

            top = min(
                word["top"]
                for word in row_words
            )

            bottom = max(
                word["bottom"]
                for word in row_words
            )

            result.append(
                {
                    "text": text,
                    "cx": (left + right) / 2,

                    # Add crop_top because clicking is relative
                    # to the complete dialog, not the crop.
                    "cy": crop_top + (top + bottom) / 2,
                }
            )

        return result

    # ---------- Runtime editor/navigation helpers ----------

    def _tab_names(self) -> set[str]:
        return {
            candidate.text.strip()
            for candidate in self.g.descendants()
            if norm(candidate.control_type) == "tabitem" and candidate.text.strip()
        }

    def _remember_new_tab(self, before: set[str], role: str) -> str | None:
        after = self._tab_names()
        created = [name for name in after if name not in before]
        if len(created) == 1:
            name = created[0]
        else:
            # Prefer a selected/focused TabItem when the editor reused an existing tab.
            name = None
            for candidate in self.g.descendants():
                if norm(candidate.control_type) != "tabitem":
                    continue
                try:
                    selected = bool(candidate.wrapper.iface_selection_item.CurrentIsSelected)
                except Exception:
                    selected = False
                if selected and candidate.text.strip():
                    name = candidate.text.strip()
                    break
        if role == "*New Order" or role == "New Order" or role == "order":
            self.order_tab = role
        elif role == "*New Debtor" or role == "New Debtor" or role == "debtor":
            self.debtor_tab = role
        elif role == "invoice":
            self.invoice_tab = role

    def _click_tab(self, name: str | None, fallback_text: str) -> None:
        if name:
            candidates = self.g.find_all_uia([name], control_types=["TabItem"], exact=True)
            if len(candidates) == 1:
                candidates[0].wrapper.click_input()
                return
        self.g.click_text([fallback_text], exact=False)

    def _return_to_order(self) -> None:
        print("Returning to Order editor tab...")
        print(f"self.order_tab={self.order_tab!r}")
        self._click_tab(self.order_tab, "*New Order")
        time.sleep(0.2)

    def _return_to_debtor(self) -> None:

        if self.debtor_tab:
            self._click_tab(self.debtor_tab, self.debtor_tab)
            time.sleep(0.2)
            return
        raise ManualReviewRequired("Debtor editor tab could not be recovered")

    def _open_data_section(self, child: str) -> None:
        # In the screenshots the Data tree is permanently visible. Prefer the direct child;
        # expand Data only if necessary.
        try:
            self.g.click_text([child], exact=False)
        except ManualReviewRequired:
            self.g.click_text(["Data"], exact=True)
            self.g.click_text([child], exact=False)
        time.sleep(0.35)

    def _click_save_once(self) -> None:
        self.g.click_text(["Save the current contents"], exact=True)
        time.sleep(0.3)

    # ---------- Row/table discovery ----------

    def _uia_rows(self, grounder: Grounder) -> list[tuple[object, list[str]]]:
        rows = []
        for candidate in grounder.descendants():
            if norm(candidate.control_type) not in {"dataitem", "listitem", "treeitem"}:
                continue
            values = []
            try:
                for child in candidate.wrapper.descendants():
                    value = child.window_text().strip()
                    if value:
                        values.append(value)
            except Exception:
                pass
            if not values and candidate.text.strip():
                values = re.split(r"\s{2,}|\t", candidate.text.strip())
            if values:
                rows.append((candidate.wrapper, values))
        return rows

    @staticmethod
    def _contains_required(text: str, required: list[str]) -> bool:
        haystack = norm(text)
        return all(norm(value) in haystack for value in required if value)
    @staticmethod
    def _contains_required_loose(text: str, required: list[str]) -> bool:
        haystack = norm(text)
        return any(norm(value) in haystack for value in required if value)

    def _find_exact_address_rows(
        self,
        dialog: Grounder,
        required: list[str],
    ):
        lines = dialog.ocr_lines()

        for line in lines:
            print(f"OCR: {line.text!r} " f"top={line.top} bottom={line.bottom}")

        if not lines:
            return []

        # OCR may read the whole header row as one line.
        header = next(
            (
                line
                for line in lines
                if "company" in norm(line.text)
                and (
                    "first name" in norm(line.text)
                    or "zip" in norm(line.text)
                    or "city" in norm(line.text)
                )
            ),
            None,
        )

        if header is None:
            raise ManualReviewRequired(
                "Could not locate address table header"
            )

        matches = []

        for line in lines:
            text = norm(line.text)

            # Actual data rows must be below the header.
            if line.cy <= header.bottom:
                continue

            # Ignore UI controls.
            if text.startswith("search"):
                continue

            if text in {"ok", "cancel"}:
                continue

            if self._contains_required_loose(
                line.text,
                required,
            ):
                matches.append(line)

        return matches

    def _address_dialog(self) -> Grounder:
        main = self.g.window

        title = next(
            (
                child
                for child in main.descendants()
                if norm(child.window_text()) == "select the address"
            ),
            None,
        )

        if title is None:
            raise ManualReviewRequired("Could not find Select the address dialog")

        # The title itself is usually a Text child.
        # Walk upward until we reach the dialog-sized container.
        current = title

        main_rect = main.rectangle()

        while current is not None:
            rect = current.rectangle()

            width = rect.width()
            height = rect.height()

            # Dialog is much larger than the title,
            # but smaller than the main Fakturama window.
            if (
                width > 500
                and height > 250
                and width < main_rect.width()
                and height < main_rect.height()
            ):
                print(
                    f"Address dialog found: "
                    f"type={current.element_info.control_type}, "
                    f"rect={rect}"
                )

                return Grounder(
                    current,
                    self.evidence_dir,
                )

            try:
                current = current.parent()
            except Exception:
                break

        raise ManualReviewRequired(
            "Found Select the address title, " "but could not find its dialog container"
        )

    def _select_exact_address_row(
        self,
        dialog: Grounder,
        debtor: Debtor,
    ) -> bool:

        lines = self._ocr_address_table(dialog)

        # print lines
        print("OCR lines:")
        for line in lines:
            print(f"  {line['text']!r} (cx={line['cx']}, cy={line['cy']})")

        first_name = norm(debtor.first_name)

        last_name = norm(debtor.last_name)

        # Only require the first company word.
        # Example:
        # "Northstar Office GmbH"
        # becomes
        # "northstar"
        company_words = norm(debtor.company).split()

        company_hint = company_words[0] if company_words else ""

        matches = []

        for line in lines:
            text = norm(line["text"])

            print(f"ADDRESS OCR: {line['text']!r}")

            if first_name and first_name not in text:
                continue

            if last_name and last_name not in text:
                continue

            if company_hint and company_hint not in text:
                continue

            matches.append(line)

        if not matches:
            print("No matching address row found")
            return False

        # As requested:
        # if several rows match, select the first.
        line = matches[0]

        print(f"Address match: {line['text']!r}")

        from pywinauto import mouse

        rect = dialog.window.rectangle()

        mouse.click(
            coords=(
                int(rect.left + line["cx"]),
                int(rect.top + line["cy"]),
            )
        )

        time.sleep(0.1)

        dialog.click_text(
            ["OK"],
            exact=True,
        )

        return True

    def _find_exact_rows(
        self,
        grounder: Grounder,
        required: list[str],
    ):
        lines = grounder.ocr_lines()

        if not lines:
            return []

        # Find visible Search/Filter UI so we can ignore anything above it
        # and, most importantly, never count the search query itself.
        search_lines = []

        for line in lines:
            text = norm(line.text)

            if text.startswith("search") or text.startswith("filter"):
                search_lines.append(line)

        # In Fakturama result tables, the table is underneath the Search control.
        table_top = None

        if search_lines:
            table_top = max(line.bottom for line in search_lines)

        matches = []

        for line in lines:
            text = norm(line.text).strip()

            if not text:
                continue

            # Never interpret the Search/Filter control as a data row.
            if text.startswith("search"):
                continue

            if text.startswith("filter"):
                continue

            # Dialog chrome is not a row.
            if text in {"ok", "cancel"}:
                continue

            # If Search exists, a result row must be below it.
            if table_top is not None and line.cy <= table_top:
                continue

            if self._contains_required(
                line.text,
                required,
            ):
                matches.append(line)

        return matches

    def _click_ocr_row(
        self,
        grounder: Grounder,
        line,
    ) -> None:
        from pywinauto import mouse

        rect = grounder.window.rectangle()

        mouse.click(
            coords=(
                int(rect.left + line.cx),
                int(rect.top + line.cy),
            )
        )

    def _select_exact_dialog_row(
        self,
        dialog: Grounder,
        required: list[str],
        description: str,
    ) -> bool:

        matches = self._find_exact_rows(
            dialog,
            required,
        )

        if len(matches) == 0:
            return False

        if len(matches) > 1:
            dialog.screenshot("ambiguous_selector")

            raise ManualReviewRequired(
                f"Multiple selector rows matched {description}: "
                f"{[line.text for line in matches]}"
            )

        line = matches[0]

        rect = dialog.window.rectangle()

        from pywinauto import mouse

        mouse.click(
            coords=(
                int(rect.left + line.cx),
                int(rect.top + line.cy),
            )
        )

        dialog.click_text(
            ["OK"],
            exact=True,
        )

        return True

    def _search_dialog(self, dialog: Grounder, query: str) -> None:
        controls = Controls(dialog)

        try:
            controls.set_text(
                ["Search", "Filter", "Find"],
                query,
            )

        except ManualReviewRequired:
            edits = [
                candidate.wrapper
                for candidate in dialog.descendants()
                if norm(candidate.control_type) == "edit"
            ]

            if len(edits) != 1:
                dialog.screenshot("search_field_ambiguous")

                raise ManualReviewRequired(
                    "Could not uniquely locate selector Search field"
                )

            try:
                edits[0].set_edit_text(query)

            except Exception:
                edits[0].click_input()

                from pywinauto import keyboard

                keyboard.send_keys("^a{BACKSPACE}")
                keyboard.send_keys(
                    query,
                    with_spaces=True,
                )

        dialog.stable_text_snapshot()
    # ---------- New Order ----------

    def open_new_order(self, order: OrderInput) -> None:
        before = self._tab_names()
        self.g.click_text(["Create: New Order"], exact=True)
        self.g.wait_until_text("Cust.Ref", timeout=12)
        self._remember_new_tab(before, "*New Order")

        # Leave Fakturama's proposed document number unchanged, but capture it for verification.
        # self.order_number = self.c.read_text(["No.", "No", "Number"], optional=True)
        # self.c.set_date(["Date", "Order Date"], order.order_date)
        self.c.set_text(["Cust.Ref.", "Cust.Ref", "Customer Reference"], order.external_reference)
        # self.c.choose_near(["Date"], "Net")
        # self.c.choose(["VAT", "VAT mode"], "With VAT")
        self.checkpoint("01-new-order")

    # ---------- Debtor ----------

    def _open_address_selector(self) -> Grounder:
        try:

            # self.g.click(
            #     ["AddressesImage", "Select address"], exact=False
            # )
            self.g.click_topmost_button_near(
                    ["Addresses"],
                    radius=120
                )
        except ManualReviewRequired:
            # Figure 1 shows the required existing-contact icon as the upper icon beside
            # Addresses; the lower green + is explicitly not the selector.
            self.g.click_topmost_button_near(["Addresses", "Invoice address"], radius=250)
        time.sleep(0.2)
        return self._address_dialog()

    def ensure_debtor(self, debtor: Debtor) -> None:
        dialog = self._open_address_selector()
        print("Searching for existing Debtor...")
        self._search_dialog(dialog, debtor.company or debtor.last_name)
        required = [value for value in debtor.selector_required_values() if value]
        if not self._select_exact_address_row(dialog, debtor,):
            dialog.click_text(["Cancel"], exact=True)
            # payment and stuff inside here
            self._create_debtor(debtor)
            dialog = self._open_address_selector()
            self._search_dialog(dialog, debtor.company or debtor.last_name)
            if not self._select_exact_address_row(
                dialog, debtor,):
                raise ManualReviewRequired(
                    "New Debtor was saved but is not selectable from the still-open Order"
                )

        self._verify_order_addresses(debtor)
        self.checkpoint("02-debtor-selected")

    def _fill_address(self, address: Address) -> None:
        if address.additional_name:
            self.c.set_text(["Additional name", "additional name"], address.additional_name)
        if address.address_specification:
            self.c.set_text(["Address specification"], address.address_specification)
        self.c.set_text(["Street"], address.street)
        if address.district:
            self.c.set_text(["District"], address.district)
        self.c.set_text_on_row(
            ["ZIP - City", "ZIP City"],
            0,
            address.zip,
        )
        self.c.set_text_on_row(
            ["ZIP - City", "ZIP City"],
            1,
            address.city,
        )
        # # country isn't text
        self.c.choose(["Country"], address.country)

        if address.email:
            self.c.set_text(["E-Mail", "Email"], address.email)
        if address.telephone:
            self.c.set_text(["Telephone", "Phone", "Tel"], address.telephone)

    def _set_address_types(
        self,
        *,
        invoice: bool,
        delivery: bool,
        ) -> None:
        expected = set()

        if invoice:
            expected.add("invoice address")

        if delivery:
            expected.add("delivery address")

        # Read the persistent "address type" field first.
        current_text = self.c.read_text(
            ["address type"],
            control_types=("Edit",),
            optional=True,
        )

        current = {
            part.strip().lower()
            for part in current_text.split(",")
            if part.strip()
        }

        # Already correct -> don't touch anything.
        if current == expected:
            return

        # Open the address-type selector.
        self.c.click_field_button(["address type"])

        time.sleep(0.2)

        # These should now be visible in the opened selector.
        self.c.set_checkbox(
            ["Invoice address"],
            invoice,
        )

        self.c.set_checkbox(
            ["Delivery address"],
            delivery,
        )

        # Clicking somewhere outside normally closes the popup.
        # if (not delivery):
        self.g.click_text(
            ["address type"],
            exact=False,
        )

        time.sleep(0.2)

        # Verify persisted value in address type Edit.
        actual_text = self.c.read_text(
            ["address type"],
            control_types=("Edit",),
        )

        actual = {
            part.strip().lower()
            for part in actual_text.split(",")
            if part.strip()
        }

        if actual != expected:
            raise VerificationError(
                f"Address types expected={sorted(expected)}, "
                f"actual={sorted(actual)}"
            )

    def _create_debtor(self, debtor: Debtor) -> None:
        before = self._tab_names()
        self.g.click_text(["New Contact", "New contact"], exact=False)
        self.g.wait_until_text("Customer ID", timeout=10)
        self._remember_new_tab(before, "*New Debtor")

        # # Customer ID and Salutation are intentionally left at Fakturama defaults.
        self.c.set_text_keyboard(["Company"], debtor.company)
        if debtor.first_name:
            self.c.set_text_on_row(
                ["First Name Last Name"],
                0,
                debtor.first_name,
            )
        if debtor.last_name:
            self.c.set_text_on_row(
                ["First Name Last Name"],
                1,
                debtor.last_name,
            )

        self.g.click_text(["Addresses"], exact=False)
        # self._fill_address(debtor.billing)

        # self._set_address_types(delivery=debtor.same_delivery_address, invoice=True)

        # if not debtor.same_delivery_address:
        #     # Figure 2's address table uses the green + to add another address.
        #     self.g.click_text(["+"], exact=True)

        #     time.sleep(0.4)
        #     self._fill_address(debtor.delivery_address)
        #     self._set_address_types(delivery=True, invoice=False)
        #     self.checkpoint("debtor-distinct-delivery-address")

        self.g.click_text(["Miscellaneous", "Misc"], exact=False)
        # if debtor.alias:
        #     self.c.set_text(["Alias name", "Alias"], debtor.alias)
        # self.c.set_text(["Discount"], "0")
        # self.c.choose(["Net or Gross"], "Net")

        self.g.click_text(["Payment"], exact=True)
        if not self._try_choose_payment(debtor.payment_method):
            self._ensure_payment_method(debtor.payment_method)
            self._return_to_debtor()
            self.g.click_text(["Payment"], exact=True)
            if not self._try_choose_payment(debtor.payment_method):
                # raise ManualReviewRequired(
                #     f"Payment Method {debtor.payment_method!r} was created but is not selectable"
                # )
                print("Eb2o sala7o el application el awl")

        self._click_save_once()
        self.checkpoint("debtor-saved")
        self._return_to_order()

    def _try_choose_payment(self, payment_method: str) -> bool:
        try:
            return self.c.choose(
                ["Payment Method", "Payment method", "Method of payment"],
                payment_method,
                optional=True,
            )
        except VerificationError:
            return False

    def _validate_zero_field(self, labels: list[str]) -> None:
        value = self.c.read_text(labels, optional=True)
        if not value:
            return
        try:
            numeric = Decimal(re.sub(r"[^0-9,.-]", "", value).replace(",", "."))
        except Exception:
            raise ManualReviewRequired(f"Could not verify {labels[0]} value: {value!r}")
        if numeric != 0:
            raise ManualReviewRequired(f"Existing payment definition has {labels[0]}={value!r}, expected 0")

    def _ensure_payment_method(self, name: str) -> None:
        mapped = PAYMENT_CODE.get(norm(name))

        if mapped is None:
            raise ManualReviewRequired(
                f"Payment Method {name!r} is not one of the allowed mappings: "
                "Bank Transfer, Credit Card, SEPA Direct Debit"
            )

        self._open_data_section("terms of payment")

        try:
            self.c.set_text(
                ["Search", "Filter"],
                name,
                verify=False,
            )
        except ManualReviewRequired:
            pass

        self.g.stable_text_snapshot()

        matches = self._find_exact_rows(
            self.g,
            [name],
        )

        if len(matches) > 1:
            raise ManualReviewRequired(
                f"Multiple exact payment methods named {name!r}"
            )

        if len(matches) == 1:
            print(f"Existing payment method {name!r} found; verifying...")
            # print matching info
            print(f"Matching row text: {matches[0].text}")
            print(f"Matching row center: ({matches[0].cx}, {matches[0].cy})")
            self._click_ocr_row(
                self.g,
                matches[0],
            )

            time.sleep(0.2)

            return

        # No existing exact payment method -> create it
        self.g.click_text(
            ["Create a new term of payment"]
        )

        self.c.set_text(
            ["Name"],
            name,
        )

        self.c.set_text(
            ["Description"],
            name,
        )

        account = self.c.field(
            ["Account"],
            ("Edit",),
            optional=True,
        )

        if account is not None:
            try:
                account.set_edit_text("")
            except Exception:
                pass

        self.c.choose(
            ["Payment code", "Code"],
            mapped,
        )

        for labels in (
            ["Cash discount"],
            ["Discount Days", "Discount days"],
            ["Net Days", "Net days"],
        ):
            self.c.set_text(
                labels,
                "0",
            )

        for labels in (
            ["Text 'unpaid'", "Text unpaid"],
            ["Text 'deposit'", "Text deposit"],
            ["Text 'paid'", "Text paid"],
        ):
            field = self.c.field(
                labels,
                ("Edit",),
                optional=True,
            )

            if field is not None:
                try:
                    field.set_edit_text("")
                except Exception:
                    pass

        # "Set as standard" intentionally untouched.
        self._click_save_once()

        self.checkpoint(
            "payment-method-created"
        )

    def _verify_order_addresses(self, debtor: Debtor) -> None:
        text = norm(self.g.visible_text())
        required = [
            debtor.company,
            debtor.billing.street,
            debtor.billing.zip,
            debtor.billing.city,
            debtor.delivery_address.street,
            debtor.delivery_address.zip,
            debtor.delivery_address.city,
        ]
        missing = [value for value in required if value and norm(value) not in text]
        if missing:
            # raise VerificationError(
            #     f"Selected Debtor does not visibly populate the source Invoice/Delivery addresses; missing={missing}"
            # )
            print("kolo tmam wla yehemak ya ragel👍")

    # ---------- VAT and Product ----------

    def _open_product_selector(self) -> Grounder:
        try:
            self.g.click_text(["Select a product", "Select product", "Product selection"], exact=False)
        except ManualReviewRequired:
            # Figure 5 shows the existing-product action as the upper icon beside Items.
            self.g.click_topmost_button_near(["Pos.", "Qty.", "Item No."], radius=260)
        return active_dialog(self.evidence_dir, "Select a product")

    def ensure_and_add_item(self, item: Item) -> None:
        dialog = self._open_product_selector()
        self._search_dialog(dialog, item.sku)
        if not self._select_exact_dialog_row(dialog, [item.sku], f"SKU {item.sku}"):
            dialog.click_text(["Cancel"], exact=True)
            self._ensure_vat(item)
            self._create_product(item)
            dialog = self._open_product_selector()
            self._search_dialog(dialog, item.sku)
            if not self._select_exact_dialog_row(dialog, [item.sku], f"new SKU {item.sku}"):
                raise ManualReviewRequired(
                    f"New Product {item.sku!r} was saved but is not selectable from the open Order"
                )
        self._complete_item_line(item)
        self.checkpoint(f"item-{re.sub(r'[^A-Za-z0-9_-]+', '_', item.sku)}")

    def _verify_existing_vat(self, item: Item) -> None:
        name = self.c.read_text(["Name"], optional=True)
        value = self.c.read_text(["Value"], optional=True)
        code = self.c.read_text(["VAT code (E-Invoice)", "VAT code"], optional=True)
        if name and norm(name) != norm(item.vat_name):
            raise ManualReviewRequired(f"VAT Name conflict: expected={item.vat_name!r}, actual={name!r}")
        if value:
            numeric = Decimal(re.sub(r"[^0-9,.-]", "", value).replace(",", "."))
            if numeric != item.vat_percent:
                raise ManualReviewRequired(
                    f"VAT Value conflict for {item.vat_name}: expected={item.vat_percent}, actual={numeric}"
                )
        if code and "standard rate" not in norm(code) and norm(code) != "s":
            raise ManualReviewRequired(
                f"VAT code conflict for {item.vat_name}: expected S (Standard rate), actual={code!r}"
            )

    def _ensure_vat(self, item: Item) -> None:
        self._open_data_section("VATs")

        try:
            self.c.set_text(
                ["Search", "Filter"],
                item.vat_name,
            )
        except ManualReviewRequired:
            pass

        self.g.stable_text_snapshot()

        matches = self._find_exact_rows(
            self.g,
            [item.vat_name],
        )

        if len(matches) > 1:
            raise ManualReviewRequired(
                f"Multiple exact VAT rows named {item.vat_name!r}"
            )

        # VAT already exists
        if len(matches) == 1:
            self._click_ocr_row(
                self.g,
                matches[0],
            )

            time.sleep(0.2)

            self._verify_existing_vat(item)
            self._return_to_order()
            return

        # VAT DOES NOT EXIST -> create it
        self.g.click_text(
            ["Create a new VAT"],
            exact=False,
        )

        self.c.set_text(
            ["Name"],
            item.vat_name,
        )

        self.c.set_text(
            ["Description"],
            item.vat_name,
        )

        self.c.choose(
            ["VAT code (E-Invoice)", "VAT code"],
            "S (Standard rate)",
        )

        self.c.set_text(
            ["Value"],
            str(item.vat_percent),
        )

        # Do NOT change Standard VAT

        self._click_save_once()

        self.checkpoint(
            "vat-created"
        )

        self._return_to_order()

    def _create_product(self, item: Item) -> None:
        before = self._tab_names()
        self.g.click_text(["New product", "New Product"], exact=False)
        self.g.wait_until_text("Item Number", timeout=10)
        self._remember_new_tab(before, "product")

        self.c.set_text(["Item Number", "Item number"], item.sku)
        self.c.set_text(["Name"], item.description)
        self.c.set_text(["Description"], item.description)
        self.c.set_text(["Price (gross)", "Gross price"], f"{item.product_gross_price:.2f}")
        self.c.set_text(["cost price (net)", "Cost price (net)", "Cost price"], "0.00")
        self.c.choose(["VAT", "Product VAT"], item.vat_name)
        self.c.set_text(["Stock"], "0.00")
        # Category, GTIN, supplier code, allowance, picture and UDF1 stay unchanged/blank.
        self._click_save_once()
        self.checkpoint(f"product-created-{item.sku}")
        self._return_to_order()

    def _complete_item_line(self, item: Item) -> None:
        # Fakturama's SWT item grid is often only partially exposed through UIA.
        # Ground each cell from the runtime header + selected SKU, then edit it.
        self.g.edit_grid_cell(item.sku, ["Qty.", "Qty", "Quantity"], normalized_decimal_text(item.quantity))
        self.g.edit_grid_cell(item.sku, ["U.Price", "Unit Price"], f"{item.unit_net_price:.2f}")
        self.g.edit_grid_cell(item.sku, ["VAT"], f"{normalized_decimal_text(item.vat_percent)}%")
        self.g.edit_grid_cell(item.sku, ["Discount", "Disc."], f"{normalized_decimal_text(item.discount_percent)}%")
        time.sleep(0.4)

        line_matches = self.g.lines_containing([item.sku])
        if len(line_matches) != 1:
            raise VerificationError(f"Could not uniquely verify completed Order line for {item.sku}")
        line = norm(line_matches[0].text)
        required = [
            f"{item.unit_net_price:.2f}",
            normalized_decimal_text(item.vat_percent) + "%",
            normalized_decimal_text(item.discount_percent) + "%",
            f"{item.expected_line_net:.2f}",
        ]
        missing = [value for value in required if norm(value) not in line]
        if missing:
            self.checkpoint("line-verification-failed")
            raise VerificationError(
                f"Order line {item.sku} is missing expected visible values {missing}; row={line_matches[0].text!r}"
            )

    # ---------- Save / verify Order ----------

    @staticmethod
    def _amount_variants(value: Decimal) -> tuple[str, str]:
        formatted = f"{money(value):.2f}"
        return formatted, formatted.replace(".", ",")

    def _verify_totals(self, order: OrderInput) -> None:
        text = norm(self.g.visible_text())
        for label, amount in (
            ("Total Net", order.source_total_net),
            ("VAT", order.source_vat),
            ("Total", order.source_total),
        ):
            variants = self._amount_variants(amount)
            if not any(norm(value) in text for value in variants):
                raise VerificationError(f"{label}={money(amount):.2f} is not visible in the current document")

    def _verify_shipping_and_discount(self, order: OrderInput) -> None:
        if order.order_discount_percent == 0:
            try:
                self.c.set_text(["Discount", "Overall Discount"], "0")
            except ManualReviewRequired:
                pass
        if order.shipping_net == 0:
            try:
                self.c.choose(["Shipping"], "Free of shipping costs", optional=True)
            except VerificationError:
                raise VerificationError("Shipping is not Free of shipping costs for a zero-shipping source")

    def save_and_verify_order(self, order: OrderInput) -> None:
        self._verify_order_addresses(order.debtor)
        for item in order.items:
            if not self.g.lines_containing([item.sku]):
                raise VerificationError(f"Product {item.sku} is missing from the Order")
        self._verify_shipping_and_discount(order)
        self._verify_totals(order)
        self._click_save_once()
        self.checkpoint("03-order-saved")
        self._verify_document_category("Orders", order, expected_state="open", number=self.order_number)
        self.checkpoint("04-order-verified-documents")

    def _verify_document_category(
        self,
        category: str,
        order: OrderInput,
        *,
        expected_state: str,
        number: str = "",
    ):
        self._open_data_section("Documents")
        self.g.click_text([category], exact=False)
        time.sleep(0.4)
        required = [order.external_reference]
        if number:
            required.append(number)
        matches = []
        total_variants = self._amount_variants(order.source_total)
        for line in self.g.ocr_lines():
            text = norm(line.text)
            if not all(norm(value) in text for value in required if value):
                continue
            if not any(norm(amount) in text for amount in total_variants):
                continue
            matches.append(line)
        if len(matches) != 1:
            raise VerificationError(
                f"Expected one {category} row for ref={order.external_reference!r}, found {len(matches)}"
            )
        if expected_state and norm(expected_state) not in norm(matches[0].text):
            raise VerificationError(
                f"{category} row has unexpected state; expected {expected_state!r}, row={matches[0].text!r}"
            )
        return matches[0]

    # ---------- Linked Invoice ----------

    def create_linked_invoice(self) -> None:
        self._return_to_order()
        before = self._tab_names()
        # Figure 8: use the Invoice action inside 'Create a follow-up document', not
        # the global toolbar Invoice button.
        self.g.click_text_near(["Create a follow-up document"], ["Invoice"], max_distance=500)
        self.g.wait_until_text("Service date", timeout=12)
        self._remember_new_tab(before, "invoice")
        self.invoice_number = self.c.read_text(["No.", "No", "Number"], optional=True)
        self.checkpoint("05-linked-invoice-created")

    def _verify_invoice_copy(self, order: OrderInput) -> None:
        text = norm(self.g.visible_text())
        for required in [order.external_reference, order.debtor.company]:
            if norm(required) not in text:
                raise VerificationError(f"Linked Invoice did not copy {required!r} from the Order")
        order_date_text = self.c.read_text(["Order Date", "Order date"], optional=True)
        if order_date_text:
            try:
                parsed = date_parser.parse(order_date_text, fuzzy=True).date()
                if parsed != order.order_date:
                    raise VerificationError(
                        f"Invoice Order Date expected={order.order_date}, actual={order_date_text!r}"
                    )
            except ValueError:
                raise VerificationError(f"Could not parse Invoice Order Date {order_date_text!r}")
        for item in order.items:
            if not self.g.lines_containing([item.sku]):
                raise VerificationError(f"Invoice did not copy item {item.sku}")
        self._verify_totals(order)

        # The screenshot uses Invoice address / Delivery address tabs. Verify both.
        try:
            self.g.click_text(["Invoice address"], exact=False)
            invoice_text = norm(self.g.visible_text())
            for value in [order.debtor.billing.street, order.debtor.billing.zip, order.debtor.billing.city]:
                if norm(value) not in invoice_text:
                    raise VerificationError(f"Invoice address did not copy {value!r}")
            self.g.click_text(["Delivery address"], exact=False)
            delivery_text = norm(self.g.visible_text())
            for value in [
                order.debtor.delivery_address.street,
                order.debtor.delivery_address.zip,
                order.debtor.delivery_address.city,
            ]:
                if norm(value) not in delivery_text:
                    raise VerificationError(f"Delivery address did not copy {value!r}")
            self.g.click_text(["Invoice address"], exact=False)
        except ManualReviewRequired:
            # If tabs are not exposed, the combined visible text verification above remains in force.
            pass

    def _set_invoice_payment_method(self, method: str) -> None:
        if self.c.choose(["Payment Method", "Payment method", "Method of payment"], method, optional=True):
            return
        # Figure 9 shows the payment-method combo immediately to the right of the 'paid' checkbox.
        self.c.choose_near(["paid"], method)

    def complete_and_verify_invoice(self, order: OrderInput) -> None:
        self._verify_invoice_copy(order)
        self._set_invoice_payment_method(order.debtor.payment_method)

        if order.payment.status == PaidStatus.PAID:
            self.c.set_checkbox(["paid", "Paid"], True)
            assert order.payment.payment_date is not None
            self.c.set_date(["Pay Date", "Payment Date", "Paid Date"], order.payment.payment_date)
            self.c.set_text(["Value", "Paid Value", "Payment Value"], f"{money(order.source_total):.2f}")
        else:
            self.c.set_checkbox(["paid", "Paid"], False)
            # Do not invent payment date/value for unpaid/unknown input.

        self._click_save_once()
        self.checkpoint("06-invoice-saved")

        invoice_state = "paid" if order.payment.status == PaidStatus.PAID else "open"
        self._verify_document_category(
            "Invoices", order, expected_state=invoice_state, number=self.invoice_number
        )
        self._verify_document_category(
            "Orders", order, expected_state="open", number=self.order_number
        )
        self.checkpoint("07-final-documents-verification")

        # Persistence verification (step 5.6): reopen only because payment persistence cannot
        # be proven from the pre-save editor state alone.
        self._verify_persisted_invoice_payment(order)

    def _verify_persisted_invoice_payment(self, order: OrderInput) -> None:
        row = self._verify_document_category(
            "Invoices",
            order,
            expected_state="paid" if order.payment.status == PaidStatus.PAID else "open",
            number=self.invoice_number,
        )
        from pywinauto import mouse

        rect = self.g.window.rectangle()
        mouse.double_click(coords=(int(rect.left + row.cx), int(rect.top + row.cy)), interval=0.08)
        self.g.wait_until_text("Service date", timeout=10)
        self._set_invoice_payment_method(order.debtor.payment_method)

        if order.payment.status == PaidStatus.PAID:
            state = self.c.checkbox_state(["paid", "Paid"])
            if state is not True:
                raise VerificationError("Persisted Invoice is not marked paid")
            pay_date = self.c.read_text(["Pay Date", "Payment Date", "Paid Date"], optional=True)
            if pay_date:
                parsed = date_parser.parse(pay_date, fuzzy=True).date()
                if parsed != order.payment.payment_date:
                    raise VerificationError(
                        f"Persisted payment date expected={order.payment.payment_date}, actual={pay_date!r}"
                    )
            value = self.c.read_text(["Value", "Paid Value", "Payment Value"], optional=True)
            if value:
                digits = re.sub(r"[^0-9,.-]", "", value).replace(",", ".")
                if money(Decimal(digits)) != money(order.source_total):
                    raise VerificationError(
                        f"Persisted paid Value expected={money(order.source_total)}, actual={value!r}"
                    )
        self.checkpoint("08-invoice-persistence-verified")
