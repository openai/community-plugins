# Compatibility and qualification

This file describes the implementation candidate's support contract. It is not a
receipt for a completed recording, installed session, independent review or release.

| Component | Candidate |
| --- | --- |
| Physical Engineering | 0.1.0-preview.1 |
| CAD dependency | Text-to-CAD `cad` and `cad-server` 0.10.3 |
| Inspected upstream source | `4d42cb0bd9a61b46254bc8cf9bd9ae1f45c3295b` |
| Python | 3.14 |
| Host target | macOS arm64 |
| CAD kernel family | build123d 0.13, OCP 8.0; actual resolved versions recorded per runtime |
| Repository runner | Node.js ≥22.19 |

Record the actual package/source digest, fixture/verifier hashes, Python/kernel
versions and command results with every qualification. A version number alone
does not identify a changed local package.

Required evidence before a public recorded release:

- Pinned runtime creates a model, measures it, exports/re-reads STEP and renders a legible PDF.
- Baseline passes, old geometry fails battery B, corrected geometry passes.
- Shifted-hole, transformed-assembly, stale-export, wrong-units and path-escape cases are covered.
- Sparse/cold installed package works in a real fresh Codex session.
- A fresh chat resumes the same saved constraints and selected design.
- Delivered reproduction runs with independent CAD state and no original document cache.
- Another facilitator completes the 110 × 60 mm transfer exercise from the published instructions.
- Continuous A–E recording, 6–8 minute cut, captions, transcript and all artifact hashes agree.
- Required maintainer review, CLA and repository checks are complete.

Keep the recorded source, inputs, outputs and media immutable. Update this separate
compatibility note as tools change. Rerun the fixture after dependency or checker
changes; re-record affected chapters when workflow or validation meaning changes.
Do not relabel old footage as evidence of a newer release.

M1 covers digital geometry. M2 adds fabrication, inert-surrogate bench measurements
and a measurement-driven revision. Whole civilian-drone integration is M3.
