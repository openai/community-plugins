# Physical Engineering

**From an engineering brief to measured CAD and a repeatable handoff.**

Three Codex skills help you create a design, check exported geometry against saved requirements, and deliver editable source with rebuild instructions. Studies preserve requirement revisions, failed measurements and output hashes. Text-to-CAD supplies the CAD execution and viewing tools; this plugin supplies the engineering workflow and independent checks.

The plugin runs without the demo, its recording tools or this repository's test files. The [drone demo](../../docs/media/physical-engineering/README.md) is one worked example. Start a different project with the `mechanical-part` profile, your own requirements and editable model source.

## Supported designs

| Profile | Use it for | Automated checks and outputs |
| --- | --- | --- |
| `mechanical-part` | A custom single-solid mechanical part: a plate, mount, spacer or other shape expressed in build123d. Includes a sensor plate starter. | Declared dimensions, envelope, volume/mass, Z-axis through bores and box keepouts; STEP/STL consistency. Source, CAD, measurements, BOM and rebuild command. |
| `drone-module` | The bundled two-part battery tray and removable electronics bridge. | Specialized interface, clearance, support, insertion, planar-wall and interference checks; also a dimensioned PDF with automated content readback. |

The generic profile checks declared properties, not arbitrary engineering intent. A bounding dimension is not a minimum wall thickness; a mass estimate is not a strength calculation. Requirements outside the available measurement methods remain unresolved. Multi-part assemblies need an appropriate specialized checker; the drone's checks do not apply automatically to a new assembly.

The [custom-part guide](references/custom-part.md) defines the model/export contract and the supported requirements. The [drone design rationale](references/design-rationale.md) explains the example's hardware assumptions.

## Install and prerequisites

Target host: macOS arm64, Python 3.14, `uv`, Codex, and Text-to-CAD 0.10.3. Repository tests also need Node.js 22.19 or newer.

For a checkout of this PR:

```sh
codex plugin marketplace add /absolute/path/to/community-plugins
codex plugin add physical-engineering@community-plugins
codex plugin list --marketplace community-plugins --json
```

Start a fresh Codex session after installation. To install a GitHub candidate, use the repository's documented marketplace flow with its actual immutable revision and sparse paths `.agents/plugins` and `plugins/physical-engineering`. A local source copy is not evidence that Codex loaded an installed plugin.

