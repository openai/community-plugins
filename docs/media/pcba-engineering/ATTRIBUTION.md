# Demo provenance and reuse

The board is [Libre Solar BMS 8S50](https://github.com/LibreSolar/bms-8s50-ic),
a general 3–8-cell reference design used here for a drone battery assembly
review. Upstream sources are pinned at:

- Older board, v0.1.1: `ae37f94d3589a6ae6f21e286309b628e5d833342`.
- Current board and BOM, v0.2: `3904858673206ee54baeda014a3e92408ab6a3ef`.

The [public fixture](../../../tests/pcba-engineering/fixtures/libresolar-bms/README.md)
contains unmodified upstream files, exact source URLs/hashes and the coordinate
mapping. Both placement projections use the current BOM population. The newer
PCB adds D11 and D12; no source row was deleted to create the discrepancy.

Libre Solar's design is licensed under **CC BY-SA 4.0**. The board-derived movie
visuals, poster and handoff retain that license and attribution. Editorial
changes include recoloring, annotations, close-ups, comparison of the two
revisions and integration into the presentation. See the retained
[license text](LICENSE-CC-BY-SA-4.0.txt). The standalone plugin's software license
is separate. Product names, application UI and trademarks remain the property
of their respective owners; no endorsement is implied.

Narration uses the unmodified stock `af_heart` voice from Kokoro-82M v1.0 through
`kokoro-onnx` 0.6.1 at speed 1.0. No person's voice was cloned. Model/voice
provenance and SHA-256 values are retained in [narration-assets.json](narration-assets.json).
The Kokoro model is Apache-2.0 and the wrapper is MIT; neither model nor runtime
is redistributed here. The quiet sine-wave arpeggio is original editorial music.

The video records an installed **0.1.2** package; it is not a new recording of
0.1.3. That patch adds early rejection of ambiguous reference identifiers and
updates repository metadata. The release candidate's qualification is separate
from the movie's original run. The Apache-2.0 `recorded-verifier-0.1.2.zip`
is the exact original 22-file package, retained only to verify that pinned
historical handoff; the current plugin remains in `plugins/pcba-engineering/`.
Its SHA-256 is `e1e76914cce5215de63b515ebd8e9a00ba6a16ea5bba787ecec75c136461cf35`.

The actual desktop recording is cropped to the chat pane. Waiting between
excerpts is removed; retained excerpts run at 1×. Saved board artifacts are
shown in a separately labeled panel. The public edition additionally masks
personal filesystem paths (including a temporarily streamed Markdown link)
and unrelated composer/model-picker chrome. No chat, tool result, success
response or board geometry is reconstructed. [provenance.json](provenance.json)
records exact scene times, crop rectangles, mask rectangles and hashes.

The private full desktop capture is not distributed. Its SHA-256 and the prior
edit's SHA-256 remain in the receipt. Public inputs and the exact handoff are
included for scenario replay and independent file verification; byte-for-byte
recreation of the private screen recording is not promised.

Native SVG preview failed in that recording. The saved HTML guide subsequently
passed search, D11/D12 selection, source/export toggles and label checks in the
Codex in-app browser over loopback HTTP. These are distinct results. Technical
video checks do not establish human listening approval or engineering acceptance.
