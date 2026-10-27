# Commissioning a selected machine profile

Commissioning is a dependency-ordered configuration workflow, not a request to accept every native issue. Resolve the exact hardware revision, controller/firmware, camera variants, nozzle topology, feeder types, and profile before preparing changes.

Build the concrete plan from supported object operations: drivers/axes, cameras, nozzles/tips, actuators, feeders, operating locations, packages, and vision settings. Preview shared references and invalidated calibration. Save a complete supported backup; avoid editing live XML as a shortcut.

The shipped `openpnp_list_issues` recomputes native Issues & Solutions on the executor; it requires session/request IDs and returns an operation. Its findings declare `apply_supported: false`. Bind each proposed solution to its returned identity/fingerprint and current revisions, then re-resolve before any separately supported apply. There is no shipped solution-apply adapter. A visible native UI action is not proof that the plugin can invoke it correctly. Keep unsupported solutions as a staged configuration plan.

Only invoke calibration recipes the active profile names. Use native returned measurements and required operator datums. Record completed steps, inputs, residuals, valid dependencies, and pending physical handoffs. A failed multi-file save is a configuration fault until restored/reconciled.

For LumenPnP V4.1, match the supplied configuration and manufacturer procedure to the actual hardware. Opulo explains that extra calibration suggestions can conflict with its intended setup. Preserve those profile-specific exceptions rather than demanding an empty Issues list. [Opulo calibration guidance](https://docs.opulo.io/openpnp/v4-1/preflight/calibration-philosophy/)

Do not infer generic production support from the plan's provisional machine candidate. Runtime capabilities and actual hardware qualification control what is executable.
