# Physical Engineering

**A wider battery breaks the fit. Measure the failure, revise the parts, and rebuild the handoff.**

This implementation candidate provides three Codex skills for a synthetic civilian drone subsystem: a candidate FDM battery tray and removable electronics bridge. It preserves requirements, measures a failed fit, revises the two parts, and reproduces a consistent handoff. Text-to-CAD creates and views the models; a separate checker reads the actual exported geometry.

The implemented workflow supports one parameterized fixture. It is useful for learning and reviewing requirement-preserving CAD changes; it does not synthesize arbitrary subsystems from a new engineering brief. Physical printability, structural strength, electrical performance and flight remain unqualified.

## How the packages fit together

| Package | Responsibility |
| --- | --- |
| Physical Engineering | This fixture's requirements, study history, geometry acceptance, failed-run evidence, drawing/BOM consistency and reproducible handoff |
| Text-to-CAD | Separately installed CAD authoring, kernel operations, export and viewing; this package uses its pinned Python/CLI path |
| Autodesk Fusion | The repository's separate licensed Autodesk desktop/cloud/CAM integration; this fixture does not connect to Fusion |

Physical Engineering is a skills package with local executable helpers. It adds no MCP server or CAD account. See [the CAD design rationale](references/design-rationale.md) for the two-part architecture, measured interfaces and unresolved hardware choices.

The tray uses rounded mounting pads, tapered arms, external gussets, floor relief and two strap-guide stations. The removable bridge has a rounded deck, circular posts and peripheral openings. These details make the mounting and service intent visible while keeping the fixture's interfaces; they do not establish strength, printability or strap retention.

## Install and prerequisites

Target host: macOS arm64, Python 3.14, `uv`, Codex, and Text-to-CAD 0.10.3. Repository tests also need Node.js 22.19 or newer. A graphical desktop is needed for native CAD snapshots and screen recording; headless geometry checks do not prove the viewer or a recording.

For a candidate checkout, register its local marketplace:

```sh
codex plugin marketplace add /absolute/path/to/community-plugins
codex plugin add physical-engineering@community-plugins
codex plugin list --marketplace community-plugins --json
```

Start a fresh Codex session after installation. For a published candidate, use its actual repository and immutable commit, with sparse paths `.agents/plugins` and `plugins/physical-engineering`. Do not substitute `main` for a verified recorded revision.

