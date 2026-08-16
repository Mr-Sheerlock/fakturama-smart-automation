"""
Fakturama Debtor Resolution (spec 2.1-2.10)
=============================================
Try to select an existing Debtor from the Order via the Select-the-address
dialog; if no exact match, create a new Debtor (including, if needed, a
new Payment Method under Data > terms of payment). Plain UIA handles
text-entry fields. The one place UIA can't help -- reading rows out of a
results list/table -- falls back to a VLM reading a screenshot crop,
since earlier row-text scans on similar list views (vats, terms of
payment) came back with zero exposed row text.

DIVISION OF LABOR (VLM vs code)
---------------------------------
The VLM ONLY extracts what's on screen (row text + rough layout) as
structured JSON. It does NOT decide what counts as a match -- the exact-
match rule from spec 2.3 (Company, First Name, Name, ZIP, City must all
match) is applied in plain Python against the VLM's output, so that logic
stays deterministic and testable independent of the model.

CLICKING A ROW WE CAN'T QUERY
-------------------------------
Rather than asking the VLM for a precise pixel bounding box per row
(the kind of task these models are least reliable at), it's asked for
ONE bounding box (the table region) plus, per row, a row_center_y_frac --
a 0..1 fraction of where that row sits top-to-bottom within the table.
That position estimate is combined with the table's known screen
rectangle (from the dialog's own UIA rect) to compute an absolute click
point. Same idea as clicking from a stored rectangle, just derived live
from a screenshot instead of a static dump.

OPEN ITEMS -- best-effort, not yet confirmed against a live tree
--------------------------------------------------------------------
- create_new_debtor(): field labels/containers for the New Debtor editor
  (2.5-2.9) are guessed from the pattern that worked on the Order tab
  (label Static + next Edit sibling). The earlier probe dumped this
  editor's tree for manual inspection but the actual layout hasn't been
  confirmed back yet -- check new_debtor_editor.txt (search "Debtor")
  and share what you find so exact selectors can replace the guesses.
- 2.8 (assign Invoice/Delivery address role) and the Discount / Net-or-
  Gross controls in 2.9 are left as TODOs -- likely checkboxes/combos,
  not Edit fields, and the exact control_type is unconfirmed.
- The Payment tab's own dropdown (tried before falling back to the
  terms-of-payment search) is a placeholder lookup.

Test incrementally, same as the probe script: run try_select_existing_debtor
in isolation first, see what breaks, iterate from there -- don't expect
the whole flow to work end-to-end on the first pass.

PREREQUISITES
--------------
pip install pywinauto pillow huggingface_hub
HF_TOKEN environment variable set.
"""

import os
import re
import io
import json
import time
import base64
import time

import hashlib
import ctypes

from PIL import ImageGrab
from pywinauto.mouse import click as mouse_click

from VLM import vlm_extract_table
from models.debtorinfo import DebtorInfo


# Set process DPI awareness BEFORE any pywinauto/UIA objects are created.
# If this process and the screenshot tool (ImageGrab) disagree on DPI
# scaling, UIA-reported rectangles and screenshot pixel coordinates won't
# line up, and every computed click point below will be off.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
except Exception:
    pass




PAYMENT_CODE_MAP = {
    "Bank Transfer": "Credit transfer",
    "Credit Card": "Credit card",
    "SEPA Direct Debit": "SEPA direct debit",
}


# ---------------------------------------------------------------------------
# Screenshot + VLM table reading
# ---------------------------------------------------------------------------

def screenshot_region(rect):
    """rect: anything with .left/.top/.right/.bottom in absolute screen
    pixels (e.g. a pywinauto RECT from element.rectangle())."""
    return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))


