---
name: select-provider
description: Show or configure the Masters' Nudge OpenAI Provider and model only when the user explicitly asks.
---

# Select a Masters' Nudge Provider

Find the plugin root above this file containing masters_nudge_cli.py. Use Python 3.10+:

```text
python masters_nudge_cli.py provider get --host codex
python masters_nudge_cli.py provider list
python masters_nudge_cli.py provider set openai --model <user-chosen-model>
python masters_nudge_cli.py provider reset
```

Explain the current selection in plain language. The first version supports only OpenAI via Codex; Claude and Ollama
are suspended, without fallback. OpenAI receives task/change/tool-result materials and the repository passages that
the Provider chooses to read. Do not select a different model without the user's request.
After an authorized change or reset, read the setting back and report it. Saving is not a runtime test.
