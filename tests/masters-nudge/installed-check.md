# Installed-client verification

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
