// SPDX-License-Identifier: Apache-2.0
// Static package fixtures; no Java/native process is launched.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, cp, readFile, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyCircularSymmetryPackage } from '../../scripts/verify-openpnp-native-patches.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
async function fixture(t) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'openpnp-patch-fixture-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const plugin = path.join(directory, 'plugin');
  const patches = path.join(plugin, 'bridge/upstream-patches');
  await mkdir(patches, { recursive: true });
  for (const name of ['native-circular-symmetry.json', 'native-circular-symmetry.patch', 'source'])
    await cp(path.join(root, 'plugins/openpnp/bridge/upstream-patches', name), path.join(patches, name), { recursive: true });
  await cp(path.join(root, 'plugins/openpnp/licenses'), path.join(plugin, 'licenses'), { recursive: true });
  const spec = JSON.parse(await readFile(path.join(patches, 'native-circular-symmetry.json')));
  const build = { upstream_commit: spec.upstream_commit,
    patches: [{ id: spec.patch_id, path: spec.patch_file, sha256: spec.patch_sha256 }],
    native_circular_symmetry: { api_version: 1, patch_id: spec.patch_id, patch_sha256: spec.patch_sha256,
      source_path: spec.source_path, source_sha256: spec.source_sha256,
      class_family_sha256: spec.reviewed_class_sha256, physical_calibration: false } };
  return { plugin, patches, spec, build, check: buildManifest => verifyCircularSymmetryPackage({ pluginRoot: plugin, buildManifest }) };
}
test('six reviewed class identities bind exact patch and corresponding GPL source', async t => {
  const f = await fixture(t); const result = await f.check(f.build);
  assert.equal(result.verified, true);
  assert.equal(Object.keys(result.native_circular_symmetry.class_family_sha256).length, 6);
  assert.equal(result.native_circular_symmetry.physical_calibration, false);
  assert.match(result.scope, /loaded class validation are separate/);
});
test('missing or contradictory provenance cannot verify the package', async t => {
  const f = await fixture(t);
  for (const [name, mutate] of [
    ['missing', b => { delete b.native_circular_symmetry; }],
    ['version', b => { b.native_circular_symmetry.api_version = 2; }],
    ['patch', b => { b.native_circular_symmetry.patch_sha256 = '0'.repeat(64); }],
    ['source', b => { b.native_circular_symmetry.source_sha256 = '0'.repeat(64); }],
    ['missing-class', b => { delete b.native_circular_symmetry.class_family_sha256[f.spec.class_family[0]]; }],
    ['wrong-class', b => { b.native_circular_symmetry.class_family_sha256[f.spec.class_family[0]] = '0'.repeat(64); }],
    ['extra-class', b => { b.native_circular_symmetry.class_family_sha256['invented.class'] = '0'.repeat(64); }],
    ['missing-patch', b => { b.patches = []; }],
    ['duplicate-patch', b => { b.patches.push(structuredClone(b.patches[0])); }],
    ['physical-claim', b => { b.native_circular_symmetry.physical_calibration = true; }],
  ]) await t.test(name, async () => { const value = structuredClone(f.build); mutate(value); await assert.rejects(f.check(value)); });
});
test('patch/source/license corruption and invalid source contract are refused', async t => {
  const f = await fixture(t);
  for (const file of [path.join(f.patches, f.spec.patch_file), path.join(f.patches, 'source', f.spec.source_path),
    path.join(f.plugin, 'licenses/OpenPnP-GPL-3.0.txt')]) {
    const original = await readFile(file); await writeFile(file, 'corrupted');
    await assert.rejects(f.check(f.build)); await writeFile(file, original);
  }
  for (const edit of [s => { s.source_path = '../outside.java'; }, s => { s.class_family.pop(); },
    s => { delete s.reviewed_class_sha256[s.class_family[0]]; }]) {
    const value = structuredClone(f.spec); edit(value);
    await writeFile(path.join(f.patches, 'native-circular-symmetry.json'), JSON.stringify(value));
    await assert.rejects(f.check(f.build));
  }
});
