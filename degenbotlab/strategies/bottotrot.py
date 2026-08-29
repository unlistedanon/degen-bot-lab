"""Pure BotToTrot bonded Runner Hunter v1.1 calculations.

This module is an extraction of the frozen strategy's calculation surface. It
has no HTTP client, websocket, wallet, state-file, or transaction dependency.
The constants and functions intentionally retain the frozen defaults and rules.
"""

from __future__ import annotations

import os
from typing import Any


STRATEGY_ID = "bottotrot_bonded_runner_v1.1"
OPPORTUNITY_MILESTONES = (0.25, 0.50, 1.00, 3.00)

POLL_SECONDS = max(
    float(os.getenv("BOT_POLL_SECONDS", "2")),
    float(os.getenv("BOT_MOVERS_MIN_POLL_SECONDS", "2.0")),
)
PROBE_SOL = float(os.getenv("BOT_PROBE_SOL", "0.01"))
ADD_SOL = float(os.getenv("BOT_ADD_SOL", "0.01"))
MAX_POSITION_SOL = float(os.getenv("BOT_MAX_POSITION_SOL", "0.03"))
MAX_ADDS = int(os.getenv("BOT_MAX_ADDS", "2"))
MAX_TRADES = int(os.getenv("BOT_MAX_TRADES", "0"))

PROBE_SCORE = float(os.getenv("BOT_PROBE_SCORE", "72"))
ADD_SCORE = float(os.getenv("BOT_ADD_SCORE", "82"))
WATCH_SCORE = float(os.getenv("BOT_WATCH_SCORE", "55"))
MIN_OBSERVATIONS = int(os.getenv("BOT_MIN_OBSERVATIONS", "4"))
PROBE_CONFIRM_POLLS = int(os.getenv("BOT_PROBE_CONFIRM_POLLS", "2"))
ADD_CONFIRM_POLLS = int(os.getenv("BOT_ADD_CONFIRM_POLLS", "2"))
RUNNER_AT_GROSS = float(os.getenv("BOT_RUNNER_AT_GROSS", "0.25"))
ADD_MIN_GROSS = max(
    float(os.getenv("BOT_ADD_MIN_GROSS", "0.05")),
    RUNNER_AT_GROSS,
)
MAX_TOP10_PCT = float(os.getenv("BOT_MAX_TOP10_PCT", "45"))
MAX_DEV_HOLD_PCT = float(os.getenv("BOT_MAX_DEV_HOLD_PCT", "8"))

HARD_STOP = float(os.getenv("BOT_HARD_STOP", "-0.18"))
PARABOLIC_AT_GROSS = float(os.getenv("BOT_PARABOLIC_AT_GROSS", "1.00"))
MOON_AT_GROSS = float(os.getenv("BOT_MOON_AT_GROSS", "3.00"))
# Frozen v1.1 defaults, which intentionally override the older v1 defaults.
RUNNER_TRAIL = float(os.getenv("BOT_RUNNER_TRAIL", "0.18"))
PARABOLIC_TRAIL = float(os.getenv("BOT_PARABOLIC_TRAIL", "0.22"))
MOON_TRAIL = float(os.getenv("BOT_MOON_TRAIL", "0.20"))
RUNNER_FLOOR = float(os.getenv("BOT_RUNNER_FLOOR", "0.05"))
THESIS_EXIT_SCORE = float(os.getenv("BOT_THESIS_EXIT_SCORE", "38"))
THESIS_BAD_POLLS = int(os.getenv("BOT_THESIS_BAD_POLLS", "3"))
MIN_THESIS_HOLD_SECONDS = float(os.getenv("BOT_MIN_THESIS_HOLD_SECONDS", "10"))
DERISK_AT_GROSS = float(os.getenv("BOT_DERISK_AT_GROSS", "1.00"))
DERISK_SELL_PCT = float(os.getenv("BOT_DERISK_SELL_PCT", "25"))
MISSING_EXIT_POLLS = int(os.getenv("BOT_MISSING_EXIT_POLLS", "15"))

