"""Unit tests for resource node yields and run total calculations."""

import unittest

from rok_automation.automation.context import FarmContext
from rok_automation.logic.resource_selector import (
    Resource,
    SearchTarget,
    calculate_farmed_resources,
    format_resource_amount,
    resource_node_yield,
)


class TestResourceNodeYields(unittest.TestCase):
    def test_food_and_wood_yields(self):
        for res in (Resource.FOOD, Resource.WOOD):
            self.assertEqual(resource_node_yield(res, 8), 1_575_000)
            self.assertEqual(resource_node_yield(res, 7), 1_417_500)
            self.assertEqual(resource_node_yield(res, 6), 1_260_000)
            self.assertEqual(resource_node_yield(res, 5), 945_000)

    def test_stone_yields(self):
        self.assertEqual(resource_node_yield(Resource.STONE, 8), 1_181_250)
        self.assertEqual(resource_node_yield(Resource.STONE, 7), 1_063_125)
        self.assertEqual(resource_node_yield(Resource.STONE, 6), 945_000)
        self.assertEqual(resource_node_yield(Resource.STONE, 5), 708_750)

    def test_gold_yields(self):
        self.assertEqual(resource_node_yield(Resource.GOLD, 8), 525_000)
        self.assertEqual(resource_node_yield(Resource.GOLD, 7), 472_000)
        self.assertEqual(resource_node_yield(Resource.GOLD, 6), 420_000)
        self.assertEqual(resource_node_yield(Resource.GOLD, 5), 336_000)

    def test_calculate_totals(self):
        targets = [
            SearchTarget(Resource.FOOD, 8),   # 1,575,000
            SearchTarget(Resource.FOOD, 7),   # 1,417,500
            SearchTarget(Resource.WOOD, 6),   # 1,260,000
            SearchTarget(Resource.STONE, 8),  # 1,181,250
            SearchTarget(Resource.GOLD, 8),   #   525,000
            SearchTarget(Resource.GOLD, 5),   #   336,000
        ]
        totals = calculate_farmed_resources(targets)
        self.assertEqual(totals[Resource.FOOD], 1_575_000 + 1_417_500)
        self.assertEqual(totals[Resource.WOOD], 1_260_000)
        self.assertEqual(totals[Resource.STONE], 1_181_250)
        self.assertEqual(totals[Resource.GOLD], 525_000 + 336_000)
        grand_total = sum(totals.values())
        self.assertEqual(grand_total, 6_294_750)

    def test_farm_context_properties(self):
        ctx = FarmContext()
        ctx.dispatched_targets = [
            SearchTarget(Resource.GOLD, 8),  # 525,000
            SearchTarget(Resource.GOLD, 7),  # 472,000
        ]
        self.assertEqual(ctx.total_farmed_resources[Resource.GOLD], 997_000)
        self.assertEqual(ctx.grand_total_farmed, 997_000)

    def test_formatting(self):
        self.assertEqual(format_resource_amount(1_575_000), "1,575,000 (1.58M)")
        self.assertEqual(format_resource_amount(945_000), "945,000 (945.0k)")
        self.assertEqual(format_resource_amount(500), "500")


if __name__ == "__main__":
    unittest.main()
