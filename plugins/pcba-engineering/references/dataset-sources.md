# Reference datasets and evidence boundaries

The repository's test fixtures are data, not applications. No upstream scripts,
firmware, CI workflows, or machine-control code are executed to prepare them.

## SparkFun STCC4/SHT40 Qwiic sensor

- Upstream: <https://github.com/sparkfun/SparkFun_CO2_Sensor_STCC4_SHT40_Qwiic>
- Pinned commit: `836b63c8f684d57779cf0e699144ac056e0808ba`.
- Native source: `Hardware/SparkFun_Qwiic_CO2_Sensor_STCC4.kicad_pcb`,
  and `.kicad_sch`, with upstream `README.md` and `LICENSE.md`. The upstream
  `.kicad_pro` file is omitted because it contains machine-local settings;
  the retained source files are unchanged.
- Original board: 25.4 by 25.4 mm, revision `v10`, KiCad 10 source format.
- Hardware is attributed to SparkFun Electronics and provided under CC BY-SA
  4.0. The original notice is retained in the fixture's `source/LICENSE.md`.
  Normalized geometry is derived from that hardware and retains the same
  attribution and license. This demo is not endorsed by SparkFun.

The clean scenario means that the supplied assembly rows agree with their pinned
reference positions. It does not resolve the upstream component-identity or
procurement questions. Those remain in `sourceFindings`:

- The README describes SHT41 while the schematic and PCB identify SHT40 (U3).
- U3 has a placeholder production ID; U2's production ID differs between the
  schematic and PCB. A SparkFun internal production ID is not a supplier MPN.
- J3 has a four-pin footprint/symbol but the value `Conn_01x06`.

The population used for the package-consistency exercise follows the native
component data: 11 SMT references and one through-hole header. Four fiducials and
four mounting features are retained as non-populated context. Copper jumpers,
logos, and silkscreen features are not purchasable or placeable components.
Whether the header should be fitted in a real build remains a manufacturing
decision. No supplier MPNs are fabricated.

`missing-u3.json` is a disclosed synthetic scenario: U3 alone is removed from the
supplied placements, while the source files, BOM, geometry, and authoritative
placement reference remain intact. It is not represented as a defect shipped by
the upstream project.

## Opulo FTP data-only portability fixture

- Upstream: <https://github.com/opulo-inc/lumenpnp>
- Pinned commit: `93f8124f24e04245a856cc3c9731e8de32d38726` (`v4.1.0`).
- Native source: `pnp/pcb/ftp/ftp.kicad_pcb`, `.kicad_sch`, and `.kicad_pro`.
  The upstream `fp-lib-table` is omitted because it contains machine-local
  library paths; the retained source files are unchanged.
- Independent export: <https://github.com/opulo-inc/lumenpnp/releases/download/v4.1.0/ftp-top.pos>.
- Original board: 75 by 75 mm. The official position file includes 40 resistor,
  40 LED, and three fiducial rows. It is used to check coordinates independently
  of the KiCad source parser.
- Copyright Opulo. The hardware path at this pin is covered by CERN-OHL-W v2 in
  the retained upstream `source/LICENSE` notice. The notice separately covers
  library data and reserves Opulo branding/trademarks. No logo geometry or
  product branding is recreated in the neutral fixture visualization. Preserve
  the notice when redistributing this source or derived data.

The portability scenario uses a resistor-only population (R1–R40), preserving
unpopulated component/reference positions. Generic `R_Small` / `LED_Small`
values do not constitute a procurement BOM. This fixture neither loads nor
depends on OpenPnP and provides no machine-control or placement-accuracy claim.

## Reproducibility

The repository fixtures live under `tests/pcba-engineering/fixtures/`. Each has
an exact-source manifest with URLs, commit, license, and SHA-256 hashes.
Omitted machine-local files are recorded separately with pinned upstream URLs.
The reduced fixture snapshots support assembly-data checks, not complete native
project qualification. Review the original project rules and library setup from
the pinned upstream revision before attempting native ERC/DRC qualification.

The fixture preparation scripts live in `tests/pcba-engineering/support/`:

1. `dataset-fetch.py` fetches only an explicit list of pinned data files.
2. `dataset-normalize.py` derives canonical rows and source geometry.
3. `dataset-verify.py` checks source hashes, row integrity, the isolated synthetic
   fault, and independently exported positions. Missing CLI exports are recorded
   as skipped; `--require-cli` makes their absence a failure.

Coordinates use millimeters, a board-bottom-left origin, Y increasing upward,
and counterclockwise rotation. Bottom-side data remains in the top-view board
coordinate frame. Source-to-canonical transforms are stored in each fixture.

Visual copper segments and pads are parsed from actual source geometry. Package
boxes are courtyard envelopes (or pad bounds when no courtyard exists), not
validated three-dimensional component bodies. Internal board cutouts and drill
holes are preserved. Copper fills, solder mask, and complete mechanical solids
are outside the neutral visual's scope.

The artifacts establish digital package consistency and source provenance only.
They do not establish electrical function, solder quality, fabrication readiness,
thermal/EMC compliance, structural performance, or production acceptance.