HISTORY_POINTS = int(os.getenv("BOT_HISTORY_POINTS", "12"))
POST_EXIT_TRACK_SECONDS = float(os.getenv("BOT_POST_EXIT_TRACK_SECONDS", "3600"))


def num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def pct_value(value: Any) -> float | None:
    """Normalize feed concentration values without guessing on exact 1.0."""
    number = num(value)
    if number is None or number < 0:
        return None
    if 0 <= number < 1:
        return number * 100.0
    if number == 1:
        return None
    return number


def snap(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize one public Movers entry to the frozen strategy schema."""
    return {
        "mint": entry.get("m"),
        "name": entry.get("n"),
        "symbol": entry.get("t"),
        "chain": entry.get("c"),
        "launchpad": entry.get("pg"),
        "age": entry.get("age"),
        "holders": entry.get("nh"),
        "txc": entry.get("txc"),
        "tx5": entry.get("tx5"),
        "bc": entry.get("bc"),
        "sc": entry.get("sc"),
        "mc": entry.get("mc"),
        "ath": entry.get("ath"),
        "volume": entry.get("v"),
        "volume_usd": entry.get("vUsd"),
        "volume_5": entry.get("v5"),
        "volume_15": entry.get("v15"),
        "volume_1h": entry.get("v1h"),
        "progress": entry.get("p"),
        "top10": entry.get("t10"),
        "dev_hold": entry.get("dh"),
    }


def metric_delta(
    latest: dict[str, Any], previous: dict[str, Any], key: str
) -> float:
    current = num(latest.get(key))
    prior = num(previous.get(key))
    if current is None or prior is None:
        return 0.0
    return current - prior


def safe_return(current: Any, previous: Any) -> float:
    current_number = num(current)
    previous_number = num(previous)
    if (
        current_number is None
        or previous_number is None
        or current_number <= 0
        or previous_number <= 0
    ):
        return 0.0
    return current_number / previous_number - 1.0


def score_history(
    history: list[dict[str, Any]],
) -> tuple[float, dict[str, float]]:
    if len(history) < 2:
        return 0.0, {}

    latest = history[-1]
    previous = history[-2]
    older = history[-3] if len(history) >= 3 else previous

    mc_1 = safe_return(latest.get("mc"), previous.get("mc"))
    mc_2 = safe_return(latest.get("mc"), older.get("mc"))

    tx_now = max(0.0, metric_delta(latest, previous, "txc"))
    tx_prev = max(0.0, metric_delta(previous, older, "txc"))
    buys_now = max(0.0, metric_delta(latest, previous, "bc"))
    sells_now = max(0.0, metric_delta(latest, previous, "sc"))
    holders_now = max(0.0, metric_delta(latest, previous, "holders"))
    holders_prev = max(0.0, metric_delta(previous, older, "holders"))

    flow_total = buys_now + sells_now
    buy_ratio = buys_now / flow_total if flow_total > 0 else 0.5
    tx_accel = (tx_now + 1.0) / (tx_prev + 1.0)
    holder_accel = (holders_now + 1.0) / (holders_prev + 1.0)

    v5 = num(latest.get("volume_5")) or 0.0
    v15 = num(latest.get("volume_15")) or 0.0
    volume_accel = v5 / (v15 / 3.0) if v15 > 0 else 1.0

    top10 = pct_value(latest.get("top10"))
    dev = pct_value(latest.get("dev_hold"))

    score = 20.0
    score += max(-15.0, min(15.0, mc_1 * 75.0))
    score += max(-10.0, min(15.0, mc_2 * 30.0))

    if buy_ratio >= 0.75:
        score += 15.0
    elif buy_ratio >= 0.65:
        score += 11.0
    elif buy_ratio >= 0.55:
        score += 6.0
    elif buy_ratio < 0.45:
        score -= 8.0

    score += min(10.0, tx_now * 1.5)
    if tx_accel >= 2.0:
        score += 10.0
    elif tx_accel >= 1.35:
        score += 6.0
    elif tx_accel < 0.70:
        score -= 5.0

    score += min(8.0, holders_now * 2.0)
    if holder_accel >= 1.5:
        score += 6.0
    elif holders_now <= 0:
        score -= 4.0

    if volume_accel >= 1.8:
        score += 8.0
    elif volume_accel >= 1.2:
        score += 4.0
    elif volume_accel < 0.5:
        score -= 4.0

    if top10 is not None:
        if top10 <= 20:
            score += 8.0
        elif top10 <= 30:
            score += 5.0
        elif top10 > 50:
            score -= 15.0

    if dev is not None:
        if dev <= 2:
            score += 8.0
        elif dev <= 5:
            score += 4.0
        elif dev > 10:
            score -= 15.0

    score = max(0.0, min(100.0, score))
    features = {
        "mc_1": mc_1,
        "mc_2": mc_2,
        "tx_delta": tx_now,
        "tx_accel": tx_accel,
        "buy_delta": buys_now,
        "sell_delta": sells_now,
        "buy_ratio": buy_ratio,
        "holder_delta": holders_now,
        "holder_accel": holder_accel,
        "volume_accel": volume_accel,
        "top10_pct": top10 if top10 is not None else -1.0,
        "dev_hold_pct": dev if dev is not None else -1.0,
    }
    return score, features


def entry_rejection(
    score: float,
    features: dict[str, float],
    current: dict[str, Any],
) -> str | None:
    market_cap = num(current.get("mc"))
    if market_cap is None or market_cap <= 0:
        return "no_market_cap"

    top10 = pct_value(current.get("top10"))
    if top10 is not None and top10 > MAX_TOP10_PCT:
        return "top10_concentration"

    dev = pct_value(current.get("dev_hold"))
    if dev is not None and dev > MAX_DEV_HOLD_PCT:
        return "dev_concentration"

    if score < PROBE_SCORE:
        return "score"
    if features.get("mc_1", 0.0) <= 0:
        return "no_positive_momentum"
    if features.get("buy_delta", 0.0) < 2:
        return "thin_buy_flow"
    if features.get("buy_ratio", 0.0) < 0.55:
        return "weak_buy_ratio"
    if features.get("holder_delta", 0.0) < 1:
        return "no_holder_growth"
    return None


def add_basis(
    invested_sol: float,
    basis_denom: float,
    add_sol: float,
    add_mc: float,
) -> tuple[float, float, float]:
    new_invested = invested_sol + add_sol
    new_denom = basis_denom + add_sol / add_mc
    basis_mc = new_invested / new_denom
    return new_invested, new_denom, basis_mc


def stage_from_peak(peak_gross: float) -> str:
    if peak_gross >= MOON_AT_GROSS:
        return "MOON"
    if peak_gross >= PARABOLIC_AT_GROSS:
        return "PARABOLIC"
    if peak_gross >= RUNNER_AT_GROSS:
        return "RUNNER"
    return "PROVING"


_STAGE_RANK = {"PROVING": 0, "RUNNER": 1, "PARABOLIC": 2, "MOON": 3}


def promoted_stage(current_stage: str, peak_gross: float) -> str:
    candidate = stage_from_peak(peak_gross)
    if _STAGE_RANK.get(current_stage, 0) >= _STAGE_RANK[candidate]:
        return current_stage
    return candidate


def exit_reason(
    position: dict[str, Any],
    current_mc: float,
    score: float,
    held_seconds: float,
) -> str | None:
    basis_mc = float(position["basis_mc"])
    peak_mc = max(float(position.get("peak_mc", basis_mc)), current_mc)
    gross = current_mc / basis_mc - 1.0
    peak_gross = peak_mc / basis_mc - 1.0
    stage = promoted_stage(str(position.get("stage", "PROVING")), peak_gross)

    if gross <= HARD_STOP:
        return "hard_stop"

    if stage != "PROVING" and gross <= RUNNER_FLOOR:
        return "profit_floor"

    if held_seconds >= MIN_THESIS_HOLD_SECONDS:
        bad_polls = int(position.get("thesis_bad_polls", 0))
        if score < THESIS_EXIT_SCORE and bad_polls >= THESIS_BAD_POLLS:
            return "thesis_failure"

    trail = None
    if stage == "RUNNER":
        trail = RUNNER_TRAIL
    elif stage == "PARABOLIC":
        trail = PARABOLIC_TRAIL
    elif stage == "MOON":
        trail = MOON_TRAIL

    if trail is not None and peak_mc > 0:
        drawdown = current_mc / peak_mc - 1.0
        if drawdown <= -trail:
            return f"{stage.lower()}_trail"

    return None


def fast_price_exit_reason(
    stage: str, gross: float, peak_gross: float
) -> str | None:
    """Frozen v1.1 price-only websocket-shadow exit calculation."""
    if gross <= HARD_STOP:
        return "fast_hard_stop"

    if stage != "PROVING" and gross <= RUNNER_FLOOR:
        return "fast_profit_floor"

    trail: float | None = None
    if stage == "RUNNER":
        trail = RUNNER_TRAIL
    elif stage == "PARABOLIC":
        trail = PARABOLIC_TRAIL
    elif stage == "MOON":
        trail = MOON_TRAIL

    if trail is not None and peak_gross > -1.0:
        current_multiple = 1.0 + gross
        peak_multiple = 1.0 + peak_gross
        if peak_multiple > 0:
            drawdown = current_multiple / peak_multiple - 1.0
            if drawdown <= -trail:
                return f"fast_{stage.lower()}_trail"
    return None


def bond_reason(
    previous: dict[str, Any], current: dict[str, Any]
) -> str | None:
    """Frozen bond definition: progress crossing or qualifying venue flip."""
    previous_progress = num(previous.get("progress"))
    current_progress = num(current.get("progress"))
    previous_launchpad = previous.get("launchpad")
    current_launchpad = current.get("launchpad")

    crossed = (
        previous_progress is not None
        and current_progress is not None
        and previous_progress < 100 <= current_progress
    )
    venue_flip = (
        previous_launchpad == "pump"
        and current_launchpad != "pump"
        and previous_progress is not None
        and previous_progress >= 95
    )
    if crossed:
        return "progress_cross"
    if venue_flip:
        return "venue_flip"
    return None


def validate_config() -> None:
    if float(os.getenv("BOT_MOVERS_MIN_POLL_SECONDS", "2.0")) <= 0:
        raise RuntimeError("BOT_MOVERS_MIN_POLL_SECONDS must be > 0")
    if POLL_SECONDS <= 0:
        raise RuntimeError("BOT_POLL_SECONDS must be > 0")
    if MIN_OBSERVATIONS < 3:
        raise RuntimeError("BOT_MIN_OBSERVATIONS must be >= 3")
    if PROBE_CONFIRM_POLLS < 1 or ADD_CONFIRM_POLLS < 1:
        raise RuntimeError("confirmation polls must be >= 1")
    if PROBE_SCORE <= 0 or ADD_SCORE < PROBE_SCORE:
        raise RuntimeError("runner score thresholds are invalid")
    if MAX_POSITION_SOL <= 0 or PROBE_SOL <= 0 or ADD_SOL <= 0:
        raise RuntimeError("position sizes must be > 0")
    if PROBE_SOL > MAX_POSITION_SOL:
        raise RuntimeError("BOT_PROBE_SOL cannot exceed BOT_MAX_POSITION_SOL")
    if HARD_STOP >= 0:
        raise RuntimeError("BOT_HARD_STOP must be negative")
    if not 0 < DERISK_SELL_PCT < 100:
        raise RuntimeError("BOT_DERISK_SELL_PCT must be between 0 and 100")
    for name, value in (
        ("BOT_RUNNER_TRAIL", RUNNER_TRAIL),
        ("BOT_PARABOLIC_TRAIL", PARABOLIC_TRAIL),
        ("BOT_MOON_TRAIL", MOON_TRAIL),
    ):
        if not 0 < value < 1:
            raise RuntimeError(f"{name} must be between 0 and 1")
