# Libre Solar BMS revision reconciliation

This example uses unmodified public source files from
[LibreSolar/bms-8s50-ic](https://github.com/LibreSolar/bms-8s50-ic), pinned in
`source-manifest.json`. Keep the upstream CC BY-SA 4.0 license and attribution
with these source and derived files.

The scenario is a drone battery-pack assembly review using a general 3–8-cell
BMS reference board. It is not a new flight-qualified hardware design.

The older PCB revision (`ae37f94`, v0.1.1) has no D11 or D12. The v0.2 revision
(`3904858`) adds those diodes as part of its CAN interface changes. No component
record was deleted to create the discrepancy. The assembly task combines the
published current BOM with a placement projection from the older board.

`prepare-bms-demo.py` reads the pinned boards without executing upstream code.
It preserves the source coordinates and records the explicit transformation
to millimeters, a board-bottom-left origin, counterclockwise rotation and top
view. The current BOM defines the selected assembly population. Both placement
projections use that same population: 145 older SMT records versus 147 required
records. All 145 common placements are identical. The correction adds only
D11 at (49.6, 56.1) and D12 at (49.6, 50.7), both top side at 90 degrees.

R59 is present in the PCB but absent from the published BOM. The fixture retains
its geometry and an unresolved source warning; it does not infer a DNP decision.
Unknown source part numbers also remain unknown. The fixture does not assess
material availability, firmware, electrical function or flight readiness.

Reproduce the inputs with:

```sh
python3 tests/pcba-engineering/support/prepare-bms-demo.py
```

The repository master `npm run test:pcba-engineering` includes a CSV import,
exact two-reference correction, immutable revision and handoff verification
regression for this case. Captured Codex execution is qualified separately.
