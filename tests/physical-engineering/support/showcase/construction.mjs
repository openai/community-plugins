import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { isDeepStrictEqual } from "node:util";

const SHA = /^[a-f0-9]{64}$/;
const QUALIFIED_MODEL_SHA = "ab5d870b60273b86452621fa128ac4714578a2f42429a853f0c7be7543ba6628";
const CHECKPOINTS = [
  ["tray-01-base", "tray", 5, [40, 45]],
  ["tray-02-mounts-seats", "tray", 7, [47, 63]],
  ["tray-03-strap-guides", "tray", 8, [66, 81]],
  ["tray-04-rails-gussets", "tray", 13, [82, 96]],
  ["tray-05-final", "tray", 18, [98, 108]],
  ["bridge-01-deck", "bridge", 0, [114, 114]],
  ["bridge-02-posts", "bridge", 1, [115, 117]],
  ["bridge-03-final", "bridge", 8, [118, 133]],
];
const SOURCE_FILES = ["manifest.json", "src/model.py", "src/design.json", "exports/tray.step", "exports/bridge.step"];

function demand(condition, message) {
  if (!condition) throw new Error(`Invalid construction evidence: ${message}`);
}
function finite(value, label, minimum = 0) {
  demand(typeof value === "number" && Number.isFinite(value) && value >= minimum, `${label} is not a finite nonnegative number`);
  return value;
}
function near(value, expected, label, tolerance = 1e-9) {
  demand(typeof value === "number" && Number.isFinite(value) && Number.isFinite(expected) && Math.abs(value - expected) <= tolerance, `${label} disagrees`);
}
function same(actual, expected, label) {
  demand(isDeepStrictEqual(actual, expected), `${label} disagrees`);
}
function bounds(value, label) {
  for (const key of ["min", "max", "size"]) {
    demand(Array.isArray(value?.[key]) && value[key].length === 3 && value[key].every(Number.isFinite), `${label}.${key} needs three finite coordinates`);
  }
  for (let axis = 0; axis < 3; axis++) {
    demand(value.max[axis] > value.min[axis], `${label} has an empty axis`);
    near(value.size[axis], value.max[axis] - value.min[axis], `${label}.size`, 1e-6);
  }
}
function boundsDifference(first, second) {
  return Math.max(...["min", "max"].flatMap(key => first[key].map((value, axis) => Math.abs(value - second[key][axis]))));
}
function hash(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}
function safeFile(root, relative) {
  demand(typeof relative === "string" && relative && !relative.includes("\\") && !relative.includes("\0") && !path.isAbsolute(relative) && !/^[A-Za-z]:/.test(relative), "unsafe relative path");
  const segments = relative.split("/");
  demand(segments.every(segment => segment && segment !== "." && segment !== ".."), "unsafe relative path");
  let current = root;
  for (const [index, segment] of segments.entries()) {
    current = path.join(current, segment);
    let stat;
    try { stat = fs.lstatSync(current); } catch { throw new Error(`Invalid construction evidence: missing file ${relative}`); }
    demand(!stat.isSymbolicLink(), `symlinked path ${relative}`);
    demand(index === segments.length - 1 ? stat.isFile() : stat.isDirectory(), `invalid file path ${relative}`);
  }
  return current;
}
function jsonFile(root, name) {
  const bytes = fs.readFileSync(safeFile(root, name));
  demand(bytes.length <= 4 * 1024 * 1024, `${name} is too large`);
  return { bytes, value: JSON.parse(bytes.toString("utf8")) };
}

/**
 * Validate construction evidence integrity and consistency before rendering.
 * This does not execute CAD or authenticate unsigned metadata. The generator's
 * independent STEP/Boolean comparison remains the native geometry evidence.
 * Pass the manifest returned by validateEvidence for the same assets directory.
 */
