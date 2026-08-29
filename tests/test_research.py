from __future__ import annotations

import io
import json
import os
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from degenbotlab import research
from degenbotlab import research_cli
from degenbotlab.strategies import bottotrot as strategy


def _config() -> research.StrategyConfig:
    return research.StrategyConfig(
        poll_seconds=2.0,
        probe_sol=0.01,
        add_sol=0.01,
        max_position_sol=0.03,
        max_adds=0,
        max_trades=0,
        probe_score=72.0,
        add_score=82.0,
        watch_score=55.0,
        min_observations=3,
        probe_confirm_polls=1,
        add_confirm_polls=2,
        add_min_gross=0.25,
        hard_stop=-0.18,
        runner_at_gross=0.25,
        parabolic_at_gross=1.0,
        moon_at_gross=3.0,
        runner_trail=0.18,
        parabolic_trail=0.22,
        moon_trail=0.20,
        runner_floor=0.05,
        thesis_exit_score=38.0,
        thesis_bad_polls=3,
        min_thesis_hold_seconds=10.0,
        derisk_at_gross=1.0,
        derisk_sell_pct=25.0,
        missing_exit_polls=15,
        history_points=12,
        post_exit_track_seconds=3600.0,
        min_post_bond_seconds=0.0,
        probe_confirm_seconds=0.0,
        add_confirm_seconds=2.0,
    )


def _bindings() -> research.StrategyBindings:
    def score_history(history):
        return 90.0, {
            "mc_1": 0.1,
            "buy_delta": 4.0,
            "buy_ratio": 0.8,
            "holder_delta": 2.0,
        }

    def exit_reason(position, current_mc, _score, _held):
        gross = current_mc / float(position["basis_mc"]) - 1.0
        return "hard_stop" if gross <= -0.18 else None

    def promoted_stage(stage, peak_gross):
        if stage != "PROVING":
            return stage
        return "RUNNER" if peak_gross >= 0.25 else "PROVING"

    def add_basis(invested, denom, add_sol, add_mc):
        new_invested = invested + add_sol
        new_denom = denom + add_sol / add_mc
        return new_invested, new_denom, new_invested / new_denom

    def fast_exit(stage, gross, _peak):
        return "fast_hard_stop" if gross <= -0.18 else None

    def bond_reason(previous, current):
        if previous.get("progress", 0) < 100 <= current.get("progress", 0):
            return "progress_cross"
        return None

    return research.StrategyBindings(
        score_history=score_history,
        entry_rejection=lambda _score, _features, _current: None,
        exit_reason=exit_reason,
        promoted_stage=promoted_stage,
        add_basis=add_basis,
        fast_price_exit_reason=fast_exit,
        bond_reason=bond_reason,
        opportunity_milestones=(0.25, 0.50, 1.00, 3.00),
    )