def wait_for_visual_stabilize(rect, timeout=5.0, poll=0.4, stable_polls=2):
    """
    Spec 2.2 says "wait for the list to stabilize". Since the list's own
    content isn't exposed via UIA, stabilization is detected visually:
    hash consecutive screenshots of the dialog region until unchanged for
    `stable_polls` consecutive checks, or give up at `timeout`.
    """
    last_hash, stable_count = None, 0
    end = time.time() + timeout
    img = screenshot_region(rect)
    while time.time() < end:
        h = hashlib.md5(img.tobytes()).hexdigest()
        if h == last_hash:
            stable_count += 1
            if stable_count >= stable_polls:
                return img
        else:
            stable_count = 0
        last_hash = h
        time.sleep(poll)
        img = screenshot_region(rect)
    return img  # give up, return the last capture anyway



# ---------------------------------------------------------------------------
# Matching (spec 2.3) -- plain Python, not the VLM's job
# ---------------------------------------------------------------------------

def find_exact_match(rows, target: DebtorInfo):
    """
    Spec 2.3: "Treat a Debtor as an exact match only when the visible
    Company, First Name, Name, ZIP, and City match the extracted values."
    Returns ("match", row) | ("ambiguous", [rows]) | ("none", None).
    """
    def norm(s):
        return (s or "").strip().casefold()

    matches = [
        r for r in rows
        if norm(r.get("company")) == norm(target.company)
        and norm(r.get("first_name")) == norm(target.first_name)
        and norm(r.get("name")) == norm(target.name)
        and norm(r.get("zip")) == norm(target.zip)
        and norm(r.get("city")) == norm(target.city)
    ]
    if len(matches) == 1:
        return "match", matches[0]
    if len(matches) > 1:
        return "ambiguous", matches
    return "none", None


def compute_row_click_point(dialog_rect, table_bbox_frac, row_center_y_frac, x_frac=0.5):
    """Map a VLM-reported row position back to an absolute screen click point."""
    dw = dialog_rect.right - dialog_rect.left
    dh = dialog_rect.bottom - dialog_rect.top
    tx0, ty0, tx1, ty1 = table_bbox_frac
    table_left = dialog_rect.left + tx0 * dw
    table_top = dialog_rect.top + ty0 * dh
    table_w = (tx1 - tx0) * dw
    table_h = (ty1 - ty0) * dh
    return (table_left + x_frac * table_w, table_top + row_center_y_frac * table_h)


def click_absolute(x, y):
    mouse_click(coords=(int(round(x)), int(round(y))))


# ---------------------------------------------------------------------------
# UIA plumbing (dialog open/close pattern proven during probing)
# ---------------------------------------------------------------------------

def _snapshot_window_descendants(app_window):
    ids = set()
    for elem in app_window.descendants(control_type="Window"):
        try:
            ids.add(tuple(elem.element_info.runtime_id))
        except Exception:
            continue
    return ids


def _wait_for_new_window_descendant(app_window, before_ids, timeout=5.0, poll=0.2):
    end = time.time() + timeout
    while time.time() < end:
        for elem in app_window.descendants(control_type="Window"):
            try:
                rid = tuple(elem.element_info.runtime_id)
            except Exception:
                continue
            if rid not in before_ids:
                return elem
        time.sleep(poll)
    return None


def open_select_address_dialog(app_window, order_tab):
    """
    Spec 2.1: click the UPPER existing-contact icon beside Addresses --
    NOT the lower green + icon. Icons are found by label-anchoring on the
    "Addresses" Static and sorting siblings by current position, matching
    what proved reliable during probing (auto_id drifts across sessions;
    title text and structural position don't).
    """
    addresses_label = order_tab.child_window(title="Addresses", control_type="Text")
    addresses_pane = addresses_label.parent()
    images = sorted(
        [c for c in addresses_pane.children() if c.element_info.control_type == "Image"],
        key=lambda c: c.rectangle().top,
    )
    if len(images) < 2:
        raise RuntimeError(f"expected 2 icons beside Addresses, found {len(images)}")

    before = _snapshot_window_descendants(app_window)
    app_window.set_focus()
    time.sleep(0.2)
    images[0].click_input()  # upper icon = select existing, per spec wording
    dlg = _wait_for_new_window_descendant(app_window, before)
    if dlg is None or "select the address" not in (dlg.window_text() or "").lower():
        raise RuntimeError(
            f"expected 'Select the address' dialog, got {dlg.window_text() if dlg else None!r}"
        )
    return dlg


