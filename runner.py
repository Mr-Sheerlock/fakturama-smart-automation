from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from pathlib import Path

from errors import ManualReviewRequired
from extractor import extract_order
from models import OrderInput


@dataclass
class RunResult:
    status: str
    evidence_dir: Path
    order: OrderInput | None = None
    message: str = ""


class ImageToCashRunner:
    def __init__(
        self,
        evidence_dir: str | Path,
        *,
        title_regex: str = r"(?i).*Fakturama.*",
    ):
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.title_regex = title_regex

    def run(
        self,
        image_path: str | Path,
        *,
        extract_only: bool = False,
        dump_uia: bool = False,
    ) -> RunResult:
        order: OrderInput | None = None
        try:
            order = extract_order(image_path, self.evidence_dir)
            if extract_only:
                self._write_status("EXTRACTED", "Source image extracted and validated")
                return RunResult("EXTRACTED", self.evidence_dir, order, "Source image extracted and validated")

            # Imported lazily so OCR/extraction tests can run on non-Windows hosts.
            from fakturama import FakturamaApp
            from ui import FakturamaSession

            session = FakturamaSession(self.evidence_dir, title_re=self.title_regex).connect()
            if dump_uia:
                session.require().dump_tree()
            app = FakturamaApp(session)

            app.open_new_order(order)
            app.ensure_debtor(order.debtor)
            for item in order.items:
                app.ensure_and_add_item(item)
            app.save_and_verify_order(order)
            app.create_linked_invoice()
            app.complete_and_verify_invoice(order)

            self._write_status("COMPLETED", "Order and linked Invoice saved and verified")
            return RunResult(
                "COMPLETED",
                self.evidence_dir,
                order,
                "Order and linked Invoice saved and verified",
            )
        except ManualReviewRequired as exc:
            self._write_status("MANUAL_REVIEW", str(exc), exc)
            return RunResult("MANUAL_REVIEW", self.evidence_dir, order, str(exc))
        except Exception as exc:
            self._write_status("FAILED", str(exc), exc)
            return RunResult("FAILED", self.evidence_dir, order, str(exc))

    def _write_status(self, status: str, message: str, exc: Exception | None = None) -> None:
        payload = {
            "status": status,
            "message": message,
            "traceback": traceback.format_exc() if exc else "",
        }
        (self.evidence_dir / "run_status.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
