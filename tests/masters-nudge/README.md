# Masters’ Nudge tests

Run `npm run test:masters-nudge` from the marketplace root. Requires Python 3.10+ on `python` and Git.

The master verifies source receipts, compiles the relocated package and runs the upstream suite in a temporary directory. Coverage includes MCP dispatch, delivery ordering, read boundaries, quotas, settings, deadlines, cleanup and safe failure. Offline Provider responses are simulated; these tests make no model calls.

[source-receipt.json](source-receipt.json) records the upstream commit and exact package/test bytes. Update them using the upstream `tools/export_community_plugin.py`. The package and copied tests are MIT-licensed.

See [test results](verification.md) and [installed-client verification](installed-check.md).
