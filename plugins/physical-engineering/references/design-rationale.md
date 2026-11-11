# Drone-module CAD design rationale

This is a digital design candidate for a synthetic civilian drone module. The
engineering exercise is to preserve fixed interfaces while changing the battery
fit and frame pitch, then verify the exported assembly and reproduce its handoff.
It is a bounded parameterized design, not a general CAD synthesis engine or a
physically qualified aircraft subsystem.

## Requirements that drive the shape

The authoritative inputs are [requirements.json](../fixtures/drone-module/requirements.json).
The editable [model](../fixtures/drone-module/model.py) produces two printable
solids, `tray` and `bridge`; the battery and board are reference envelopes rather
than fabricated parts. Their presentation views do not change the acceptance
assembly.

| Design choice | Purpose and fixed interface |
| --- | --- |
| Battery tray with an open insertion end | Supports the battery at Z = 3 mm and permits straight insertion along +X with the bridge installed and retention removed |
| Side rails and rear stop | Locate a 100 mm long battery; nominal lateral clearance is 0.75 mm per side inside the accepted 0.5–1.0 mm interval |
| Removable electronics bridge | Carries the 40 × 40 × 2 mm board on the Z = 33 mm support plane while leaving the declared battery path clear |
| Four bridge attachments | Preserve centers at X = ±18 mm, Y = ±28 mm; the tray/bridge contact plane is Z = 3 mm |
| Four frame mounts | Preserve 3.2 mm through holes on the selected 100 × 60 or 110 × 60 mm pitch |
| Board mounting and component space | Preserve four 3.2 mm holes at a 30 × 30 mm pitch and the declared 40 × 40 × 12 mm component keep-out above the board |

Battery A is 100 × 35 × 25 mm and B is 100 × 40 × 25 mm. The intended cavity
widths are therefore 36.5 and 41.5 mm. The first B check deliberately retains
A geometry: a 36.5 mm cavity produces −1.75 mm clearance on each side. The
correct response is to retain that failure, revise the model and measure a new
export. Changing the requirement record alone must not move an existing face.

The frame-pitch exercise similarly preserves the old hole pattern until a new
build. Only battery A/B and X pitch 100/110 are admitted requirement changes.
The board, bridge attachment, support areas, service path, manufacturing
assumptions and acceptance tolerances stay fixed.

## Shape details and their intent

The current model makes the frame attachment, electronics support and strap
routing visible as distinct features. The dimensions below describe the modeled
geometry; the selected run's readback is the evidence that an export matches it.

| Feature | Modeled detail | Design intent |
| --- | --- | --- |
| Tray outline and frame mounts | R3 mm planform corners, Ø12 mm mounting pads joined by tapered arms | Give each frame fastener a defined seating region and connect it to the continuous tray floor |
| Bridge landing regions | Four Ø11 mm seats, with four external triangular gussets 4 mm wide near the frame arms | Preserve flat bridge contact and add material at the tray's outer transitions |
| Tray relief | Four 18 × 4 mm rounded floor slots | Remove peripheral material while preserving the central 80 × 20 mm wall-measurement region and the required battery support area |
| Strap guides | Two stations at X = ±34 mm; paired 18.2 × 9.2 mm ears with R2.5 mm corners and 13 × 3.5 mm slots; R0.5 mm rounds on both slot mouths | Provide a proposed threading route for nominal 12 mm webbing outside the cavity and bridge; widened lands retain at least 2 mm around the rounded openings |
| Rail and backstop edges | Top at Z = 11 mm with R0.8 mm edge rounds | Soften exposed handling edges while retaining complete planar datum faces from Z = 3 to 10 mm |
| Bridge deck and posts | 45 × 65 × 3 mm deck, R4.5 mm planform corners, R0.6 mm top-edge round and four Ø9 mm posts | Provide a compact removable electronics support with defined annular feet around the attachment bores |
| Bridge openings | Four 20 × 4 mm rounded slots at Y = ±14 and ±25 mm | Provide peripheral openings while retaining the central 20 × 20 mm measurement region and board mounting seats |

All attachment bores retain their 3.2 mm diameter and required through spans.
Relief is bounded by minimum measured contact areas: 3,000 mm² for the battery,
1,200 mm² for the board and 200 mm² for the bridge. The openings are not evidence
of improved cooling, and the arms, pads and gussets are not a validated load
model. No post-to-deck junction fillet or completed fastener/strap assembly is
claimed.

## What a passing digital check establishes

The separate [geometry checker](../scripts/verify_geometry.py) reads the STEP
assembly, resolves accumulated placements, and measures the named tray and
bridge. It checks valid solids, the declared hole interfaces, five planar wall
regions, lateral fit, support/contact area, occupied envelope, modeled mass and
overlap with the battery, insertion sweep, board and component keep-out. It also
compares delivered part STEP and STL geometry to the selected assembly.

This is a check of the declared geometry contract. The five planar wall regions
are the tray floor, side rails, backstop and bridge deck; they do not establish
the minimum thickness of every edge, transition or local detail. Unknown or
ambiguous measurements are incomplete, not passing. A visually attractive model
still needs these checks, and a passing model still needs visual and physical
review.

The 100 g limit uses actual modeled polymer volume at 1.25 g/cm³. It excludes
the battery, board, strap and purchased hardware. Neither the estimate nor a
smaller modeled volume establishes printed mass, stiffness or strength. The
maximum 140 × 90 × 60 mm occupied envelope includes the declared components and
keep-out; installed fastener and strap envelopes need separate review.

## Physical decisions still open

| Item | Qualification still needed |
| --- | --- |
| Frame attachment | Select a real frame, screw lengths, washers and nut/insert arrangement; check seating, engagement, edge distances, access and clamp loads |
| Bridge attachment | Select the fastener stack through the bridge and tray; verify retention, accessibility, tightening sequence and clearances to the battery and electronics |
| Board mounting | Confirm standoff/insulation needs, screw-head and nut clearances, connector access and wire routing for the actual board |
| Battery retention | Choose strap width, material, route and closure; check threading, tension, slip, abrasion, swelling allowance and accidental release using an inert surrogate |
| FDM manufacture | Select material, slicing orientation, supports, infill and tolerances; inspect overhangs and small transitions, then print and measure both parts |
| Service and loading | Bench-check insertion/removal, repeated assembly, vibration, creep, temperature and retention under declared loads before any broader integration |

The automatic service-path check explicitly uses the strap-removed state. Strap
guides and the proposed webbing route do not qualify the closed retention system.
Nominal 3.2 mm bores are clearance geometry; they do not specify usable threads,
nut capture, screw length or a completed joint. Small cosmetic radii or ribs do
not demonstrate stress reduction or adequate strength.

The bridge STL is exported in assembly coordinates. The handoff suggests placing
its deck on the build plate in the slicer, but that orientation and all support
settings require inspection. The assumed 0.4 mm nozzle and 0.2 mm layers are
inputs, not a printer/material qualification.

## Change and evidence discipline

Keep requirement revisions, source, failed checks and selected exports together.
After a model or checker change, use a new study with the new package identity
and rerun the exercise. Old snapshots, reports and recordings describe their
recorded geometry; they do not validate a revised model. The study history and
reproduction demonstrate traceability within this fixture. They do not establish
engineering approval, production release, load capability or airworthiness.
