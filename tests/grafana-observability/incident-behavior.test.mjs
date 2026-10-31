import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { lstatSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { SOURCE_PLUGIN_ROOT } from "./helpers/mcp-client.mjs";
import { collectSyntheticIncident, startSyntheticSession } from "./helpers/synthetic-session.mjs";
import {
  CASES, CURRENT_PROMPT_REVISION, INCIDENT_SKILLS, candidateBundle, checkOutput, evaluationPrompt, finalizeRecord, makePacket,
  newTrace, recordDiagnostic, recordEvent, receiptIdentity, reviewTemplate, sha256, validateArchivedRecord, validateCandidateBundle, validateRetainedRecord, validateReview,
} from "./support/incident-evaluation.mjs";

function completedTrace() {
  const trace = newTrace();
  recordEvent(trace, JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: "not retained" } }));
  recordEvent(trace, JSON.stringify({ type: "turn.completed", usage: { input_tokens: 10, output_tokens: 20 } }));
  return trace;
}

function structuralAnswer(packet) {
  return {
    artifact_markdown: "Synthetic test answer for checker unit tests; not a model output.",
    report_assessments: packet.toolOutputs.map(({ report }) => ({ report_ref: report.reportRef, origin: report.origin, status: report.status })),
    citations: packet.toolOutputs.flatMap(({ report, evidence }) => evidence.slice(0, 1).map((detail) => ({ report_ref: report.reportRef, evidence_ref: detail.evidenceRef, claim: "Fixture observation" }))),
    cause_status: "hypothesis", recovery_status: "unknown", publication_status: "draft", executed_actions: [],
  };
}

test("incident evaluation packets preserve actual packaged report states, baseline windows and owned evidence", { timeout: 180_000 }, async () => {
  const expected = { complete: "COMPLETE", partial: "PARTIAL", stale: "STALE", empty: "EMPTY", contradictory: "CONTRADICTORY" };
  for (const [fixture, status] of Object.entries(expected)) {
    const scenario = CASES.find((entry) => entry.fixture === fixture);
    const session = await startSyntheticSession({ scenario: fixture });
    let packet;
    try {
      const collection = await collectSyntheticIncident(session);
      packet = makePacket(scenario, collection);
      assert.deepEqual(packet.toolOutputs[0].report, collection.report, "Do not massage real report outputs for expected model answers");
      assert.equal(packet.toolOutputs[0].report.status, status);
      assert.equal(packet.toolOutputs[0].report.origin, "SIMULATED");
      assert.deepEqual(packet.toolOutputs[0].evidence, collection.evidence);
      assert.equal(packet.transport.providerReads, collection.transport.requiredOperationsPerWindow * 2);
      assert.equal(packet.transport.unexpectedRequests, 0);
      const { window, baselineWindow } = collection.request;
      assert.equal(Date.parse(window.end) - Date.parse(window.start), Date.parse(baselineWindow.end) - Date.parse(baselineWindow.start));
      assert.ok(Date.parse(baselineWindow.end) <= Date.parse(window.start));
      const answer = structuralAnswer(packet);
      assert.equal(checkOutput(answer, packet, completedTrace()).status, "CONSISTENT");
      const references = [collection.report.reportRef, collection.evidence[0].evidenceRef, collection.evidence[0].linkRefs[0]].filter(Boolean);
      for (const ref of references) {
        const cited = { ...answer, artifact_markdown: `Supported inline reference: ${ref}.` };
        assert.equal(checkOutput(cited, packet, completedTrace()).status, "CONSISTENT");
        for (const invalidRef of [`${ref}b`, `${ref.slice(0, 18)}...`, `${ref.slice(0, 18)}…`, `${ref.split("_")[0]}_unknown`, ref.toUpperCase()]) {
          const changed = { ...answer, artifact_markdown: `Unsupported inline reference: ${invalidRef}` };
          assert.equal(checkOutput(changed, packet, completedTrace()).status, "FAIL", invalidRef);
        }
      }
      for (const mutation of [
        (value) => { value.report_assessments[0].status = "HEALTHY"; },
        (value) => { value.report_assessments[0].origin = "LIVE"; },
        (value) => { value.citations[0].report_ref = "another-report"; },
        (value) => { value.citations[0].evidence_ref = "another-evidence"; },
        (value) => { value.cause_status = "confirmed"; },
        (value) => { value.recovery_status = "verified"; },
        (value) => { value.executed_actions.push("Restarted checkout"); },
        (value) => { value.publication_status = "sent"; },
      ]) {
        const changed = structuredClone(answer);
        mutation(changed);
        assert.equal(checkOutput(changed, packet, completedTrace()).status, "FAIL");
      }
      assert.equal(JSON.stringify(packet).includes(session.sandbox.root), false, "Do not retain authority paths in evidence packets");
      assert.equal(Object.hasOwn(packet, "launchEnv"), false);
    } finally {
      const receipt = await session.close();
      assert.equal(receipt.temporaryStateRemoved, true);
    }
  }
});

