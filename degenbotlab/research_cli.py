from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from . import research
from .strategies import bottotrot as strategy


PUBLIC_SHADOW_WS_URL = "wss://pumpportal.fun/api/data"
PUBLIC_MOVERS_URL = "https://advanced-indexer.pump.fun/boards/movers"
PUBLIC_MOVERS_PARAMS = {"tier": "web", "surface": "WEB", "limit": "100"}
PUBLIC_MOVERS_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Degen-Bot-Lab/0.1 research-only",
}
MAX_REPLAY_INPUT_BYTES = 1024 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-only BotToTrot Movers recorder, deterministic replay runner, "
            "and websocket exit-shadow research tool."
        )
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    def output_args(item: argparse.ArgumentParser) -> None:
        item.add_argument("--output", type=Path, required=True)
        item.add_argument(
            "--max-log-mb",
            type=float,
            default=float(os.getenv("BOT_RESEARCH_MAX_LOG_MB", "64")),
        )
        item.add_argument("--overwrite", action="store_true")

    record = sub.add_parser("record", help="record Movers snapshots and dry decisions")
    output_args(record)
    record.add_argument("--duration-seconds", type=float, default=0.0)
    record.add_argument("--max-snapshots", type=int, default=0)

    shadow = sub.add_parser(
        "shadow",
        help="record Movers plus the public, observation-only websocket shadow",
    )
    output_args(shadow)
    shadow.add_argument("--duration-seconds", type=float, default=0.0)
    shadow.add_argument("--max-snapshots", type=int, default=0)
    shadow.add_argument("--enable-websocket-shadow", action="store_true")
    shadow.add_argument("--max-shadow-candidates", type=int, default=32)

    replay = sub.add_parser("replay", help="offline deterministic replay")
    output_args(replay)
    replay.add_argument("--input", type=Path, required=True)
    return parser


def _enforce_dry_boundary(mode: str) -> None:
    if os.getenv("BOT_RESEARCH_ENABLE") != "1":
        raise research.ResearchSafetyError(
            "set BOT_RESEARCH_ENABLE=1 to opt into the dry research layer"
        )
    if os.getenv("BOT_LIVE") != "0":
        raise research.ResearchSafetyError(
            "research requires an explicit BOT_LIVE=0"
        )
    if os.getenv("BOT_FAST_EXIT", "0") == "1":
        raise research.ResearchSafetyError(
            "BOT_FAST_EXIT must be 0 for the research layer"
        )
    if os.getenv("BOT_DEV_HISTORY_ENFORCE", "0") == "1":
        raise research.ResearchSafetyError(
            "creator-history enforcement is outside this frozen research run"
        )
    if mode == "replay" and os.getenv("BOT_RESEARCH_ALLOW_NETWORK", "0") == "1":
        raise research.ResearchSafetyError("offline replay never permits network access")

    # Defense in depth: the child process cannot build an authenticated endpoint.
    os.environ["BOT_LIVE"] = "0"
    os.environ["BOT_FAST_EXIT"] = "0"
    os.environ.pop("PUMPPORTAL_API_KEY", None)


def _blocked_execution(*_args, **_kwargs):
    raise research.ResearchSafetyError(
        "transaction/execution function is unavailable in BotToTrot research mode"
    )


def _blocked_network(*_args, **_kwargs):
    raise research.ResearchSafetyError("network call blocked by offline replay boundary")


class ReplayNetworkDeny:
    """Process-wide Python socket deny used for the complete replay operation."""

    def __init__(self) -> None:
        self._original_socket = socket.socket
        self._original_create_connection = socket.create_connection
        self._original_getaddrinfo = socket.getaddrinfo
        self._active = False
        self.blocked_attempts = 0

    def __enter__(self) -> "ReplayNetworkDeny":
        if self._active:
            raise research.ResearchSafetyError("replay network deny is already active")
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


