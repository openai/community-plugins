# Verification

Supported installed platform: **Windows 11 x64**. Local host: Windows 11 x64, Node 24.14.0, Python 3.12.10.

| Command / check | Result |
| --- | --- |
| `npm run validate:marketplace` | Passed. |
| `npm run test:masters-nudge` | 86 passed on Windows, no skips. |
| `npm run validate` | Passed. |
| Installed normal workflow | Completed; suggestion delivered; behavior verification passed. |
| Installed turn interruption during Provider execution | Interrupted turn; zero remaining Provider CLIs; interrupted journal fault; no feedback delivery. |

The single plugin master job runs the same complete suite on Windows Server 2025, sequentially with Python 3.10 and 3.14. The shared marketplace command also runs it on its own host; Windows-only process checks are explicitly skipped on other platforms.

The added lifecycle checks execute real processes for MCP cancellation/disconnection, caller termination, timeouts, descendant cleanup, and killed journal writers. Journal checks cover turn-scoped interruption, late-result rejection, restart recovery, competing finalizers, transaction rollback, and corrupt storage. Model responses in the automated suite are fixed fixtures; the installed trials use the signed-in Codex model.

[Installed evidence](installed-check.md), [tests](support/upstream-tests/spec/test_lifecycle.py), [source receipt](source-receipt.json).
