#!/usr/bin/env node

/**
 * Deterministic, dependency-free deliberation and coverage-gated review ledger
 * for the Ponder plugin. Read-only commands (`inspect`, `coverage`, `verify`)
 * never write files; ledger commands (`init`, `enumerate`, `pass`, `report`)
 * write only inside the explicitly declared `--session-dir`.
 */

import {
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import {
  isAbsolute,
  normalize,
  relative,
  resolve,
  sep,
} from "node:path";
import process from "node:process";

const COMMANDS = new Set([
  "inspect",
  "coverage",
  "verify",
  "init",
  "enumerate",
  "pass",
  "report",
]);

const DEFAULT_VOICES = ["skeptic", "builder", "dreamer", "empath"];
const VALID_LENSES = new Set([
  "code",
  "ui",
  "architecture",
  "tests",
  "docs",
  "copy",
  "product",
]);
const VALID_SEVERITIES = new Set(["must", "should", "could", "note"]);
const VALID_DELTA_OPS = new Set([
  "add",
  "change",
  "retract",
  "keep",
  "unit-add",
]);
const MUTATING_DELTA_OPS = new Set(["add", "change", "retract", "unit-add"]);

class CliError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "CliError";
    this.code = code;
    this.details = details;
  }
}

function fail(code, message, details = {}) {
  throw new CliError(code, message, details);
}

function usage() {
  return `ponder.mjs

Deterministic, dependency-free multi-pass deliberation ledger.
Read-only commands (inspect, coverage, verify) never create or modify files.
Ledger commands (init, enumerate, pass, report) write only inside --session-dir.

Commands:
  inspect --session-dir DIR [--format json|markdown]
  coverage --session-dir DIR [--format json|markdown]
  verify --session-dir DIR [--format json|markdown]
  init --session-dir DIR --goal TEXT [--lens NAME] [--voices CSV] [--min-passes N] [--format json|markdown]
  enumerate --session-dir DIR --units-file FILE [--format json|markdown]
  pass --session-dir DIR --pass N --deliberation-file FILE --deltas-file FILE [--format json|markdown]
  report --session-dir DIR [--output RELATIVE_FILE] [--format json|markdown]
`;
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  if (!command || command === "--help" || command === "-h") {
    return { command: "help", options: new Map() };
  }
  if (!COMMANDS.has(command)) {
    fail("UNKNOWN_COMMAND", `Unknown command: ${command}`, {
      supported: [...COMMANDS],
    });
  }

  const options = new Map();
  for (let index = 0; index < rest.length; index += 1) {
    const argument = rest[index];
    if (argument === "--help" || argument === "-h") {
      addOption(options, "help", "true");
      continue;
    }
    if (!argument.startsWith("--")) {
      fail("UNEXPECTED_ARGUMENT", `Expected an option, received: ${argument}`);
    }
    const withoutPrefix = argument.slice(2);
    const equalsIndex = withoutPrefix.indexOf("=");
    let key;
    let value;
    if (equalsIndex >= 0) {
      key = withoutPrefix.slice(0, equalsIndex);
      value = withoutPrefix.slice(equalsIndex + 1);
    } else {
      key = withoutPrefix;
      if (key === "json" || key === "markdown") {
        value = "true";
      } else {
        const next = rest[index + 1];
        if (!next || next.startsWith("--")) {
          fail("MISSING_OPTION_VALUE", `Option --${key} requires a value`);
        }
        value = next;
        index += 1;
      }
    }
    if (!key || value === "") {
      fail("INVALID_OPTION", `Invalid option: ${argument}`);
    }
    addOption(options, key, value);
  }
  return { command, options };
}

function addOption(options, key, value) {
  const values = options.get(key) ?? [];
  values.push(value);
  options.set(key, values);
}

function assertKnownOptions(options, allowed) {
  for (const key of options.keys()) {
    if (!allowed.has(key)) {
      fail("UNKNOWN_OPTION", `Unknown option: --${key}`);
    }
  }
}

