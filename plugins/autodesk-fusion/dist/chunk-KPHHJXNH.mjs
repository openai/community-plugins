import {
  FixtureDesktopProvider,
  FusionError,
  NativeFusionClient,
  assertJson,
  boundedJson,
  cloneNativeJson,
  createRuntime,
  defaultStateRoot,
  ensurePrivateDirectory,
  errorResult,
  external_exports,
  fixtureProfile,
  getOperation,
  hash,
  hashBytes,
  loadProfile,
  newId,
  now,
  parseOperation,
  parseProfile,
  profileHash,
  readTrustedFile,
  recoverDeadLease,
  redact,
  schemaFingerprint,
  validateEnrollment
} from "./chunk-LNNNQITG.mjs";

// src/cli.ts
import { constants } from "node:fs";
import { cp, lstat, open, readFile, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";

// src/qualification.ts
import os from "node:os";
var assertionSchema = external_exports.discriminatedUnion("kind", [
  external_exports.strictObject({ kind: external_exports.literal("equals"), pointer: external_exports.string().max(1024), expected: external_exports.unknown() }),
  external_exports.strictObject({ kind: external_exports.literal("exists"), pointer: external_exports.string().max(1024) }),
  external_exports.strictObject({ kind: external_exports.literal("approx"), pointer: external_exports.string().max(1024), expected: external_exports.number().finite(), absolute_tolerance: external_exports.number().nonnegative().max(1e3), relative_tolerance: external_exports.number().min(0).max(0.01).default(0) }),
  external_exports.strictObject({ kind: external_exports.literal("length_at_least"), pointer: external_exports.string().max(1024), expected: external_exports.number().int().nonnegative().max(1e4) })
]);
var stepSchema = external_exports.strictObject({ id: external_exports.string().regex(/^[a-z][a-z0-9_-]{0,63}$/), action: external_exports.enum(["read", "change", "artifact", "wait_job"]), input: external_exports.unknown(), assertions: external_exports.array(assertionSchema).min(1).max(100), max_polls: external_exports.number().int().min(1).max(120).default(30), poll_interval_ms: external_exports.number().int().min(20).max(1e3).default(500) });
var scenarioSchema = external_exports.strictObject({ version: external_exports.literal(1), id: external_exports.string().regex(/^[a-z][a-z0-9_-]{0,63}$/), purpose: external_exports.string().min(1).max(4096), steps: external_exports.array(stepSchema).min(1).max(100), cleanup: external_exports.array(stepSchema).max(30).default([]) });
var MAX_REFERENCE_SELECTION_ITEMS = 1e4;
var selectionPointer = external_exports.string().max(1024).refine((pointer) => pointer === "" || pointer.startsWith("/") && pointer.slice(1).split("/").every((part) => !/~(?![01])/u.test(part) && !["__proto__", "prototype", "constructor"].includes(part.replace(/~1/g, "/").replace(/~0/g, "~"))), "Use a bounded JSON pointer without reserved keys.");
var referenceSchema = external_exports.strictObject({
  $ref: external_exports.string().min(2).max(1089),
  select: external_exports.strictObject({ pointer: selectionPointer, equals: external_exports.string().min(1).max(1024), value_pointer: selectionPointer }).optional()
});
function jsonPointer(value, pointer) {
  if (pointer === "") return value;
  if (!pointer.startsWith("/") || pointer.length > 1024) throw new FusionError("INVALID_POINTER", "Use a bounded JSON pointer.");
  let current = value;
  for (const part of pointer.slice(1).split("/")) {
    if (/~(?![01])/u.test(part)) throw new FusionError("INVALID_POINTER", "Invalid JSON pointer escape.");
    const key = part.replace(/~1/g, "/").replace(/~0/g, "~");
    if (["__proto__", "prototype", "constructor"].includes(key) || !current || typeof current !== "object" || !Object.hasOwn(current, key)) throw new FusionError("ASSERTION_PATH_MISSING", "The qualification result does not contain the requested field.");
    current = current[key];
  }
  return current;
}
function resolve(value, results) {
  if (Array.isArray(value)) return value.map((item) => resolve(item, results));
  if (value && typeof value === "object") {
    const object = value;
    if (Object.hasOwn(object, "$ref")) {
      const parsed = referenceSchema.safeParse(object);
      if (!parsed.success) throw new FusionError("INVALID_REFERENCE", "A scenario reference permits only $ref and an optional strict string-identity selection.");
      const reference = parsed.data;
      const split = reference.$ref.indexOf("#");
      const step = reference.$ref.slice(0, split), pointer = reference.$ref.slice(split + 1);
      if (split < 1 || !results.has(step)) throw new FusionError("INVALID_REFERENCE", "Scenario reference must name an already completed step.");
      const referenced = jsonPointer(results.get(step), pointer);
      if (!reference.select) return referenced;
      if (!Array.isArray(referenced) || referenced.length > MAX_REFERENCE_SELECTION_ITEMS) throw new FusionError("INVALID_REFERENCE", "Scenario identity selection requires an array of at most 10000 items.");
      let selected, matches = 0;
      for (const item of referenced) {
        let identity;
        try {
          identity = jsonPointer(item, reference.select.pointer);
        } catch {
          throw new FusionError("INVALID_REFERENCE", "A selected array item has no valid identity at the declared pointer.");
        }
        if (typeof identity !== "string" || !identity.length || identity.length > 1024) throw new FusionError("INVALID_REFERENCE", "Every selected array identity must be a bounded nonempty string.");
        if (identity === reference.select.equals) {
          selected = item;
          if (++matches > 1) throw new FusionError("INVALID_REFERENCE", "Scenario identity selection is ambiguous; exactly one match is required.");
        }
      }
      if (matches !== 1) throw new FusionError("INVALID_REFERENCE", "Scenario identity selection has no match; exactly one match is required.");
      try {
        return jsonPointer(selected, reference.select.value_pointer);
      } catch {
        throw new FusionError("INVALID_REFERENCE", "The uniquely selected item does not contain the declared value pointer.");
      }
    }
    return Object.fromEntries(Object.entries(object).map(([key, item]) => [key, resolve(item, results)]));
  }
  return value;
}
function checkQualificationAssertions(value, assertions) {
  for (const assertion of assertions) {
    const actual = jsonPointer(value, assertion.pointer);
    if (assertion.kind === "exists") continue;
    if (assertion.kind === "equals" && hash(actual) === hash(assertion.expected)) continue;
    if (assertion.kind === "length_at_least" && (Array.isArray(actual) || typeof actual === "string") && actual.length >= assertion.expected) continue;
    if (assertion.kind === "approx" && typeof actual === "number" && Number.isFinite(actual) && Math.abs(actual - assertion.expected) <= assertion.absolute_tolerance + Math.abs(assertion.expected) * assertion.relative_tolerance) continue;
    throw new FusionError("QUALIFICATION_ASSERTION_FAILED", `A declared ${assertion.kind} check failed at ${assertion.pointer}.`, "none", { actual, expected: "expected" in assertion ? assertion.expected : null });
  }
}
async function runQualification(runtime, raw) {
  assertJson(raw, 2097152);
  const parsed = scenarioSchema.safeParse(raw);
  if (!parsed.success) throw new FusionError("INVALID_SCENARIO", "Qualification scenario does not match the declarative schema.", "none", parsed.error.issues);
  const scenario = parsed.data;
  const ids = [...scenario.steps, ...scenario.cleanup].map((s) => s.id);
  if (new Set(ids).size !== ids.length) throw new FusionError("INVALID_SCENARIO", "Scenario step IDs must be unique.");
  const runId = newId("qualification");
  const results = /* @__PURE__ */ new Map(), evidence = [];
  const resultSizes = /* @__PURE__ */ new Map();
  const createdDocuments = /* @__PURE__ */ new Set(), remainingDocuments = /* @__PURE__ */ new Set();
  const failedCleanupStepIds = [];
  const artifactIds = /* @__PURE__ */ new Set(), jobIds = /* @__PURE__ */ new Set();
  const operations = [];
  const jobOperations = /* @__PURE__ */ new Map(), jobStates = /* @__PURE__ */ new Map();
  let connection;
  try {
    connection = await runtime.engine.connectionStatus();
  } catch (error) {
    connection = { desktop_evidence: { kind: "unverified_provider", error: errorResult(error), handler_execution_reported: false } };
  }
  const providerEvidence = connection.desktop_evidence;
  const fixture = runtime.profile.mode === "fixture" || runtime.engine.desktop instanceof FixtureDesktopProvider || providerEvidence?.kind === "synthetic_fixture";
  let inlineBytes = 0, retainedBytes = 0;
  let failed = false;
  const object = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : void 0;
  const remember = (stepId, value) => {
    const bytes = Buffer.byteLength(boundedJson(value, 16777216));
    const retained = retainedBytes - (resultSizes.get(stepId) ?? 0) + bytes;
    if (retained > 16777216) throw new FusionError("QUALIFICATION_EVIDENCE_LIMIT", "Scenario results exceed the bounded reference cache. Use smaller scenarios; retained operation receipts still require review.");
    retainedBytes = retained;
    resultSizes.set(stepId, bytes);
    results.set(stepId, value);
  };
  const checkScope = async (documentId) => {
    if (!fixture && documentId && !runtime.profile.policy.qualificationDocuments.includes(documentId) && !(runtime.profile.policy.allowCreatedDocuments && createdDocuments.has(documentId) && await runtime.engine.isCreatedDocument(documentId))) throw new FusionError("QUALIFICATION_SCOPE_DENIED", "Use an explicitly allowlisted qualification document or a document created/imported by this run. Previously created work and production documents are not implicit test fixtures.");
  };
  const capture = async (record, result) => {
    if (result !== void 0) {
      const serialized = boundedJson(result, 16777216), size = Buffer.byteLength(serialized);
      const receiptId = `${runId}_${record.id}`;
      await runtime.engine.store.put("qualification_result", receiptId, result);
      record.result_receipt = { id: receiptId, kind: "qualification_result", sha256: hash(result), size_bytes: size };
      if (size <= 65536 && inlineBytes + size <= 1e6) {
        record.result = result;
        inlineBytes += size;
      } else record.result_omitted = "Full result is retained in the private result receipt; it is omitted here to bound the report.";
    }
    evidence.push(record);
  };
  const run = async (step, cleanup) => {
    const started = now();
    let result, operationEvidence, completionEvidence;
    try {
      const input = cloneNativeJson(resolve(step.input, results), 2097152);
      assertJson(input, 2097152);
      if (step.action === "read") {
        const operation = parseOperation(input);
        await checkScope(operation.document_id);
        if (["cam.status", "render.status"].includes(operation.operation) && !jobIds.has(operation.args.job_id)) throw new FusionError("QUALIFICATION_SCOPE_DENIED", "Qualification may inspect only futures submitted by this run.");
        result = await runtime.engine.read(operation);
        operationEvidence = { step_id: step.id, operation: operation.operation, execution_status: "succeeded", assertions_passed: false };
        operations.push(operationEvidence);
      } else if (step.action === "change") {
        const operation = parseOperation(input);
        const definition = getOperation(operation.operation);
        if (!["local_edit", "local_artifact"].includes(definition.effect) || operation.operation === "cam.nc_post") throw new FusionError("QUALIFICATION_EFFECT_DENIED", "This local kernel harness does not save to cloud, post NC, submit compute or administer systems. Those need their own qualified fixtures and reviewer gates.");
        if (operation.operation === "documents.open") throw new FusionError("QUALIFICATION_SCOPE_DENIED", "Opening existing cloud work is not an isolated qualification fixture. Open and explicitly scope a reviewed fixture before this local harness runs.");
        if (cleanup && !definition.document) throw new FusionError("QUALIFICATION_CLEANUP_DENIED", "Cleanup must not create, import or open another document. Use explicitly scoped compensating changes or leave a manual cleanup record.");
        await checkScope(operation.document_id);
        if (operation.operation === "documents.import" && object(operation.args.source)?.kind === "artifact" && !artifactIds.has(object(operation.args.source).id)) throw new FusionError("QUALIFICATION_SCOPE_DENIED", "Round-trip qualification must import an artifact produced by this run.");
        const plan = await runtime.engine.prepare(operation);
        if (plan.artifact) artifactIds.add(plan.artifact.id);
        result = await runtime.engine.execute(plan.id, plan.hash, `${runId}:${step.id}`);
        const completed = object(result), payload = object(completed.result), data = object(payload?.data);
        const job = object(payload?.job);
        if (typeof job?.id === "string") {
          jobIds.add(job.id);
          jobStates.set(job.id, String(job.status));
        }
        operationEvidence = { step_id: step.id, operation: operation.operation, execution_status: String(completed.status), assertions_passed: false };
        operations.push(operationEvidence);
        if (typeof job?.id === "string") jobOperations.set(job.id, operationEvidence);
        if (completed.status === "succeeded" && ["documents.create", "documents.import"].includes(operation.operation)) {
          const id = operation.operation === "documents.import" ? object(data?.document)?.document_id : data?.document_id;
          if (typeof id === "string") {
            createdDocuments.add(id);
            remainingDocuments.add(id);
          }
        }
        if (completed.status === "succeeded" && operation.operation === "documents.close") remainingDocuments.delete(operation.document_id);
      } else if (step.action === "artifact") {
        const args = external_exports.strictObject({ artifact_id: external_exports.string().min(1) }).parse(input);
        if (!artifactIds.has(args.artifact_id)) throw new FusionError("QUALIFICATION_SCOPE_DENIED", "Qualification may inspect only artifacts prepared by this run.");
        result = await runtime.engine.artifacts.inspect(args.artifact_id);
      } else {
        const args = external_exports.strictObject({ job_id: external_exports.string().min(1) }).parse(input);
        if (!jobIds.has(args.job_id)) throw new FusionError("QUALIFICATION_SCOPE_DENIED", "Qualification may poll only jobs submitted by this run.");
        for (let poll = 0; poll < step.max_polls; poll++) {
          const job = await runtime.engine.jobStatus(args.job_id);
          result = job;
          jobStates.set(job.id, job.status);
          if (["succeeded", "failed", "cancelled", "outcome_unknown"].includes(job.status)) break;
          if (poll + 1 < step.max_polls) await new Promise((resolve2) => setTimeout(resolve2, step.poll_interval_ms));
        }
        if (!["succeeded", "failed", "cancelled", "outcome_unknown"].includes(String(object(result)?.status))) throw new FusionError("QUALIFICATION_JOB_TIMEOUT", "The bounded poll window ended before a terminal result. A pending future cannot pass completion qualification.");
        completionEvidence = jobOperations.get(args.job_id);
        if (completionEvidence) {
          completionEvidence.execution_status = String(object(result)?.status);
          completionEvidence.completion_assertions_passed = false;
        }
      }
      remember(step.id, result);
      checkQualificationAssertions(result, step.assertions);
      if (operationEvidence) operationEvidence.assertions_passed = true;
      if (completionEvidence) completionEvidence.completion_assertions_passed = true;
      if (cleanup && (step.action === "change" && ["failed", "cancelled", "outcome_unknown"].includes(operationEvidence?.execution_status ?? "") || step.action === "wait_job" && object(result)?.status !== "succeeded")) throw new FusionError("QUALIFICATION_CLEANUP_FAILED", "The cleanup operation did not complete successfully; human reconciliation is required.");
      await capture({ id: step.id, action: step.action, cleanup, started_at: started, finished_at: now(), status: "passed", assertions: step.assertions }, result);
    } catch (error) {
      failed = true;
      if (cleanup) failedCleanupStepIds.push(step.id);
      if (result !== void 0 && !results.has(step.id)) {
        try {
          remember(step.id, result);
        } catch {
        }
      }
      try {
        await capture({ id: step.id, action: step.action, cleanup, started_at: started, finished_at: now(), status: "failed", error: errorResult(error) }, result);
      } catch (recordError) {
        evidence.push({ id: step.id, action: step.action, cleanup, status: "failed", error: errorResult(error), receipt_error: errorResult(recordError) });
      }
      if (!cleanup) throw error;
    }
  };
  try {
    for (const step of scenario.steps) await run(step, false);
  } catch {
  } finally {
    for (const step of scenario.cleanup) await run(step, true);
  }
  const successfulOperations = operations.filter((entry) => entry.execution_status === "succeeded" && entry.assertions_passed && entry.completion_assertions_passed !== false);
  const unresolvedJobs = [...jobStates].filter(([, status]) => !["succeeded", "failed", "cancelled"].includes(status)).map(([job_id, status]) => ({ job_id, status }));
  const report = {
    id: runId,
    scenario_id: scenario.id,
    scenario_hash: hash(scenario),
    purpose: scenario.purpose,
    tested_at: now(),
    profile_id: runtime.profile.id,
    profile_sha256: profileHash(runtime.profile),
    mode: runtime.profile.mode,
    handler_sha256: runtime.engine.handlerHash,
    execution_contract_sha256: runtime.engine.executionContractHash,
    execution_contract_kind: runtime.engine.executionContractKind,
    runtime_environment: { platform: process.platform, arch: process.arch, os_release: os.release() },
    status: failed ? "failed" : "scenario_passed",
    evidence_kind: fixture ? "synthetic_fixture" : providerEvidence?.kind ?? "unverified_provider",
    provider_evidence: providerEvidence ?? { kind: "unverified_provider" },
    desktop_qualification_candidate: connection.desktop_qualification_candidate ?? null,
    desktop_qualification_candidate_observed_at: connection.desktop_qualification_candidate_observed_at ?? null,
    desktop_qualification_candidate_requires: "Reviewed evidence, reviewer identity and expiry are supplied only by the trusted profile owner after scoped live qualification. This report does not promote the profile.",
    live_fusion_exercised: !fixture && providerEvidence?.handler_execution_reported === true && operations.some((entry) => entry.execution_status === "succeeded"),
    live_fusion_verified: false,
    live_fusion_qualified: false,
    operations_exercised: operations,
    successful_operations: successfulOperations,
    evidence,
    cleanup: { created_document_ids: [...createdDocuments], remaining_document_ids: [...remainingDocuments], unresolved_jobs: unresolvedJobs, failed_step_ids: failedCleanupStepIds, automatic_discard_or_save: false, review_required: remainingDocuments.size > 0 || unresolvedJobs.length > 0 || failedCleanupStepIds.length > 0 },
    remaining_gates: [
      "A responsible engineer must review the declared assertions, fixture provenance, supported OS/build and measured results. Provider-reported execution is not independent Autodesk verification.",
      "A passing negative test does not show that an operation succeeded. Pending asynchronous work is not complete.",
      "A passing scenario does not qualify other operation variants, materials, manufacturing profiles or cloud tenants.",
      "Failed cleanup steps, unsaved documents and unresolved jobs require explicit human reconciliation; this harness never discards work or silently saves to cloud.",
      "No automatic profile promotion or unsupported machine verification is performed."
    ]
  };
  report.report_hash = hash(report);
  await runtime.engine.store.put("qualification", runId, report);
  return report;
}

// src/cli.ts
var MAX_PROFILE_BYTES = 1048576;
var MAX_FIXED_ARGUMENT_BYTES = 65536;
var profileChanged = () => new FusionError("PROFILE_CHANGED", "The trusted profile changed during native discovery. Review the current file before enrolling again.");
var sameRevision = (a, b) => a.dev === b.dev && a.ino === b.ino && a.size === b.size && a.mtimeNs === b.mtimeNs && a.ctimeNs === b.ctimeNs && a.mode === b.mode && a.uid === b.uid && a.gid === b.gid && a.nlink === b.nlink;
async function fixedArgumentsFile(filename) {
  const { bytes } = await readTrustedFile(filename, MAX_FIXED_ARGUMENT_BYTES);
  let value;
  try {
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new FusionError("INVALID_INPUT", "Fixed arguments must contain valid bounded UTF-8 JSON.");
  }
  assertJson(value, MAX_FIXED_ARGUMENT_BYTES);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new FusionError("INVALID_INPUT", "Fixed arguments must be an explicit JSON object.");
  return value;
}
async function enrollmentSnapshot(filename, profile) {
  const before = await lstat(filename, { bigint: true });
  const file = await readTrustedFile(filename, MAX_PROFILE_BYTES);
  const revision = await lstat(file.canonicalPath, { bigint: true });
  if (!sameRevision(before, revision)) throw profileChanged();
  try {
    if (profileHash(parseProfile(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(file.bytes)))) !== profileHash(profile)) throw profileChanged();
  } catch {
    throw profileChanged();
  }
  return { ...file, revision };
}
async function saveEnrollment(filename, snapshot, profile) {
  const bytes = Buffer.from(JSON.stringify(profile, null, 2) + "\n");
  if (bytes.length > MAX_PROFILE_BYTES) throw new FusionError("INPUT_LIMIT", "The enrolled profile exceeds the trusted profile byte limit.");
  const current = await readTrustedFile(filename, MAX_PROFILE_BYTES);
  if (current.canonicalPath !== snapshot.canonicalPath || !current.bytes.equals(snapshot.bytes)) throw profileChanged();
  const handle = await open(snapshot.canonicalPath, constants.O_RDWR | (constants.O_NOFOLLOW ?? 0));
  let writeAttempted = false;
  try {
    const opened = await handle.stat({ bigint: true });
    if (!opened.isFile() || opened.nlink !== 1n || !sameRevision(opened, snapshot.revision)) throw profileChanged();
    const observed = Buffer.alloc(snapshot.bytes.length + 1);
    let length = 0;
    while (length < observed.length) {
      const result = await handle.read(observed, length, observed.length - length, length);
      if (!result.bytesRead) break;
      length += result.bytesRead;
    }
    const after = await handle.stat({ bigint: true }), linked = await lstat(snapshot.canonicalPath, { bigint: true });
    if (length !== snapshot.bytes.length || !observed.subarray(0, length).equals(snapshot.bytes) || !sameRevision(opened, after) || !sameRevision(opened, linked) || await realpath(filename) !== snapshot.canonicalPath) throw profileChanged();
    writeAttempted = true;
    await handle.writeFile(bytes);
    await handle.truncate(bytes.length);
    await handle.sync();
    const verified = await readTrustedFile(filename, MAX_PROFILE_BYTES);
    const final = await lstat(snapshot.canonicalPath, { bigint: true });
    if (verified.canonicalPath !== snapshot.canonicalPath || !verified.bytes.equals(bytes) || final.dev !== opened.dev || final.ino !== opened.ino) throw profileChanged();
  } catch (error) {
    if (writeAttempted) throw new FusionError("PROFILE_UPDATE_UNCONFIRMED", "The enrollment profile write could not be confirmed. Inspect the current file before another enrollment.", "unknown");
    throw error;
  } finally {
    await handle.close();
  }
}
var help = `Autodesk Fusion interoperability CLI

  fusionctl status [--profile /absolute/profile.json]
  fusionctl capabilities [--family parameters] [--schema]
  fusionctl read --request /absolute/request.json
  fusionctl prepare --request /absolute/request.json
  fusionctl execute --plan PLAN --hash SHA256 --key UNIQUE_KEY
  fusionctl plan --plan PLAN
  fusionctl artifact --artifact ARTIFACT
  fusionctl profile-init --output /absolute/profile.json --mode managed|assisted|fixture
  fusionctl native-discover --url http://127.0.0.1:27182/mcp
  fusionctl native-enroll --profile /absolute/profile.json --tool NAME --argument FIELD --hash TOOL_SCHEMA_SHA256
    [--url http://127.0.0.1:27182/mcp] [--fixed-arguments-file /absolute/reviewed-values.json]
  fusionctl qualify [--live] [--scenario /absolute/scenario.json] [--profile /absolute/profile.json]
  fusionctl install-addin --output /absolute/new/CodexFusionInterop
  fusionctl auth-login|auth-status|auth-logout --profile /absolute/cloud-profile.json
  fusionctl cloud-read|cloud-prepare --request /absolute/request.json
  fusionctl cloud-submit --job JOB --hash SHA256 --key UNIQUE_KEY
  fusionctl cloud-job|cloud-cancel|cloud-validate|cloud-settle --job JOB
  fusionctl recover-lock --profile /absolute/profile.json

No command starts Fusion, installs packages at runtime, grants broad writes, merges a PR,
or submits paid compute implicitly. Profile mutation grants must be explicitly scoped
and expire. Review the setup skill for live qualification and enterprise host controls.

Native enrollment requires a managed or assisted native profile. Fixed arguments are
an optional trusted UTF-8 JSON object (64 KiB maximum), explicitly reviewed and never inferred.
They cannot override the script argument. The selected --url is saved with the mapping;
profiles changed during discovery are rejected. Enrollment does not qualify Fusion or
execute a tool. Full argument-schema validation remains required at each dispatch.
`;
async function runCli(argv = process.argv.slice(2)) {
  const { values, positionals } = parseArgs({ args: argv, allowPositionals: true, strict: true, options: {
    help: { type: "boolean", short: "h" },
    profile: { type: "string" },
    family: { type: "string" },
    schema: { type: "boolean" },
    request: { type: "string" },
    plan: { type: "string" },
    hash: { type: "string" },
    key: { type: "string" },
    artifact: { type: "string" },
    output: { type: "string" },
    mode: { type: "string" },
    url: { type: "string" },
    tool: { type: "string" },
    argument: { type: "string" },
    "fixed-arguments-file": { type: "string" },
    live: { type: "boolean" },
    scenario: { type: "string" },
    job: { type: "string" }
  } });
  const command = positionals[0] ?? "help";
  if (values.help || command === "help") {
    process.stdout.write(help);
    return 0;
  }
  if (values["fixed-arguments-file"] !== void 0 && command !== "native-enroll") throw new FusionError("INVALID_INPUT", "--fixed-arguments-file is only supported by native-enroll.");
  const emit = (value) => process.stdout.write(JSON.stringify(redact(value), null, 2) + "\n");
  const required = (name) => {
    const value = values[name];
    if (typeof value !== "string" || !value) throw new FusionError("CLI_ARGUMENT_REQUIRED", `--${name} is required.`);
    return value;
  };
  if (command === "profile-init") {
    const filename = required("output");
    if (!path.isAbsolute(filename)) throw new FusionError("UNSAFE_PATH", "Profile output must be absolute.");
    const mode = values.mode ?? "managed";
    const id = `fusion-${mode}`;
    const stateRoot = path.join(defaultStateRoot(), id);
    const profile = mode === "fixture" ? fixtureProfile(stateRoot) : parseProfile({ version: 1, id, mode, stateRoot, desktop: { url: values.url ?? "http://127.0.0.1:27182/mcp" }, policy: { mutationsEnabled: false }, outputs: [{ id: "artifacts", path: path.join(stateRoot, "artifacts") }] });
    await ensurePrivateDirectory(path.dirname(filename));
    await writeFile(filename, JSON.stringify(profile, null, 2) + "\n", { flag: "wx", mode: 384 });
    emit({
      created: filename,
      mode,
      mutations_enabled: profile.policy.mutationsEnabled,
      next: mode === "fixture" ? "Synthetic fixture only. Run fixture qualification; this profile grants no Autodesk access." : "Review the profile, discover and enroll the exact native script tool, then run read-only qualification."
    });
    return 0;
  }
  if (command === "native-discover" || command === "native-enroll") {
    const filename = command === "native-enroll" ? required("profile") : values.profile;
    const profile = filename ? await loadProfile(filename) : void 0;
    if (profile && (profile.mode === "fixture" || profile.desktop?.provider !== "native")) throw new FusionError("NATIVE_PROFILE_REQUIRED", "Native discovery and enrollment require a managed or assisted native desktop profile; fixture and add-in profiles are not converted.");
    const url = values.url ?? profile?.desktop?.url;
    if (!url) throw new FusionError("CLI_ARGUMENT_REQUIRED", "--url or a desktop profile is required.");
    const mapping = command === "native-enroll" ? {
      tool: required("tool"),
      argument: required("argument"),
      schemaHash: required("hash"),
      ...values["fixed-arguments-file"] !== void 0 ? { fixedArguments: await fixedArgumentsFile(required("fixed-arguments-file")) } : {}
    } : void 0;
    const snapshot = mapping && filename && profile ? await enrollmentSnapshot(filename, profile) : void 0;
    const native = new NativeFusionClient({ url, ...mapping ? { mapping } : {}, timeoutMs: 15e3 });
    try {
      const connection = await native.connect();
      const tools = await native.listTools();
      if (command === "native-discover") emit({ connection, tools: tools.map((tool) => ({ ...tool, schema_sha256: schemaFingerprint(tool) })), next: "Select the actual Python execution tool and its string argument. Review every additional required argument and provide its fixed value through --fixed-arguments-file. Enroll the displayed schema hash only after review; no tool name or value is guessed." });
      else {
        if (!profile || !filename || !profile.desktop || !mapping || !snapshot) throw new FusionError("CLI_ARGUMENT_REQUIRED", "Enrollment requires an existing native desktop profile.");
        validateEnrollment(mapping, tools);
        const updated = parseProfile({ ...profile, desktop: { ...profile.desktop, url, mapping } });
        assertJson(updated, MAX_PROFILE_BYTES);
        const confirmation = redact({ enrolled: mapping, profile: filename, endpoint: url, live_qualified: false, next: "Run read-only qualification. Enrollment binds a schema and explicit fixed values; it does not prove the server is Autodesk or that a workflow works." });
        assertJson(confirmation, MAX_PROFILE_BYTES);
        const confirmationJson = JSON.stringify(confirmation, null, 2) + "\n";
        if (Buffer.byteLength(confirmationJson) > MAX_PROFILE_BYTES) throw new FusionError("INPUT_LIMIT", "The enrollment confirmation exceeds its bounded output limit.");
        await saveEnrollment(filename, snapshot, updated);
        process.stdout.write(confirmationJson);
      }
    } finally {
      await native.close();
    }
    return 0;
  }
  if (command === "recover-lock") {
    const profile = await loadProfile(required("profile"));
    await recoverDeadLease(profile.stateRoot);
    emit({ lock_removed: true, warning: "An interrupted operation may still have committed. Inspect its durable plan; execution intent is never replayed." });
    return 0;
  }
  const runtime = await createRuntime(values.profile);
  try {
    const requestFile = async () => {
      const filename = required("request");
      const stat = await lstat(filename);
      if (!stat.isFile() || stat.size > 2097152) throw new FusionError("INPUT_LIMIT", "Request must be a bounded JSON file.");
      return JSON.parse(await readFile(filename, "utf8"));
    };
    const cloud = () => {
      if (!runtime.cloud) throw new FusionError("CLOUD_NOT_CONFIGURED", "An explicit scoped cloud profile is required.");
      return runtime.cloud;
    };
    const pkce = () => {
      const client = cloud().oauth;
      if (!client) throw new FusionError("EXTERNAL_AUTHORIZATION_OWNER", "This profile uses enterprise-managed credentials. Sign in or revoke through that authorization owner; the plugin does not create an unrelated public-client grant.");
      return client;
    };
    if (command === "install-addin") {
      const destination = required("output");
      if (!path.isAbsolute(destination)) throw new FusionError("UNSAFE_PATH", "Choose an explicit absolute new add-in directory.");
      await cp(path.join(runtime.root, "dist", "addin", "CodexFusionInterop"), destination, { recursive: true, errorOnExist: true, force: false });
      emit({ installed: destination, started: false, next: "In Fusion Scripts and Add-Ins, add the installed CodexFusionInterop directory and run it. Review its displayed pairing path, then configure desktop.provider=addin; there is no silent fallback or autostart." });
    } else if (command === "auth-login") {
      const authorization = await pkce().beginAuthorization();
      emit({ authorization_url: authorization.authorizationUrl, expires_in: "5 minutes", action: "Open this Autodesk authorization URL in your browser and complete sign-in. No browser is opened automatically." });
      try {
        emit({ signed_in: await authorization.completion });
      } finally {
        authorization.cancel();
      }
    } else if (command === "auth-status") emit(await cloud().status());
    else if (command === "auth-logout") emit(await pkce().revoke());
    else if (command === "cloud-read") {
      const request = await requestFile();
      emit(await cloud().read(request.operation, request.args));
    } else if (command === "cloud-prepare") {
      const request = await requestFile();
      emit(await cloud().prepareJob(request.recipe_id, request.inputs, request.context));
    } else if (command === "cloud-submit") {
      const result = await cloud().submitJob(required("job"), required("hash"), required("key"));
      emit(result);
      return ["failed", "outcome_unknown"].includes(result.status) ? 2 : 0;
    } else if (command === "cloud-job") emit(await cloud().jobStatus(required("job")));
    else if (command === "cloud-cancel") emit(await cloud().cancelJob(required("job")));
    else if (command === "cloud-validate") emit(await cloud().validateJob(required("job")));
    else if (command === "cloud-settle") emit(await cloud().settleJob(required("job")));
    else if (command === "status") emit(await runtime.engine.connectionStatus());
    else if (command === "capabilities") emit(runtime.engine.capabilities(values.family, values.schema ?? false));
    else if (command === "read" || command === "prepare") {
      const request = await requestFile();
      emit(command === "read" ? await runtime.engine.read(request) : await runtime.engine.prepare(request));
    } else if (command === "execute") {
      const result = await runtime.engine.execute(required("plan"), required("hash"), required("key"));
      emit(result);
      return result.status === "succeeded" ? 0 : 2;
    } else if (command === "plan") emit(await runtime.engine.inspectPlan(required("plan")));
    else if (command === "artifact") emit(await runtime.engine.artifacts.inspect(required("artifact"), true));
    else if (command === "qualify") {
      if (values.scenario) {
        const stat = await lstat(values.scenario);
        if (!stat.isFile() || stat.size > 2097152) throw new FusionError("INPUT_LIMIT", "Scenario must be a bounded JSON file.");
        if (values.live && runtime.profile.mode === "fixture") throw new FusionError("LIVE_PROVIDER_REQUIRED", "A fixture scenario cannot count as live Fusion qualification.");
        const report2 = await runQualification(runtime, JSON.parse(await readFile(values.scenario, "utf8")));
        emit(report2);
        return report2.status === "scenario_passed" ? 0 : 2;
      }
      const report = { tested_at: now(), profile: runtime.profile.id, mode: runtime.profile.mode, package_handler_sha256: runtime.engine.handlerHash, tests: [] };
      const tests = report.tests;
      try {
        const documents = await runtime.engine.read({ operation: "documents.list", args: { limit: 100 } });
        tests.push({ name: "desktop_read_roundtrip", status: "passed", evidence: documents });
      } catch (error) {
        tests.push({ name: "desktop_read_roundtrip", status: "blocked", error: errorResult(error) });
      }
      const isLive = values.live && runtime.profile.mode !== "fixture";
      report.live_fusion_attempted = isLive;
      report.live_fusion_qualified = false;
      report.required_external_evidence = ["Licensed Fusion desktop on each supported OS/build.", "Synthetic engineering model edit/measure/export/reopen checks in the real kernel.", "Entitled CAM strategy, post and machine review by a manufacturing engineer.", "Scoped APS hub/OAuth/compute checks using account-authorized test data."];
      report.status = values.live ? "incomplete" : "diagnostic_only";
      const reportId = `qualification_${hashBytes(JSON.stringify(report)).slice(0, 24)}`;
      await runtime.engine.store.put("qualification", reportId, report);
      emit({ id: reportId, ...report });
      return values.live ? 2 : tests.some((t) => t.status === "blocked") ? 2 : 0;
    } else throw new FusionError("UNKNOWN_COMMAND", `Unknown command ${command}. Run fusionctl --help.`);
    return 0;
  } finally {
    await runtime.close();
  }
}
async function cliMain() {
  try {
    process.exitCode = await runCli();
  } catch (error) {
    process.stderr.write(JSON.stringify({ error: errorResult(error) }) + "\n");
    process.exitCode = 1;
  }
}

export {
  jsonPointer,
  checkQualificationAssertions,
  runQualification,
  runCli,
  cliMain
};
