import assert from 'node:assert/strict';

export const RECOVERY_TOOLS = ['openpnp_request_sensing_reconciliation', 'openpnp_get_sensing_reconciliation'];

// Only these exact descriptions changed when sensing profiles gained material tools.
// Callers retain historical hashes for every schema, annotation and routing field.
const materialDescriptionDelta = {
  "openpnp_get_material_loads": {
    "before": "Read cached virtual tray-load identities, retained native index observations, setup revision, available tray geometry digests with observation timestamps, and unresolved feed/change records. Supported only by the headless sustained simulator profile. Reads do not refresh hardware or verify physical stock. Full retained response paging remains available when large.",
    "after": "Read cached virtual tray-load identities, retained native index observations, setup revision, available tray geometry digests with observation timestamps, and unresolved feed/change records. Supported by the finite-tray sustained or attested sensing simulator profiles. Reads do not refresh hardware or verify physical stock. Full retained response paging remains available when large."
  },
  "openpnp_register_material_load": {
    "before": "Bind an existing virtual tray index or explicitly replace a full tray with the same native part and geometry. Use the observed material revision/digest and exact previous load ID for replacement; initial binding omits it. Requires a disabled, idle headless sustained simulator and empty nozzles. Returns an operation, preserves retired load history, and requires job revalidation. Enrolled loads block generic settings/restores and manual feeder tests. No physical refill, powered feeder, paused-job refill, or restart reattachment; unknown outcomes cannot be replayed.",
    "after": "Bind an existing virtual tray index or explicitly replace a full tray with the same native part and geometry. Use the observed material revision/digest and exact previous load ID for replacement; initial binding omits it. Requires a disabled, idle finite-tray sustained or attested sensing simulator and empty nozzles. Returns an operation, preserves retired load history, and requires job revalidation. Enrolled loads block generic settings/restores and manual feeder tests. No physical refill, powered feeder, paused-job refill, or restart reattachment; unknown outcomes cannot be replayed."
  }
};

export function normalizeMaterialDescription(definition) {
  const delta = materialDescriptionDelta[definition.name];
  if (delta) {
    assert.equal(definition.description, delta.after, 'A later description change needs its own reviewed delta');
    definition.description = delta.before;
  }
  return definition;
}