function one(options, key, { required = true } = {}) {
  const values = options.get(key) ?? [];
  if (values.length === 0) {
    if (required) {
      fail("MISSING_REQUIRED_OPTION", `Required option --${key} was not provided`);
    }
    return undefined;
  }
  if (values.length > 1) {
    fail("REPEATED_SINGLE_OPTION", `Option --${key} may only be provided once`);
  }
  return values[0];
}

function outputFormat(options) {
  const explicit = one(options, "format", { required: false });
  const json = options.has("json");
  const markdown = options.has("markdown");
  if (json && markdown) {
    fail("INVALID_FORMAT", "Choose only one of --json or --markdown");
  }
  const format = explicit ?? (markdown ? "markdown" : "json");
  if (!new Set(["json", "markdown"]).has(format)) {
    fail("INVALID_FORMAT", "--format must be json or markdown");
  }
  return format;
}

function slashPath(value) {
  return value.split(sep).join("/");
}

function isInside(root, candidate) {
  const rel = relative(root, candidate);
  return (
    rel === "" ||
    (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel))
  );
}

function assertNoSymlinkAtOrBelow(root, candidate) {
  if (existsSync(root) && lstatSync(root).isSymbolicLink()) {
    fail("SYMLINK_REJECTED", `Symbolic links are not accepted: ${root}`);
  }
  if (!candidate) {
    return;
  }
  const rel = relative(root, candidate);
  if (rel === "" || rel.startsWith(`..${sep}`) || rel === ".." || isAbsolute(rel)) {
    if (existsSync(candidate) && lstatSync(candidate).isSymbolicLink()) {
      fail("SYMLINK_REJECTED", `Symbolic links are not accepted: ${candidate}`);
    }
    return;
  }
  let cursor = root;
  for (const part of rel.split(sep).filter(Boolean)) {
    cursor = resolve(cursor, part);
    if (existsSync(cursor) && lstatSync(cursor).isSymbolicLink()) {
      fail("SYMLINK_REJECTED", `Symbolic links are not accepted: ${cursor}`);
    }
  }
}

function resolveSessionDir(raw, { mustExist = true, createIfMissing = false } = {}) {
  if (!raw || typeof raw !== "string" || !raw.trim()) {
    fail("AMBIGUOUS_SESSION_DIR", "An explicit --session-dir is required");
  }
  const resolved = resolve(raw);
  if (existsSync(resolved) && lstatSync(resolved).isSymbolicLink()) {
    fail("SYMLINK_REJECTED", `Symbolic links are not accepted: ${raw}`);
  }
  if (!existsSync(resolved)) {
    if (mustExist && !createIfMissing) {
      fail("SESSION_DIR_NOT_FOUND", `Session directory does not exist: ${raw}`);
    }
    if (createIfMissing) {
      mkdirSync(resolved, { recursive: true });
    }
  } else {
    const stat = lstatSync(resolved);
    if (stat.isSymbolicLink()) {
      fail("SYMLINK_REJECTED", `Symbolic links are not accepted: ${raw}`);
    }
    if (!stat.isDirectory()) {
      fail("SESSION_DIR_NOT_DIRECTORY", `Expected a directory for --session-dir: ${raw}`);
    }
  }
  return resolved;
}

function validateRelativeSessionFile(sessionDir, raw, label) {
  if (!raw || isAbsolute(raw) || raw.includes("\0") || raw.includes("\\")) {
    fail(
      "PATH_OUTSIDE_SESSION_DIR",
      `${label} must be a relative path inside the explicit session directory`,
      { path: raw },
    );
  }
  const segments = raw.split("/");
  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) {
    fail(
      "PATH_OUTSIDE_SESSION_DIR",
      `${label} must stay inside the explicit session directory without dot or parent segments`,
      { path: raw },
    );
  }
  const normalized = normalize(raw);
  const candidate = resolve(sessionDir, normalized);
  if (!isInside(sessionDir, candidate)) {
    fail(
      "PATH_OUTSIDE_SESSION_DIR",
      `${label} escapes the declared --session-dir`,
      { path: raw },
    );
  }
  assertNoSymlinkAtOrBelow(sessionDir, candidate);
  return { absolute: candidate, relative: slashPath(relative(sessionDir, candidate)) };
}

