import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { validateConstruction } from "../support/showcase/construction.mjs";

const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
const qualifiedModel = "ab5d870b60273b86452621fa128ac4714578a2f42429a853f0c7be7543ba6628";
const checkpoints = [
  ["tray-01-base", "tray", 5, [40, 45]],
  ["tray-02-mounts-seats", "tray", 7, [47, 63]],
  ["tray-03-strap-guides", "tray", 8, [66, 81]],
  ["tray-04-rails-gussets", "tray", 13, [82, 96]],
  ["tray-05-final", "tray", 18, [98, 108]],
  ["bridge-01-deck", "bridge", 0, [114, 114]],
  ["bridge-02-posts", "bridge", 1, [115, 117]],
  ["bridge-03-final", "bridge", 8, [118, 133]],
];
const sourceFiles = [
  "manifest.json",
  "src/model.py",
  "src/design.json",
  "exports/tray.step",
  "exports/bridge.step",
];
const unitBounds = () => ({ min: [0, 0, 0], max: [1, 1, 1], size: [1, 1, 1] });

// Deliberately synthetic mesh bytes and metrics: these tests exercise the
// renderer's evidence consistency contract without Python, CAD or Three.js.
function fixture(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "construction-evidence-"));
  const root = path.join(workspace, "assets");
  fs.mkdirSync(root);
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const links = Object.fromEntries(sourceFiles.map((file) => [file, sha(file)]));
  links["src/model.py"] = qualifiedModel;
  const baseline = {
    schema: 1,
    qualified_plugin_commit: "a".repeat(40),
    qualified_model_sha256: qualifiedModel,
    sources: {
      baseline_a: { status: "pass", run_id: "run-baseline-a", files_sha256: { ...links } },
    },
    meshes: Object.fromEntries(["tray", "bridge"].map((part) => [
      `a-${part}`, { cad_volume_mm3: 1, cad_bounds_mm: unitBounds() },
    ])),
  };
  const m = {
    schema: 1,
    status: "pass",
    purpose: "Derived construction explanation; intermediate solids are not qualified parts",
    qualified_plugin_commit: baseline.qualified_plugin_commit,
    baseline_run_id: baseline.sources.baseline_a.run_id,
    source_files_sha256: { ...links },
    generator_sha256: sha("synthetic generator"),
    instrumented_ast_sha256: sha("synthetic instrumented AST"),
    instrumentation: "Original statements with snapshot callbacks",
    coordinate_frame: {
      units: "mm", up: "+Z", handedness: "right", all_mesh_vertices: "assembly world coordinates",
    },
    stage_order: checkpoints.map(([key]) => key),
    stages: {},
    final_comparisons: {},
    qualified_inputs_unchanged: true,
    main_evidence_manifest_unchanged: true,
    geometry_changed_by_instrumentation: false,
  };
  for (const [index, [key, part, after, lines]] of checkpoints.entries()) {
    const bytes = Buffer.from(`Synthetic construction STL ${key}\n`);
    fs.writeFileSync(path.join(root, `${key}.stl`), bytes);
    m.stages[key] = {
      part, description: key, source_function: part,
      after_statement_index: after, source_lines: [...lines], source_operations_sha256: sha(key),
      path: `${key}.stl`, sha256: sha(bytes), bytes: bytes.length,
      cad_volume_mm3: 1, cad_bounds_mm: unitBounds(), solid_count: 1, solid_valid: true,
      translation_mm: [0, 0, 0], rotation_xyz_radians: [0, 0, 0], scale: [1, 1, 1],
      elapsed_seconds_in_source_function: index + 1,
      triangle_count: 12, bounds_mm: unitBounds(), mesh_volume_mm3: 1, mesh_volume_relative_error: 0,
    };
  }
  for (const part of ["tray", "bridge"]) {
    m.final_comparisons[part] = {
      status: "pass", source_volume_mm3: 1, baseline_step_volume_mm3: 1,
      absolute_volume_difference_mm3: 0, source_bounds_mm: unitBounds(), baseline_step_bounds_mm: unitBounds(),
      maximum_bounds_difference_mm: 0, source_minus_baseline_mm3: 0, baseline_minus_source_mm3: 0,
      symmetric_difference_mm3: 0,
      limits: { volume_mm3: .001, bounds_mm: .00001, symmetric_difference_mm3: .001 },
    };
  }
  function saveConstruction() {
    fs.writeFileSync(path.join(root, "construction-manifest.json"), JSON.stringify(m));
  }
  function saveBaseline() {
    const bytes = JSON.stringify(baseline);
    fs.writeFileSync(path.join(root, "manifest.json"), bytes);
    m.baseline_manifest_sha256 = sha(bytes);
    saveConstruction();
  }
  saveBaseline();
  return { workspace, root, baseline, m, saveConstruction, saveBaseline };
}

