from __future__ import annotations

import unittest

from degenbotlab.strategies import bottotrot as strategy


def observation(
    market_cap: float,
    holders: int,
    transactions: int,
    buys: int,
    sells: int,
    *,
    top10: float = 20,
    dev: float = 2,
    volume_5: float = 0,
    volume_15: float = 0,
) -> dict:
    return {
        "mc": market_cap,
        "holders": holders,
        "txc": transactions,
        "bc": buys,
        "sc": sells,
        "top10": top10,
        "dev_hold": dev,
        "volume_5": volume_5,
        "volume_15": volume_15,
    }


class FrozenDefaultsTests(unittest.TestCase):
    def test_v11_defaults_and_add_floor_are_frozen(self) -> None:
        self.assertEqual(strategy.PROBE_SCORE, 72.0)
        self.assertEqual(strategy.ADD_SCORE, 82.0)
        self.assertEqual(strategy.HARD_STOP, -0.18)
        self.assertEqual(strategy.RUNNER_AT_GROSS, 0.25)
        self.assertEqual(strategy.PARABOLIC_AT_GROSS, 1.0)
        self.assertEqual(strategy.MOON_AT_GROSS, 3.0)
        self.assertEqual(strategy.RUNNER_TRAIL, 0.18)
        self.assertEqual(strategy.PARABOLIC_TRAIL, 0.22)
        self.assertEqual(strategy.MOON_TRAIL, 0.20)
        self.assertEqual(strategy.ADD_MIN_GROSS, 0.25)

    def test_strategy_module_has_no_execution_or_network_surface(self) -> None:
        for forbidden in (
            "trade",
            "buy",
            "sell",
            "submit_transaction",
            "wallet",
            "requests",
            "websocket",
            "API_KEY",
            "TRADE_URL",
        ):
            self.assertFalse(hasattr(strategy, forbidden), forbidden)


class ScoringAndEntryTests(unittest.TestCase):
    def test_strong_runner_scores_and_passes(self) -> None:
        history = [
            observation(100, 10, 10, 6, 4, dev=1, volume_5=10, volume_15=20),
            observation(108, 12, 18, 12, 6, dev=1, volume_5=15, volume_15=25),
            observation(120, 16, 31, 23, 8, dev=1, volume_5=25, volume_15=40),
            observation(138, 22, 50, 40, 10, dev=1, volume_5=40, volume_15=55),
        ]
        score, features = strategy.score_history(history)
        self.assertGreaterEqual(score, strategy.PROBE_SCORE)
        self.assertIsNone(strategy.entry_rejection(score, features, history[-1]))

    def test_concentration_guard_rejects(self) -> None:
        history = [
            observation(100, 10, 10, 6, 4, top10=60, dev=12),
            observation(110, 12, 18, 12, 6, top10=60, dev=12),
            observation(125, 16, 31, 23, 8, top10=60, dev=12),
            observation(145, 22, 50, 40, 10, top10=60, dev=12),
        ]
        score, features = strategy.score_history(history)
        self.assertIn(
            strategy.entry_rejection(score, features, history[-1]),
            {"top10_concentration", "dev_concentration"},
        )

    def test_exact_one_concentration_is_unknown(self) -> None:
        self.assertIsNone(strategy.pct_value(1))
        self.assertEqual(strategy.pct_value(0.2), 20.0)
        self.assertEqual(strategy.pct_value(12), 12.0)

    def test_too_short_history_is_empty_score(self) -> None:
        self.assertEqual(strategy.score_history([observation(100, 1, 1, 1, 0)]), (0.0, {}))

    def test_snap_retains_only_the_frozen_public_fields(self) -> None:
        raw = {
            "m": "mint-a",
            "n": "Alpha",
            "t": "ALP",
            "c": "solana:mainnet",
            "pg": "pump",
            "mc": 100,
            "p": 99,
            "credential": "must-not-propagate",
        }
        normalized = strategy.snap(raw)
        self.assertEqual(normalized["mint"], "mint-a")
        self.assertEqual(normalized["mc"], 100)
        self.assertNotIn("credential", normalized)


class PositionCalculationTests(unittest.TestCase):
    def test_harmonic_signal_basis_for_adds(self) -> None:
        invested, denom, basis = strategy.add_basis(1.0, 1.0 / 100.0, 1.0, 200.0)
        self.assertAlmostEqual(invested, 2.0)
        self.assertAlmostEqual(denom, 0.015)
        self.assertAlmostEqual(basis, 133.3333333333, places=6)

    def test_stage_progression_and_no_regression(self) -> None:
        self.assertEqual(strategy.stage_from_peak(0.10), "PROVING")
        self.assertEqual(strategy.stage_from_peak(0.30), "RUNNER")
        self.assertEqual(strategy.stage_from_peak(1.20), "PARABOLIC")
        self.assertEqual(strategy.stage_from_peak(3.20), "MOON")
        self.assertEqual(strategy.promoted_stage("RUNNER", 0.15), "RUNNER")
        self.assertEqual(strategy.promoted_stage("PARABOLIC", 0.40), "PARABOLIC")

    def test_movers_hard_stop_and_runner_trail(self) -> None:
        position = {"basis_mc": 100.0, "peak_mc": 100.0, "thesis_bad_polls": 0}
        self.assertEqual(strategy.exit_reason(position, 80.0, 80.0, 5.0), "hard_stop")
        runner = {"basis_mc": 100.0, "peak_mc": 160.0, "thesis_bad_polls": 0}
        self.assertEqual(
            strategy.exit_reason(runner, 110.0, 80.0, 20.0),
            "runner_trail",
        )

    def test_thesis_failure_uses_frozen_hold_and_bad_poll_rules(self) -> None:
        position = {"basis_mc": 100.0, "peak_mc": 100.0, "thesis_bad_polls": 3}
        self.assertIsNone(strategy.exit_reason(position, 100.0, 20.0, 9.0))
        self.assertEqual(
            strategy.exit_reason(position, 100.0, 20.0, 10.0),
            "thesis_failure",
        )

    def test_fast_shadow_price_exits(self) -> None:
        self.assertEqual(
            strategy.fast_price_exit_reason("PROVING", -0.181, 0.02),
            "fast_hard_stop",
        )
        self.assertEqual(
            strategy.fast_price_exit_reason("RUNNER", 0.10, 0.40),
            "fast_runner_trail",
        )
        self.assertIsNone(
            strategy.fast_price_exit_reason("RUNNER", 0.20, 0.30)
        )
        self.assertEqual(
            strategy.fast_price_exit_reason("RUNNER", 0.04, 0.30),
            "fast_profit_floor",
        )


class BondDefinitionTests(unittest.TestCase):
    def test_progress_cross_is_bond(self) -> None:
        self.assertEqual(
            strategy.bond_reason(
                {"progress": 99, "launchpad": "pump"},
                {"progress": 100, "launchpad": "pump"},
            ),
            "progress_cross",
        )

    def test_qualifying_venue_flip_is_bond(self) -> None:
        self.assertEqual(
            strategy.bond_reason(
                {"progress": 97, "launchpad": "pump"},
                {"progress": None, "launchpad": "raydium"},
            ),
            "venue_flip",
        )

    def test_other_changes_are_not_bonds(self) -> None:
        self.assertIsNone(
            strategy.bond_reason(
                {"progress": 94, "launchpad": "pump"},
                {"progress": 95, "launchpad": "raydium"},
            )
        )
        self.assertEqual(strategy.OPPORTUNITY_MILESTONES, (0.25, 0.50, 1.00, 3.00))


if __name__ == "__main__":
    unittest.main()
