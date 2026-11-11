# Compatibility and qualification

| Component | Support contract |
| --- | --- |
| Physical Engineering | 0.2.0-preview.1 |
| CAD dependency | Text-to-CAD `cad` and `cad-server` 0.10.3 |
| Inspected upstream source | `4d42cb0bd9a61b46254bc8cf9bd9ae1f45c3295b` |
| Python | 3.14 |
| Host target | macOS arm64 |
| CAD kernel | build123d 0.13 / OCP 8.0, exact pins and versions recorded per runtime |
| Repository runner | Node.js ≥22.19; CI runs 22.19, 24 and 26 sequentially |

## Plugin contract

`mechanical-part` accepts custom single-solid models with validated requirements
and fixed measurement methods. Its sensor plate is a starter, not a restriction
to one design. The [custom-part guide](custom-part.md) lists supported checks.
`drone-module` is a specialized two-part example with a narrower set of accepted
requirement changes. Both use the same revision, run, export and handoff lifecycle.

Required release evidence is a cold package start, actual CAD lifecycle tests
for both profiles, failed-geometry and stale/tampered-input cases, delivered
source rebuilds, dependency review, and the repository's required checks and
maintainer approval. The single master command runs automated checks; actual
installed-Codex discovery and host viewer behavior require separate evidence.
Report untested hosts explicitly. Headless geometry checks do not qualify the
viewer, another operating system or physical hardware.

Record source/checker hashes and runtime versions. Studies freeze their installed
code: preserve old studies and create a new one after code changes. Handoffs carry
the runtime scripts that produced them; use their own rebuild command. Hashes
provide consistency checks, not signatures or isolation from malicious Python.

## Demo contract

The drone recording, construction animation and facilitator exercises are
optional repository companion material. They are not runtime dependencies or
release prerequisites for unrelated designs. Media names its actual source
revision; do not present an old recording as a test of a newer package.

The included video uses the drone implementation from 0.1.0-preview.2. The
0.2.0-preview.1 release adds a general part profile and shared workflow changes;
its tests establish that behavior separately. The same drone model and demo
sequence remain available. No demonstration establishes physical manufacture,
retention, structural/electrical performance or flight qualification.

## Distribution

This is a preview candidate for the community marketplace. Maintainer approval,
CLA and required checks gate merge/publication. Inclusion in OpenAI's official
Plugins Directory is a separate process with its own listing, policy and
publisher requirements; this PR does not claim that approval.
