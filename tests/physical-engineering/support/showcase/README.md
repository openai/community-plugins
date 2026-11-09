# CAD construction and engineering-change showcase

This two-minute repository companion follows brief → build → check → adapt →
deliver. Start with the battery, board and mounting requirements, build the tray
and bridge through actual source operations, then check the assembled fit. A
wider battery exposes a failed fit; the revised tray preserves the mounting
interfaces and leads to a verified CAD handoff. Codex preserves the requirements
and failed run; a separate checker measures the delivered exports.

The earlier CLI replay contains the observed work, including setup and waits.
This presentation explains the construction and fit change with a deliberate
camera, readable section views and one measurement at a time. Use it before the live
exercise in the [demo runbook](../../../../docs/physical-engineering-demo-runbook.md).
The closing introduces that next live exercise: build from the brief, then change
the battery requirement, inspect the failed fit and generate the revised handoff. Keep the spoken story
and on-screen copy focused on that progression. Provenance, runtime details and
physical qualification belong in this companion material and the release
receipts. The primer has no LIVE indicator or claim of real-time execution.

The animation is a later explanation of qualified source and saved workflow
evidence. Its construction stages are newly derived from that original source;
any fresh original-CLI cross-check has separate receipts. It is not a continuous
CLI recording, a new CAD qualification, or proof of physical performance. The
recorded 0.1.0-preview.2 source and frozen run files remain unchanged. That
version supports the drone fixture. The current plugin also has a separate
[custom-part profile](../../../../plugins/physical-engineering/references/custom-part.md);
this video does not claim to demonstrate or qualify that newer behavior.

## Source and display provenance

`prepare_assets.py` reads explicitly selected baseline, failed and corrected
runs. It checks their manifest hashes before and after derivation, requires the
pass/fail/pass sequence and checks each model against the selected Git commit.
The asset manifest records source and output hashes, measurements, runtime
versions, mesh bounds, volume comparisons and display transforms.

| Visible element | Source and meaning |
| --- | --- |
| Tray and bridge construction | Real intermediate solids captured after original model operations; final solids are compared with baseline STEP, while intermediate parts remain incomplete explanations |
| A and B tray/bridge | Exact manufacturing STL bytes copied from their frozen runs; bounds and volume are compared with the corresponding STEP |
| Battery and board | Synthetic envelope solids from the requirement snapshot; no purchased component or electronics detail is implied |
| Pink seated interference | Actual Boolean intersection of the unchanged assembly STEP with battery B's seated envelope; its volume must agree with the recorded failed check |
| Section view | CAD intersection with the 2 mm slab at X = −1 to +1 mm, including the same section of the true interfering solid |
| Insertion interference | Intersection with the complete translation envelope; this is a swept volume, not an instantaneous collision |
| Reproduced parts | Meshes from the actual separate reproduction run, checked against its comparison receipt; they are not copies substituted from the corrected scene |

The failed scene retains the A tray while applying B's envelope. The corrected
scene switches to the discrete verified B tray. Do not morph between them and
imply that intermediate shapes were verified. Display colors, smoothing,
transparency, explosion offsets and camera motion are presentation choices.
Section meshes are explanatory assets, not extra printable parts.

Every mesh already contains assembly-world coordinates in millimetres, in a
right-handed frame with +Z up. Use identity object transforms: translation
`[0, 0, 0]`, rotation `[0, 0, 0]`, scale `[1, 1, 1]`. Do not center individual
meshes. Set the camera's up vector to `(0, 0, 1)`; if another viewer needs Y up,
rotate one common parent by −π/2 about X. Read the manifest's declared display
offsets for exploded and insertion views. These offsets never alter the source
parts or their acceptance measurements.

## Build prerequisites

- The existing qualified CAD Python runtime, including the pinned build123d/OCP
  stack, for asset preparation. No CAD server is launched by this helper.
