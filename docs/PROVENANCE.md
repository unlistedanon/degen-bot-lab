# Source provenance

Degen Bot Lab was extracted with fresh Git history from the frozen BotToTrot
research layer. The preservation commit immediately before extraction was:

`3220ebb35326a300ba611458eb2ea519507ff736`

The following transaction-free files were copied byte-for-byte before package
import adaptation:

| Preserved source | Public module | SHA-256 before adaptation |
|---|---|---|
| `bottotrot_research.py` | `degenbotlab/research.py` | `a22ff67038e4f322e0e5ebe5a680644268d29e7b1e5c947e6dc879d490741895` |
| `bottotrot_lab.py` | `degenbotlab/analysis.py` | `121e92d5805e29af832afe89d49671cd9fe1d12b0bd9875dc68ec8cc108fb1c2` |

The pure BotToTrot strategy adapter was mechanically extracted from calculation
surfaces frozen at that commit. For auditability, their source hashes were:

| Calculation source | SHA-256 |
|---|---|
| Runner Hunter core | `3a4e146a9f46b4908f4d2547c272ea73c20768ac74a663416f025423abd42504` |
| Runner Hunter v1.1 wrapper | `00eca7e72efc0e65eb1957c68bc93c4e2f9feb4d6b0adf454daa3592f1d6706a` |
| Add guard | `acab7e99e2590871a438b26a76acae66a71aac73a5c7e52e3c82442a9626c493` |
| Bond-entry guard | `c9bf83aa1f5adf4054a4a892c95a962b9bbfb8f6dc217264d4b16eeddee9e093` |

Transaction submission, wallet/API-key handling, authenticated URLs, live state,
live entrypoints, creator-history HTTP enforcement, private project documents,
local recordings, logs, and unrelated tools were deliberately not transferred.

The public adapter retains the frozen defaults and calculations but has a new
source fingerprint because transaction-capable source files are not shipped.
Old recordings can be analyzed as evidence; replay requires a fingerprint that
matches the installed public adapter and configuration.
