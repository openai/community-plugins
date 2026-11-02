import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { CAPABILITY_PACKS } from "./helpers/live-authority.mjs";
import { startSyntheticSession } from "./helpers/synthetic-session.mjs";
import {
  PACK_CASES,
  SYNTHETIC_ERROR_CANARY,
  SYNTHETIC_TEXT_CANARY,
} from "./support/synthetic-grafana.mjs";

const INFRASTRUCTURE = "grafana.infrastructure";

function timeWindow({ durationMs = 3_600_000, endAgoMs = 1_000 } = {}) {
  const end = Math.floor(Date.now() / 1_000) * 1_000 - endAgoMs;
  return { start: new Date(end - durationMs).toISOString(), end: new Date(end).toISOString() };
}

function packFor(packId) {
  const pack = CAPABILITY_PACKS.find((candidate) => candidate.packId === packId);
  assert.ok(pack, `Missing test pack ${packId}`);
  return pack;
}

function providerQueries(provider) {
  return provider.requests.filter((request) => request.operationId !== undefined);
}

function assertToolError(result, code) {
  assert.equal(result.isError, true, JSON.stringify(result));
  assert.equal(result.structuredContent?.status, "ERROR");
  assert.equal(result.structuredContent?.error?.code, code);
  assert.equal(result.structuredContent?.error?.message, "The Grafana observability request was rejected");
  assert.deepEqual(JSON.parse(result.content.find((item) => item.type === "text").text), result.structuredContent);
}

function readEvidenceArtifacts(root) {
  const evidence = [];
  const visit = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile() && entry.name.startsWith("pmev_") && entry.name.endsWith(".json")) {
        const value = JSON.parse(readFileSync(path, "utf8"));
        if (value.evidenceId && value.operationId && value.source) evidence.push(value);
      }
    }
  };
  visit(root);
  return evidence;
}

async function startFixture(t, { conflictingClaims = false } = {}) {
  const session = await startSyntheticSession({ conflictingClaims });
  t.after(() => session.close());
  return session;
}

