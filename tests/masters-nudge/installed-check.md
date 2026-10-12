# Installed-client verification

Tested on Windows with Codex desktop CLI `0.162.0-alpha.17.2`, workspace permissions and MXC. Actor and Provider: `gpt-6.1-sol`, medium effort. The plugin was installed from a local marketplace into an isolated profile, with both hook definitions trusted.

Both synchronous `PostToolUse` hooks completed: one delivered suggestion, followed by one silence and zero faults. Codex adopted the suggestion by removing the duplicated `is_done` field; `finish(job)` updates `status`, and `label(job)` reads it. Behavior verification passed: `pending -> finish(job) -> done`.

[Result and synthetic code](installed-check.json).

## Reproduce

In a disposable Git project, install and trust the plugin, run its readiness prompt and select an accessible Provider model. Request a small `apply_patch` edit, inspect hook completion and recent attempts, then verify the resulting behavior.

Safe-failure check: run readiness with a missing prerequisite or in a non-Git directory. It should report the condition without a model call or settings change.

Linux/macOS installed-client workflows have not been tested.
