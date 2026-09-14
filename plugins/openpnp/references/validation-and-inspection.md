# Validation and independent inspection

Keep three modes explicit: offline data/planning checks, a qualified physical dry-run recipe, and a first article with real parts and independent inspection. A simulator is useful evidence for software behavior, but it cannot qualify a physical placement.

The native `openpnp_validate_job` checks pending placements, parts/packages/heights, compatible native tips and changer requirements, calibration readiness, enabled feeder assignments and known finite stock. Unknown strip capacity is a warning, not an invented count. It requires a homed simulator, session/request IDs, and following its operation; the native processor checks again at start. A passing software model does not independently certify physical tool fit, clearance, registration quality, or placement quality.

With the candidate [load registry](boards-panels-and-registration.md#candidate-native-load-records), exact current load bindings are also required. Native reload, board flips/replacement and placement edits invalidate earlier validation. Use [complete native inspection](native-placement-editing.md#read-the-complete-native-model) when disabled/opposite-side/pseudo records matter; its software history and source fingerprint remain distinct from independent metrology.

Validate job/load/material/calibration revisions and prerequisites before physical work: part identity and height, compatible tip, usable material, fixture/envelope, registration, and supported vision. Turning vacuum off in a normal placement job does not define a safe dry run.

A first-article scope must have the profile's measurement thresholds and sample coverage. Record actual inspection method/instrument, X/Y/rotation residuals or supported categorical finding, uncertainty, board/load/placement IDs, source, artifacts, and accept/reject disposition through the available evidence path. If inspection ingestion is unavailable, keep the real evidence and report that production qualification cannot yet be recorded.

## Shipped offline inspection tools

For the separately advertised local GUI simulator workflow, use [native
loaded-board inspection](native-loaded-board-inspection.md). It binds a local
operator report to an actual completed native job and board load, retaining a
receipt after rechecking that scope. It does not authenticate the operator or
instrument, verify physical quality, or grant production authority.

`openpnp_record_inspection` evaluates supplied measurements and tolerances for a placement in a local canonical job artifact and saves an immutable inspection receipt. Supply the real measurement source, actor, timestamp, presence/polarity, uncertainty, and evidence references. The configuration identity and board-load identity are supplied scope values; this tool does not read native machine state, acquire measurements, authenticate the actor or external evidence, or grant production/hardware qualification. Its result declares `provenance_validation: "supplied-references-only"` and `production_qualified: false`. Do not substitute a native `cfg-N` revision for the schema's required SHA-256 configuration identity or invent that identity to make a call pass.

Use `openpnp_get_artifact` to read the saved local receipt. Use `openpnp_assess_inspection_coverage` with the exact job artifact, supplied configuration/load identities, and inspection artifact IDs to check accepted coverage. Missing, stale, rejected, or uncertain records do not count as accepted coverage. Passing supplied-data coverage does not authorize a native run or authenticate independent inspection. These are offline operations; their presence does not imply a native `openpnp_get_inspection` adapter.

Only the actual operator submission or authenticated instrument integration can establish its source. Never label an agent's visual impression as independent metrology. Native completion events establish software placement progress, not inspected quality. The native processor's documented steps include alignment and placement but are not external inspection. [Native job processing](https://github.com/openpnp/openpnp/wiki/Job-Processing)

Failed inspection leaves the component's physical presence intact and adds a hold/rework disposition. It must not reset a placement to pending and place another part on top. Partial inspection remains partial; any qualification is limited to the accepted scope. Changes to relevant settings/material/fixture/job invalidate the affected result.
