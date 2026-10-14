# Verification

Supported installed platform: **Windows 11 x64**. Local host: Windows 11 x64, Node 24.14.0, Python 3.12.10.

| Command / check | Result |
| --- | --- |
| `npm run validate:marketplace` | Passed. |
| `npm run test:masters-nudge` | 93 passed on Windows, no skips; package version `0.6.4+codex.20261015`. |
| `npm run validate` | Passed. |
| Installed normal workflow (`0.6.3+codex.20261013`) | Completed; suggestion delivered; behavior verification passed. |
| Installed turn interruption during Provider execution (`0.6.3+codex.20261013`) | Interrupted turn; zero remaining Provider CLIs; interrupted journal fault; no feedback delivery. |

The single plugin master job runs the same complete suite on Windows Server 2025, sequentially with Python 3.10 and 3.14. The shared marketplace command also runs it on its own host; Windows-only process checks are explicitly skipped on other platforms.

The lifecycle checks execute real processes for MCP cancellation/disconnection, caller termination, timeouts, descendant cleanup, and killed journal writers. Journal checks cover turn-scoped interruption, cancellation while queued, superseded rounds, late-result rejection, persisted deadlines and abandoned-queue recovery, competing finalizers, transaction rollback, corrupt storage, and capacity admission with finalization space. Transport checks cover full queues, duplicate request IDs and oversized-message draining. Model responses in the automated suite are fixed fixtures; the installed trials use the signed-in Codex model.

[Installed evidence](installed-check.md), [tests](support/upstream-tests/spec/test_lifecycle.py), [source receipt](source-receipt.json).
