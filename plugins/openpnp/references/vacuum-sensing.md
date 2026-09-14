# Native simulator vacuum sensing

Use this workflow only when discovery advertises both the requested tool and `vacuum_sensing.available: true` with profile `native-vacuum-sensing-v1`. The initial source profile is `controlled-native-vacuum-v1`: explicit synthetic signals in real OpenPnP reads, checks and job processing. It does not model pneumatic physics or calibrate physical pressure.

## Configure and inspect

1. Read capabilities, status and configuration. Bind the observed nozzle, installed compatible tip, sensor actuator, Boolean vacuum valve, configuration revision and source generation. The active profile excludes a configured head pump, automatic homing and effectful actuator commands on machine enable/home/disable; it requires the supported direct-Z Safe Z geometry and native Simple planner.
2. If advertised, submit `set_vacuum_sensing_settings` through `openpnp_plan_configuration` as its own complete change. Apply its returned plan under the same current session/revision. Do not mix it with other settings or reconstruct an expired plan.
3. Read configuration again and verify native settings. The change invalidates sensing readiness and job validation. A changed configuration, source generation or native action makes earlier empty-nozzle evidence stale.

The complete setting uses these fields:

| Fields | Contract |
| --- | --- |
| `nozzle_id`, `nozzle_tip_id`, `vacuum_actuator_id` | Existing exact native identities; use discovered IDs. |
| `vacuum_sense_actuator_id` | Existing exact sensor identity; null is valid only when both sensing methods are None. |
| `reading_units`, `threshold_provenance` | `native-actuator-units`, `configured-thresholds`. |
| `method_part_on`, `method_part_off` | `None` or `Absolute`. Difference sensing is not in this profile. |
| `part_on_low`, `part_on_high`, `part_off_low`, `part_off_high` | Finite values within ±1,000,000. Absolute intervals require low < high; enabled on/off intervals must be disjoint. |
| `part_on_check_after_pick`, `part_on_check_align`, `part_on_check_before_place` | Explicit booleans for the native job check stages. |
| `part_off_check_after_place`, `part_off_check_before_pick` | Explicit booleans for the native job check stages. |
| `part_off_probe_ms`, `part_off_dwell_ms` | Integer milliseconds, each 0–1,000. |

Both None methods leave their stage flags inactive according to native semantics. Settings do not create a sensor source, confirm occupancy or authorize production.

If prior observations require a current empty-nozzle check before changeover, verify part-off while enabled, homed and at Safe Z, then disable under the supported policy before planning configuration. Do not use a direct valve command as occupancy evidence. Such an action invalidates earlier empty readiness.

## Read and verify

Both tools return asynchronous operations. Supply `session_id`, a fresh lowercase canonical UUID `request_id`, `expected_config_revision`, and `nozzle_id`.

- `openpnp_measure_sensor`: optionally supply `samples` from 1 to 32 (default 1). Collect immediate native values and their exact source/units. A sample count bound is not a hardware I/O deadline. Readings alone do not establish occupancy.
- `openpnp_verify_part_state`: supply `state: "part_on"` or `"part_off"`. The native check uses configured Absolute thresholds. Part-off actively pulses the vacuum valve and requires a homed, enabled nozzle at Safe Z with no model-held part. Read effect order from observed events; positive dwell can move the read after valve closure.

Poll by the returned operation ID. After a lost response, call `openpnp_get_request_status` with the original request ID. Do not issue a new ID to retry an uncertain sensing or valve operation.

Before starting or stepping a job, obtain fresh validation. The Bridge must check every participating sensing source before native initialization or the first feed. Preserve native retry counts and feeder consumption. A known missed pick can follow its configured bounded retry policy; failed reads and retained-part outcomes need different handling.

## Faults and transfer

A retained part, failed observation or incomplete journal publication must remain unresolved. A null OpenPnP model Part, later ordinary check, homing, configuration edit, takeover, reconnect, older job, or backup restore cannot clear that fault. Mandatory local valve-off cleanup may run after a fault; an unrecorded cleanup outcome remains uncertain. Use only an explicitly advertised [local sensing reconciliation workflow](sensing-reconciliation.md). When it is unavailable, report the retained fault and stop dependent actions.

Typed snapshots describe their sensing omissions; they cannot restore process-local source authority or occupancy. Portable transfer rejects enabled sensing until the receiving profile can qualify a fresh source. Preserve the original settings and fault history instead of removing them to make an archive or job pass.

## Explicit simulator scenarios

The `start-simulator` CLI accepts `--profile vacuum-sensing` and optional `--sensing-scenario success|missed-pick-retry|retained-after-place|lost-before-place|invalid-read`. The default is `success`. These are declared test inputs, not physical machine states. Other profiles do not accept a sensing scenario. Keep each run's source/scenario provenance with its results.
