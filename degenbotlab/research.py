from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA = "bottotrot.research.v1"
MIN_FREE_DISK_BYTES = 256 * 1024 * 1024
SENSITIVE_KEY_PARTS = (
    "api_key",
    "api-key",
    "apikey",
    "authorization",
    "credential",
    "mnemonic",
    "password",
    "private_key",
    "secret",
    "seed_phrase",
)
SENSITIVE_VALUE_MARKERS = (
    "api-key=",
    "apikey=",
    "bearer ",
    "authorization: bearer",
    "mnemonic=",
    "private_key=",
    "secret=",
)


class ResearchSafetyError(RuntimeError):
    pass


class ReplayFormatError(ResearchSafetyError):
    pass


def iso_from_unix(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _assert_secret_free(value: Any, path: str = "event") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                raise ResearchSafetyError("refusing to record sensitive-looking field")
            _assert_secret_free(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_secret_free(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in SENSITIVE_VALUE_MARKERS):
            raise ResearchSafetyError("refusing to record sensitive-looking value")


def decision_digest(events: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for event in events:
        if event.get("type") != "strategy_decision":
            continue
        clean = {
            key: value
            for key, value in event.items()
            if key not in {"schema", "event_index"}
        }
        digest.update(_canonical(clean))
        digest.update(b"\n")
    return digest.hexdigest()


class SafeJsonlWriter:
    """Exclusive, bounded JSONL writer that rejects secret-shaped material."""

    def __init__(self, path: Path, max_bytes: int, *, overwrite: bool = False) -> None:
        self.path = path.resolve()
        self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0:
            raise ResearchSafetyError("research log max_bytes must be > 0")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self.path.parent).free
        except OSError as exc:
            raise ResearchSafetyError(
                f"cannot prepare research output ({exc.__class__.__name__}, errno={exc.errno})"
            ) from None
        required = self.max_bytes + MIN_FREE_DISK_BYTES
        if free < required:
            raise ResearchSafetyError(
                "insufficient free disk for bounded research log plus 256 MB reserve"
            )
        mode = "wb" if overwrite else "xb"
        try:
            self._fh = self.path.open(mode)
        except FileExistsError as exc:
            raise ResearchSafetyError(
                "output already exists; choose a new path or use --overwrite"
            ) from exc
        except OSError as exc:
            raise ResearchSafetyError(
                f"cannot open research output ({exc.__class__.__name__}, errno={exc.errno})"
            ) from None
        self._bytes = 0
        self._index = 0
        self._lock = threading.RLock()

    def write(self, event: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event, dict) or not event.get("type"):
            raise ResearchSafetyError("every research record requires a type")
        _assert_secret_free(event)
        with self._lock:
            self._index += 1
            record = {
                "schema": SCHEMA,
                "event_index": self._index,
                **event,
            }
            try:
                encoded = _canonical(record) + b"\n"
            except (TypeError, ValueError) as exc:
                raise ResearchSafetyError(
                    f"research event is not safely serializable ({exc.__class__.__name__})"
                ) from None
            if self._bytes + len(encoded) > self.max_bytes:
                raise ResearchSafetyError(
                    f"research log cap reached at {self.max_bytes} bytes; stopped fail-closed"
                )
            try:
                free = shutil.disk_usage(self.path.parent).free
                if free - len(encoded) < MIN_FREE_DISK_BYTES:
                    raise ResearchSafetyError(
                        "research disk reserve reached; stopped fail-closed"
                    )
                self._fh.write(encoded)
                self._fh.flush()
            except ResearchSafetyError:
                raise
            except OSError as exc:
                raise ResearchSafetyError(
                    f"research log write failed ({exc.__class__.__name__}, errno={exc.errno})"
                ) from None
            self._bytes += len(encoded)
            return record

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                failure: OSError | None = None
                try:
                    self._fh.flush()
                except OSError as exc:
                    failure = exc
                try:
                    self._fh.close()
                except OSError as exc:
                    if failure is None:
                        failure = exc
                if failure is not None:
                    raise ResearchSafetyError(
                        "research log close failed "
                        f"({failure.__class__.__name__}, errno={failure.errno})"
                    ) from None

    def __enter__(self) -> "SafeJsonlWriter":
        return self

    def __exit__(self, _kind, value, _traceback) -> None:
        try:
            self.close()
        except ResearchSafetyError:
            if value is None:
                raise


@dataclass(frozen=True)
class StrategyConfig:
    poll_seconds: float
    probe_sol: float
    add_sol: float
    max_position_sol: float
    max_adds: int
    max_trades: int
    probe_score: float
    add_score: float
    watch_score: float
    min_observations: int
    probe_confirm_polls: int
    add_confirm_polls: int
    add_min_gross: float
    hard_stop: float
    runner_at_gross: float
    parabolic_at_gross: float
    moon_at_gross: float
    runner_trail: float
    parabolic_trail: float
    moon_trail: float
    runner_floor: float
    thesis_exit_score: float
    thesis_bad_polls: int
    min_thesis_hold_seconds: float
    derisk_at_gross: float
    derisk_sell_pct: float
    missing_exit_polls: int
    history_points: int
    post_exit_track_seconds: float
    min_post_bond_seconds: float
    probe_confirm_seconds: float
    add_confirm_seconds: float

    @classmethod
    def from_core(cls, core: Any, environ: dict[str, str]) -> "StrategyConfig":
        add_confirm_seconds = float(environ.get("BOT_ADD_CONFIRM_SECONDS", "2.0"))
        effective_add_confirm_polls = max(
            int(core.ADD_CONFIRM_POLLS),
            (
                int(math.ceil(add_confirm_seconds / float(core.POLL_SECONDS)))
                if add_confirm_seconds > 0 and float(core.POLL_SECONDS) > 0
                else 1
            ),
        )
        return cls(
            poll_seconds=float(core.POLL_SECONDS),
            probe_sol=float(core.PROBE_SOL),
            add_sol=float(core.ADD_SOL),
            max_position_sol=float(core.MAX_POSITION_SOL),
            max_adds=int(core.MAX_ADDS),
            max_trades=int(core.MAX_TRADES),
            probe_score=float(core.PROBE_SCORE),
            add_score=float(core.ADD_SCORE),
            watch_score=float(core.WATCH_SCORE),
            min_observations=int(core.MIN_OBSERVATIONS),
            probe_confirm_polls=int(core.PROBE_CONFIRM_POLLS),
            add_confirm_polls=effective_add_confirm_polls,
            add_min_gross=float(core.ADD_MIN_GROSS),
            hard_stop=float(core.HARD_STOP),
            runner_at_gross=float(core.RUNNER_AT_GROSS),
            parabolic_at_gross=float(core.PARABOLIC_AT_GROSS),
            moon_at_gross=float(core.MOON_AT_GROSS),
            runner_trail=float(core.RUNNER_TRAIL),
            parabolic_trail=float(core.PARABOLIC_TRAIL),
            moon_trail=float(core.MOON_TRAIL),
            runner_floor=float(core.RUNNER_FLOOR),
            thesis_exit_score=float(core.THESIS_EXIT_SCORE),
            thesis_bad_polls=int(core.THESIS_BAD_POLLS),
            min_thesis_hold_seconds=float(core.MIN_THESIS_HOLD_SECONDS),
            derisk_at_gross=float(core.DERISK_AT_GROSS),
            derisk_sell_pct=float(core.DERISK_SELL_PCT),
            missing_exit_polls=int(core.MISSING_EXIT_POLLS),
            history_points=int(core.HISTORY_POINTS),
            post_exit_track_seconds=float(core.POST_EXIT_TRACK_SECONDS),
            min_post_bond_seconds=float(environ.get("BOT_MIN_POST_BOND_SECONDS", "4.0")),
            probe_confirm_seconds=float(environ.get("BOT_PROBE_CONFIRM_SECONDS", "2.0")),
            add_confirm_seconds=add_confirm_seconds,
        )


@dataclass(frozen=True)
class StrategyBindings:
    score_history: Callable[[list[dict[str, Any]]], tuple[float, dict[str, float]]]
    entry_rejection: Callable[[float, dict[str, float], dict[str, Any]], str | None]
    exit_reason: Callable[[dict[str, Any], float, float, float], str | None]
    promoted_stage: Callable[[str, float], str]
    add_basis: Callable[[float, float, float, float], tuple[float, float, float]]
    fast_price_exit_reason: Callable[[str, float, float], str | None]
    bond_reason: Callable[[dict[str, Any], dict[str, Any]], str | None]
    opportunity_milestones: tuple[float, ...]


def strategy_fingerprint(
    config: StrategyConfig,
    source_paths: Iterable[Path],
) -> str:
    digest = hashlib.sha256(_canonical(asdict(config)))
    for path in sorted((item.resolve() for item in source_paths), key=str):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError as exc:
            raise ResearchSafetyError(
                "cannot fingerprint frozen strategy source "
                f"({exc.__class__.__name__}, errno={exc.errno})"
            ) from None
        digest.update(b"\0")
    return digest.hexdigest()


class ShadowHooks:
    def bond_candidate(
        self,
        mint: str,
        bond_mc: float | None,
        observed_unix: float,
    ) -> None:
        pass

    def simulated_open(
        self,
        position: dict[str, Any],
        movers_mc: float,
        observed_unix: float,
    ) -> None:
        pass

    def simulated_add(
        self,
        position: dict[str, Any],
        add_sol: float,
        movers_mc: float,
        observed_unix: float,
    ) -> None:
        pass

    def simulated_derisk(
        self,
        position: dict[str, Any],
        observed_unix: float,
    ) -> None:
        pass

    def movers_exit(
        self,
        position: dict[str, Any],
        reason: str,
        movers_mc: float | None,
        movers_gross: float | None,
        observed_unix: float,
    ) -> None:
        pass


class ResearchEngine:
    """Deterministic in-memory mirror of the frozen bonded Runner Hunter loop."""

    def __init__(
        self,
        bindings: StrategyBindings,
        config: StrategyConfig,
        writer: SafeJsonlWriter,
        *,
        shadow: ShadowHooks | None = None,
    ) -> None:
        self.bindings = bindings
        self.config = config
        self.writer = writer
        self.shadow = shadow or ShadowHooks()
        self.tracks: dict[str, dict[str, Any]] = {}
        self.bonds: dict[str, dict[str, Any]] = {}
        self.closed_watch: dict[str, dict[str, Any]] = {}
        self.session_closed: set[str] = set()
        self.position: dict[str, Any] | None = None
        self.completed = 0
        self.snapshot_sequence = 0
        self.halted = False
        self._decision_hash = hashlib.sha256()
        self._decision_count = 0

    @property
    def decision_hash(self) -> str:
        return self._decision_hash.hexdigest()

    @property
    def decision_count(self) -> int:
        return self._decision_count

    def _write(self, event: dict[str, Any]) -> dict[str, Any]:
        return self.writer.write(event)

    def _decision(self, event: dict[str, Any]) -> None:
        complete = {
            "type": "strategy_decision",
            "snapshot_sequence": self.snapshot_sequence,
            **event,
        }
        self._decision_hash.update(_canonical(complete))
        self._decision_hash.update(b"\n")
        self._decision_count += 1
        self._write(complete)

    def ingest_snapshot(
        self,
        entries: list[dict[str, Any]],
        observed_unix: float,
        *,
        observed_at: str | None = None,
        observed_monotonic: float | None = None,
    ) -> bool:
        if self.halted:
            return False
        if not math.isfinite(observed_unix) or observed_unix < 0:
            raise ReplayFormatError("snapshot observed_unix must be a finite positive time")
        observed_at = observed_at or iso_from_unix(observed_unix)
        observed_monotonic = (
            observed_unix if observed_monotonic is None else observed_monotonic
        )
        if not math.isfinite(observed_monotonic):
            raise ReplayFormatError("snapshot observed_monotonic must be finite")
        self.snapshot_sequence += 1
        self._write(
            {
                "type": "movers_snapshot",
                "snapshot_sequence": self.snapshot_sequence,
                "observed_at": observed_at,
                "observed_unix": observed_unix,
                "observed_monotonic": observed_monotonic,
                "entries": entries,
            }
        )
        self._process(entries, observed_unix, observed_at, observed_monotonic)
        if (
            self.config.max_trades > 0
            and self.completed >= self.config.max_trades
            and self.position is None
        ):
            self.halted = True
        return not self.halted

    def _new_track(self, observed_unix: float) -> dict[str, Any]:
        return {
            "first_seen_unix": observed_unix,
            "history": [],
            "qualifying_polls": 0,
            "add_polls": 0,
        }

    def _new_bond_state(self) -> dict[str, Any]:
        return {
            "bonded": False,
            "bond_reason": None,
            "bond_mc": None,
            "peak_mc": None,
            "bond_clock": None,
            "qualify_since_clock": None,
            "post_bond_history": [],
            "milestones": set(),
            "ever_qualified": False,
            "last_snapshot": None,
        }

    def _update_bond(
        self,
        mint: str,
        current: dict[str, Any],
        observed_unix: float,
        observed_at: str,
        observed_monotonic: float,
    ) -> dict[str, Any]:
        state = self.bonds.setdefault(mint, self._new_bond_state())
        previous = state.get("last_snapshot")
        if not state["bonded"] and isinstance(previous, dict):
            reason = self.bindings.bond_reason(previous, current)
            if reason is not None:
                bond_mc = _number(current.get("mc"))
                state.update(
                    {
                        "bonded": True,
                        "bond_reason": reason,
                        "bond_mc": bond_mc,
                        "peak_mc": bond_mc,
                        "bond_clock": observed_monotonic,
                        "qualify_since_clock": None,
                        "post_bond_history": [current],
                    }
                )
                self._decision(
                    {
                        "observed_at": observed_at,
                        "observed_unix": observed_unix,
                        "mint": mint,
                        "phase": "bond_gate",
                        "decision": "OPEN",
                        "reason": reason,
                        "bond_mc": bond_mc,
                    }
                )
                self.shadow.bond_candidate(mint, bond_mc, observed_unix)
        elif state["bonded"]:
            post = state["post_bond_history"]
            post.append(current)
            if len(post) > self.config.history_points:
                del post[: -self.config.history_points]
        state["last_snapshot"] = current
        return state

    def _bonded_rejection(
        self,
        mint: str,
        state: dict[str, Any],
        score: float,
        features: dict[str, float],
        current: dict[str, Any],
        observed_monotonic: float,
    ) -> str | None:
        if not state["bonded"]:
            return "waiting_for_bond"
        post = state["post_bond_history"]
        bond_clock = float(state["bond_clock"])
        post_elapsed = max(0.0, observed_monotonic - bond_clock)
        if len(post) < self.config.min_observations:
            state["qualify_since_clock"] = None
            return "post_bond_history"
        if post_elapsed < self.config.min_post_bond_seconds:
            state["qualify_since_clock"] = None
            return "post_bond_time"
        if mint in self.session_closed:
            state["qualify_since_clock"] = None
            return "session_reentry_lockout"
        base = self.bindings.entry_rejection(score, features, current)
        if base is not None:
            state["qualify_since_clock"] = None
            return base
        qualify_since = state.get("qualify_since_clock")
        if not isinstance(qualify_since, (int, float)):
            qualify_since = observed_monotonic
            state["qualify_since_clock"] = qualify_since
        if (
            observed_monotonic - float(qualify_since)
            < self.config.probe_confirm_seconds
        ):
            return "entry_persistence"
        return None

    def _track_opportunity(
        self,
        mint: str,
        state: dict[str, Any],
        current: dict[str, Any],
        score: float,
        rejection: str | None,
        observed_unix: float,
        observed_at: str,
        observed_monotonic: float,
    ) -> None:
        if not state["bonded"]:
            return
        current_mc = _number(current.get("mc"))
        bond_mc = _number(state.get("bond_mc"))
        if current_mc is None or current_mc <= 0 or bond_mc is None or bond_mc <= 0:
            return
        peak_mc = _number(state.get("peak_mc")) or bond_mc
        peak_mc = max(peak_mc, current_mc)
        state["peak_mc"] = peak_mc
        current_from_bond = current_mc / bond_mc - 1.0
        peak_from_bond = peak_mc / bond_mc - 1.0
        state["current_from_bond"] = current_from_bond
        state["peak_from_bond"] = peak_from_bond
        state["last_rejection"] = rejection
        state["last_score"] = score
        if rejection is None:
            state["ever_qualified"] = True
        bond_clock = _number(state.get("bond_clock"))
        qualify_since = _number(state.get("qualify_since_clock"))
        post_elapsed = (
            max(0.0, observed_monotonic - bond_clock)
            if bond_clock is not None
            else 0.0
        )
        qualifying_elapsed = (
            max(0.0, observed_monotonic - qualify_since)
            if qualify_since is not None
            else 0.0
        )
        self._decision(
            {
                "observed_at": observed_at,
                "observed_unix": observed_unix,
                "mint": mint,
                "phase": "bond_opportunity",
                "decision": "MARK",
                "bond_mc": bond_mc,
                "current_mc": current_mc,
                "peak_mc": peak_mc,
                "current_from_bond": current_from_bond,
                "peak_from_bond": peak_from_bond,
                "score": score,
                "rejection": rejection,
                "ever_qualified": bool(state["ever_qualified"]),
                "post_bond_elapsed_seconds": post_elapsed,
                "qualifying_elapsed_seconds": qualifying_elapsed,
            }
        )
        for milestone in self.bindings.opportunity_milestones:
            if peak_from_bond < milestone or milestone in state["milestones"]:
                continue
            state["milestones"].add(milestone)
            self._decision(
                {
                    "observed_at": observed_at,
                    "observed_unix": observed_unix,
                    "mint": mint,
                    "phase": "missed_opportunity",
                    "decision": "MILESTONE",
                    "milestone": milestone,
                    "bond_mc": bond_mc,
                    "current_mc": current_mc,
                    "peak_mc": peak_mc,
                    "current_from_bond": current_from_bond,
                    "peak_from_bond": peak_from_bond,
                    "score": score,
                    "rejection": rejection,
                    "ever_qualified": bool(state["ever_qualified"]),
                }
            )

    def _process(
        self,
        entries: list[dict[str, Any]],
        observed_unix: float,
        observed_at: str,
        observed_monotonic: float,
    ) -> None:
        live_entries: dict[str, dict[str, Any]] = {}
        for current in entries:
            if current.get("chain") != "solana:mainnet":
                continue
            mint = str(current.get("mint") or "")
            if not mint:
                continue
            live_entries[mint] = current
            track = self.tracks.setdefault(mint, self._new_track(observed_unix))
            history = track["history"]
            history.append(current)
            if len(history) > self.config.history_points:
                del history[: -self.config.history_points]

            bond = self._update_bond(
                mint,
                current,
                observed_unix,
                observed_at,
                observed_monotonic,
            )
            scoring_history = bond["post_bond_history"] if bond["bonded"] else history
            score, features = self.bindings.score_history(scoring_history)
            track["score"] = score
            track["features"] = features
            rejection = (
                "not_enough_history"
                if len(history) < self.config.min_observations
                else self._bonded_rejection(
                    mint,
                    bond,
                    score,
                    features,
                    current,
                    observed_monotonic,
                )
            )
            if rejection is None:
                track["qualifying_polls"] = int(track["qualifying_polls"]) + 1
            else:
                track["qualifying_polls"] = 0

            if len(history) >= self.config.min_observations:
                self._track_opportunity(
                    mint,
                    bond,
                    current,
                    score,
                    rejection,
                    observed_unix,
                    observed_at,
                    observed_monotonic,
                )
            self._decision(
                {
                    "observed_at": observed_at,
                    "observed_unix": observed_unix,
                    "mint": mint,
                    "phase": "entry",
                    "decision": "QUALIFY" if rejection is None else "REJECT",
                    "score": score,
                    "features": features,
                    "rejection": rejection,
                    "qualifying_polls": int(track["qualifying_polls"]),
                    "post_bond_observations": len(bond["post_bond_history"]),
                    "snapshot": current,
                }
            )

            if self.position is None:
                can_trade = self.config.max_trades == 0 or self.completed < self.config.max_trades
                if (
                    can_trade
                    and rejection is None
                    and int(track["qualifying_polls"]) >= self.config.probe_confirm_polls
                ):
                    self._simulate_probe(
                        mint,
                        current,
                        score,
                        features,
                        observed_unix,
                        observed_at,
                    )
            elif mint == str(self.position["mint"]):
                self._manage_position(
                    mint,
                    current,
                    score,
                    features,
                    track,
                    observed_unix,
                    observed_at,
                )

        if self.position is not None:
            held_mint = str(self.position["mint"])
            if held_mint not in live_entries:
                self.position["missing_polls"] = int(self.position["missing_polls"]) + 1
                self._decision(
                    {
                        "observed_at": observed_at,
                        "observed_unix": observed_unix,
                        "mint": held_mint,
                        "phase": "position",
                        "decision": "MISSING",
                        "missing_polls": self.position["missing_polls"],
                    }
                )
                if (
                    self.config.missing_exit_polls > 0
                    and int(self.position["missing_polls"])
                    >= self.config.missing_exit_polls
                ):
                    self._close_position(
                        "missing_feed_failsafe",
                        None,
                        None,
                        observed_unix,
                        observed_at,
                    )

        for mint, watch in list(self.closed_watch.items()):
            current = live_entries.get(mint)
            if current is not None:
                mc = _number(current.get("mc"))
                if mc is not None:
                    watch["post_exit_peak_mc"] = max(
                        float(watch["post_exit_peak_mc"]), mc
                    )
            if (
                observed_unix - float(watch["exit_time_unix"])
                >= self.config.post_exit_track_seconds
            ):
                entry_mc = float(watch["entry_mc"])
                self._decision(
                    {
                        "observed_at": observed_at,
                        "observed_unix": observed_unix,
                        "mint": mint,
                        "phase": "post_exit",
                        "decision": "RESULT",
                        "reason": watch["reason"],
                        "exit_gross": float(watch["exit_mc"]) / entry_mc - 1.0,
                        "post_exit_peak_gross": float(watch["post_exit_peak_mc"])
                        / entry_mc
                        - 1.0,
                    }
                )
                del self.closed_watch[mint]

        active = set(live_entries)
        stale_before = observed_unix - 600.0
        for mint, track in list(self.tracks.items()):
            if mint not in active and float(track["first_seen_unix"]) < stale_before:
                del self.tracks[mint]

    def _simulate_probe(
        self,
        mint: str,
        current: dict[str, Any],
        score: float,
        features: dict[str, float],
        observed_unix: float,
        observed_at: str,
    ) -> None:
        entry_mc = _number(current.get("mc"))
        if entry_mc is None or entry_mc <= 0:
            raise ResearchSafetyError("qualified simulated probe has invalid market cap")
        self.position = {
            "mint": mint,
            "name": current.get("name"),
            "symbol": current.get("symbol"),
            "stage": "PROVING",
            "entry_time_unix": observed_unix,
            "entry_at": observed_at,
            "invested_sol": self.config.probe_sol,
            "basis_denom": self.config.probe_sol / entry_mc,
            "basis_mc": entry_mc,
            "peak_mc": entry_mc,
            "peak_gross": 0.0,
            "mfe_gross": 0.0,
            "adds": 0,
            "derisked": False,
            "remaining_pct": 100.0,
            "missing_polls": 0,
            "thesis_bad_polls": 0,
            "entry_score": score,
            "last_score": score,
        }
        self._decision(
            {
                "observed_at": observed_at,
                "observed_unix": observed_unix,
                "mint": mint,
                "phase": "execution",
                "decision": "SIMULATED_PROBE",
                "amount_sol": self.config.probe_sol,
                "score": score,
                "features": features,
                "entry_mc": entry_mc,
            }
        )
        self.shadow.simulated_open(dict(self.position), entry_mc, observed_unix)

    def _manage_position(
        self,
        mint: str,
        current: dict[str, Any],
        score: float,
        features: dict[str, float],
        track: dict[str, Any],
        observed_unix: float,
        observed_at: str,
    ) -> None:
        assert self.position is not None
        current_mc = _number(current.get("mc"))
        if current_mc is None or current_mc <= 0:
            return
        position = self.position
        position["missing_polls"] = 0
        position["last_score"] = score
        basis_mc = float(position["basis_mc"])
        gross = current_mc / basis_mc - 1.0
        position["peak_mc"] = max(float(position["peak_mc"]), current_mc)
        peak_gross = float(position["peak_mc"]) / basis_mc - 1.0
        position["peak_gross"] = peak_gross
        position["mfe_gross"] = max(float(position["mfe_gross"]), peak_gross)
        position["stage"] = self.bindings.promoted_stage(
            str(position["stage"]), peak_gross
        )
        if score < self.config.thesis_exit_score:
            position["thesis_bad_polls"] = int(position["thesis_bad_polls"]) + 1
        else:
            position["thesis_bad_polls"] = 0

        can_add = (
            int(position["adds"]) < self.config.max_adds
            and float(position["invested_sol"]) + self.config.add_sol
            <= self.config.max_position_sol + 1e-12
            and score >= self.config.add_score
            and gross >= self.config.add_min_gross
            and features.get("buy_ratio", 0.0) >= 0.60
            and features.get("mc_1", 0.0) > 0
        )
        track["add_polls"] = int(track["add_polls"]) + 1 if can_add else 0
        if int(track["add_polls"]) >= self.config.add_confirm_polls:
            invested, denom, new_basis = self.bindings.add_basis(
                float(position["invested_sol"]),
                float(position["basis_denom"]),
                self.config.add_sol,
                current_mc,
            )
            position.update(
                {
                    "invested_sol": invested,
                    "basis_denom": denom,
                    "basis_mc": new_basis,
                    "adds": int(position["adds"]) + 1,
                }
            )
            position["peak_gross"] = float(position["peak_mc"]) / new_basis - 1.0
            position["stage"] = self.bindings.promoted_stage(
                str(position["stage"]), float(position["peak_gross"])
            )
            position["mfe_gross"] = max(
                float(position["mfe_gross"]), float(position["peak_gross"])
            )
            track["add_polls"] = 0
            self._decision(
                {
                    "observed_at": observed_at,
                    "observed_unix": observed_unix,
                    "mint": mint,
                    "phase": "execution",
                    "decision": "SIMULATED_ADD",
                    "amount_sol": self.config.add_sol,
                    "score": score,
                    "movers_mc": current_mc,
                    "new_invested_sol": invested,
                    "new_basis_mc": new_basis,
                }
            )
            self.shadow.simulated_add(
                dict(position), self.config.add_sol, current_mc, observed_unix
            )

        basis_mc = float(position["basis_mc"])
        gross = current_mc / basis_mc - 1.0
        peak_gross = float(position["peak_mc"]) / basis_mc - 1.0
        position["peak_gross"] = peak_gross
        position["stage"] = self.bindings.promoted_stage(
            str(position["stage"]), peak_gross
        )
        if (
            not bool(position["derisked"])
            and gross >= self.config.derisk_at_gross
            and self.config.derisk_sell_pct > 0
        ):
            position["derisked"] = True
            position["remaining_pct"] = max(
                0.0,
                float(position["remaining_pct"]) - self.config.derisk_sell_pct,
            )
            self._decision(
                {
                    "observed_at": observed_at,
                    "observed_unix": observed_unix,
                    "mint": mint,
                    "phase": "execution",
                    "decision": "SIMULATED_DERISK",
                    "sell_pct": self.config.derisk_sell_pct,
                    "gross": gross,
                    "peak_gross": peak_gross,
                }
            )
            self.shadow.simulated_derisk(dict(position), observed_unix)

        held = observed_unix - float(position["entry_time_unix"])
        reason = self.bindings.exit_reason(position, current_mc, score, held)
        self._decision(
            {
                "observed_at": observed_at,
                "observed_unix": observed_unix,
                "mint": mint,
                "phase": "position",
                "decision": "SIMULATED_EXIT" if reason else "HOLD",
                "reason": reason,
                "stage": position["stage"],
                "score": score,
                "gross": gross,
                "peak_gross": peak_gross,
                "held_seconds": held,
                "movers_mc": current_mc,
            }
        )
        if reason:
            self._close_position(
                reason,
                current_mc,
                gross,
                observed_unix,
                observed_at,
            )

    def _close_position(
        self,
        reason: str,
        current_mc: float | None,
        gross: float | None,
        observed_unix: float,
        observed_at: str,
    ) -> None:
        assert self.position is not None
        position = self.position
        mint = str(position["mint"])
        if current_mc is not None:
            self.closed_watch[mint] = {
                "exit_time_unix": observed_unix,
                "entry_mc": float(position["basis_mc"]),
                "exit_mc": current_mc,
                "post_exit_peak_mc": current_mc,
                "reason": reason,
            }
        self.shadow.movers_exit(
            dict(position), reason, current_mc, gross, observed_unix
        )
        self._decision(
            {
                "observed_at": observed_at,
                "observed_unix": observed_unix,
                "mint": mint,
                "phase": "execution",
                "decision": "SIMULATED_CLOSE",
                "reason": reason,
                "movers_mc": current_mc,
                "gross": gross,
                "mfe_gross": position.get("mfe_gross"),
                "invested_sol": position.get("invested_sol"),
                "remaining_pct": position.get("remaining_pct"),
            }
        )
        self.session_closed.add(mint)
        self.position = None
        self.completed += 1


class WebsocketShadowMonitor(ShadowHooks):
    """Observation-only websocket monitor. It has no execution dependency."""

    def __init__(
        self,
        writer: SafeJsonlWriter,
        bindings: StrategyBindings,
        config: StrategyConfig,
        *,
        ws_factory: Callable[[], Any] | None = None,
        max_candidates: int = 32,
    ) -> None:
        if max_candidates < 1:
            raise ResearchSafetyError("max websocket shadow candidates must be >= 1")
        self.writer = writer
        self.bindings = bindings
        self.config = config
        self.ws_factory = ws_factory
        self.max_candidates = max_candidates
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ws: Any | None = None
        self._thread: threading.Thread | None = None
        self._subscribed_on_connection: set[str] = set()
        self._failure: ResearchSafetyError | None = None
        self._candidates: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._latest: dict[str, dict[str, float]] = {}
        self._position: dict[str, Any] | None = None
        self._first_exit: dict[str, Any] | None = None

    def start(self) -> None:
        if self.ws_factory is None:
            raise ResearchSafetyError("websocket shadow requires an explicit public ws factory")
        if self._stop.is_set():
            raise ResearchSafetyError("stopped websocket shadow cannot be restarted")
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="bottotrot-research-shadow",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            ws = self._ws
            subscribed = sorted(self._subscribed_on_connection)
        if ws is not None:
            if subscribed:
                try:
                    ws.send(
                        json.dumps(
                            {
                                "method": "unsubscribeTokenTrade",
                                "keys": subscribed,
                            },
                            separators=(",", ":"),
                        )
                    )
                except Exception:
                    pass
            try:
                ws.settimeout(0.5)
            except Exception:
                pass
            self._close_connection(ws)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=12.0)
            if thread.is_alive():
                raise ResearchSafetyError(
                    "websocket shadow thread did not stop within its bounded timeout"
                )
        with self._lock:
            self._thread = None
            self._ws = None
            self._subscribed_on_connection.clear()

    def raise_if_failed(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure

    def _set_failure(self, failure: ResearchSafetyError) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = failure
        self._stop.set()

    @staticmethod
    def _close_connection(ws: Any) -> None:
        try:
            ws.close(timeout=1.0)
        except TypeError:
            try:
                ws.close()
            except Exception:
                pass
        except Exception:
            pass

    def _emit(self, event: dict[str, Any]) -> None:
        self.writer.write({"type": "websocket_shadow", **event})

    def _subscribe(self, mint: str) -> None:
        with self._lock:
            ws = self._ws
            if ws is None or mint in self._subscribed_on_connection:
                return
            ws.send(
                json.dumps(
                    {"method": "subscribeTokenTrade", "keys": [mint]},
                    separators=(",", ":"),
                )
            )
            self._subscribed_on_connection.add(mint)

    def _unsubscribe(self, mint: str) -> None:
        with self._lock:
            ws = self._ws
            if ws is None or mint not in self._subscribed_on_connection:
                return
            self._subscribed_on_connection.discard(mint)
            ws.send(
                json.dumps(
                    {"method": "unsubscribeTokenTrade", "keys": [mint]},
                    separators=(",", ":"),
                )
            )

    def bond_candidate(
        self,
        mint: str,
        bond_mc: float | None,
        observed_unix: float,
    ) -> None:
        evicted: str | None = None
        with self._lock:
            if mint in self._candidates:
                self._candidates.move_to_end(mint)
            else:
                self._candidates[mint] = {
                    "bond_mc": bond_mc,
                    "bond_unix": observed_unix,
                }
            if len(self._candidates) > self.max_candidates:
                held_mint = (
                    str(self._position["mint"]) if self._position is not None else None
                )
                for candidate in list(self._candidates):
                    if candidate != held_mint:
                        evicted = candidate
                        del self._candidates[candidate]
                        break
            kept = mint in self._candidates
        if evicted:
            self._unsubscribe(evicted)
            self._emit(
                {
                    "event": "candidate_unsubscribed",
                    "observed_at": iso_from_unix(observed_unix),
                    "observed_unix": observed_unix,
                    "mint": evicted,
                    "reason": "candidate_cap",
                }
            )
        if kept:
            self._subscribe(mint)
        self._emit(
            {
                "event": (
                    "candidate_subscribed" if kept else "candidate_not_subscribed"
                ),
                "observed_at": iso_from_unix(observed_unix),
                "observed_unix": observed_unix,
                "mint": mint,
                "bond_mc": bond_mc,
            }
        )

    def simulated_open(
        self,
        position: dict[str, Any],
        movers_mc: float,
        observed_unix: float,
    ) -> None:
        mint = str(position["mint"])
        with self._lock:
            latest = self._latest.get(mint)
            self._first_exit = None
            self._position = {
                "mint": mint,
                "entry_movers_mc": movers_mc,
                "entry_unix": observed_unix,
                "invested_sol": float(position["invested_sol"]),
                "basis_denom_ws": 0.0,
                "basis_ws": None,
                "movers_per_ws": None,
                "peak_ws": None,
                "stage": "PROVING",
                "derisked": False,
            }
            if latest is not None:
                self._calibrate_locked(float(latest["mcap_sol"]), observed_unix)
        self._subscribe(mint)
        self._emit(
            {
                "event": "simulated_position_armed",
                "observed_at": iso_from_unix(observed_unix),
                "observed_unix": observed_unix,
                "mint": mint,
                "entry_movers_mc": movers_mc,
                "calibrated": latest is not None,
            }
        )

    def _calibrate_locked(self, mcap_sol: float, observed_unix: float) -> None:
        assert self._position is not None
        invested = float(self._position["invested_sol"])
        self._position["basis_denom_ws"] = invested / mcap_sol
        self._position["basis_ws"] = mcap_sol
        self._position["movers_per_ws"] = (
            float(self._position["entry_movers_mc"]) / mcap_sol
        )
        self._position["peak_ws"] = mcap_sol
        self._position["calibrated_unix"] = observed_unix

    def simulated_add(
        self,
        position: dict[str, Any],
        add_sol: float,
        movers_mc: float,
        observed_unix: float,
    ) -> None:
        mint = str(position["mint"])
        with self._lock:
            if self._position is None or self._position["mint"] != mint:
                return
            latest = self._latest.get(mint)
            if latest is None or float(self._position["basis_denom_ws"]) <= 0:
                self._position["invested_sol"] = float(position["invested_sol"])
                self._emit(
                    {
                        "event": "simulated_add_uncalibrated",
                        "observed_at": iso_from_unix(observed_unix),
                        "observed_unix": observed_unix,
                        "mint": mint,
                        "add_sol": add_sol,
                    }
                )
                return
            mcap_sol = float(latest["mcap_sol"])
            invested, denom, basis = self.bindings.add_basis(
                float(self._position["invested_sol"]),
                float(self._position["basis_denom_ws"]),
                add_sol,
                mcap_sol,
            )
            self._position.update(
                {
                    "invested_sol": invested,
                    "basis_denom_ws": denom,
                    "basis_ws": basis,
                }
            )
        self._emit(
            {
                "event": "simulated_add_noted",
                "observed_at": iso_from_unix(observed_unix),
                "observed_unix": observed_unix,
                "mint": mint,
                "add_sol": add_sol,
                "movers_mc": movers_mc,
                "ws_market_cap_sol": mcap_sol,
            }
        )

    def simulated_derisk(
        self,
        position: dict[str, Any],
        observed_unix: float,
    ) -> None:
        mint = str(position["mint"])
        with self._lock:
            if self._position is not None and self._position["mint"] == mint:
                self._position["derisked"] = True

    def handle_event(
        self,
        event: dict[str, Any],
        *,
        observed_unix: float | None = None,
    ) -> None:
        observed_unix = time.time() if observed_unix is None else observed_unix
        mint = str(event.get("mint") or "")
        mcap_sol = _number(event.get("marketCapSol"))
        if not mint or mcap_sol is None or mcap_sol <= 0:
            return
        exit_event: dict[str, Any] | None = None
        with self._lock:
            if mint not in self._candidates and (
                self._position is None or self._position["mint"] != mint
            ):
                return
            self._latest[mint] = {
                "mcap_sol": mcap_sol,
                "observed_unix": observed_unix,
            }
            position = self._position
            if position is None or position["mint"] != mint:
                position_snapshot = None
            else:
                if float(position["basis_denom_ws"]) <= 0:
                    self._calibrate_locked(mcap_sol, observed_unix)
                basis_ws = float(position["invested_sol"]) / float(
                    position["basis_denom_ws"]
                )
                position["basis_ws"] = basis_ws
                peak_ws = max(float(position["peak_ws"] or mcap_sol), mcap_sol)
                position["peak_ws"] = peak_ws
                gross = mcap_sol / basis_ws - 1.0
                peak_gross = peak_ws / basis_ws - 1.0
                position["stage"] = self.bindings.promoted_stage(
                    str(position["stage"]), peak_gross
                )
                position_snapshot = {
                    "stage": position["stage"],
                    "gross": gross,
                    "peak_gross": peak_gross,
                    "basis_ws": basis_ws,
                    "movers_per_ws": position["movers_per_ws"],
                }
                reason = self.bindings.fast_price_exit_reason(
                    str(position["stage"]), gross, peak_gross
                )
                if reason and self._first_exit is None:
                    estimated_movers_mc = (
                        mcap_sol * float(position["movers_per_ws"])
                        if position["movers_per_ws"] is not None
                        else None
                    )
                    self._first_exit = {
                        "reason": reason,
                        "observed_unix": observed_unix,
                        "mcap_sol": mcap_sol,
                        "estimated_movers_mc": estimated_movers_mc,
                        "gross": gross,
                        "peak_gross": peak_gross,
                        "stage": position["stage"],
                    }
                    exit_event = dict(self._first_exit)
                else:
                    exit_event = None
        self._emit(
            {
                "event": "trade_tick",
                "observed_at": iso_from_unix(observed_unix),
                "observed_unix": observed_unix,
                "mint": mint,
                "market_cap_sol": mcap_sol,
                "position": position_snapshot,
            }
        )
        if exit_event is not None:
            self._emit(
                {
                    "event": "websocket_shadow_exit_decision",
                    "observed_at": iso_from_unix(observed_unix),
                    "mint": mint,
                    **exit_event,
                    "action": "LOG_ONLY_NO_SELL",
                }
            )

    def movers_exit(
        self,
        position: dict[str, Any],
        reason: str,
        movers_mc: float | None,
        movers_gross: float | None,
        observed_unix: float,
    ) -> None:
        mint = str(position["mint"])
        with self._lock:
            shadow = dict(self._first_exit) if self._first_exit is not None else None
            self._position = None
            self._first_exit = None
        comparison: dict[str, Any] = {
            "event": "exit_comparison",
            "observed_at": iso_from_unix(observed_unix),
            "observed_unix": observed_unix,
            "mint": mint,
            "movers_reason": reason,
            "movers_mc": movers_mc,
            "movers_gross": movers_gross,
        }
        if shadow is None:
            comparison.update(
                {
                    "status": "no_websocket_exit_before_movers",
                    "websocket_reason": None,
                    "latency_seconds": None,
                    "estimated_price_difference": None,
                    "gross_difference_bps": None,
                }
            )
        else:
            estimated = _number(shadow.get("estimated_movers_mc"))
            price_difference = (
                movers_mc - estimated
                if movers_mc is not None and estimated is not None
                else None
            )
            comparison.update(
                {
                    "status": "websocket_first_or_same_time",
                    "websocket_reason": shadow["reason"],
                    "websocket_observed_unix": shadow["observed_unix"],
                    "websocket_market_cap_sol": shadow["mcap_sol"],
                    "websocket_estimated_movers_mc": estimated,
                    "websocket_gross": shadow["gross"],
                    "latency_seconds": observed_unix
                    - float(shadow["observed_unix"]),
                    "estimated_price_difference": price_difference,
                    "estimated_price_difference_pct": (
                        price_difference / estimated
                        if price_difference is not None and estimated not in {None, 0.0}
                        else None
                    ),
                    "gross_difference_bps": (
                        (movers_gross - float(shadow["gross"])) * 10_000.0
                        if movers_gross is not None
                        else None
                    ),
                }
            )
        self._emit(comparison)

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            ws = None
            try:
                assert self.ws_factory is not None
                ws = self.ws_factory()
                ws.settimeout(1.0)
                with self._lock:
                    self._ws = ws
                    self._subscribed_on_connection.clear()
                    mints = list(self._candidates)
                if self._stop.is_set():
                    continue
                for mint in mints:
                    self._subscribe(mint)
                self._emit(
                    {
                        "event": "connected",
                        "observed_at": iso_from_unix(time.time()),
                        "transport": "public_websocket_no_credentials",
                    }
                )
                backoff = 1.0
                while not self._stop.is_set():
                    try:
                        raw = ws.recv()
                    except Exception as exc:
                        if exc.__class__.__name__ == "WebSocketTimeoutException":
                            continue
                        raise
                    if not raw:
                        raise RuntimeError("websocket closed")
                    try:
                        event = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(event, dict):
                        self.handle_event(event)
            except ResearchSafetyError as exc:
                self._set_failure(exc)
                break
            except Exception as exc:
                if not self._stop.is_set():
                    try:
                        self._emit(
                            {
                                "event": "reconnect_wait",
                                "observed_at": iso_from_unix(time.time()),
                                "error_type": exc.__class__.__name__,
                                "retry_seconds": backoff,
                            }
                        )
                    except ResearchSafetyError as failure:
                        self._set_failure(failure)
                        break
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2.0, 15.0)
            finally:
                with self._lock:
                    if self._ws is ws:
                        self._ws = None
                    self._subscribed_on_connection.clear()
                if ws is not None:
                    self._close_connection(ws)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReplayFormatError("duplicate JSON object key in replay input")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ReplayFormatError("non-finite JSON number in replay input")


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    previous_index = 0
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise ReplayFormatError(
            f"cannot open replay input ({exc.__class__.__name__}, errno={exc.errno})"
        ) from None
    with fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                raise ReplayFormatError(
                    f"blank replay record at line {line_number}"
                )
            try:
                event = json.loads(
                    line,
                    object_pairs_hook=_pairs_without_duplicates,
                    parse_constant=_reject_json_constant,
                )
            except ReplayFormatError:
                raise
            except ValueError as exc:
                raise ReplayFormatError(
                    f"invalid replay JSON at line {line_number}"
                ) from exc
            if not isinstance(event, dict) or event.get("schema") != SCHEMA:
                raise ReplayFormatError(
                    f"unsupported replay record at line {line_number}"
                )
            index = event.get("event_index")
            if not isinstance(index, int) or index <= previous_index:
                raise ReplayFormatError(
                    f"non-monotonic event_index at line {line_number}"
                )
            previous_index = index
            _assert_secret_free(event)
            yield event
