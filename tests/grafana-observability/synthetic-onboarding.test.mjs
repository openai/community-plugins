import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

import { isolatedEnvironment, REPOSITORY_ROOT } from "./helpers/mcp-client.mjs";
import { collectSyntheticIncident, startSyntheticSession, SYNTHETIC_SCENARIOS } from "./helpers/synthetic-session.mjs";

function run(t, args, extraEnv = {}) {
  const temporaryRoot = mkdtempSync(join(tmpdir(), "grafana-onboarding-process-"));
  t.after(() => rmSync(temporaryRoot, { recursive: true, force: true }));
  const marker = join(temporaryRoot, "unrelated-owner-file");
  writeFileSync(marker, "preserve");
  const result = spawnSync(process.execPath, [join(REPOSITORY_ROOT, "scripts/demo-grafana-synthetic.mjs"), ...args], {
    cwd: temporaryRoot,
    env: isolatedEnvironment({ TMPDIR: temporaryRoot, TMP: temporaryRoot, TEMP: temporaryRoot, ...extraEnv }),
    encoding: "utf8", timeout: 120_000, maxBuffer: 4 * 1024 * 1024,
  });
  assert.equal(result.error, undefined, result.error?.message);
  assert.deepEqual(readdirSync(temporaryRoot), ["unrelated-owner-file"], "Onboarding must remove only its own temporary state");
  return result;
}

test("onboarding subprocess uses actual packaged MCP and isolates inherited authority and credentials", { timeout: 120_000 }, (t) => {
  const canary = "synthetic-inherited-secret-canary";
  const result = run(t, ["--json"], {
    GRAFANA_API_TOKEN: canary,
    PRODUCTION_MONITORING_MODE: "live",
    PRODUCTION_MONITORING_TRUST_ROOT: "/nonexistent/production-authority",
    PRODUCTION_MONITORING_ACTIVATION_RECEIPT: "/nonexistent/production-receipt",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");
  assert.equal(result.stdout.includes(canary), false);
  const packet = JSON.parse(result.stdout);
  assert.equal(packet.origin, "SIMULATED");
  assert.equal(packet.report.status, "COMPLETE");
  assert.equal(packet.profile.status, "PARTIAL");
  assert.equal(packet.profile.readiness, "WARN");
  assert.equal(packet.request.packId, "grafana.apm");
  assert.equal(packet.transport.providerReads, 10);
  assert.equal(packet.transport.unexpectedRequests, 0);
  assert.equal(packet.evidence.length, 10);
  assert.equal(packet.links.length, 10);
  assert.ok(packet.report.issues.some((issue) => issue.kind === "REGRESSION" && issue.classification === "SIGNED_RULE"));
  assert.ok(packet.evidence.some((detail) => detail.title === "grafana.apm.error.codes" && detail.values.some((value) => value.summary?.includes("HTTP 503"))));
  assert.ok(packet.evidence.every((detail) => detail.reportRef === packet.report.reportRef && detail.origin === "SIMULATED"));
  assert.equal(packet.qualification.installedClient, false);
  assert.equal(packet.qualification.realGrafana, false);
  assert.equal(packet.qualification.diagnosis, "NOT_ESTABLISHED");
  assert.equal(packet.qualification.recovery, "NOT_ASSESSED");
  assert.deepEqual(packet.cleanup, { temporaryStateRemoved: true, mcpClosed: true, providerClosed: true });
});

for (const [scenario, expected] of [["partial", "PARTIAL"], ["empty", "EMPTY"], ["stale", "STALE"], ["contradictory", "CONTRADICTORY"]]) {
  test(`onboarding subprocess preserves ${scenario} instead of manufacturing a successful incident`, { timeout: 120_000 }, (t) => {
    const result = run(t, ["--scenario", scenario, "--json"]);
    assert.equal(result.status, 0, result.stderr);
    const packet = JSON.parse(result.stdout);
    assert.equal(packet.report.status, expected);
    assert.equal(packet.origin, "SIMULATED");
    assert.equal(packet.qualification.diagnosis, "NOT_ESTABLISHED");
    assert.equal(packet.qualification.recovery, "NOT_ASSESSED");
    assert.ok(packet.transport.providerReads > 0, "Scenario must traverse the real worker transport");
    assert.equal(packet.transport.unexpectedRequests, 0);
    if (scenario === "partial") assert.ok(packet.evidence.some((item) => item.title === "grafana.apm.traces.slow" && item.status === "ERROR" && item.values.length === 0));
    if (scenario === "empty") assert.equal(packet.report.signals.length, 0);
    if (scenario === "contradictory") assert.ok(packet.report.issues.some((item) => item.kind === "CONTRADICTION"));
  });
}

test("onboarding refuses unbounded inputs before starting a synthetic session", (t) => {
  for (const args of [
    ["--scenario", "LIVE"], ["--url", "https://example.invalid"], ["--query", "arbitrary"],
    ["--output", "/tmp/unowned"], ["--json", "--json"], ["--scenario"],
  ]) {
    const result = run(t, args);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /onboarding failed/u);
    assert.doesNotMatch(result.stderr, /example.invalid|arbitrary|unowned/u);
  }
  assert.deepEqual(SYNTHETIC_SCENARIOS, ["complete", "partial", "empty", "stale", "contradictory"]);
});

test("shared synthetic session cleans owned processes/state after a collection failure and closes idempotently", { timeout: 120_000 }, async () => {
  const session = await startSyntheticSession();
  const root = session.sandbox.root;
  const providerOrigin = session.provider.origin;
  try {
    await assert.rejects(collectSyntheticIncident(session, { window: { start: "not-a-time", end: "not-a-time" } }));
  } finally {
    const first = session.close();
    assert.equal(session.close(), first, "Concurrent cleanup must share one owned teardown");
    await first;
  }
  assert.equal(existsSync(root), false);
  await assert.rejects(fetch(`${providerOrigin}/api/health`));
});

test("sandbox construction failure removes its new root and preserves unrelated files", (t) => {
  const parent = mkdtempSync(join(tmpdir(), "grafana-startup-failure-"));
  t.after(() => rmSync(parent, { recursive: true, force: true }));
  writeFileSync(join(parent, "unrelated-owner-file"), "preserve");
  const helper = pathToFileURL(join(REPOSITORY_ROOT, "tests/grafana-observability/helpers/mcp-client.mjs")).href;
  const code = `import assert from 'node:assert/strict'; import { createSandbox } from ${JSON.stringify(helper)}; assert.throws(() => createSandbox(undefined, { sourcePluginRoot: './missing-candidate' }));`;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", code], {
    cwd: parent, env: isolatedEnvironment({ TMPDIR: parent, TMP: parent, TEMP: parent }),
    encoding: "utf8", timeout: 10_000,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(readdirSync(parent), ["unrelated-owner-file"]);
});

test("synthetic startup cleans authority and provider when the client cannot start", { timeout: 30_000 }, async () => {
  let ownedRoot;
  await assert.rejects(startSyntheticSession({
    startClient: async ({ env }) => {
      ownedRoot = dirname(env.PRODUCTION_MONITORING_STATE_ROOT);
      throw new Error("Injected client startup failure");
    },
  }), /Injected client startup failure/u);
  assert.equal(typeof ownedRoot, "string");
  assert.equal(existsSync(ownedRoot), false);
});
