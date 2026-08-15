from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageEnhance

from errors import ExtractionError


@dataclass(frozen=True)
class OCRToken:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int
    block: int
    paragraph: int
    line: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2


@dataclass(frozen=True)
class OCRLine:
    tokens: tuple[OCRToken, ...]

    @property
    def text(self) -> str:
        return " ".join(t.text for t in sorted(self.tokens, key=lambda t: t.left))

    @property
    def left(self) -> int:
        return min(t.left for t in self.tokens)

    @property
    def right(self) -> int:
        return max(t.right for t in self.tokens)

    @property
    def top(self) -> int:
        return min(t.top for t in self.tokens)

    @property
    def bottom(self) -> int:
        return max(t.bottom for t in self.tokens)

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(frozen=True)
class OCRResult:
    text: str
    tokens: tuple[OCRToken, ...]
    lines: tuple[OCRLine, ...]
    image_width: int
    image_height: int
    strategy: str
    image: Image.Image


ANCHORS = (
    "EXTERNAL REFERENCE",
    "ORDER DATE",
    "COMPANY",
    "PAYMENT METHOD",
    "ITEMS",
    "NET TOTAL",
    "GROSS TOTAL",
)


def configure_tesseract() -> None:
    explicit = os.environ.get("TESSERACT_CMD")
    if explicit:
        pytesseract.pytesseract.tesseract_cmd = explicit
        return
    if shutil.which("tesseract"):
        return
    windows_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if windows_default.exists():
        pytesseract.pytesseract.tesseract_cmd = str(windows_default)
        return
    raise ExtractionError(
        "Tesseract executable was not found. Install Tesseract and add it to PATH, "
        "or set TESSERACT_CMD to tesseract.exe."
    )


def _deskew_bgr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) < 200:
        return image
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    # Avoid destructive rotations when graphics dominate the page.
    if abs(angle) < 0.15 or abs(angle) > 5:
        return image
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _variants(path: Path) -> list[tuple[str, Image.Image]]:
    bgr = cv2.imread(str(path))
    if bgr is None:
        raise ExtractionError(f"Could not read source image: {path}")

    bgr = _deskew_bgr(bgr)
    h, w = bgr.shape[:2]
    if w < 1300:
        scale = 1300 / w
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    original = Image.fromarray(rgb)
    gray = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    contrast = ImageEnhance.Contrast(gray).enhance(1.35)
    return [("rgb-psm6", original), ("gray-psm6", gray), ("contrast-psm6", contrast)]


def _tokens_from_data(data: dict) -> tuple[OCRToken, ...]:
    tokens: list[OCRToken] = []
    for i, raw in enumerate(data["text"]):
        text = str(raw).strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if conf < 0:
            continue
        tokens.append(
            OCRToken(
                text=text,
                confidence=conf,
                left=int(data["left"][i]),
                top=int(data["top"][i]),
                width=int(data["width"][i]),
                height=int(data["height"][i]),
                block=int(data["block_num"][i]),
                paragraph=int(data["par_num"][i]),
                line=int(data["line_num"][i]),
            )
        )
    return tuple(tokens)


def _group_lines(tokens: tuple[OCRToken, ...]) -> tuple[OCRLine, ...]:
    groups: dict[tuple[int, int, int], list[OCRToken]] = {}
    for token in tokens:
        groups.setdefault((token.block, token.paragraph, token.line), []).append(token)
    lines = [OCRLine(tuple(sorted(items, key=lambda t: t.left))) for items in groups.values() if items]
    return tuple(sorted(lines, key=lambda line: (line.top, line.left)))


def _score(text: str, tokens: tuple[OCRToken, ...]) -> float:
    upper = " ".join(text.upper().split())
    anchor_score = sum(1 for anchor in ANCHORS if anchor in upper) * 100
    confidences = [t.confidence for t in tokens if t.confidence >= 0]
    confidence_score = (sum(confidences) / len(confidences)) if confidences else 0
    return anchor_score + confidence_score


def run_ocr(path: str | Path) -> OCRResult:
    configure_tesseract()
    path = Path(path)
    if not path.exists():
        raise ExtractionError(f"Source image does not exist: {path}")

    attempts: list[tuple[float, OCRResult]] = []
    for strategy, image in _variants(path):
        config = "--oem 3 --psm 6"
        data = pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT)
        tokens = _tokens_from_data(data)
        text = pytesseract.image_to_string(image, config=config)
        result = OCRResult(
            text=text,
            tokens=tokens,
            lines=_group_lines(tokens),
            image_width=image.width,
            image_height=image.height,
            strategy=strategy,
            image=image,
        )
        attempts.append((_score(text, tokens), result))

    attempts.sort(key=lambda pair: pair[0], reverse=True)
    if not attempts or not attempts[0][1].tokens:
        raise ExtractionError("OCR produced no usable text tokens")
    return attempts[0][1]
