from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from runner import ImageToCashRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Turn a supplied order image into a verified Fakturama Order + linked Invoice"
    )
    parser.add_argument(
        "image",
        nargs="?",
        type=Path,
        default=Path("samples/order.png"),
        help="Source order image. Defaults to samples/order.png so VS Code Run works immediately.",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="Directory for OCR, structured JSON, screenshots, UIA dump and run status",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Run OCR + validation only; do not touch Fakturama",
    )
    parser.add_argument(
        "--dump-uia",
        action="store_true",
        help="Save the Fakturama UI Automation tree to the evidence directory before running",
    )
    parser.add_argument(
        "--window-title-regex",
        default=r"(?i).*Fakturama.*",
        help="Regex used to find the one visible Fakturama main window",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    evidence = args.evidence_dir or Path("artifacts") / datetime.now().strftime("%Y%m%d-%H%M%S")
    result = ImageToCashRunner(evidence, title_regex=args.window_title_regex).run(
        args.image,
        extract_only=args.extract_only,
        dump_uia=args.dump_uia,
    )
    print(f"status={result.status}")
    print(f"evidence={result.evidence_dir.resolve()}")
    if result.message:
        print(f"message={result.message}")
    if result.order is not None:
        print(f"reference={result.order.external_reference}")
        print(f"items={len(result.order.items)}")
        print(f"total={result.order.source_total}")
    if result.status in {"COMPLETED", "EXTRACTED"}:
        return 0
    if result.status == "MANUAL_REVIEW":
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
