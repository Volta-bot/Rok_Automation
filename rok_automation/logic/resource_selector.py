"""Choose resource searches from account identity and level priority."""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Iterator


class Resource(str, Enum):
    GOLD = "Gold"
    STONE = "Stone"
    FOOD = "Food"
    WOOD = "Wood"


ACCOUNT_RESOURCE: dict[str, Resource] = {
    "Vàng": Resource.GOLD,
    "Gold": Resource.GOLD,
    "Đá": Resource.STONE,
    "Stone": Resource.STONE,
    "Ngô": Resource.FOOD,
    "Food": Resource.FOOD,
    "Gỗ": Resource.WOOD,
    "Wood": Resource.WOOD,
}

MAX_RESOURCE_LEVEL = 8
MIN_RESOURCE_LEVEL = 5
RESOURCE_LEVELS = tuple(range(MAX_RESOURCE_LEVEL, MIN_RESOURCE_LEVEL - 1, -1))


@dataclass(frozen=True)
class SearchTarget:
    resource: Resource
    level: int


_OCR_RESOURCE_PATTERNS: tuple[tuple[re.Pattern[str], Resource], ...] = (
    (re.compile(r"(?<![a-z])(?:vang|gold)(?![a-z])"), Resource.GOLD),
    (re.compile(r"(?<![a-z])(?:da|d4|stone)(?![a-z])"), Resource.STONE),
    (re.compile(r"(?<![a-z])(?:ng(?:o|[06])|food)(?![a-z])"), Resource.FOOD),
    (
        re.compile(r"(?<![a-z])(?:g(?:o|[056789]{1,2})|wood)(?![a-z])"),
        Resource.WOOD,
    ),
)


def _fold_keyword_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    return (
        "".join(
            character
            for character in decomposed
            if not unicodedata.category(character).startswith("M")
        )
        .replace("đ", "d")
        .replace("ð", "d")
    )


def account_name_without_guild(label: str) -> str:
    """Return the account name from a city label, omitting its bracketed guild."""

    normalized_label = unicodedata.normalize("NFC", label)
    return re.sub(r"\[[^\]]*\]", " ", normalized_label).strip(" []|: \t\r\n")


def resource_for_account(account_name: str) -> Resource | None:
    """Find a Vietnamese or English resource keyword in the city label.

    Bracketed text is the guild name and is ignored. For example,
    ``[AC:U] Vàng8 hello`` maps to Gold. A digit may touch the keyword, while
    adjacent letters must form a word boundary so e.g. ``golden`` is ignored.
    Also tolerates common OCR substitutions such as ``G6``/``G0`` for ``Gỗ``
    and ``Ng0``/``Ng6`` for ``Ngô``.
    """

    folded_label = _fold_keyword_text(account_name_without_guild(account_name))

    for display_keyword, resource in ACCOUNT_RESOURCE.items():
        keyword = _fold_keyword_text(display_keyword)
        if re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", folded_label):
            return resource

    for pattern, resource in _OCR_RESOURCE_PATTERNS:
        if pattern.search(folded_label):
            return resource
    return None


def search_plan(
    preferred_resource: Resource,
    *,
    rng: random.Random | None = None,
) -> Iterator[SearchTarget]:
    """Yield one pass of searches, highest level first for each resource.

    The preferred resource is searched first at levels 8 through 5. The other
    resource types are shuffled once, then each is searched from level 8 down
    to level 5.
    An injected random generator makes the fallback order reproducible when
    needed by a caller.
    """

    fallback_resources = [
        resource for resource in Resource if resource is not preferred_resource
    ]
    (rng or random).shuffle(fallback_resources)

    for resource in (preferred_resource, *fallback_resources):
        for level in RESOURCE_LEVELS:
            yield SearchTarget(resource=resource, level=level)
