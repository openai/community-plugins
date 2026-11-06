# Engineering brief: drone module

Copy this into a new study. Keep a requirement revision for every accepted change.
These are synthetic workshop inputs, not a customer or airworthiness specification.

| Field | Reference exercise |
| --- | --- |
| Functional goal | Removable battery tray and electronics bridge for a small civilian quadcopter |
| Fixed frame interface | Four 3.2 mm holes on a 100 × 60 mm rectangular pitch |
| Fixed board interface | 40 × 40 × 2 mm board; four 3.2 mm holes on a 30 × 30 mm pitch |
| Component keep-out | 40 × 40 × 12 mm above the board |
| Battery A | 100 × 35 × 25 mm |
| Battery B change | 100 × 40 × 25 mm; keep other applicable requirements |
| Maximum subsystem envelope | 140 × 90 × 60 mm |
| Fit | 0.5–1.0 mm lateral clearance per side; supported battery base |
| Service state | Retention released; straight battery insertion along the declared X path |
| Process assumptions | FDM, 0.4 mm nozzle, 0.2 mm layer height |
| Declared planar walls | At least 2 mm at the fixture's measured regions |
| Material assumption | 1.25 g/cm³ illustrative polymer density |
| Mass | At most 100 g modeled polymer; hardware separately reported |
| CAD readback tolerance | 0.01 mm numerical; 0.05 mm interface position tolerance |
| Transfer change | 110 × 60 mm frame pitch, battery B, all other applicable limits preserved |

Record the exact placements and verifier version from the bundled fixture, not from a drawing guessed during the session. Record deviations explicitly; do not silently relax an acceptance limit.

The wider battery must first be checked against the existing battery-A geometry.
Then revise the design and show that the new measured cavity fits. The transfer
exercise changes the requirements through the CLI; it does not need code edits
to the plugin or verifier.

For another geometry class, establish its measurement method before claiming
this fixture's checks apply.
