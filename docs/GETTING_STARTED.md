# Getting started

## Requirements

- Python 3.10 or newer
- Git
- Internet access only for installation, `record`, and optional `shadow`
- No wallet, signer, seed phrase, private key, or trading API key

## Windows CMD install

```bat
git clone https://github.com/unlistedanon/degen-bot-lab.git
cd degen-bot-lab
py -m pip install -r requirements.txt
set BOT_LIVE=0
py -m unittest discover -s tests -p "test_*.py" -v
```

`BOT_LIVE=0` is mandatory for every command. Record/replay/shadow also require
the separate opt-in `BOT_RESEARCH_ENABLE=1`.

## Record

Both a wall-clock duration and a snapshot cap are shown. The first one reached
ends the session. The log is also capped at 64 MiB and preserves a 256 MiB free
disk reserve.

```bat
mkdir "%LOCALAPPDATA%\DegenBotLab\recordings" 2>NUL
set BOT_LIVE=0
set BOT_RESEARCH_ENABLE=1
py -m degenbotlab record --output "%LOCALAPPDATA%\DegenBotLab\recordings\movers-session.jsonl" --duration-seconds 900 --max-snapshots 450 --max-log-mb 64
```

This command performs unauthenticated GET requests to the fixed public Movers
endpoint. It records normalized snapshots and simulated decisions. It has no
transaction client.

## Websocket shadow

Shadowing is a second explicit opt-in. It observes public trade messages for a
bounded set of bonded candidates and the simulated held position.

```bat
set BOT_LIVE=0
set BOT_RESEARCH_ENABLE=1
py -m degenbotlab shadow --output "%LOCALAPPDATA%\DegenBotLab\recordings\shadow-session.jsonl" --duration-seconds 900 --max-snapshots 450 --max-log-mb 64 --enable-websocket-shadow --max-shadow-candidates 32
```

Shadow exit decisions are logged as `LOG_ONLY_NO_SELL`. Shutdown closes the
connection, joins the one worker thread with a bounded timeout, and fails if
cleanup cannot complete.

## Replay

Replay must use a different output path. The process denies socket creation,
connection, datagrams, and DNS for the complete replay operation.

```bat
set BOT_LIVE=0
set BOT_RESEARCH_ENABLE=1
py -m degenbotlab replay --input "%LOCALAPPDATA%\DegenBotLab\recordings\movers-session.jsonl" --output "%LOCALAPPDATA%\DegenBotLab\recordings\movers-replay.jsonl" --max-log-mb 64
```

A replay succeeds only when its decision digest matches the recorded digest
and the recording strategy fingerprint matches the installed strategy/config.

## Analyze

```bat
set BOT_LIVE=0
py -m degenbotlab analyze --input "%LOCALAPPDATA%\DegenBotLab\recordings\movers-session.jsonl" --output "%LOCALAPPDATA%\DegenBotLab\reports\movers-session.report.json"
```

For an additional identity check, append:

```bat
--expected-strategy-fingerprint <64-lowercase-hex-characters>
```

## Compare

```bat
set BOT_LIVE=0
py -m degenbotlab compare --baseline "%LOCALAPPDATA%\DegenBotLab\reports\baseline.report.json" --candidate "%LOCALAPPDATA%\DegenBotLab\reports\candidate.report.json" --output "%LOCALAPPDATA%\DegenBotLab\reports\comparison.json"
```

The normal command requires the same fingerprint, observation window, snapshot
count, and snapshot digest. `--allow-cross-strategy` is intended for controlled
A/B research over the same observations; it prominently marks the result as
cross-strategy and not directly attributable if compatibility is insufficient.
