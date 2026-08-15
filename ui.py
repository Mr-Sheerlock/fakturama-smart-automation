from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np
import pytesseract
from dateutil import parser as date_parser
from PIL import Image
from pywinauto import Desktop, keyboard, mouse
from pywinauto.base_wrapper import BaseWrapper

from errors import AutomationError, ManualReviewRequired, VerificationError


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


@dataclass(frozen=True)
class Candidate:
    wrapper: BaseWrapper
    text: str
    control_type: str
    automation_id: str


@dataclass(frozen=True)
class VisualBox:
    text: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


class Grounder:
    """UIA-first runtime grounding with OCR/computer-vision fallbacks.

    All fallback clicks are calculated from the current Fakturama window and
    currently recognized controls/text. There are no stored screen coordinates.
    """

    def __init__(self, window: BaseWrapper, evidence_dir: Path):
        self.window = window
        self.evidence_dir = evidence_dir
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def descendants(self) -> list[Candidate]:
        out: list[Candidate] = []
        try:
            wrappers = self.window.descendants()
        except Exception:
            wrappers = []
        for wrapper in wrappers:
            try:
                info = wrapper.element_info
                text = wrapper.window_text() or info.name or ""
                out.append(
                    Candidate(
                        wrapper=wrapper,
                        text=text,
                        control_type=info.control_type or "",
                        automation_id=info.automation_id or "",
                    )
                )
            except Exception:
                continue
        return out

    def find_all_uia(
        self,
        names: Sequence[str],
        *,
        control_types: Sequence[str] | None = None,
        exact: bool = False,
    ) -> list[Candidate]:
        wanted = [norm(name) for name in names]
        type_set = {norm(t) for t in (control_types or [])}
        matches: list[Candidate] = []
        for candidate in self.descendants():
            if type_set and norm(candidate.control_type) not in type_set:
                continue
            text = norm(candidate.text)
            aid = norm(candidate.automation_id)
            matched = (
                any(text == value or aid == value for value in wanted)
                if exact
                else any(value in text or value in aid for value in wanted)
            )
            if matched:
                try:
                    if candidate.wrapper.is_visible() and candidate.wrapper.is_enabled():
                        matches.append(candidate)
                except Exception:
                    matches.append(candidate)
        return matches

    def find_uia(
        self,
        names: Sequence[str],
        *,
        control_types: Sequence[str] | None = None,
        exact: bool = False,
    ) -> BaseWrapper | None:
        matches = self.find_all_uia(names, control_types=control_types, exact=exact)
        return matches[0].wrapper if len(matches) == 1 else None

    def _ocr_words(self) -> tuple[Image.Image, list[dict]]:
        image = self.window.capture_as_image()
        data = pytesseract.image_to_data(
            image, config="--oem 3 --psm 6", output_type=pytesseract.Output.DICT
        )
        words: list[dict] = []
        for i, raw in enumerate(data["text"]):
            text = str(raw).strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            if conf < 35:
                continue
            words.append(
                {
                    "text": text,
                    "norm": norm(text),
                    "left": int(data["left"][i]),
                    "top": int(data["top"][i]),
                    "right": int(data["left"][i]) + int(data["width"][i]),
                    "bottom": int(data["top"][i]) + int(data["height"][i]),
                    "block": int(data["block_num"][i]),
                    "par": int(data["par_num"][i]),
                    "line": int(data["line_num"][i]),
                }
            )
        return image, words

    def ocr_lines(self) -> list[VisualBox]:
        _, words = self._ocr_words()
        groups: dict[tuple[int, int, int], list[dict]] = {}
        for word in words:
            groups.setdefault((word["block"], word["par"], word["line"]), []).append(word)
        lines: list[VisualBox] = []
        for group in groups.values():
            group.sort(key=lambda w: w["left"])
            lines.append(
                VisualBox(
                    text=" ".join(w["text"] for w in group),
                    left=min(w["left"] for w in group),
                    top=min(w["top"] for w in group),
                    right=max(w["right"] for w in group),
                    bottom=max(w["bottom"] for w in group),
                )
            )
        return sorted(lines, key=lambda line: (line.top, line.left))

    def find_ocr_phrase(self, names: Sequence[str], *, exact: bool = False) -> list[VisualBox]:
        # Return the bounding box of the phrase itself, not the whole OCR line.
        # This matters for table headers: the runtime x-position of 'Qty.' or 'VAT'
        # is what allows grid-cell editing without fixed coordinates.
        _, words = self._ocr_words()
        groups: dict[tuple[int, int, int], list[dict]] = {}
        for word in words:
            groups.setdefault((word["block"], word["par"], word["line"]), []).append(word)

        def clean(value: str) -> str:
            return re.sub(r"[^a-z0-9%]+", "", value.casefold())

        matches: list[VisualBox] = []
        seen = set()
        for group in groups.values():
            group.sort(key=lambda w: w["left"])
            cleaned = [clean(w["text"]) for w in group]
            line_text = norm(" ".join(w["text"] for w in group))
            for name in names:
                phrase_words = [clean(part) for part in name.split() if clean(part)]
                if not phrase_words:
                    continue
                for start in range(0, len(cleaned) - len(phrase_words) + 1):
                    if cleaned[start : start + len(phrase_words)] != phrase_words:
                        continue
                    if exact and line_text != norm(name):
                        continue
                    selected = group[start : start + len(phrase_words)]
                    box = VisualBox(
                        text=" ".join(w["text"] for w in selected),
                        left=min(w["left"] for w in selected),
                        top=min(w["top"] for w in selected),
                        right=max(w["right"] for w in selected),
                        bottom=max(w["bottom"] for w in selected),
                    )
                    key = (box.left, box.top, box.right, box.bottom)
                    if key not in seen:
                        seen.add(key)
                        matches.append(box)
        return matches

    def visual_boxes(self, names: Sequence[str], *, exact: bool = False) -> list[VisualBox]:
        boxes: list[VisualBox] = []
        for candidate in self.find_all_uia(names, exact=exact):
            try:
                rect = candidate.wrapper.rectangle()
                boxes.append(
                    VisualBox(candidate.text, rect.left, rect.top, rect.right, rect.bottom)
                )
            except Exception:
                pass
        if boxes:
            return boxes
        # OCR positions are window-local; convert them to screen coordinates.
        rect = self.window.rectangle()
        for box in self.find_ocr_phrase(names, exact=exact):
            boxes.append(
                VisualBox(
                    box.text,
                    rect.left + box.left,
                    rect.top + box.top,
                    rect.left + box.right,
                    rect.top + box.bottom,
                )
            )
        return boxes

    def click_text(self, names: Sequence[str], *, exact: bool = False) -> None:
        control = self.find_uia(
            names,
            control_types=["Button", "Hyperlink", "MenuItem", "Text", "TreeItem", "TabItem","Image"],
            exact=exact,
        )
        if control is not None:
            try:
                control.invoke()
            except Exception:
                control.click_input()
            return

        boxes = self.visual_boxes(names, exact=exact)
        if len(boxes) != 1:
            self.screenshot("ambiguous_text_click")
            raise ManualReviewRequired(
                f"Could not uniquely locate visible UI text {list(names)}; matches={len(boxes)}"
            )
        mouse.click(coords=(int(boxes[0].cx), int(boxes[0].cy)))

    def click_text_near(
        self,
        anchor_names: Sequence[str],
        target_names: Sequence[str],
        *,
        max_distance: float = 550,
    ) -> None:
        anchors = self.visual_boxes(anchor_names, exact=False)
        targets = self.visual_boxes(target_names, exact=True) or self.visual_boxes(target_names, exact=False)
        if not anchors or not targets:
            raise ManualReviewRequired(
                f"Could not locate anchored action anchor={anchor_names}, target={target_names}"
            )
        scored = []
        for anchor in anchors:
            for target in targets:
                distance = ((target.cx - anchor.cx) ** 2 + (target.cy - anchor.cy) ** 2) ** 0.5
                if distance <= max_distance:
                    scored.append((distance, target))
        if not scored:
            raise ManualReviewRequired(
                f"Target {target_names} is not near anchor {anchor_names}; refusing global click"
            )
        scored.sort(key=lambda pair: pair[0])
        if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 8:
            raise ManualReviewRequired(f"Anchored action is ambiguous: {target_names}")
        target = scored[0][1]
        mouse.click(coords=(int(target.cx), int(target.cy)))

    def click_topmost_button_near(
        self,
        anchor_names: Sequence[str],
        *,
        radius: float = 240
    ) -> None:

        anchors = self.visual_boxes(anchor_names, exact=False)

        if not anchors:
            raise ManualReviewRequired(
                f"Could not locate anchor for icon control: {anchor_names}"
            )

        anchor = anchors[0]
        candidates = []

        for candidate in self.descendants():

            # candidate is our Candidate dataclass
            if candidate.control_type not in {"Button", "Image"}:
                continue

            try:
                wrapper = candidate.wrapper

                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue

                rect = wrapper.rectangle()

                cx = (rect.left + rect.right) / 2
                cy = (rect.top + rect.bottom) / 2

                distance = (
                    (cx - anchor.cx) ** 2 +
                    (cy - anchor.cy) ** 2
                ) ** 0.5

                # Don't allow controls above the section label
                if distance <= radius and cy >= anchor.top - 20:
                    candidates.append(
                        (cy, distance, wrapper)
                    )

            except Exception:
                continue

        if not candidates:
            raise ManualReviewRequired(
                f"No runtime Button/Image controls were found near "
                f"{anchor_names}. Run --dump-uia for calibration."
            )

        # First: upper control
        # Second: closest control
        candidates.sort(
            key=lambda item: (item[0], item[1])
        )

        control = candidates[0][2]

        try:
            control.click_input()
        except Exception:
            rect = control.rectangle()

            mouse.click(
                coords=(
                    (rect.left + rect.right) // 2,
                    (rect.top + rect.bottom) // 2
                )
            )
    def click_green_plus_near(self, anchor_names: Sequence[str], *, radius: float = 650) -> None:
        anchors = self.visual_boxes(anchor_names, exact=False)
        if not anchors:
            raise ManualReviewRequired(f"Could not locate anchor for green +: {anchor_names}")
        anchor = anchors[0]

        # Prefer semantically exposed buttons, but still scope them to the requested
        # section. A global 'New' button is not safe in Fakturama.
        semantic_candidates = []
        for candidate in self.find_all_uia(["Add", "New", "+"], control_types=["Button"], exact=False):
            try:
                rect = candidate.wrapper.rectangle()
                cx, cy = (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2
                distance = ((cx - anchor.cx) ** 2 + (cy - anchor.cy) ** 2) ** 0.5
                if distance <= radius:
                    semantic_candidates.append((distance, candidate.wrapper))
            except Exception:
                continue
        if semantic_candidates:
            semantic_candidates.sort(key=lambda pair: pair[0])
            control = semantic_candidates[0][1]
            try:
                control.invoke()
            except Exception:
                control.click_input()
            return
        image = self.window.capture_as_image()
        rgb = np.array(image.convert("RGB"))
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        # Broad green range; candidate selection is still anchored to semantic text.
        mask = cv2.inRange(hsv, np.array([35, 45, 40]), np.array([95, 255, 255]))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        window_rect = self.window.rectangle()
        candidates = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area < 12 or area > 2500 or w > 60 or h > 60:
                continue
            cx = window_rect.left + x + w / 2
            cy = window_rect.top + y + h / 2
            distance = ((cx - anchor.cx) ** 2 + (cy - anchor.cy) ** 2) ** 0.5
            if distance <= radius:
                candidates.append((distance, cx, cy))
        if not candidates:
            self.screenshot("green_plus_not_found")
            raise ManualReviewRequired(f"Could not visually locate green + near {anchor_names}")
        candidates.sort(key=lambda item: item[0])
        mouse.click(coords=(int(candidates[0][1]), int(candidates[0][2])))

    def edit_grid_cell(self, row_key: str, header_names: Sequence[str], value: str) -> None:
        row_matches = [line for line in self.ocr_lines() if norm(row_key) in norm(line.text)]
        headers = self.find_ocr_phrase(header_names, exact=False)
        if len(row_matches) != 1 or not headers:
            self.screenshot("grid_grounding_failure")
            raise ManualReviewRequired(
                f"Could not ground grid cell row={row_key!r}, header={list(header_names)}"
            )
        # Prefer the shortest matching header line to avoid a whole table row if possible.
        header = sorted(headers, key=lambda box: len(box.text))[0]
        # If the OCR line contains multiple headers, locate a word-level target from UIA if available.
        header_boxes = self.visual_boxes(header_names, exact=False)
        if header_boxes:
            hx = header_boxes[0].cx
        else:
            # Last-resort: use the OCR line midpoint. This is only reached when a header itself
            # cannot be isolated; typically UIA exposes column header names.
            rect = self.window.rectangle()
            hx = rect.left + header.cx
        rect = self.window.rectangle()
        row_y = rect.top + row_matches[0].cy
        mouse.double_click(coords=(int(hx), int(row_y)), interval=0.08)
        keyboard.send_keys("^a{BACKSPACE}")
        keyboard.send_keys(str(value), with_spaces=True)
        keyboard.send_keys("{ENTER}")
        time.sleep(0.25)

    def lines_containing(self, required: Sequence[str]) -> list[VisualBox]:
        wanted = [norm(value) for value in required if value]
        return [
            line for line in self.ocr_lines() if all(value in norm(line.text) for value in wanted)
        ]

    def visible_text(self) -> str:
        uia = " ".join(candidate.text for candidate in self.descendants() if candidate.text)
        ocr = " ".join(line.text for line in self.ocr_lines())
        return f"{uia}\n{ocr}"

    def wait_until_text(self, text: str, timeout: float = 12.0) -> None:
        deadline = time.time() + timeout
        needle = norm(text)
        while time.time() < deadline:
            if needle in norm(self.visible_text()):
                return
            time.sleep(0.25)
        self.screenshot("wait_timeout")
        raise ManualReviewRequired(f"Timed out waiting for UI text: {text}")

    def stable_text_snapshot(self, timeout: float = 8.0, settle: float = 0.7) -> list[str]:
        deadline = time.time() + timeout
        previous = None
        stable_since = time.time()
        while time.time() < deadline:
            current = sorted(norm(line.text) for line in self.ocr_lines() if norm(line.text))
            if current == previous:
                if time.time() - stable_since >= settle:
                    return current
            else:
                previous = current
                stable_since = time.time()
            time.sleep(0.2)
        return previous or []

    def screenshot(self, name: str) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.evidence_dir / f"{stamp}-{name}.png"
        self.window.capture_as_image().save(path)
        return path

    def dump_tree(self, filename: str = "uia_tree.txt") -> Path:
        lines = []
        for candidate in self.descendants():
            try:
                rect = candidate.wrapper.rectangle()
                lines.append(
                    f"{candidate.control_type:14} name={candidate.text!r} "
                    f"automation_id={candidate.automation_id!r} rect={rect}"
                )
            except Exception:
                lines.append(
                    f"{candidate.control_type:14} name={candidate.text!r} automation_id={candidate.automation_id!r}"
                )
        path = self.evidence_dir / filename
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


class Controls:
    def __init__(self, grounder: Grounder):
        self.g = grounder

    def _candidate_fields(self, control_types: Sequence[str]) -> list[BaseWrapper]:
        wanted = {norm(t) for t in control_types}
        return [c.wrapper for c in self.g.descendants() if norm(c.control_type) in wanted]

    def field(
        self,
        labels: Sequence[str],
        control_types: Sequence[str] = ("Edit", "ComboBox"),
        *,
        optional: bool = False,
    ) -> BaseWrapper | None:
        direct = []
        wanted = [norm(label) for label in labels]
        types = {norm(t) for t in control_types}
        for candidate in self.g.descendants():
            if norm(candidate.control_type) not in types:
                continue
            text = norm(candidate.text)
            aid = norm(candidate.automation_id)
            if any(label in text or label in aid for label in wanted):
                direct.append(candidate.wrapper)
        if len(direct) == 1:
            return direct[0]

        labels_boxes = self.g.visual_boxes(labels, exact=False)
        if labels_boxes:
            label_box = labels_boxes[0]
            scored = []
            for field in self._candidate_fields(control_types):
                try:
                    if not field.is_visible() or not field.is_enabled():
                        continue
                    rect = field.rectangle()
                    cx, cy = (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2
                    vertical_overlap = not (rect.bottom < label_box.top or rect.top > label_box.bottom)
                    # Forms normally place the editor to the right of a label; allow a nearby
                    # editor below it for date widgets / SWT composites.
                    if cx >= label_box.cx and vertical_overlap:
                        distance = abs(cx - label_box.cx) + abs(cy - label_box.cy) * 2
                        scored.append((distance, field))
                    elif cy >= label_box.cy and abs(cx - label_box.cx) < 280:
                        distance = abs(cx - label_box.cx) * 2 + abs(cy - label_box.cy)
                        scored.append((distance + 100, field))
                except Exception:
                    continue
            if scored:
                scored.sort(key=lambda pair: pair[0])
                if len(scored) == 1 or scored[0][0] + 12 < scored[1][0]:
                    return scored[0][1]

        if optional:
            return None
        self.g.screenshot("field_not_found")
        raise ManualReviewRequired(
            f"Could not uniquely discover field {list(labels)} with types={list(control_types)}"
        )

    @staticmethod
    def _raw_value(ctrl: BaseWrapper) -> str:
        for getter in ("get_value", "selected_text", "window_text"):
            try:
                value = getattr(ctrl, getter)()
                if value is not None:
                    return str(value)
            except Exception:
                continue
        return ""

    def read_text(
        self,
        labels: Sequence[str],
        control_types: Sequence[str] = ("Edit", "ComboBox", "Text"),
        *,
        optional: bool = False,
    ) -> str:
        ctrl = self.field(labels, control_types, optional=optional)
        if ctrl is None:
            return ""
        return self._raw_value(ctrl).strip()

    def set_text(self, labels: Sequence[str], value: str, *, verify: bool = True) -> None:
        ctrl = self.field(labels, ("Edit",))
        assert ctrl is not None
        try:
            ctrl.set_edit_text(str(value))
        except Exception:
            ctrl.click_input()
            keyboard.send_keys("^a{BACKSPACE}")
            keyboard.send_keys(str(value), with_spaces=True)
        if verify:
            actual = self._raw_value(ctrl)
            if norm(str(value)) not in norm(actual):
                raise VerificationError(
                    f"Field {labels[0]} expected={value!r}, actual={actual!r}"
                )

    def click_field_button(
        self,
        labels: Sequence[str],
    ) -> None:

        field = self.field(
            labels,
            ("Edit",),
        )

        if field is None:
            raise ManualReviewRequired(
                f"Could not locate field for button: {list(labels)}"
            )

        field_rect = field.rectangle()

        candidates = []

        for candidate in self.g.descendants():

            if norm(candidate.control_type) != "button":
                continue

            try:
                wrapper = candidate.wrapper

                if not wrapper.is_visible() or not wrapper.is_enabled():
                    continue

                rect = wrapper.rectangle()

                # Button must be on the same row as the Edit.
                vertical_overlap = not (
                    rect.bottom < field_rect.top
                    or rect.top > field_rect.bottom
                )

                # It must be immediately to the right of the field.
                right_of_field = rect.left >= field_rect.right - 5

                if not vertical_overlap or not right_of_field:
                    continue

                distance = abs(rect.left - field_rect.right)

                candidates.append(
                    (distance, wrapper)
                )

            except Exception:
                continue

        if not candidates:
            raise ManualReviewRequired(
                f"Could not locate dropdown button for field {list(labels)}"
            )

        candidates.sort(
            key=lambda item: item[0]
        )

        button = candidates[0][1]

        button.click_input()
    
    def set_date(self, labels: Sequence[str], expected: date) -> None:
        ctrl = self.field(labels, ("Edit", "ComboBox"))
        assert ctrl is not None
        existing = self._raw_value(ctrl)
        formats = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%b %d, %Y"]
        # Try to preserve the control's current formatting convention first.
        if "." in existing:
            formats.insert(0, "%d.%m.%Y")
        elif "/" in existing:
            formats.insert(0, "%m/%d/%Y")
        elif re.search(r"[A-Za-z]", existing):
            formats.insert(0, "%b %d, %Y")

        tried = []
        for fmt in dict.fromkeys(formats):
            value = expected.strftime(fmt)
            tried.append(value)
            try:
                ctrl.click_input()
                keyboard.send_keys("^a{BACKSPACE}")
                keyboard.send_keys(value, with_spaces=True)
                keyboard.send_keys("{TAB}")
                time.sleep(0.15)
                actual = self._raw_value(ctrl)
                parsed = date_parser.parse(actual, fuzzy=True, dayfirst=("." in actual)).date()
                if parsed == expected:
                    return
            except Exception:
                continue
        raise VerificationError(
            f"Could not set date field {labels[0]} to {expected.isoformat()}; tried={tried}"
        )

    def choose(self, labels: Sequence[str], option: str, *, optional: bool = False) -> bool:
        combo = self.field(labels, ("ComboBox",), optional=optional)
        if combo is None:
            return False
        try:
            combo.select(option)
        except Exception:
            # Inspect list items when exposed so a unique option containing the exact
            # master-data name (e.g. 'VAT 19% (19.0%)') can be selected safely.
            matches = []
            try:
                for item in combo.descendants():
                    text = item.window_text().strip()
                    if norm(option) == norm(text) or norm(option) in norm(text):
                        matches.append(text)
            except Exception:
                pass
            if len(set(matches)) == 1:
                try:
                    combo.select(matches[0])
                except Exception:
                    combo.click_input()
                    keyboard.send_keys(matches[0], with_spaces=True)
                    keyboard.send_keys("{ENTER}")
            else:
                combo.click_input()
                keyboard.send_keys(str(option), with_spaces=True)
                keyboard.send_keys("{ENTER}")
        actual = self._raw_value(combo)
        if norm(option) not in norm(actual):
            if optional:
                return False
            raise VerificationError(
                f"Combo {labels[0]} expected option containing {option!r}, actual={actual!r}"
            )
        return True

    def choose_near(self, anchor_labels: Sequence[str], option: str) -> None:
        anchors = self.g.visual_boxes(anchor_labels, exact=False)
        if not anchors:
            raise ManualReviewRequired(f"Could not locate anchor for nearby combo: {anchor_labels}")
        anchor = anchors[0]
        combos = []
        for candidate in self.g.descendants():
            if norm(candidate.control_type) != "combobox":
                continue
            try:
                rect = candidate.wrapper.rectangle()
                cx, cy = (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2
                if cx >= anchor.cx and abs(cy - anchor.cy) < 100:
                    combos.append((abs(cx - anchor.cx) + abs(cy - anchor.cy), candidate.wrapper))
            except Exception:
                pass
        if not combos:
            raise ManualReviewRequired(f"Could not locate ComboBox near {anchor_labels}")
        combos.sort(key=lambda pair: pair[0])
        combo = combos[0][1]
        try:
            combo.select(option)
        except Exception:
            combo.click_input()
            keyboard.send_keys(option, with_spaces=True)
            keyboard.send_keys("{ENTER}")
        actual = self._raw_value(combo)
        if norm(option) not in norm(actual):
            raise VerificationError(f"Nearby combo expected={option!r}, actual={actual!r}")

    def set_checkbox(self, labels: Sequence[str], checked: bool) -> None:
        ctrl = self.g.find_uia(labels, control_types=["CheckBox", "RadioButton"], exact=False)
        if ctrl is None:
            # Text label may be separate from the actual checkbox. Find nearest checkbox.
            boxes = self.g.visual_boxes(labels, exact=False)
            candidates = []
            if boxes:
                anchor = boxes[0]
                for candidate in self.g.descendants():
                    if norm(candidate.control_type) not in {"checkbox", "radiobutton"}:
                        continue
                    try:
                        rect = candidate.wrapper.rectangle()
                        cx, cy = (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2
                        candidates.append(
                            (((cx - anchor.cx) ** 2 + (cy - anchor.cy) ** 2) ** 0.5, candidate.wrapper)
                        )
                    except Exception:
                        pass
            if candidates:
                candidates.sort(key=lambda pair: pair[0])
                ctrl = candidates[0][1]
        if ctrl is None:
            raise ManualReviewRequired(f"Could not locate checkbox/radio for {list(labels)}")
        try:
            state = bool(ctrl.get_toggle_state())
        except Exception:
            state = None
        if state is None or state != checked:
            ctrl.click_input()
        try:
            state = bool(ctrl.get_toggle_state())
        except Exception:
            state = checked
        if state != checked:
            raise VerificationError(f"Could not set {labels[0]} checked={checked}")

    def checkbox_state(self, labels: Sequence[str]) -> bool | None:
        ctrl = self.g.find_uia(labels, control_types=["CheckBox", "RadioButton"], exact=False)
        if ctrl is None:
            return None
        try:
            return bool(ctrl.get_toggle_state())
        except Exception:
            return None


class FakturamaSession:
    def __init__(
        self,
        evidence_dir: str | Path,
        title_re: str = r"*Fakturama -",
    ):
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.title_re = title_re
        self.window: BaseWrapper | None = None
        self.grounder: Grounder | None = None

    def connect(self) -> "FakturamaSession":
        desktop = Desktop(backend="uia")
        candidates = desktop.windows(title_re=self.title_re, visible_only=True)
        if len(candidates) != 1:
            raise AutomationError(
                f"Expected exactly one visible Fakturama main window matching {self.title_re!r}; "
                f"found {len(candidates)}"
            )
        self.window = candidates[0]
        self.window.set_focus()
        self.grounder = Grounder(self.window, self.evidence_dir)
        return self

    def require(self) -> Grounder:
        if self.grounder is None:
            raise AutomationError("Fakturama session has not been connected")
        return self.grounder


def active_dialog(evidence_dir: Path, title_or_text: str, timeout: float = 10.0) -> Grounder:
    desktop = Desktop(backend="uia")
    needle = norm(title_or_text)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for window in desktop.windows(visible_only=True):
            try:
                title = norm(window.window_text())
                child_text = " ".join(norm(d.window_text()) for d in window.descendants())
                if needle in title or needle in child_text:
                    return Grounder(window, evidence_dir)
            except Exception:
                continue
        time.sleep(0.2)
    raise ManualReviewRequired(f"Timed out waiting for dialog containing {title_or_text!r}")
