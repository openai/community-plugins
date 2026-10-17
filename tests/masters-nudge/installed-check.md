# Installed-client verification

## Current package: 0.6.5

Installed on Windows 11 x64 with the Codex plugin installer. All 29 installed package fingerprints match the exported source package. The readiness diagnostic passed; Codex resolves the MCP from the 0.6.5 cache and lists all three hooks as enabled and trusted, without the Interrupt timeout warning.

Ten tests loaded the actual installed modules in a fresh process and passed without skips. They cover real Git repositories, a Windows junction pointing outside the workspace, tracked ignored files, fixed Provider responses after read failures, no delivery from failed reviews, same-turn closure and next-turn recovery.

The installed Windows command hook completed interruption in 0.360 seconds; interruption plus real process-tree cleanup took 0.735 seconds, within the three-second hook limit. No Provider processes or unresolved reviews remained; the interrupted result was undelivered, the same session continued successfully and its old record stayed unchanged. Database integrity was `ok`.

These 0.6.5 regressions use real local processes and fixed Provider output; no model-service call was made. The earlier model-backed workflows below remain dated evidence for their tested versions. [0.6.5 results and installed package fingerprints](windows-qualification-20261018.json).

## Model-backed qualification: 0.6.4+codex.20261015

Tested on Windows 11 x64 (10.0.26300), Python 3.12.10, Codex 0.162.0-alpha.17.2, MXC workspace sandbox. Actor and Provider: `gpt-6.1-sol`, medium effort. A new isolated profile installed exact committed package bytes from a local marketplace and trusted all three hooks. Every installed file fingerprint matched the source package.

| Installed trial | Result |
| --- | --- |
| Normal code task | Completed; one delivered suggestion followed by one silence; the coding model removed `is_done` and used `status` as the sole completion source; behavior checks passed. |
| Interrupt while the actual Provider CLI is running | Turn ended as `interrupted`; zero Provider processes remained; journal recorded an interrupted fault with no delivered feedback. |
| Continue in the same chat | New turn completed with one delivered suggestion and one silence; pending/finish/reset/finish checks passed; the original interrupted record remained unchanged. |
| Final state | Both workflows had zero unresolved reviews and zero Provider processes; database integrity checks returned `ok`. |
| Hook reporting | `Interrupt` completed. In this trial, the original `PostToolUse` also completed, after the interrupted turn ended. |

Normal workflow: 40.281 seconds. Interruption and continuation together: 58.156 seconds. [Sanitized results and installed package fingerprints](windows-qualification-20261015.json).

## Installed qualification: 0.6.3+codex.20261013

Qualified installed platform: **Windows 11 x64**, Python 3.12.10, Codex 0.162.0-alpha.17.2, MXC workspace sandbox. Actor and Provider: `gpt-6.1-sol`, medium effort. A fresh isolated profile installed the package and trusted all three hooks.

| Installed trial | Result |
| --- | --- |
| Normal code task | Completed; one suggestion delivered; behavior verification passed. |
| Interrupt while Provider CLI is running | Turn ended as `interrupted`; no Provider CLI remained; journal stored an interrupted fault with no delivered feedback. |
| Hook reporting | `Interrupt` completed; original `PostToolUse` completion was absent in this trial, tracked in [Codex #46765](https://github.com/openai/codex/issues/46765). |

[Sanitized results and tested runtime fingerprints](windows-qualification.json).

## Earlier delivery/adoption trial: 0.6.2+codex.20261012

Tested on Windows with Codex desktop CLI `0.162.0-alpha.17.2`, workspace permissions and MXC. Actor and Provider: `gpt-6.1-sol`, medium effort. The plugin was installed from a local marketplace into an isolated profile, with both hook definitions trusted.

Both synchronous `PostToolUse` hooks completed: one delivered suggestion, followed by one silence and zero faults. Codex adopted the suggestion by removing the duplicated `is_done` field; `finish(job)` updates `status`, and `label(job)` reads it. Behavior verification passed: `pending -> finish(job) -> done`.

[Result and synthetic code](installed-check.json).

## Reproduce

In a disposable Git project, install and trust the plugin, run its readiness prompt and select an accessible Provider model. Request a small `apply_patch` edit, inspect hook completion and recent attempts, then verify the resulting behavior.

Safe-failure check: run readiness with a missing prerequisite or in a non-Git directory. It should report the condition without a model call or settings change.

Linux/macOS installed-client workflows are outside this release's supported-platform qualification.
