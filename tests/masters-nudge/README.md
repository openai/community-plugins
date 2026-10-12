# Masters’ Nudge verification

Run the canonical master command from the marketplace root:

```text
npm run test:masters-nudge
```

Requires Python 3.10+ on `python` and Git. No Codex login, API key, model calls, customer data, or Python dependency installation is required. The master checks source receipts, compiles the relocated shipped package, and runs the complete original upstream suite in a temporary source-shaped checkout. Existing test files remain byte-identical to their source receipt; only the package is the implementation under test.

The suite covers MCP dispatch and delivery ordering, material/read boundaries, quotas and durable faults, settings and package relocation, subprocess deadlines and cleanup, and safe failure. It includes protocol/process integration tests with explicitly simulated Provider responses. It does not claim that a mock is a real OpenAI judgment, or that Python CI installs and trusts hooks in Codex.

`source-receipt.json` identifies the upstream commit and exact shipped plugin/test bytes. To update them, export from a clean committed upstream checkout using `python tools/export_community_plugin.py --output <community-checkout>`; do not maintain a divergent copy of the runtime. The implementation and copied upstream tests are MIT-licensed by shihchengwei-lab; the plugin contains its LICENSE.

Installed-client verification and its limitations are documented in `installed-check.md`. Live checks use a disposable synthetic Git project and a signed-in Codex account; they consume real model usage and are separate from the offline master. Faults are distinct from silence and are not passing live checks.

The [verification record](verification.md) lists actual results and outstanding qualification.
