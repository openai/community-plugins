# Installed-client check: incomplete qualification

This contribution is a **review preview**. The offline master and installed discovery are separate checks. This report does not claim a passing installed edit-to-feedback-to-next-edit workflow.

On 2026-10-12, Windows Codex CLI 0.159.1 installed the package from a local marketplace into a fresh, isolated Codex home. A disposable Git project contained only synthetic job-state code. The check used a signed-in account with `gpt-6.1-sol` and medium reasoning, and a separate plugin data directory. Normal user configuration and Provider selection were not changed. Credentials, local paths, private transcripts and account details are not included here.

The app-server listed both installed hook definitions; the check verified their source belonged to the installed cache and trusted only their current hashes through the normal hook state configuration. The plugin MCP server reported ready, and `UserPromptSubmit` completed. These observations establish discovery and task-start hook execution, not review delivery.

The first attempt selected the legacy workspace-write sandbox option, but the host returned read-only. After preserving it, the harness used the documented `default_permissions = ":workspace"` profile. The host returned workspace-write, yet rejected ordinary file-read commands with `blocked by policy`. The coding model made no edits and there were no Provider review attempts. The underlying cause of the second host rejection has not been established. No permission bypass was used and no successful result was substituted for either failure.

See [installed-check.json](installed-check.json) for sanitized observations and hashes of the privately retained originals. The tested snapshot predates final documentation wording and line-ending normalization; normalized executable runtime bytes must match the committed source receipt before this observation is used for a later update.

## Reproduce the remaining happy-path gate

1. Use a fresh Codex home/profile and disposable Git project, with Python 3.10+, Git, a signed-in executable Codex CLI and permission to edit that project. Do not use customer or private code.
2. Install `masters-nudge@community-plugins` from the candidate marketplace. Review and trust both hook definitions, then start a new coding chat.
3. Run the safe readiness prompt from the package README; explicitly choose an accessible Provider model. Readiness discovery alone is not a pass.
4. Request a small synthetic product-code change through `apply_patch`. Observe a synchronous `PostToolUse` completion and a recorded Provider outcome. A returned suggestion plus its actual next edit is needed to establish adoption; silence is a distinct legitimate outcome, not proof of improvement.
5. Verify the resulting behavior and inspect recent attempts. Preserve faults, timeouts and interrupted hooks. Do not retry merely to obtain a preferred outcome.

For a safe failure, use a disposable non-Git directory or a missing prerequisite and run only the readiness probe. It must report the condition without a model call, dependency installation or configuration mutation. The offline suite exercises these boundaries with protocol/process doubles; it does not replace the installed gate above.

Linux/macOS installed-client qualification and an end-to-end Windows happy path remain unverified. Before release, complete applicable qualification or obtain a documented reviewer-approved exception under [review-and-publish.md](../../docs/review-and-publish.md). Required CI, human CLA verification and latest-change CDE approval also remain merge gates. Follow the marketplace's [private security reporting route](../../SECURITY.md) for vulnerabilities; do not post credentials or private transcripts in issues.
