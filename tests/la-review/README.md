# Loupe tests

The Loupe suite is offline except for the external reviewer tools that the installed skill may invoke during an actual user-requested review. The automated tests do not launch those tools. They validate the catalog and explicit skill metadata, inspect the package boundary, exercise deterministic Git diff capture, and test the cold copied runner with local simulated reviewer commands. Runner checks cover missing tools, exact scope forwarding, mixed outcomes, timeout process cleanup, and retained JSON output.

Run from the repository root:

```sh
npm run test:la-review
```

The test uses a disposable local Git repository and the host Python interpreter. It requires Python 3.6 or newer and Git, does not require provider credentials, and deletes its temporary fixture after a successful or failed assertion.
