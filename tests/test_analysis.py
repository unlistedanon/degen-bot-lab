from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from degenbotlab import analysis as lab
from degenbotlab import analysis_cli as lab_cli


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _events(
    *,
    fingerprint: str = "a" * 64,
    close_gross: float | None = 0.50,
    close_reason: str = "parabolic_trail",
    include_derisk: bool = True,
    open_position: bool = False,
) -> list[dict]:
    events: list[dict] = []

    def add(record: dict) -> None:
        events.append(
            {
                "schema": lab.RESEARCH_SCHEMA,
                "event_index": len(events) + 1,
                **record,
            }
        )

    def snapshot(sequence: int, when: float, monotonic: float) -> None:
        add(
            {
                "type": "movers_snapshot",
                "snapshot_sequence": sequence,
                "observed_at": _iso(when),
                "observed_unix": when,
                "observed_monotonic": monotonic,
                "entries": [
                    {
                        "mint": "mint-a",
                        "chain": "solana:mainnet",
                        "mc": 100.0 + sequence,
                        "progress": min(100.0, 98.0 + sequence),
                    },
                    {
                        "mint": "mint-b",
                        "chain": "solana:mainnet",
                        "mc": 50.0,
                        "progress": 80.0,
                    },
                ],
            }
        )

    def decision(sequence: int, when: float, mint: str, **values) -> None:
        add(
            {
                "type": "strategy_decision",
                "snapshot_sequence": sequence,
                "observed_at": _iso(when),
                "observed_unix": when,
                "mint": mint,
                **values,
            }
        )

    add(
        {
            "type": "session",
            "mode": "shadow",
            "started_at": _iso(999.0),
            "branch": "test-fixture",
            "commit": "e9a71cd9166510e13abfda99fc755004631328ce",
            "strategy": "bottotrot_bonded_runner_v1.1",
            "strategy_config": {},
            "strategy_fingerprint": fingerprint,
            "execution": "simulated_only",
            "transactions": "blocked",
        }
    )
    snapshot(1, 1000.0, 500.0)
    decision(
        1,
        1000.0,
        "mint-b",
        phase="entry",
        decision="REJECT",
        rejection="waiting_for_bond",
    )
    snapshot(2, 1002.0, 502.0)
    decision(
        2,
        1002.0,
        "mint-a",
        phase="bond_gate",
        decision="OPEN",
        reason="progress_cross",
        bond_mc=102.0,
    )
    snapshot(3, 1004.0, 504.0)
    decision(
        3,
        1004.0,
        "mint-a",
        phase="entry",
        decision="QUALIFY",
        rejection=None,
    )
    decision(
        3,
        1004.0,
        "mint-a",
        phase="execution",
        decision="SIMULATED_PROBE",
        amount_sol=0.01,
        entry_mc=103.0,
    )
    snapshot(4, 1006.0, 506.0)
    decision(
        4,
        1006.0,
        "mint-a",
        phase="execution",
        decision="SIMULATED_ADD",
        amount_sol=0.01,
        new_invested_sol=0.02,
        new_basis_mc=104.0,
    )
    if include_derisk:
        decision(
            4,
            1006.0,
            "mint-a",
            phase="execution",
            decision="SIMULATED_DERISK",
            sell_pct=25.0,
            gross=1.0,
        )
    decision(
        4,
        1006.0,
        "mint-a",
        phase="position",
        decision="HOLD",
        reason=None,
        stage="PARABOLIC",
        gross=0.80,
        peak_gross=1.20,
        held_seconds=2.0,
    )
    add(
        {
            "type": "websocket_shadow",
            "event": "websocket_shadow_exit_decision",
            "observed_at": _iso(1007.0),
            "observed_unix": 1007.0,
            "mint": "mint-a",
            "reason": "fast_parabolic_trail",
            "gross": 0.60,
            "peak_gross": 1.20,
            "action": "LOG_ONLY_NO_SELL",
        }
    )
    snapshot(5, 1010.0, 510.0)
    for milestone in (0.25, 0.50, 1.00):
        decision(
            5,
            1010.0,
            "mint-a",
            phase="missed_opportunity",
            decision="MILESTONE",
            milestone=milestone,
            bond_mc=102.0,
            peak_from_bond=milestone,
        )
    if not open_position:
        decision(
            5,
            1010.0,
            "mint-a",
            phase="position",
            decision="SIMULATED_EXIT",
            reason=close_reason,
            stage="PARABOLIC",
            gross=close_gross,
            peak_gross=1.20,
            held_seconds=6.0,
        )
        decision(
            5,
            1010.0,
            "mint-a",
            phase="execution",
            decision="SIMULATED_CLOSE",
            reason=close_reason,
            gross=close_gross,
            mfe_gross=1.20,
            invested_sol=0.02,
            remaining_pct=75.0 if include_derisk else 100.0,
        )
        active = None
        completed = 1
    else:
        active = {
            "mint": "mint-a",
            "stage": "PARABOLIC",
            "mfe_gross": 1.20,
            "peak_gross": 1.20,
        }
        completed = 0
    add(
        {
            "type": "websocket_shadow",
            "event": "exit_comparison",
            "observed_at": _iso(1010.0),
            "observed_unix": 1010.0,
            "mint": "mint-a",
            "status": "websocket_first_or_same_time",
            "movers_reason": close_reason,
            "movers_gross": close_gross,
            "latency_seconds": 3.0,
            "estimated_price_difference_pct": -0.125,
            "gross_difference_bps": -1000.0,
        }
    )
    decisions = sum(item["type"] == "strategy_decision" for item in events)
    decision_hash = hashlib.sha256()
    for item in events:
        if item["type"] != "strategy_decision":
            continue
        clean = {
            key: value
            for key, value in item.items()
            if key not in {"schema", "event_index"}
        }
        decision_hash.update(
            json.dumps(
                clean,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        )
        decision_hash.update(b"\n")
    add(
        {
            "type": "summary",
            "mode": "shadow",
            "finished_at": _iso(1011.0),
            "stop_reason": "snapshot_limit_reached",
            "snapshots": 5,
            "decisions": decisions,
            "decision_digest": decision_hash.hexdigest(),
            "simulated_completed_positions": completed,
            "open_simulated_position": active,
            "transactions_submitted": 0,
        }
    )
    return events


def _write_jsonl(path: Path, events: list[dict]) -> bytes:
    encoded = b"".join(
        json.dumps(
            item,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
        for item in events
    )
    path.write_bytes(encoded)
    return encoded


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


class AnalyzeMetricsTests(unittest.TestCase):
    def test_calculates_required_metrics_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "recording.jsonl"
            original = _write_jsonl(source, _events())
            report = lab.analyze_recording(source)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(
                report["source"]["sha256"], hashlib.sha256(original).hexdigest()
            )
            self.assertEqual(
                report["recording_id"], f"sha256:{report['source']['sha256']}"
            )
            metrics = report["metrics"]
            self.assertEqual(metrics["snapshots"], 5)
            self.assertEqual(metrics["observed_mints"], 2)
            self.assertEqual(metrics["bond_events"], 1)
            self.assertEqual(metrics["entry_qualified_candidates"], 1)
            self.assertEqual(metrics["simulated_probes"], 1)
            self.assertEqual(metrics["simulated_adds"], 1)
            self.assertEqual(metrics["completed_simulated_trades"], 1)
            self.assertEqual(metrics["open_positions"], 0)
            self.assertEqual(metrics["wins"], 1)
            self.assertEqual(metrics["losses"], 0)
            self.assertEqual(metrics["simulated_realized_return"], 0.625)
            self.assertEqual(metrics["average_trade_return"], 0.625)
            self.assertEqual(metrics["median_trade_return"], 0.625)
            self.assertEqual(metrics["maximum_favorable_excursion"], 1.2)
            self.assertEqual(metrics["maximum_adverse_excursion"], 0.0)
            self.assertEqual(
                metrics["opportunity_milestone_counts"],
                {"+25%": 1, "+50%": 1, "+100%": 1, "+300%": 0},
            )
            self.assertEqual(metrics["runner_conversion_rate"], 1.0)
            self.assertEqual(
                report["distributions"]["exit_reasons"], {"parabolic_trail": 1}
            )
            self.assertEqual(metrics["trail_exit_counts"]["PARABOLIC"], 1)
            self.assertEqual(metrics["derisk_events"], 1)
            self.assertEqual(metrics["catastrophic_tail_loss_count"], 0)
            self.assertEqual(metrics["average_hold_duration_seconds"], 6.0)
            self.assertEqual(metrics["average_peak_to_exit_giveback"], 0.7)
            self.assertEqual(metrics["average_bond_to_entry_latency_seconds"], 2.0)
            self.assertEqual(metrics["average_websocket_exit_latency_seconds"], 3.0)
            self.assertEqual(
                report["distributions"]["candidate_rejection_reasons"],
                {"waiting_for_bond": 1},
            )
            self.assertEqual(report["integrity"]["transactions_submitted"], 0)
            self.assertIn("Observed dry-run evidence only", report["evidence_statement"])

    def test_open_position_is_reported_without_becoming_a_completed_trade(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "open.jsonl"
            _write_jsonl(source, _events(open_position=True))
            report = lab.analyze_recording(source)
            self.assertEqual(report["metrics"]["completed_simulated_trades"], 0)
            self.assertEqual(report["metrics"]["open_positions"], 1)
            self.assertEqual(report["metrics"]["wins"], 0)
            self.assertEqual(len(report["open_trade_details"]), 1)

    def test_missing_return_fields_are_null_and_counted_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "missing.jsonl"
            _write_jsonl(source, _events(close_gross=None))
            report = lab.analyze_recording(source)
            metrics = report["metrics"]
            self.assertIsNone(metrics["simulated_realized_return"])
            self.assertIsNone(metrics["average_trade_return"])
            self.assertEqual(metrics["unclassified_completed_trades"], 1)
            self.assertEqual(report["missing_data"]["trade_return"], 1)
            self.assertEqual(report["available_samples"]["trade_returns"], 0)

    def test_catastrophic_threshold_is_explicit_inclusive_and_not_strategy_logic(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "tail.jsonl"
            _write_jsonl(
                source,
                _events(
                    close_gross=-0.50,
                    close_reason="hard_stop",
                    include_derisk=False,
                ),
            )
            report = lab.analyze_recording(source)
            self.assertEqual(
                report["definitions"]["catastrophic_tail_return_threshold"], -0.50
            )
            self.assertEqual(report["metrics"]["catastrophic_tail_loss_count"], 1)
            self.assertEqual(report["metrics"]["hard_stop_count"], 1)
            self.assertEqual(report["metrics"]["losses"], 1)


class ValidationTests(unittest.TestCase):
    def test_rejects_malformed_schema_indexes_times_and_incomplete_runs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cases: list[tuple[str, list[dict]]] = []
            wrong_schema = _events()
            wrong_schema[1]["schema"] = "other.schema"
            cases.append(("schema", wrong_schema))
            bad_index = _events()
            bad_index[2]["event_index"] = bad_index[1]["event_index"]
            cases.append(("index", bad_index))
            backwards = _events()
            snapshot_indexes = [
                index
                for index, item in enumerate(backwards)
                if item["type"] == "movers_snapshot"
            ]
            target = backwards[snapshot_indexes[2]]
            target["observed_unix"] = 1001.0
            target["observed_at"] = _iso(1001.0)
            cases.append(("time", backwards))
            decision_time = _events()
            qualify = next(
                item
                for item in decision_time
                if item.get("type") == "strategy_decision"
                and item.get("decision") == "QUALIFY"
            )
            qualify["observed_unix"] = 1003.0
            qualify["observed_at"] = _iso(1003.0)
            cases.append(("decision_time", decision_time))
            unknown_decision = _events()
            unknown = next(
                item
                for item in unknown_decision
                if item.get("type") == "strategy_decision"
            )
            unknown["decision"] = "UNRECOGNIZED"
            cases.append(("decision_shape", unknown_decision))
            incomplete = _events()[:-1]
            cases.append(("summary", incomplete))
            multiple_sessions = _events()
            multiple_sessions.insert(1, dict(multiple_sessions[0]))
            for index, item in enumerate(multiple_sessions, 1):
                item["event_index"] = index
            cases.append(("sessions", multiple_sessions))
            for name, events in cases:
                with self.subTest(name=name):
                    path = root / f"{name}.jsonl"
                    _write_jsonl(path, events)
                    with self.assertRaises(lab.LabSafetyError):
                        lab.analyze_recording(path)

    def test_rejects_expected_fingerprint_mismatch_and_sensitive_material(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "recording.jsonl"
            _write_jsonl(source, _events())
            with self.assertRaises(lab.LabSafetyError):
                lab.analyze_recording(
                    source,
                    expected_strategy_fingerprint="b" * 64,
                )
            events = _events()
            events[1]["api_key_do-not-echo"] = "x"
            _write_jsonl(source, events)
            with self.assertRaises(lab.LabSafetyError) as failure:
                lab.analyze_recording(source)
            self.assertNotIn("do-not-echo", str(failure.exception))

    def test_rejects_duplicate_json_keys_and_nonfinite_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            duplicate = Path(folder) / "duplicate.jsonl"
            duplicate.write_text(
                '{"schema":"bottotrot.research.v1","schema":"bottotrot.research.v1"}\n',
                encoding="utf-8",
            )
            with self.assertRaises(lab.LabSafetyError):
                lab.analyze_recording(duplicate)
            nonfinite = Path(folder) / "nonfinite.jsonl"
            nonfinite.write_text(
                '{"schema":"bottotrot.research.v1","event_index":1,"type":"session","x":NaN}\n',
                encoding="utf-8",
            )
            with self.assertRaises(lab.LabSafetyError):
                lab.analyze_recording(nonfinite)

    def test_report_write_is_separate_exclusive_bounded_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "recording.jsonl"
            original = _write_jsonl(source, _events())
            report = lab.analyze_recording(source)
            first = root / "first.json"
            second = root / "second.json"
            lab.write_report(first, report, source_paths=(source,))
            lab.write_report(second, report, source_paths=(source,))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(source.read_bytes(), original)
            with self.assertRaises(lab.LabSafetyError):
                lab.write_report(first, report, source_paths=(source,))
            with self.assertRaises(lab.LabSafetyError):
                lab.write_report(source, report, source_paths=(source,), overwrite=True)
            self.assertEqual(source.read_bytes(), original)


class ComparisonTests(unittest.TestCase):
    def _report_file(
        self,
        root: Path,
        name: str,
        *,
        fingerprint: str = "a" * 64,
        snapshot_shift: float = 0.0,
    ) -> Path:
        source = root / f"{name}.jsonl"
        events = _events(fingerprint=fingerprint)
        if snapshot_shift:
            for event in events:
                if event.get("observed_unix") is not None:
                    event["observed_unix"] += snapshot_shift
                    event["observed_at"] = _iso(event["observed_unix"])
            events[0]["started_at"] = _iso(999.0 + snapshot_shift)
            events[-1]["finished_at"] = _iso(1011.0 + snapshot_shift)
            digest = hashlib.sha256()
            for event in events:
                if event["type"] != "strategy_decision":
                    continue
                clean = {
                    key: value
                    for key, value in event.items()
                    if key not in {"schema", "event_index"}
                }
                digest.update(
                    json.dumps(
                        clean,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    ).encode("utf-8")
                )
                digest.update(b"\n")
            events[-1]["decision_digest"] = digest.hexdigest()
        _write_jsonl(source, events)
        report_path = root / f"{name}.json"
        lab.write_report(
            report_path,
            lab.analyze_recording(source),
            source_paths=(source,),
        )
        return report_path

    def test_valid_same_strategy_comparison_has_deterministic_zero_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline = self._report_file(root, "baseline")
            candidate = self._report_file(root, "candidate")
            comparison = lab.compare_reports(baseline, candidate)
            self.assertEqual(
                comparison["compatibility"]["status"],
                "VALID_SAME_STRATEGY_COMPARISON",
            )
            self.assertTrue(
                comparison["compatibility"]["observation_fingerprint_match"]
            )
            numeric = comparison["metric_deltas_candidate_minus_baseline"]
            self.assertTrue(all(value in {0, 0.0, None} for value in numeric.values()))

    def test_comparison_refuses_different_observation_windows(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline = self._report_file(root, "baseline")
            candidate = self._report_file(root, "candidate", snapshot_shift=100.0)
            with self.assertRaises(lab.LabSafetyError):
                lab.compare_reports(baseline, candidate)

    def test_cross_strategy_requires_explicit_flag_and_is_prominently_marked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline = self._report_file(root, "baseline", fingerprint="a" * 64)
            candidate = self._report_file(root, "candidate", fingerprint="b" * 64)
            with self.assertRaises(lab.LabSafetyError):
                lab.compare_reports(baseline, candidate)
            comparison = lab.compare_reports(
                baseline,
                candidate,
                allow_cross_strategy=True,
            )
            self.assertFalse(
                comparison["compatibility"]["strategy_fingerprint_match"]
            )
            self.assertIn("WARNING", comparison["compatibility"]["status"])
            self.assertIn("not pooled", comparison["compatibility"]["warning"])

    def test_comparison_refuses_definition_or_required_metric_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline = self._report_file(root, "baseline")
            candidate = self._report_file(root, "candidate")
            value = json.loads(candidate.read_text(encoding="utf-8"))
            value["definitions"]["catastrophic_tail_return_threshold"] = -0.75
            _write_json(candidate, value)
            with self.assertRaises(lab.LabSafetyError):
                lab.compare_reports(baseline, candidate)
            value["definitions"]["catastrophic_tail_return_threshold"] = -0.50
            del value["metrics"]["wins"]
            _write_json(candidate, value)
            with self.assertRaises(lab.LabSafetyError):
                lab.compare_reports(baseline, candidate)


class CliSafetyTests(unittest.TestCase):
    def test_offline_boundary_rejects_live_and_removes_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {"BOT_LIVE": "1", "BOT_FAST_EXIT": "0"},
            clear=True,
        ):
            with self.assertRaises(lab.LabSafetyError):
                lab_cli._enforce_offline_boundary()
        with patch.dict(
            os.environ,
            {
                "BOT_LIVE": "0",
                "BOT_FAST_EXIT": "0",
                "PUMPPORTAL_API_KEY": "must-not-survive",
            },
            clear=True,
        ):
            lab_cli._enforce_offline_boundary()
            self.assertNotIn("PUMPPORTAL_API_KEY", os.environ)

    def test_process_wide_network_deny_blocks_and_restores(self) -> None:
        original_socket = socket.socket
        original_create = socket.create_connection
        guard = lab_cli.OfflineNetworkDeny()
        with guard:
            denied = socket.socket()
            try:
                with self.assertRaises(lab.LabSafetyError):
                    denied.connect(("127.0.0.1", 9))
            finally:
                denied.close()
            with self.assertRaises(lab.LabSafetyError):
                socket.create_connection(("127.0.0.1", 9))
            with self.assertRaises(lab.LabSafetyError):
                socket.getaddrinfo("example.invalid", 443)
        self.assertGreaterEqual(guard.blocked_attempts, 3)
        self.assertIs(socket.socket, original_socket)
        self.assertIs(socket.create_connection, original_create)

    def test_cli_analyze_is_zero_transaction_and_sanitizes_unexpected_errors(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "recording.jsonl"
            output = root / "report.json"
            _write_jsonl(source, _events())
            stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {
                        "BOT_LIVE": "0",
                        "BOT_FAST_EXIT": "0",
                        "BOT_DEV_HISTORY_ENFORCE": "0",
                    },
                    clear=True,
                ),
                redirect_stdout(stdout),
            ):
                result = lab_cli.main(
                    ["analyze", "--input", str(source), "--output", str(output)]
                )
            self.assertEqual(result, 0)
            self.assertTrue(output.is_file())
            self.assertIn("network_calls=0", stdout.getvalue())
            self.assertIn("transactions=0", stdout.getvalue())

            stderr = io.StringIO()
            with (
                patch.dict(os.environ, {"BOT_LIVE": "0"}, clear=True),
                patch.object(
                    lab,
                    "analyze_recording",
                    side_effect=RuntimeError("do-not-print"),
                ),
                redirect_stderr(stderr),
            ):
                result = lab_cli.main(
                    [
                        "analyze",
                        "--input",
                        str(source),
                        "--output",
                        str(root / "unused.json"),
                    ]
                )
            self.assertEqual(result, 2)
            self.assertNotIn("do-not-print", stderr.getvalue())
            self.assertIn("RuntimeError", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
