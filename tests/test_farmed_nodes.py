"""Unit tests for the persistent farmed-node memory."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.game.farmed_nodes import (
    DEFAULT_MEMORY_WINDOW_SECONDS,
    load_recent_farmed_nodes,
    remember_farmed_node,
)


class TestFarmedNodes(unittest.TestCase):
    """Nodes are remembered per account and only while recent."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "farmed_nodes.json"
        self.addCleanup(self.tmp.cleanup)

    def test_a_remembered_node_is_loaded(self) -> None:
        remember_farmed_node("acc", (1038, 647), now=1000.0, path=self.path)
        recent = load_recent_farmed_nodes("acc", now=1060.0, path=self.path)
        self.assertIn((1038, 647), recent)

    def test_an_old_node_is_forgotten(self) -> None:
        remember_farmed_node("acc", (1038, 647), now=1000.0, path=self.path)
        recent = load_recent_farmed_nodes(
            "acc",
            window=30.0,
            now=1000.0 + DEFAULT_MEMORY_WINDOW_SECONDS + 1,
            path=self.path,
        )
        self.assertEqual(recent, {})

    def test_accounts_do_not_share_nodes(self) -> None:
        # Different RoK accounts look at different parts of the world.
        remember_farmed_node("acc A", (1038, 647), now=1000.0, path=self.path)
        self.assertEqual(
            load_recent_farmed_nodes("acc B", now=1010.0, path=self.path),
            {},
        )

    def test_the_file_only_keeps_live_entries(self) -> None:
        remember_farmed_node("acc", (1, 2), now=1000.0, path=self.path)
        remember_farmed_node("acc", (3, 4), now=2000.0, path=self.path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(data["acc"]), {"1,2", "3,4"})

    def test_a_broken_file_is_tolerated(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("not json", encoding="utf-8")
        self.assertEqual(
            load_recent_farmed_nodes("acc", now=1000.0, path=self.path),
            {},
        )
        remember_farmed_node("acc", (5, 6), now=1000.0, path=self.path)
        self.assertIn((5, 6), load_recent_farmed_nodes("acc", now=1001.0, path=self.path))

    def test_a_missing_file_is_not_an_error(self) -> None:
        self.assertEqual(
            load_recent_farmed_nodes("acc", now=1000.0, path=self.path),
            {},
        )


def _controller(account_name: str) -> FarmingController:
    """Build a controller without touching the screen."""

    with patch.object(FarmingController, "__init__", lambda self, *a, **k: None):
        controller = FarmingController(MagicMock())
    controller.context = MagicMock()
    controller.context.account_name = account_name
    controller._log = lambda *_args, **_kwargs: None
    return controller


class TestControllerNodeMemory(unittest.TestCase):
    """The controller persists farmed nodes under the account's name."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "farmed_nodes.json"
        self.addCleanup(self.tmp.cleanup)

    def test_a_farmed_node_is_remembered_for_the_account(self) -> None:
        with patch(
            "rok_automation.automation.controller.remember_farmed_node"
        ) as remember:
            _controller("acc")._remember_farmed_node((1038, 647))

        remember.assert_called_once_with("acc", (1038, 647))

    def test_nothing_is_remembered_without_coordinates(self) -> None:
        with patch(
            "rok_automation.automation.controller.remember_farmed_node"
        ) as remember:
            _controller("acc")._remember_farmed_node(None)

        remember.assert_not_called()

    def test_nothing_is_remembered_without_an_account(self) -> None:
        controller = _controller(None)
        with patch(
            "rok_automation.automation.controller.remember_farmed_node"
        ) as remember:
            controller._remember_farmed_node((1038, 647))

        remember.assert_not_called()


if __name__ == "__main__":
    unittest.main()
