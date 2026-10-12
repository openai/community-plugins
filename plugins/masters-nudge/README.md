# Masters’ Nudge

An optional structural second opinion while Codex writes code. Passing tests can leave independent copies of state, competing update paths, or responsibilities spread across callbacks. After a product-code `apply_patch`, this plugin asks another Codex model to read the task and relevant code and suggest one concrete structural alternative. Codex decides whether to use it.

## Evidence and cost

In a recorded Element device-selection task, a delivered suggestion was followed by a change requiring the selection list and its updater together, choosing their source together. Both the with- and without-plugin answers passed fixed acceptance. The with-plugin run took about 87.5 seconds longer. This is one observed adoption, not evidence that every suggestion improves code.

The broader study has mixed outcomes. With a Sol xhigh Actor and Sol medium Provider, reviewed structure was better in 3 paired deliveries, worse in 3, and tied in 2; cumulative attempt time increased 43.6%. The stronger xhigh Provider had better results, but this distributed runtime uses **medium** Provider effort. It does not promise faster delivery, lower usage, or a guaranteed improvement in design. Independent-user retention and long-term maintenance savings have not been measured.

See the [recorded adoption case](https://github.com/shihchengwei-lab/masters-nudge/blob/main/docs/examples/element-live-recording.zh-TW.md) and [study results and limitations](https://github.com/shihchengwei-lab/masters-nudge/blob/main/benchmark/model-comparison-20261004/PRIMARY.zh-TW.md). The homepage's chat animation is an explicitly labeled reconstruction of real records, not original screen footage.

## Requirements

- Codex desktop or CLI with `UserPromptSubmit`, synchronous MCP `PostToolUse`, and `turn_id` support. Review and trust both installed hook definitions before use. A plain MCP client without these Codex hooks does not provide this workflow.
- The `python` command in Codex's launch environment must resolve to Python 3.10 or newer. The runtime uses only the Python standard library; installation does not download Python packages or modify PATH.
- Git, a Git workspace for the coding task, a coding chat permitted to edit that workspace, and an executable, signed-in Codex CLI. Having desktop Codex alone does not establish that the CLI is installed or signed in.
- An OpenAI model available to the signed-in CLI account. Provider calls consume that account's applicable usage or billing. Each call can wait up to 1,200 seconds; the outer hook timeout is 1,320 seconds. The coding model waits during a synchronous review.

## Install and first safe prompt

Add the marketplace that distributes this package, install **Masters’ Nudge**, and enable it. Start a new Codex chat after installation or an update. Review and trust its hooks when Codex prompts; installation alone does not grant that trust. See [marketplace setup](https://developers.openai.com/plugins/build/plugins#build-your-own-curated-plugin-list) and [hook trust](https://learn.chatgpt.com/docs/hooks).

The first safe prompt is:

> Check whether Masters’ Nudge is ready. Do not call the Provider or change my settings.

The bundled `doctor` skill checks the runtime, Codex login, plugin enablement, and MCP initialization/tool discovery. It **does not call a model**. Unknown enablement or an unavailable query is not reported as disabled. `mcp_ready` proves discovery, not hook delivery or adoption. If prerequisites are missing, stop before a code trial and address the reported condition; the plugin does not install dependencies automatically.

Then explicitly choose a Provider model you can access, for example:

> Set Masters’ Nudge's OpenAI Provider model to gpt-6.1-sol.

The stored selection changes only the Provider, not the coding model. Without a saved selection, the fallback is `gpt-5.6-sol`; do not assume that fallback is available to your account. Provider effort is fixed at medium in this package.

For a first real trial, use a **disposable Git project with synthetic code**, give Codex a small normal code-change task, and let it use `apply_patch`. Avoid credentials, customer code, private data, and network-dependent tasks in the trial. Then ask:

> Show the recent Masters’ Nudge attempts for this trial.

`feedback` means a suggestion was returned; `silence` means the review completed without a suggestion. Faults have separate reasons. Check the returned context and the next code edit to establish delivery and adoption. Do not count silence, an untrusted hook, a failed call, or an initialization probe as evidence that the tool improved code.

## Permissions, authentication, and data

The package declares **Read** and **Write**: it reads the active Git workspace and writes its own settings, SQLite review journal, and error log. Its reviewer cannot modify the coding model's product files. It starts Python for hooks/MCP and a Codex CLI subprocess for the Provider, using the existing CLI login. No hosted Masters’ Nudge service or separate account is required. It does not bundle credentials or copy them into the package, and it does not automatically install dependencies, change your Codex configuration, commit, push, or deploy code.

The Provider may read non-ignored files within the Git workspace. Task text, edits, tool results, changed files that fit the shared material allowance, and file-origin facts are sent to **OpenAI through Codex**. The complete task-start file list stays local. Read-only review does not mean that code remains on the computer; don't enable it for a repository you are not permitted to send to OpenAI. See the [privacy description](https://github.com/shihchengwei-lab/masters-nudge#privacy).

By default, settings live at `~/.masters-nudge/config.json` and records at `~/.masters-nudge/data/feedback.sqlite3`, under the user's Windows profile or home directory. `MASTERS_NUDGE_DATA_DIR` overrides this location; keep a test location separate from normal use. Disabling or uninstalling the plugin does not erase those user-owned records. Removing records is a separate explicit action.

## Limits and safe failure

Only explicit `apply_patch` edits are observed; shell commands that write files opaquely are not covered. Recognized test-only edits skip the Provider. A turn stops reviewing after three suggestions or two silences, whichever comes first.

Missing identity/context, non-Git workspaces, unsupported settings, malformed output, subprocess failures, and timeouts are faults, not successful silence. Normal runtime failure returns a diagnostic and keeps review records when storage is available; it does not change the coding model's answer or claim acceptance. A failed journal may prevent record persistence. Do not retry a trial merely to obtain a more favorable suggestion.

A recorded Windows interruption during synchronous `PostToolUse` has previously lacked a corresponding `hook/completed` event. Inspect hook state and active processes after an interruption; see the [host issue](https://github.com/openai/codex/issues/46765). Disable Masters’ Nudge in the plugin browser to stop future reviews; disabling does not imply that an already-running review has been cancelled.

## Verification and support

The upstream automated suite exercises transport deadlines and cleanup, read boundaries, configuration, quotas, feedback/fault delivery, and relocated-package startup without paid model calls. These checks do not qualify every installed Codex version or OS integration. A full first-use check needs an installed, trusted hook and a real returned-context event in a new coding chat.

Use [repository issues](https://github.com/shihchengwei-lab/masters-nudge/issues) for setup or reproducible failures. Include versions and a sanitized error description; do not attach login files, tokens, private transcripts, personal paths, or customer code. The distributed implementation is under the [MIT license](LICENSE). No benchmark source trees, customer code, or private session logs are bundled in this plugin.