test("incident evaluator binds exact candidate instructions and labels authored context separately", () => {
  const bundle = candidateBundle(SOURCE_PLUGIN_ROOT);
  assert.equal(bundle.packageTreeSha256.length, 64);
  for (const entry of bundle.instructions) assert.equal(sha256(entry.content), entry.sha256, "Prompt content and hash must describe the same bytes");
  for (const name of INCIDENT_SKILLS) assert.ok(bundle.instructions.find((entry) => entry.path === `skills/${name}/SKILL.md`));
  const scenario = CASES.find((entry) => entry.id === "missing-connector");
  const packet = makePacket(scenario, null);
  assert.deepEqual(packet.toolOutputs, []);
  assert.equal(packet.transport, null);
  assert.deepEqual(packet.authoredContext, scenario.notes);
  const prompt = evaluationPrompt(scenario, packet, bundle);
  const supplied = JSON.parse(prompt.split("<candidate_files>\n\n")[1].split("\n\n</candidate_files>")[0]);
  assert.deepEqual(supplied, bundle.instructions, "All skill bytes and referenced pack instructions must remain exact");
  const answer = structuralAnswer(packet);
  assert.equal(checkOutput(answer, packet, completedTrace()).status, "CONSISTENT");
  answer.report_assessments.push({ report_ref: "invented", origin: "LIVE", status: "COMPLETE" });
  assert.equal(checkOutput(answer, packet, completedTrace()).status, "FAIL");
});

test("event collector detects attempted actions and unknown tools without retaining their arguments or results", () => {
  for (const itemType of ["command_execution", "mcp_tool_call", "web_search", "file_change", "future_unknown_tool"]) {
    const trace = completedTrace();
    recordEvent(trace, JSON.stringify({ type: "item.started", item: { type: itemType, arguments: "NONSECRET_TEST_CANARY", output: "NONSECRET_TEST_CANARY" } }));
    assert.equal(trace.toolAttempts.length, 1);
    assert.equal(JSON.stringify(trace).includes("NONSECRET_TEST_CANARY"), false);
    const scenario = CASES.find((entry) => entry.id === "missing-connector");
    const packet = makePacket(scenario, null);
    assert.equal(checkOutput(structuralAnswer(packet), packet, trace).status, "FAIL");
  }
  const invalid = newTrace();
  recordEvent(invalid, "not json");
  assert.equal(invalid.invalidJsonLines, 1);
});

test("diagnostic status codes require explicit context, not incidental request IDs or timestamps", () => {
  for (const line of [
    "request_id=abc429def401ghi completed successfully",
    "request_id=req-429-401 completed successfully",
    "request_id=status429-code401 completed successfully",
    "2026-10-30T12:34:56.429401Z request completed",
    "trace=429401 elapsed=401 duration=429",
  ]) {
    const trace = newTrace();
    recordDiagnostic(trace, line);
    assert.deepEqual(trace.diagnostics, [], line);
  }
  for (const [line, category] of [
    ["HTTP 401", "AUTHENTICATION_UNAVAILABLE"],
    ['{"status_code":401}', "AUTHENTICATION_UNAVAILABLE"],
    ["401 Unauthorized", "AUTHENTICATION_UNAVAILABLE"],
    ["authentication required", "AUTHENTICATION_UNAVAILABLE"],
    ["HTTP/1.1 429", "USAGE_LIMIT"],
    ['{"statusCode":429}', "USAGE_LIMIT"],
    ["code=429", "USAGE_LIMIT"],
    ["429 Too Many Requests", "USAGE_LIMIT"],
    ["quota exceeded", "USAGE_LIMIT"],
    ["rate limit exceeded", "USAGE_LIMIT"],
  ]) {
    const trace = newTrace();
    recordDiagnostic(trace, line);
    assert.deepEqual(trace.diagnostics, [category], line);
  }
});

