import assert from "node:assert/strict";
import { existsSync, realpathSync } from "node:fs";

import { CAPABILITY_PACKS, createLiveAuthority } from "./live-authority.mjs";
import { assertNoAncestorDependencies, createSandbox, McpClient, removeSandbox, toolData } from "./mcp-client.mjs";
import { createSyntheticCatalog, startSyntheticGrafana, SYNTHETIC_SCOPES } from "../support/synthetic-grafana.mjs";

export const SYNTHETIC_SCENARIOS = Object.freeze(["complete", "partial", "empty", "stale", "contradictory"]);

export function syntheticWindow({ durationMs = 20_000, endAgoMs = 1_000 } = {}) {
  const end = Math.floor(Date.now() / 1_000) * 1_000 - endAgoMs;
  return { start: new Date(end - durationMs).toISOString(), end: new Date(end).toISOString() };
}

/** Repository-only fixture. Never inherits a user's credentials or authority. */
export async function startSyntheticSession({
  scenario = "complete",
  conflictingClaims = false,
  // Trusted repository-test seams only, never exposed by the companion CLI.
  // Installed-client tests verify the candidate bytes before selecting its root.
  pluginRoot,
  startClient = ({ pluginRoot: root, env }) => McpClient.start(root, { env }),
} = {}) {
  assert.ok(SYNTHETIC_SCENARIOS.includes(scenario), "Unknown synthetic scenario");
  const selectedPluginRoot = pluginRoot === undefined ? undefined : realpathSync(pluginRoot);
  const sandbox = createSandbox(undefined, { copyPlugin: selectedPluginRoot === undefined, sourcePluginRoot: selectedPluginRoot });
  const outputs = [];
  let catalog;
  let provider;
  let client;
  let closing;
  const close = () => {
    closing ??= (async () => {
      try {
        if (client) {
          assert.deepEqual(await client.close(), { code: 0, signal: null }, "Packaged MCP server did not exit cleanly");
          assert.equal(client.stderr, "", "Packaged MCP server wrote unexpected diagnostics");
        }
        assert.deepEqual(provider?.unexpectedRequests ?? [], [], "Unexpected provider requests were rejected");
        const serialized = JSON.stringify(outputs);
        for (const token of catalog?.leakageTokens ?? []) {
          assert.equal(serialized.includes(token), false, "Provider-native fixture material leaked into MCP output");
        }
        assert.doesNotMatch(serialized, /datasourceUid|sourceIdentitySha256|providerValue|rawSql|executedQueryString|private_synthetic_/u);
      } finally {
        try { if (provider) await provider.close(); }
        finally { removeSandbox(sandbox.root); }
      }
      return { temporaryStateRemoved: !existsSync(sandbox.root), mcpClosed: true, providerClosed: true };
    })();
    return closing;
  };
  try {
    catalog = createSyntheticCatalog({ conflictingClaims: conflictingClaims || scenario === "contradictory" });
    assertNoAncestorDependencies(sandbox.pluginRoot);
    provider = await startSyntheticGrafana(catalog);
    const authority = createLiveAuthority(sandbox.root, {
      pluginRoot: sandbox.pluginRoot,
      origin: provider.origin,
      entries: catalog.entries,
      scopes: SYNTHETIC_SCOPES,
    });
    const launchEnv = { ...sandbox.env, ...authority.env };
    client = await startClient({ pluginRoot: sandbox.pluginRoot, env: launchEnv });
    if (scenario === "partial") provider.setBehavior("http-error", ["grafana.apm.traces.slow"]);
    if (scenario === "empty") provider.setBehavior("empty");
    const rawCall = async (name, args) => {
      const result = await client.callTool(name, args);
      outputs.push(result);
      return result;
    };
    const call = async (name, args) => toolData(await rawCall(name, args));
    const input = (packId, scopeAlias, key, window = syntheticWindow(), extra = {}) => {
      provider.allowWindow(window);
      if (extra.baselineWindow) provider.allowWindow(extra.baselineWindow);
      return { idempotencyKey: key, profileAlias: authority.profileAlias, packId, scopeAlias, origin: "SIMULATED", window, ...extra };
    };
    return { scenario, sandbox, catalog, provider, authority, client, launchEnv, outputs, rawCall, call, input, close };
  } catch (error) {
    try { await close(); } catch (cleanupError) {
      throw new AggregateError([error, cleanupError], "Synthetic setup failed; cleanup also reported an error");
    }
    throw error;
  }
}

/** Collect through the shipped MCP tools; no stored report or direct normalizer shortcut. */
export async function collectSyntheticIncident(session, {
  idempotencyKey = `onboarding-${session.scenario}`,
  window = syntheticWindow({ endAgoMs: session.scenario === "stale" ? 3_600_000 : 1_000 }),
  baseline = true,
} = {}) {
  const { authority, call, input, provider, scenario } = session;
  const profile = await call("grafana_profile_status", { profileAlias: authority.profileAlias });
  assert.notEqual(profile.status, "ERROR", "Synthetic profile check failed");
  assert.equal(profile.readiness, "WARN", "Anonymous synthetic permission uncertainty must remain visible");
  assert.deepEqual(profile.originModes, ["SIMULATED"]);
  const packs = await call("grafana_list_packs", { profileAlias: authority.profileAlias });
  const packId = scenario === "contradictory" ? "grafana.infrastructure" : "grafana.apm";
  const scopeAlias = scenario === "contradictory" ? "infrastructure" : "apm";
  assert.ok(packs.packs.some((pack) => pack.packId === packId), "Required synthetic pack is inactive");
  const duration = Date.parse(window.end) - Date.parse(window.start);
  const baselineWindow = { start: new Date(Date.parse(window.start) - duration).toISOString(), end: window.start };
  const request = input(packId, scopeAlias, idempotencyKey, window, baseline ? { baselineWindow } : {});
  if (baseline) provider.allowWindow(baselineWindow, { valueScale: 0.5 });
  const report = await call("grafana_run_pack", request);
  const evidence = [];
  const links = [];
  for (const card of report.evidence) {
    evidence.push(await call("grafana_get_evidence", { reportRef: report.reportRef, evidenceRef: card.evidenceRef }));
    for (const linkRef of card.linkRefs) {
      links.push(await call("grafana_resolve_link", { reportRef: report.reportRef, linkRef }));
    }
  }
  assert.equal(report.origin, "SIMULATED");
  return {
    profile, packs, request, report, evidence, links,
    transport: {
      kind: "PACKAGED_MCP_STDIO_AND_SIGNED_WORKER",
      provider: "LOOPBACK_SYNTHETIC_GRAFANA_HTTP",
      requiredOperationsPerWindow: CAPABILITY_PACKS.find((pack) => pack.packId === packId).requiredOperationIds.length,
      providerReads: provider.requests.filter((item) => item.operationId !== undefined).length,
      unexpectedRequests: provider.unexpectedRequests.length,
    },
  };
}