- Node.js 22.19 or newer, Three.js **0.185.1**, and Playwright **1.62.1** with its
  Chromium browser installed for preview/video rendering. The standalone HTML
  bundler also uses the pinned esbuild **0.25.12** dependency.
- `ffmpeg` for video encoding and `ffprobe` for inspecting the finished media.

Install the companion's locked dependencies explicitly, from the repository
root. This keeps them outside the installed plugin and the CAD Python runtime.
Choose a fresh ignored build directory for each attempt.

```sh
PE_SHOWCASE="$PWD/tests/physical-engineering/support/showcase"
PE_BUILD="$PWD/work/physical-engineering/showcase-build"
PE_ASSETS="$PE_BUILD/assets"
npm --prefix "$PE_SHOWCASE" ci --include=dev --ignore-scripts
node "$PE_SHOWCASE/node_modules/playwright/cli.js" install chromium
PE_THREE_PACKAGE="$PE_SHOWCASE/node_modules/three"
PE_PLAYWRIGHT_PACKAGE="$PE_SHOWCASE/node_modules/playwright"
```

`--vendor` takes the Three.js package directory containing `build/` and
`examples/`. `--playwright-module` selects a separate Playwright installation
when needed; pass its absolute directory, as above. Without that option, the
renderer resolves its local `playwright` package. Record the actual browser
and encoder versions with the rendered release. These presentation dependencies
do not replace the qualified CAD dependency pins.

## Prepare assets from explicit runs

Run from the repository root with the setup variables above. Set the study,
run IDs, source commit and separately executed reproduction variables to the
selected evidence. `PE_ASSETS` must name a new directory; the helper refuses
an existing output.

```sh
"$PE_RUNTIME/bin/python" "$PE_SHOWCASE/prepare_assets.py" \
  --study "$PE_STUDY" \
  --baseline-run "$PE_BASELINE_RUN" \
  --failed-run "$PE_FAILED_RUN" \
  --corrected-run "$PE_CORRECTED_RUN" \
  --reproduced-study "$PE_REPRODUCED_STUDY" \
  --reproduced-run "$PE_REPRODUCED_RUN" \
  --source-commit "$PE_SOURCE_COMMIT" \
  --output "$PE_ASSETS"
```

The generator can also prepare assets without reproduction, but this complete
showcase uses the actual reproduced meshes and therefore needs that evidence.
Inspect the generated `manifest.json` and `qa.json` before rendering. A passing
asset receipt establishes the stated numeric/provenance checks, not visual
clarity or a new execution of the engineering workflow.

## Derive genuine construction stages

After preparing the main assets, run the construction helper against the same
explicit baseline. It needs the existing `manifest.json` but refuses any
existing construction mesh or `construction-manifest.json`, so use a fresh
asset directory for another attempt.

```sh
"$PE_RUNTIME/bin/python" "$PE_SHOWCASE/prepare_construction.py" \
  --study "$PE_STUDY" \
  --baseline-run "$PE_BASELINE_RUN" \
  --assets "$PE_ASSETS"
```

The default repository is the helper's repository; pass `--repo` explicitly
when needed. The helper checks the selected run's hashes and compares its
`src/model.py` with the qualified Git commit. Its checkpoint map supports the
qualified model with SHA256
`ab5d870b60273b86452621fa128ac4714578a2f42429a853f0c7be7543ba6628`.
It fails on a different source layout rather than guessing where to insert
construction checkpoints.

The helper removes only the `@model` document decorators and their import from
an in-memory AST copy, then inserts snapshot callbacks after existing top-level
operations. It executes the original tray and bridge bodies with the frozen
design snapshot. It does not reimplement the shapes, launch a CAD server or
change the installable plugin. The ordered output is:

| Part | Construction meshes, in order |
| --- | --- |
| Tray | `tray-01-base.stl`, `tray-02-mounts-seats.stl`, `tray-03-strap-guides.stl`, `tray-04-rails-gussets.stl`, `tray-05-final.stl` |
| Bridge | `bridge-01-deck.stl`, `bridge-02-posts.stl`, `bridge-03-final.stl` |

