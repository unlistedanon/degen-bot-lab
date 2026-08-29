from __future__ import annotations

import ast
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from degenbotlab import __main__ as public_cli
from degenbotlab import analysis_cli, research, research_cli


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "degenbotlab"


class PublicSourceBoundaryTests(unittest.TestCase):
    def test_no_transaction_function_or_post_call_exists(self) -> None:
        forbidden_functions = {
            "trade",
            "buy",
            "sell",
            "swap",
            "execute_trade",
            "submit_transaction",
            "send_transaction",
        }
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertNotIn(node.name.lower(), forbidden_functions, str(path))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotEqual(node.func.attr.lower(), "post", str(path))

    def test_no_wallet_or_chain_execution_dependency_is_imported(self) -> None:
        forbidden_roots = {
            "anchorpy",
            "base58",
            "solana",
            "solders",
            "web3",
        }
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertTrue(imported.isdisjoint(forbidden_roots), (path, imported))

    def test_no_authenticated_trade_endpoint_literal_exists(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        ).lower()
        self.assertNotIn("/api/trade", combined)
        self.assertNotIn("sendtransaction", combined)
        self.assertNotIn("keypair", combined)


class PublicCliBoundaryTests(unittest.TestCase):
    def test_help_and_unknown_command_are_deterministic(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(public_cli.main(["--help"]), 0)
        self.assertIn("Live trading is not included", stdout.getvalue())

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(public_cli.main(["unknown"]), 2)
        self.assertEqual(
            stderr.getvalue(),
            "DEGEN_BOT_LAB_REFUSED unknown command: unknown\n",
        )

    def test_no_environment_combination_can_turn_live_mode_on(self) -> None:
        for live_value in (None, "1", "true", "yes", ""):
            environment = {"BOT_RESEARCH_ENABLE": "1"}
            if live_value is not None:
                environment["BOT_LIVE"] = live_value
            with self.subTest(live_value=live_value), patch.dict(
                os.environ,
                environment,
                clear=True,
            ):
                with self.assertRaises(research.ResearchSafetyError):
                    research_cli._enforce_dry_boundary("record")
                with self.assertRaises(Exception):
                    analysis_cli._enforce_offline_boundary()

    def test_only_exact_zero_passes_live_guard_and_api_key_is_discarded(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BOT_RESEARCH_ENABLE": "1",
                "BOT_LIVE": "0",
                "PUMPPORTAL_API_KEY": "test-value-that-must-disappear",
            },
            clear=True,
        ):
            research_cli._enforce_dry_boundary("record")
            self.assertEqual(os.environ["BOT_LIVE"], "0")
            self.assertNotIn("PUMPPORTAL_API_KEY", os.environ)


if __name__ == "__main__":
    unittest.main()
