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

The copied-package check runs `--help`; it verifies that the package can start
without repository-relative imports. The functional suite separately builds
real BRep geometry, exports and re-reads STEP/STL, and runs the delivered
reproduction with independent CAD state. That automated reproduction uses the
prepared test runtime. It does not establish a fresh dependency installation or
prove that an installed Codex session loaded the skills and CAD tools.

Python tests under `unit/`, `integration/` and `security/` need package
`__init__.py` files so unittest discovery includes them. Keep every automated
test connected to this master command. Tests use disposable study/CAD state,
preserve meaningful failure receipts, and never stop an unrelated CAD engine.

Coverage must include nominal geometry, battery B against old geometry, corrected
geometry, shifted mounting hole, accumulated assembly placements, stale export,
wrong units and attempted path escape. Include delivered-handoff reproduction and
the 110 × 60 mm transfer variant. The current test output is the authority for
which cases executed; this list alone is not a test receipt.

Test every admitted fixture combination when changing geometry: battery A/B
with frame X pitch 100/110 mm. Keep negative checks that expose misleading
success: an equal-volume shifted hole, an STL with matching bounds but changed
surface, excessive cavity clearance, and an unreadable or stale drawing. A
passing sidecar or unchanged overall mass cannot establish artifact correctness.
The fixture's nominal hole interfaces and declared planar wall measurements do
not qualify fasteners, a secured strap, arbitrary feature thickness or FDM
manufacture. Those limits belong in the public design rationale and handoff.

The drawing gate parses the actual two-page A3 PDF and checks its selectable
readback text for revision, part identity, units, measured dimensions, mounting
pitches, hole callouts, cavity width and declared wall/deck thickness. Negative
cases include unreadable, stale and inconsistent PDFs. The outlined dimension
artwork still needs visual review; text agreement does not establish that every
line, leader or label is legible and correctly placed.

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