Text-to-CAD is a separate dependency. Its [0.10.3 installation guide](https://github.com/earthtojake/text-to-cad/blob/v0.10.3/docs/install.md) covers installation, restart and telemetry. Review the installer's dry run before using `--only codex --no-telemetry`: it can replace an existing CAD plugin or change its active server. The helper below uses a separate pinned runtime and study-specific state without replacing that installation.

## Start your own part

Ask Codex:

```text
Use $engineer-subsystem to create a mechanical-part study from my brief
in a new directory. Record measurable requirements before building.
Author the CAD source, measure the actual exports, and preserve failures.
Identify requirements the available checker cannot assess.
```

For a runnable starter, use the sensor plate and then adapt its requirements and source:

```sh
PE_PLUGIN="/absolute/path/to/physical-engineering"
PE_STUDY="/absolute/path/to/new-part-study"
export PE_RUNTIME="/absolute/path/to/physical-engineering-runtime"

python3.14 "$PE_PLUGIN/scripts/engineering.py" setup --runtime "$PE_RUNTIME"
python3.14 "$PE_PLUGIN/scripts/engineering.py" init "$PE_STUDY" --profile mechanical-part
python3.14 "$PE_PLUGIN/scripts/engineering.py" build "$PE_STUDY"
python3.14 "$PE_PLUGIN/scripts/engineering.py" verify "$PE_STUDY"
python3.14 "$PE_PLUGIN/scripts/engineering.py" resume "$PE_STUDY"
```

Use `init STUDY --profile mechanical-part --requirements brief.json` for your own validated requirements. Edit the study's `src/model.py` to implement the brief; the included model is a plate starter, not a universal shape generator. The installed verifier stays outside editable source.

Save a revised requirements file, then run each command separately:

```sh
python3.14 "$PE_PLUGIN/scripts/engineering.py" change "$PE_STUDY" --requirements revised-brief.json --reason "Move the mounting interface"
python3.14 "$PE_PLUGIN/scripts/engineering.py" verify "$PE_STUDY"
# Inspect the real result against the unchanged geometry before revising source.
python3.14 "$PE_PLUGIN/scripts/engineering.py" build "$PE_STUDY"
python3.14 "$PE_PLUGIN/scripts/engineering.py" handoff "$PE_STUDY"
```

Changing requirements preserves old geometry. It does not guarantee a failure: show what the checker actually measures. A build snapshots the current source and active requirements; a handoff rebuilds and checks every delivered file.

## The three skills

| Skill | Result |
| --- | --- |
| `engineer-subsystem` | Select a supported profile, establish requirements, author or revise the model, resume a saved study |
| `verify-engineering-design` | Measure actual exports; preserve pass, fail or unknown results with their provenance |
| `prepare-engineering-handoff` | Regenerate a consistent delivery and rebuild it in a separate destination |

Each study has an authoritative `study.json`, generated `context.md`, editable `src/`, preserved `runs/` and a selected `handoff/`. Hashes detect changed inputs and stale artifacts; they do not authenticate untrusted code or make writable local evidence tamper-proof. An installed checker/runtime change requires a new study, preserving the original evidence.

Execute a delivered handoff with a new destination:

```sh
PE_RUNTIME="/absolute/path/to/rebuild-runtime" \
  sh "$PE_STUDY/handoff/reproduce.sh" /absolute/path/to/new-rebuild-study
```

Use a separately prepared runtime for a cold-runtime check. The rebuild compares measured values and the actual named STEP solids using
native Boolean differences. Fixed numerical limits are recorded in its receipt;
STEP metadata can differ between exports.

## Optional drone example

Use `init STUDY --profile drone-module` (also the backward-compatible default), then build and verify. Change with `change STUDY --battery B --reason TEXT`, verify the old geometry, then rebuild and hand off. The transfer exercise uses `--frame-pitch-x 110`.

The supported drone variants are battery A/B (100 × 35 × 25 / 100 × 40 × 25 mm) and frame X pitch 100/110 mm. Board mounts remain 30 × 30 mm. The old 36.5 mm cavity gives −1.75 mm clearance per side for battery B; widening it to 41.5 mm restores +0.75 mm. These values belong to this example, not the general workflow.

See the [example brief](references/drone-brief.md), [session guide](references/session-guide.md), [demo video](../../docs/media/physical-engineering/README.md) and [recording runbook](../../docs/physical-engineering-demo-runbook.md). Companion media and render dependencies are outside the installable package.

## Data flow and scope

The package declares Read and Write for selected local study, runtime and handoff directories. It executes Python model source and Text-to-CAD commands. Review source received from others before execution; this is not a sandbox for untrusted scripts. Setup downloads pinned public Python dependencies. CAD executes locally and serves its viewer on loopback, with study-specific state and upstream telemetry disabled through `CAD_TELEMETRY=0` and `DO_NOT_TRACK=1`.

Codex uses the model/provider and policies configured by its host. Local CAD does not imply local inference, offline qualification or customer-program isolation. Inspect evidence before sharing: files can contain local paths, and viewer URL query parameters are credentials.

Digital checks do not qualify fabrication, strength, thermal/electrical performance, hardware retention or flight. Mass uses a uniform solid density, excluding infill and purchased hardware. The drone's insertion check assumes removed straps; it does not test retention or fastener engagement. The sibling Autodesk Fusion plugin owns Autodesk-specific desktop/cloud/CAM integration; this plugin does not connect to Fusion.

## Development and release

```sh
export PE_PYTHON="$PE_RUNTIME/bin/python"
npm run test:physical-engineering
```

The single master runs package/cold-copy checks and all Node and Python functional, integration and security tests. Missing CAD dependencies fail with setup guidance. See [tests](../../tests/physical-engineering/README.md) and [compatibility](references/compatibility.md) for coverage and remaining qualification. Marketplace publication requires passing checks and maintainer approval; official Plugins Directory review is separate.

Original material is Apache-2.0 licensed. Dependency notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
