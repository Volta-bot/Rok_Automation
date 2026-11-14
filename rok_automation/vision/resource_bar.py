"""Read the resource stockpile amounts from the game's top bar."""

from __future__ import annotations

import re
from typing import Any

from rok_automation.logic.resource_selector import Resource
from rok_automation.vision.account_label import read_text_region

# The top bar shows the four gatherable resources in a fixed order
# (food, wood, stone, gold) between the gem counter and the ruby/+ area.
# The game UI is a fixed overlay: each amount starts about 0.025 of the
# frame width right of its icon and grows rightward with its digit count
# ("173.9M" spans ~0.10). Crops are ratios of the frame, calibrated
# against live 1280x720 captures (city and world map), with slack so both
# short and long amounts fit inside their own crop. Because each amount
# is the leftmost text in its crop, the first readable line belongs to
# that resource; a neighbour's tail can only appear after it.
_AMOUNT_TOP = 0.0
_AMOUNT_HEIGHT = 0.085
_RESOURCE_CROPS: dict[Resource, tuple[float, float]] = {
    # resource: (left ratio, width ratio) of its amount label
    Resource.FOOD: (0.525, 0.090),
    Resource.WOOD: (0.615, 0.090),
    Resource.STONE: (0.718, 0.090),
    Resource.GOLD: (0.805, 0.107),
}

_AMOUNT_PATTERN = re.compile(r"(\d[\d .,]{0,15})\s*([KMB])?", re.IGNORECASE)
_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_resource_amount(text: str) -> int | None:
    """Parse a top-bar amount such as ``17.7M`` or ``13.219``.

    The game displays large amounts with a ``K``/``M``/``B`` suffix and a
    decimal separator (``296.9M``), and small amounts with Vietnamese
    thousands separators (``13.219``). With a suffix, only a final 1-2
    digit group counts as decimals, so ``1.500K`` stays ``1_500_000``.
    """

    match = _AMOUNT_PATTERN.search(text)
    if match is None:
        return None
    compact = match.group(1).replace(" ", "")
    suffix = match.group(2).upper() if match.group(2) else None

    if suffix:
        decimals = re.fullmatch(r"(\d+)(?:[.,](\d{1,2}))?", compact)
        if decimals is not None:
            value = float(decimals.group(1))
            if decimals.group(2):
                value += float(decimals.group(2)) / 10 ** len(decimals.group(2))
        else:
            value = float(re.sub(r"[.,]", "", compact))
        return int(value * _MULTIPLIERS[suffix])

    plain = re.sub(r"[.,]", "", compact)
    if not plain.isdigit():
        return None
    # A bare value with a 1-2 digit group after a separator is never a
    # valid plain amount in this UI, so the OCR most likely dropped the
    # ``M`` suffix ("52.1M" read as "52.1").
    if re.fullmatch(r"\d+[.,]\d{1,2}", compact):
        return int(float(compact.replace(",", ".")) * 1_000_000)
    return int(plain)


def read_resource_bar(frame: Any) -> dict[Resource, int | None]:
    """OCR each top-bar amount label and return the stockpiles.

    Resources whose label is unreadable map to ``None`` so callers can
    ignore them instead of mistaking them for zero.
    """

    amounts: dict[Resource, int | None] = {
        resource: None for resource in Resource
    }
    frame_height, frame_width = frame.shape[:2]
    for resource, (left_ratio, width_ratio) in _RESOURCE_CROPS.items():
        bounds = (
            round(frame_width * left_ratio),
            round(frame_height * _AMOUNT_TOP),
            round(frame_width * width_ratio),
            round(frame_height * _AMOUNT_HEIGHT),
        )
        for line in read_text_region(frame, bounds, upscale=3.0):
            amount = parse_resource_amount(line)
            if amount is not None:
                amounts[resource] = amount
                break
    return amounts
