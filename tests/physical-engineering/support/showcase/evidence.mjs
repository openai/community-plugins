import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

const SHA256 = /^[a-f0-9]{64}$/;
const STATES = {
  baseline_a: {
    status: "pass",
    battery: "A",
    width: 35,
    cavity: 36.5,
    clearance: 0.75,
    prefix: "a",
  },
  unchanged_a_with_b: {
    status: "fail",
    battery: "B",
    width: 40,
    cavity: 36.5,
    clearance: -1.75,
    prefix: "a",
  },
  corrected_b: {
    status: "pass",
    battery: "B",
    width: 40,
    cavity: 41.5,
    clearance: 0.75,
    prefix: "b",
  },
  reproduced_b: {
    status: "pass",
    battery: "B",
    width: 40,
    cavity: 41.5,
    clearance: 0.75,
    prefix: "reproduced",
  },
};
const REQUIRED_MESHES = [
  "a-tray",
  "a-bridge",
  "b-tray",
  "b-bridge",
  "battery-a",
  "battery-b",
  "board",
  "failed-b-intersection",
  "failed-b-intersection-section",
  "a-tray-section",
  "b-tray-section",
  "battery-a-section",
  "battery-b-section",
  "reproduced-tray",
  "reproduced-bridge",
];
const SOURCE_FILES = [
  "manifest.json",
  "requirements.json",
  "checks.json",
  "src/model.py",
  "src/design.json",
  "exports/assembly.step",
  "exports/tray.step",
  "exports/bridge.step",
  "exports/tray.stl",
  "exports/bridge.stl",
];

function demand(condition, message) {
  if (!condition) throw new Error(`Invalid showcase evidence: ${message}`);
}
function near(value, expected, label, tolerance = 1e-9) {
  demand(
    typeof value === "number" &&
      Number.isFinite(value) &&
      Math.abs(value - expected) <= tolerance,
    `${label} must equal ${expected} within ${tolerance}`,
  );
}
function vector(actual, expected, label) {
  demand(
    Array.isArray(actual) && actual.length === expected.length,
    `${label} has the wrong dimensions`,
  );
  actual.forEach((value, index) =>
    near(value, expected[index], `${label}[${index}]`),
  );
}
function equalArray(actual, expected, label) {
  demand(
    Array.isArray(actual) &&
      JSON.stringify(actual) === JSON.stringify(expected),
    `${label} disagrees`,
  );
}
function hash(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}
function relativeFile(root, relative) {
  demand(
    typeof relative === "string" &&
      relative.length > 0 &&
      !relative.includes("\\") &&
      !relative.includes("\0") &&
      !path.isAbsolute(relative) &&
      !/^[A-Za-z]:/.test(relative),
    "unsafe asset path",
  );
  const segments = relative.split("/");
  demand(
    segments.every((segment) => segment && segment !== "." && segment !== ".."),
    "unsafe asset path",
  );
  let current = root;
  for (const [index, segment] of segments.entries()) {
    current = path.join(current, segment);
    let stat;
    try {
      stat = fs.lstatSync(current);
    } catch {
      throw new Error(`Invalid showcase evidence: missing asset ${relative}`);
    }
    demand(!stat.isSymbolicLink(), `symlinked asset path ${relative}`);
    demand(
      index === segments.length - 1 ? stat.isFile() : stat.isDirectory(),
      `invalid asset path ${relative}`,
    );
  }
  return current;
}

/**
 * Synchronous renderer preflight. Throws on failure; reads files without changing them.
 * Validates declared evidence consistency and byte integrity, not CAD geometry or
 * the authenticity of an unsigned manifest. CAD acceptance remains in the frozen runs.
 */
