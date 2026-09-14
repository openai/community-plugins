// SPDX-License-Identifier: Apache-2.0
// Static source/package boundary fixtures; native JAR execution is separate.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, cp, readFile, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyVacuumSensingPackage, verifyNativePatchPackages, verifyCircularSymmetryPackage } from '../../scripts/verify-openpnp-native-patches.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
async function fixture(t) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'vacuum-patch-fixture-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const plugin = path.join(directory, 'plugin'), patches = path.join(plugin, 'bridge/upstream-patches');
  await mkdir(patches, { recursive: true });
  for (const name of ['native-vacuum-sensing.json', 'native-vacuum-sensing.patch', 'native-circular-symmetry.json', 'native-circular-symmetry.patch', 'source'])
    await cp(path.join(root, 'plugins/openpnp/bridge/upstream-patches', name), path.join(patches, name), { recursive: true });
  await cp(path.join(root, 'plugins/openpnp/licenses'), path.join(plugin, 'licenses'), { recursive: true });
  const spec = JSON.parse(await readFile(path.join(patches, 'native-vacuum-sensing.json')));
  const circular = JSON.parse(await readFile(path.join(patches, 'native-circular-symmetry.json')));
  const build = { upstream_commit: spec.upstream_commit, bridge_sha256: '1'.repeat(64), runtime_manifest_sha256: '2'.repeat(64), patched_native_jar_sha256: '3'.repeat(64),
    patches: [spec, circular].map(s => ({ id: s.patch_id, path: s.patch_file, sha256: s.patch_sha256 })),
    native_vacuum_sensing: { api_version: 1, patch_id: spec.patch_id, patch_sha256: spec.patch_sha256, java_release: 11,
      source_sha256: spec.source_sha256, class_family_sha256: spec.reviewed_class_sha256, controlled_sources_serialized: false, physical_qualification: false },
    native_circular_symmetry: { api_version: 1, patch_id: circular.patch_id, patch_sha256: circular.patch_sha256,
      source_path: circular.source_path, source_sha256: circular.source_sha256, class_family_sha256: circular.reviewed_class_sha256, physical_calibration: false } };
  return { plugin, patches, spec, build, check: value => verifyVacuumSensingPackage({ pluginRoot: plugin, buildManifest: value }) };
}
test('four reviewed families bind all source postimages and Java11 package provenance', async t => {
  const f = await fixture(t), result = await f.check(f.build);
  assert.equal(result.verified, true); assert.equal(Object.keys(result.native_vacuum_sensing.class_family_sha256).length, 58);
  assert.equal(Object.keys(result.native_vacuum_sensing.source_sha256).length, 4);
  assert.equal(result.native_vacuum_sensing.java_release, 11);
  assert.equal(result.native_vacuum_sensing.controlled_sources_serialized, false);
  assert.match(result.scope, /loaded class validation are separate/);
  const aggregate = await verifyNativePatchPackages({ pluginRoot: f.plugin, buildManifest: f.build });
  assert.deepEqual(aggregate.native_vacuum_sensing, result.native_vacuum_sensing);
  assert.equal(Object.keys(aggregate.native_circular_symmetry.class_family_sha256).length, 6);
  assert.equal((await verifyCircularSymmetryPackage({ pluginRoot: f.plugin, buildManifest: f.build })).verified, true);
});
test('each absent or changed class family and contradictory build markers are refused', async t => {
  const f = await fixture(t);
  for (const [stem, classes] of Object.entries(f.spec.class_families)) {
    for (const mode of ['absent', 'changed']) {
      const build = structuredClone(f.build);
      for (const name of classes) mode === 'absent' ? delete build.native_vacuum_sensing.class_family_sha256[name] : build.native_vacuum_sensing.class_family_sha256[name] = '0'.repeat(64);
      await assert.rejects(f.check(build), `${mode}: ${stem}`);
    }
  }
  for (const mutate of [b => { delete b.native_vacuum_sensing; }, b => { b.native_vacuum_sensing.physical_qualification = true; },
    b => { b.native_vacuum_sensing.controlled_sources_serialized = true; }, b => { b.native_vacuum_sensing.java_release = 17; },
    b => { b.native_vacuum_sensing.patch_sha256 = '0'.repeat(64); }, b => { b.patches.push(structuredClone(b.patches[0])); },
    b => { b.patches = b.patches.slice(1); }, b => { b.runtime_manifest_sha256 = 'not-a-digest'; },
    b => { delete b.patched_native_jar_sha256; }, b => { delete b.bridge_sha256; },
    b => { b.native_vacuum_sensing.source_sha256[Object.keys(f.spec.source_sha256)[0]] = '0'.repeat(64); }]) {
    const build = structuredClone(f.build); mutate(build); await assert.rejects(f.check(build));
  }
});
test('every source, patch and GPL license must retain exact packaged bytes', async t => {
  const f = await fixture(t);
  for (const file of [path.join(f.patches, f.spec.patch_file), ...Object.keys(f.spec.source_sha256).map(name => path.join(f.patches, 'source', name)), path.join(f.plugin, 'licenses/OpenPnP-GPL-3.0.txt')]) {
    const bytes = await readFile(file); await writeFile(file, 'corrupted'); await assert.rejects(f.check(f.build)); await writeFile(file, bytes);
  }
});
test('source contract is closed over four fixed families, sources, pin and qualification limits', async t => {
  const f = await fixture(t);
  for (const mutate of [s => { delete s.class_families[Object.keys(s.class_families)[0]]; },
    s => { s.class_families[Object.keys(s.class_families)[0]].pop(); }, s => { s.source_sha256['../outside.java'] = '0'.repeat(64); },
    s => { delete s.reviewed_class_sha256[Object.keys(s.reviewed_class_sha256)[0]]; },
    s => { s.stock_source_sha256['org/openpnp/machine/reference/VacuumSensing.java'] = '0'.repeat(64); },
    s => { s.physical_qualification = true; }, s => { s.source_authority = true; }, s => { s.upstream_commit = '0'.repeat(40); }]) {
    const spec = structuredClone(f.spec); mutate(spec); await writeFile(path.join(f.patches, 'native-vacuum-sensing.json'), JSON.stringify(spec)); await assert.rejects(f.check(f.build));
  }
});