function readJsonFile(filePath, label) {
  if (existsSync(filePath) && lstatSync(filePath).isSymbolicLink()) {
    fail("SYMLINK_REJECTED", `Symbolic links are not accepted: ${filePath}`);
  }
  if (!existsSync(filePath) || !lstatSync(filePath).isFile()) {
    fail("FILE_NOT_FOUND", `${label} does not exist: ${filePath}`);
  }
  try {
    return JSON.parse(readFileSync(filePath, "utf8"));
  } catch (error) {
    fail("INVALID_JSON", `${label} is not valid JSON: ${error.message}`);
  }
}

function writeSessionJson(sessionDir, relativeName, data) {
  const target = validateRelativeSessionFile(sessionDir, relativeName, relativeName);
  writeFileSync(target.absolute, `${JSON.stringify(data, null, 2)}\n`, "utf8");
  return target.relative;
}

function writeSessionText(sessionDir, relativeName, text) {
  const target = validateRelativeSessionFile(sessionDir, relativeName, relativeName);
  writeFileSync(target.absolute, text, "utf8");
  return target.relative;
}

function validateUnitsArray(units) {
  if (!Array.isArray(units) || units.length === 0) {
    fail("AMBIGUOUS_SCOPE", "units must be a non-empty array of declared scope units");
  }
  const seenIds = new Set();
  for (const unit of units) {
    if (!unit || typeof unit !== "object" || Array.isArray(unit)) {
      fail("INVALID_UNIT", "Each unit must be an object");
    }
    for (const field of ["id", "path", "kind", "label"]) {
      if (typeof unit[field] !== "string" || unit[field].trim() === "") {
        fail("INVALID_UNIT", `Unit is missing non-empty string field '${field}'`);
      }
    }
    if (unit.path.includes("..") || isAbsolute(unit.path)) {
      fail("INVALID_UNIT_PATH", `Unit path must be a relative in-scope path: ${unit.path}`);
    }
    if (seenIds.has(unit.id)) {
      fail("DUPLICATE_UNIT_ID", `Duplicate unit id: ${unit.id}`);
    }
    seenIds.add(unit.id);
  }
  return units;
}

function parseDeltasText(rawText, label) {
  const lines = rawText
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  const deltas = [];
  for (let index = 0; index < lines.length; index += 1) {
    let delta;
    try {
      delta = JSON.parse(lines[index]);
    } catch (error) {
      fail("INVALID_DELTA_JSONL", `${label} line ${index + 1} is not valid JSON: ${error.message}`);
    }
    if (!delta || typeof delta !== "object" || Array.isArray(delta)) {
      fail("INVALID_DELTA", `${label} line ${index + 1} must be a JSON object`);
    }
    if (!VALID_DELTA_OPS.has(delta.op)) {
      fail("INVALID_DELTA_OP", `Unsupported delta op '${delta.op}' on line ${index + 1}`);
    }
    if (typeof delta.perspective !== "string" || delta.perspective.trim() === "") {
      fail("MISSING_DELTA_PERSPECTIVE", `Delta on line ${index + 1} requires a non-empty 'perspective'`);
    }
    if (typeof delta.why !== "string" || delta.why.trim() === "") {
      fail("MISSING_DELTA_WHY", `Delta on line ${index + 1} requires a non-empty 'why'`);
    }
    deltas.push(delta);
  }
  return deltas;
}

function discoverPasses(sessionDir) {
  const entries = readdirSync(sessionDir, { withFileTypes: true });
  const passNumbers = new Set();
  for (const entry of entries) {
    if (entry.isSymbolicLink()) {
      fail("SYMLINK_REJECTED", `Symbolic link inside session directory: ${entry.name}`);
    }
    const delibMatch = entry.name.match(/^deliberation-(\d+)\.json$/u);
    if (delibMatch) {
      passNumbers.add(Number.parseInt(delibMatch[1], 10));
    }
    const deltaMatch = entry.name.match(/^deltas-(\d+)\.jsonl$/u);
    if (deltaMatch) {
      passNumbers.add(Number.parseInt(deltaMatch[1], 10));
    }
  }
  return [...passNumbers].sort((a, b) => a - b);
}