export function validateConstruction(assetsDirectory, baselineManifest) {
  const namedRoot = path.resolve(assetsDirectory);
  let stat;
  try { stat = fs.lstatSync(namedRoot); } catch { throw new Error("Invalid construction evidence: missing asset directory"); }
  demand(stat.isDirectory() && !stat.isSymbolicLink(), "asset directory must be a real directory");
  const root = fs.realpathSync(namedRoot);
  const main = jsonFile(root, "manifest.json");
  same(baselineManifest, main.value, "supplied baseline manifest");
  const record = jsonFile(root, "construction-manifest.json");
  const manifest = record.value;
  demand(manifest.schema === 1 && manifest.status === "pass", "construction did not pass supported schema");
  demand(manifest.baseline_manifest_sha256 === hash(main.bytes), "main manifest SHA mismatch");
  const baseline = baselineManifest.sources?.baseline_a;
  demand(baseline?.status === "pass", "A baseline must pass");
  demand(manifest.qualified_plugin_commit === baselineManifest.qualified_plugin_commit && /^[a-f0-9]{40}$/.test(manifest.qualified_plugin_commit), "qualified commit mismatch");
  demand(manifest.baseline_run_id === baseline.run_id && typeof baseline.run_id === "string" && /^[A-Za-z0-9_-]+$/.test(baseline.run_id), "baseline run mismatch");
  demand(baselineManifest.qualified_model_sha256 === QUALIFIED_MODEL_SHA, "unsupported model layout");
  for (const name of SOURCE_FILES) {
    const actual = manifest.source_files_sha256?.[name];
    demand(SHA.test(actual) && actual === baseline.files_sha256?.[name], `source linkage mismatch: ${name}`);
  }
  demand(manifest.source_files_sha256["src/model.py"] === QUALIFIED_MODEL_SHA, "source model differs from checkpoints");
  demand(SHA.test(manifest.generator_sha256) && SHA.test(manifest.instrumented_ast_sha256), "generator or AST hash missing");
  demand(manifest.qualified_inputs_unchanged === true && manifest.main_evidence_manifest_unchanged === true && manifest.geometry_changed_by_instrumentation === false, "qualified inputs or original geometry changed");
  const frame = manifest.coordinate_frame;
  demand(frame?.units === "mm" && frame.up === "+Z" && frame.handedness === "right" && frame.all_mesh_vertices === "assembly world coordinates", "construction frame must be millimetres in right-handed Z-up world coordinates");
  same(manifest.stage_order, CHECKPOINTS.map(([key]) => key), "construction stage order");
  demand(manifest.stages && Object.keys(manifest.stages).length === CHECKPOINTS.length, "unexpected construction stage count");
  const assetHashes = {};
  const previousTime = { tray: -1, bridge: -1 };
  for (const [key, part, index, lines] of CHECKPOINTS) {
    const stage = manifest.stages[key];
    demand(stage && stage.part === part && stage.source_function === part && stage.after_statement_index === index, `${key} source operation mismatch`);
    same(stage.source_lines, lines, `${key} source lines`);
    demand(SHA.test(stage.source_operations_sha256), `${key} operation hash missing`);
    demand(stage.path === `${key}.stl`, `${key} asset name mismatch`);
    const bytes = fs.readFileSync(safeFile(root, stage.path));
    demand(Number.isSafeInteger(stage.bytes) && stage.bytes > 0 && stage.bytes === bytes.length, `${key} byte size changed`);
    demand(SHA.test(stage.sha256) && hash(bytes) === stage.sha256, `${key} SHA mismatch`);
    same(stage.translation_mm, [0, 0, 0], `${key} translation`);
    same(stage.rotation_xyz_radians, [0, 0, 0], `${key} rotation`);
    same(stage.scale, [1, 1, 1], `${key} scale`);
    demand(stage.solid_count === 1 && stage.solid_valid === true, `${key} is not one valid solid`);
    demand(finite(stage.cad_volume_mm3, `${key} CAD volume`) > 0 && finite(stage.mesh_volume_mm3, `${key} mesh volume`) > 0, `${key} has empty geometry`);
    demand(Number.isSafeInteger(stage.triangle_count) && stage.triangle_count > 0, `${key} triangle count is invalid`);
    bounds(stage.cad_bounds_mm, `${key} CAD bounds`);
    bounds(stage.bounds_mm, `${key} mesh bounds`);
    demand(boundsDifference(stage.cad_bounds_mm, stage.bounds_mm) <= .05, `${key} mesh bounds exceed tessellation tolerance`);
    const volumeError = Math.abs(stage.mesh_volume_mm3 - stage.cad_volume_mm3) / stage.cad_volume_mm3;
    near(stage.mesh_volume_relative_error, volumeError, `${key} mesh volume error`);
    demand(volumeError <= .005, `${key} mesh volume exceeds tessellation tolerance`);
    finite(stage.elapsed_seconds_in_source_function, `${key} elapsed time`);
    demand(stage.elapsed_seconds_in_source_function >= previousTime[part], `${key} source time moved backwards`);
    previousTime[part] = stage.elapsed_seconds_in_source_function;
    assetHashes[stage.path] = stage.sha256;
  }
  for (const [part, finalKey] of [["tray", "tray-05-final"], ["bridge", "bridge-03-final"]]) {
    const comparison = manifest.final_comparisons?.[part];
    const final = manifest.stages[finalKey];
    const original = baselineManifest.meshes?.[`a-${part}`];
    demand(comparison?.status === "pass" && original, `${part} final comparison missing or failed`);
    same(comparison.limits, { volume_mm3: .001, bounds_mm: .00001, symmetric_difference_mm3: .001 }, `${part} comparison limits`);
    near(comparison.source_volume_mm3, final.cad_volume_mm3, `${part} final source volume`);
    near(comparison.baseline_step_volume_mm3, original.cad_volume_mm3, `${part} baseline volume`);
    bounds(comparison.source_bounds_mm, `${part} source bounds`);
    bounds(comparison.baseline_step_bounds_mm, `${part} baseline bounds`);
    same(comparison.source_bounds_mm, final.cad_bounds_mm, `${part} final source bounds`);
    same(comparison.baseline_step_bounds_mm, original.cad_bounds_mm, `${part} reference bounds`);
    const volumeDelta = Math.abs(comparison.source_volume_mm3 - comparison.baseline_step_volume_mm3);
    near(comparison.absolute_volume_difference_mm3, volumeDelta, `${part} volume difference`);
    demand(volumeDelta <= .001, `${part} volume differs from baseline`);
    const boundsDelta = boundsDifference(comparison.source_bounds_mm, comparison.baseline_step_bounds_mm);
    near(comparison.maximum_bounds_difference_mm, boundsDelta, `${part} bounds difference`);
    demand(boundsDelta <= .00001, `${part} bounds differ from baseline`);
    const first = finite(comparison.source_minus_baseline_mm3, `${part} source-minus-baseline`);
    const second = finite(comparison.baseline_minus_source_mm3, `${part} baseline-minus-source`);
    near(comparison.symmetric_difference_mm3, first + second, `${part} Boolean difference sum`);
    demand(first + second <= .001, `${part} Boolean difference exceeds tolerance`);
  }
  return { manifest, manifestSha256: hash(record.bytes), stageCount: CHECKPOINTS.length, assetHashes };
}