test("inline references require exact supplied tokens and cannot use valid prefixes or abbreviations", () => {
  // In-memory checker inputs, not retained model evaluation evidence.
  const reportRef = `gfrpt_${"a".repeat(64)}`;
  const evidenceRef = `gfev_${"b".repeat(64)}`;
  const linkRef = `gflnk_${"c".repeat(64)}`;
  const packet = { toolOutputs: [{
    report: { reportRef, origin: "SIMULATED", status: "COMPLETE", evidence: [{ evidenceRef, linkRefs: [linkRef] }] },
    evidence: [{ evidenceRef, linkRefs: [linkRef] }], links: [{ linkRef }],
  }] };
  const answer = structuralAnswer(packet);
  for (const ref of [reportRef, evidenceRef, linkRef]) {
    assert.equal(checkOutput({ ...answer, artifact_markdown: `Reference ${ref}.` }, packet, completedTrace()).status, "CONSISTENT");
    for (const invalid of [`${ref}b`, `${ref.slice(0, 16)}...`, `${ref.slice(0, 16)}…`, ref.toUpperCase(), `${ref.split("_")[0]}_unknown`]) {
      assert.equal(checkOutput({ ...answer, artifact_markdown: `Reference ${invalid}` }, packet, completedTrace()).status, "FAIL", invalid);
    }
  }
});

test("consistent structured output does not count as a semantic pass; reviewer must inspect the artifact", () => {
  const scenario = CASES.find((entry) => entry.id === "missing-connector");
  const packet = makePacket(scenario, null);
  const answer = structuralAnswer(packet);
  answer.artifact_markdown = "This deliberately false unit-test prose says a production deploy fixed every error.";
  const result = checkOutput(answer, packet, completedTrace());
  assert.equal(result.status, "CONSISTENT");
  assert.equal(result.semanticReview, "REQUIRED", "Structural fields do not establish whether prose is correct");
  const review = reviewTemplate(scenario);
  assert.throws(() => validateReview(review));
  const bundle = candidateBundle(SOURCE_PLUGIN_ROOT);
  const prompt = evaluationPrompt(scenario, packet, bundle);
  const identity = receiptIdentity({ scenarioId: scenario.id, bundle, packet, prompt, final: JSON.stringify(answer) });
  review.binding = identity;
  review.status = "REVIEWED";
  review.reviewer = "unit-test-reviewer-not-a-model-evaluation";
  review.independentReview = true;
  for (const entry of [...review.criteria, review.scenarioSpecificCheck]) {
    entry.score = 2;
    entry.rationale = "Unit-test fixture explanation, not evaluation evidence.";
  }
  review.hardFailures = ["Artifact invents production deployment and recovery."];
  review.decision = "PASS";
  assert.throws(() => validateReview(review, identity));
  review.decision = "FAIL";
  assert.equal(validateReview(review, identity), "FAIL");
  assert.throws(() => validateReview(review, { ...identity, finalSha256: "different-output" }));
});