Text-to-CAD is a separately installed dependency. Its [public installation guide](https://github.com/earthtojake/text-to-cad/blob/v0.10.3/docs/install.md) describes Codex plugin installation, restart behavior and telemetry. Pin 0.10.3 and use `--only codex --no-telemetry` when using its installer. Review the installer's dry run first: the normal installer can replace an existing CAD plugin or change its active server.

The commands below use a separate Python runtime and per-study CAD state. They do not require replacing your normal Text-to-CAD installation. The separate runtime proves the CLI/geometry integration; it does not alone prove that Codex loaded either installed plugin.

## One quick start

Ask Codex:

```text
Use $engineer-subsystem to start a study for the bundled drone-module
fixture in a new empty directory. Show the fixed interfaces and process
assumptions, create the battery-A design, and measure the actual exports.
Keep every failed run. Do not change requirements to make a check pass.
```

Equivalent reproducible commands, with the installed package's actual path:

```sh
PE_PLUGIN="/absolute/path/to/physical-engineering"
PE_STUDY="/absolute/path/to/new-drone-study"
export PE_RUNTIME="/absolute/path/to/physical-engineering-runtime"

python3.14 "$PE_PLUGIN/scripts/engineering.py" setup --runtime "$PE_RUNTIME"
python3.14 "$PE_PLUGIN/scripts/engineering.py" init "$PE_STUDY"
python3.14 "$PE_PLUGIN/scripts/engineering.py" build "$PE_STUDY"
python3.14 "$PE_PLUGIN/scripts/engineering.py" verify "$PE_STUDY"
python3.14 "$PE_PLUGIN/scripts/engineering.py" resume "$PE_STUDY"

# Preserve the old geometry and measure it against the wider battery.
python3.14 "$PE_PLUGIN/scripts/engineering.py" change "$PE_STUDY" --battery B --reason "Accommodate the wider synthetic battery"
python3.14 "$PE_PLUGIN/scripts/engineering.py" verify "$PE_STUDY"
# The preceding verification must fail: inspect and retain that result.

python3.14 "$PE_PLUGIN/scripts/engineering.py" build "$PE_STUDY"
python3.14 "$PE_PLUGIN/scripts/engineering.py" verify "$PE_STUDY"
python3.14 "$PE_PLUGIN/scripts/engineering.py" handoff "$PE_STUDY"
```

Run each command separately so the expected failure is visible. A new build is required to revise the geometry; changing requirements does not alter the old model. A new handoff must regenerate and check all delivered files. If passing `--runtime` explicitly instead of exporting `PE_RUNTIME`, put it after the subcommand.

For the transfer exercise, create a new requirement revision with `change "$PE_STUDY" --frame-pitch-x 110 --reason "Transfer exercise"`. First verify and retain the old mounting-pattern failure; then build, verify and regenerate the handoff. Do not edit the verifier.

## The three skills

| Skill | Result |
| --- | --- |
| `engineer-subsystem` | Establish or resume a study, preserve requirements and decisions, build or revise the two parts |
| `verify-engineering-design` | Measure actual geometry against the active requirement revision; retain failures and unknowns |
| `prepare-engineering-handoff` | Produce a revision-consistent package and execute a clean reproduction |

Each study uses `study.json` as its authoritative record, `context.md` as a generated resume summary, editable `src/`, preserved `runs/`, and a selected `handoff/`. A CAD document ID locates a document; source, requirements and output hashes identify a run.

## Requirements and measurement limits

The [brief template](references/brief-template.md) records the supported fixture. The CLI accepts exactly these requirement changes, each with a recorded reason:

| Input | Supported values | What stays fixed |
| --- | --- | --- |
| Battery selection | A: 100 × 35 × 25 mm; B: 100 × 40 × 25 mm | Seating plane, insertion direction and 0.5–1.0 mm lateral clearance per side |
| Frame X pitch | 100 or 110 mm | 60 mm Y pitch, four 3.2 mm mounting holes and attachment axes |

The teaching sequence is A/100 → B/100 → B/110. A/110 is also supported; the geometry suite includes all four combinations, and its executed results establish their status. Board mounts remain 30 × 30 mm, maximum occupied envelope 140 × 90 × 60 mm, and modeled polymer at most 100 g using the illustrative 1.25 g/cm³ density. Units are millimetres. Other requirement edits are refused; a new geometry class requires a new fixture and an appropriate checker.

Checks cover the declared interfaces, planar walls, support/contact and straight insertion path in the declared service state. They are not a universal CAD feature recognizer or a manufacturing certificate. Unresolved labels, invalid solids, kernel failures or incomplete measurements cannot pass. Mesh measurements and BRep measurements have different tolerances; neither silently substitutes for the other.

Mass is a solid-material estimate, not a slicer prediction or measured printed mass. Purchased hardware and the retention strap are reported separately. No FEA, CFD, firmware, whole-aircraft design or flight claim is included.

The mounting holes establish nominal interfaces, not a complete fastener specification. Screw lengths, engagement, washers, nut retention, tool access and installed hardware clearances need a physical assembly review. The battery insertion check assumes the retention strap is removed; it does not test a closed strap, retention force, abrasion or battery swelling. Slicing, fabrication and inert-surrogate bench checks are required before use. The [design rationale](references/design-rationale.md) lists these remaining checks and the reason for each.

## Data flow and permissions

The package declares Read and Write for the selected local study, runtime and handoff directories. It executes Python model scripts and Text-to-CAD commands. Treat editable model source as executable code and inspect source received from others before running it.

Setup downloads pinned public Python dependencies. Normal CAD work runs in local Python/kernel processes; Text-to-CAD serves its viewer on loopback. The wrapper uses study-specific state/cache/engine/launcher directories and disables upstream telemetry with `CAD_TELEMETRY=0` and `DO_NOT_TRACK=1`. It does not select a model provider, change Codex authentication or disable host policy.

Codex prompts and context use the model/provider configured by the user and host. A local CAD process does not mean model inference is local. CAD files and manifests can contain local paths; inspect exported evidence and footage before public sharing. Viewer URLs may contain access tokens and must be excluded from public reports and media.

Separate study directories reduce accidental cross-study use. They do not establish customer program isolation, offline support, an approved gateway or restricted deployment qualification.

## Reproduce and troubleshoot

Execute the reproduction command delivered in the handoff with a separately
prepared runtime and a new destination:

```sh
PE_RUNTIME="/absolute/path/to/reproduction-runtime" \
  sh "$PE_STUDY/handoff/reproduce.sh" /absolute/path/to/empty-reproduction
```

The CLI also exposes:

```sh
python3.14 "$PE_PLUGIN/scripts/engineering.py" reproduce "$PE_STUDY/handoff" /absolute/path/to/empty-reproduction
```

Use independent CAD state and a newly prepared runtime when testing cold reproduction. Compare geometry within recorded tolerances; STEP metadata need not have identical bytes.

| Symptom | Action |
| --- | --- |
| Dependency or Python version missing | Run `setup` in a new explicit runtime; use Python 3.14 |
| Default package index lacks the pinned release | Check the configured index; the release is on public PyPI |
| Snapshot needs a graphical desktop | Keep geometry results; record viewer qualification as pending |
| Old tray unexpectedly passes battery B | Inspect the measured cavity and active requirements; do not manufacture a failure |
| Changed checker, stale export or missing artifact | Preserve the failed run and regenerate from the selected source |
| CAD process outlives a command timeout | Inspect its owned state before retrying; do not stop unrelated CAD servers |
| Reproduction differs | Compare source, requirements, runtime versions and declared tolerances |

See [compatibility and qualification](references/compatibility.md), [the reusable session guide](references/session-guide.md), and the repository's [full recording runbook](../../docs/physical-engineering-demo-runbook.md). The repository runbook is companion material; all files needed to run the installed plugin are inside this package.

## Development

```sh
export PE_PYTHON="$PE_RUNTIME/bin/python"
npm run test:physical-engineering
```

The master covers package integrity and the Python functional/integration/security suite. Missing dependencies fail with setup guidance. Installed Codex behavior, graphical capture, independent facilitator acceptance and release review remain separate evidence gates. See the repository [test guide](../../tests/physical-engineering/README.md).

Original package material is Apache-2.0 licensed. Text-to-CAD and any reused upstream excerpts retain the notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
