# Third-party dependency notices

Degen Bot Lab source is released under the MIT License. The project depends on
packages installed separately by pip; their source is not vendored here.

| Dependency | Purpose | License |
|---|---|---|
| Requests | Public Movers HTTP observation | Apache-2.0 |
| websocket-client | Optional public websocket shadow | Apache-2.0 |
| Certifi | TLS CA bundle used by the websocket client | MPL-2.0 |
| urllib3 | Requests transport dependency | MIT |
| charset-normalizer | Requests text dependency | MIT |
| idna | Requests IDNA dependency | BSD-3-Clause |

Those licenses are compatible with using an MIT license for this project's own
source. Redistributors remain responsible for complying with each dependency's
license when redistributing dependency artifacts.
