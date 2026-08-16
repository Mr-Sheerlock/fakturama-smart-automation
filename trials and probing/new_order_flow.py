"""
Open Order flow (steps 1.3–1.8)
================================
Opens a new Order in Fakturama, sets the basic fields:
- Date (combo box, may require VLM fallback)
- External Reference (Cust.Ref.)
- Document price mode → Net (combo box to the right of Date)

VAT is left as "With VAT" (already default), and the Order tab is kept open.
"""

import re
import time
import pywinauto
from pywinauto.timings import wait_until

# Reuse the label‑anchored helpers from the debtor resolution module.
# In practice you would import them from a shared utils module.
# For clarity, they are duplicated here with minimal changes.

def set_edit_by_label(container, label_text, value, exact=True):
    """Set an Edit field next to a Static label."""
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
            for nxt in siblings[i+1:]:
                if nxt.element_info.control_type == "Edit":
                    nxt.set_focus()
                    nxt.set_edit_text(value)
                    return nxt
    raise RuntimeError(f"could not find an Edit control near label {label_text!r}")

def set_combobox_by_label(container, label_text, value, select_by_text=True, exact=True):
    """
    Find a ComboBox near a label and either set its text (editable combo)
    or select an item by text.
    If `select_by_text` is True, attempts combo.select(value).
    Otherwise, sets the edit text directly.
    Returns the ComboBox control.
    """
    if exact:
        # print(f"Looking for label {label_text!r} (exact match)")
        # label = container.child_window(title=label_text, control_type="Text")
        # print(f"Found label: {label.window_text()}")
        matches = container.descendants(
        title=label_text,
        control_type="Text")

        print("matches:", len(matches))

        for m in matches:
            print(
                repr(m.window_text()),
                m.element_info.control_type,
                m.element_info.automation_id
            )

        
    else:
        label = container.child_window(title_re=f".*{re.escape(label_text)}.*", control_type="Static")
    return ; 
    label.wait("exists", timeout=5)
    parent = label.parent()
    siblings = parent.children()
    label_rid = label.element_info.runtime_id
    for i, sib in enumerate(siblings):
        if sib.element_info.runtime_id == label_rid:
            for nxt in siblings[i+1:]:
                if nxt.element_info.control_type == "ComboBox":
                    if select_by_text:
                        # Try to select the item; if it fails (e.g. items not exposed),
                        # we fall back to a VLM‑based approach (see below).
                        nxt.select(value)
                    else:
                        nxt.set_focus()
                        nxt.set_edit_text(value)
                    return nxt
    raise RuntimeError(f"could not find a ComboBox near label {label_text!r}")

# ----------------------------------------------------------------------
# VLM fallback stubs (to be implemented if UIA selection fails)
# ----------------------------------------------------------------------
def _vlm_expand_and_select_combo(combo, value):
    """
    Placeholder: expand the combo, take a screenshot, use VLM to locate
    the item with text `value`, and click it.
    """
    raise NotImplementedError(
        "VLM fallback for combobox selection not yet implemented. "
        "Please implement using vlm_extract_table on the dropdown list."
    )

# ----------------------------------------------------------------------
# Main flow
# ----------------------------------------------------------------------

def open_new_order(app_window: pywinauto.WindowSpecification):
    """
    Step 1.3: Click Order in the top toolbar and wait for the New Order editor.
    Returns the Order tab pane (the container where we can set fields).
    """
    # Try toolbar button first, fallback to menu.
    try:
        order_btn = app_window.child_window(title="Create: New Order", control_type="Button")
        order_btn.click_input()
    except Exception:
        # Might be a menu item
        print("Toolbar Order button not found, trying menu...")
        app_window.menu_select("Order")
    # Wait for the New Order tab to appear.
    
    order_tab = app_window.child_window(title_re=".*New Order.*", control_type="TabItem")
    order_tab= order_tab.parent()
    # order_tab.wait("exists visible", timeout=10)
    # Ensure it's selected (click it if not already)
    if not order_tab.get_properties().get("selected", False):
        order_tab.click_input()

    print("TAB :", order_tab.element_info)

    print("TAB children:")
    for child in order_tab.children():
        print(
            child.element_info.control_type,
            repr(child.element_info.name),
            repr(child.element_info.automation_id)
        )
    print("TAB children end:")
    order_panes = order_tab.descendants(
    title="New Order",
    control_type="Pane"
    )

    print("Found panes:", len(order_panes))

    for pane in order_panes:
        print(pane.element_info)

    # Return the pane that contains the order fields (usually the parent of the tab)
    # In many UIA trees, the tab's parent is a pane that holds the content.
    # We'll return the tab itself for simplicity, assuming descendants are accessible.
    # For better targeting, we might return the pane inside the tab.
    # Let's return the tab's parent (which is the content pane).
    order_pane = order_panes[0]
    # But the actual editable fields might be deeper; we'll use `order_pane` as container.
    return order_pane

