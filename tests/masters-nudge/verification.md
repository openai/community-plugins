# Verification

Supported installed platform: **Windows 11 x64**. Local host: Windows 11 x64, Node 24.14.0, Python 3.12.10.

| Command / check | Result |
| --- | --- |
| `npm run validate:marketplace` | Passed. |
| `npm run test:masters-nudge` | 101 passed on Windows, no skips; package version `0.6.5`. |
| `npm run validate` | Passed. |
| Installed read boundaries and read-fault handling (`0.6.5`) | Ten tests passed against modules loaded from the actual installed package; no skips or model calls. |
| Installed interruption and continuation (`0.6.5`) | Command hook 0.360 seconds; interruption plus process cleanup 0.735 seconds; zero remaining processes or unresolved reviews; next turn passed and old record remained unchanged. |
| Installed package identity (`0.6.5`) | All 29 installed fingerprints match the exported source; MCP readiness passed and all three hooks are enabled and trusted. |
| Installed normal workflow (`0.6.4+codex.20261015`) | Completed; one suggestion delivered, then one silence; behavior verification passed. |
| Installed turn interruption during Provider execution (`0.6.4+codex.20261015`) | Interrupted turn; zero remaining Provider CLIs; interrupted journal fault; no feedback delivery. |
| New turn in the same chat after interruption (`0.6.4+codex.20261015`) | Completed; suggestion delivered; pending/finish/reset/finish checks passed; old interrupted record unchanged; no unresolved reviews or Provider processes. |

The single plugin master job runs the same complete suite on Windows Server 2025, sequentially with Python 3.10 and 3.14. The shared marketplace command also runs it on its own host; Windows-only process checks are explicitly skipped on other platforms.

The lifecycle checks execute real processes for MCP cancellation/disconnection, caller termination, timeouts, descendant cleanup, and killed journal writers. Journal checks cover turn-scoped interruption, cancellation while queued, superseded rounds, late-result rejection, persisted deadlines and abandoned-queue recovery, competing finalizers, transaction rollback, corrupt storage, and capacity admission with finalization space. Transport checks cover full queues, duplicate request IDs and oversized-message draining. Read checks reject external links and tracked ignored files; recorded read failures become faults even when Provider output is valid. The automated suite and installed 0.6.5 regressions use fixed Provider responses. The dated 0.6.4 model-backed trials remain separate historical evidence.

[Installed evidence](installed-check.md), [tests](support/upstream-tests/spec/test_lifecycle.py), [source receipt](source-receipt.json).