test("direct CLI finalization binds exact bytes and fails closed on critical configuration and client errors", () => {
  const scenario = CASES.find((entry) => entry.id === "missing-connector");
  const bundle = candidateBundle(SOURCE_PLUGIN_ROOT);
  const packet = makePacket(scenario, null);
  const prompt = evaluationPrompt(scenario, packet, bundle);
  const final = `${JSON.stringify(structuralAnswer(packet))}\n`;
  const events = `${JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: final } })}\n${JSON.stringify({ type: "turn.completed", usage: { output_tokens: 30 } })}\n`;
  const args = { scenario, bundle, packet, prompt, final, events, execution: { source: "DIRECT_CODEX_EXEC", code: 0, clientVersion: "codex-cli 0.0.0-test", requestedModel: "TEST_ONLY" } };
  const receipt = finalizeRecord(args);
  assert.equal(receipt.consistency.status, "CONSISTENT");
  assert.equal(receipt.identity.finalSha256, sha256(final));
  assert.deepEqual(receipt.review.binding, receipt.identity);
  assert.equal(receipt.execution.source, "DIRECT_CODEX_EXEC");
  assert.ok(receipt.identity.executionSha256);
  assert.throws(() => finalizeRecord({ ...args, prompt: `${prompt} changed` }));
  const calibrationPrompt = evaluationPrompt(scenario, packet, bundle, { revision: "executed-actions-v1" });
  assert.notEqual(sha256(calibrationPrompt), sha256(prompt));
  assert.throws(() => finalizeRecord({ ...args, prompt: calibrationPrompt }), "Calibration prompts cannot silently become accepted current-revision runs");
  const v2Prompt = evaluationPrompt(scenario, packet, bundle, { revision: "external-actions-v2" });
  assert.notEqual(sha256(v2Prompt), sha256(prompt));
  assert.throws(() => finalizeRecord({ ...args, prompt: v2Prompt }));
  const rejected = finalizeRecord({ ...args, diagnostics: "unknown configuration field `features.plugins` in override" });
  assert.equal(rejected.consistency.status, "FAIL");
  assert.equal(finalizeRecord({ ...args, diagnostics: "Unknown feature in config: shell_tool" }).consistency.status, "FAIL");
  const trace = newTrace();
  recordDiagnostic(trace, "unknown configuration field `features.executed_tool_call_metadata` in host configuration");
  assert.deepEqual(trace.criticalConfigurationErrors, [], "Unrelated host compatibility warnings do not relax required isolation");
  recordEvent(trace, JSON.stringify({ type: "turn.failed", error: { message: "model_not_found NONSECRET_TEST_CANARY" } }));
  assert.deepEqual(trace.failureCategories, ["MODEL_UNAVAILABLE"]);
  assert.equal(JSON.stringify(trace).includes("NONSECRET_TEST_CANARY"), false);
  // These are in-memory checker fixtures, never retained model evidence.
  receipt.review.status = "REVIEWED";
  receipt.review.reviewer = "unit-test-reviewer-not-a-model-evaluation";
  receipt.review.independentReview = true;
  receipt.review.decision = "PASS";
  for (const entry of [...receipt.review.criteria, receipt.review.scenarioSpecificCheck]) {
    entry.score = 2;
    entry.rationale = "Checker fixture only; no real model evaluation claimed.";
  }
  const retained = { scenario, bundle, packet, final, ...receipt };
  assert.deepEqual(validateRetainedRecord(retained), receipt.identity);
  assert.throws(() => validateRetainedRecord({ ...retained, final: `${final} ` }), "Changed output invalidates the exact receipt");
  assert.throws(() => validateRetainedRecord({ ...retained, execution: { ...receipt.execution, code: 1 } }));
  const alteredTrace = structuredClone(retained);
  alteredTrace.execution.trace.toolAttempts.push({ eventType: "item.started", itemType: "command_execution" });
  assert.throws(() => validateRetainedRecord(alteredTrace), "Changed tool history invalidates the review binding");
  const staleReview = structuredClone(retained);
  staleReview.review.binding.finalSha256 = "previous-output";
  assert.throws(() => validateRetainedRecord(staleReview), "A review of previous output cannot accept a new receipt");
  assert.equal(validateCandidateBundle(bundle), bundle.packageTreeSha256);
  const corruptedBundle = structuredClone(bundle);
  corruptedBundle.instructions[0].content += " altered archive";
  assert.throws(() => validateCandidateBundle(corruptedBundle));
  const ungraded = { status: "NOT_GRADED", decision: null, binding: receipt.identity, reason: "In-memory archive checker fixture; not an evaluated model output." };
  const archived = { ...retained, promptRevision: CURRENT_PROMPT_REVISION, review: ungraded };
  assert.deepEqual(validateArchivedRecord(archived), receipt.identity);
  assert.throws(() => validateArchivedRecord({ ...archived, review: { ...ungraded, decision: "PASS" } }), "Ungraded history cannot become an acceptance pass");
  const failedReview = structuredClone(receipt.review);
  failedReview.criteria.at(-1).score = 0;
  failedReview.criteria.at(-1).rationale = "Checker fixture demonstrating a preserved failing historical grade.";
  failedReview.decision = "FAIL";
  assert.deepEqual(validateArchivedRecord({ ...archived, review: failedReview }), receipt.identity);
});