def set_order_basic_fields(order_pane, order_data):
    """
    Steps 1.4–1.7: leave No. unchanged, set Date, Cust.Ref., price mode to Net.
    Assumes order_pane is the container (e.g., the tab content pane).
    """
    # 1.4 No. – do nothing, it's automatically proposed.

    # 1.5 Date – set via combobox (editable).
    # Use VLM fallback if UIA selection fails? For date, we just set text.
    # date_combo = set_combobox_by_label(
    #     order_pane, "Date", order_data.order_date, select_by_text=False
    # )

    # date_edit = order_pane.child_window(
    # auto_id="1510776",
    # control_type="Edit"
    # )
    # print("setting")
    # date_edit.set_edit_text(str(order_data.order_date))

    print("order_pane descendants:")
    for i, control in enumerate(order_pane.descendants()):
        info = control.element_info
        print(
            i,
            "|",
            info.control_type,
            "|",
            repr(info.name),
            "|",
            repr(info.automation_id)
        )

    # labels = order_pane.descendants(
    # title="Date",
    # control_type="Text"
    # )

    # if not labels:
    #     raise RuntimeError("Could not find Date label")

    # date_label = labels[0]

    # parent = date_label.parent()

    # for child in parent.children():
    #     print(
    #         child.element_info.control_type,
    #         repr(child.element_info.name),
    #         child.element_info.automation_id
    #     )

    #     children = parent.children()

    # label_index = next(
    #     i for i, child in enumerate(children)
    #     if child.element_info.runtime_id == date_label.element_info.runtime_id
    # )

    # for child in children[label_index + 1:]:
    #     if child.element_info.control_type == "Pane":
    #         date_pane = child
    #         break
    # else:
    #     raise RuntimeError("Could not find Date pane")
    date_edit = order_pane.child_window(
    title="DateEdit"
    )
    # date_edit = order_pane.child_window(
    # auto_id="1510776", #10684720 1642224
    # control_type="Edit"
    # )

    date_edit.set_edit_text(str(order_data.order_date))
    # # If setting text doesn't work (maybe it's a read‑only combo),
    # # we could fallback to expanding and clicking a date in a calendar,
    # # but that's not a simple list selection. So we assume it's editable.
    # # If not, we may need a VLM‑based calendar clicker – not implemented here.

    # # 1.6 External Reference – plain text field.
    # set_edit_by_label(order_pane, "Cust.Ref.", order_data.external_ref)

    # # 1.7 Set document price mode to Net.
    # # The combo is "to the right of Date" – we can locate it by its own label,
    # # but the label might be "Price mode" or "Net/Gross".
    # # Use the label "Price mode" if present, else try "Net" or "Gross".
    # # We'll use a heuristic: find a ComboBox near "Price" or "Net".
    # # For robustness, we'll search for a ComboBox that is not the Date one.
    # # Simpler: we can try to find by label "Price mode".
    # try:
    #     price_combo = set_combobox_by_label(order_pane, "Price mode", "Net", select_by_text=True)
    # except Exception:
    #     # Maybe it's labeled "Net/Gross"
    #     price_combo = set_combobox_by_label(order_pane, "Net/Gross", "Net", select_by_text=True)
    # # If selection fails, fallback to VLM.
    # # We'll check if the selected value is actually "Net" – not implemented.

    # # 1.8 Keep Order tab open – nothing to do, we return the container.
    return order_pane

# ----------------------------------------------------------------------
# Integration with the outer flow
# ----------------------------------------------------------------------

def open_and_set_order(app_window, order_data):
    """
    Convenience wrapper that performs 1.3–1.7 and returns the order pane.
    """
    order_pane = open_new_order(app_window)
    set_order_basic_fields(order_pane, order_data)
    return order_pane

# Example usage:
# from fakturama_flows import open_and_set_order
# order_pane = open_and_set_order(app, extracted_order_data)
# # then proceed to debtor resolution, etc.