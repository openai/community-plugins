import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm, access } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { adoptConfiguration, launchAdoptedSimulator, parseArguments, runAdoptionProcess, readBoundedRegular, main } from '../../plugins/openpnp/scripts/openpnp.mjs';
import { javaEnvironment } from '../../plugins/openpnp/scripts/java-environment.mjs';

const complete = { adopted: true, native_xml_roundtrip_verified: true, scripts_activated: false, physical_state_transferred: false };
const print = value => `console.log('OPENPNP_CODEX_ADOPTED '+JSON.stringify(${JSON.stringify(value)}));`;
async function temporary(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-portable-cli-'));
  t.after(() => rm(root, { recursive: true, force: true })); return root;
}

test('adoption CLI parses only its explicit bounded workflow options', async () => {
  assert.equal(parseArguments(['adopt-configuration', '--bundle', '/bundle.zip', '--sha256', 'a'.repeat(64), '--destination', '/fresh']).command, 'adopt-configuration');
  assert.throws(() => parseArguments(['adopt-configuration', '--activate-scripts', 'true']), /Unknown/);
  assert.throws(() => parseArguments(['start-adopted-simulator', '--profile', 'physical']), /Unknown/);
  await assert.rejects(main(['start-adopted-simulator', '--adoption-dir', '/unused']), /explicit absolute --state-dir/);
});

test('archive integrity refusal happens before native invocation or destination creation', async t => {
  const root = await temporary(t), bundle = path.join(root, 'bundle.zip'), destination = path.join(root, 'destination');
  await writeFile(bundle, 'archive bytes');
  await assert.rejects(adoptConfiguration({ bundle, expectedSha256: 'a'.repeat(64), destination, java: '/not-invoked' }), /SHA-256 does not match/);
  await assert.rejects(access(destination), { code: 'ENOENT' });
  await assert.rejects(adoptConfiguration({ bundle, expectedSha256: 'ABC', destination }), /expected lowercase/);
  const hash = createHash('sha256').update('archive bytes').digest('hex');
  await assert.rejects(adoptConfiguration({ bundle, expectedSha256: hash, destination, openpnpHome: 'relative' }), /absolute path/);
});

test('adopted launch refuses existing state and preserves its operational history', async t => {
  const root = await temporary(t), stateDir = path.join(root, 'state'), adoptionDir = path.join(root, 'adopted');
  await mkdir(stateDir); await mkdir(adoptionDir);
  const journal = path.join(stateDir, 'history.jsonl'); await writeFile(journal, 'existing history\n');
  await assert.rejects(launchAdoptedSimulator({ stateDir, adoptionDir, java: '/not-invoked' }), { code: 'EEXIST' });
  assert.equal(await readFile(journal, 'utf8'), 'existing history\n');
  await assert.rejects(launchAdoptedSimulator({ stateDir: 'relative', adoptionDir }), /absolute/);
  await assert.rejects(launchAdoptedSimulator({ stateDir: path.join(adoptionDir, 'nested-state'), adoptionDir }), /separate directory trees/);
  await assert.rejects(access(path.join(adoptionDir, 'nested-state')), { code: 'ENOENT' });
});

test('adoption subprocess requires unique completion, successful exit and declared authority', async () => {
  assert.deepEqual(await runAdoptionProcess(process.execPath, ['-e', print(complete)]), complete);
  for (const script of [print(complete) + print(complete), 'console.log("diagnostic only")', 'console.log("OPENPNP_CODEX_ADOPTED {")', print(null), print({ ...complete, scripts_activated: true }), print(complete) + 'process.exitCode=7']) {
    await assert.rejects(runAdoptionProcess(process.execPath, ['-e', script]), /completion|scope|failed/);
  }
});

test('oversized native output stops and reaps the owned subprocess', async t => {
  const root = await temporary(t), pidFile = path.join(root, 'child.pid');
  const script = `require('node:fs').writeFileSync(${JSON.stringify(pidFile)},String(process.pid));process.stdout.write('x'.repeat(300000));setInterval(()=>{},100);`;
  await assert.rejects(runAdoptionProcess(process.execPath, ['-e', script]), /output exceeded/);
  const pid = Number(await readFile(pidFile, 'utf8'));
  assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
});

test('native receipt reconstruction preserves split UTF-8 bytes and rejects invalid encoding', async () => {
  const expected = { ...complete, adoption_directory: '/fixture/café' };
  const script = `const b=Buffer.from('OPENPNP_CODEX_ADOPTED '+JSON.stringify(${JSON.stringify(expected)})+'\\n');const at=b.indexOf(0xc3)+1;process.stdout.write(b.subarray(0,at));setTimeout(()=>process.stdout.write(b.subarray(at)),50);`;
  assert.deepEqual(await runAdoptionProcess(process.execPath, ['-e', script]), expected);
  await assert.rejects(runAdoptionProcess(process.execPath, ['-e', 'process.stdout.write(Buffer.from([255]));']), /UTF-8/);
});

test('file-handle reads enforce the actual byte bound', async t => {
  const root = await temporary(t), file = path.join(root, 'bounded.bin');
  await writeFile(file, Buffer.alloc(65));
  await assert.rejects(readBoundedRegular(file, 64), /bounded/);
  await writeFile(file, Buffer.alloc(64));
  assert.equal((await readBoundedRegular(file, 64)).length, 64);
});

test('owned Java children exclude JVM and native loader overrides while preserving ordinary environment', async () => {
  const keys = ['JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS', 'CLASSPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH'];
  const source = Object.fromEntries(keys.map(key => [key, 'untrusted-override']));
  source.Path = '/ordinary/path'; source.java_tool_options = 'case-variant';
  const sanitized = javaEnvironment(source);
  assert.deepEqual(sanitized.environment, { Path: '/ordinary/path' });
  assert.equal(sanitized.ignored.length, keys.length + 1);
  assert.equal(source.JAVA_TOOL_OPTIONS, 'untrusted-override');
  const sentinel = 'OPENPNP_ENVIRONMENT_TEST_SENTINEL';
  const saved = Object.fromEntries([...keys, sentinel].map(key => [key, process.env[key]]));
  try {
    for (const key of keys) process.env[key] = 'fixture-only-must-not-reach-child';
    process.env[sentinel] = 'ordinary-value';
    const script = `const keys=${JSON.stringify(keys)};if(keys.some(key=>key in process.env)||process.env.${sentinel}!== 'ordinary-value')process.exit(9);${print(complete)}`;
    assert.deepEqual(await runAdoptionProcess(process.execPath, ['-e', script]), complete);
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});
