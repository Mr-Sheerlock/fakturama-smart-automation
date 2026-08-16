"""
Fakturama UIA Control Probe
============================
Opens each master-data dialog and the Items region in turn, dumps the full
control tree to a timestamped file, and specifically flags whether any
list/table-like region exposes row/cell TEXT via UIA (ListItem, DataItem,
TreeItem, or a generic Custom control with readable Name/Value). That's the
concrete question this script answers: can 2.3/3.3's "exact match" logic
and the Items grid's per-line values be read via pywinauto, or do you need
the OCR/vision fallback baked in from the start.

PREREQUISITES
--------------
- pip install pywinauto
- Fakturama running, with a New Order already open and its "New Order" tab
  active (this script does not create the order for you).
- Run probe_items_grid() separately AFTER manually adding one line item —
  it's commented out of main() for that reason.
- Don't cover the Fakturama window with the terminal/editor while this
  runs — click_input() is a real OS-level click at screen coordinates.

KEY FINDING FROM LIVE TESTING
------------------------------
Select-the-address / Select-a-product are owned/modal dialogs that show up
as descendants of app_window in UIA's own tree — NOT as separate entries
in Desktop(backend="uia").windows(). Detection below is scoped to
app_window.descendants(control_type="Window") for that reason; diffing
Desktop().windows() will silently never find them.

Every probe is independently wrapped, so one missing control or unexpected
dialog won't stop the rest of the run. Check the console summary at the end
to see what still needs a manual look.
"""

import time
import datetime
from pathlib import Path
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
from pywinauto.timings import TimeoutError as PwaTimeoutError

OUTPUT_DIR = Path("./fakturama_probe_output")
OUTPUT_DIR.mkdir(exist_ok=True)
RUN_STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# Row/cell-like control types worth checking for exposed text.
ROW_LIKE_TYPES = {"ListItem", "DataItem", "TreeItem", "Custom"}


def timestamped_path(name: str) -> Path:
    return OUTPUT_DIR / f"{RUN_STAMP}_{name}.txt"


def dump_tree(window, label: str):
    """
    Write a flat listing of every descendant of `window` to a file:
    control_type, auto_id, text, and rectangle.

    Deliberately NOT using print_control_identifiers() — that method only
    exists on WindowSpecification (from .child_window()/.window()), not on
    the raw UIAWrapper objects that .descendants() returns. Since dialogs
    found via the diff-detection below come back as raw wrappers, this
    walks .descendants() manually instead, which both types support.
    """
    path = timestamped_path(label)
    lines = []
    try:
        lines.append(f"ROOT: text={window.window_text()!r} control_type={window.element_info.control_type}")
    except Exception as e:
        lines.append(f"ROOT: <could not read root element: {e}>")

    try:
        for elem in window.descendants():
            try:
                ctrl_type = elem.element_info.control_type
                auto_id = elem.element_info.automation_id
                text = elem.window_text()
                rect = elem.rectangle()
                lines.append(f"[{ctrl_type}] auto_id={auto_id!r} text={text!r} rect={rect}")
            except Exception as e:
                lines.append(f"  <error reading a descendant: {e}>")
    except Exception as e:
        lines.append(f"<could not enumerate descendants: {e}>")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[{label}] tree dumped -> {path}")
    return path


def scan_for_row_text(window, label: str):
    """
    Walk every descendant of `window` and report which ones look like
    list/grid rows (by control_type) AND expose non-empty text. This is
    the concrete answer to "does UIA expose row-level values here, or is
    this custom-painted and effectively opaque".
    """
    findings = []
    try:
        descendants = window.descendants()
    except Exception as e:
        print(f"[{label}] could not enumerate descendants: {e}")
        return findings

    for elem in descendants:
        try:
            ctrl_type = elem.element_info.control_type
        except Exception:
            continue
        if ctrl_type in ROW_LIKE_TYPES:
            try:
                text = elem.window_text()
            except Exception:
                text = ""
            if text.strip():
                findings.append((ctrl_type, elem.element_info.automation_id, text))

    report_path = timestamped_path(f"{label}_row_text_scan")
    with open(report_path, "w", encoding="utf-8") as f:
        if findings:
            f.write(f"{len(findings)} row-like elements WITH exposed text:\n\n")
            for ctrl_type, auto_id, text in findings:
                f.write(f"  [{ctrl_type}] auto_id={auto_id!r} text={text!r}\n")
        else:
            f.write(
                "NO row-like elements with exposed text found.\n"
                "This suggests the region is custom-painted and NOT exposing "
                "row/cell values via UIA -> plan for OCR/vision fallback here.\n"
            )
    print(f"[{label}] row-text scan: {len(findings)} hits -> {report_path}")
    return findings


