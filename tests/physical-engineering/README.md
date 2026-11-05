# Physical Engineering tests

Run the single master suite from the repository root:

```sh
export PE_RUNTIME="/absolute/path/to/physical-engineering-runtime"
python3.14 plugins/physical-engineering/scripts/engineering.py setup --runtime "$PE_RUNTIME"
export PE_PYTHON="$PE_RUNTIME/bin/python"
npm run test:physical-engineering
```

The master validates the installed package layout, metadata, local references and
absence of generated/private files, launches a cold copy's CLI, then runs all
Python `test_*.py` cases. It requires Python 3.14 and the actual Text-to-CAD CAD
kernel dependencies; a missing runtime is an error with setup guidance.

Python tests under `unit/`, `integration/` and `security/` need package
`__init__.py` files so unittest discovery includes them. Keep every automated
test connected to this master command. Tests use disposable study/CAD state,
preserve meaningful failure receipts, and never stop an unrelated CAD engine.

Coverage must include nominal geometry, battery B against old geometry, corrected
geometry, shifted mounting hole, accumulated assembly placements, stale export,
wrong units and attempted path escape. Include delivered-handoff reproduction and
the 110 × 60 mm transfer variant. The current test output is the authority for
which cases executed; this list alone is not a test receipt.

The marketplace dispatcher invokes the same root command. The plugin workflow
uses one master job and sequential Node versions; the shared marketplace suite
prepares a compatible Python runtime too.

Separate acceptance gates:

- Sparse public installation and a real installed-Codex run.
- Fresh-session recovery of the saved requirements.
- Independent operator reproduction and facilitator transfer exercise.
- Actual viewer/PDF visual review, continuous recording and complete playback.
- Required maintainer review, CLA and release checks.

Do not treat a skip, subprocess mock, cached receipt or geometry-only test as
proof of these gates.
