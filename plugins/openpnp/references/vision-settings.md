# Typed native vision settings

Read `get_configuration.settings.vision_settings` and the current capability schema. This view describes the actual native machine → package → part inheritance, direct assignments, effective source holder, profile fields and pipeline parameter controls. A profile's stored enabled flag is distinct from the machine alignment's enabled state; the pinned fiducial locator does not use its profile enabled flag as an execution gate.

## Configuration workflow

1. Resolve the exact profile and holder IDs from the returned view. Inspect `effective_users` before editing a shared profile. Use `clone_vision_settings` to create an independent profile when only one package or part should change; stock profiles are immutable.
2. Submit a **vision-only** `plan_configuration` patch. Other setting groups require a separate plan. The patch can clone a profile, change its supported values, assign it to an exact holder, and explicitly detach then remove an unused profile. A null part/package assignment restores inheritance. Machine roots must retain explicit matching profile assignments.
3. Review affected holders and invalidation, then apply the returned configuration plan under the existing lease. Re-read the actual values. Changes invalidate job validation and prior vision/alignment evidence. They do not capture an image, execute a detector, or demonstrate alignment accuracy.

The seven change types are `clone_vision_settings`, `set_bottom_vision_settings`, `set_fiducial_vision_settings`, `assign_vision_settings`, `remove_vision_settings`, `set_vision_pipeline_stage`, and `set_vision_parameter`. Native model identity, subclass, shared references, bounds and stale-plan checks are authoritative. A deleted identity cannot be recreated under another case within the same patch.

## Pipeline parameters

Direct stage editing supports existing exact `BlurGaussian`, `Threshold` and manual `MaskHsv` scalar fields. It does not replace pipeline XML, add arbitrary stages, execute scripts, change image paths or change stage order.

Read `parameter_editable` and `pipeline_parameters` first. Native parameter stages and runtime property names can override stored stage values. A direct edit to an overridden stage is refused. Do not work around that refusal by rewriting XML or removing controls.

For an advertised editable parameter, use `set_vision_parameter` with its existing `parameter_name` and a typed value. Null clears the stored assignment and restores the native parameter default. The current subset accepts enabled native numeric or Boolean parameters targeting a native `Threshold` stage, with the parameter preceding its target. Numeric values satisfy both the native parameter bounds and the 0–255 integer threshold bounds. Automatic thresholding or a second active control can make a numeric threshold ineffective; those combinations are refused. Read the reported native bounds and value type before planning.

Stored thresholds alone do not qualify a detection result. Validate any production recipe against positive, negative and difficult images from the intended camera/part scope. The current native tests include synthetic scalar OpenCV fixtures; those are not a package detector corpus, camera calibration or independent placement inspection.

## Persistence limits

Native `Configuration.save/load` preserves supported profiles, pipelines and assignments. Version-one typed backup restoration still covers its earlier declared setting families. Vision readback is retained in those backups, but vision edits are **not restored** by that snapshot version; the omission and excluded state are explicit. A complete portable machine restore remains a separate capability.

This adapter requires the pinned stock stage class set, scalar existing parameter assignments and the exact native bottom-vision/fiducial implementations. Unsupported models remain visible as an unsupported adapter result; they are not silently converted or executed.