export function validateEvidence(assetsDirectory) {
  const namedRoot = path.resolve(assetsDirectory);
  let stat;
  try {
    stat = fs.lstatSync(namedRoot);
  } catch {
    throw new Error("Invalid showcase evidence: missing assets directory");
  }
  demand(
    stat.isDirectory() && !stat.isSymbolicLink(),
    "assets directory must be a real directory",
  );
  const root = fs.realpathSync(namedRoot);
  const manifestBytes = fs.readFileSync(relativeFile(root, "manifest.json"));
  demand(manifestBytes.length <= 4 * 1024 * 1024, "manifest is too large");
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  demand(manifest.schema === 1, "unsupported manifest schema");
  demand(
    /^[a-f0-9]{40}$/.test(manifest.qualified_plugin_commit),
    "qualified source commit is missing",
  );
  demand(
    SHA256.test(manifest.qualified_model_sha256),
    "qualified model SHA is missing",
  );
  const frame = manifest.coordinate_frame;
  demand(
    frame?.units === "mm" &&
      frame.up === "+Z" &&
      frame.handedness === "right" &&
      frame.origin === "frame-plane center" &&
      frame.all_mesh_vertices === "assembly world coordinates",
    "mesh frame must be millimetres in the right-handed Z-up assembly world",
  );
  vector(frame.default_object_translation_mm, [0, 0, 0], "default translation");
  vector(
    frame.default_object_rotation_xyz_radians,
    [0, 0, 0],
    "default rotation",
  );
  vector(frame.default_object_scale, [1, 1, 1], "default scale");
  demand(
    manifest.meshes &&
      typeof manifest.meshes === "object" &&
      !Array.isArray(manifest.meshes),
    "meshes are missing",
  );
  const entries = Object.entries(manifest.meshes);
  demand(
    entries.length >= REQUIRED_MESHES.length && entries.length <= 256,
    "unexpected mesh count",
  );
  const assetHashes = {};
  for (const [name, mesh] of entries) {
    demand(mesh && typeof mesh === "object", `missing mesh ${name}`);
    const file = relativeFile(root, mesh.path);
    demand(SHA256.test(mesh.sha256), `${name} has an invalid SHA`);
    const bytes = fs.readFileSync(file);
    demand(
      Number.isSafeInteger(mesh.bytes) &&
        mesh.bytes > 0 &&
        bytes.length === mesh.bytes,
      `${name} byte size changed`,
    );
    demand(hash(bytes) === mesh.sha256, `${name} SHA mismatch`);
    vector(mesh.translation_mm, [0, 0, 0], `${name} translation`);
    vector(mesh.rotation_xyz_radians, [0, 0, 0], `${name} rotation`);
    vector(mesh.scale, [1, 1, 1], `${name} scale`);
    assetHashes[mesh.path] = mesh.sha256;
  }
  for (const name of REQUIRED_MESHES) {
    demand(
      manifest.meshes[name]?.path === `${name}.stl`,
      `required renderer mesh ${name} is missing or renamed`,
    );
  }
  const ids = new Set();
  for (const [name, contract] of Object.entries(STATES)) {
    const source = manifest.sources?.[name];
    demand(
      source?.status === contract.status,
      `${name} source status must be ${contract.status}`,
    );
    demand(
      typeof source.run_id === "string" &&
        /^[A-Za-z0-9_-]+$/.test(source.run_id),
      `${name} source run ID is missing`,
    );
    demand(!ids.has(source.run_id), "source runs must be distinct");
    ids.add(source.run_id);
    for (const file of SOURCE_FILES)
      demand(
        SHA256.test(source.files_sha256?.[file]),
        `${name} source SHA missing for ${file}`,
      );
    demand(
      source.files_sha256["src/model.py"] === manifest.qualified_model_sha256,
      `${name} model differs from qualified source`,
    );
    const scene = manifest.scenes?.[name];
    demand(
      scene?.status === contract.status && scene.source_run === source.run_id,
      `${name} scene source/status mismatch`,
    );
    equalArray(
      scene.meshes,
      [
        `${contract.prefix}-tray`,
        `${contract.prefix}-bridge`,
        `battery-${contract.battery.toLowerCase()}`,
        "board",
      ],
      `${name} displayed meshes`,
    );
    const measured = manifest.measurements?.[name];
    demand(
      measured?.battery === contract.battery,
      `${name} battery selection disagrees`,
    );
    vector(
      measured.battery_size_mm,
      [100, contract.width, 25],
      `${name} battery size`,
    );
    vector(
      measured.battery_bounds_y_mm,
      [-contract.width / 2, contract.width / 2],
      `${name} battery bounds`,
    );
    near(measured.cavity_width_mm, contract.cavity, `${name} cavity width`);
    vector(
      measured.cavity_inside_y_mm,
      [-contract.cavity / 2, contract.cavity / 2],
      `${name} cavity bounds`,
    );
    vector(
      measured.clearance_each_side_mm,
      [contract.clearance, contract.clearance],
      `${name} clearance`,
    );
    vector(measured.frame_pitch_mm, [100, 60], `${name} frame pitch`);
    vector(measured.board_pitch_mm, [30, 30], `${name} board pitch`);
    const expectedFailures =
      contract.status === "fail"
        ? [
            "battery_lateral_fit",
            "battery_interference",
            "insertion_interference",
          ]
        : [];
    equalArray(
      measured.failed_checks,
      expectedFailures,
      `${name} failed checks`,
    );
    for (const field of ["assembly", "board", "board_keepout"])
      near(measured.overlap_mm3?.[field], 0, `${name} ${field} overlap`, 1e-5);
    for (const field of ["battery", "insertion"]) {
      if (contract.status === "pass")
        near(
          measured.overlap_mm3?.[field],
          0,
          `${name} ${field} overlap`,
          1e-5,
        );
      else
        demand(
          Number.isFinite(measured.overlap_mm3?.[field]) &&
            measured.overlap_mm3[field] > 1e-5,
          `${name} must retain positive ${field} interference`,
        );
    }
  }
  for (const [state, prefix] of [
    ["baseline_a", "a"],
    ["corrected_b", "b"],
    ["reproduced_b", "reproduced"],
  ]) {
    for (const part of ["tray", "bridge"]) {
      const mesh = manifest.meshes[`${prefix}-${part}`];
      const sourceFile = `exports/${part}.stl`;
      demand(
        mesh.copied_original_bytes === true &&
          mesh.source?.run === state &&
          mesh.source.file === sourceFile,
        `${prefix}-${part} must use its actual recorded run output`,
      );
      demand(
        mesh.sha256 === manifest.sources[state].files_sha256[sourceFile],
        `${prefix}-${part} source SHA mismatch`,
      );
    }
  }
  for (const file of [
    "src/design.json",
    "exports/assembly.step",
    "exports/tray.step",
    "exports/bridge.step",
    "exports/tray.stl",
    "exports/bridge.stl",
  ]) {
    demand(
      manifest.sources.baseline_a.files_sha256[file] ===
        manifest.sources.unchanged_a_with_b.files_sha256[file],
      `failed run changed the original design: ${file}`,
    );
  }
  for (const name of ["corrected_b", "reproduced_b"])
    near(
      manifest.measurements[name].polymer_mass_g,
      43.9671,
      `${name} polymer mass`,
      0.00005,
    );
  const reproduction = manifest.reproduction;
  demand(
    reproduction?.status === "pass" &&
      reproduction.independent_cad_state === true,
    "independent reproduction must pass",
  );
  equalArray(reproduction.differences, [], "reproduction differences");
  near(
    reproduction.measurement_tolerance,
    0.01,
    "reproduction tolerance",
    1e-12,
  );
  demand(
    reproduction.source_run === manifest.sources.corrected_b.run_id &&
      reproduction.reproduced_run === manifest.sources.reproduced_b.run_id,
    "reproduction run linkage mismatch",
  );
  demand(
    SHA256.test(reproduction.source_files_sha256?.["reproduction.json"]),
    "reproduction comparison SHA missing",
  );
  for (const [field, state] of [
    ["corrected_checks.json", "corrected_b"],
    ["reproduced_checks.json", "reproduced_b"],
  ]) {
    demand(
      reproduction.source_files_sha256[field] ===
        manifest.sources[state].files_sha256["checks.json"],
      "reproduction checks SHA mismatch",
    );
  }
  demand(
    manifest.sources.corrected_b.files_sha256["requirements.json"] ===
      manifest.sources.reproduced_b.files_sha256["requirements.json"],
    "reproduction requirements changed",
  );
  const fullClash = manifest.meshes["failed-b-intersection"];
  demand(
    fullClash.source?.run === "unchanged_a_with_b",
    "collision must come from the failing run",
  );
  near(
    fullClash.cad_volume_mm3,
    manifest.measurements.unchanged_a_with_b.overlap_mm3.battery,
    "collision volume",
    1e-5,
  );
  demand(
    manifest.meshes["failed-b-intersection-section"].source?.mesh ===
      "failed-b-intersection",
    "collision section source mismatch",
  );
  for (const [section, source] of [
    ["a-tray-section", "a-tray"],
    ["b-tray-section", "b-tray"],
    ["battery-a-section", "battery-a"],
    ["battery-b-section", "battery-b"],
    ["failed-b-intersection-section", "failed-b-intersection"],
  ]) {
    const mesh = manifest.meshes[section];
    demand(mesh.source?.mesh === source, `${section} source mismatch`);
    near(mesh.cad_bounds_mm?.min?.[0], -1, `${section} minimum X`);
    near(mesh.cad_bounds_mm?.max?.[0], 1, `${section} maximum X`);
    demand(
      Number.isFinite(mesh.cad_volume_mm3) && mesh.cad_volume_mm3 > 0,
      `${section} must contain real material`,
    );
  }
  demand(
    manifest.verification?.all_frozen_source_hashes_match_before_and_after ===
      true &&
      manifest.verification.failed_run_uses_baseline_stl_bytes === true &&
      manifest.verification.actual_reproduced_meshes_included === true,
    "required generator provenance checks are missing",
  );
  return {
    manifest,
    manifestSha256: hash(manifestBytes),
    meshCount: entries.length,
    assetHashes,
    story: {
      batteryWidthsMm: [35, 40],
      cavityWidthsMm: [36.5, 41.5],
      clearancesMm: [0.75, -1.75, 0.75],
      framePitchMm: [100, 60],
      boardPitchMm: [30, 30],
      correctedMassG: manifest.measurements.corrected_b.polymer_mass_g,
      reproductionTolerance: reproduction.measurement_tolerance,
    },
  };
}
