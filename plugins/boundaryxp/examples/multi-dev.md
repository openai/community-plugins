## ADMIN → PM
Task ID: ROOT-DEMO

Summarize only these two supplied statements: A, "view reports in a local browser";
B, "support multiple subtasks with the same role". Deliver summary-a.md and
summary-b.md for PM review. DEV-01 owns A and DEV-02 owns B. Stop after the assigned
summary. Do not deploy, delete files, search the network or access production.
No budget, disk threshold or deadline is specified; do not invent one.

## PM → DEV (1/2)
Task ID: DEV-01
Parent Task ID: ROOT-DEMO

Use only statement A to write summary-a.md for PM review, then stop.
Inherit the parent restrictions. Do not modify summary-b.md.

## PM → DEV (2/2)
Task ID: DEV-02
Parent Task ID: ROOT-DEMO

Use only statement B to write summary-b.md for PM review, then stop.
Inherit the parent restrictions. Do not modify summary-a.md.