def safe_probe(fn, *args, label=None, **kwargs):
    """Run a probe, catch+log failures, never let one probe kill the run."""
    label = label or fn.__name__
    try:
        fn(*args, **kwargs)
        return True
    except (ElementNotFoundError, PwaTimeoutError) as e:
        print(f"[{label}] SKIPPED — control/dialog not found: {e}")
        return False
    except Exception as e:
        print(f"[{label}] FAILED — unexpected error: {e}")
        return False


def _dismiss(dlg):
    """Best-effort close of a probe dialog: try Cancel, fall back to Escape."""
    try:
        dlg.child_window(title="Cancel", control_type="Button").click_input()
    except Exception:
        dlg.type_keys("{ESC}")


def _snapshot_window_descendants(app_window):
    """
    Fakturama's modal dialogs are owned by the main window and appear as
    descendants of app_window in UIA's tree, not as separate entries in
    Desktop(backend="uia").windows() — confirmed by testing. Snapshot by
    runtime_id (stable per-element) so a genuinely new one can be detected.
    """
    ids = set()
    for elem in app_window.descendants(control_type="Window"):
        try:
            ids.add(tuple(elem.element_info.runtime_id))
        except Exception:
            continue
    return ids


def _wait_for_new_window_descendant(app_window, before_ids, timeout=3.0, poll=0.2):
    """Poll app_window's own descendants for a "Window" not in before_ids."""
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


def identify_icon_by_click(app_window, icon, index, label):
    """
    Click an icon and see what new "Window"-type descendant appears under
    app_window. Returns the new element (still open) or None if nothing
    appeared within the timeout.
    """
    try:
        app_window.set_focus()
        time.sleep(0.2)
    except Exception as e:
        print(f"[{label}] icon[{index}] could not set focus before click: {e}")

    before = _snapshot_window_descendants(app_window)
    icon.click_input()
    new_win = _wait_for_new_window_descendant(app_window, before)
    title = new_win.window_text() if new_win else None
    print(f"[{label}] icon[{index}] auto_id={icon.element_info.automation_id} opened={title!r}")
    return new_win


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------

def probe_select_address(app_window, order_tab):
    """
    Anchored on the "Addresses" text label (title-based — survives session
    restarts, unlike auto_id). Tries each icon in turn and matches by the
    title of whatever "Window" descendant appears under app_window.
    """
    label = "select_address_dialog"
    addresses_label = order_tab.child_window(title="Addresses", control_type="Text")
    addresses_pane = addresses_label.parent()
    images = sorted(
        [c for c in addresses_pane.children() if c.element_info.control_type == "Image"],
        key=lambda c: c.rectangle().top,
    )
    if len(images) < 2:
        raise RuntimeError(f"expected 2 icons beside Addresses, found {len(images)}")

    for i, img in enumerate(images):
        new_win = identify_icon_by_click(app_window, img, i, label)
        if new_win is None:
            continue
        title = (new_win.window_text() or "").lower()
        print(f"[{label}] icon[{i}] opened window title: {title!r}")
        if "select the address" in title:
            dump_tree(new_win, label)
            scan_for_row_text(new_win, label)
            _dismiss(new_win)
            return
        # some other window opened (e.g. New Debtor editor) — close and continue
        new_win.type_keys("{ESC}")
        time.sleep(0.3)

    print(f"[{label}] none of the {len(images)} icons opened a 'Select the address' window — see printed titles above")


