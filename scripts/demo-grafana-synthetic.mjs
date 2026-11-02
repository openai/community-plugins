#!/usr/bin/env node

import { pathToFileURL } from "node:url";
import { collectSyntheticIncident, startSyntheticSession, SYNTHETIC_SCENARIOS } from "../tests/grafana-observability/helpers/synthetic-session.mjs";

const USAGE = "Usage: node scripts/demo-grafana-synthetic.mjs [--scenario complete|partial|empty|stale|contradictory] [--json]\n";

function parseArguments(args) {
  const options = { scenario: "complete", json: false };
  const seen = new Set();
  for (let index = 0; index < args.length; index += 1) {
    const flag = args[index];
    if (seen.has(flag)) throw new Error("Duplicate argument");
    seen.add(flag);
    if (flag === "--json") options.json = true;
    else if (flag === "--scenario" && SYNTHETIC_SCENARIOS.includes(args[index + 1])) options.scenario = args[++index];
    else throw new Error("Unsupported argument");
  }
  return options;
}

export async function runSyntheticOnboarding({ scenario = "complete" } = {}) {
  const session = await startSyntheticSession({ scenario });
  let result;
  let cleanup;
  try {
    result = await collectSyntheticIncident(session);
  } finally {
    cleanup = await session.close();
  }
  return {
    schemaVersion: "1.0.0", origin: "SIMULATED", scenario,
    ...result,
    qualification: {
      packagedMcpExecution: true, syntheticProvider: true,
      realGrafana: false, productionAccess: false, installedClient: false,
      diagnosis: "NOT_ESTABLISHED", recovery: "NOT_ASSESSED",
      sourceLinks: "SESSION_ENDED",
    },
    cleanup,
  };
}

function readable(result) {
  return [
    "SIMULATED — Grafana incident onboarding",
    `Scenario: ${result.scenario}; report: ${result.report.status}; readiness: ${result.profile.readiness}`,
    `Pack: ${result.request.packId}; scope: ${result.request.scopeAlias}`,
    `Current: ${result.request.window.start} → ${result.request.window.end}`,
    `Baseline: ${result.request.baselineWindow.start} → ${result.request.baselineWindow.end}`,
    `Evidence: ${result.evidence.length} records from ${result.transport.providerReads} bounded synthetic provider reads`,
    `Report reference: ${result.report.reportRef}`,
    "",
    JSON.stringify({ signals: result.report.signals, issues: result.report.issues, evidenceSummary: result.report.evidenceSummary }, null, 2),
    "",
    "The actual packaged MCP and signed worker collected this synthetic HTTP evidence.",
    "Anonymous fixture authorization remains WARN. This is not a real Grafana or installed-client qualification.",
    "No cause or recovery is established. No message, page, patch, or runtime remediation was performed.",
    "Temporary authority, evidence and processes are removed; source links belonged to the ended synthetic session.",
    "Use --json for the complete normalized evidence packet and the incident-workflow evaluation kit.",
    "",
  ].join("\n");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length === 3 && process.argv[2] === "--help") process.stdout.write(USAGE);
  else {
    try {
      const options = parseArguments(process.argv.slice(2));
      const result = await runSyntheticOnboarding(options);
      process.stdout.write(options.json ? `${JSON.stringify(result)}\n` : readable(result));
    } catch {
      // Do not print raw child/provider errors or inherited environment values.
      process.stderr.write(`Synthetic Grafana onboarding failed. No production access was requested.\n${USAGE}`);
      process.exitCode = 1;
    }
  }
}
