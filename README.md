# Degen Bot Lab

Open-source infrastructure for recording, replaying, analyzing, and comparing
degen trading strategies without confusing simulation with reality.

> [!IMPORTANT]
> Degen Bot Lab v0.1.0 is research software. It has no wallet integration and
> no transaction-submission code. Recorded and simulated returns are **not**
> wallet P&L. Backtests, replays, and dry runs do **not** establish trading edge
> or profitability. Live trading is disabled and is not included.

BotToTrot bonded Runner Hunter v1.1 is the first included research strategy.
Its frozen thresholds, sizing, entry rules, exit rules, and bond definition are
exposed only as pure calculations for deterministic simulation.

Never put a seed phrase, private key, wallet credential, API secret, bearer
token, `.env` file, or authenticated endpoint into a recording or this
repository. The JSONL writer and analyzer reject secret-shaped fields and
values, but that is defense in depth—not permission to handle secrets.

## What works in v0.1.0

- `record`: bounded observation of the public Pump Movers feed plus simulated
  BotToTrot decisions. `record` and `shadow` are the only commands that make
  HTTP requests.
- `shadow`: `record` plus an opt-in public websocket observer for bonded
  candidates and simulated positions. It logs hypothetical exit timing and
  never executes an exit.
- `replay`: deterministic replay from recorded snapshots with process-wide
  Python socket and DNS denial.
- `analyze`: one recording to one structured experiment report, offline.
- `compare`: compatible report A versus report B, offline, with invalid
  comparisons refused or prominently flagged.

## Install and test

Windows CMD:

```bat
git clone https://github.com/unlistedanon/degen-bot-lab.git
cd degen-bot-lab
py -m pip install -r requirements.txt
set BOT_LIVE=0
py -m unittest discover -s tests -p "test_*.py" -v
```

Python 3.10 or newer is required.

## Quick commands (Windows CMD)

Create a local output directory once:

```bat
mkdir "%LOCALAPPDATA%\DegenBotLab\recordings" 2>NUL
mkdir "%LOCALAPPDATA%\DegenBotLab\reports" 2>NUL
```

Record a bounded 15-minute public Movers session:

```bat
set BOT_LIVE=0
set BOT_RESEARCH_ENABLE=1
py -m degenbotlab record --output "%LOCALAPPDATA%\DegenBotLab\recordings\movers-session.jsonl" --duration-seconds 900 --max-snapshots 450 --max-log-mb 64
```

Replay it with zero network calls and zero transactions:

```bat
set BOT_LIVE=0
set BOT_RESEARCH_ENABLE=1
py -m degenbotlab replay --input "%LOCALAPPDATA%\DegenBotLab\recordings\movers-session.jsonl" --output "%LOCALAPPDATA%\DegenBotLab\recordings\movers-replay.jsonl" --max-log-mb 64
```

Analyze exactly one recording:

```bat
set BOT_LIVE=0
py -m degenbotlab analyze --input "%LOCALAPPDATA%\DegenBotLab\recordings\movers-session.jsonl" --output "%LOCALAPPDATA%\DegenBotLab\reports\movers-session.report.json"
```

Compare reports only when they represent a valid common observation window:

```bat
set BOT_LIVE=0
py -m degenbotlab compare --baseline "%LOCALAPPDATA%\DegenBotLab\reports\baseline.report.json" --candidate "%LOCALAPPDATA%\DegenBotLab\reports\candidate.report.json" --output "%LOCALAPPDATA%\DegenBotLab\reports\comparison.json"
```

Outputs are exclusive by default. Existing files are never overwritten unless
the operator explicitly adds `--overwrite`.

## Documentation

- [Getting started](docs/GETTING_STARTED.md)
- [Research method](docs/RESEARCH_METHOD.md)
- [Safety boundaries](docs/SAFETY.md)
- [Data formats](docs/DATA_FORMAT.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Source provenance](docs/PROVENANCE.md)
- [Third-party licenses](THIRD_PARTY_NOTICES.md)

## Scientific boundary

Reports state only what was observed in a particular dry recording. Missing
fields are counted as missing rather than silently converted to zero. Every
analysis names its source file SHA-256 and strategy fingerprint. Recordings are
never silently combined, and comparison refuses mismatched observation windows
and malformed reports. Cross-strategy comparison requires an explicit flag and
is labeled incompatible for causal claims.

No v0.1.0 result should be described as stable production readiness, expected
profit, or evidence that funds should be deployed.
