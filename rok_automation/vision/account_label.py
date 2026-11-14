"""Read the player's visible city label from a world-map screenshot."""

from __future__ import annotations

import logging
import re
from typing import Any

import cv2
from rok_automation.performance import timed

from rok_automation.logic.resource_selector import (
    Resource,
    account_name_without_guild,
    resource_for_account,
)

_OCR_ENGINE: Any | None = None


def _get_ocr_engine() -> Any:
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        try:
            from rapidocr import OCRVersion, RapidOCR
        except ImportError as exc:
            raise RuntimeError(
                "World-map OCR is unavailable. Install project dependencies with: "
                "python -m pip install -e ."
            ) from exc

        # RapidOCR defaults to INFO and emits a warning for normal empty OCR
        # crops. Silence its dedicated logger before constructing the engine;
        # the automation's own console messages remain unaffected.
        logging.getLogger("RapidOCR").disabled = True
        _OCR_ENGINE = RapidOCR(
            params={
                "Global.log_level": "critical",
                "Det.lang_type": "ch",
                "Det.ocr_version": OCRVersion.PPOCRV6,
                "Rec.lang_type": "vi",
                "Rec.ocr_version": OCRVersion.PPOCRV6,
            }
        )
    return _OCR_ENGINE


@timed("ocr")
def read_text_region(
    frame: Any,
    bounds: tuple[int, int, int, int],
    *,
    upscale: float = 2.0,
) -> tuple[str, ...]:
    """Read OCR lines from a selected screenshot region after enlarging it."""

    x, y, width, height = bounds
    frame_height, frame_width = frame.shape[:2]
    left = max(0, x)
    top = max(0, y)
    right = min(frame_width, x + width)
    bottom = min(frame_height, y + height)
    if right <= left or bottom <= top:
        return ()

    region = frame[top:bottom, left:right]
    if region.ndim == 2:
        region = cv2.cvtColor(region, cv2.COLOR_GRAY2BGR)
    enlarged = cv2.resize(
        region,
        None,
        fx=upscale,
        fy=upscale,
        interpolation=cv2.INTER_CUBIC,
    )
    result = _get_ocr_engine()(enlarged)
    return tuple(result.txts or ()) if result is not None else ()


@timed("ocr")
def read_text_detections(
    frame: Any,
    bounds: tuple[int, int, int, int],
    *,
    upscale: float = 2.0,
) -> tuple[tuple[str, tuple[int, int, int, int], float], ...]:
    """Read OCR strings with their boxes in full-frame coordinates."""

    x, y, width, height = bounds
    frame_height, frame_width = frame.shape[:2]
    left, top = max(0, x), max(0, y)
    right, bottom = min(frame_width, x + width), min(frame_height, y + height)
    if right <= left or bottom <= top:
        return ()

    region = frame[top:bottom, left:right]
    if region.ndim == 2:
        region = cv2.cvtColor(region, cv2.COLOR_GRAY2BGR)
    enlarged = cv2.resize(
        region,
        None,
        fx=upscale,
        fy=upscale,
        interpolation=cv2.INTER_CUBIC,
    )
    result = _get_ocr_engine()(enlarged)
    if result is None or result.txts is None or result.boxes is None:
        return ()

    detections = []
    scores = (
        result.scores
        if result.scores is not None
        else [0.0] * len(result.txts)
    )
    for text, box, score in zip(result.txts, result.boxes, scores):
        x1 = left + int(float(box[:, 0].min()) / upscale)
        y1 = top + int(float(box[:, 1].min()) / upscale)
        x2 = left + int(float(box[:, 0].max()) / upscale)
        y2 = top + int(float(box[:, 1].max()) / upscale)
        detections.append((text, (x1, y1, max(1, x2 - x1), max(1, y2 - y1)), float(score)))
    return tuple(detections)


def read_army_count(frame: Any) -> int | None:
    """Read the right-side march counter (for example, ``3/5``)."""

    frame_height, frame_width = frame.shape[:2]
    lines = read_text_region(
        frame,
        (
            round(frame_width * 0.80),
            round(frame_height * 0.13),
            round(frame_width * 0.20),
            round(frame_height * 0.24),
        ),
        upscale=3.0,
    )
    text = " ".join(lines).replace("／", "/").replace("|", "/")
    match = re.search(r"(?<!\d)([0-5])\s*/\s*5(?!\d)", text)
    return int(match.group(1)) if match else None


@timed("ocr")
def read_account_label(
    frame: Any,
) -> tuple[str | None, Resource | None]:
    """Return the centered account label and its keyword-selected resource.

    The player's city is centered on the world map. Search that area for an
    OCR line containing one of the supported resource keywords, then remove
    bracketed guild text from the displayed account name.
    """

    frame_height, frame_width = frame.shape[:2]
    left = round(frame_width * 0.10)
    right = round(frame_width * 0.90)
    top = round(frame_height * 0.10)
    bottom = round(frame_height * 0.84)
    map_crop = frame[top:bottom, left:right]
    if map_crop.size == 0:
        return None, None

    enlarged = cv2.resize(
        map_crop,
        None,
        fx=1.7,
        fy=1.7,
        interpolation=cv2.INTER_CUBIC,
    )
    result = _get_ocr_engine()(enlarged)
    if result is None or result.txts is None:
        return None, None

    candidates: list[tuple[float, str, Resource]] = []
    fallback_candidates: list[tuple[float, str]] = []
    boxes = result.boxes if result.boxes is not None else ()
    scores = result.scores if result.scores is not None else ()
    for text, confidence, box in zip(result.txts, scores, boxes):
        account_name = account_name_without_guild(text)
        if not account_name:
            continue

        center_x = left + float(box[:, 0].mean()) / 1.7
        center_y = top + float(box[:, 1].mean()) / 1.7
        normalized_x = center_x / frame_width
        normalized_y = center_y / frame_height
        # Own-city labels are close to the camera's map center. The mild
        # location weight breaks ties when another city label is also visible.
        distance = abs(normalized_x - 0.5) + abs(normalized_y - 0.42)
        rank = float(confidence) - 0.20 * distance

        resource = resource_for_account(text)
        if resource is not None:
            candidates.append((rank, account_name, resource))
        elif (
            len(account_name) >= 3
            and re.search(r"[A-Za-zÀ-ỹ]", account_name)
            and 0.30 <= normalized_x <= 0.72
            and 0.30 <= normalized_y <= 0.65
        ):
            has_guild_tag = "[" in text or "]" in text
            fallback_rank = rank + (0.25 if has_guild_tag else 0.0) - 0.40 * distance
            fallback_candidates.append((fallback_rank, account_name))

    if candidates:
        _rank, account_name, resource = max(candidates, key=lambda item: item[0])
        return account_name, resource
    if fallback_candidates:
        _rank, account_name = max(fallback_candidates, key=lambda item: item[0])
        return account_name, None
    return None, None