`construction-manifest.json` augments the unchanged main manifest. It binds to
the main manifest SHA, baseline run, qualified commit and model/design/STEP
hashes. Each stage records its original source-line span, operation hash,
mesh hash, actual CAD volume/bounds, tessellation readback and identity pose.
Final raw solids must match the baseline STEP within 0.001 mm³ for volume and
Boolean symmetric difference, and 0.00001 mm for bounds. Intermediate solids
are valid construction checkpoints, not parts that passed every fixture check.

The renderer and bundler call
`validateConstruction(assetsDirectory, baselineManifest)` from
`construction.mjs`, after the main `validateEvidence` check. The result is
`{ manifest, manifestSha256, stageCount, assetHashes }`. This checks byte
integrity, source linkage, exact checkpoint order, poses, finite metrics and
fixed comparison limits. It does not rerun CAD or authenticate unsigned JSON.
Native Boolean results come from the executed helper receipt. Construction
timings describe that derivation, not a live-screen recording or the duration
of a Codex session. Preserve a separate init/build/verify receipt when running
a fresh original-CLI cross-check, and distinguish it from recorded Codex work.

## Preview, serve and render

Use the installed package paths from setup and preserve earlier previews and
receipts when making changes. On macOS, append `--angle metal` to the preview
or video command to select the Metal rendering path. Omitting it uses the
default SwiftShader software path; review the output from the backend you use.

```sh
node "$PE_SHOWCASE/render.mjs" --mode preview \
  --assets "$PE_ASSETS" --vendor "$PE_THREE_PACKAGE" \
  --output "$PE_BUILD/previews" \
  --playwright-module "$PE_PLAYWRIGHT_PACKAGE"

node "$PE_SHOWCASE/render.mjs" --mode serve \
  --assets "$PE_ASSETS" --vendor "$PE_THREE_PACKAGE" \
  --output "$PE_BUILD/player"

node "$PE_SHOWCASE/render.mjs" --mode video \
  --assets "$PE_ASSETS" --vendor "$PE_THREE_PACKAGE" \
  --output "$PE_BUILD/render" \
  --playwright-module "$PE_PLAYWRIGHT_PACKAGE"
```

`preview` writes the chapter PNGs and `preview-index.json`. `serve` prints a
loopback URL for the interactive player; stop it with Control-C. It serves the
source HTML and selected assets rather than producing a standalone HTML bundle.
The preview serves only declared assets, the player modules and files beneath
the selected vendor root; descendant symlinks are refused. Optional caption text
is displayed literally in both the preview and standalone player.
`video` produces the silent visual master `showcase-silent.mp4` and a render
receipt with source, asset-manifest and video hashes. The default is 1920 × 1080
at 24 fps; `--fps` can select another rate. All generated assets, audio, videos,
runtime packages and receipts belong under ignored `work/physical-engineering/`,
outside the installable plugin.

## Narration and final files

Narration is a replaceable production input. Keep a timed script aligned to the
nine scene intervals, and identify the actual voice source in the media receipt.
A stock local neural voice may be used; it is not a recorded participant or
operator. The player can read `narration.wav` and `captions.json` beside the
asset directory. The visual renderer itself does not generate or mux a voice.
The [optional Kokoro narration helper](narration/README.md) provides manual,
isolated setup with pinned model hashes and dependencies, explicit local inputs,
per-segment WAVs and timing receipts. It never installs or downloads by itself.
After approving the script and generated WAVs, set `PE_SCRIPT` and
`PE_VOICE_SEGMENTS` to that script and the directory containing its
`segment-01.wav` files and `audio-manifest.json`. The mixer verifies their
hashes and places each clip 0.20 seconds after its scene starts, with at least
0.20 seconds remaining before the scene ends.

