#!/usr/bin/env python3
"""
Fakturama Image‑to‑Cash – main runner
-------------------------------------
1. Connects to a running Fakturama instance.
2. Opens a New Order and sets Date, Cust.Ref., Price mode = Net.
3. Calls the debtor resolution (select existing or create new).
4. (Placeholder) Product resolution and Invoice creation follow.
"""

import time
import logging
from pywinauto import Application

# Import the order‑opening functions (from your newly written code)
from new_order_flow import open_and_set_order

# Import the debtor resolution functions (from your previous code)
# Adjust the import to match your actual module names.
# from debtor_resolution import try_select_existing_debtor, create_new_debtor
# For now we'll use a mock if the module isn't fully ready.

from models.debtorinfo import DebtorInfo   # if you have this model

# Optional: VLM extraction stub – replace with your real extractor.
# from extractor import extract_order_from_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def connect_to_fakturama():
    """Attach to the running Fakturama main window."""
    # Try to connect by window title
    app = Application(backend="uia").connect(title_re="Fakturama -")
    mainwindow = app.window(title_re="Fakturama -")
    
    mainwindow.wait("exists visible", timeout=10)
    mainwindow.set_focus()
    return app, mainwindow

def get_order_data_from_image(image_path):
    """
    Stub: replace with real OCR+LLM extraction.
    Returns an object with attributes: order_date, external_ref, debtor, etc.
    """
    # In reality you'd call extract_order_from_image(image_path)
    # For testing, return a hardcoded DebtorInfo (from your model)
    debtor = DebtorInfo(
        company="Acme Corp",
        first_name="John",
        name="Doe",
        zip="12345",
        city="Springfield",
        street="Main St 1",
        country="USA",
        email="john@acme.com",
        telephone="555-1234",
        alias="ACME",
        payment_method="Bank Transfer"
    )
    # Simple stub – you should fill all required fields.
    class DummyOrder:
        pass
    order = DummyOrder()
    order.order_date = "2026-07-15"
    order.external_ref = "PO-12345"
    order.debtor = debtor
    order.payment_method = "Bank Transfer"
    order.payment_status = "PAID"
    order.payment_date = "2026-07-16"
    order.total_gross = 1234.56
    logger.info("Order Data is DUMMY data")

    return order

def run_automation(image_path):
    """Main orchestration."""
    logger.info("Connecting to Fakturama...")
    app, mainwindow = connect_to_fakturama()

    # 1. Extract order data from image (implement this!)
    logger.info("Extracting order data from image...")
    order_data = get_order_data_from_image(image_path)

    # 2. Open a New Order and set basic fields (steps 1.3–1.8)
    logger.info("Opening New Order and setting fields...")
    order_pane = open_and_set_order(mainwindow, order_data)  # returns the order content pane

    # 3. Debtor resolution (steps 2.1–2.10)
    # The debtor resolution code expects `app_window` and `order_tab`
    # where `order_tab` is the container with the fields.
    # In your debtor code, they used `order_tab` – we pass the same pane.
    # logger.info("Resolving Debtor...")
    # from debtor_resolution import try_select_existing_debtor, create_new_debtor
    # try:
    #     found = try_select_existing_debtor(app, order_pane, order_data.debtor)
    #     if not found:
    #         logger.info("No existing Debtor found – creating new one...")
    #         create_new_debtor(app, order_data.debtor)
    #         # After creation, you would need to re‑select it in the Order.
    #         # This is a stub – you'll need to implement the re‑selection logic.
    # except Exception as e:
    #     logger.error(f"Debtor resolution failed: {e}")
    #     # Stop for manual review as per spec.
    #     raise

    # 4. Product resolution (steps 3.x) – placeholder
    logger.info("Product resolution not yet implemented – stopping for demo.")
    # Call product flow here when ready.

    # 5. Complete Order and create Invoice (steps 4.x–5.x) – placeholder
    logger.info("Order completion and Invoice creation not implemented yet.")

    logger.info("Automation flow finished (partial).")

if __name__ == "__main__":
    run_automation("fakturama_order.png")  # Replace with sys.argv[1] for real usage