# Safety boundaries

## No execution path

The v0.1.0 package contains no wallet library, signer, transaction builder,
trade endpoint, or function that can submit a buy or sell. The included
BotToTrot module contains calculations only. `record` performs unauthenticated
GET observation; `shadow` additionally opens one public observation websocket.

All modes refuse startup unless `BOT_LIVE` is explicitly `0`. Research modes
also require `BOT_RESEARCH_ENABLE=1`. Live/fast-exit or network-override flags
are rejected, and any inherited PumpPortal API-key variable is removed before
work starts even though no code consumes it.

## Offline commands

`replay`, `analyze`, and `compare` install a process-wide Python boundary that
blocks socket connections, datagrams, connection helpers, and DNS resolution
for the complete operation. Imported libraries cannot bypass that boundary by
calling normal Python sockets indirectly.

This is a software safety boundary, not an operating-system sandbox. Run
untrusted recordings in an OS sandbox as an additional precaution.

## Secret handling

Do not supply secrets. JSONL and JSON output recursively reject keys that look
like credentials, authorization, mnemonic, password, private key, secret, or
seed phrase fields. They also reject common bearer/key assignment patterns in
values. Expected safety failures are sanitized; unexpected failures print only
the exception class, never the exception message.

Public token mint addresses are identifiers, not wallet secrets.

## Bounded storage and lifecycle

- Recording output has an explicit byte cap (64 MiB by default, 1 GiB maximum).
- Output creation preserves a 256 MiB free-disk reserve.
- Recording and shadow sessions require a duration or snapshot limit.
- Analyzer input is capped at 1 GiB; reports are capped at 8 MiB.
- Existing outputs are not overwritten implicitly.
- Websocket subscriptions are deduplicated and bounded.
- The shadow monitor has one worker, bounded reconnect backoff, explicit
  unsubscribe/close, and bounded join; cleanup failure is fatal.

## Reporting boundary

The catastrophic-tail threshold (`<= -50%`) is an explicit analysis definition.
It does not alter BotToTrot's hard stop or any strategy behavior. All reported
actions are simulated and all shadow exits are log-only.