test("synthetic HTTP: copied package runs all five signed packs through real MCP stdio and worker transport", { timeout: 120_000 }, async (t) => {
  const fixture = await startFixture(t);
  const { catalog, provider, authority, call, rawCall, input } = fixture;
  const profile = await call("grafana_profile_status", { profileAlias: authority.profileAlias });
  // Anonymous Viewer fixtures cannot prove read authorization in advance.
  // The doctor must disclose that limitation rather than claim full readiness.
  assert.equal(profile.status, "PARTIAL");
  assert.equal(profile.readiness, "WARN");
  assert.deepEqual(profile.checks.filter((check) => check.status !== "PASS"), [{ name: "provider-read-authorization:grafana", status: "WARN" }]);
  assert.deepEqual(profile.originModes, ["SIMULATED"]);
  const listed = await call("grafana_list_packs", { profileAlias: authority.profileAlias });
  assert.equal(listed.status, "COMPLETE");
  assert.deepEqual(listed.packs.map((pack) => pack.packId).sort(), CAPABILITY_PACKS.map((pack) => pack.packId).sort());
  assert.deepEqual(listed.packs.map((pack) => pack.requiredOperationCount).sort((a, b) => a - b), [2, 3, 3, 4, 5]);

  const window = timeWindow();
  const reports = new Map();
  const details = new Map();
  const requestsBeforePacks = provider.requests.length;
  for (const [packId, scopeAlias] of PACK_CASES) {
    const pack = packFor(packId);
    const request = input(packId, scopeAlias, `synthetic-${scopeAlias}`, window);
    const report = await call("grafana_run_pack", request);
    reports.set(packId, report);
    assert.equal(report.status, "COMPLETE", packId);
    assert.equal(report.origin, "SIMULATED");
    assert.deepEqual(report.window, window);
    assert.equal(report.collectionFailures, 0, packId);
    assert.equal(report.evidence.length, pack.requiredOperationIds.length, packId);
    assert.deepEqual(report.evidence.map((card) => card.title).sort(), [...pack.requiredOperationIds].sort(), packId);
    assert.deepEqual(report.evidenceSummary, { COMPLETE: pack.requiredOperationIds.length, EMPTY: 0, STALE: 0, CONTRADICTORY: 0, ERROR: 0 });
    assert.deepEqual(report.signals.map((signal) => signal.title), [...pack.signals].sort((a, b) => a.order - b.order).map((signal) => signal.signalId));
    assert.match(report.reportRef, /^gfrpt_[a-f0-9]{64}$/u);
    assert.doesNotMatch(JSON.stringify(report), /https?:\/\//iu);
    for (const card of report.evidence) {
      assert.equal(card.status, "COMPLETE", card.title);
      assert.match(card.evidenceRef, /^gfev_[a-f0-9]{64}$/u);
      assert.equal(card.linkRefs.length, 1, card.title);
      const detail = await call("grafana_get_evidence", { reportRef: report.reportRef, evidenceRef: card.evidenceRef });
      details.set(detail.title, detail);
      assert.equal(detail.status, "COMPLETE", card.title);
      assert.equal(detail.provider, "grafana", card.title);
      assert.equal(detail.origin, "SIMULATED", card.title);
      assert.equal(detail.reportRef, report.reportRef);
      assert.equal(detail.evidenceRef, card.evidenceRef);
      assert.deepEqual(detail.window, window);
      assert.deepEqual(detail.linkRefs, card.linkRefs);
      assert.ok(detail.values.length > 0, card.title);
      assert.doesNotMatch(JSON.stringify(detail), /https?:\/\//iu);
      const compiled = catalog.compiled.find((item) => item.operationId === card.title);
      const linkRef = card.linkRefs[0];
      assert.match(linkRef, /^gflnk_[a-f0-9]{64}$/u);
      const resolved = await call("grafana_resolve_link", { reportRef: report.reportRef, linkRef });
      assert.equal(resolved.status, "COMPLETE");
      assert.equal(resolved.mode, "OPEN_EXTERNAL");
      assert.equal(resolved.reportRef, report.reportRef);
      assert.equal(resolved.linkRef, linkRef);
      const url = new URL(resolved.url);
      assert.equal(url.origin, provider.origin);
      assert.equal(url.pathname, `/d/${compiled.entry.sourcePanel.dashboardUid}`);
      assert.equal(url.searchParams.get("viewPanel"), String(compiled.entry.sourcePanel.panelId));
      assert.equal(url.username + url.password + url.hash, "");
    }
    const afterFirstRun = provider.requests.length;
    assert.deepEqual(await call("grafana_run_pack", request), report, `${packId} exact retry must replay`);
    assert.equal(provider.requests.length, afterFirstRun, "An exact retry must make no provider requests");
    assertToolError(await rawCall("grafana_run_pack", { ...request, window: timeWindow({ endAgoMs: 3_600_000 }) }), "IDEMPOTENCY_CONFLICT");
    assert.equal(provider.requests.length, afterFirstRun, "A conflicting retry must fail before provider I/O");
  }

  const queries = providerQueries(provider);
  assert.equal(queries.length, 17);
  assert.equal(provider.requests.length - requestsBeforePacks, 34, "Each operation has one health preflight and one bounded read");
  for (const compiled of catalog.compiled) {
    const requests = queries.filter((request) => request.operationId === compiled.operationId);
    assert.equal(requests.length, 1, compiled.operationId);
    assert.equal(requests[0].datasourceType, compiled.entry.target.datasourceType, compiled.operationId);
    assert.equal(requests[0].method, compiled.entry.target.datasourceType === "tempo" ? "GET" : "POST");
    assert.equal(requests[0].path, compiled.entry.target.datasourceType === "tempo"
      ? `/api/datasources/proxy/uid/${compiled.entry.datasourceUid}/api/search`
      : "/api/ds/query");
  }

  const infra = reports.get(INFRASTRUCTURE);
  const logs = reports.get("grafana.logs");
  const beforeOwnershipChecks = provider.requests.length;
  assertToolError(await rawCall("grafana_get_evidence", { reportRef: infra.reportRef, evidenceRef: logs.evidence[0].evidenceRef }), "EVIDENCE_NOT_FOUND");
  assertToolError(await rawCall("grafana_resolve_link", { reportRef: infra.reportRef, linkRef: logs.evidence[0].linkRefs[0] }), "LINK_NOT_FOUND");
  assertToolError(await rawCall("grafana_get_evidence", { reportRef: `gfrpt_${"f".repeat(64)}`, evidenceRef: infra.evidence[0].evidenceRef }), "REPORT_NOT_FOUND");
  assertToolError(await rawCall("grafana_resolve_link", { reportRef: `gfrpt_${"f".repeat(64)}`, linkRef: infra.evidence[0].linkRefs[0] }), "REPORT_NOT_FOUND");
  assert.equal(provider.requests.length, beforeOwnershipChecks, "Opaque evidence/link ownership checks are local");

  assert.equal(details.get("grafana.apm.error.codes").domain, "LOGS");
  assert.ok(details.get("grafana.apm.error.codes").values.some((value) => value.kind === "EVENT" && /HTTP 503/u.test(value.summary)));
  assert.equal(details.get("grafana.apm.traces.slow").domain, "TRACES");
  assert.ok(details.get("grafana.apm.traces.slow").values.some((value) => value.kind === "EVENT" && /Slow checkout trace/u.test(value.summary)));
  assert.ok(infra.issues.some((issue) => issue.kind === "ALERT" && issue.classification === "SIGNED_RULE" && issue.severity === "CRITICAL"));
  assert.ok(logs.issues.some((issue) => issue.kind === "ANOMALY" && issue.classification === "SIGNED_RULE"));
  assert.ok(reports.get("grafana.iot-edge").issues.some((issue) => issue.kind === "ALERT" && issue.classification === "SIGNED_RULE"));

  const artifacts = readEvidenceArtifacts(fixture.sandbox.artifactRoot);
  assert.equal(artifacts.length, 17, "Every completed operation must be sealed in local evidence artifacts");
  for (const compiled of catalog.compiled) {
    const envelope = artifacts.find((item) => item.operationId === compiled.operationId);
    assert.ok(envelope, compiled.operationId);
    assert.equal(envelope.origin, "SIMULATED");
    assert.equal(envelope.collectionState, "COMPLETE");
    assert.equal(envelope.source.lineage.accessPath, "GRAFANA");
    assert.equal(envelope.source.lineage.datasourceType, compiled.entry.target.datasourceType === "grafana-postgresql-datasource" ? "postgres" : compiled.entry.target.datasourceType);
    if (compiled.entry.scope.subject) assert.ok(JSON.stringify(envelope.values).includes(compiled.entry.scope.subject.ref));
  }
  const serializedArtifacts = JSON.stringify(artifacts);
  for (const token of catalog.leakageTokens) assert.equal(serializedArtifacts.includes(token), false, "Provider-native fixture material leaked into sealed evidence");
});

test("synthetic HTTP: packaged reports preserve empty, stale, partial, denied, drifted, and rate-limited states", { timeout: 120_000 }, async (t) => {
  const { provider, call, input } = await startFixture(t);
  const operationIds = packFor(INFRASTRUCTURE).requiredOperationIds;
  const currentWindow = timeWindow();
  const run = async (name, mode, { selected, window = currentWindow, expectedStatus, expectedStates, expectedQueries = operationIds.length, collectionFailures = 0 } = {}) => {
    provider.setBehavior(mode, selected);
    const before = providerQueries(provider).length;
    const report = await call("grafana_run_pack", input(INFRASTRUCTURE, "infrastructure", `synthetic-state-${name}`, window));
    assert.equal(report.status, expectedStatus, name);
    assert.equal(report.collectionFailures, collectionFailures, name);
    for (const [state, count] of Object.entries(expectedStates)) assert.equal(report.evidenceSummary[state], count, `${name}: ${state}`);
    assert.equal(providerQueries(provider).length - before, expectedQueries, name);
    for (const card of report.evidence) {
      const detail = await call("grafana_get_evidence", { reportRef: report.reportRef, evidenceRef: card.evidenceRef });
      assert.equal(detail.status, card.status, `${name}: ${card.title}`);
      assert.equal(detail.origin, "SIMULATED");
      if (card.status === "ERROR" || card.status === "EMPTY") assert.equal(detail.values.length, 0, `${name} must not invent evidence`);
    }
    assert.equal(JSON.stringify(report).includes(SYNTHETIC_ERROR_CANARY), false);
    return report;
  };

  const empty = await run("empty", "empty", { expectedStatus: "EMPTY", expectedStates: { EMPTY: 4, COMPLETE: 0 } });
  assert.equal(empty.signals.length, 0, "An empty response must not be reported as zero utilization");
  await run("stale", "complete", { window: timeWindow({ endAgoMs: 3_600_000 }), expectedStatus: "STALE", expectedStates: { STALE: 4, COMPLETE: 0 } });
  const partial = await run("partial", "http-error", { selected: [operationIds[3]], expectedStatus: "PARTIAL", expectedStates: { COMPLETE: 3, ERROR: 1 } });
  assert.ok(partial.issues.some((issue) => issue.kind === "EVIDENCE_GAP"));
  await run("unavailable", "http-error", { expectedStatus: "ERROR", expectedStates: { ERROR: 4, COMPLETE: 0 } });
  await run("denied", "query-error", { expectedStatus: "ERROR", expectedStates: { ERROR: 4, COMPLETE: 0 } });
  await run("shape-drift", "drift", { expectedStatus: "ERROR", expectedStates: { ERROR: 4, COMPLETE: 0 } });
  await run("scope-mismatch", "scope-mismatch", { expectedStatus: "ERROR", expectedStates: { ERROR: 4, COMPLETE: 0 } });
  await run("rate-limit", "rate-limit", { selected: [operationIds[0]], expectedStatus: "ERROR", expectedStates: { ERROR: 1, COMPLETE: 0 }, expectedQueries: 1, collectionFailures: 3 });
});

test("synthetic HTTP: current/baseline collection and sensitive provider text remain bounded", { timeout: 120_000 }, async (t) => {
  const { provider, call, input } = await startFixture(t);
  const window = timeWindow({ durationMs: 20_000 });
  const baselineWindow = { start: new Date(Date.parse(window.start) - 20_000).toISOString(), end: window.start };
  const request = input("grafana.apm", "apm", "synthetic-baseline", window, { baselineWindow });
  provider.allowWindow(baselineWindow, { valueScale: 0.5 });
  const before = providerQueries(provider).length;
  const report = await call("grafana_run_pack", request);
  assert.equal(report.status, "COMPLETE");
  assert.deepEqual(report.baselineWindow, baselineWindow);
  assert.equal(report.evidence.length, 10);
  assert.equal(providerQueries(provider).length - before, 10);
  assert.equal(report.evidenceSummary.COMPLETE, 10);
  assert.ok(report.issues.some((issue) => issue.kind === "REGRESSION" && issue.classification === "SIGNED_RULE"));
  const windows = [];
  for (const card of report.evidence) {
    const detail = await call("grafana_get_evidence", { reportRef: report.reportRef, evidenceRef: card.evidenceRef });
    windows.push(detail.window);
  }
  assert.equal(windows.filter((value) => value.start === window.start && value.end === window.end).length, 5);
  assert.equal(windows.filter((value) => value.start === baselineWindow.start && value.end === baselineWindow.end).length, 5);

  provider.setBehavior("unsafe-text", ["grafana.apm.error.codes"]);
  const redacted = await call("grafana_run_pack", input("grafana.apm", "apm", "synthetic-sensitive-text"));
  const card = redacted.evidence.find((item) => item.title === "grafana.apm.error.codes");
  assert.ok(card);
  const detail = await call("grafana_get_evidence", { reportRef: redacted.reportRef, evidenceRef: card.evidenceRef });
  assert.equal(JSON.stringify({ redacted, detail }).includes(SYNTHETIC_TEXT_CANARY), false);
  assert.doesNotMatch(JSON.stringify(detail), /Authorization:\s*Bearer/u);
});

test("synthetic HTTP: conflicting signed scalar claims produce a contradictory report", { timeout: 120_000 }, async (t) => {
  // This alternate test authority intentionally enrolls two differing scalar
  // claims under the same normalized name. It is not a recommended catalog.
  const { provider, call, input } = await startFixture(t, { conflictingClaims: true });
  const report = await call("grafana_run_pack", input(INFRASTRUCTURE, "infrastructure", "synthetic-contradiction"));
  assert.equal(report.status, "CONTRADICTORY");
  assert.equal(report.evidenceSummary.CONTRADICTORY, 2);
  assert.equal(report.evidenceSummary.COMPLETE, 2);
  assert.equal(providerQueries(provider).length, 4);
  const contradiction = report.issues.find((issue) => issue.kind === "CONTRADICTION");
  assert.ok(contradiction);
  assert.equal(contradiction.evidenceRefs.length, 2);
  for (const evidenceRef of contradiction.evidenceRefs) {
    const detail = await call("grafana_get_evidence", { reportRef: report.reportRef, evidenceRef });
    assert.equal(detail.status, "CONTRADICTORY");
  }
});

test("synthetic HTTP: native Tempo and Loki responses cannot bypass enrolled scope or shape", { timeout: 120_000 }, async (t) => {
  const { provider, call, input } = await startFixture(t);
  const cases = [
    ["tempo-scope", "scope-mismatch", "grafana.apm.traces.slow"],
    ["tempo-identity", "drift", "grafana.apm.traces.slow"],
    ["loki-scope", "scope-mismatch", "grafana.apm.error.codes"],
    ["loki-shape", "drift", "grafana.apm.error.codes"],
  ];
  for (const [name, mode, operationId] of cases) {
    provider.setBehavior(mode, [operationId]);
    const before = providerQueries(provider).length;
    const report = await call("grafana_run_pack", input("grafana.apm", "apm", `synthetic-native-${name}`));
    assert.equal(report.status, "PARTIAL", name);
    assert.equal(report.evidenceSummary.COMPLETE, 4, name);
    assert.equal(report.evidenceSummary.ERROR, 1, name);
    assert.equal(providerQueries(provider).length - before, 5, name);
    const card = report.evidence.find((item) => item.title === operationId);
    assert.equal(card.status, "ERROR", name);
    const detail = await call("grafana_get_evidence", { reportRef: report.reportRef, evidenceRef: card.evidenceRef });
    assert.equal(detail.status, "ERROR", name);
    assert.deepEqual(detail.values, [], "Rejected native records must not become evidence");
  }
});
