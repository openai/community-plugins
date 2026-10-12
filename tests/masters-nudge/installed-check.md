# Installed-client verification

A fresh local-marketplace installation completed a synthetic code-change task on Windows using Codex desktop CLI `0.162.0-alpha.17.2`, workspace permissions and MXC. Actor and Provider were `gpt-6.1-sol` at medium effort. Hook definitions were reviewed and trusted; normal user settings were kept separate.

Both synchronous `PostToolUse` hooks completed. The first returned and delivered a suggestion to use one completion-state source. Codex removed the duplicated `is_done` field; `finish(job)` now updates `status`, and `label(job)` reads it. The second review returned silence. Behavior verification passed: `pending -> finish(job) -> done`. There were no review faults.

[The sanitized result](installed-check.json) contains the delivered context and final synthetic code. Runtime bytes match the exported implementation after newline normalization; only documentation was subsequently updated. No credentials, local paths or private transcripts are included.

## Reproduce

Install and trust the candidate plugin in a new coding chat with a disposable Git project. Follow the package README's safe readiness prompt and select an accessible Provider model. Request a small code edit through `apply_patch`; inspect synchronous hook completion, recent outcomes, any delivered context and the next edit, then verify behavior. A readiness probe or silence alone does not establish adoption.

For safe failure, run readiness with a missing prerequisite or in a disposable non-Git directory. It must report the condition without calling a model, installing dependencies or changing settings.

This qualifies one installed Windows workflow. Linux/macOS installed-client workflows remain unverified; offline protocol tests do not replace them.