function replaySession(sessionDir) {
  const metaPath = validateRelativeSessionFile(sessionDir, "meta.json", "meta.json").absolute;
  const unitsPath = validateRelativeSessionFile(sessionDir, "units.json", "units.json").absolute;
  const meta = readJsonFile(metaPath, "meta.json");
  const initialUnits = validateUnitsArray(readJsonFile(unitsPath, "units.json"));

  const voices = Array.isArray(meta.voices) && meta.voices.length > 0
    ? meta.voices
    : DEFAULT_VOICES;
  const minPasses = Number.isInteger(meta.minPasses) && meta.minPasses >= 1
    ? meta.minPasses
    : 2;

  const unitsMap = new Map(initialUnits.map((unit) => [unit.id, { ...unit }]));
  const findingsMap = new Map();
  const passNumbers = discoverPasses(sessionDir);
  const passSummaries = [];

  for (let idx = 0; idx < passNumbers.length; idx += 1) {
    const passNumber = passNumbers[idx];
    if (passNumber !== idx + 1) {
      fail("NON_SEQUENTIAL_PASS", `Expected pass ${idx + 1}, found pass ${passNumber}`);
    }
    const delibPath = validateRelativeSessionFile(
      sessionDir,
      `deliberation-${passNumber}.json`,
      `deliberation-${passNumber}.json`,
    ).absolute;
    const deltasPath = validateRelativeSessionFile(
      sessionDir,
      `deltas-${passNumber}.jsonl`,
      `deltas-${passNumber}.jsonl`,
    ).absolute;

    const deliberation = readJsonFile(delibPath, `deliberation-${passNumber}.json`);
    if (!existsSync(deltasPath) || !lstatSync(deltasPath).isFile()) {
      fail("FILE_NOT_FOUND", `deltas-${passNumber}.jsonl does not exist`);
    }
    const deltas = parseDeltasText(
      readFileSync(deltasPath, "utf8"),
      `deltas-${passNumber}.jsonl`,
    );

    const missingVoices = [];
    const activeVoices = [];
    for (const voice of voices) {
      const text = deliberation?.[voice];
      if (typeof text === "string" && text.trim().length > 0) {
        activeVoices.push(voice);
      } else {
        missingVoices.push(voice);
      }
    }

    const opCounts = {
      add: 0,
      change: 0,
      retract: 0,
      keep: 0,
      "unit-add": 0,
    };

    for (const delta of deltas) {
      opCounts[delta.op] += 1;
      if (delta.op === "unit-add") {
        const newUnit = delta.unit;
        validateUnitsArray([newUnit]);
        if (unitsMap.has(newUnit.id)) {
          fail("DUPLICATE_UNIT_ID", `unit-add duplicates existing unit '${newUnit.id}'`);
        }
        unitsMap.set(newUnit.id, { ...newUnit });
        continue;
      }

      if (delta.op === "add") {
        if (typeof delta.id !== "string" || !delta.id.trim()) {
          fail("INVALID_FINDING_ID", `Pass ${passNumber} add delta requires non-empty 'id'`);
        }
        if (findingsMap.has(delta.id)) {
          fail("DUPLICATE_FINDING_ID", `Finding '${delta.id}' already exists`);
        }
        if (!unitsMap.has(delta.unit)) {
          fail("UNKNOWN_UNIT", `Finding '${delta.id}' references unknown unit '${delta.unit}'`);
        }
        if (!VALID_SEVERITIES.has(delta.severity)) {
          fail("INVALID_SEVERITY", `Finding '${delta.id}' has invalid severity '${delta.severity}'`);
        }
        if (typeof delta.confidence !== "number" || delta.confidence < 0 || delta.confidence > 1) {
          fail("INVALID_CONFIDENCE", `Finding '${delta.id}' confidence must be a number in 0..1`);
        }
        for (const field of ["title", "detail", "evidence", "recommendation"]) {
          if (typeof delta[field] !== "string" || !delta[field].trim()) {
            fail("INVALID_FINDING_FIELD", `Finding '${delta.id}' requires non-empty '${field}'`);
          }
        }
        findingsMap.set(delta.id, {
          id: delta.id,
          unit: delta.unit,
          severity: delta.severity,
          confidence: delta.confidence,
          title: delta.title,
          detail: delta.detail,
          evidence: delta.evidence,
          recommendation: delta.recommendation,
          status: "open",
          createdPass: passNumber,
          updatedPass: passNumber,
          lastPerspective: delta.perspective,
          lastWhy: delta.why,
        });
        continue;
      }

      if (typeof delta.id !== "string" || !findingsMap.has(delta.id)) {
        fail("UNKNOWN_FINDING_ID", `Pass ${passNumber} ${delta.op} references unknown finding '${delta.id}'`);
      }
      const existing = findingsMap.get(delta.id);
      if (existing.status === "retracted") {
        fail("RETRACTED_FINDING_MUTATION", `Finding '${delta.id}' was already retracted`);
      }

      if (delta.op === "retract") {
        existing.status = "retracted";
        existing.updatedPass = passNumber;
        existing.lastPerspective = delta.perspective;
        existing.lastWhy = delta.why;
        continue;
      }

      if (delta.op === "keep") {
        existing.updatedPass = passNumber;
        existing.lastPerspective = delta.perspective;
        existing.lastWhy = delta.why;
        continue;
      }

      if (delta.op === "change") {
        if (delta.severity !== undefined) {
          if (!VALID_SEVERITIES.has(delta.severity)) {
            fail("INVALID_SEVERITY", `Invalid severity '${delta.severity}' on finding '${delta.id}'`);
          }
          existing.severity = delta.severity;
        }
        if (delta.confidence !== undefined) {
          if (typeof delta.confidence !== "number" || delta.confidence < 0 || delta.confidence > 1) {
            fail("INVALID_CONFIDENCE", `Invalid confidence on finding '${delta.id}'`);
          }
          existing.confidence = delta.confidence;
        }
        for (const field of ["title", "detail", "evidence", "recommendation"]) {
          if (delta[field] !== undefined) {
            if (typeof delta[field] !== "string" || !delta[field].trim()) {
              fail("INVALID_FINDING_FIELD", `Invalid '${field}' on finding '${delta.id}'`);
            }
            existing[field] = delta[field];
          }
        }
        existing.updatedPass = passNumber;
        existing.lastPerspective = delta.perspective;
        existing.lastWhy = delta.why;
      }
    }

    const mutatingDeltas =
      opCounts.add + opCounts.change + opCounts.retract + opCounts["unit-add"];

    passSummaries.push({
      pass: passNumber,
      activeVoices,
      missingVoices,
      deliberationOk: missingVoices.length === 0,
      deltaCount: deltas.length,
      mutatingDeltas,
      opCounts,
    });
  }

  const allUnits = [...unitsMap.values()];
  const allFindings = [...findingsMap.values()];
  const openFindings = allFindings.filter((finding) => finding.status === "open");

  const coveredUnitIds = new Set(openFindings.map((finding) => finding.unit));
  const uncoveredUnits = allUnits
    .filter((unit) => !coveredUnitIds.has(unit.id))
    .map((unit) => unit.id);
  const coverageRatio = allUnits.length === 0
    ? 0
    : (allUnits.length - uncoveredUnits.length) / allUnits.length;

  const deliberationGateOk =
    passSummaries.length > 0 &&
    passSummaries.every((summary) => summary.deliberationOk);

  const lowConfidenceMust = openFindings
    .filter((finding) => finding.severity === "must" && finding.confidence < 0.5)
    .map((finding) => finding.id);

  const latestPass = passSummaries.at(-1) ?? null;
  const minPassesMet = passSummaries.length >= minPasses;
  const latestPassQuiet = latestPass !== null && latestPass.mutatingDeltas === 0;
  const coverageComplete = uncoveredUnits.length === 0;
  const confidenceOk = lowConfidenceMust.length === 0;

  const blockingReasons = [];
  if (!minPassesMet) {
    blockingReasons.push(`PASS_COUNT_BELOW_MIN (${passSummaries.length}/${minPasses})`);
  }
  if (!coverageComplete) {
    blockingReasons.push(`UNCOVERED_UNITS (${uncoveredUnits.join(", ")})`);
  }
  if (!deliberationGateOk) {
    blockingReasons.push("DELIBERATION_INCOMPLETE");
  }
  if (!latestPassQuiet) {
    blockingReasons.push(
      `NON_QUIET_FINAL_PASS (${latestPass ? latestPass.mutatingDeltas : 0} mutating deltas)`,
    );
  }
  if (!confidenceOk) {
    blockingReasons.push(`LOW_CONFIDENCE_MUST (${lowConfidenceMust.join(", ")})`);
  }

  const canStop = blockingReasons.length === 0;
  let verdict = "converged";
  if (!coverageComplete || !deliberationGateOk || !confidenceOk) {
    verdict = "blocked";
  } else if (!minPassesMet || !latestPassQuiet) {
    verdict = "continue";
  }

  return {
    meta: {
      goal: meta.goal,
      lens: meta.lens ?? "code",
      voices,
      minPasses,
    },
    units: allUnits,
    findings: allFindings,
    openFindings,
    passCount: passSummaries.length,
    passes: passSummaries,
    coverage: {
      totalUnits: allUnits.length,
      coveredUnits: allUnits.length - uncoveredUnits.length,
      uncoveredUnits,
      ratio: Number(coverageRatio.toFixed(4)),
      complete: coverageComplete,
    },
    deliberationGate: {
      ok: deliberationGateOk,
      requiredVoices: voices,
    },
    confidenceGate: {
      ok: confidenceOk,
      lowConfidenceMust,
    },
    latestPass,
    canStop,
    verdict,
    blockingReasons,
  };
}