def _load_strategy(mode: str):
    del mode
    try:
        strategy.validate_config()
    except (TypeError, ValueError, RuntimeError) as exc:
        raise research.ResearchSafetyError(
            f"invalid frozen strategy configuration ({exc.__class__.__name__})"
        ) from None
    for forbidden in ("trade", "buy", "sell", "submit_transaction"):
        if callable(getattr(strategy, forbidden, None)):
            raise research.ResearchSafetyError(
                "transaction-capable strategy surface is forbidden"
            )

    config = research.StrategyConfig.from_core(strategy, dict(os.environ))
    if (
        config.min_post_bond_seconds < 0
        or config.probe_confirm_seconds < 0
        or config.add_confirm_seconds < 0
    ):
        raise research.ResearchSafetyError("bond timing guards must be >= 0")
    bindings = research.StrategyBindings(
        score_history=strategy.score_history,
        entry_rejection=strategy.entry_rejection,
        exit_reason=strategy.exit_reason,
        promoted_stage=strategy.promoted_stage,
        add_basis=strategy.add_basis,
        fast_price_exit_reason=strategy.fast_price_exit_reason,
        bond_reason=strategy.bond_reason,
        opportunity_milestones=strategy.OPPORTUNITY_MILESTONES,
    )
    return config, bindings