def probe_select_product(app_window, order_tab):
    """
    Anchored on the "Items" text label. There are FOUR unlabeled icons
    here (not two, like Addresses). Tries each in turn and matches by the
    title of whatever "Window" descendant appears under app_window.
    """
    label = "select_product_dialog"
    items_label = order_tab.child_window(title="Items", control_type="Text")
    items_pane = items_label.parent()
    images = sorted(
        [c for c in items_pane.children() if c.element_info.control_type == "Image"],
        key=lambda c: c.rectangle().top,
    )
    if not images:
        raise RuntimeError("expected icons beside Items, found none")

    for i, img in enumerate(images):
        new_win = identify_icon_by_click(app_window, img, i, label)
        if new_win is None:
            continue
        title = (new_win.window_text() or "").lower()
        if "select a product" in title:
            dump_tree(new_win, label)
            scan_for_row_text(new_win, label)
            _dismiss(new_win)
            return
        new_win.type_keys("{ESC}")
        time.sleep(0.3)

    print(f"[{label}] none of the {len(images)} icons opened a 'Select a product' window — see printed titles above")


def probe_new_debtor(app_window):
    """
    DISCOVERY MODE. Likely opens as a TabItem inside the existing "New
    Order" TabControl rather than a separate Window — click, wait, and
    dump the whole app window so you can find the real container in the
    file afterward (search for "Debtor").

    NOTE: entered via the left-nav "New Contact" shortcut, not via the
    Select-address dialog's own New panel (per spec 2.5) — confirm the
    real entry point behaves the same once you've found the container.
    """
    label = "new_debtor_editor"
    app_window.child_window(title="New Contact", control_type="Text").click_input()
    time.sleep(1.5)
    dump_tree(app_window, label)
    print(f"[{label}] dumped whole app window — search the file for 'Debtor' to find the real container")


def probe_new_product(app_window):
    """
    DISCOVERY MODE — same reasoning as probe_new_debtor above.

    NOTE: entered via the global "Create a new product" toolbar button,
    not the Order's Product-selector "New product" link (spec 3.7).
    Confirm the real entry point separately once the container is known.
    """
    label = "new_product_editor"
    app_window.child_window(title="Create a new product", control_type="Button").click_input()
    time.sleep(1.5)
    dump_tree(app_window, label)
    print(f"[{label}] dumped whole app window — search the file for 'product' to find the real container")


def probe_vats(app_window):
    label = "vats_editor"
    app_window.menu_select("Data->VATs")
    time.sleep(0.5)
    vats_view = app_window.child_window(title_re=".*VAT.*", control_type="Pane")
    dump_tree(vats_view, label)
    scan_for_row_text(vats_view, label)


def probe_terms_of_payment(app_window):
    label = "terms_of_payment_editor"
    app_window.menu_select("Data->terms of payment")
    time.sleep(0.5)
    top_view = app_window.child_window(title_re=".*terms of payment.*", control_type="Pane")
    dump_tree(top_view, label)
    scan_for_row_text(top_view, label)


def probe_items_grid(order_tab):
    """
    Run this MANUALLY after adding at least one line item to the open
    Order, so there's actual row data to check for. Not run by default —
    see main() below.
    """
    label = "items_grid_with_data"
    items_region = order_tab.child_window(auto_id="1510346", control_type="Pane")
    dump_tree(items_region, label)
    scan_for_row_text(items_region, label)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app_window = Desktop(backend="uia").window(title_re="Fakturama -")
    app_window.wait("exists visible", timeout=10)

    order_tab = app_window.child_window(title="New Order", control_type="Tab")
    order_tab.wait("exists visible", timeout=10)

    results = {}
    results["select_address"] = safe_probe(probe_select_address, app_window, order_tab, label="select_address")
    results["select_product"] = safe_probe(probe_select_product, app_window, order_tab, label="select_product")
    # results["new_debtor"] = safe_probe(probe_new_debtor, app_window, label="new_debtor")
    # results["new_product"] = safe_probe(probe_new_product, app_window, label="new_product")
    # results["vats"] = safe_probe(probe_vats, app_window, label="vats")
    # results["terms_of_payment"] = safe_probe(probe_terms_of_payment, app_window, label="terms_of_payment")

    # Uncomment after manually adding a line item to the open Order:
    # results["items_grid"] = safe_probe(probe_items_grid, order_tab, label="items_grid")

    print("\n=== PROBE SUMMARY ===")
    for name, ok in results.items():
        print(f"  {name:20s} {'OK' if ok else 'FAILED/SKIPPED'}")
    print(f"\nAll output written to: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()