test("retained incident evaluations match the exact current candidate and independent bound reviews", () => {
  // This evidence is part of the release contract. Missing files fail: there is
  // no fallback fixture, implicit skip or fabricated model output.
  const evidenceRoot = fileURLToPath(new URL("./review-evidence/incidents/2026-10-30/", import.meta.url));
  const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
  const manifest = readJson(join(evidenceRoot, "manifest.json"));
  const candidateBytes = readFileSync(join(evidenceRoot, "candidate.json"));
  const bundle = JSON.parse(candidateBytes.toString("utf8"));
  const current = candidateBundle(SOURCE_PLUGIN_ROOT);
  assert.equal(manifest.candidatePackageTreeSha256, current.packageTreeSha256, "Evidence belongs to a different installable candidate");
  assert.equal(manifest.candidateBundleSha256, sha256(candidateBytes));
  assert.equal(manifest.promptRevision, CURRENT_PROMPT_REVISION);
  assert.deepEqual(bundle, current, "Retained candidate bytes and instruction content must match the current package");
  validateCandidateBundle(bundle);
  const candidates = new Map([["candidate.json", { bytes: candidateBytes, bundle }]]);
  const archivedCandidate = (candidateFile = "candidate.json") => {
    assert.match(candidateFile, /^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.json$/u, "Candidate archive must be one safe relative filename");
    if (!candidates.has(candidateFile)) {
      const path = join(evidenceRoot, candidateFile);
      assert.equal(lstatSync(path).isSymbolicLink(), false, "Candidate archive must not be a symlink");
      const bytes = readFileSync(path);
      const archived = JSON.parse(bytes.toString("utf8"));
      validateCandidateBundle(archived);
      candidates.set(candidateFile, { bytes, bundle: archived });
    }
    return candidates.get(candidateFile);
  };
  assert.deepEqual([...manifest.cases].sort(), CASES.map((entry) => entry.id).sort(), "Retain every required scenario exactly once");
  const runs = manifest.cases.map((id) => ({ caseId: id, directory: id }));
  // Optional repetitions retain their own outputs/reviews without replacing a
  // primary result. Directories are a single safe relative path component.
  for (const repetition of manifest.repetitions ?? []) {
    assert.ok(CASES.some((entry) => entry.id === repetition.caseId));
    assert.match(repetition.directory, /^[A-Za-z0-9][A-Za-z0-9_-]{0,100}$/u);
    runs.push(repetition);
  }
  assert.equal(new Set(runs.map((entry) => entry.directory)).size, runs.length);
  for (const run of runs) {
    const directory = join(evidenceRoot, run.directory);
    const scenario = CASES.find((entry) => entry.id === run.caseId);
    const packet = readJson(join(directory, "input.json"));
    const final = readFileSync(join(directory, "final.txt"), "utf8");
    const identity = readJson(join(directory, "identity.json"));
    assert.equal(identity.inputSha256, sha256(readFileSync(join(directory, "input.json"))), "Retain exact serialized input bytes");
    assert.equal(identity.candidateBundleSha256, sha256(candidateBytes));
    validateRetainedRecord({
      scenario, bundle, packet, final, identity,
      execution: readJson(join(directory, "execution.json")),
      consistency: readJson(join(directory, "consistency.json")),
      review: readJson(join(directory, "review.json")),
    });
  }
  const calibrations = manifest.calibrations;
  assert.ok(Array.isArray(calibrations) && calibrations.length > 0, "Disclose the observed prompt-format calibration separately");
  assert.deepEqual(calibrations.map((entry) => `${entry.promptRevision}:${entry.caseId}`).sort(), [
    "executed-actions-v1:complete-baseline",
    "external-actions-v2:complete-baseline", "external-actions-v2:partial-traces",
    "external-actions-v2:stale-evidence", "external-actions-v2:empty-evidence",
  ].sort(), "Retain all five observed calibration turns, including successful prior-format outputs");
  assert.equal(manifest.acceptedModelTurns, runs.length);
  assert.equal(manifest.calibrationModelTurns, calibrations.length);
  const historicalRuns = manifest.historicalRuns;
  assert.ok(Array.isArray(historicalRuns));
  assert.deepEqual(historicalRuns.map((entry) => entry.caseId).sort(), CASES.map((entry) => entry.id).sort(), "Retain all nine runs against the candidate before the retry correction");
  assert.equal(manifest.historicalModelTurns, historicalRuns.length);
  const rejectedRuns = manifest.rejectedRuns ?? [];
  assert.ok(Array.isArray(rejectedRuns));
  assert.equal(manifest.rejectedModelTurns ?? 0, rejectedRuns.length);
  assert.equal(manifest.totalModelTurns, runs.length + calibrations.length + historicalRuns.length + rejectedRuns.length);
  const directories = new Set(runs.map((run) => run.directory));
  const checkArchive = (entry, requiresReview) => {
    assert.equal(entry.excludedFromAcceptance, true);
    assert.ok(typeof entry.reason === "string" && entry.reason.trim().length > 0);
    assert.match(entry.directory, /^[A-Za-z0-9][A-Za-z0-9_-]{0,100}$/u);
    assert.equal(directories.has(entry.directory), false);
    directories.add(entry.directory);
    const scenario = CASES.find((candidate) => candidate.id === entry.caseId);
    assert.ok(scenario);
    const archived = archivedCandidate(entry.candidateFile);
    const directory = join(evidenceRoot, entry.directory);
    const packet = readJson(join(directory, "input.json"));
    const final = readFileSync(join(directory, "final.txt"), "utf8");
    const identity = readJson(join(directory, "identity.json"));
    const execution = readJson(join(directory, "execution.json"));
    assert.equal(identity.inputSha256, sha256(readFileSync(join(directory, "input.json"))));
    assert.equal(identity.candidateBundleSha256, sha256(archived.bytes));
    const review = requiresReview ? readJson(join(directory, "review.json")) : undefined;
    validateArchivedRecord({
      scenario, bundle: archived.bundle, packet, final, identity, execution, review,
      promptRevision: entry.promptRevision, consistency: readJson(join(directory, "consistency.json")),
    });
    return { bundle: archived.bundle, review };
  };
  for (const calibration of calibrations) {
    assert.ok(["executed-actions-v1", "external-actions-v2"].includes(calibration.promptRevision));
    checkArchive(calibration, false);
  }
  for (const historical of historicalRuns) {
    assert.equal(historical.promptRevision, CURRENT_PROMPT_REVISION);
    const archived = checkArchive(historical, true);
    assert.notEqual(archived.bundle.packageTreeSha256, current.packageTreeSha256, "Before-fix history cannot use the corrected candidate");
    if (historical.caseId === "partial-traces") {
      assert.equal(archived.review.status, "REVIEWED");
      assert.equal(archived.review.decision, "FAIL", "Preserve the discovered retry-advice failure");
      assert.ok(archived.review.criteria.find((entry) => entry.criterion.startsWith("Usefulness:")).score < 2);
    }
  }
  for (const rejected of rejectedRuns) {
    assert.equal(rejected.promptRevision, CURRENT_PROMPT_REVISION);
    const archived = checkArchive(rejected, true);
    assert.equal(archived.review.status, "REVIEWED", "Rejected runs require an independently completed review");
    assert.equal(archived.review.decision, "FAIL", "Rejected output cannot be relabeled as a calibration or acceptance pass");
  }
});

