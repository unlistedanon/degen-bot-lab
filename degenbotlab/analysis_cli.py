from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

from . import analysis as lab


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline deterministic BotToTrot experiment analyzer and report comparator."
        )
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    analyze = sub.add_parser("analyze", help="analyze exactly one research JSONL file")
    analyze.add_argument("--input", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--expected-strategy-fingerprint")
    analyze.add_argument("--overwrite", action="store_true")

    compare = sub.add_parser("compare", help="compare two compatible lab reports")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--allow-cross-strategy", action="store_true")
    compare.add_argument("--overwrite", action="store_true")
    return parser


def _enforce_offline_boundary() -> None:
    if os.getenv("BOT_LIVE") != "0":
        raise lab.LabSafetyError("Bot Lab requires an explicit BOT_LIVE=0")
    if os.getenv("BOT_FAST_EXIT", "0") == "1":
        raise lab.LabSafetyError("BOT_FAST_EXIT must be 0 for offline analysis")
    if os.getenv("BOT_DEV_HISTORY_ENFORCE", "0") == "1":
        raise lab.LabSafetyError(
            "creator-history enforcement is unavailable in offline analysis"
        )
    if os.getenv("BOT_RESEARCH_ALLOW_NETWORK", "0") == "1" or os.getenv(
        "BOT_LAB_ALLOW_NETWORK", "0"
    ) == "1":
        raise lab.LabSafetyError("Bot Lab analysis never permits network access")
    os.environ["BOT_LIVE"] = "0"
    os.environ["BOT_FAST_EXIT"] = "0"
    os.environ["BOT_DEV_HISTORY_ENFORCE"] = "0"
    os.environ.pop("PUMPPORTAL_API_KEY", None)


def _blocked_network(*_args, **_kwargs):
    raise lab.LabSafetyError("network call blocked by offline Bot Lab boundary")


class OfflineNetworkDeny:
    """Process-wide Python socket and DNS deny for the complete CLI operation."""

    def __init__(self) -> None:
        self._original_socket = socket.socket
        self._original_create_connection = socket.create_connection
        self._original_getaddrinfo = socket.getaddrinfo
        self._active = False
        self.blocked_attempts = 0

    def __enter__(self) -> "OfflineNetworkDeny":
        if self._active:
            raise lab.LabSafetyError("offline network deny is already active")
        original_socket = self._original_socket
        guard = self

        def deny(*_args, **_kwargs):
            guard.blocked_attempts += 1
            return _blocked_network()

        class DeniedSocket(original_socket):
            def connect(self, *_args, **_kwargs):
                return deny()

            def connect_ex(self, *_args, **_kwargs):
                return deny()

            def sendto(self, *_args, **_kwargs):
                return deny()

        socket.socket = DeniedSocket
        socket.create_connection = deny
        socket.getaddrinfo = deny
        self._active = True
        return self

    def __exit__(self, _kind, _value, _traceback) -> None:
        if not self._active:
            return
        socket.socket = self._original_socket
        socket.create_connection = self._original_create_connection
        socket.getaddrinfo = self._original_getaddrinfo
        self._active = False


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _enforce_offline_boundary()
        with OfflineNetworkDeny():
            if args.mode == "analyze":
                report = lab.analyze_recording(
                    args.input,
                    expected_strategy_fingerprint=args.expected_strategy_fingerprint,
                )
                lab.write_report(
                    args.output,
                    report,
                    source_paths=(args.input,),
                    overwrite=args.overwrite,
                )
                metrics = report["metrics"]
                print(
                    "LAB_ANALYSIS_COMPLETE",
                    f"recording_id={report['recording_id']}",
                    f"snapshots={metrics['snapshots']}",
                    f"completed_trades={metrics['completed_simulated_trades']}",
                    "network_calls=0",
                    "transactions=0",
                    "output_written=true",
                    flush=True,
                )
                return 0

            comparison = lab.compare_reports(
                args.baseline,
                args.candidate,
                allow_cross_strategy=args.allow_cross_strategy,
            )
            lab.write_report(
                args.output,
                comparison,
                source_paths=(args.baseline, args.candidate),
                overwrite=args.overwrite,
            )
            print(
                "LAB_COMPARISON_COMPLETE",
                f"status={comparison['compatibility']['status']}",
                "network_calls=0",
                "transactions=0",
                "output_written=true",
                flush=True,
            )
            return 0
    except lab.LabSafetyError as exc:
        print(f"LAB_REFUSED {exc}", file=sys.stderr, flush=True)
        return 2
    except Exception as exc:
        print(
            f"LAB_REFUSED internal safety failure ({exc.__class__.__name__})",
            file=sys.stderr,
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