function renderMarkdownSummary(state, { readOnly = true } = {}) {
  const lines = [
    `# Ponder Session Report: ${state.meta.goal}`,
    "",
    `- **Verdict**: \`${state.verdict}\` (\`canStop: ${state.canStop}\`)`,
    `- **Lens**: \`${state.meta.lens}\``,
    `- **Passes completed**: ${state.passCount} (minimum required: ${state.meta.minPasses})`,
    `- **Unit coverage**: ${state.coverage.coveredUnits}/${state.coverage.totalUnits} (${Math.round(state.coverage.ratio * 100)}%)`,
    `- **Deliberation gate**: ${state.deliberationGate.ok ? "passed" : "failed"} (${state.meta.voices.join(", ")})`,
    `- **Read-only check**: ${readOnly}`,
    "",
    "## Enumerated units",
    "",
    "| Unit ID | Kind | Path | Label |",
    "| --- | --- | --- | --- |",
  ];
  for (const unit of state.units) {
    lines.push(`| \`${unit.id}\` | \`${unit.kind}\` | \`${unit.path}\` | ${unit.label} |`);
  }
  lines.push("", "## Pass convergence", "", "| Pass | Voices | Total Deltas | Mutating Deltas | Keep Deltas |", "| --- | --- | --- | --- | --- |");
  for (const pass of state.passes) {
    lines.push(
      `| ${pass.pass} | ${pass.activeVoices.length}/${state.meta.voices.length} | ${pass.deltaCount} | ${pass.mutatingDeltas} | ${pass.opCounts.keep} |`,
    );
  }
  lines.push("", "## Open findings", "");
  if (state.openFindings.length === 0) {
    lines.push("No open findings.");
  } else {
    for (const finding of state.openFindings) {
      lines.push(
        `### [${finding.severity.toUpperCase()}] \`${finding.id}\`: ${finding.title}`,
        `- **Unit**: \`${finding.unit}\``,
        `- **Confidence**: ${finding.confidence}`,
        `- **Evidence**: \`${finding.evidence}\``,
        `- **Detail**: ${finding.detail}`,
        `- **Recommendation**: ${finding.recommendation}`,
        "",
      );
    }
  }
  return `${lines.join("\n").trimEnd()}\n`;
}