test("actual source-skill model evaluations are an explicit opt-in with independently reviewed receipts", {
  skip: process.env.GRAFANA_E2E_SKILL_EVAL !== "1" && "Set GRAFANA_E2E_SKILL_EVAL=1 to run actual source-skill model evaluations; default skips model usage, not a behavioral pass",
  timeout: 2_700_000,
}, async (t) => {
  const script = fileURLToPath(new URL("../../scripts/evaluate-grafana-incidents.mjs", import.meta.url));
  const args = [script, "--run"];
  if (process.env.GRAFANA_EVAL_OUTPUT) args.push("--out", process.env.GRAFANA_EVAL_OUTPUT);
  if (process.env.GRAFANA_EVAL_MODEL) args.push("--model", process.env.GRAFANA_EVAL_MODEL);
  const child = spawn(process.execPath, args, { stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => { output = `${output}${chunk}`.slice(-16_384); });
  }
  t.after(() => { if (child.exitCode === null) child.kill("SIGTERM"); });
  const code = await new Promise((accept, reject) => { child.once("error", reject); child.once("close", accept); });
  t.diagnostic(output);
  assert.equal(code, 0, "Model execution/consistency failed; authentication or tool availability is not a silent pass");
  t.diagnostic("Model outputs require independent semantic review. This automated check proves execution and consistency only.");
});
