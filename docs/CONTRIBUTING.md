# Contributing

Degen Bot Lab v0.1.0 accepts deterministic research, validation, documentation,
and safety improvements. Live wallet execution is out of scope.

Before a pull request:

1. Keep strategy changes separate from infrastructure changes.
2. Never change a frozen threshold under an existing strategy ID/fingerprint.
3. Add deterministic tests for malformed data, missing data, and safety edges.
4. Run `python -m unittest discover -s tests -p "test_*.py" -v`.
5. Run `git diff --check`.
6. Confirm no recordings, reports, `.env` files, credentials, logs, caches, or
   local state are staged.
7. Do not describe dry evidence as profit, wallet P&L, or trading edge.

Future strategy variants should use separate modules and IDs so recordings and
reports remain suitable for controlled A/B comparison.