function runInspect(options) {
  assertKnownOptions(options, new Set(["session-dir", "format", "json", "markdown"]));
  const sessionDir = resolveSessionDir(one(options, "session-dir"));
  const state = replaySession(sessionDir);
  return {
    ok: true,
    command: "inspect",
    readOnly: true,
    writesPerformed: false,
    ...state,
  };
}

function runCoverage(options) {
  assertKnownOptions(options, new Set(["session-dir", "format", "json", "markdown"]));
  const sessionDir = resolveSessionDir(one(options, "session-dir"));
  const state = replaySession(sessionDir);
  return {
    ok: state.coverage.complete,
    command: "coverage",
    readOnly: true,
    writesPerformed: false,
    coverage: state.coverage,
    verdict: state.verdict,
  };
}

function runVerify(options) {
  assertKnownOptions(options, new Set(["session-dir", "format", "json", "markdown"]));
  const sessionDir = resolveSessionDir(one(options, "session-dir"));
  const state = replaySession(sessionDir);
  return {
    ok: state.canStop,
    command: "verify",
    readOnly: true,
    writesPerformed: false,
    canStop: state.canStop,
    verdict: state.verdict,
    passCount: state.passCount,
    minPasses: state.meta.minPasses,
    coverage: state.coverage,
    deliberationGate: state.deliberationGate,
    confidenceGate: state.confidenceGate,
    latestPass: state.latestPass,
    blockingReasons: state.blockingReasons,
  };
}

