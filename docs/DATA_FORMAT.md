# Data formats

## Research JSONL

Each nonblank line is a JSON object with schema `bottotrot.research.v1` and a
strictly increasing `event_index`. The writer emits canonical compact JSON.

Record types:

- `session`: mode, branch, commit, strategy, configuration, fingerprint, and
  explicit `execution=simulated_only` / `transactions=blocked` markers;
- `movers_snapshot`: sequence, ISO timestamp, Unix timestamp, monotonic
  timestamp, and normalized public entries;
- `strategy_decision`: deterministic bond, opportunity, entry, simulated
  action, position, exit, and post-exit evidence;
- `websocket_shadow`: observation-only subscription/tick/exit/comparison data;
- `observer_status`: sanitized rate-limit/retry/connection state;
- `summary`: terminal counts, digest, open simulated position, and explicit
  `transactions_submitted=0`.

Replay requires the first record to be `session`, at least one contiguous
snapshot, valid finite and matching timestamps, the supported schema, and an
exact strategy fingerprint. Duplicate JSON keys and NaN/Infinity are refused.

## Analysis report

Schema: `bottotrot.lab.report.v1`.

The report identifies its exact input SHA-256, session, observation window,
metric definitions, integrity status, available sample counts, missing-data
counts, completed simulated trades, and any open simulated position.

## Comparison report

Schema: `bottotrot.lab.comparison.v1`.

The comparison identifies both report hashes and source hashes, compatibility
status, the common observation window, scalar metric deltas, nested metric
deltas, and distribution deltas. Candidate-minus-baseline deltas are evidence
descriptions, not proof of causation or edge.

## Publication hygiene

Raw recordings can contain public token identifiers and market observations.
They are ignored by Git by default. Review and sanitize any dataset separately
before deciding to publish it. Never rename a secret-bearing file to bypass the
writer or `.gitignore` protections.