test("accepts consistent construction evidence and returns its exact hash and eight assets", (t) => {
  const f = fixture(t);
  const before = fs.readFileSync(path.join(f.root, "construction-manifest.json"));
  const result = validateConstruction(f.root, f.baseline);
  assert.equal(result.stageCount, 8);
  assert.equal(result.manifestSha256, sha(before));
  assert.deepEqual(result.manifest, f.m);
  assert.equal(Object.keys(result.assetHashes).length, 8);
  assert.equal(result.assetHashes["tray-01-base.stl"], f.m.stages["tray-01-base"].sha256);
  assert.deepEqual(fs.readFileSync(path.join(f.root, "construction-manifest.json")), before);
});

test("permits tiny explicitly reported CAD round-trip bounds drift", (t) => {
  const f = fixture(t);
  const comparison = f.m.final_comparisons.tray;
  comparison.source_bounds_mm.min[2] = -1e-7;
  comparison.source_bounds_mm.size[2] += 1e-7;
  comparison.maximum_bounds_difference_mm = 1e-7;
  f.m.stages["tray-05-final"].cad_bounds_mm = structuredClone(comparison.source_bounds_mm);
  f.saveConstruction();
  assert.equal(validateConstruction(f.root, f.baseline).stageCount, 8);
});

test("rejects changed mesh bytes even with unchanged length", (t) => {
  const f = fixture(t);
  const file = path.join(f.root, "tray-01-base.stl");
  const bytes = fs.readFileSync(file);
  bytes[0] ^= 1;
  fs.writeFileSync(file, bytes);
  assert.throws(() => validateConstruction(f.root, f.baseline));
});

test("rejects missing mesh evidence", (t) => {
  const f = fixture(t);
  fs.unlinkSync(path.join(f.root, "bridge-03-final.stl"));
  assert.throws(() => validateConstruction(f.root, f.baseline));
});

test("rejects a symlinked mesh even when its target bytes match", (t) => {
  const f = fixture(t);
  const file = path.join(f.root, "tray-01-base.stl");
  const target = path.join(f.workspace, "outside.stl");
  fs.renameSync(file, target);
  fs.symlinkSync(target, file);
  assert.throws(() => validateConstruction(f.root, f.baseline));
});

test("rejects a symlinked assets directory", (t) => {
  const f = fixture(t);
  const link = path.join(f.workspace, "linked-assets");
  fs.symlinkSync(f.root, link, "dir");
  assert.throws(() => validateConstruction(link, f.baseline));
});

for (const name of ["manifest.json", "construction-manifest.json"]) {
  test(`rejects a symlinked ${name}`, (t) => {
    const f = fixture(t);
    const file = path.join(f.root, name);
    const target = path.join(f.workspace, name);
    fs.renameSync(file, target);
    fs.symlinkSync(target, file);
    assert.throws(() => validateConstruction(f.root, f.baseline));
  });
}

for (const unsafe of ["../outside.stl", "/tmp/outside.stl", "C:\\outside.stl", "nested/../tray-01-base.stl", "./tray-01-base.stl", "nested//mesh.stl", "bad\0mesh.stl"]) {
  test(`rejects unsafe stage path ${JSON.stringify(unsafe)}`, (t) => {
    const f = fixture(t);
    f.m.stages["tray-01-base"].path = unsafe;
    f.saveConstruction();
    assert.throws(() => validateConstruction(f.root, f.baseline));
  });
}