function runInit(options) {
  assertKnownOptions(
    options,
    new Set(["session-dir", "goal", "lens", "voices", "min-passes", "format", "json", "markdown"]),
  );
  const sessionDir = resolveSessionDir(one(options, "session-dir"), {
    mustExist: false,
    createIfMissing: true,
  });
  const goal = one(options, "goal");
  if (!goal.trim()) {
    fail("INVALID_GOAL", "--goal must be a non-empty string");
  }
  const lens = one(options, "lens", { required: false }) ?? "code";
  if (!VALID_LENSES.has(lens)) {
    fail("INVALID_LENS", `Unsupported lens: ${lens}`);
  }
  const voicesRaw = one(options, "voices", { required: false });
  const voices = voicesRaw
    ? voicesRaw.split(",").map((voice) => voice.trim()).filter(Boolean)
    : DEFAULT_VOICES;
  if (voices.length === 0) {
    fail("INVALID_VOICES", "At least one voice is required");
  }
  const minPassesRaw = one(options, "min-passes", { required: false }) ?? "2";
  const minPasses = Number.parseInt(minPassesRaw, 10);
  if (!Number.isInteger(minPasses) || minPasses < 2) {
    fail("INVALID_MIN_PASSES", "--min-passes must be an integer >= 2");
  }

  const meta = {
    goal: goal.trim(),
    lens,
    voices,
    minPasses,
  };
  const written = writeSessionJson(sessionDir, "meta.json", meta);
  return {
    ok: true,
    command: "init",
    readOnly: false,
    writesPerformed: true,
    filesWritten: [written],
    meta,
  };
}

function runEnumerate(options) {
  assertKnownOptions(
    options,
    new Set(["session-dir", "units-file", "format", "json", "markdown"]),
  );
  const sessionDir = resolveSessionDir(one(options, "session-dir"));
  const unitsFile = resolve(one(options, "units-file"));
  const units = validateUnitsArray(readJsonFile(unitsFile, "units-file"));
  const written = writeSessionJson(sessionDir, "units.json", units);
  return {
    ok: true,
    command: "enumerate",
    readOnly: false,
    writesPerformed: true,
    filesWritten: [written],
    unitCount: units.length,
  };
}

