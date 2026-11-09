# Drone BMS assembly review

The November 10 demo uses the standalone PCBA Engineering 0.1.2 plugin and
public Libre Solar BMS source. It reconciles two actual board revisions: the
current bill of materials requires D11 and D12, which were introduced after
the older placement export. All 145 common placements are unchanged. The
correction adds two source-backed rows and produces 147 required placements.

The installable plugin has no OpenPnP, browser, narration, video or machine
controller dependency. Node 22.19 or newer is required. Supplied CSV and JSON
inputs work without KiCad. A fresh Codex conversation uses normal Codex access;
the exported guide and handoff remain usable offline.

## Inputs

Copy `tests/pcba-engineering/fixtures/libresolar-bms/` to a fresh workspace as
`inputs/`, retaining the source files, manifest, README and license. The five
workflow inputs are:

| File | Role |
| --- | --- |
| `bom-v0.2.csv` | Current component identities and assembly population |
| `placements-v0.1.1.csv` | Earlier placement export: 145 SMT rows |
| `source-placements-v0.2.csv` | Current source geometry: 147 required SMT rows |
| `brief.json` | Board, quantity, coordinate conventions and source findings |
| `columns.json` | Explicit CSV column mapping |

Source revisions are `ae37f94d3589a6ae6f21e286309b628e5d833342` (v0.1.1)
and `3904858673206ee54baeda014a3e92408ab6a3ef` (v0.2) in
[LibreSolar/bms-8s50-ic](https://github.com/LibreSolar/bms-8s50-ic).
Original files are unchanged; derived assembly data retains CC BY-SA 4.0.
Regenerate with `python3 tests/pcba-engineering/support/prepare-bms-demo.py`.

## Actual Codex prompts

First, review:

> Use PCBA Engineering to review this drone BMS assembly. Compare the v0.2 BOM
> and v0.1.1 placement export with the supplied v0.2 source geometry in inputs/.
> Use brief.json and columns.json. Show the board and findings before changing
> anything. Keep the response brief.

Then, correct and verify:

> Add D11 and D12 from the v0.2 source geometry. Show the exact change plan,
> apply it, and rerun the same checks. Then export the assembly packet and
> independently verify it. Preserve existing placements, BOM identities,
> quantity and source findings. Show the corrected board and link the verified
> outputs.

The installed skills resolve their own CLI path. All operations must use the
installed `scripts/pcba.mjs`. Never edit a saved revision or plan to force a
pass. If a new input produces different findings, inspect those findings.

The complete recorded take used this single, authorized prompt:

> Run a fresh PCBA Engineering workflow from the original inputs into
> outputs/bms-recorded-take. Show the input revisions and before board. Preview
> and apply only D11 and D12 from source geometry, rerun checks, export to
> outputs/bms-recorded-handoff, and independently verify the files. Preserve
> existing placements, BOM identities, quantity and source findings. Show
> concise progress and the final board.

## Visible proof

1. Show the real input files and the actual Codex review request.
2. Open the generated board guide. Both D11 and D12 are absent from the older
   export and present in the current source; coverage is 145/147.
3. Show the two-reference plan: D11 at (49.6, 56.1) mm and D12 at (49.6, 50.7)
   mm, both top side at 90 degrees. Version 0.1.2 records CSV line-number
   changes separately from engineering changes.
4. Apply the plan and run unchanged checks against the new revision: 147/147.
5. Reopen the exported packet and independently verify all 11 artifacts,
   including CSV readback, source/revision identity and hashes.

The short film uses actual desktop footage, trimmed to remove waiting, plus
close-ups of the real generated outputs. The edit list must identify original
capture intervals. Keep the original recording and actual command evidence;
do not replace either with a recreated chat. The delivered edit is 147 seconds,
with 84 seconds of real-time native chat excerpts, six narration cues totaling
72 words, and a three-step indicator:
Inputs → Correction → Verified outputs.

The native SVG preview tab failed during the recorded run. The film pairs the
real chat with separately labeled saved artifacts. The actual HTML guide
subsequently passed interactive in-app browser checks over loopback HTTP.
If file preview is unavailable in a presentation environment, serve only the
chosen handoff directory on loopback, then open its guide in a browser:

```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory outputs/bms-recorded-handoff
```

Open `http://127.0.0.1:8765/assembly-guide.html`. Stop the local server when done.
The static guide has no network dependencies; this is a display alternative.

## Reuse and interpretation

The same workflow accepts other canonical packages or explicitly mapped CSV
files. Independent sensor and training-board cases remain in the master suite.
Use a fresh output directory for each export and preserve the hash manifest.
Run `status` or `resume` to recover an existing project's revision.

This is a drone battery-pack assembly application example using a general
3–8-cell BMS reference board. The result verifies assembly document
consistency. R59 remains a source-only footprint with unresolved BOM
population, and 11 populated parts retain unknown manufacturer part numbers.
Those two source findings stay in the reports. The task does not establish
electrical, thermal, firmware, soldering or flight qualification.
