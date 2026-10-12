# Verification

The [source receipt](source-receipt.json) identifies exact upstream plugin and test blobs. Local checks used Windows, Node 24.14.0 and Python 3.12.10.

| Check | Result |
| --- | --- |
| `npm run validate:marketplace` | Passed. |
| `npm run test:masters-nudge` | 76 tests passed against the shipped package, including source receipts, transport, read boundaries, quotas and safe failure. |
| `npm run validate` | Passed. |
| Installed Windows workflow | Passed; see [installed-check.md](installed-check.md). |
| `npm run test:marketplace` | Fails in the unchanged Grafana suite on this Windows host: 16 passed, 10 failed, 6 skipped. The unmodified base reproduces the same failures. Later masters are not reached by the dispatcher. |

No other plugin implementations or dependency lockfiles are changed. Masters’ Nudge uses Python's standard library and adds no npm dependencies. Required hosted marketplace/security checks and Linux/macOS installed-client qualification remain outstanding; failed or skipped checks are not passes.

Source and copied tests are MIT-licensed. One root master command connects the original tests to the marketplace dispatcher; one plugin workflow runs that master in one job with sequential Python versions. The package contains no credentials, customer data, private paths, session logs or benchmark trees.
