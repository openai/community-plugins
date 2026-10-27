#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, lstat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
export async function verifyControllerHistoryPackage({ repositoryRoot = root, pluginRoot = path.join(repositoryRoot, 'plugins/openpnp') } = {}) {
  const helperRoot = path.join(pluginRoot, 'controller-history');
  const manifest = JSON.parse(await readFile(path.join(helperRoot, 'build-manifest.json'), 'utf8'));
  assert.equal(manifest.schema_version, 2); assert.equal(manifest.upstream_commit, '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c');
  assert.equal(manifest.main_class, 'org.openpnp.codex.ControllerHistoryMain');
  const files = [ ['openpnp-controller-history.jar', manifest.helper_sha256], ['source/org/openpnp/codex/ControllerHistoryMain.java', manifest.source_sha256], ['corresponding-source.zip', manifest.source_archive_sha256] ];
  const verified = [];
  for (const [relative, expected] of files) {
    assert.match(expected, /^[a-f0-9]{64}$/);
    const file = path.join(helperRoot, relative), info = await lstat(file);
    assert.ok(info.isFile() && !info.isSymbolicLink() && info.size < 32 * 1024 * 1024);
    const bytes = await readFile(file); assert.equal(sha(bytes), expected);
    verified.push({ path: 'controller-history/' + relative, sha256: expected, bytes: bytes.length });
  }
  assert.equal(sha(await readFile(path.join(repositoryRoot, 'src/openpnp/controller-history/org/openpnp/codex/ControllerHistoryMain.java'))), manifest.source_sha256);
  assert.equal(sha(await readFile(path.join(repositoryRoot, 'scripts/openpnp-build-controller-history.py'))), manifest.build_script_sha256);
  const reducer = 'src/openpnp/java/org/openpnp/codex/NativeControllerJournal.java';
  const journalJson = 'src/openpnp/java/org/openpnp/codex/NativeJournalJson.java';
  const bridge = JSON.parse(await readFile(path.join(pluginRoot, 'bridge/build-manifest.json'), 'utf8'));
  assert.equal(sha(await readFile(path.join(pluginRoot, 'bridge/openpnp-codex-bridge.jar'))), bridge.bridge_sha256);
  assert.equal(sha(await readFile(path.join(repositoryRoot, reducer))), manifest.reducer_source_sha256);
  assert.equal(bridge.production_source_sha256[reducer], manifest.reducer_source_sha256);
  assert.equal(sha(await readFile(path.join(repositoryRoot, journalJson))), manifest.journal_json_source_sha256);
  assert.equal(bridge.production_source_sha256[journalJson], manifest.journal_json_source_sha256);
  assert.equal(bridge.bridge_sha256, manifest.built_against_bridge_sha256);
  assert.equal(bridge.runtime_manifest_sha256, manifest.runtime_manifest_sha256);
  assert.match(manifest.journal_json_class_sha256, /^[a-f0-9]{64}$/);
  assert.match(manifest.reducer_class_sha256, /^[a-f0-9]{64}$/);
  assert.equal(sha(await readFile(path.join(helperRoot, 'COPYING'))), sha(await readFile(path.join(pluginRoot, 'licenses/OpenPnP-GPL-3.0.txt'))));
  return { verified: true, artifacts: verified, reducer_class_sha256: manifest.reducer_class_sha256, journal_json_class_sha256: manifest.journal_json_class_sha256,
    bridge_sha256: bridge.bridge_sha256, runtime_class_compatibility: 'Enforced by Java main before journal read; execute CLI qualification separately',
    scope: 'Separate helper artifact/source integrity; reproducible Java rebuild and native qualification are separate' };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.stdout.write(JSON.stringify(await verifyControllerHistoryPackage(), null, 2) + '\n'); }
  catch { process.stderr.write('Controller history helper source/artifact verification failed.\n'); process.exitCode = 1; }
}
