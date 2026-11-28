# Rebuild the guard-coupon bench

An indoor 65 mm quad has two candidate guard walls, 1.00 and 1.40 mm. This companion includes the revised aircraft, two separate printable specimens, a keyed test fixture and a custom three-axis recorder. The nominal tip-to-stop displacement is 2.00 mm for either specimen. Build the virtual station, then compare the enclosure, board and fixture in their installed positions. Measuring acceleration or ring-down requires physical hardware.

## Present the demo

Play the stage or narrated MP4 from [PR #61](https://github.com/openai/community-plugins/pull/61). The [presenter script](presenting/Presenter-runbook.md) has timed speaking cues and the [printable runbook](presenting/Presenter-runbook.pdf) fits a second screen. Download and open the [standalone HTML prompt deck](presenting/Prompt-runbook.html) for the copy buttons, or use the [plain-text prompts](presenting/Prompt-runbook.md).

A separate 561-second Desktop turn started with an empty output directory and authored its own accelerometer PCB. Its J1.3–U1.6 SDA route measured 5.1534 mm on front copper with zero vias. KiCad reported zero ERC findings, DRC findings, unconnected items and schematic-parity findings. The reviewed film board takes 7.393 mm and two vias between the same pins. The source replay below reproduces the reviewed board. [Independent Desktop board readback](evidence/desktop-board.json)

## Replay from editable source

Start at the repository root. The replay creates a new directory. It generates the populated board from its native KiCad project, uses that fresh STEP for the housing and combines the delivered housing and fixture. Each of the three Physical Engineering handoffs also rebuilds from its own source in a second directory. Set up the CAD runtime with Python 3.14 and uv once. [Physical Engineering setup](../../../../plugins/physical-engineering/references/assembly-designs.md)

```sh
export PE_RUNTIME="${PE_RUNTIME:-$HOME/.cache/physical-engineering/runtime}"
if [ ! -x "$PE_RUNTIME/bin/python" ]; then
  python3 plugins/physical-engineering/scripts/engineering.py setup --runtime "$PE_RUNTIME"
fi

companion=docs/media/physical-engineering/guard-bench
docker build -t physical-engineering-kicad:9.0.2 "$companion/ecad"
python3 "$companion/replay.py" ./guard-bench-replay --kicad-image physical-engineering-kicad:9.0.2
```

A native KiCad 9.0.2 installation with matching stock symbol/footprint libraries, `kicad-cli` on PATH and Python bindings for `pcbnew` also works with the same command after removing the image argument. If necessary, set `KICAD_PYTHON` to the interpreter containing `pcbnew`. The Docker run uses the local image with networking disabled after setup. The final `replay.json` contains the results and baseline/rebuild manifest digests.

| Output under the replay directory | What to open |
| --- | --- |
| `aircraft/` | Complete STEP, colored GLB, print meshes, offline viewer, formatted aircraft/bench BOM, board guide and flight-controller thickness readback |
| `board/` | Native project with local models, rendered schematic PDF, ERC, parity-aware DRC, netlist, placements, parts list, nine Gerber layers, plated/non-plated drills and populated STEP |
| `fixture/` | Assembly with one 1.00 mm specimen, keyed stop and an enclosure reference. The alternate directory contains the installed 1.40 mm specimen and its separate print. |
| `housing/` | Enclosure positioned around the freshly exported, populated recorder. The installed board contains discrete substrate, copper, pad, via and packaged-part geometry. |
| `station/` | Integrated STEP, GLB and a registration report. The real recorder assembly replaces the two enclosure reference bodies. |
| `aircraft-rebuild/`, `fixture-rebuild/`, `housing-rebuild/` | Independent rebuilds from each handoff’s delivered source |
| `housing-regression/exports/integration.json` | Rejected enclosure copy with the earlier 15 × 3 mm opening. The cable gauge intersects 28.70 mm³ of the wall. |
| `board/edge-regression-drc.json` | Separate rejected source copy with the connector moved 0.25 mm toward the edge |

The [full system workbook](sourcing/system-bom.xlsx) consolidates aircraft, both specimen configurations, the recorder PCB and workshop equipment. Its six sheets reconcile 69 source entries into 59 distinct rows. The two coupon columns represent alternative assemblies and shared fixture fasteners are counted once. [Flat CSV](sourcing/system-bom.csv) · [Reconciliation](sourcing/reconciliation.json)

## Engineering decisions

| Interface | Source decision and digital evidence | Physical work |
| --- | --- | --- |
| Aircraft controller | The BETAFPV Air 5IN1 specifies a 0.80 mm substrate. The earlier STEP measured 0.95 mm. The revised model holds the lower face at Z 3.50 mm and seats its upper components at 4.30 mm. | Check the received revision, compressed grommets, screw engagement and harness routing. [Controller specification](https://betafpv.com/products/air-brushless-flight-controller?variant=41142912745606) |
| Motor connection | The 0702SE II leads ship with connectors. The selected controller takes soldered motor wires. The workbook records lead termination and kit inclusions. | Confirm finished lengths, strain relief, prop fit and propulsion load. [Motor specification](https://betafpv.com/products/0702-ii-brushless-motors?variant=41478718750854) |
| Recorder board | An ST LIS3DHTR and side-entry JST SH connector share the 34 × 22 mm board. A 3.3 V host supplies power and reads I²C at address 0x18. The two-layer SDA route from J1.3 to U1.6 measures 7.393 mm and changes layers twice. | Fabricate, inspect orientation and electrical joints, verify identity and gravity response. [ST datasheet](https://www.st.com/resource/en/datasheet/lis3dh.pdf) · [JST drawing](https://www.jst-mfg.com/product/pdf/eng/eSH.pdf) |
| Connector hold-downs | KiCad requires 0.50 mm copper-to-edge clearance. Moving J1 inward 0.25 mm gives both hold-down pads 0.60 mm. The replay moves J1 back in a copy and requires both 0.35 mm violations. Moving an already-routed footprint also triggers other layout findings recorded in that copy. | Check actual fit and fabrication tolerances. |
| Recorder mounting | The housing uses a 64 mm ear spacing and checks the populated board against the lid, four mounts, screws and cable entry. A rectangular insertion gauge models approach to the header exterior. | Check the keyed mating plug, slack, strain relief, physical lid removal and mounting stiffness. |
| Guard experiment | Both coupons use the same locating features, 16.30 mm inner radius and 2.00 mm nominal travel. The installed station uses the baseline coupon and includes the alternate separately. | Print both in the same material and orientation, measure the seated gaps, alternate specimens across repeated trials and record the instrument response. |

[Aircraft sourcing workbook](sourcing/whoop-sourcing.xlsx) · [Aircraft sourcing CSV](sourcing/whoop-sourcing.csv) · [Dated supplier evidence](sourcing/source-receipts.json) · [Custom PCB procurement](sourcing/accelerometer-parts.csv)

The aircraft procurement ledger covers all 20 aircraft and 14 bench lines. The recorder BOM contains seven fitted components across five part numbers. The manufacturer listed the selected aircraft controller and motor variants as unavailable at the sourcing review on November 28, 2026 UTC. The ledger identifies alternate supplier listings and distinguishes a store pack from quantity installed. Recheck availability and variant before ordering.

## Source and evidence

The [aircraft](designs/aircraft/), [from-empty fixture](designs/fixture/) and [enclosure](designs/housing/) hold editable geometry and independent checks. The [native KiCad sources](ecad/design/) contain the electrical design. Extract [models.zip](ecad/models.zip) into the `ecad` directory of a working copy to open the native project with its component solids. The original library notices and modifications are documented in [attribution](ecad/ATTRIBUTION.md).

The [earlier controller readback](evidence/controller-substrate.json) compares the exported 0.95 and 0.80 mm solids. The [original preflight DRC](evidence/original-preflight-drc.json) records the two hold-down failures on the initial PCB layout. The source replay independently recreates these edge errors on a copy of the final routed board.

The three handoffs passed 37 aircraft, 33 fixture and 19 housing export checks. Their design-specific checks add seven controller substrate and seating checks, 26 drawing checks across both coupons and 12 board-to-case checks. The registered station has zero measured plate-to-case interference. The populated board has 5.505 mm lid clearance. A separate rectangular gauge extending as far as the molded connector mouth overlaps a tab by 0.0046 mm³, so the keyed mating plug still requires a physical trial.

Reference qualification used KiCad 9.0.2 on Debian 13 arm64 and macOS arm64 with Python 3.14.6, build123d 0.13.0 and Text-to-CAD 0.10.3. The [replay evidence](evidence/replay.json) records this run. Native STEP headers can contain export times. Each handoff’s manifest records the plugin's artifact and geometric comparisons.
