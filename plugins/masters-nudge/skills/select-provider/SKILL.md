---
name: select-provider
description: Show or configure the Masters' Nudge Provider model and reasoning effort when the user asks to inspect or adjust them.
---

# Select a Masters' Nudge Provider

Find the plugin root above this file containing masters_nudge_cli.py. Use Python 3.10+:

```text
python masters_nudge_cli.py provider get --host codex
python masters_nudge_cli.py provider list
python masters_nudge_cli.py provider set openai --model <user-chosen-model>
python masters_nudge_cli.py provider set openai --model auto
python masters_nudge_cli.py provider set openai --reasoning-effort <user-chosen-effort>
python masters_nudge_cli.py provider set openai --model <user-chosen-model> --reasoning-effort <user-chosen-effort>
python masters_nudge_cli.py provider reset
```

Explain the current selection in plain language. The first version supports only OpenAI via Codex; Claude and Ollama
are suspended, without fallback. OpenAI receives task/change/tool-result materials and the repository passages that
the Provider chooses to read.

The default model is `auto`: Codex chooses its current recommended model, so a new model generation does not require
editing this plugin. A saved model ID stays pinned until the user changes it or selects `auto`. The default reasoning
effort is `medium`. Models and generations support different efforts and have different quality, wait time and usage;
the user chooses the trade-off. Consult current Codex model information when helping choose an available model or effort.
Do not switch a user's pinned model or reasoning effort merely because a newer model exists.

Model-only changes preserve the saved effort; effort-only changes preserve the saved model. `provider reset` restores
`auto` and `medium`. These choices affect the Provider, not the Actor.
After an authorized change or reset, read the setting back and report it. Restart Codex or reconnect the Nudge MCP so
its persistent process loads the selection. Saving is not a runtime test.