def search_in_dialog(dlg, search_text):
    """Type into the dialog's Search field -- assumes a single Edit
    control, matching the Search: pattern seen elsewhere in the app."""
    search_edit = dlg.child_window(control_type="Edit")
    search_edit.set_focus()
    search_edit.set_edit_text(search_text)


def set_edit_by_label(container, label_text, value, exact=True):
    """
    Generic label-anchored field setter: find a Static with `label_text`,
    then set the value of the next Edit sibling. Best-effort pattern based
    on what worked for the Order tab's own fields -- NOT yet confirmed
    against the actual New Debtor editor's tree. Adjust selectors once
    you've inspected new_debtor_editor.txt.
    """
    if exact:
        label = container.child_window(title=label_text, control_type="Text")
    else:
        label = container.child_window(title_re=f".*{re.escape(label_text)}.*", control_type="Text")
    label.wait("exists", timeout=5)
    parent = label.parent()
    siblings = parent.children()
    label_rid = label.element_info.runtime_id
    for i, sib in enumerate(siblings):
        if sib.element_info.runtime_id == label_rid:
            for nxt in siblings[i + 1:]:
                if nxt.element_info.control_type == "Edit":
                    nxt.set_focus()
                    nxt.set_edit_text(value)
                    return nxt
    raise RuntimeError(f"could not find an Edit control near label {label_text!r}")


# ---------------------------------------------------------------------------
# 2.1-2.4: try to select an existing Debtor from the Order
# ---------------------------------------------------------------------------

def try_select_existing_debtor(app_window, order_tab, target: DebtorInfo):
    """
    Runs 2.1-2.4. Returns True if an existing Debtor was selected and
    confirmed, False if none matched (caller should proceed to creation).
    Raises on ambiguous matches, per spec ("stop for manual review").
    """
    dlg = open_select_address_dialog(app_window, order_tab)          # 2.1
    search_in_dialog(dlg, target.company or target.name)             # 2.2
    dlg_rect = dlg.rectangle()
    img = wait_for_visual_stabilize(dlg_rect)                        # 2.2 "wait to stabilize"

    extracted = vlm_extract_table(
        img, "Select the address",
        fields=["company", "first_name", "name", "zip", "city"],
    )

    if not extracted.get("table_bbox") or not extracted.get("rows"):
        dlg.child_window(title="Cancel", control_type="Button").click_input()
        return False

    status, result = find_exact_match(extracted["rows"], target)     # 2.3

    if status == "ambiguous":
        raise RuntimeError(
            f"ambiguous Debtor match for {target.company!r} -- stopping for manual review: {result}"
        )

    if status == "none":
        dlg.child_window(title="Cancel", control_type="Button").click_input()
        return False

    # Exact match -- click the row, then OK.
    click_x, click_y = compute_row_click_point(
        dlg_rect, extracted["table_bbox"], result["row_center_y_frac"]
    )
    click_absolute(click_x, click_y)
    time.sleep(0.3)
    dlg.child_window(title="OK", control_type="Button").click_input()

    # 2.4: confirm populated Invoice/Delivery addresses match the source.
    # Those fields ARE plain UIA Edit controls once the Order re-renders,
    # so a window_text() comparison against `target` works here without
    # another VLM call -- left for the caller to add if wanted.
    return True


# ---------------------------------------------------------------------------
# 2.5-2.9: create a new Debtor
# ---------------------------------------------------------------------------

