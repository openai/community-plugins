# Submission verification, 2026-10-12

Candidate source: [Masters’ Nudge 352946d](https://github.com/shihchengwei-lab/masters-nudge/commit/352946d033fed9f79e749f35c91575a0d8f13710). Marketplace base: `62844ca1cd865b76c7fed7180fc1ffef16e9167b`. Local host: Windows, Node 24.14.0, Python 3.12.10. Tests use synthetic fixtures, not customer code.

| Check | Actual result |
| --- | --- |
| `npm run validate:marketplace` | Passed with the five-entry catalog. |
| `npm run test:masters-nudge` | All 76 original tests passed against the shipped package; source receipt matched. |
| `npm run validate` | Passed after restoring unchanged upstream Git blobs affected by Windows checkout newline conversion. No other plugin source was modified. |
| `npm run test:marketplace` | Failed at Grafana: 16 passed, 10 failed, 6 explicitly skipped. The unmodified base, exported through `git archive`, produced the same failing test names and counts on this host. The dispatcher correctly stopped; it did not run later masters. |
| Other masters run directly | React Native to SwiftUI: 18 passed. ReviewOps: 104 passed, 1 failed, 1 skipped; failure creating a test symlink (`EPERM`). Fusion package gate: 6 passed, 7 failed with `ACL_UNVERIFIED`; deeper Fusion tests were not reached. These packages and tests are unchanged in this contribution. |
| Installed Masters’ Nudge | Discovery/task-start hook passed; edit-to-feedback gate incomplete. See [installed-check.md](installed-check.md). |
| Existing upstream source CI | [All six OS/Python jobs passed](https://github.com/shihchengwei-lab/masters-nudge/actions/runs/38267461831). This covers the source suite, not installed Codex workflows. |

Pinned existing packages' development dependencies were installed locally using `npm ci --ignore-scripts` solely to run the marketplace's own required checks. No dependency or lockfile changes are included. Their audits reported existing findings: ReviewOps `brace-expansion` (high) and `fast-uri` (moderate); Fusion `@modelcontextprotocol/client` (high). Masters’ Nudge adds no npm or third-party Python runtime dependencies. These audit findings are not declared resolved and do not waive required security review.

No credentials, local user paths, private transcripts, benchmark source trees, media recordings or generated caches are added. Plugin/test files are exact upstream Git blobs identified by [source-receipt.json](source-receipt.json); the original implementation and copied tests are MIT-licensed. Companion code only adapts the existing test layout into a temporary directory. The plugin has one workflow with one master job and sequential Python versions; the existing shared marketplace workflow is unchanged.

This is a draft qualification preview. Required hosted CI/security checks, human CLA verification and latest-change CDE approval must be completed before merge. Installed-client qualification must be completed or receive an explicit reviewer-approved exception; discovery, skipped gates and baseline failures are not passes. The submission does not assert official Plugins Directory acceptance.
