# 1337 Gang release security gate

Public release means permanent exposure. A green test suite is not security approval.

## Mandatory before a release

1. **Ownership and license:** Confirm rights to the source, test fixtures, assets, text, and dependency redistribution. No client code, private continuity, non-public business plans, third-party proprietary work, or data shared without authorization.
2. **Current tree:** Inspect all code, docs, datasets, configs, assets and packaged ZIPs for API keys, passwords, private keys, seed phrases, OAuth tokens, webhook secrets, payment identifiers, user emails, service credentials, browser/session cookies, private endpoints, environment files, and personal/customer data.
3. **Full history:** Scan every reachable commit, including removed files, branches and tags. A clean latest checkout is not enough.
4. **Automated:** Require successful pinned, redacted Gitleaks full-history AND working-tree checks, plus existing project CI on the exact release commit. Never suppress a real positive to force green.
5. **Human:** Independently review diffs, client rights, dependency provenance and public security exposure. Secret scanners cannot detect everything, and do not review IP ownership.
6. **Production isolation:** Never include live credentials, production database contents, browser profiles, wallets, signing authority, or private account session data. Example values must be obviously fake.
7. **Incident:** If exposure is suspected, halt release, immediately rotate/revoke the affected credential, examine use/impact, and only then consider Git history cleanup. Deleting text from Git history does not make an already exposed secret safe.
8. **Permission:** Only the owner approves public releases or merges. Passing CI is necessary, never sufficient.

## Local independent check

From a full clone, run these commands with Docker installed:

    docker run --rm -v "$PWD:/repo:ro" -w /repo ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f git --redact --log-opts="--all" /repo
    docker run --rm -v "$PWD:/repo:ro" -w /repo ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f dir --redact /repo

Keep secret findings out of issue bodies, build logs, and public artifacts. No scanner guarantees zero risk.