function runPass(options) {
  assertKnownOptions(
    options,
    new Set([
      "session-dir",
      "pass",
      "deliberation-file",
      "deltas-file",
      "format",
      "json",
      "markdown",
    ]),
  );
  const sessionDir = resolveSessionDir(one(options, "session-dir"));
  const passNumber = Number.parseInt(one(options, "pass"), 10);
  if (!Number.isInteger(passNumber) || passNumber < 1) {
    fail("INVALID_PASS_NUMBER", "--pass must be a positive integer");
  }

  const deliberationSource = resolve(one(options, "deliberation-file"));
  const deltasSource = resolve(one(options, "deltas-file"));
  const deliberation = readJsonFile(deliberationSource, "deliberation-file");
  if (existsSync(deltasSource) && lstatSync(deltasSource).isSymbolicLink()) {
    fail("SYMLINK_REJECTED", `Symbolic links are not accepted: ${deltasSource}`);
  }
  if (!existsSync(deltasSource) || !lstatSync(deltasSource).isFile()) {
    fail("FILE_NOT_FOUND", `deltas-file does not exist: ${deltasSource}`);
  }
  const deltasText = readFileSync(deltasSource, "utf8");
  parseDeltasText(deltasText, "deltas-file");

  const delibName = `deliberation-${passNumber}.json`;
  const deltasName = `deltas-${passNumber}.jsonl`;
  const writtenDelib = writeSessionJson(sessionDir, delibName, deliberation);
  const writtenDeltas = writeSessionText(
    sessionDir,
    deltasName,
    `${deltasText.trimEnd()}\n`,
  );

  const state = replaySession(sessionDir);
  const passSummary = state.passes.find((item) => item.pass === passNumber);
  if (!passSummary.deliberationOk) {
    fail(
      "DELIBERATION_INCOMPLETE",
      `Pass ${passNumber} deliberation is missing required voices: ${passSummary.missingVoices.join(", ")}`,
      { missingVoices: passSummary.missingVoices },
    );
  }
  const writtenFindings = writeSessionJson(sessionDir, "findings.json", state.findings);

  return {
    ok: true,
    command: "pass",
    readOnly: false,
    writesPerformed: true,
    filesWritten: [writtenDelib, writtenDeltas, writtenFindings],
    pass: passSummary,
    coverage: state.coverage,
    verdict: state.verdict,
    canStop: state.canStop,
  };
}

function runReport(options) {
  assertKnownOptions(
    options,
    new Set(["session-dir", "output", "format", "json", "markdown"]),
  );
  const sessionDir = resolveSessionDir(one(options, "session-dir"));
  const state = replaySession(sessionDir);
  if (!state.canStop) {
    fail(
      "SESSION_NOT_CONVERGED",
      `Cannot emit final report before convergence: ${state.blockingReasons.join("; ")}`,
      { blockingReasons: state.blockingReasons },
    );
  }
  const outputRel = one(options, "output", { required: false }) ?? "report.md";
  const markdown = renderMarkdownSummary(state, { readOnly: false });
  const written = writeSessionText(sessionDir, outputRel, markdown);
  return {
    ok: true,
    command: "report",
    readOnly: false,
    writesPerformed: true,
    filesWritten: [written],
    verdict: state.verdict,
    canStop: state.canStop,
  };
}

function main() {
  try {
    const { command, options } = parseArgs(process.argv.slice(2));
    if (command === "help" || options.has("help")) {
      process.stdout.write(usage());
      return;
    }

    const format = outputFormat(options);
    let result;
    if (command === "inspect") {
      result = runInspect(options);
    } else if (command === "coverage") {
      result = runCoverage(options);
    } else if (command === "verify") {
      result = runVerify(options);
    } else if (command === "init") {
      result = runInit(options);
    } else if (command === "enumerate") {
      result = runEnumerate(options);
    } else if (command === "pass") {
      result = runPass(options);
    } else if (command === "report") {
      result = runReport(options);
    }

    if (format === "markdown" && command === "inspect") {
      process.stdout.write(renderMarkdownSummary(result, { readOnly: true }));
    } else {
      process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    }
    if (result && result.ok === false) {
      process.exitCode = 1;
    }
  } catch (error) {
    if (error instanceof CliError) {
      process.stderr.write(
        `${JSON.stringify({
          ok: false,
          code: error.code,
          message: error.message,
          details: error.details,
        })}\n`,
      );
      process.exitCode = 2;
      return;
    }
    process.stderr.write(
      `${JSON.stringify({
        ok: false,
        code: "INTERNAL_ERROR",
        message: error instanceof Error ? error.message : String(error),
      })}\n`,
    );
    process.exitCode = 1;
  }
}

main();
