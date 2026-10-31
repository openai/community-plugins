#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { collectSyntheticIncident, startSyntheticSession } from "../tests/grafana-observability/helpers/synthetic-session.mjs";
import {
  CASES, OUTPUT_SCHEMA, candidateBundle, checkOutput, evaluationPrompt, makePacket,
  jsonBytes, newTrace, recordDiagnostic, recordEvent, receiptIdentity, reviewTemplate, sha256,
} from "../tests/grafana-observability/support/incident-evaluation.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const help = `Prepare frozen synthetic Grafana incident cases or explicitly run source-skill model evaluations.

  node scripts/evaluate-grafana-incidents.mjs --prepare [--cases id,id] [--out NEW_DIRECTORY]
  GRAFANA_E2E_SKILL_EVAL=1 node scripts/evaluate-grafana-incidents.mjs --run [--cases id,id] [--out NEW_DIRECTORY] [--model MODEL]
  node scripts/evaluate-grafana-incidents.mjs --list

Defaults to --prepare. --run uses the existing Codex sign-in without reading or copying auth.
No plugin is installed. Every final output still requires independent semantic review.
Cases: ${CASES.map((entry) => entry.id).join(", ")}`;

function options(args) {
  const result = { mode: "prepare", cases: CASES.map((entry) => entry.id), out: null, model: null };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (["--help", "-h"].includes(arg)) return { mode: "help" };
    if (["--prepare", "--run", "--list"].includes(arg)) result.mode = arg.slice(2);
    else if (["--cases", "--out", "--model"].includes(arg)) {
      const value = args[++index];
      assert.ok(value && !value.startsWith("--"), `Missing value for ${arg}`);
      if (arg === "--cases") result.cases = value.split(",");
      else result[arg.slice(2)] = value;
    } else throw new Error(`Unknown option ${arg}`);
  }
  assert.equal(new Set(result.cases).size, result.cases.length, "Duplicate scenario IDs");
  assert.ok(result.cases.length > 0 && result.cases.every((id) => CASES.some((entry) => entry.id === id)), "Unknown scenario ID");
  if (result.model !== null) assert.match(result.model, /^[A-Za-z0-9._:/+-]{1,160}$/u);
  return result;
}

function save(path, value) {
  writeFileSync(path, typeof value === "string" ? value : jsonBytes(value), { flag: "wx", mode: 0o600 });
}

// Keep normal auth lookup, but do not forward API keys, monitoring credentials,
// proxy overrides, NODE_OPTIONS or the parent task's plugin/runtime settings.
function clientEnvironment() {
  return Object.fromEntries(["PATH", "HOME", "USERPROFILE", "USER", "LOGNAME", "CODEX_HOME", "SystemRoot", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP"]
    .filter((name) => process.env[name] !== undefined).map((name) => [name, process.env[name]]));
}

async function modelTurn({ prompt, work, model }) {
  const schema = join(work, "output-schema.json");
  const finalPath = join(work, "final.txt");
  save(schema, OUTPUT_SCHEMA);
  const args = [
    "exec", "--ephemeral", "--ignore-user-config", "--sandbox", "read-only", "--skip-git-repo-check",
    "--json", "--color", "never", "--cd", work, "--output-schema", schema, "--output-last-message", finalPath,
    "-c", "approval_policy=\"never\"", "-c", "web_search=\"disabled\"",
    "-c", "skills.include_instructions=false", "-c", "features.plugins=false",
    "-c", "features.apps=false", "-c", "features.hooks=false", "-c", "features.shell_tool=false",
  ];
  if (model !== null) args.push("--model", model);
  args.push("-");
  const trace = newTrace();
  const startedAt = new Date().toISOString();
  const begin = Date.now();
  const child = spawn("codex", args, { cwd: work, env: clientEnvironment(), stdio: ["pipe", "pipe", "pipe"] });
  let buffered = "";
  let stderrLine = "";
  let exceeded = false;
  let totalBytes = 0;
  let timedOut = false;
  let force;
  const stop = () => { child.kill("SIGTERM"); force ??= setTimeout(() => child.kill("SIGKILL"), 2_000); };
  const timer = setTimeout(() => { timedOut = true; stop(); }, 240_000);
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    totalBytes += Buffer.byteLength(chunk);
    if (totalBytes > 8 * 1024 * 1024) { exceeded = true; stop(); return; }
    buffered += chunk;
    let newline;
    while ((newline = buffered.indexOf("\n")) !== -1) {
      const line = buffered.slice(0, newline).trim();
      buffered = buffered.slice(newline + 1);
      if (line) recordEvent(trace, line);
    }
    if (trace.toolAttempts.length > 0) stop();
  });
  // Some client versions print a model header even in JSON mode. Parse only
  // this bounded identifier; never retain raw stderr, auth errors or startup logs.
  child.stderr.on("data", (chunk) => {
    stderrLine = `${stderrLine}${chunk}`.slice(-4_096);
    let newline;
    while ((newline = stderrLine.indexOf("\n")) !== -1) {
      const line = stderrLine.slice(0, newline).trim();
      stderrLine = stderrLine.slice(newline + 1);
      recordDiagnostic(trace, line);
      if (trace.criticalConfigurationErrors.length > 0) stop();
    }
  });
  child.stdin.on("error", () => {});
  child.stdin.end(prompt);
  let exit;
  try {
    exit = await new Promise((accept, reject) => { child.once("error", reject); child.once("close", (code, signal) => accept({ code, signal })); });
  } finally { clearTimeout(timer); clearTimeout(force); }
  if (buffered.trim()) recordEvent(trace, buffered.trim());
  if (stderrLine.trim()) recordDiagnostic(trace, stderrLine.trim());
  const execution = { source: "HARNESS_CODEX_EXEC", startedAt, durationMs: Date.now() - begin, ...exit, timedOut, outputBoundExceeded: exceeded, requestedModel: model ?? "CLIENT_DEFAULT", reportedModel: trace.reportedModel, trace };
  if (exit.code !== 0 || timedOut || exceeded || trace.toolAttempts.length > 0 || trace.criticalConfigurationErrors.length > 0 || trace.failureCategories.length > 0) return { execution, final: null };
  const final = readFileSync(finalPath, "utf8");
  assert.ok(Buffer.byteLength(final) <= 256 * 1024, "Final output exceeds retention bound");
  return { execution, final };
}

async function main() {
  const selected = options(process.argv.slice(2));
  if (selected.mode === "help") { console.log(help); return; }
  if (selected.mode === "list") { console.log(CASES.map((entry) => `${entry.id}\t${entry.skill}\t${entry.focus}`).join("\n")); return; }
  if (selected.mode === "run") assert.equal(process.env.GRAFANA_E2E_SKILL_EVAL, "1", "Set GRAFANA_E2E_SKILL_EVAL=1 to authorize model usage; --prepare is credential-free");
  let clientVersion = null;
  if (selected.mode === "run") {
    const version = spawnSync("codex", ["--version"], { env: clientEnvironment(), encoding: "utf8", timeout: 10_000, maxBuffer: 16_384 });
    assert.equal(version.status, 0, "A signed-in Codex CLI is required; no authentication data will be copied");
    clientVersion = version.stdout.trim();
    assert.match(clientVersion, /^codex-cli [A-Za-z0-9.+-]+$/u);
  }
  let out;
  if (selected.out !== null) { out = resolve(selected.out); mkdirSync(out, { mode: 0o700 }); }
  else {
    const parent = join(root, ".codex-cache", "grafana-incident-evaluations");
    mkdirSync(parent, { recursive: true, mode: 0o700 });
    out = mkdtempSync(join(parent, "run-"));
  }
  // Every prompt and every collection uses one frozen package, even if another
  // developer edits the checkout during a long sequence of model turns.
  const frozenRoot = mkdtempSync(join(tmpdir(), "grafana-incident-candidate-"));
  const frozenPlugin = join(frozenRoot, "plugin");
  try {
  cpSync(join(root, "plugins/grafana-observability"), frozenPlugin, { recursive: true, dereference: false });
  const bundle = candidateBundle(frozenPlugin);
  save(join(out, "candidate.json"), bundle);
  save(join(out, "manifest.json"), {
    kind: "SOURCE_SKILL_FROZEN_PACKET_EVALUATION", candidatePackageVersion: bundle.version,
    candidatePackageTreeSha256: bundle.packageTreeSha256, candidateBundleSha256: sha256(jsonBytes(bundle)), clientVersion, mode: selected.mode,
    requestedModel: selected.model ?? "CLIENT_DEFAULT", cases: selected.cases, independentReviewRequired: true,
    limitations: ["No installed-plugin model turn", "No model-driven MCP/tool-access test", "No live provider or production monitoring", "No deployment, restart, publication, scheduler or recovery execution", "Structured checks do not grade prose"],
  });
  console.log(`Evaluation receipts: ${out}`);
  for (const id of selected.cases) {
    const scenario = CASES.find((entry) => entry.id === id);
    const directory = join(out, id);
    mkdirSync(directory, { mode: 0o700 });
    let collection = null;
    if (scenario.fixture !== null) {
      const session = await startSyntheticSession({ scenario: scenario.fixture, pluginRoot: frozenPlugin });
      try { collection = await collectSyntheticIncident(session); }
      finally { await session.close(); }
    }
    const packet = makePacket(scenario, collection);
    const prompt = evaluationPrompt(scenario, packet, bundle);
    assert.equal(candidateBundle(frozenPlugin).packageTreeSha256, bundle.packageTreeSha256, "Frozen candidate changed during collection");
    const preparedIdentity = receiptIdentity({ scenarioId: id, bundle, packet, prompt });
    save(join(directory, "input.json"), packet);
    save(join(directory, "prompt.txt"), prompt);
    save(join(directory, "output-schema.json"), OUTPUT_SCHEMA);
    save(join(directory, "prepared.identity.json"), preparedIdentity);
    save(join(directory, "review.template.json"), reviewTemplate(scenario, preparedIdentity));
    save(join(directory, "prompt.sha256"), `${sha256(prompt)}\n`);
    if (selected.mode === "prepare") { console.log(`${id}: prepared; zero model turns`); continue; }
    const work = mkdtempSync(join(tmpdir(), "grafana-incident-model-"));
    try {
      const result = await modelTurn({ prompt, work, model: selected.model });
      result.execution.clientVersion = clientVersion;
      save(join(directory, "execution.json"), result.execution);
      assert.notEqual(result.final, null, `Model run for ${id} failed or attempted a tool. Inspect bounded execution.json; raw logs and any unsafe final output were not retained.`);
      // Exact model output, never rewritten by the harness. Keep synthetic
      // receipts local until independently reviewed for content and provenance.
      save(join(directory, "final.txt"), result.final);
      const identity = receiptIdentity({ scenarioId: id, bundle, packet, prompt, final: result.final, execution: result.execution });
      save(join(directory, "identity.json"), identity);
      save(join(directory, "review.json"), reviewTemplate(scenario, identity));
      const parsed = JSON.parse(result.final);
      const checks = checkOutput(parsed, packet, result.execution.trace);
      save(join(directory, "consistency.json"), checks);
      console.log(`${id}: ${checks.status}; independent semantic review REQUIRED`);
      assert.equal(checks.status, "CONSISTENT", `Model output for ${id} failed mechanical consistency checks`);
      assert.equal(candidateBundle(frozenPlugin).packageTreeSha256, bundle.packageTreeSha256, "Frozen candidate changed during model evaluation");
    } finally { rmSync(work, { recursive: true, force: true }); }
  }
  } finally { rmSync(frozenRoot, { recursive: true, force: true }); }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