def create_new_debtor(app_window, target: DebtorInfo):
    app_window.child_window(title="New Contact", control_type="Text").click_input()  # 2.5
    time.sleep(1.0)
    # TODO: confirmed container selector pending -- see module docstring.
    editor = app_window.child_window(title_re="New Debtor.*")
    editor.wait("exists visible", timeout=5)

    set_edit_by_label(editor, "Company", target.company)              # 2.6
    set_edit_by_label(editor, "First Name", target.first_name)
    set_edit_by_label(editor, "Last Name", target.name)

    editor.child_window(title="Addresses", control_type="TabItem").click_input()  # 2.7
    set_edit_by_label(editor, "Street", target.street)
    set_edit_by_label(editor, "ZIP", target.zip)
    set_edit_by_label(editor, "City", target.city)
    if target.country:
        set_edit_by_label(editor, "Country", target.country)
    if target.email:
        set_edit_by_label(editor, "E-Mail", target.email)
    if target.telephone:
        set_edit_by_label(editor, "Telephone", target.telephone)

    # 2.8: assign Invoice (and Delivery, if identical) address role --
    # likely a checkbox/dropdown near the address block. TODO: confirm
    # exact control once the editor's tree has been inspected.

    editor.child_window(title="Miscellaneous", control_type="TabItem").click_input()  # 2.9
    set_edit_by_label(editor, "Alias name", target.alias)
    # Discount / Net-or-Gross: likely combo boxes, not Edit fields --
    # TODO: confirm control_type once verified.

    return editor


# ---------------------------------------------------------------------------
# 2.10: select or create the Payment Method
# ---------------------------------------------------------------------------

def open_terms_of_payment_search(app_window):
    app_window.menu_select("Data->terms of payment")                 # 2.10.1
    time.sleep(0.5)
    return app_window.child_window(title_re=".*terms of payment.*", control_type="Pane")


def select_or_create_payment_method(app_window, editor, target: DebtorInfo):
    editor.child_window(title="Payment", control_type="TabItem").click_input()
    # TODO: try the Payment tab's own dropdown first -- cheaper than the
    # terms-of-payment search if the method is already a plain ComboBox
    # option. Placeholder pending the editor's confirmed control layout.

    top_view = open_terms_of_payment_search(app_window)               # 2.10.1
    search_edit = top_view.child_window(control_type="Edit")
    search_edit.set_edit_text(target.payment_method)

    rect = top_view.rectangle()
    img = wait_for_visual_stabilize(rect)
    extracted = vlm_extract_table(
        img, "terms of payment",
        fields=["name", "description"],
    )

    def norm(s):
        return (s or "").strip().casefold()

    matches = [
        r for r in extracted.get("rows", [])
        if norm(r.get("name")) == norm(target.payment_method)
    ]

    if len(matches) > 1:
        raise RuntimeError(
            f"ambiguous Payment Method {target.payment_method!r} -- stopping for manual review"
        )

    if len(matches) == 1:
        return  # 2.10.2: reuse existing -- nothing further to create

    # 2.10.2 (no match) -> 2.10.3-2.10.6: create it.
    new_button = top_view.child_window(title_re=".*\\+.*", control_type="Button")  # best-effort
    new_button.click_input()
    time.sleep(0.5)

    set_edit_by_label(app_window, "Name", target.payment_method)      # 2.10.3
    set_edit_by_label(app_window, "Description", target.payment_method)

    code_value = PAYMENT_CODE_MAP.get(target.payment_method)          # 2.10.4
    if code_value is None:
        raise RuntimeError(f"no payment-code mapping for {target.payment_method!r}")
    payment_code_combo = app_window.child_window(control_type="ComboBox")
    payment_code_combo.select(code_value)

    for field_label in ("Cash discount", "Discount Days", "Net Days"):  # 2.10.5
        set_edit_by_label(app_window, field_label, "0")

    app_window.child_window(
        title="Save the current contents", control_type="Button"
    ).click_input()                                                   # 2.10.6
    time.sleep(0.5)
    # Caller returns to the still-open Debtor editor and selects the new method.