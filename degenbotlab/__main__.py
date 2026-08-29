"""Public command dispatcher for Degen Bot Lab."""

from __future__ import annotations

import sys

from . import analysis_cli, research_cli


RESEARCH_COMMANDS = frozenset({"record", "shadow", "replay"})
ANALYSIS_COMMANDS = frozenset({"analyze", "compare"})


def _help() -> str:
    return """Degen Bot Lab v0.1.0 (research and simulation only)

Usage:
  python -m degenbotlab record  [options]
  python -m degenbotlab shadow  [options]
  python -m degenbotlab replay  [options]
  python -m degenbotlab analyze [options]
  python -m degenbotlab compare [options]

Run a command with --help for its exact options. Live trading is not included.
"""


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        print(_help(), end="")
        return 0
    command = arguments[0]
    if command in RESEARCH_COMMANDS:
        return research_cli.main(arguments)
    if command in ANALYSIS_COMMANDS:
        return analysis_cli.main(arguments)
    print(f"DEGEN_BOT_LAB_REFUSED unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
