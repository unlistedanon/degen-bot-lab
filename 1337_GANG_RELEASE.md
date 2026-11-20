# 1337 Gang release: Degen Bot Lab v0.1.0

**FREE & OPEN SOURCE — MIT LICENSE**  
**Release lane:** public branch `release/1337-gang-2026-11-19`  
**Maintainer:** [unlistedanon](https://github.com/unlistedanon)  
**Scope:** research, recording, deterministic offline replay, analysis, and comparison. **No live trading.**

## What's available now

This is working, runnable Python research infrastructure, not a token, Telegram signal business, or profit claim.

| Command | Result | Network access |
|---|---|---|
| `record` | Bounded public Pump Movers observation plus simulated decisions | Public GET, no credential |
| `shadow` | Optional public websocket observation of simulated exits | Public HTTP / websocket |
| `replay` | Deterministic playback of compatible recordings | Denied by Python socket/DNS guard |
| `analyze` | Offline report from a recording with provenance and missing-data visibility | Denied |
| `compare` | Compatible experiment report comparison, refusing mismatches | Denied |

## Get it running

**Requirements:** Python 3.10+, an internet connection only for installing dependencies and optional public recording.

```bash
git clone --branch release/1337-gang-2026-11-19 https://github.com/unlistedanon/degen-bot-lab.git
cd degen-bot-lab
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
```

For first use, start with `docs/GETTING_STARTED.md`. Recording requires `BOT_LIVE=0` and `BOT_RESEARCH_ENABLE=1` and explicit bounded duration/snapshot limits. Replay and analysis can operate without network access.

## Verification receipts

- Mainline workflow `Tests` succeeded at [4c64d66](https://github.com/unlistedanon/degen-bot-lab/actions/runs/33337487447) on 2026-08-30.
- Repository-hardening branch workflow `Tests` succeeded at [e302e7c](https://github.com/unlistedanon/degen-bot-lab/actions/runs/40838763342) on 2026-10-29; this release branch inherits that code.
- These are CI receipts, **not proof of ongoing feed availability, profitability, or performance under every workload**.

## What it will not do

- No wallet creation, wallet keys, buy/sell submission, live P&L, or claims of edge.
- Public feed schemas and providers can change, so network collection may need upkeep.
- Offline Python socket/DNS blocking is not an operating-system sandbox; use OS isolation for untrusted data.
- Never put secret keys, phrases, authenticated endpoints, or personal user data into test recordings.

## Third-party and contributions

Project source is MIT-licensed ([LICENSE](LICENSE)); external dependencies keep their own terms ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). Improvements and reproducible test cases welcome. Prefer a small issue with the exact command, observed result, and expected result.

**1337 Gang:** publish inspectable tools, show the limits, let people verify the code. No hype-based performance guarantees.
