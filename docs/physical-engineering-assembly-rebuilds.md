# Assembly rebuild evidence

On November 26, 2026, the Whoop 65 and tabletop sensor enclosure each rebuilt in a fresh output directory from the source shipped in their baseline handoff. Both rebuilds passed the exported-geometry checks and matched the baseline GLB, viewer, preview, print meshes, CSV, XLSX, board guide and editable source byte for byte. The host ran macOS arm64, Python 3.14.6, build123d 0.13.0 and Text-to-CAD 0.10.3 with 69 pinned packages.

| Handoff | Checks passed | Baseline manifest SHA-256 | Rebuild manifest SHA-256 |
| --- | ---: | --- | --- |
| Whoop 65 | 37 of 37 | [892e4e53f6326cb3483de51decb30be2a9ef330621373fb0b4e104cbebc542cd](media/physical-engineering/assembly-rebuilds/whoop-65-baseline.json) | [569129b8b42773054197d629fc6692e29a901d0ef3cbfe6e5ecb74b9ebcd3435](media/physical-engineering/assembly-rebuilds/whoop-65-rebuild.json) |
| Sensor enclosure | 19 of 19 | [90af43b3861028c0ac032c4ab8df18706489f0d7d0374bf33cfbfcb410767aa0](media/physical-engineering/assembly-rebuilds/sensor-enclosure-baseline.json) | [6b8e937029bf9af02ab98837ff4822e055355399f66cd6f1c632b3ec45ec5b06](media/physical-engineering/assembly-rebuilds/sensor-enclosure-rebuild.json) |

Each rebuild manifest records the digest of its own baseline under `reproduction.baseline_manifest_sha256`. The raw STEP digests differed between runs. The checker reimported the geometry and compared the recorded check results. Each measurement report also contains hashes of the specific exports it inspected.

## Measured results

| Build | Geometry | Bill of materials |
| --- | --- | --- |
| Whoop 65 | Both motor diagonals measured 65.00 mm. Each swept rotor disk cleared the frame by 0.80 mm. All 95 named display meshes matched their installed STEP bounds within 0.008 mm. Five print meshes passed, including both latch coupons. | Quantities on 19 aircraft lines matched modeled occurrences or complete wire sets, including 12 motor screws, four dock screws, and two propellers of each hand. Retention tape was the twentieth aircraft line. The Bench sheet contained 14 additional lines. [Whoop measurements](media/physical-engineering/assembly-rebuilds/whoop-65-measurements.json) |
| Sensor enclosure | The PCB assembly cleared the lid by 6.20 mm against a 6.1–6.4 mm requirement. The base, lid and board component STEP exports passed bidirectional solid-difference checks. Both STL prints and all 14 named display meshes passed. | Five installed lines covered the base, lid, board, four board screws and four lid screws. Three bench lines covered filament, driver and calipers. [Enclosure measurements](media/physical-engineering/assembly-rebuilds/sensor-enclosure-measurements.json) |

These results cover digital geometry, delivery contents and reproduction on one host using a prepared runtime. The Whoop's purchased boards and the enclosure PCB are mechanical references. Prototype fit, electrical operation and flight still require physical tests.

## Reproduce from a checkout

From the repository root on a supported host, select a prepared runtime or run the setup command once in an empty runtime directory. The CAD runtime setup requires Python 3.14 and uv.

```sh
export PE_PLUGIN="$PWD/plugins/physical-engineering"
export PE_RUNTIME="${PE_RUNTIME:-$HOME/.cache/physical-engineering/runtime}"
if [ ! -x "$PE_RUNTIME/bin/python" ]; then
  python3 "$PE_PLUGIN/scripts/engineering.py" setup --runtime "$PE_RUNTIME"
fi

runs=$(mktemp -d)
for design in whoop-65 sensor-enclosure; do
  first="$runs/$design"
  second="$runs/$design-rebuild"
  python3 "$PE_PLUGIN/scripts/engineering.py" assembly-build "$PE_PLUGIN/fixtures/$design" "$first" &&
    sh "$first/rebuild.sh" "$second" &&
    python3 "$second/source/scripts/engineering.py" assembly-verify "$second" || exit 1
done
```

Read each rebuild's **manifest.json** for the comparison result. Compare its **measurements.json** with the corresponding report above. To run the repository suite with the prepared runtime, use the command below. The recorded master run passed the cold copied-package CLI startup check, 135 Node tests and 113 Python tests. Coverage includes delivered-source rebuilds and rejection of incorrect quantities, moved components and shifted bores. [Master suite](../tests/physical-engineering/run.mjs) · [Assembly workflow](../plugins/physical-engineering/references/assembly-designs.md)

```sh
PE_PYTHON="$PE_RUNTIME/bin/python" npm run test:physical-engineering
```
