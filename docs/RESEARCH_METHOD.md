# Research method

Degen Bot Lab separates observation, deterministic simulation, measurement,
and comparison. It does not convert simulation into a claim of market edge.

## Experiment identity

One JSONL file is one recording. The analyzer never silently combines files.
Every report includes:

- recording/session ID;
- source file SHA-256 and byte count;
- recorded branch and commit;
- strategy name, exact configuration, and fingerprint;
- observation start/end/duration, snapshot count, and snapshot digest;
- completeness, event-contiguity, simulated-only, and zero-transaction flags.

Fingerprints must not be mixed silently. Comparing different strategies
requires an explicit flag and still receives a prominent compatibility label.

## Reported evidence

The experiment report distinguishes observed counts from missing data and can
include:

- snapshots, distinct observed mints, bond events, entry qualifications, and
  rejection reasons;
- simulated probes, adds, derisk events, completed trades, and open positions;
- wins, losses, realized simulated return, average/median trade return;
- observable maximum favorable and adverse excursion;
- observed +25%, +50%, +100%, and +300% opportunity milestones measured from
  the recorded bond market cap;
- runner conversion rate;
- exit distribution, hard stops, thesis exits, and Runner/Parabolic/Moon trails;
- catastrophic-tail count using the documented inclusive `return <= -50%`
  analysis threshold (this is a reporting definition, not a strategy rule);
- average/median holding time, peak-to-exit giveback, and bond-to-entry latency;
- websocket-shadow versus Movers exit latency and normalized price differences.

If required fields were not observed, the associated metric is `null` and a
missing-data counter increases. Absence is never silently converted to zero.

## Determinism

Replay uses recorded Unix and monotonic timestamps. It does not consult the
wall clock for strategy decisions, random values, or container iteration order.
It rejects non-contiguous sequences, non-monotonic or inconsistent timestamps,
malformed JSONL, duplicate JSON keys, non-finite numbers, schema mismatches,
secret-shaped data, and strategy-fingerprint mismatches.

The original recording remains unchanged. Replay and report outputs must use
separate paths, and overwrite is opt-in.

## Interpretation

Returns are simulated signal-market-cap returns. They omit wallet execution,
fill quality, routing, fees, slippage, latency beyond recorded observations,
liquidity constraints, price impact, failed transactions, and adversarial token
behavior unless those facts happen to be explicitly represented in the data.
They are not wallet P&L and do not establish profitability.