def _git_value(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise research.ResearchSafetyError(
            f"local git metadata check failed ({exc.__class__.__name__})"
        ) from None
    return completed.stdout.strip()


def _metadata(repo_root: Path, config: research.StrategyConfig) -> dict[str, Any]:
    source_paths = [repo_root / "degenbotlab" / "strategies" / "bottotrot.py"]
    return {
        "branch": _git_value(repo_root, "branch", "--show-current"),
        "commit": _git_value(repo_root, "rev-parse", "HEAD"),
        "strategy": strategy.STRATEGY_ID,
        "strategy_config": asdict(config),
        "strategy_fingerprint": research.strategy_fingerprint(config, source_paths),
        "execution": "simulated_only",
        "transactions": "blocked",
    }


class MoversClient:
    def __init__(self, writer: research.SafeJsonlWriter) -> None:
        self.writer = writer
        self.initial_backoff = float(os.getenv("BOT_MOVERS_429_INITIAL_BACKOFF", "15"))
        self.max_backoff = float(os.getenv("BOT_MOVERS_429_MAX_BACKOFF", "60"))
        if self.initial_backoff <= 0 or self.max_backoff < self.initial_backoff:
            raise research.ResearchSafetyError("invalid Movers backoff configuration")
        self.session = requests.Session()
        self.session.trust_env = False

    def fetch(self) -> dict[str, Any]:
        backoff = self.initial_backoff
        transient_attempt = 0
        while True:
            try:
                response = self.session.get(
                    PUBLIC_MOVERS_URL,
                    params=PUBLIC_MOVERS_PARAMS,
                    headers=PUBLIC_MOVERS_HEADERS,
                    timeout=12,
                )
                if response.status_code == 429:
                    retry = response.headers.get("Retry-After")
                    try:
                        wait_seconds = float(retry) if retry else backoff
                    except (TypeError, ValueError):
                        wait_seconds = backoff
                    wait_seconds = min(max(wait_seconds, self.initial_backoff), self.max_backoff)
                    self.writer.write(
                        {
                            "type": "observer_status",
                            "event": "movers_rate_limit",
                            "observed_at": research.iso_from_unix(time.time()),
                            "status": 429,
                            "retry_seconds": wait_seconds,
                        }
                    )
                    time.sleep(wait_seconds)
                    backoff = min(backoff * 2.0, self.max_backoff)
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Movers payload is not an object")
                return payload
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                transient_attempt += 1
                if transient_attempt >= 5:
                    raise research.ResearchSafetyError(
                        f"Movers observation failed ({exc.__class__.__name__})"
                    ) from exc
                wait_seconds = min(2 ** (transient_attempt - 1), 8)
                self.writer.write(
                    {
                        "type": "observer_status",
                        "event": "movers_retry",
                        "observed_at": research.iso_from_unix(time.time()),
                        "error_type": exc.__class__.__name__,
                        "retry_seconds": wait_seconds,
                    }
                )
                time.sleep(wait_seconds)

    def close(self) -> None:
        self.session.close()


def _normalize_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    entries = payload.get("entries") or []
    if not isinstance(entries, list):
        raise research.ResearchSafetyError("Movers entries must be a list")
    for raw in entries:
        if isinstance(raw, dict):
            normalized.append(strategy.snap(raw))
    return normalized


def _session_event(
    mode: str,
    metadata: dict[str, Any],
    *,
    started_at: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "session",
        "mode": mode,
        "started_at": started_at or research.iso_from_unix(time.time()),
        **metadata,
    }


def _max_bytes(max_log_mb: float) -> int:
    if not 0 < max_log_mb <= 1024:
        raise research.ResearchSafetyError("--max-log-mb must be > 0 and <= 1024")
    return int(max_log_mb * 1024 * 1024)


def _run_observer(args: argparse.Namespace, repo_root: Path) -> int:
    if args.duration_seconds < 0 or args.max_snapshots < 0:
        raise research.ResearchSafetyError(
            "duration and snapshot limits must be >= 0"
        )
    if args.mode == "shadow" and not 1 <= args.max_shadow_candidates <= 256:
        raise research.ResearchSafetyError(
            "--max-shadow-candidates must be between 1 and 256"
        )
    if args.duration_seconds == 0 and args.max_snapshots == 0:
        raise research.ResearchSafetyError(
            "record and shadow sessions require a duration or snapshot bound"
        )
    config, bindings = _load_strategy(args.mode)
    metadata = _metadata(repo_root, config)
    with research.SafeJsonlWriter(
        args.output,
        _max_bytes(args.max_log_mb),
        overwrite=args.overwrite,
    ) as writer:
        writer.write(_session_event(args.mode, metadata))
        shadow = None
        if args.mode == "shadow":
            if not args.enable_websocket_shadow:
                raise research.ResearchSafetyError(
                    "shadow mode requires --enable-websocket-shadow"
                )
            import certifi
            import websocket

            def public_ws_factory():
                return websocket.create_connection(
                    PUBLIC_SHADOW_WS_URL,
                    timeout=8,
                    sslopt={"ca_certs": certifi.where()},
                )

            shadow = research.WebsocketShadowMonitor(
                writer,
                bindings,
                config,
                ws_factory=public_ws_factory,
                max_candidates=args.max_shadow_candidates,
            )
        engine = research.ResearchEngine(
            bindings,
            config,
            writer,
            shadow=shadow,
        )
        client = MoversClient(writer)
        started = time.monotonic()
        snapshots = 0
        stop_reason = "operator_interrupt"
        try:
            if shadow is not None:
                shadow.start()
            while True:
                if shadow is not None:
                    shadow.raise_if_failed()
                if args.duration_seconds > 0 and time.monotonic() - started >= args.duration_seconds:
                    stop_reason = "duration_reached"
                    break
                payload = client.fetch()
                observed_unix = time.time()
                observed_monotonic = time.monotonic()
                keep_running = engine.ingest_snapshot(
                    _normalize_entries(payload),
                    observed_unix,
                    observed_at=research.iso_from_unix(observed_unix),
                    observed_monotonic=observed_monotonic,
                )
                snapshots += 1
                if shadow is not None:
                    shadow.raise_if_failed()
                if not keep_running:
                    stop_reason = "strategy_max_trades_reached"
                    break
                if args.max_snapshots > 0 and snapshots >= args.max_snapshots:
                    stop_reason = "snapshot_limit_reached"
                    break
                time.sleep(config.poll_seconds)
        except KeyboardInterrupt:
            stop_reason = "operator_interrupt"
        finally:
            try:
                if shadow is not None:
                    shadow.stop()
            finally:
                client.close()
        writer.write(
            {
                "type": "summary",
                "mode": args.mode,
                "finished_at": research.iso_from_unix(time.time()),
                "stop_reason": stop_reason,
                "snapshots": snapshots,
                "decisions": engine.decision_count,
                "decision_digest": engine.decision_hash,
                "simulated_completed_positions": engine.completed,
                "open_simulated_position": engine.position,
                "transactions_submitted": 0,
            }
        )
    print(
        "RESEARCH_COMPLETE",
        f"mode={args.mode}",
        f"snapshots={snapshots}",
        f"decisions={engine.decision_count}",
        "transactions=0",
        "output_written=true",
        flush=True,
    )
    return 0


def _run_replay(args: argparse.Namespace, repo_root: Path) -> int:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if input_path == output_path:
        raise research.ResearchSafetyError("replay input and output must be different files")
    if not input_path.is_file():
        raise research.ResearchSafetyError("replay input does not exist")
    try:
        input_bytes = input_path.stat().st_size
    except OSError as exc:
        raise research.ResearchSafetyError(
            f"cannot stat replay input ({exc.__class__.__name__}, errno={exc.errno})"
        ) from None
    if input_bytes <= 0 or input_bytes > MAX_REPLAY_INPUT_BYTES:
        raise research.ResearchSafetyError(
            "replay input must be non-empty and no larger than 1 GiB"
        )

    config, bindings = _load_strategy("replay")
    metadata = _metadata(repo_root, config)
    recorded_decisions: list[dict[str, Any]] = []
    header: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    last_event: dict[str, Any] | None = None
    snapshots: list[dict[str, Any]] = []
    for event in research.read_jsonl(input_path):
        if header is None:
            if event.get("type") != "session":
                raise research.ReplayFormatError("first replay record must be a session")
            header = event
        elif event.get("type") == "session":
            raise research.ReplayFormatError("replay input contains multiple sessions")
        if event.get("type") == "movers_snapshot":
            snapshots.append(event)
        elif event.get("type") == "strategy_decision":
            recorded_decisions.append(event)
        elif event.get("type") == "summary":
            if summary is not None:
                raise research.ReplayFormatError("replay input contains multiple summaries")
            summary = event
        last_event = event
    assert header is not None
    if header.get("mode") not in {"record", "shadow"}:
        raise research.ReplayFormatError("replay requires a record or shadow session")
    if (
        header.get("execution") != "simulated_only"
        or header.get("transactions") != "blocked"
    ):
        raise research.ReplayFormatError(
            "replay input is not explicitly simulated-only and transaction-blocked"
        )
    if header.get("strategy_fingerprint") != metadata["strategy_fingerprint"]:
        raise research.ReplayFormatError(
            "strategy fingerprint mismatch; replay requires the exact recorded strategy/config"
        )
    if (
        header.get("strategy") != metadata["strategy"]
        or header.get("strategy_config") != metadata["strategy_config"]
    ):
        raise research.ReplayFormatError(
            "recorded strategy identity/config does not match the installed strategy"
        )
    if not snapshots:
        raise research.ReplayFormatError("replay input contains no Movers snapshots")
    if summary is None or last_event is not summary:
        raise research.ReplayFormatError(
            "replay input is incomplete; one terminal summary is required"
        )
    recorded_hash = research.decision_digest(recorded_decisions)
    if (
        summary.get("mode") != header.get("mode")
        or summary.get("transactions_submitted") != 0
        or summary.get("snapshots") != len(snapshots)
        or summary.get("decisions") != len(recorded_decisions)
        or summary.get("decision_digest") != recorded_hash
    ):
        raise research.ReplayFormatError(
            "replay summary does not verify the complete zero-transaction recording"
        )

    with research.SafeJsonlWriter(
        output_path,
        _max_bytes(args.max_log_mb),
        overwrite=args.overwrite,
    ) as writer:
        writer.write(
            {
                **_session_event(
                    "replay",
                    metadata,
                    started_at=str(header.get("started_at") or snapshots[0]["observed_at"]),
                ),
                "source_strategy_fingerprint": header["strategy_fingerprint"],
            }
        )
        engine = research.ResearchEngine(bindings, config, writer)
        previous_sequence = 0
        previous_time = -1.0
        previous_monotonic = -1.0
        for snapshot in snapshots:
            sequence = snapshot.get("snapshot_sequence")
            observed_unix = snapshot.get("observed_unix")
            observed_monotonic = snapshot.get("observed_monotonic", observed_unix)
            observed_at = snapshot.get("observed_at")
            entries = snapshot.get("entries")
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or sequence != previous_sequence + 1
            ):
                raise research.ReplayFormatError("snapshot sequence is not contiguous")
            if (
                isinstance(observed_unix, bool)
                or not isinstance(observed_unix, (int, float))
                or observed_unix < previous_time
            ):
                raise research.ReplayFormatError("snapshot time is not monotonic")
            if (
                isinstance(observed_monotonic, bool)
                or not isinstance(observed_monotonic, (int, float))
                or observed_monotonic < previous_monotonic
            ):
                raise research.ReplayFormatError(
                    "snapshot monotonic clock is not monotonic"
                )
            if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
                raise research.ReplayFormatError("snapshot entries are invalid")
            if not isinstance(observed_at, str):
                raise research.ReplayFormatError("snapshot observed_at is invalid")
            try:
                parsed_time = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            except (OverflowError, TypeError, ValueError):
                raise research.ReplayFormatError("snapshot observed_at is invalid") from None
            if parsed_time.tzinfo is None:
                raise research.ReplayFormatError("snapshot observed_at is invalid")
            try:
                parsed_unix = parsed_time.timestamp()
            except (OverflowError, OSError, ValueError):
                raise research.ReplayFormatError("snapshot observed_at is invalid") from None
            if abs(parsed_unix - float(observed_unix)) > 1e-6:
                raise research.ReplayFormatError(
                    "snapshot observed_at does not match observed_unix"
                )
            engine.ingest_snapshot(
                entries,
                float(observed_unix),
                observed_at=observed_at,
                observed_monotonic=float(observed_monotonic),
            )
            previous_sequence = sequence
            previous_time = float(observed_unix)
            previous_monotonic = float(observed_monotonic)

        matched = recorded_hash == engine.decision_hash
        writer.write(
            {
                "type": "summary",
                "mode": "replay",
                "finished_at": str(snapshots[-1]["observed_at"]),
                "snapshots": len(snapshots),
                "recorded_decisions": len(recorded_decisions),
                "replayed_decisions": engine.decision_count,
                "recorded_decision_digest": recorded_hash,
                "replayed_decision_digest": engine.decision_hash,
                "deterministic_match": matched,
                "network_calls": 0,
                "transactions_submitted": 0,
            }
        )
    if not matched:
        raise research.ReplayFormatError(
            "replayed decisions differ from the recording; see replay summary"
        )
    print(
        "REPLAY_MATCH",
        f"snapshots={len(snapshots)}",
        f"decisions={engine.decision_count}",
        "network_calls=0",
        "transactions=0",
        "output_written=true",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _enforce_dry_boundary(args.mode)
        if args.mode == "replay":
            with ReplayNetworkDeny():
                repo_root = Path(__file__).resolve().parent.parent
                return _run_replay(args, repo_root)
        repo_root = Path(__file__).resolve().parent.parent
        return _run_observer(args, repo_root)
    except research.ResearchSafetyError as exc:
        print(f"RESEARCH_REFUSED {exc}", file=sys.stderr, flush=True)
        return 2
    except Exception as exc:
        print(
            f"RESEARCH_REFUSED internal safety failure ({exc.__class__.__name__})",
            file=sys.stderr,
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
