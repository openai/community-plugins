#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Static package/source binding. Actual loaded class/JAR admission is separate.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, lstat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const pin = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const stem = 'org/openpnp/vision/pipeline/stages/DetectCircularSymmetry';
const family = ['', '$1', '$2', '$ScoreRange', '$SymmetryCircle', '$SymmetryScore'].map(s => stem + s + '.class').sort();
const vacuumFamilies = {
  "org/openpnp/machine/reference/ReferenceNozzle": [
    "org/openpnp/machine/reference/ReferenceNozzle$1.class",
    "org/openpnp/machine/reference/ReferenceNozzle$ManualLoadException.class",
    "org/openpnp/machine/reference/ReferenceNozzle$ManualUnloadException.class",
    "org/openpnp/machine/reference/ReferenceNozzle.class"
  ],
  "org/openpnp/machine/reference/ReferencePnpJobProcessor": [
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$1.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Abort.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$AbstractOptimizationNozzlesStep.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Align.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$AlignLocator.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$CalibrateNozzleTips.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$ChangeNozzleTips.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Cleanup.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$EndCameraBatchOperation.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$FiducialCheck$ExtendedPlacementsHolderLocation.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$FiducialCheck.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Finish.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$FinishCycle.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$JobOrderHint.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Locator.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$OptimizeNozzlesForAlign.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$OptimizeNozzlesForPick.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$OptimizeNozzlesForPlace.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Pick.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$PickLocator.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Place.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$PlaceLocator.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$1.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$1JobPlacementNozzleTip.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$2.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$ReturnJobPlacementsAndNozzleTips.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$ReturnListAndLocation.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$PlannedPlacementStep.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$PreFlight$1.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$PreFlight.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$PrerotateAllNozzlesForAlign.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$PrerotateAllNozzlesForPick.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$PrerotateAllNozzlesForPlace.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$PrerotateAllNozzlesStep.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$SimplePnpJobPlanner$PlannerState.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$SimplePnpJobPlanner.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$StartCameraBatchOperation.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$Step.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor$TrivialPnpJobPlanner.class",
    "org/openpnp/machine/reference/ReferencePnpJobProcessor.class"
  ],
  "org/openpnp/machine/reference/VacuumSensing": [
    "org/openpnp/machine/reference/VacuumSensing$CheckedBoolean.class",
    "org/openpnp/machine/reference/VacuumSensing$CheckedString.class",
    "org/openpnp/machine/reference/VacuumSensing$CheckedVoid.class",
    "org/openpnp/machine/reference/VacuumSensing$ControlledSource.class",
    "org/openpnp/machine/reference/VacuumSensing$Frame.class",
    "org/openpnp/machine/reference/VacuumSensing$Observer.class",
    "org/openpnp/machine/reference/VacuumSensing$ObserverFailure.class",
    "org/openpnp/machine/reference/VacuumSensing$SampleSource.class",
    "org/openpnp/machine/reference/VacuumSensing$Scope.class",
    "org/openpnp/machine/reference/VacuumSensing$SensorValueException.class",
    "org/openpnp/machine/reference/VacuumSensing.class"
  ],
  "org/openpnp/machine/reference/driver/NullDriver": [
    "org/openpnp/machine/reference/driver/NullDriver$1.class",
    "org/openpnp/machine/reference/driver/NullDriver.class"
  ]
};
const vacuumClasses = Object.values(vacuumFamilies).flat().sort();
const vacuumSources = Object.keys(vacuumFamilies).map(stem => stem + '.java').sort();
const sha = value => createHash('sha256').update(value).digest('hex');
async function bytes(file, maximum = 1024 * 1024) {
  const stat = await lstat(file);
  assert.ok(stat.isFile() && !stat.isSymbolicLink() && stat.size <= maximum, 'Native patch input must be a bounded regular file.');
  return readFile(file);
}
export async function verifyCircularSymmetryPackage({ pluginRoot = path.join(root, 'plugins/openpnp'), buildManifest } = {}) {
  const patches = path.join(pluginRoot, 'bridge/upstream-patches');
  const spec = JSON.parse(await bytes(path.join(patches, 'native-circular-symmetry.json')));
  assert.equal(spec.schema_version, 1); assert.equal(spec.upstream_commit, pin);
  assert.equal(spec.patch_id, 'native-circular-symmetry-v1');
  assert.equal(spec.patch_file, 'native-circular-symmetry.patch');
  assert.equal(spec.source_path, stem + '.java'); assert.equal(spec.license, 'GPL-3.0-or-later');
  assert.equal(spec.java_release, 11); assert.deepEqual(spec.class_family, family);
  assert.deepEqual(Object.keys(spec.reviewed_class_sha256).sort(), family);
  for (const hash of [spec.patch_sha256, spec.stock_source_sha256, spec.source_sha256, ...Object.values(spec.reviewed_class_sha256)])
    assert.match(hash, /^[a-f0-9]{64}$/);
  for (const key of ['supported_api_changed', 'thresholds_changed', 'physical_calibration']) assert.equal(spec[key], false);
  const patch = await bytes(path.join(patches, spec.patch_file));
  const source = await bytes(path.join(patches, 'source', spec.source_path));
  assert.equal(sha(patch), spec.patch_sha256, 'Reviewed detector patch bytes changed.');
  assert.equal(sha(source), spec.source_sha256, 'Corresponding GPL detector source changed.');
  assert.match(source.toString(), /General Public License/);
  assert.match((await bytes(path.join(pluginRoot, 'licenses/OpenPnP-GPL-3.0.txt'))).toString(), /GNU GENERAL PUBLIC LICENSE/);
  const build = buildManifest ?? JSON.parse(await bytes(path.join(pluginRoot, 'bridge/build-manifest.json')));
  assert.equal(build.upstream_commit, pin);
  const provenance = build.native_circular_symmetry;
  assert.ok(provenance, 'Native circular detector patch provenance is required.');
  assert.deepEqual(provenance, { api_version: 1, patch_id: spec.patch_id, patch_sha256: spec.patch_sha256,
    source_path: spec.source_path, source_sha256: spec.source_sha256,
    class_family_sha256: spec.reviewed_class_sha256, physical_calibration: false });
  assert.deepEqual(build.patches.filter(p => p.id === spec.patch_id),
    [{ id: spec.patch_id, path: spec.patch_file, sha256: spec.patch_sha256 }], 'Detector patch list and capability must match.');
  return { verified: true, native_circular_symmetry: provenance,
    scope: 'Static packaged patch/GPL source/build-manifest binding; native JAR and loaded class validation are separate.' };
}
export async function verifyVacuumSensingPackage({ pluginRoot = path.join(root, 'plugins/openpnp'), buildManifest } = {}) {
  const patches = path.join(pluginRoot, 'bridge/upstream-patches');
  const spec = JSON.parse(await bytes(path.join(patches, 'native-vacuum-sensing.json')));
  const fixed = { schema_version: 1, upstream_commit: pin, patch_id: 'native-vacuum-sensing-v1',
    patch_file: 'native-vacuum-sensing.patch', license: 'GPL-3.0-or-later', java_release: 11, api_version: 1,
    class_families: vacuumFamilies, controlled_sources_serialized: false, physical_qualification: false };
  assert.deepEqual(Object.keys(spec).sort(), [...Object.keys(fixed), 'patch_sha256', 'source_sha256',
    'stock_source_sha256', 'reviewed_class_sha256', 'reviewed_native_build'].sort(), 'Unexpected vacuum source contract fields.');
  for (const [key, value] of Object.entries(fixed)) assert.deepEqual(spec[key], value, `Vacuum source contract mismatch: ${key}`);
  for (const [key, keys] of [['source_sha256', vacuumSources], ['stock_source_sha256', vacuumSources],
    ['reviewed_class_sha256', vacuumClasses], ['reviewed_native_build', ['build_manifest_sha256', 'runtime_manifest_sha256', 'native_jar_sha256']]]) {
    assert.ok(spec[key] && typeof spec[key] === 'object' && !Array.isArray(spec[key]), `Vacuum ${key} must be an identity map.`);
    assert.deepEqual(Object.keys(spec[key]).sort(), [...keys].sort(), `Vacuum ${key} inventory mismatch.`);
    for (const [name, hash] of Object.entries(spec[key])) {
      if (key === 'stock_source_sha256' && name === 'org/openpnp/machine/reference/VacuumSensing.java') assert.equal(hash, null);
      else assert.match(hash, /^[a-f0-9]{64}$/);
    }
  }
  assert.match(spec.patch_sha256, /^[a-f0-9]{64}$/);
  assert.equal(sha(await bytes(path.join(patches, spec.patch_file))), spec.patch_sha256, 'Reviewed vacuum patch bytes changed.');
  for (const [name, hash] of Object.entries(spec.source_sha256))
    assert.equal(sha(await bytes(path.join(patches, 'source', name))), hash, `Corresponding GPL vacuum source changed: ${name}`);
  assert.match((await bytes(path.join(pluginRoot, 'licenses/OpenPnP-GPL-3.0.txt'))).toString(), /GNU GENERAL PUBLIC LICENSE/);
  const build = buildManifest ?? JSON.parse(await bytes(path.join(pluginRoot, 'bridge/build-manifest.json')));
  assert.equal(build.upstream_commit, pin);
  const expected = { api_version: 1, patch_id: spec.patch_id, patch_sha256: spec.patch_sha256, java_release: 11,
    source_sha256: spec.source_sha256, class_family_sha256: spec.reviewed_class_sha256,
    controlled_sources_serialized: false, physical_qualification: false };
  assert.deepEqual(build.native_vacuum_sensing, expected, 'Vacuum build marker must bind all reviewed source and class families.');
  assert.ok(Array.isArray(build.patches), 'Vacuum build patch inventory is required.');
  assert.deepEqual(build.patches.filter(p => p.id === spec.patch_id),
    [{ id: spec.patch_id, path: spec.patch_file, sha256: spec.patch_sha256 }], 'Vacuum patch list and capability must match.');
  for (const key of ['bridge_sha256', 'runtime_manifest_sha256', 'patched_native_jar_sha256']) assert.match(build[key], /^[a-f0-9]{64}$/, `Vacuum build must bind ${key}.`);
  return { verified: true, native_vacuum_sensing: expected,
    build_artifacts: { bridge_sha256: build.bridge_sha256, runtime_manifest_sha256: build.runtime_manifest_sha256,
      patched_native_jar_sha256: build.patched_native_jar_sha256 },
    scope: 'Static packaged patch/GPL source/build-manifest binding; native JAR and loaded class validation are separate.' };
}

/** Preserve the individual circular verifier for existing callers; packages require both. */
export async function verifyNativePatchPackages(options = {}) {
  const circular = await verifyCircularSymmetryPackage(options);
  const vacuum = await verifyVacuumSensingPackage(options);
  return { verified: true, native_circular_symmetry: circular.native_circular_symmetry,
    native_vacuum_sensing: vacuum.native_vacuum_sensing, build_artifacts: vacuum.build_artifacts, scope: vacuum.scope };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.stdout.write(JSON.stringify(await verifyNativePatchPackages(), null, 2) + '\n'); }
  catch (error) { process.stderr.write(`Native patch package verification failed: ${error.message}\n`); process.exitCode = 1; }
}
