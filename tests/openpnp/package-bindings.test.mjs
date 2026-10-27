// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { verifyPackageArtifactBindings } from '../../scripts/verify-openpnp-package.mjs';

const bridgeHash = '1'.repeat(64), runtimeHash = '2'.repeat(64), otherHash = '3'.repeat(64);
const names = ['bridge/build-manifest.json', 'controller-history/build-manifest.json', 'assets/evaluations/job-edit-tool-contracts.json'];
async function fixture(t, alter = () => {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'openpnp-bindings-'));
  const values = [
    { bridge_sha256: bridgeHash, runtime_manifest_sha256: runtimeHash },
    { built_against_bridge_sha256: bridgeHash, runtime_manifest_sha256: runtimeHash },
    { candidate_packaged_mcp: { native_bridge_sha256: bridgeHash, native_runtime_manifest_sha256: runtimeHash },
      historical_candidate: { native_bridge_sha256: otherHash, native_runtime_manifest_sha256: otherHash } },
  ];
  alter(values);
  for (const [index, name] of names.entries()) {
    const file = path.join(directory, name);
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, JSON.stringify(values[index]));
  }
  const bytes = await Promise.all(names.map(name => readFile(path.join(directory, name), 'utf8')));
  t.after(async () => {
    try {
      assert.deepEqual(await Promise.all(names.map(name => readFile(path.join(directory, name), 'utf8'))), bytes,
        'Verification must preserve current and historical metadata.');
    } finally { await rm(directory, { recursive: true, force: true }); }
  });
  return directory;
}

test('current Bridge, helper and evidence metadata bind the same runtime while historical identities remain distinct', async t => {
  const result = await verifyPackageArtifactBindings(await fixture(t));
  assert.equal(result.bridge_sha256, bridgeHash);
  assert.equal(result.runtime_manifest_sha256, runtimeHash);
  assert.match(result.scope, /metadata binding only/);
});

test('package verification rejects stale runtime or Bridge provenance before rebuilding', async t => {
  const cases = [
    ['current runtime', values => { values[2].candidate_packaged_mcp.native_runtime_manifest_sha256 = otherHash; }, /Current metadata.*runtime/],
    ['helper runtime', values => { values[1].runtime_manifest_sha256 = otherHash; }, /History helper.*runtime/],
    ['current Bridge', values => { values[2].candidate_packaged_mcp.native_bridge_sha256 = otherHash; }, /Current metadata.*Bridge/],
    ['helper Bridge', values => { values[1].built_against_bridge_sha256 = otherHash; }, /History helper.*Bridge/],
    ['missing current metadata', values => { delete values[2].candidate_packaged_mcp; }, /Current package metadata is missing/],
    ['invalid identity', values => { values[0].runtime_manifest_sha256 = 'unknown'; }, /SHA-256/],
  ];
  for (const [name, alter, expected] of cases) await t.test(name, async t => {
    await assert.rejects(verifyPackageArtifactBindings(await fixture(t, alter)), expected);
  });
});
