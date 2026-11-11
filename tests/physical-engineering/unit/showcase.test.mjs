import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { validateEvidence } from "../support/showcase/evidence.mjs";
import { buildOfflineHtml } from "../support/showcase/bundle_html.mjs";

const template =
  '<html><head><script type="importmap">{}</script></head><body><audio src="/narration.wav"></audio><script type="module" src="/showcase.mjs"></script></body></html>';
function executeEmbedded(html) {
  const context = { window: {} };
  for (const match of html.matchAll(
    /<script(?: type="module")?>([\s\S]*?)<\/script>/g,
  ))
    vm.runInNewContext(match[1], context);
  return context.window;
}
test("offline bundle preserves dollar replacement tokens in compiled code", () => {
  const sentinel = "$& $` $' $$";
  const html = buildOfflineHtml(
    template,
    {},
    `window.sentinel=${JSON.stringify(sentinel)};`,
  );
  assert.equal(executeEmbedded(html).sentinel, sentinel);
  assert.equal((html.match(/<script/g) || []).length, 2);
  assert.doesNotMatch(html, /src="\//);
});
test("offline bundle escapes mixed-case closing script tags in data and code", () => {
  const sentinel = "</SCRIPT><script>window.injected=true</script>";
  const html = buildOfflineHtml(
    template,
    { sentinel },
    `window.sentinel=${JSON.stringify(sentinel)};`,
  );
  const result = executeEmbedded(html);
  assert.equal(result.sentinel, sentinel);
  assert.equal(result.__SHOWCASE__.sentinel, sentinel);
  assert.equal(result.injected, undefined);
  assert.equal((html.match(/<\/script>/gi) || []).length, 2);
});
test("offline bundle rejects a missing module entry", () => {
  assert.throws(() => buildOfflineHtml("<html></html>", {}, ""), /exactly one/);
});
test("offline bundle rejects duplicate module entries", () => {
  assert.throws(
    () => buildOfflineHtml(template + template, {}, ""),
    /exactly one/,
  );
});

const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
const identity = () => ({
  translation_mm: [0, 0, 0],
  rotation_xyz_radians: [0, 0, 0],
  scale: [1, 1, 1],
});
const files = [
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

// Deliberately synthetic bytes: this suite checks the renderer evidence contract,
// not STL parsing or CAD acceptance, and needs neither Three.js nor Python/CAD.
function fixture(t) {
  const workspace = fs.mkdtempSync(
    path.join(os.tmpdir(), "showcase-evidence-"),
  );
  const root = path.join(workspace, "assets");
  fs.mkdirSync(root);
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const m = {
    schema: 1,
    qualified_plugin_commit: "a".repeat(40),
    qualified_model_sha256: sha("qualified model"),
    coordinate_frame: {
      units: "mm",
      up: "+Z",
      handedness: "right",
      origin: "frame-plane center",
      all_mesh_vertices: "assembly world coordinates",
      default_object_translation_mm: [0, 0, 0],
      default_object_rotation_xyz_radians: [0, 0, 0],
      default_object_scale: [1, 1, 1],
    },
    meshes: {},
    sources: {},
    measurements: {},
    scenes: {},
    verification: {
      all_frozen_source_hashes_match_before_and_after: true,
      failed_run_uses_baseline_stl_bytes: true,
      actual_reproduced_meshes_included: true,
    },
  };
  const states = [
    ["baseline_a", "pass", "A", 35, 36.5, 0.75, "a"],
    ["unchanged_a_with_b", "fail", "B", 40, 36.5, -1.75, "a"],
    ["corrected_b", "pass", "B", 40, 41.5, 0.75, "b"],
    ["reproduced_b", "pass", "B", 40, 41.5, 0.75, "reproduced"],
  ];
  for (const [
    name,
    status,
    battery,
    width,
    cavity,
    clearance,
    prefix,
  ] of states) {
    m.sources[name] = {
      status,
      run_id: `run-${name}`,
      files_sha256: Object.fromEntries(
        files.map((file) => [file, sha(name + file)]),
      ),
    };
    m.sources[name].files_sha256["src/model.py"] = m.qualified_model_sha256;
    m.measurements[name] = {
      battery,
      battery_size_mm: [100, width, 25],
      battery_bounds_y_mm: [-width / 2, width / 2],
      cavity_width_mm: cavity,
      cavity_inside_y_mm: [-cavity / 2, cavity / 2],
      clearance_each_side_mm: [clearance, clearance],
      frame_pitch_mm: [100, 60],
      board_pitch_mm: [30, 30],
      polymer_mass_g: 43.967095864691245,
      overlap_mm3: {
        assembly: 0,
        board: 0,
        board_keepout: 0,
        battery: status === "fail" ? 2764.9976865 : 0,
        insertion: status === "fail" ? 2847.6040191 : 0,
      },
      failed_checks:
        status === "fail"
          ? [
              "battery_lateral_fit",
              "battery_interference",
              "insertion_interference",
            ]
          : [],
    };
    m.scenes[name] = {
      status,
      source_run: `run-${name}`,
      meshes: [
        `${prefix}-tray`,
        `${prefix}-bridge`,
        `battery-${battery.toLowerCase()}`,
        "board",
      ],
    };
  }
  function mesh(name, source = {}) {
    const bytes = Buffer.from(`Synthetic mesh bytes: ${name}\n`);
    fs.writeFileSync(path.join(root, `${name}.stl`), bytes);
    m.meshes[name] = {
      path: `${name}.stl`,
      bytes: bytes.length,
      sha256: sha(bytes),
      source,
      ...identity(),
      cad_volume_mm3: 55.3,
      cad_bounds_mm: { min: [-1, -20, 3], max: [1, 20, 11] },
    };
  }
  for (const [state, prefix] of [
    ["baseline_a", "a"],
    ["corrected_b", "b"],
    ["reproduced_b", "reproduced"],
  ]) {
    for (const part of ["tray", "bridge"]) {
      mesh(`${prefix}-${part}`, { run: state, file: `exports/${part}.stl` });
      m.meshes[`${prefix}-${part}`].copied_original_bytes = true;
      m.sources[state].files_sha256[`exports/${part}.stl`] =
        m.meshes[`${prefix}-${part}`].sha256;
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
    m.sources.unchanged_a_with_b.files_sha256[file] =
      m.sources.baseline_a.files_sha256[file];
  }
  m.sources.reproduced_b.files_sha256["requirements.json"] =
    m.sources.corrected_b.files_sha256["requirements.json"];
  for (const name of ["battery-a", "battery-b", "board"]) mesh(name);
  mesh("failed-b-intersection", { run: "unchanged_a_with_b" });
  m.meshes["failed-b-intersection"].cad_volume_mm3 = 2764.9976865;
  for (const name of [
    "a-tray",
    "b-tray",
    "battery-a",
    "battery-b",
    "failed-b-intersection",
  ])
    mesh(`${name}-section`, { mesh: name });
  m.reproduction = {
    status: "pass",
    source_run: "run-corrected_b",
    reproduced_run: "run-reproduced_b",
    measurement_tolerance: 0.01,
    differences: [],
    independent_cad_state: true,
    source_files_sha256: {
      "reproduction.json": sha("recorded reproduction comparison"),
      "corrected_checks.json":
        m.sources.corrected_b.files_sha256["checks.json"],
      "reproduced_checks.json":
        m.sources.reproduced_b.files_sha256["checks.json"],
    },
  };
  const save = () =>
    fs.writeFileSync(path.join(root, "manifest.json"), JSON.stringify(m));
  save();
  return { root, workspace, m, save };
}

test("accepts consistent evidence and returns exact file/manifest hashes and story values", (t) => {
  const { root, m } = fixture(t);
  const result = validateEvidence(root);
  assert.equal(
    result.manifestSha256,
    sha(fs.readFileSync(path.join(root, "manifest.json"))),
  );
  assert.equal(result.meshCount, 15);
  assert.equal(
    result.assetHashes["reproduced-tray.stl"],
    m.meshes["reproduced-tray"].sha256,
  );
  assert.equal(result.story.correctedMassG, 43.967095864691245);
  assert.equal(result.story.reproductionTolerance, 0.01);
});

test("rejects changed bytes even when the file size remains identical", (t) => {
  const { root } = fixture(t);
  const file = path.join(root, "a-tray.stl");
  const bytes = fs.readFileSync(file);
  bytes[0] ^= 1;
  fs.writeFileSync(file, bytes);
  assert.throws(() => validateEvidence(root), /SHA mismatch/);
});

test("rejects missing files before rendering", (t) => {
  const { root } = fixture(t);
  fs.unlinkSync(path.join(root, "reproduced-tray.stl"));
  assert.throws(() => validateEvidence(root), /missing asset/);
});

for (const unsafe of [
  "../outside.stl",
  "/tmp/outside.stl",
  "C:/private/a.stl",
  "nested\\a.stl",
  "./a.stl",
  "nested/../a.stl",
  "nested//a.stl",
]) {
  test(`rejects unsafe asset path ${JSON.stringify(unsafe)}`, (t) => {
    const { root, m, save } = fixture(t);
    m.meshes["a-tray"].path = unsafe;
    save();
    assert.throws(() => validateEvidence(root), /unsafe asset path/);
  });
}

test("rejects file symlinks without changing the outside sentinel", (t) => {
  const { root, workspace } = fixture(t);
  const outside = path.join(workspace, "outside.stl");
  fs.writeFileSync(outside, "sentinel");
  fs.unlinkSync(path.join(root, "a-tray.stl"));
  fs.symlinkSync(outside, path.join(root, "a-tray.stl"));
  assert.throws(() => validateEvidence(root), /symlinked asset path/);
  assert.equal(fs.readFileSync(outside, "utf8"), "sentinel");
});

test("rejects symlinked asset parents, root, and manifest", (t) => {
  const { root, workspace, m, save } = fixture(t);
  const link = path.join(workspace, "linked-assets");
  fs.symlinkSync(root, link, "dir");
  assert.throws(() => validateEvidence(link), /real directory/);
  fs.symlinkSync(workspace, path.join(root, "parent-link"), "dir");
  m.meshes["a-tray"].path = "parent-link/assets/a-tray.stl";
  save();
  assert.throws(() => validateEvidence(root), /symlinked asset path/);
  const outsideManifest = path.join(workspace, "manifest.json");
  fs.renameSync(path.join(root, "manifest.json"), outsideManifest);
  fs.symlinkSync(outsideManifest, path.join(root, "manifest.json"));
  assert.throws(() => validateEvidence(root), /symlinked asset path/);
});

const rejectionCases = [
  [
    "wrong units",
    (m) => {
      m.coordinate_frame.units = "m";
    },
    /mesh frame/,
  ],
  [
    "wrong up axis",
    (m) => {
      m.coordinate_frame.up = "+Y";
    },
    /mesh frame/,
  ],
  [
    "recentered mesh",
    (m) => {
      m.meshes["a-tray"].translation_mm[2] = -5.5;
    },
    /translation/,
  ],
  [
    "rotated mesh",
    (m) => {
      m.meshes["b-tray"].rotation_xyz_radians[0] = Math.PI / 2;
    },
    /rotation/,
  ],
  [
    "scaled mesh",
    (m) => {
      m.meshes["a-bridge"].scale = [0.001, 0.001, 0.001];
    },
    /scale/,
  ],
  [
    "failure relabeled pass",
    (m) => {
      m.sources.unchanged_a_with_b.status = "pass";
    },
    /source status/,
  ],
  [
    "missing reproduced source",
    (m) => {
      delete m.sources.reproduced_b;
    },
    /source status/,
  ],
  [
    "corrected geometry relabeled reproduced",
    (m) => {
      m.meshes["reproduced-tray"].source.run = "corrected_b";
    },
    /actual recorded run output/,
  ],
  [
    "reproduced source SHA changed",
    (m) => {
      m.sources.reproduced_b.files_sha256["exports/tray.stl"] =
        sha("unrelated");
    },
    /source SHA mismatch/,
  ],
  [
    "reproduced scene duplicates corrected files",
    (m) => {
      m.scenes.reproduced_b.meshes[0] = "b-tray";
    },
    /displayed meshes/,
  ],
  [
    "reproduction failed",
    (m) => {
      m.reproduction.status = "fail";
    },
    /reproduction must pass/,
  ],
  [
    "reproduction differences hidden",
    (m) => {
      m.reproduction.differences = ["cavity"];
    },
    /reproduction differences/,
  ],
  [
    "reproduction tolerance widened",
    (m) => {
      m.reproduction.measurement_tolerance = 0.1;
    },
    /reproduction tolerance/,
  ],
  [
    "missing reproduction comparison hash",
    (m) => {
      delete m.reproduction.source_files_sha256["reproduction.json"];
    },
    /comparison SHA/,
  ],
  [
    "reproduction check receipt mismatch",
    (m) => {
      m.reproduction.source_files_sha256["reproduced_checks.json"] =
        sha("other");
    },
    /checks SHA mismatch/,
  ],
  [
    "same run presented as independent",
    (m) => {
      m.sources.reproduced_b.run_id = m.sources.corrected_b.run_id;
    },
    /runs must be distinct/,
  ],
  [
    "reproduction uses shared CAD state",
    (m) => {
      m.reproduction.independent_cad_state = false;
    },
    /reproduction must pass/,
  ],
  [
    "source commit missing",
    (m) => {
      delete m.qualified_plugin_commit;
    },
    /source commit/,
  ],
  [
    "original geometry changed in failed case",
    (m) => {
      m.sources.unchanged_a_with_b.files_sha256["exports/tray.step"] =
        sha("changed");
    },
    /original design/,
  ],
  [
    "wrong A battery width",
    (m) => {
      m.measurements.baseline_a.battery_size_mm[1] = 36;
    },
    /battery size/,
  ],
  [
    "wrong B battery width",
    (m) => {
      m.measurements.corrected_b.battery_size_mm[1] = 39;
    },
    /battery size/,
  ],
  [
    "wrong old cavity",
    (m) => {
      m.measurements.unchanged_a_with_b.cavity_width_mm = 36.6;
    },
    /cavity width/,
  ],
  [
    "wrong corrected cavity",
    (m) => {
      m.measurements.corrected_b.cavity_width_mm = 41.4;
    },
    /cavity width/,
  ],
  [
    "collision sign flipped",
    (m) => {
      m.measurements.unchanged_a_with_b.clearance_each_side_mm = [1.75, 1.75];
    },
    /clearance/,
  ],
  [
    "frame mounting changed",
    (m) => {
      m.measurements.corrected_b.frame_pitch_mm = [110, 60];
    },
    /frame pitch/,
  ],
  [
    "board mounting changed",
    (m) => {
      m.measurements.corrected_b.board_pitch_mm = [35, 35];
    },
    /board pitch/,
  ],
  [
    "mass claim stale",
    (m) => {
      m.measurements.corrected_b.polymer_mass_g = 44;
    },
    /polymer mass/,
  ],
  [
    "numeric string substituted",
    (m) => {
      m.measurements.corrected_b.cavity_width_mm = "41.5";
    },
    /cavity width/,
  ],
  [
    "passing fit has interference",
    (m) => {
      m.measurements.corrected_b.overlap_mm3.battery = 0.1;
    },
    /overlap/,
  ],
  [
    "failed fit has no measured collision",
    (m) => {
      m.measurements.unchanged_a_with_b.overlap_mm3.battery = 0;
    },
    /positive battery interference/,
  ],
  [
    "collision mesh volume disagrees",
    (m) => {
      m.meshes["failed-b-intersection"].cad_volume_mm3 = 100;
    },
    /collision volume/,
  ],
  [
    "section from unrelated geometry",
    (m) => {
      m.meshes["failed-b-intersection-section"].source.mesh = "b-tray";
    },
    /section source/,
  ],
  [
    "section offset from advertised plane",
    (m) => {
      m.meshes["a-tray-section"].cad_bounds_mm.min[0] = 0;
    },
    /minimum X/,
  ],
];
for (const [name, mutate, pattern] of rejectionCases) {
  test(`rejects ${name}`, (t) => {
    const { root, m, save } = fixture(t);
    mutate(m);
    save();
    assert.throws(() => validateEvidence(root), pattern);
  });
}
