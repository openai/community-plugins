export { DomainError, LIMITS, digest, stableStringify, normalizeAngle } from './common.mjs';
export { parseCsv, parseBom } from './imports.mjs';
export { flattenJob, validateJob, transformPlacement, importCanonicalJob, exportCanonicalJob } from './geometry.mjs';
export { recordInspection, verifyInspection, assessInspectionCoverage } from './evidence.mjs';
export { createPlacementLedger, applyPlacementEvent, createMaterialLedger, applyMaterialEvent } from './ledger.mjs';
import { prepareJob as preparePlacementJob } from './imports.mjs';
import { importCanonicalJob } from './geometry.mjs';
import { fail } from './common.mjs';

/** Imports return a complete immutable-by-convention value; no native OpenPnP state is changed. */
export function prepareJob(options = {}) {
  if (!options || typeof options !== 'object') fail('invalid_input', 'Import options must be an object.');
  if (options.format === 'canonical-json') return importCanonicalJob(options.content);
  return preparePlacementJob(options);
}
