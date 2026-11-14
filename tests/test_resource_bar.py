"""Unit tests for the top-bar stockpile reader and lowest-stockpile choice."""

import unittest

from rok_automation.logic.resource_selector import (
    Resource,
    lowest_stockpile_resource,
)
from rok_automation.vision.resource_bar import parse_resource_amount


class TestParseResourceAmount(unittest.TestCase):
    def test_suffixed_amounts(self):
        self.assertEqual(parse_resource_amount("17.7M"), 17_700_000)
        self.assertEqual(parse_resource_amount("296.9M"), 296_900_000)
        self.assertEqual(parse_resource_amount("52.1M"), 52_100_000)
        self.assertEqual(parse_resource_amount("123.4K"), 123_400)
        self.assertEqual(parse_resource_amount("2.5B"), 2_500_000_000)

    def test_plain_amounts_use_thousands_separators(self):
        self.assertEqual(parse_resource_amount("13.219"), 13_219)
        self.assertEqual(parse_resource_amount("1.234.567"), 1_234_567)
        self.assertEqual(parse_resource_amount("842"), 842)

    def test_suffix_with_three_digit_group_is_thousands(self):
        self.assertEqual(parse_resource_amount("1.500K"), 1_500_000)

    def test_dropped_suffix_recovered_as_millions(self):
        self.assertEqual(parse_resource_amount("52.1"), 52_100_000)

    def test_embedded_text(self):
        self.assertEqual(parse_resource_amount("  Food 17.7M "), 17_700_000)

    def test_garbage_returns_none(self):
        self.assertIsNone(parse_resource_amount("Cấp"))
        self.assertIsNone(parse_resource_amount(""))
        self.assertIsNone(parse_resource_amount("M"))


class TestLowestStockpileResource(unittest.TestCase):
    def test_picks_lowest(self):
        amounts = {
            Resource.FOOD: 17_700_000,
            Resource.WOOD: 11_700_000,
            Resource.STONE: 296_900_000,
            Resource.GOLD: 52_100_000,
        }
        self.assertIs(lowest_stockpile_resource(amounts), Resource.WOOD)

    def test_unreadable_entries_are_ignored(self):
        amounts = {
            Resource.FOOD: None,
            Resource.WOOD: 5_000,
            Resource.STONE: 296_900_000,
            Resource.GOLD: 52_100_000,
        }
        self.assertIs(lowest_stockpile_resource(amounts), Resource.WOOD)

    def test_all_unreadable_returns_none(self):
        self.assertIsNone(lowest_stockpile_resource({r: None for r in Resource}))
        self.assertIsNone(lowest_stockpile_resource({}))

    def test_tie_prefers_gold(self):
        amounts = dict.fromkeys(Resource, 1_000)
        self.assertIs(lowest_stockpile_resource(amounts), Resource.GOLD)


if __name__ == "__main__":
    unittest.main()