const manifestMutations = [
  ["unsupported schema", (m) => { m.schema = 2; }],
  ["failed construction status", (m) => { m.status = "fail"; }],
  ["main manifest SHA mismatch", (m) => { m.baseline_manifest_sha256 = sha("other manifest"); }],
  ["different qualified commit", (m) => { m.qualified_plugin_commit = "b".repeat(40); }],
  ["different baseline run", (m) => { m.baseline_run_id = "run-other"; }],
  ["missing generator hash", (m) => { delete m.generator_sha256; }],
  ["malformed instrumented AST hash", (m) => { m.instrumented_ast_sha256 = "not-a-hash"; }],
  ["changed source input flag", (m) => { m.qualified_inputs_unchanged = false; }],
  ["changed main evidence flag", (m) => { m.main_evidence_manifest_unchanged = false; }],
  ["changed geometry by instrumentation", (m) => { m.geometry_changed_by_instrumentation = true; }],
  ["different units", (m) => { m.coordinate_frame.units = "cm"; }],
  ["different up axis", (m) => { m.coordinate_frame.up = "+Y"; }],
  ["different handedness", (m) => { m.coordinate_frame.handedness = "left"; }],
  ["local mesh coordinates", (m) => { m.coordinate_frame.all_mesh_vertices = "part-local coordinates"; }],
  ["reordered stage sequence", (m) => { [m.stage_order[0], m.stage_order[1]] = [m.stage_order[1], m.stage_order[0]]; }],
  ["missing stage key", (m) => { delete m.stages["tray-03-strap-guides"]; }],
  ["extra unproven stage", (m) => { m.stages.extra = structuredClone(m.stages["tray-01-base"]); }],
  ["missing final comparison", (m) => { delete m.final_comparisons.bridge; }],
];
for (const source of sourceFiles) {
  manifestMutations.push([`changed linkage for ${source}`, (m) => { m.source_files_sha256[source] = sha("wrong " + source); }]);
}
for (const [name, mutate] of manifestMutations) {
  test(`rejects ${name}`, (t) => {
    const f = fixture(t);
    mutate(f.m);
    f.saveConstruction();
    assert.throws(() => validateConstruction(f.root, f.baseline));
  });
}

const stageMutations = [
  ["changed declared SHA", (s) => { s.sha256 = sha("changed mesh"); }],
  ["changed byte size", (s) => { s.bytes += 1; }],
  ["wrong part", (s) => { s.part = "bridge"; }],
  ["wrong source function", (s) => { s.source_function = "bridge"; }],
  ["wrong statement index", (s) => { s.after_statement_index += 1; }],
  ["wrong source line span", (s) => { s.source_lines[1] += 1; }],
  ["missing operation hash", (s) => { delete s.source_operations_sha256; }],
  ["translated geometry", (s) => { s.translation_mm[0] = 1; }],
  ["rotated geometry", (s) => { s.rotation_xyz_radians[2] = .1; }],
  ["scaled geometry", (s) => { s.scale[0] = 2; }],
  ["invalid solid", (s) => { s.solid_valid = false; }],
  ["multiple solids", (s) => { s.solid_count = 2; }],
  ["zero CAD volume", (s) => { s.cad_volume_mm3 = 0; }],
  ["nonfinite CAD volume serialized as null", (s) => { s.cad_volume_mm3 = Infinity; }],
  ["negative elapsed time", (s) => { s.elapsed_seconds_in_source_function = -1; }],
  ["fractional triangle count", (s) => { s.triangle_count = 1.5; }],
  ["zero mesh volume", (s) => { s.mesh_volume_mm3 = 0; }],
  ["inconsistent mesh volume error", (s) => { s.mesh_volume_relative_error = .001; }],
  ["excessive mesh volume error", (s) => { s.mesh_volume_mm3 = .99; s.mesh_volume_relative_error = .01; }],
  ["inconsistent CAD bound size", (s) => { s.cad_bounds_mm.size[0] = 2; }],
  ["inverted CAD bounds", (s) => { s.cad_bounds_mm.min[0] = 2; s.cad_bounds_mm.size[0] = -1; }],
  ["nonfinite CAD bounds serialized as null", (s) => { s.cad_bounds_mm.min[0] = NaN; }],
  ["mesh bounds drift", (s) => { s.bounds_mm.min[0] = .1; s.bounds_mm.size[0] = .9; }],
];
for (const [name, mutate] of stageMutations) {
  test(`rejects stage ${name}`, (t) => {
    const f = fixture(t);
    mutate(f.m.stages["tray-01-base"]);
    f.saveConstruction();
    assert.throws(() => validateConstruction(f.root, f.baseline));
  });
}