def _snapshot(mc: float, progress: float) -> dict:
    return {
        "mint": "mint-a",
        "name": "A",
        "symbol": "A",
        "chain": "solana:mainnet",
        "launchpad": "pump",
        "holders": 10,
        "txc": 10,
        "bc": 8,
        "sc": 2,
        "mc": mc,
        "progress": progress,
        "top10": 20,
        "dev_hold": 2,
    }


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class SafeJsonlWriterTests(unittest.TestCase):
    def test_rejects_sensitive_fields_and_refuses_implicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "research.jsonl"
            writer = research.SafeJsonlWriter(path, 4096)
            with self.assertRaises(research.ResearchSafetyError) as key_error:
                writer.write({"type": "bad", "api_key_do-not-log": "x"})
            self.assertNotIn("do-not-log", str(key_error.exception))
            with self.assertRaises(research.ResearchSafetyError) as value_error:
                writer.write({"type": "bad", "message": "Bearer do-not-log"})
            self.assertNotIn("do-not-log", str(value_error.exception))
            writer.write({"type": "ok", "mint": "public-mint"})
            writer.close()
            with self.assertRaises(research.ResearchSafetyError):
                research.SafeJsonlWriter(path, 4096)

    def test_requires_log_cap_plus_reserved_free_disk(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "no-space.jsonl"
            with patch(
                "degenbotlab.research.shutil.disk_usage",
                return_value=SimpleNamespace(free=research.MIN_FREE_DISK_BYTES),
            ):
                with self.assertRaises(research.ResearchSafetyError):
                    research.SafeJsonlWriter(path, 4096)
            self.assertFalse(path.exists())

    def test_stops_at_the_bounded_log_cap(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            writer = research.SafeJsonlWriter(Path(folder) / "small.jsonl", 100)
            with self.assertRaises(research.ResearchSafetyError):
                writer.write({"type": "large", "payload": "x" * 200})
            writer.close()


class ResearchEngineTests(unittest.TestCase):
    def _run(self, path: Path) -> research.ResearchEngine:
        with research.SafeJsonlWriter(path, 1024 * 1024) as writer:
            engine = research.ResearchEngine(_bindings(), _config(), writer)
            for when, snapshot in [
                (1000.0, _snapshot(100, 99)),
                (1002.0, _snapshot(100, 100)),
                (1004.0, _snapshot(110, 100)),
                (1006.0, _snapshot(130, 100)),
                (1008.0, _snapshot(100, 100)),
            ]:
                engine.ingest_snapshot([snapshot], when)
        return engine

    def test_records_bond_opportunity_and_only_simulated_actions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recording.jsonl"
            engine = self._run(path)
            events = _events(path)
            decisions = [e for e in events if e.get("type") == "strategy_decision"]
            self.assertEqual(engine.completed, 1)
            self.assertTrue(
                any(e.get("phase") == "bond_gate" and e.get("decision") == "OPEN" for e in decisions)
            )
            milestone = next(e for e in decisions if e.get("phase") == "missed_opportunity")
            self.assertEqual(milestone["bond_mc"], 100.0)
            self.assertEqual(milestone["milestone"], 0.25)
            actions = {e.get("decision") for e in decisions}
            self.assertIn("SIMULATED_PROBE", actions)
            self.assertIn("SIMULATED_CLOSE", actions)
            self.assertFalse(any("sell_result" in e or "buy_result" in e for e in events))

    def test_same_snapshots_have_identical_decision_digest(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first = self._run(Path(folder) / "first.jsonl")
            second = self._run(Path(folder) / "second.jsonl")
            self.assertEqual(first.decision_count, second.decision_count)
            self.assertEqual(first.decision_hash, second.decision_hash)


class ReplayIntegrationTests(unittest.TestCase):
    def _recording(
        self, path: Path
    ) -> tuple[research.StrategyConfig, research.StrategyBindings, dict]:
        config = _config()
        bindings = _bindings()
        fingerprint = research.strategy_fingerprint(
            config,
            (Path(strategy.__file__),),
        )
        metadata = {
            "branch": "test-fixture",
            "commit": "0" * 40,
            "strategy": strategy.STRATEGY_ID,
            "strategy_config": asdict(config),
            "strategy_fingerprint": fingerprint,
            "execution": "simulated_only",
            "transactions": "blocked",
        }
        with research.SafeJsonlWriter(path, 1024 * 1024) as writer:
            writer.write(
                {
                    "type": "session",
                    "mode": "record",
                    "started_at": research.iso_from_unix(1000.0),
                    **metadata,
                }
            )
            engine = research.ResearchEngine(bindings, config, writer)
            for when, snapshot in (
                (1000.0, _snapshot(100, 99)),
                (1002.0, _snapshot(100, 100)),
                (1004.0, _snapshot(110, 100)),
                (1006.0, _snapshot(130, 100)),
                (1008.0, _snapshot(100, 100)),
            ):
                engine.ingest_snapshot(
                    [snapshot],
                    when,
                    observed_monotonic=when,
                )
            writer.write(
                {
                    "type": "summary",
                    "mode": "record",
                    "finished_at": research.iso_from_unix(1008.0),
                    "stop_reason": "snapshot_limit_reached",
                    "snapshots": 5,
                    "decisions": engine.decision_count,
                    "decision_digest": engine.decision_hash,
                    "simulated_completed_positions": engine.completed,
                    "open_simulated_position": engine.position,
                    "transactions_submitted": 0,
                }
            )
        return config, bindings, metadata

    def test_complete_recording_replays_with_matching_digest_offline(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "recording.jsonl"
            output = Path(folder) / "replay.jsonl"
            config, bindings, metadata = self._recording(source)
            args = research_cli._parser().parse_args(
                [
                    "replay",
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--max-log-mb",
                    "1",
                ]
            )
            with (
                patch.object(
                    research_cli,
                    "_load_strategy",
                    return_value=(config, bindings),
                ),
                patch.object(research_cli, "_metadata", return_value=metadata),
                research_cli.ReplayNetworkDeny() as guard,
            ):
                result = research_cli._run_replay(args, Path.cwd())
            self.assertEqual(result, 0)
            self.assertEqual(guard.blocked_attempts, 0)
            replay_events = _events(output)
            summary = replay_events[-1]
            self.assertTrue(summary["deterministic_match"])
            self.assertEqual(summary["network_calls"], 0)
            self.assertEqual(summary["transactions_submitted"], 0)

    def test_replay_rejects_incomplete_or_nonzero_transaction_summary(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "recording.jsonl"
            config, bindings, metadata = self._recording(source)
            lines = source.read_text(encoding="utf-8").splitlines()
            cases = {
                "missing-summary.jsonl": lines[:-1],
                "nonzero-summary.jsonl": [
                    *lines[:-1],
                    json.dumps(
                        {
                            **json.loads(lines[-1]),
                            "transactions_submitted": 1,
                        },
                        separators=(",", ":"),
                    ),
                ],
            }
            for name, case_lines in cases.items():
                with self.subTest(name=name):
                    case_path = Path(folder) / name
                    case_path.write_text("\n".join(case_lines) + "\n", encoding="utf-8")
                    args = research_cli._parser().parse_args(
                        [
                            "replay",
                            "--input",
                            str(case_path),
                            "--output",
                            str(Path(folder) / f"{name}.out"),
                        ]
                    )
                    with (
                        patch.object(
                            research_cli,
                            "_load_strategy",
                            return_value=(config, bindings),
                        ),
                        patch.object(
                            research_cli,
                            "_metadata",
                            return_value=metadata,
                        ),
                        self.assertRaises(research.ReplayFormatError),
                    ):
                        research_cli._run_replay(args, Path.cwd())

    def test_replay_reader_rejects_blank_duplicate_and_nonfinite_json(self) -> None:
        invalid = {
            "blank.jsonl": "\n",
            "duplicate.jsonl": (
                '{"schema":"bottotrot.research.v1",'
                '"schema":"bottotrot.research.v1","event_index":1}\n'
            ),
            "nonfinite.jsonl": (
                '{"schema":"bottotrot.research.v1",'
                '"event_index":1,"value":NaN}\n'
            ),
        }
        with tempfile.TemporaryDirectory() as folder:
            for name, payload in invalid.items():
                with self.subTest(name=name):
                    path = Path(folder) / name
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaises(research.ReplayFormatError):
                        list(research.read_jsonl(path))

    def test_replay_rejects_timestamp_string_unix_disagreement(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "recording.jsonl"
            config, bindings, metadata = self._recording(source)
            events = _events(source)
            snapshot = next(event for event in events if event["type"] == "movers_snapshot")
            snapshot["observed_at"] = research.iso_from_unix(
                float(snapshot["observed_unix"]) + 1.0
            )
            source.write_text(
                "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events),
                encoding="utf-8",
            )
            args = research_cli._parser().parse_args(
                [
                    "replay",
                    "--input",
                    str(source),
                    "--output",
                    str(Path(folder) / "out.jsonl"),
                ]
            )
            with (
                patch.object(
                    research_cli,
                    "_load_strategy",
                    return_value=(config, bindings),
                ),
                patch.object(research_cli, "_metadata", return_value=metadata),
                self.assertRaises(research.ReplayFormatError),
            ):
                research_cli._run_replay(args, Path.cwd())


class ExistingStrategyBoundaryTests(unittest.TestCase):
    def test_unexpected_failure_does_not_echo_exception_text(self) -> None:
        stderr = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {"BOT_RESEARCH_ENABLE": "1", "BOT_LIVE": "0"},
                clear=True,
            ),
            patch.object(
                research_cli,
                "_git_value",
                return_value="test-fixture",
            ),
            patch.object(
                research_cli,
                "_run_observer",
                side_effect=RuntimeError("do-not-print"),
            ),
            redirect_stderr(stderr),
        ):
            result = research_cli.main(["record", "--output", "unused.jsonl"])
        self.assertEqual(result, 2)
        self.assertNotIn("do-not-print", stderr.getvalue())
        self.assertIn("RuntimeError", stderr.getvalue())

    def test_replay_process_wide_socket_boundary_blocks_and_restores(self) -> None:
        original_socket = socket.socket
        original_create_connection = socket.create_connection
        guard = research_cli.ReplayNetworkDeny()
        with guard:
            denied = socket.socket()
            try:
                with self.assertRaises(research.ResearchSafetyError):
                    denied.connect(("127.0.0.1", 9))
            finally:
                denied.close()
            with self.assertRaises(research.ResearchSafetyError):
                socket.create_connection(("127.0.0.1", 9))
            with self.assertRaises(research.ResearchSafetyError):
                socket.getaddrinfo("example.invalid", 443)
            import requests

            attempts_before_session = guard.blocked_attempts
            session = requests.Session()
            session.trust_env = False
            with self.assertRaises(Exception):
                session.get("http://127.0.0.1:9", timeout=0.1)
            session.close()
            self.assertGreater(guard.blocked_attempts, attempts_before_session)
        self.assertIs(socket.socket, original_socket)
        self.assertIs(socket.create_connection, original_create_connection)

    def test_dry_boundary_rejects_live_flags_and_strips_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BOT_RESEARCH_ENABLE": "1",
                "BOT_LIVE": "1",
                "BOT_FAST_EXIT": "0",
            },
            clear=True,
        ):
            with self.assertRaises(research.ResearchSafetyError):
                research_cli._enforce_dry_boundary("shadow")
        with patch.dict(
            os.environ,
            {
                "BOT_RESEARCH_ENABLE": "1",
                "BOT_LIVE": "0",
                "BOT_FAST_EXIT": "1",
            },
            clear=True,
        ):
            with self.assertRaises(research.ResearchSafetyError):
                research_cli._enforce_dry_boundary("shadow")
        with patch.dict(
            os.environ,
            {
                "BOT_RESEARCH_ENABLE": "1",
                "BOT_LIVE": "0",
                "BOT_FAST_EXIT": "0",
                "PUMPPORTAL_API_KEY": "must-not-survive",
            },
            clear=True,
        ):
            research_cli._enforce_dry_boundary("shadow")
            self.assertNotIn("PUMPPORTAL_API_KEY", os.environ)

    def test_replay_binds_only_the_public_pure_strategy(self) -> None:
        config, bindings = research_cli._load_strategy("replay")
        self.assertEqual(config.hard_stop, strategy.HARD_STOP)
        self.assertIs(bindings.score_history, strategy.score_history)
        self.assertIs(bindings.entry_rejection, strategy.entry_rejection)
        self.assertIs(bindings.exit_reason, strategy.exit_reason)
        self.assertIs(bindings.bond_reason, strategy.bond_reason)
        self.assertEqual(
            bindings.opportunity_milestones,
            strategy.OPPORTUNITY_MILESTONES,
        )
        for forbidden in (
            "trade",
            "buy",
            "sell",
            "submit_transaction",
            "requests",
            "websocket",
            "wallet",
        ):
            self.assertFalse(hasattr(strategy, forbidden), forbidden)

    def test_every_research_mode_uses_the_same_zero_execution_surface(self) -> None:
        surfaces = []
        for mode in ("record", "shadow", "replay"):
            config, bindings = research_cli._load_strategy(mode)
            surfaces.append((config, bindings))
        self.assertEqual(surfaces[0][0], surfaces[1][0])
        self.assertEqual(surfaces[1][0], surfaces[2][0])
        self.assertIs(surfaces[0][1].exit_reason, strategy.exit_reason)

    def test_observer_requires_a_finite_session_bound(self) -> None:
        args = research_cli._parser().parse_args(
            ["record", "--output", "unused.jsonl"]
        )
        with self.assertRaises(research.ResearchSafetyError):
            research_cli._run_observer(args, Path.cwd())


class WebsocketShadowTests(unittest.TestCase):
    def test_logs_exit_latency_and_normalized_price_difference_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shadow.jsonl"
            with research.SafeJsonlWriter(path, 1024 * 1024) as writer:
                monitor = research.WebsocketShadowMonitor(
                    writer,
                    _bindings(),
                    _config(),
                    max_candidates=2,
                )
                monitor.bond_candidate("mint-a", 100.0, 1000.0)
                monitor.handle_event(
                    {"mint": "mint-a", "marketCapSol": 10.0},
                    observed_unix=1001.0,
                )
                monitor.simulated_open(
                    {"mint": "mint-a", "invested_sol": 0.01},
                    100.0,
                    1002.0,
                )
                monitor.handle_event(
                    {"mint": "mint-a", "marketCapSol": 8.0},
                    observed_unix=1003.0,
                )
                monitor.movers_exit(
                    {"mint": "mint-a"},
                    "hard_stop",
                    70.0,
                    -0.30,
                    1006.0,
                )
            events = _events(path)
            shadow_exit = next(
                e for e in events if e.get("event") == "websocket_shadow_exit_decision"
            )
            comparison = next(e for e in events if e.get("event") == "exit_comparison")
            self.assertEqual(shadow_exit["action"], "LOG_ONLY_NO_SELL")
            self.assertEqual(comparison["latency_seconds"], 3.0)
            self.assertEqual(comparison["websocket_estimated_movers_mc"], 80.0)
            self.assertEqual(comparison["estimated_price_difference"], -10.0)
            self.assertAlmostEqual(comparison["gross_difference_bps"], -1000.0)

    def test_candidate_tick_without_position_is_observation_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "candidate.jsonl"
            with research.SafeJsonlWriter(path, 1024 * 1024) as writer:
                monitor = research.WebsocketShadowMonitor(
                    writer,
                    _bindings(),
                    _config(),
                )
                monitor.bond_candidate("mint-a", 100.0, 1000.0)
                monitor.handle_event(
                    {"mint": "mint-a", "marketCapSol": 10.0},
                    observed_unix=1001.0,
                )
            tick = next(e for e in _events(path) if e.get("event") == "trade_tick")
            self.assertIsNone(tick["position"])

    def test_subscription_is_deduplicated_and_shutdown_closes_thread(self) -> None:
        class WebSocketTimeoutException(Exception):
            pass

        class FakeWebSocket:
            def __init__(self) -> None:
                self.messages: list[dict] = []
                self.ready = threading.Event()
                self.closed = False

            def settimeout(self, _timeout) -> None:
                self.ready.set()

            def send(self, payload: str) -> None:
                self.messages.append(json.loads(payload))

            def recv(self) -> str:
                if self.closed:
                    return ""
                threading.Event().wait(0.01)
                raise WebSocketTimeoutException()

            def close(self, timeout=None) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lifecycle.jsonl"
            fake = FakeWebSocket()
            with research.SafeJsonlWriter(path, 1024 * 1024) as writer:
                monitor = research.WebsocketShadowMonitor(
                    writer,
                    _bindings(),
                    _config(),
                    ws_factory=lambda: fake,
                )
                monitor.start()
                self.assertTrue(fake.ready.wait(1.0))
                monitor.bond_candidate("mint-a", 100.0, 1000.0)
                monitor.simulated_open(
                    {"mint": "mint-a", "invested_sol": 0.01},
                    100.0,
                    1001.0,
                )
                monitor.stop()
                monitor.raise_if_failed()
                self.assertIsNone(monitor._thread)
            subscribes = [
                message
                for message in fake.messages
                if message.get("method") == "subscribeTokenTrade"
            ]
            unsubscribes = [
                message
                for message in fake.messages
                if message.get("method") == "unsubscribeTokenTrade"
            ]
            self.assertEqual(len(subscribes), 1)
            self.assertEqual(unsubscribes[-1]["keys"], ["mint-a"])
            self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
