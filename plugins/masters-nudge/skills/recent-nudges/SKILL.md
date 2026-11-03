---
name: recent-nudges
description: Read recent Masters' Nudge attempts only when the user asks for returned feedback, silence, failures, or audit history.
---

# Recent Masters' Nudge Attempts

Find the plugin root above this file containing masters_nudge_cli.py. Use Python 3.10+:

```text
python masters_nudge_cli.py recent-nudges --limit 10
```

Explain each stored attempt's time, outcome and feedback in plain language. A null outcome is an unfinished attempt;
feedback is a completed finding; silence is a completed no-feedback judgment; fault is an error.
The result of an older round keeps its recorded outcome even when a new request prevents delivery.
If historical records use other outcome names, report them as historical values, not current result types.
Only delivered=1 confirms the hook wrote its response to Codex. It does not prove Actor adoption.
delivered=0 is not proof that the hook sent nothing: writing the delivery receipt can also fail.
The detail contains source materials, Provider output, repository reads, duration and available usage.
Report missing fields as unavailable. Do not invent a retention deadline or infer an unrecorded delivery.
