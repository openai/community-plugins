# Record a real Codex CLI workflow

These operator tools record actual `codex exec --json` processes and render an
explicitly labeled event replay with the CAD files observed during that take.
They do not capture or imitate a native Codex desktop screen. The full replay
keeps elapsed time and waits. The teaching cut trims idle setup and shortens long
unchanged holds, with a precise map back to the original recorded timeline.

Use the qualified CAD Python runtime for rendering (Pillow is already pinned),
plus `ffmpeg`/`ffprobe`. Run the recorder with Python 3.14 or an available Python
3.11+ interpreter. The tools do not install dependencies or start an agent until
the operator invokes `phase`.

1. Prepare an exact Codex argv JSON file, an operator prompt text file for each
   real scene A–E, and metadata JSON with the plugin ref/version, actual model,
   runtime versions and qualification state. Check the installed CLI's help.
   Use `-` in the command's prompt position: the recorder sends the prompt on
   standard input. Do not add fabricated chat messages to the event journal.
2. Create a new take. Declare only the study/reproduction roots whose files may
   be observed, and add the repository root as a redaction root:

   ```bash
   python3 record_cli.py init --take "$PE_TAKE" --metadata metadata.json \
     --source-root "$PE_STUDY" --source-root "$PE_REPRODUCTION" \
     --redact-root "$PE_REPOSITORY"
   ```

3. Record each actual operator prompt and CLI subprocess:

   ```bash
   python3 record_cli.py phase --take "$PE_TAKE" --scene A \
     --prompt-file prompts/A.txt --cwd "$PE_STUDY" --command-file command.json
   ```

   Repeat for B, C, D and E. Capture preserves stdout and stderr, command failures
   and interruptions. A new `codex exec` without a resume argument is a fresh
   CLI session; record its actual thread events. The command file controls the
   requested sandbox, approvals, model and plugin selection—this tool supplies
   none implicitly. The caller must inspect the phase's observed exit status.

4. After a real run exists, generate its CAD snapshots and drawing previews.
   `capture_views.py` reads its recorded document IDs, checks every frozen run
   hash before and after rendering, and writes a **new** directory under the
   study's `media/`. It uses only that study's isolated `.cad` state. Set
   `PE_PLUGIN` to the actual installed plugin directory; omitting `--plugin`
   selects this repository's plugin. `pdftoppm` must be on PATH (or supplied
   explicitly with `--pdftoppm`).

   ```bash
   python3 capture_views.py --study "$PE_STUDY" --run selected \
     --runtime "$PE_RUNTIME" --plugin "$PE_PLUGIN" \
     --output "$PE_STUDY/media/baseline"
   ```

   The helper creates actual overview, insertion and exploded CAD snapshots,
   both complete PDF pages, and documented PDF detail crops for legible cavity
   and mounting dimensions. The crops use the drone fixture's drawing layout;
   review them when that layout changes. `--pdf-only` renders only drawing
   previews. `--run` also accepts `latest` or an exact run ID. The receipt lists
   source/output hashes, commands, failures and manual interventions.

   If native graphics creation fails inside the host sandbox, preserve the
   failed attempt. Run the helper through the host's authorized execution path
   with a **new** output directory and `--restart-owned-server`. This explicit
   option stops and restarts only the selected study's isolated CAD server;
   the receipt records that operator intervention. It does not stop the user's
   global CAD service or change the immutable run.

   Observe the files only after they exist. Set `PE_RUN` to the actual run ID
   printed by the helper. The recorder copies and hashes the observed bytes:

   ```bash
   python3 record_cli.py observe --take "$PE_TAKE" --scene B \
     --path "$PE_STUDY/media/baseline/exploded.png" --label "Baseline CAD — exploded fit view"
   python3 record_cli.py observe --take "$PE_TAKE" --scene B \
     --path "$PE_STUDY/media/baseline/drawing-cavity.png" --label "Baseline cavity — actual PDF detail crop"
   python3 record_cli.py observe --take "$PE_TAKE" --scene B \
     --path "$PE_STUDY/runs/$PE_RUN/checks.json" --label "Baseline measured checks"
   python3 record_cli.py observe --take "$PE_TAKE" --scene B \
     --path "$PE_STUDY/media/baseline/capture-receipt.json" --label "Baseline visual capture receipt"
   ```

   Never substitute an earlier rehearsal image. Images appear only after their
   observation timestamp. JSON is redacted in its published
   copy; the event records both source and published hashes. Source artifacts
   remain unchanged. Observe the revised and independently reproduced outputs
   in the same way. Optional transfer exercise observations remain in scene E.

5. Seal the take and render it into a new output directory:

   ```bash
   python3 record_cli.py finish --take "$PE_TAKE"
   "$PE_RUNTIME/bin/python" render_replay.py --take "$PE_TAKE" \
     --output "$PE_MEDIA" --layout cad-first
   ```

The optional `--layout cad-first` gives the captured CAD image most of the frame,
with a smaller actual CLI excerpt and measured-result strip. The default `proof`
layout remains available. Layout changes do not alter events, timing or source
artifacts.

Outputs include a 1920×1080 full MP4, teaching MP4, event captions (SRT), searchable
transcript, chapter timestamps, actual observed artifacts and a hash manifest.
The default teaching target is seven minutes. The cut begins at the first real
CLI phase. It initially retains up to ten seconds of each unchanged visible
state during CLI execution and eight seconds between actions. If that retains
less than six minutes, it may keep up to 15, 20 or 30 seconds of real in-process
waits. Long operator setup gaps remain capped at eight seconds. Every visible
state change, captioned action and artifact observation remains a boundary.
If retained activity exceeds the target it is compressed uniformly; if fewer
than six minutes remain, the tool reports the target unmet. It never stretches
source intervals or pads the capture to reach a duration.

The media manifest records each retained and omitted source interval, the
selected hold cap and the source/video mapping. Captions, chapters and the
on-screen source clock use that same map. The full replay's timing is unchanged.
Visual state is sampled once per video second; complete events remain in the
transcript. Review each observed image/report for several real seconds during
capture: observations submitted milliseconds apart cannot all have readable
screen time without inventing holds.

While a successful phase's artifacts are being reviewed, the replay keeps its
latest actual Codex response visible. Cleanup warnings remain in the complete
transcript and event journal. Failed phase exits retain their latest CLI output.
Oversized rapid caption batches show the real event count and latest complete
caption; the transcript preserves every event and artifact label.
Run the timing tests from the repository root with
`python3 tests/physical-engineering/support/test_replay_timeline.py`.

Keep the take's **`private/` folder private**. It holds raw prompts, CLI output,
local paths and potentially credential-bearing viewer URLs. Publish only the
reviewed renderer output, after checking every video chapter, artifact image,
caption and transcript for readability and disclosure. Encoder/decoder checks
do not replace watching the videos. The replay has no narrated audio; captions
describe real recorded events. Rendering a replay does not prove public install,
independent human facilitation or physical fabrication.