const comparisonMutations = [
  ["failed final comparison", (c) => { c.status = "fail"; }],
  ["loosened volume tolerance", (c) => { c.limits.volume_mm3 = 1; }],
  ["loosened bounds tolerance", (c) => { c.limits.bounds_mm = .1; }],
  ["loosened Boolean tolerance", (c) => { c.limits.symmetric_difference_mm3 = 1; }],
  ["final source volume disconnected from stage", (c) => { c.source_volume_mm3 += .0001; c.absolute_volume_difference_mm3 = .0001; }],
  ["baseline volume disconnected from original evidence", (c) => { c.baseline_step_volume_mm3 += .0001; c.absolute_volume_difference_mm3 = .0001; }],
  ["incorrect absolute volume difference", (c) => { c.absolute_volume_difference_mm3 = .0001; }],
  ["incorrect maximum bounds difference", (c) => { c.maximum_bounds_difference_mm = .000001; }],
  ["source bounds disconnected from final stage", (c) => { c.source_bounds_mm.min[0] = -1e-7; c.source_bounds_mm.size[0] += 1e-7; c.maximum_bounds_difference_mm = 1e-7; }],
  ["baseline bounds disconnected from original evidence", (c) => { c.baseline_step_bounds_mm.min[0] = -1e-7; c.baseline_step_bounds_mm.size[0] += 1e-7; c.maximum_bounds_difference_mm = 1e-7; }],
  ["negative Boolean difference", (c) => { c.source_minus_baseline_mm3 = -.0001; c.symmetric_difference_mm3 = -.0001; }],
  ["Boolean difference above acceptance limit", (c) => { c.source_minus_baseline_mm3 = .002; c.symmetric_difference_mm3 = .002; }],
  ["combined Boolean difference above limit", (c) => { c.source_minus_baseline_mm3 = .0006; c.baseline_minus_source_mm3 = .0006; c.symmetric_difference_mm3 = .0012; }],
  ["incorrect symmetric difference sum", (c) => { c.source_minus_baseline_mm3 = .0001; }],
  ["nonfinite Boolean difference serialized as null", (c) => { c.baseline_minus_source_mm3 = Infinity; }],
];
for (const [name, mutate] of comparisonMutations) {
  test(`rejects ${name}`, (t) => {
    const f = fixture(t);
    mutate(f.m.final_comparisons.tray);
    f.saveConstruction();
    assert.throws(() => validateConstruction(f.root, f.baseline));
  });
}

test("rejects caller-supplied baseline data different from the on-disk bound manifest", (t) => {
  const f = fixture(t);
  const supplied = structuredClone(f.baseline);
  supplied.sources.baseline_a.run_id = "run-other";
  assert.throws(() => validateConstruction(f.root, supplied));
});

test("rejects a failed baseline even if its changed manifest is rebound consistently", (t) => {
  const f = fixture(t);
  f.baseline.sources.baseline_a.status = "fail";
  f.saveBaseline();
  assert.throws(() => validateConstruction(f.root, f.baseline));
});

test("rejects a different model even when both manifests agree on its hash", (t) => {
  const f = fixture(t);
  const wrong = sha("unqualified source");
  f.baseline.qualified_model_sha256 = wrong;
  f.baseline.sources.baseline_a.files_sha256["src/model.py"] = wrong;
  f.m.source_files_sha256["src/model.py"] = wrong;
  f.saveBaseline();
  assert.throws(() => validateConstruction(f.root, f.baseline));
});

test("rejects a construction operation timestamp moving backwards within its part", (t) => {
  const f = fixture(t);
  f.m.stages["tray-03-strap-guides"].elapsed_seconds_in_source_function = .5;
  f.saveConstruction();
  assert.throws(() => validateConstruction(f.root, f.baseline));
});
