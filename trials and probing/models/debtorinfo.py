# ---------------------------------------------------------------------------
# Source data (already extracted from the order image per spec 1.1/1.2) --
# passed in, not produced here.
# ---------------------------------------------------------------------------
from dataclasses import dataclass

@dataclass
class DebtorInfo:
    company: str = ""
    first_name: str = ""
    name: str = ""            # "Name" per spec -- last name / surname
    zip: str = ""
    city: str = ""
    street: str = ""
    country: str = ""
    email: str = ""
    telephone: str = ""
    alias: str = ""
    payment_method: str = ""  # e.g. "Bank Transfer", "Credit Card", "SEPA Direct Debit"
