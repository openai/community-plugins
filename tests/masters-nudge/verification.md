# Verification

Local host: Windows, Node 24.14.0, Python 3.12.10.

| Command / check | Result |
| --- | --- |
| `npm run validate:marketplace` | Passed. |
| `npm run test:masters-nudge` | 76 passed. |
| `npm run validate` | Passed. |
| Installed Windows workflow | Passed; [details](installed-check.md). |
| `npm run test:marketplace` | Grafana: 16 passed, 10 failed, 6 skipped; dispatcher stopped there. |

Hosted marketplace/security checks are pending. Linux/macOS installed-client workflows have not been tested.

Package and test provenance: [source-receipt.json](source-receipt.json).
