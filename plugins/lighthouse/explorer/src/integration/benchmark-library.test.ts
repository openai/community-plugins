import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { BenchmarkLibrary, libraryOptionsFromEnvironment } from "../benchmark-library.ts";

async function marker(directory: string, name: string): Promise<string> {
  await mkdir(directory, { recursive: true });
  const result = path.join(directory, "benchmark.lighthouse");
  await writeFile(result, JSON.stringify({ schemaVersion: 1, name }));
  return result;
}

test("benchmark discovery is shallow and defaults to conventional checkout names", async () => {
  const temporary = await mkdtemp(path.join(tmpdir(), "lighthouse-library-"));
  try {
    const home = path.join(temporary, "code");
    await marker(path.join(home, "second-bench-3"), "Second");
    await marker(path.join(home, "first-bench"), "First");
    await marker(path.join(home, "different-name"), "Explicitly discoverable");
    await marker(path.join(home, "nested-bench", "hidden-bench"), "Too deep");
    await marker(path.join(home, ".hidden-bench"), "Hidden");
    await mkdir(path.join(home, "invalid-bench"), { recursive: true });
    await writeFile(path.join(home, "invalid-bench", "benchmark.lighthouse"), '{"schemaVersion":99}');
    const linked = path.join(home, "link-bench");
    await symlink(path.join(home, "first-bench"), linked, "dir");

    const standard = new BenchmarkLibrary(libraryOptionsFromEnvironment({}, temporary));
    const result = await standard.list();
    assert.deepEqual(result.benchmarks.map((item) => item.name), ["First", "Second"]);
    assert.equal(result.preferredId, undefined);
    assert.doesNotMatch(JSON.stringify(result), new RegExp(temporary));

    const explicit = new BenchmarkLibrary(libraryOptionsFromEnvironment({ LIGHTHOUSE_BENCHMARK_HOME: home }, temporary));
    const expanded = await explicit.list();
    assert.deepEqual(expanded.benchmarks.map((item) => item.name), ["Explicitly discoverable", "First", "Second"]);
    assert.equal((await standard.attach(linked)).id, result.benchmarks[0]?.id);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

test("explicit selection validates exact markers, keeps stable opaque identities, and controls defaults", async () => {
  const temporary = await mkdtemp(path.join(tmpdir(), "lighthouse-library-"));
  try {
    const first = await marker(path.join(temporary, "checkout-a"), "One");
    const second = await marker(path.join(temporary, "checkout-b"), "Two");
    const emptyHome = path.join(temporary, "missing");
    const opts = libraryOptionsFromEnvironment({ LIGHTHOUSE_BENCHMARKS: [first, path.dirname(first)].join(path.delimiter), LIGHTHOUSE_BENCHMARK_HOME: emptyHome });
    const registry = new BenchmarkLibrary(opts);
    const firstList = await registry.list();
    const item = firstList.benchmarks[0];
    assert.ok(item);
    assert.equal(firstList.preferredId, item.id);
    assert.equal((await registry.attach(path.dirname(first))).id, item.id);
    assert.equal((await new BenchmarkLibrary(opts).attach(first)).id, item.id);
    assert.match(item.id, /^benchmark_[A-Za-z0-9_-]{43}$/);
    assert.equal(await registry.resolve(item.id), await new BenchmarkLibrary(opts).resolve(item.id));

    const next = await registry.attach(second);
    assert.notEqual(next.id, item.id);
    assert.equal((await registry.list()).preferredId, item.id);
    await assert.rejects(registry.resolve(second), /Choose a known benchmark/);
    await assert.rejects(registry.resolve("benchmark_unknown"), /Choose a known benchmark/);
    await assert.rejects(registry.attach("checkout-a"), /absolute benchmark folder/);
    await assert.rejects(registry.attach(`${path.dirname(first)}/../checkout-b`), /absolute benchmark folder/);
    await assert.rejects(registry.attach(path.join(temporary, "missing")), /No readable benchmark/);
    await writeFile(path.join(temporary, ".env"), "secret-value");
    await assert.rejects(registry.attach(path.join(temporary, ".env")), /No readable benchmark/);

    const markerLink = path.join(temporary, "marker-link");
    await mkdir(markerLink);
    await symlink(first, path.join(markerLink, "benchmark.lighthouse"));
    await assert.rejects(registry.attach(markerLink), /may not point outside/);

    const multiple = new BenchmarkLibrary(libraryOptionsFromEnvironment({ LIGHTHOUSE_BENCHMARKS: [first, second].join(path.delimiter), LIGHTHOUSE_BENCHMARK_HOME: emptyHome }));
    assert.equal((await multiple.list()).preferredId, undefined);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});