```sh
PE_SCRIPT="$PE_BUILD/narration-segments.json"
PE_VOICE_SEGMENTS="$PE_BUILD/narration-take-01"
python3 "$PE_SHOWCASE/mix_narration.py" \
  --script "$PE_SCRIPT" --segments "$PE_VOICE_SEGMENTS" \
  --output "$PE_BUILD/audio"
```

This writes the raw `narration.wav`, `captions.json`, `showcase.srt`,
`showcase.vtt` and `narration-mix.json`. The input WAVs must be mono 24 kHz
PCM24. Sentence caption boundaries are estimated within each measured speech
segment; they are not word-level forced alignment.

Prepare one accepted voice track for both outputs, retaining the raw mix and
receipt. This example normalizes loudness, then muxes the existing silent
video with AAC audio and selectable English captions. The output paths use
the recommended final names.

```sh
ffmpeg -n -i "$PE_BUILD/audio/narration.wav" \
  -af loudnorm=I=-16:TP=-1.5:LRA=11 -ar 48000 -ac 2 -c:a pcm_s24le \
  "$PE_BUILD/narration.wav"
cp "$PE_BUILD/audio/captions.json" "$PE_BUILD/captions.json"
mkdir -p "$PE_BUILD/final"
ffmpeg -n -i "$PE_BUILD/render/showcase-silent.mp4" \
  -i "$PE_BUILD/narration.wav" -i "$PE_BUILD/audio/showcase.srt" \
  -map 0:v:0 -map 1:a:0 -map 2:s:0 \
  -c:v copy -c:a aac -b:a 192k -c:s mov_text \
  -metadata:s:s:0 language=eng -movflags +faststart \
  "$PE_BUILD/final/physical-engineering-showcase.mp4"

node "$PE_SHOWCASE/bundle.mjs" \
  --assets "$PE_ASSETS" --audio "$PE_BUILD/narration.wav" \
  --captions "$PE_BUILD/captions.json" \
  --output "$PE_BUILD/final/physical-engineering-showcase.html" \
  > "$PE_BUILD/final/bundle-receipt.json"
```

The HTML embeds the validated meshes, manifest, player code, audio and captions
in one file. The bundler prints a JSON receipt containing the HTML byte count,
SHA256 and asset-manifest SHA256. Open the generated HTML offline and check
its controls, audio and captions. Keep the accepted voice track beside the
asset directory to use the same narration with `--mode serve`.

The mux command above creates a narrated, captioned MP4. A chaptered release
also needs a reviewed FFmetadata chapter file: set `PE_CHAPTERS` to that file,
add `-f ffmetadata -i "$PE_CHAPTERS"` after the subtitle input, and add
`-map_chapters 3` before the output. Preserve that file with the release recipe.
Keep all generated
media and receipts under the ignored build directory, and record final hashes
after any muxing or packaging change.

## Release review

Review every preview and the final encoded video. Check that:

- Each dimension and status describes the geometry currently shown, especially
  the unchanged A tray in the failed B scene.
- Sections, witness lines, captions and labels remain visible without overlap;
  narrated claims line up with the scene being explained.
- The companion manifest identifies the actual rebuilt parts, recorded
  comparison tolerance and metric units. Its reproduction receipt accurately
  describes the separate runtime on the same host.
- The closing introduces the next live battery-change exercise. Keep printing,
  hardware-fit and bench qualification in the companion notes; no strength,
  retention, electrical or flight qualification is implied by the CAD handoff.
- The decoded video, final audio/captions and public evidence contain no private
  paths, viewer tokens or unrelated session data. Keep raw capture data private.

Run the repository's canonical suite with the qualified runtime:

```sh
PE_PYTHON="$PE_RUNTIME/bin/python" npm run test:physical-engineering
```

Support regression tests belong to that same master command. The optional
browser/video build needs its own executed build receipt, decoder check and
visual/audio review. A passing master suite does not claim that an optional
video build ran in CI or that a film is visually correct. Keep the original
[CLI recording and replay](../README.md) available for the complete observed
workflow, prompts, timing and failure evidence.
