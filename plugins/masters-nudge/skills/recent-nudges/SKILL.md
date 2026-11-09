---
name: recent-nudges
description: Read recent Masters' Nudge attempts only when the user asks for returned feedback, silence, failures, or audit history.
---

# Recent Masters' Nudge Attempts

Find the plugin root above this file containing masters_nudge_cli.py. Use Python 3.10+:

```text
python masters_nudge_cli.py recent-nudges --limit 10
```

Explain each recorded attempt's time, outcome, reason and delivery status in plain language. Feedback is a completed finding; silence is a completed no-feedback judgment; fault is an error. The recent view is limited to 128 summaries and stores no feedback text or source material.
Only delivered=1 confirms that the hook wrote its response to Codex. For a feedback outcome with a null delivery status, report that writing the response was not confirmed. A receipt does not establish Actor adoption. A missing summary cannot establish whether an attempt occurred.
