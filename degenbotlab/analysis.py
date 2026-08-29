from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


RESEARCH_SCHEMA = "bottotrot.research.v1"
REPORT_SCHEMA = "bottotrot.lab.report.v1"
COMPARISON_SCHEMA = "bottotrot.lab.comparison.v1"
ANALYSIS_VERSION = "bottotrot.experiment-analysis.v1"
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
MIN_FREE_DISK_BYTES = 256 * 1024 * 1024
CATASTROPHIC_TAIL_RETURN_THRESHOLD = -0.50
MILESTONES = (0.25, 0.50, 1.00, 3.00)
MILESTONE_LABELS = {
    0.25: "+25%",
    0.50: "+50%",
    1.00: "+100%",
    3.00: "+300%",
}
ALLOWED_RECORD_TYPES = {
    "session",
    "movers_snapshot",
    "strategy_decision",
    "websocket_shadow",
    "observer_status",
    "summary",
}
ALLOWED_DECISIONS = {
    ("bond_gate", "OPEN"),
    ("bond_opportunity", "MARK"),
    ("missed_opportunity", "MILESTONE"),
    ("entry", "QUALIFY"),
    ("entry", "REJECT"),
    ("execution", "SIMULATED_PROBE"),
    ("execution", "SIMULATED_ADD"),
    ("execution", "SIMULATED_DERISK"),
    ("execution", "SIMULATED_CLOSE"),
    ("position", "MISSING"),
    ("position", "HOLD"),
    ("position", "SIMULATED_EXIT"),
    ("post_exit", "RESULT"),
}
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
    "authorization: bearer",
    "bearer ",
    "mnemonic=",
    "private_key=",
    "secret=",
)
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class LabSafetyError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _assert_secret_free(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                raise LabSafetyError("sensitive-looking field is not valid laboratory data")
            _assert_secret_free(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_secret_free(item)
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in SENSITIVE_VALUE_MARKERS):
            raise LabSafetyError("sensitive-looking value is not valid laboratory data")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LabSafetyError("duplicate JSON object key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise LabSafetyError("non-finite JSON number")


def _load_json(text: str, context: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except LabSafetyError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise LabSafetyError(
            f"malformed {context} ({exc.__class__.__name__})"
        ) from None
    if not isinstance(value, dict):
        raise LabSafetyError(f"{context} must be a JSON object")
    _assert_secret_free(value)
    return value


def _number(value: Any, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LabSafetyError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise LabSafetyError(f"{label} must be a finite number")
    if minimum is not None and result < minimum:
        raise LabSafetyError(f"{label} is outside its valid range")
    return result


def _optional_number(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
) -> float | None:
    if value is None:
        return None
    return _number(value, label, minimum=minimum)


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LabSafetyError(f"{label} must be an integer >= {minimum}")
    return value


def _iso_timestamp(value: Any, label: str) -> float:
    if not isinstance(value, str) or not value:
        raise LabSafetyError(f"{label} must be a timezone-aware timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise LabSafetyError(f"{label} is not a valid ISO timestamp") from None
    if parsed.tzinfo is None:
        raise LabSafetyError(f"{label} must include a timezone")
    result = parsed.timestamp()
    if not math.isfinite(result) or result < 0:
        raise LabSafetyError(f"{label} is outside its valid range")
    return result


def _event_time(event: dict[str, Any], label: str) -> float | None:
    value = event.get("observed_unix")
    if value is None:
        return None
    result = _number(value, f"{label}.observed_unix", minimum=0.0)
    observed_at = event.get("observed_at")
    if observed_at is not None:
        encoded = _iso_timestamp(observed_at, f"{label}.observed_at")
        if abs(encoded - result) > 0.001:
            raise LabSafetyError(f"{label} timestamp fields disagree")
    return result


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 12)


def _mean(values: list[float]) -> float | None:
    return _rounded(statistics.fmean(values)) if values else None


def _median(values: list[float]) -> float | None:
    return _rounded(float(statistics.median(values))) if values else None


def _minimum(values: list[float]) -> float | None:
    return _rounded(min(values)) if values else None


def _maximum(values: list[float]) -> float | None:
    return _rounded(max(values)) if values else None


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _sha256_file(path: Path, max_bytes: int) -> tuple[str, int]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise LabSafetyError(
            f"cannot inspect input ({exc.__class__.__name__}, errno={exc.errno})"
        ) from None
    if size <= 0:
        raise LabSafetyError("input is empty")
    if size > max_bytes:
        raise LabSafetyError("input exceeds the laboratory size limit")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while True:
                block = fh.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise LabSafetyError(
            f"cannot read input ({exc.__class__.__name__}, errno={exc.errno})"
        ) from None
    return digest.hexdigest(), size


@dataclass
class _TradeState:
    trade_index: int
    mint: str
    entry_event_index: int
    entry_unix: float | None
    entry_mc: float | None
    invested_sol: float | None
    bond_unix: float | None
    adds: int = 0
    add_event_indexes: list[int] = field(default_factory=list)
    derisks: list[tuple[int, float | None, float | None]] = field(
        default_factory=list
    )
    gross_samples: list[float] = field(default_factory=lambda: [0.0])
    peak_samples: list[float] = field(default_factory=lambda: [0.0])
    stages: set[str] = field(default_factory=lambda: {"PROVING"})


class _RecordingAnalyzer:
    def __init__(self) -> None:
        self.session: dict[str, Any] | None = None
        self.summary: dict[str, Any] | None = None
        self.expected_event_index = 1
        self.last_record_type: str | None = None
        self.snapshot_count = 0
        self.decision_count = 0
        self.recorded_decision_digest = hashlib.sha256()
        self.last_snapshot_sequence = 0
        self.snapshot_times: dict[int, float] = {}
        self.first_snapshot_unix: float | None = None
        self.last_snapshot_unix: float | None = None
        self.last_snapshot_monotonic: float | None = None
        self.observation_digest = hashlib.sha256()
        self.observed_mints: set[str] = set()
        self.bond_mints: set[str] = set()
        self.bond_events = 0
        self.bond_times: dict[str, float] = {}
        self.qualified_mints: set[str] = set()
        self.qualification_events = 0
        self.rejections: Counter[str] = Counter()
        self.milestones: Counter[str] = Counter()
        self.probes = 0
        self.adds = 0
        self.derisk_events = 0
        self.completed: list[dict[str, Any]] = []
        self.active: _TradeState | None = None
        self.exit_reasons: Counter[str] = Counter()
        self.websocket_statuses: Counter[str] = Counter()
        self.websocket_shadow_exits = 0
        self.websocket_comparisons = 0
        self.websocket_latencies: list[float] = []
        self.websocket_price_differences: list[float] = []
        self.websocket_gross_differences: list[float] = []
        self.missing: Counter[str] = Counter()

    def consume(self, event: dict[str, Any], line_number: int) -> None:
        if event.get("schema") != RESEARCH_SCHEMA:
            raise LabSafetyError(f"incompatible schema at line {line_number}")
        index = _integer(event.get("event_index"), "event_index", minimum=1)
        if index != self.expected_event_index:
            raise LabSafetyError("event_index must be contiguous from 1")
        self.expected_event_index += 1
        record_type = event.get("type")
        if record_type not in ALLOWED_RECORD_TYPES:
            raise LabSafetyError("unknown research record type")
        if self.summary is not None:
            raise LabSafetyError("summary must be the final record")
        if self.session is None and record_type != "session":
            raise LabSafetyError("session must be the first record")
        if record_type == "session":
            self._session(event)
        elif record_type == "movers_snapshot":
            self._snapshot(event)
        elif record_type == "strategy_decision":
            self._decision(event)
        elif record_type == "websocket_shadow":
            self._websocket(event)
        elif record_type == "observer_status":
            self._observer_status(event)
        elif record_type == "summary":
            self._summary(event)
        self.last_record_type = str(record_type)

    def _session(self, event: dict[str, Any]) -> None:
        if self.session is not None:
            raise LabSafetyError("recording contains multiple sessions")
        mode = event.get("mode")
        if mode not in {"record", "shadow"}:
            raise LabSafetyError("analyze accepts record or shadow recordings only")
        fingerprint = event.get("strategy_fingerprint")
        if not isinstance(fingerprint, str) or not HEX_64.fullmatch(fingerprint):
            raise LabSafetyError("strategy fingerprint must be lowercase SHA-256")
        if not isinstance(event.get("branch"), str) or not event["branch"]:
            raise LabSafetyError("session branch is missing")
        if not isinstance(event.get("strategy"), str) or not event["strategy"]:
            raise LabSafetyError("session strategy identifier is missing")
        if not isinstance(event.get("strategy_config"), dict):
            raise LabSafetyError("session strategy configuration is missing")
        _iso_timestamp(event.get("started_at"), "session.started_at")
        if event.get("execution") != "simulated_only" or event.get("transactions") != "blocked":
            raise LabSafetyError("recording is not marked simulated-only and transaction-blocked")
        self.session = event

    def _snapshot(self, event: dict[str, Any]) -> None:
        sequence = _integer(
            event.get("snapshot_sequence"),
            "snapshot_sequence",
            minimum=1,
        )
        if sequence != self.last_snapshot_sequence + 1:
            raise LabSafetyError("snapshot_sequence must be contiguous from 1")
        observed = _number(event.get("observed_unix"), "snapshot time", minimum=0.0)
        observed_at = _iso_timestamp(event.get("observed_at"), "snapshot observed_at")
        if abs(observed_at - observed) > 0.001:
            raise LabSafetyError("snapshot timestamp fields disagree")
        monotonic = _number(
            event.get("observed_monotonic"),
            "snapshot monotonic time",
            minimum=0.0,
        )
        if self.last_snapshot_unix is not None and observed < self.last_snapshot_unix:
            raise LabSafetyError("snapshot wall-clock time moved backwards")
        if (
            self.last_snapshot_monotonic is not None
            and monotonic < self.last_snapshot_monotonic
        ):
            raise LabSafetyError("snapshot monotonic time moved backwards")
        entries = event.get("entries")
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            raise LabSafetyError("snapshot entries must be a list of objects")
        for item in entries:
            mint = item.get("mint")
            if isinstance(mint, str) and mint:
                self.observed_mints.add(mint)
            else:
                self.missing["snapshot_entry_mint"] += 1
        observation = {
            "snapshot_sequence": sequence,
            "observed_unix": observed,
            "observed_monotonic": monotonic,
            "entries": entries,
        }
        self.observation_digest.update(_canonical(observation))
        self.observation_digest.update(b"\n")
        self.snapshot_count += 1
        self.last_snapshot_sequence = sequence
        self.snapshot_times[sequence] = observed
        self.first_snapshot_unix = (
            observed if self.first_snapshot_unix is None else self.first_snapshot_unix
        )
        self.last_snapshot_unix = observed
        self.last_snapshot_monotonic = monotonic

    def _decision(self, event: dict[str, Any]) -> None:
        self.decision_count += 1
        sequence = _integer(
            event.get("snapshot_sequence"),
            "decision snapshot_sequence",
            minimum=1,
        )
        if sequence > self.last_snapshot_sequence:
            raise LabSafetyError("decision refers to a future snapshot")
        mint = event.get("mint")
        if not isinstance(mint, str) or not mint:
            raise LabSafetyError("strategy decision mint is missing")
        if mint not in self.observed_mints:
            raise LabSafetyError("strategy decision mint was never observed")
        when = _event_time(event, "strategy decision")
        if when is not None and abs(when - self.snapshot_times[sequence]) > 0.001:
            raise LabSafetyError("strategy decision time differs from its snapshot")
        phase = event.get("phase")
        decision = event.get("decision")
        if not isinstance(phase, str) or not isinstance(decision, str):
            raise LabSafetyError("strategy decision phase or decision is missing")
        if (phase, decision) not in ALLOWED_DECISIONS:
            raise LabSafetyError("unknown strategy decision shape")
        clean = {
            key: value
            for key, value in event.items()
            if key not in {"schema", "event_index"}
        }
        self.recorded_decision_digest.update(_canonical(clean))
        self.recorded_decision_digest.update(b"\n")

        if phase == "bond_gate" and decision == "OPEN":
            if mint in self.bond_mints:
                raise LabSafetyError("mint contains multiple observed bond-open events")
            self.bond_events += 1
            self.bond_mints.add(mint)
            if when is not None:
                self.bond_times.setdefault(mint, when)
            else:
                self.missing["bond_event_time"] += 1
            return
        if phase == "entry":
            if decision == "QUALIFY":
                if mint not in self.bond_mints:
                    raise LabSafetyError("entry qualified before an observed bond")
                self.qualification_events += 1
                self.qualified_mints.add(mint)
            elif decision == "REJECT":
                reason = event.get("rejection")
                if isinstance(reason, str) and reason:
                    self.rejections[reason] += 1
                else:
                    self.missing["candidate_rejection_reason"] += 1
            return
        if phase == "missed_opportunity" and decision == "MILESTONE":
            if mint not in self.bond_mints:
                raise LabSafetyError("opportunity milestone precedes observed bond")
            milestone = _number(event.get("milestone"), "opportunity milestone")
            matched = next(
                (item for item in MILESTONES if abs(milestone - item) < 1e-12),
                None,
            )
            if matched is None:
                raise LabSafetyError("recording contains an unknown opportunity milestone")
            self.milestones[MILESTONE_LABELS[matched]] += 1
            return
        if phase == "execution" and decision == "SIMULATED_PROBE":
            self._probe(event, mint, when)
            return
        if phase == "execution" and decision == "SIMULATED_ADD":
            self._add(event, mint)
            return
        if phase == "execution" and decision == "SIMULATED_DERISK":
            self._derisk(event, mint)
            return
        if phase == "position" and decision in {"HOLD", "SIMULATED_EXIT"}:
            self._position_observation(event, mint)
            return
        if phase == "position" and decision == "MISSING":
            self._require_active(mint, "missing-position observation")
            return
        if phase == "execution" and decision == "SIMULATED_CLOSE":
            self._close(event, mint, when)

    def _probe(self, event: dict[str, Any], mint: str, when: float | None) -> None:
        if self.active is not None:
            raise LabSafetyError("multiple simultaneous simulated positions")
        if mint not in self.bond_mints or mint not in self.qualified_mints:
            raise LabSafetyError("simulated probe lacks bonded entry qualification")
        self.probes += 1
        entry_mc = _optional_number(event.get("entry_mc"), "probe entry_mc", minimum=0.0)
        if entry_mc == 0.0:
            raise LabSafetyError("probe entry_mc must be positive")
        invested = _optional_number(event.get("amount_sol"), "probe amount_sol", minimum=0.0)
        if invested == 0.0:
            raise LabSafetyError("probe amount_sol must be positive")
        self.active = _TradeState(
            trade_index=self.probes,
            mint=mint,
            entry_event_index=int(event["event_index"]),
            entry_unix=when,
            entry_mc=entry_mc,
            invested_sol=invested,
            bond_unix=self.bond_times.get(mint),
        )

    def _require_active(self, mint: str, action: str) -> _TradeState:
        if self.active is None or self.active.mint != mint:
            raise LabSafetyError(f"{action} has no matching simulated position")
        return self.active

    def _add(self, event: dict[str, Any], mint: str) -> None:
        trade = self._require_active(mint, "simulated add")
        self.adds += 1
        trade.adds += 1
        trade.add_event_indexes.append(int(event["event_index"]))
        invested = _optional_number(
            event.get("new_invested_sol"),
            "add new_invested_sol",
            minimum=0.0,
        )
        if invested is None:
            self.missing["trade_invested_sol"] += 1
        elif invested == 0.0:
            raise LabSafetyError("add new_invested_sol must be positive")
        else:
            trade.invested_sol = invested

    def _derisk(self, event: dict[str, Any], mint: str) -> None:
        trade = self._require_active(mint, "simulated derisk")
        self.derisk_events += 1
        gross = _optional_number(event.get("gross"), "derisk gross")
        sell_pct = _optional_number(
            event.get("sell_pct"),
            "derisk sell_pct",
            minimum=0.0,
        )
        if sell_pct is not None and sell_pct > 100.0:
            raise LabSafetyError("derisk sell_pct exceeds 100")
        if gross is None:
            self.missing["derisk_return"] += 1
        if sell_pct is None:
            self.missing["derisk_sell_pct"] += 1
        if gross is not None:
            trade.gross_samples.append(gross)
        trade.derisks.append((int(event["event_index"]), sell_pct, gross))

    def _position_observation(self, event: dict[str, Any], mint: str) -> None:
        trade = self._require_active(mint, "position observation")
        gross = _optional_number(event.get("gross"), "position gross")
        peak = _optional_number(event.get("peak_gross"), "position peak_gross")
        stage = event.get("stage")
        if gross is None:
            self.missing["position_gross"] += 1
        else:
            trade.gross_samples.append(gross)
        if peak is None:
            self.missing["position_peak_gross"] += 1
        else:
            trade.peak_samples.append(peak)
        if isinstance(stage, str) and stage in {"PROVING", "RUNNER", "PARABOLIC", "MOON"}:
            trade.stages.add(stage)
        else:
            self.missing["position_stage"] += 1

    def _close(self, event: dict[str, Any], mint: str, when: float | None) -> None:
        trade = self._require_active(mint, "simulated close")
        reason = event.get("reason")
        if not isinstance(reason, str) or not reason:
            self.missing["exit_reason"] += 1
            reason_value: str | None = None
        else:
            reason_value = reason
            self.exit_reasons[reason] += 1
        gross = _optional_number(event.get("gross"), "close gross")
        mfe = _optional_number(event.get("mfe_gross"), "close mfe_gross")
        invested = _optional_number(
            event.get("invested_sol"),
            "close invested_sol",
            minimum=0.0,
        )
        remaining_pct = _optional_number(
            event.get("remaining_pct"),
            "close remaining_pct",
            minimum=0.0,
        )
        if remaining_pct is not None and remaining_pct > 100.0:
            raise LabSafetyError("close remaining_pct exceeds 100")
        if gross is not None:
            trade.gross_samples.append(gross)
        if mfe is not None:
            trade.peak_samples.append(mfe)
        if invested is not None:
            if invested == 0.0:
                raise LabSafetyError("close invested_sol must be positive")
            trade.invested_sol = invested
        realized_return = self._realized_return(trade, gross, remaining_pct)
        hold_seconds: float | None = None
        if trade.entry_unix is None or when is None:
            self.missing["trade_hold_duration"] += 1
        else:
            hold_seconds = when - trade.entry_unix
            if hold_seconds < 0:
                raise LabSafetyError("simulated close precedes simulated probe")
        bond_latency: float | None = None
        if trade.bond_unix is None or trade.entry_unix is None:
            self.missing["bond_to_entry_latency"] += 1
        else:
            bond_latency = trade.entry_unix - trade.bond_unix
            if bond_latency < 0:
                raise LabSafetyError("simulated probe precedes observed bond")
        observed_mfe = max(trade.peak_samples + trade.gross_samples)
        observed_mae = min(trade.gross_samples)
        giveback = observed_mfe - gross if gross is not None else None
        if giveback is None:
            self.missing["peak_to_exit_giveback"] += 1
        if realized_return is None:
            self.missing["trade_return"] += 1
        if trade.invested_sol is None:
            self.missing["trade_invested_sol"] += 1
        self.completed.append(
            {
                "trade_index": trade.trade_index,
                "mint": trade.mint,
                "entry_event_index": trade.entry_event_index,
                "exit_event_index": int(event["event_index"]),
                "exit_reason": reason_value,
                "adds": trade.adds,
                "derisk_events": len(trade.derisks),
                "runner_converted": bool(
                    trade.stages.intersection({"RUNNER", "PARABOLIC", "MOON"})
                ),
                "realized_return": _rounded(realized_return),
                "invested_sol": _rounded(trade.invested_sol),
                "simulated_pnl_sol": (
                    _rounded(trade.invested_sol * realized_return)
                    if trade.invested_sol is not None and realized_return is not None
                    else None
                ),
                "maximum_favorable_excursion": _rounded(observed_mfe),
                "maximum_adverse_excursion": _rounded(observed_mae),
                "hold_duration_seconds": _rounded(hold_seconds),
                "peak_to_exit_giveback": _rounded(giveback),
                "bond_to_entry_latency_seconds": _rounded(bond_latency),
            }
        )
        self.active = None

    def _realized_return(
        self,
        trade: _TradeState,
        close_gross: float | None,
        close_remaining_pct: float | None,
    ) -> float | None:
        if close_gross is None:
            return None
        if trade.derisks and trade.add_event_indexes:
            first_derisk = trade.derisks[0][0]
            if any(index > first_derisk for index in trade.add_event_indexes):
                self.missing["return_add_after_derisk"] += 1
                return None
        remaining = 1.0
        realized = 0.0
        for _index, sell_pct, gross in trade.derisks:
            if sell_pct is None or gross is None:
                return None
            fraction = sell_pct / 100.0
            if fraction > remaining + 1e-12:
                raise LabSafetyError("cumulative derisk percentage exceeds open position")
            realized += fraction * gross
            remaining -= fraction
        if close_remaining_pct is None:
            self.missing["close_remaining_pct"] += 1
            return None
        recorded_remaining = close_remaining_pct / 100.0
        if abs(recorded_remaining - remaining) > 1e-9:
            raise LabSafetyError("close remaining_pct disagrees with derisk history")
        return realized + remaining * close_gross

    def _websocket(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        if not isinstance(name, str) or not name:
            raise LabSafetyError("websocket event name is missing")
        if event.get("observed_at") is not None:
            observed = _iso_timestamp(event["observed_at"], "websocket observed_at")
            if event.get("observed_unix") is not None:
                unix = _number(
                    event["observed_unix"],
                    "websocket observed_unix",
                    minimum=0.0,
                )
                if abs(observed - unix) > 0.001:
                    raise LabSafetyError("websocket timestamp fields disagree")
        if name == "websocket_shadow_exit_decision":
            self.websocket_shadow_exits += 1
            if event.get("action") != "LOG_ONLY_NO_SELL":
                raise LabSafetyError("websocket exit event is not marked log-only")
        if name != "exit_comparison":
            return
        self.websocket_comparisons += 1
        status = event.get("status")
        if isinstance(status, str) and status:
            self.websocket_statuses[status] += 1
        else:
            self.missing["websocket_comparison_status"] += 1
        latency = _optional_number(
            event.get("latency_seconds"),
            "websocket comparison latency",
            minimum=0.0,
        )
        price_difference = _optional_number(
            event.get("estimated_price_difference_pct"),
            "websocket estimated price difference",
        )
        gross_difference = _optional_number(
            event.get("gross_difference_bps"),
            "websocket gross difference",
        )
        if latency is None:
            self.missing["websocket_exit_latency"] += 1
        else:
            self.websocket_latencies.append(latency)
        if price_difference is None:
            self.missing["websocket_price_difference_pct"] += 1
        else:
            self.websocket_price_differences.append(price_difference)
        if gross_difference is None:
            self.missing["websocket_gross_difference_bps"] += 1
        else:
            self.websocket_gross_differences.append(gross_difference)

    def _observer_status(self, event: dict[str, Any]) -> None:
        if not isinstance(event.get("event"), str):
            raise LabSafetyError("observer status event name is missing")
        if event.get("observed_at") is not None:
            _iso_timestamp(event["observed_at"], "observer status observed_at")

    def _summary(self, event: dict[str, Any]) -> None:
        assert self.session is not None
        if event.get("mode") != self.session.get("mode"):
            raise LabSafetyError("summary mode differs from session mode")
        if _integer(event.get("snapshots"), "summary snapshots") != self.snapshot_count:
            raise LabSafetyError("summary snapshot count disagrees with recording")
        if _integer(event.get("decisions"), "summary decisions") != self.decision_count:
            raise LabSafetyError("summary decision count disagrees with recording")
        decision_digest = event.get("decision_digest")
        if (
            not isinstance(decision_digest, str)
            or not HEX_64.fullmatch(decision_digest)
            or decision_digest != self.recorded_decision_digest.hexdigest()
        ):
            raise LabSafetyError("summary decision digest disagrees with recording")
        if event.get("transactions_submitted") != 0:
            raise LabSafetyError("summary is not explicitly zero-transaction")
        completed = _integer(
            event.get("simulated_completed_positions"),
            "summary completed positions",
        )
        if completed != len(self.completed):
            raise LabSafetyError("summary completed count disagrees with decisions")
        open_position = event.get("open_simulated_position")
        if open_position is None:
            if self.active is not None:
                raise LabSafetyError("summary omits the active simulated position")
        elif not isinstance(open_position, dict):
            raise LabSafetyError("summary open position must be an object or null")
        else:
            mint = open_position.get("mint")
            if self.active is None or mint != self.active.mint:
                raise LabSafetyError("summary open position disagrees with decisions")
            mfe = _optional_number(open_position.get("mfe_gross"), "open position mfe")
            peak = _optional_number(open_position.get("peak_gross"), "open position peak")
            if mfe is not None:
                self.active.peak_samples.append(mfe)
            if peak is not None:
                self.active.peak_samples.append(peak)
            stage = open_position.get("stage")
            if isinstance(stage, str):
                self.active.stages.add(stage)
        finished = _iso_timestamp(event.get("finished_at"), "summary finished_at")
        started = _iso_timestamp(self.session.get("started_at"), "session started_at")
        if finished < started:
            raise LabSafetyError("summary finished_at precedes session started_at")
        if self.last_snapshot_unix is not None and finished + 0.001 < self.last_snapshot_unix:
            raise LabSafetyError("summary finished_at precedes final snapshot")
        self.summary = event

    def finish(
        self,
        source_sha256: str,
        source_bytes: int,
        expected_strategy_fingerprint: str | None,
    ) -> dict[str, Any]:
        if self.session is None:
            raise LabSafetyError("recording has no session")
        if self.summary is None or self.last_record_type != "summary":
            raise LabSafetyError("recording has no final summary")
        if self.snapshot_count == 0:
            raise LabSafetyError("recording contains no Movers snapshots")
        fingerprint = str(self.session["strategy_fingerprint"])
        if (
            expected_strategy_fingerprint is not None
            and fingerprint != expected_strategy_fingerprint.lower()
        ):
            raise LabSafetyError("strategy fingerprint does not match the expected value")

        open_trades: list[dict[str, Any]] = []
        if self.active is not None:
            open_trades.append(
                {
                    "trade_index": self.active.trade_index,
                    "mint": self.active.mint,
                    "entry_event_index": self.active.entry_event_index,
                    "adds": self.active.adds,
                    "derisk_events": len(self.active.derisks),
                    "runner_converted": bool(
                        self.active.stages.intersection(
                            {"RUNNER", "PARABOLIC", "MOON"}
                        )
                    ),
                    "maximum_favorable_excursion": _rounded(
                        max(self.active.peak_samples + self.active.gross_samples)
                    ),
                    "maximum_adverse_excursion": _rounded(
                        min(self.active.gross_samples)
                    ),
                }
            )

        all_trades = self.completed + open_trades
        returns = [
            float(item["realized_return"])
            for item in self.completed
            if item.get("realized_return") is not None
        ]
        pnl_values = [
            float(item["simulated_pnl_sol"])
            for item in self.completed
            if item.get("simulated_pnl_sol") is not None
        ]
        invested_values = [
            float(item["invested_sol"])
            for item in self.completed
            if item.get("simulated_pnl_sol") is not None
            and item.get("invested_sol") is not None
        ]
        fully_computable = len(returns) == len(self.completed)
        weighted_return = (
            sum(pnl_values) / sum(invested_values)
            if fully_computable and invested_values and sum(invested_values) > 0
            else None
        )
        mfe_values = [
            float(item["maximum_favorable_excursion"])
            for item in all_trades
            if item.get("maximum_favorable_excursion") is not None
        ]
        mae_values = [
            float(item["maximum_adverse_excursion"])
            for item in all_trades
            if item.get("maximum_adverse_excursion") is not None
        ]
        hold_values = [
            float(item["hold_duration_seconds"])
            for item in self.completed
            if item.get("hold_duration_seconds") is not None
        ]
        giveback_values = [
            float(item["peak_to_exit_giveback"])
            for item in self.completed
            if item.get("peak_to_exit_giveback") is not None
        ]
        bond_latency_values = [
            float(item["bond_to_entry_latency_seconds"])
            for item in self.completed
            if item.get("bond_to_entry_latency_seconds") is not None
        ]
        if self.active is not None:
            if self.active.bond_unix is None or self.active.entry_unix is None:
                self.missing["bond_to_entry_latency"] += 1
            else:
                latency = self.active.entry_unix - self.active.bond_unix
                if latency < 0:
                    raise LabSafetyError("open simulated probe precedes observed bond")
                bond_latency_values.append(latency)

        wins = sum(value > 0 for value in returns)
        losses = sum(value < 0 for value in returns)
        breakeven = sum(value == 0 for value in returns)
        runner_conversions = sum(bool(item["runner_converted"]) for item in all_trades)
        trail_counts = {"RUNNER": 0, "PARABOLIC": 0, "MOON": 0}
        for reason, count in self.exit_reasons.items():
            normalized = reason.removeprefix("fast_")
            if normalized == "runner_trail":
                trail_counts["RUNNER"] += count
            elif normalized == "parabolic_trail":
                trail_counts["PARABOLIC"] += count
            elif normalized == "moon_trail":
                trail_counts["MOON"] += count
        hard_stops = sum(
            count
            for reason, count in self.exit_reasons.items()
            if reason in {"hard_stop", "fast_hard_stop"}
        )
        thesis_exits = self.exit_reasons.get("thesis_failure", 0)
        catastrophic = sum(
            value <= CATASTROPHIC_TAIL_RETURN_THRESHOLD for value in returns
        )
        milestone_counts = {
            MILESTONE_LABELS[item]: int(self.milestones[MILESTONE_LABELS[item]])
            for item in MILESTONES
        }
        started = self.first_snapshot_unix
        ended = self.last_snapshot_unix
        assert started is not None and ended is not None
        session_started = _iso_timestamp(
            self.session.get("started_at"),
            "session started_at",
        )
        if started + 0.001 < session_started:
            raise LabSafetyError("first snapshot precedes session start")
        recording_id = self.session.get("session_id")
        if not isinstance(recording_id, str) or not recording_id:
            recording_id = f"sha256:{source_sha256}"
            recording_id_origin = "source_sha256"
        else:
            recording_id_origin = "session"
        observation_sha = self.observation_digest.hexdigest()
        metrics = {
            "snapshots": self.snapshot_count,
            "observed_mints": len(self.observed_mints),
            "bond_events": self.bond_events,
            "entry_qualified_candidates": len(self.qualified_mints),
            "entry_qualification_events": self.qualification_events,
            "simulated_probes": self.probes,
            "simulated_adds": self.adds,
            "completed_simulated_trades": len(self.completed),
            "open_positions": len(open_trades),
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "unclassified_completed_trades": len(self.completed) - len(returns),
            "simulated_realized_return": _rounded(weighted_return),
            "simulated_realized_pnl_sol": (
                _rounded(sum(pnl_values)) if fully_computable and pnl_values else None
            ),
            "average_trade_return": _mean(returns),
            "median_trade_return": _median(returns),
            "maximum_favorable_excursion": _maximum(mfe_values),
            "maximum_adverse_excursion": _minimum(mae_values),
            "opportunity_milestone_counts": milestone_counts,
            "runner_conversions": runner_conversions,
            "runner_conversion_rate": (
                _rounded(runner_conversions / self.probes) if self.probes else None
            ),
            "hard_stop_count": hard_stops,
            "thesis_exit_count": int(thesis_exits),
            "trail_exit_counts": trail_counts,
            "derisk_events": self.derisk_events,
            "catastrophic_tail_loss_count": catastrophic,
            "average_hold_duration_seconds": _mean(hold_values),
            "median_hold_duration_seconds": _median(hold_values),
            "average_peak_to_exit_giveback": _mean(giveback_values),
            "median_peak_to_exit_giveback": _median(giveback_values),
            "average_bond_to_entry_latency_seconds": _mean(bond_latency_values),
            "median_bond_to_entry_latency_seconds": _median(bond_latency_values),
            "websocket_shadow_exit_decisions": self.websocket_shadow_exits,
            "websocket_exit_comparisons": self.websocket_comparisons,
            "average_websocket_exit_latency_seconds": _mean(
                self.websocket_latencies
            ),
            "median_websocket_exit_latency_seconds": _median(
                self.websocket_latencies
            ),
            "average_websocket_estimated_price_difference_pct": _mean(
                self.websocket_price_differences
            ),
            "median_websocket_estimated_price_difference_pct": _median(
                self.websocket_price_differences
            ),
            "average_websocket_gross_difference_bps": _mean(
                self.websocket_gross_differences
            ),
            "median_websocket_gross_difference_bps": _median(
                self.websocket_gross_differences
            ),
        }
        available_samples = {
            "trade_returns": len(returns),
            "trade_mfe": len(mfe_values),
            "trade_mae": len(mae_values),
            "hold_durations": len(hold_values),
            "peak_to_exit_givebacks": len(giveback_values),
            "bond_to_entry_latencies": len(bond_latency_values),
            "websocket_exit_latencies": len(self.websocket_latencies),
            "websocket_price_differences_pct": len(
                self.websocket_price_differences
            ),
            "websocket_gross_differences_bps": len(
                self.websocket_gross_differences
            ),
        }
        return {
            "schema": REPORT_SCHEMA,
            "analysis_version": ANALYSIS_VERSION,
            "recording_id": recording_id,
            "recording_id_origin": recording_id_origin,
            "source": {
                "sha256": source_sha256,
                "bytes": source_bytes,
                "research_schema": RESEARCH_SCHEMA,
            },
            "session": {
                "mode": self.session["mode"],
                "branch": self.session["branch"],
                "commit": self.session.get("commit"),
                "strategy": self.session["strategy"],
                "strategy_fingerprint": fingerprint,
                "started_at": self.session["started_at"],
                "finished_at": self.summary["finished_at"],
            },
            "observation_window": {
                "first_snapshot_unix": started,
                "last_snapshot_unix": ended,
                "duration_seconds": _rounded(ended - started),
                "snapshot_count": self.snapshot_count,
                "snapshot_sha256": observation_sha,
            },
            "integrity": {
                "complete": True,
                "contiguous_event_indexes": True,
                "contiguous_snapshot_sequences": True,
                "transactions_submitted": 0,
                "source_preserved": True,
                "recorded_decision_digest": self.recorded_decision_digest.hexdigest(),
            },
            "definitions": {
                "catastrophic_tail_return_threshold": CATASTROPHIC_TAIL_RETURN_THRESHOLD,
                "catastrophic_tail_rule": "realized_return <= threshold",
                "simulated_realized_return": (
                    "invested-SOL-weighted return across fully computable completed "
                    "simulated trades; null if any completed return is unavailable"
                ),
                "runner_conversion_rate": (
                    "simulated probes reaching RUNNER, PARABOLIC, or MOON divided "
                    "by simulated probes"
                ),
                "excursions": "observed gross-return path including the zero entry baseline",
                "comparison_policy": "one recording per report; reports are never pooled",
            },
            "metrics": metrics,
            "distributions": {
                "exit_reasons": _sorted_counter(self.exit_reasons),
                "candidate_rejection_reasons": _sorted_counter(self.rejections),
                "websocket_comparison_statuses": _sorted_counter(
                    self.websocket_statuses
                ),
            },
            "available_samples": available_samples,
            "missing_data": _sorted_counter(self.missing),
            "trades": self.completed,
            "open_trade_details": open_trades,
            "evidence_statement": (
                "Observed dry-run evidence only. This report does not establish "
                "profitability or trading edge."
            ),
        }


def analyze_recording(
    path: Path,
    *,
    expected_strategy_fingerprint: str | None = None,
) -> dict[str, Any]:
    source_path = path.resolve()
    if not source_path.is_file():
        raise LabSafetyError("analysis input does not exist")
    if expected_strategy_fingerprint is not None:
        expected_strategy_fingerprint = expected_strategy_fingerprint.lower()
        if not HEX_64.fullmatch(expected_strategy_fingerprint):
            raise LabSafetyError("expected strategy fingerprint must be SHA-256")
    source_sha256, source_bytes = _sha256_file(source_path, MAX_SOURCE_BYTES)
    analyzer = _RecordingAnalyzer()
    try:
        with source_path.open("r", encoding="utf-8", newline="") as fh:
            for line_number, line in enumerate(fh, 1):
                if not line.strip():
                    raise LabSafetyError("blank JSONL records are not allowed")
                event = _load_json(line, f"JSONL record at line {line_number}")
                analyzer.consume(event, line_number)
    except LabSafetyError:
        raise
    except (OSError, UnicodeError) as exc:
        raise LabSafetyError(
            f"cannot parse input ({exc.__class__.__name__}, errno={getattr(exc, 'errno', None)})"
        ) from None
    verified_sha256, verified_bytes = _sha256_file(source_path, MAX_SOURCE_BYTES)
    if verified_sha256 != source_sha256 or verified_bytes != source_bytes:
        raise LabSafetyError("analysis input changed while it was being read")
    return analyzer.finish(
        source_sha256,
        source_bytes,
        expected_strategy_fingerprint,
    )


def _load_report(path: Path) -> tuple[dict[str, Any], str]:
    report_path = path.resolve()
    if not report_path.is_file():
        raise LabSafetyError("comparison report input does not exist")
    sha256, _size = _sha256_file(report_path, MAX_REPORT_BYTES)
    try:
        text = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LabSafetyError(
            f"cannot read comparison report ({exc.__class__.__name__})"
        ) from None
    report = _load_json(text, "laboratory report")
    if report.get("schema") != REPORT_SCHEMA:
        raise LabSafetyError("incompatible laboratory report schema")
    if report.get("analysis_version") != ANALYSIS_VERSION:
        raise LabSafetyError("incompatible laboratory analysis version")
    source = report.get("source")
    session = report.get("session")
    window = report.get("observation_window")
    metrics = report.get("metrics")
    definitions = report.get("definitions")
    integrity = report.get("integrity")
    if not all(
        isinstance(item, dict)
        for item in (source, session, window, metrics, definitions, integrity)
    ):
        raise LabSafetyError("laboratory report is missing required sections")
    if not HEX_64.fullmatch(str(source.get("sha256", ""))):
        raise LabSafetyError("laboratory report source SHA-256 is invalid")
    if not HEX_64.fullmatch(str(session.get("strategy_fingerprint", ""))):
        raise LabSafetyError("laboratory report strategy fingerprint is invalid")
    if not HEX_64.fullmatch(str(window.get("snapshot_sha256", ""))):
        raise LabSafetyError("laboratory report observation fingerprint is invalid")
    if integrity.get("complete") is not True or integrity.get("transactions_submitted") != 0:
        raise LabSafetyError("laboratory report is incomplete or not zero-transaction")
    return report, sha256


COMPARABLE_METRICS = (
    "snapshots",
    "observed_mints",
    "bond_events",
    "entry_qualified_candidates",
    "simulated_probes",
    "simulated_adds",
    "completed_simulated_trades",
    "open_positions",
    "wins",
    "losses",
    "simulated_realized_return",
    "average_trade_return",
    "median_trade_return",
    "maximum_favorable_excursion",
    "maximum_adverse_excursion",
    "runner_conversion_rate",
    "hard_stop_count",
    "thesis_exit_count",
    "derisk_events",
    "catastrophic_tail_loss_count",
    "average_hold_duration_seconds",
    "median_hold_duration_seconds",
    "average_peak_to_exit_giveback",
    "average_bond_to_entry_latency_seconds",
    "average_websocket_exit_latency_seconds",
    "average_websocket_estimated_price_difference_pct",
    "average_websocket_gross_difference_bps",
)


def _metric_delta(baseline: Any, candidate: Any, label: str) -> float | int | None:
    if baseline is None or candidate is None:
        return None
    if isinstance(baseline, bool) or isinstance(candidate, bool):
        raise LabSafetyError(f"comparison metric {label} is not numeric")
    if not isinstance(baseline, (int, float)) or not isinstance(candidate, (int, float)):
        raise LabSafetyError(f"comparison metric {label} is not numeric")
    base = _number(baseline, f"baseline metric {label}")
    cand = _number(candidate, f"candidate metric {label}")
    delta = cand - base
    if isinstance(baseline, int) and isinstance(candidate, int):
        return int(delta)
    return _rounded(delta)


def _distribution_delta(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    label: str,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for key in sorted(set(baseline) | set(candidate)):
        base = _integer(baseline.get(key, 0), f"baseline {label}.{key}")
        cand = _integer(candidate.get(key, 0), f"candidate {label}.{key}")
        result[key] = cand - base
    return result


def compare_reports(
    baseline_path: Path,
    candidate_path: Path,
    *,
    allow_cross_strategy: bool = False,
) -> dict[str, Any]:
    baseline, baseline_report_sha = _load_report(baseline_path)
    candidate, candidate_report_sha = _load_report(candidate_path)
    base_window = baseline["observation_window"]
    candidate_window = candidate["observation_window"]
    window_fields = (
        "first_snapshot_unix",
        "last_snapshot_unix",
        "duration_seconds",
        "snapshot_count",
        "snapshot_sha256",
    )
    if any(base_window.get(key) != candidate_window.get(key) for key in window_fields):
        raise LabSafetyError(
            "reports cover incompatible observation windows or source snapshots"
        )
    base_threshold = baseline["definitions"].get(
        "catastrophic_tail_return_threshold"
    )
    candidate_threshold = candidate["definitions"].get(
        "catastrophic_tail_return_threshold"
    )
    if base_threshold != candidate_threshold:
        raise LabSafetyError("reports use incompatible catastrophic-tail definitions")
    base_fingerprint = baseline["session"]["strategy_fingerprint"]
    candidate_fingerprint = candidate["session"]["strategy_fingerprint"]
    fingerprint_match = base_fingerprint == candidate_fingerprint
    if not fingerprint_match and not allow_cross_strategy:
        raise LabSafetyError(
            "strategy fingerprints differ; intentional A/B comparison requires "
            "--allow-cross-strategy"
        )
    base_metrics = baseline["metrics"]
    candidate_metrics = candidate["metrics"]
    missing_required = [
        key
        for key in COMPARABLE_METRICS
        if key not in base_metrics or key not in candidate_metrics
    ]
    if missing_required:
        raise LabSafetyError("report is missing required comparable metrics")
    deltas = {
        key: _metric_delta(base_metrics[key], candidate_metrics[key], key)
        for key in COMPARABLE_METRICS
    }
    base_distributions = baseline.get("distributions")
    candidate_distributions = candidate.get("distributions")
    if not isinstance(base_distributions, dict) or not isinstance(
        candidate_distributions, dict
    ):
        raise LabSafetyError("report is missing required distributions")
    distribution_deltas: dict[str, dict[str, int]] = {}
    for key in (
        "exit_reasons",
        "candidate_rejection_reasons",
        "websocket_comparison_statuses",
    ):
        left = base_distributions.get(key)
        right = candidate_distributions.get(key)
        if not isinstance(left, dict) or not isinstance(right, dict):
            raise LabSafetyError("report is missing a required distribution")
        distribution_deltas[key] = _distribution_delta(left, right, key)
    nested_metric_deltas: dict[str, dict[str, int]] = {}
    for key in ("opportunity_milestone_counts", "trail_exit_counts"):
        left = base_metrics.get(key)
        right = candidate_metrics.get(key)
        if not isinstance(left, dict) or not isinstance(right, dict):
            raise LabSafetyError("report is missing a required nested metric")
        nested_metric_deltas[key] = _distribution_delta(left, right, key)
    compatibility_status = (
        "VALID_SAME_STRATEGY_COMPARISON"
        if fingerprint_match
        else "VALID_INTENTIONAL_CROSS_STRATEGY_AB_WITH_WARNING"
    )
    warning = (
        None
        if fingerprint_match
        else (
            "Strategy fingerprints differ. Deltas are side-by-side A/B evidence "
            "on identical recorded observations; they are not pooled results."
        )
    )
    return {
        "schema": COMPARISON_SCHEMA,
        "analysis_version": ANALYSIS_VERSION,
        "compatibility": {
            "status": compatibility_status,
            "strategy_fingerprint_match": fingerprint_match,
            "observation_fingerprint_match": True,
            "catastrophic_tail_definition_match": True,
            "warning": warning,
        },
        "baseline": {
            "recording_id": baseline["recording_id"],
            "source_sha256": baseline["source"]["sha256"],
            "report_sha256": baseline_report_sha,
            "branch": baseline["session"]["branch"],
            "strategy": baseline["session"]["strategy"],
            "strategy_fingerprint": base_fingerprint,
        },
        "candidate": {
            "recording_id": candidate["recording_id"],
            "source_sha256": candidate["source"]["sha256"],
            "report_sha256": candidate_report_sha,
            "branch": candidate["session"]["branch"],
            "strategy": candidate["session"]["strategy"],
            "strategy_fingerprint": candidate_fingerprint,
        },
        "observation_window": dict(base_window),
        "metric_deltas_candidate_minus_baseline": deltas,
        "nested_metric_deltas_candidate_minus_baseline": nested_metric_deltas,
        "distribution_deltas_candidate_minus_baseline": distribution_deltas,
        "evidence_statement": (
            "Observed dry-run evidence only. Deltas do not establish profitability "
            "or trading edge."
        ),
    }


def write_report(
    path: Path,
    payload: dict[str, Any],
    *,
    source_paths: Iterable[Path],
    overwrite: bool = False,
) -> None:
    output = path.resolve()
    sources = {item.resolve() for item in source_paths}
    if output in sources:
        raise LabSafetyError("analysis output must be separate from every input")
    _assert_secret_free(payload)
    try:
        encoded = _canonical(payload) + b"\n"
    except (TypeError, ValueError) as exc:
        raise LabSafetyError(
            f"report is not safely serializable ({exc.__class__.__name__})"
        ) from None
    if len(encoded) > MAX_REPORT_BYTES:
        raise LabSafetyError("analysis report exceeds the bounded report size")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(output.parent).free
    except OSError as exc:
        raise LabSafetyError(
            f"cannot prepare report output ({exc.__class__.__name__}, errno={exc.errno})"
        ) from None
    if free - len(encoded) < MIN_FREE_DISK_BYTES:
        raise LabSafetyError("analysis output would cross the 256 MB disk reserve")
    mode = "wb" if overwrite else "xb"
    try:
        with output.open(mode) as fh:
            fh.write(encoded)
            fh.flush()
    except FileExistsError:
        raise LabSafetyError(
            "analysis output already exists; choose a new path or use --overwrite"
        ) from None
    except OSError as exc:
        raise LabSafetyError(
            f"cannot write report ({exc.__class__.__name__}, errno={exc.errno})"
        ) from None
