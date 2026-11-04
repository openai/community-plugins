import { BenchmarkFiles, child, number, string } from "./files.ts";
import type { CohortDefinition } from "./manifest.ts";
import { qualityQueue } from "./quality.ts";
import type { LeaderboardCohort, LeaderboardMetrics, LeaderboardRow, Outcome, QualityItem, QualityStatus } from "./types.ts";

function errorName(trial: Record<string, unknown>): string | null {
  return string(child(trial, "exception_info")?.exception_type) ?? (trial.exception_info ? "Harbor reported an exception" : null);
}

function functionalOutcome(trial: Record<string, unknown>, key: string): Outcome {
  if (trial.exception_info || !trial.finished_at) return "unavailable";
  const rewards = child(child(trial, "verifier_result"), "rewards");
  const value = rewards?.[key];
  if (value === 1) return "pass";
  if (value === 0) return "fail";
  return "unavailable";
}

function combinedOutcome(functional: Outcome, quality: QualityStatus): Outcome {
  if (functional === "unavailable") return "unavailable";
  if (functional === "fail" || quality === "fail") return "fail";
  return quality;
}

function native(value: Record<string, unknown> | null): value is Record<string, unknown> {
  return Boolean(value && string(value.id) && string(value.task_name) && child(value, "config"));
}

async function pathsInDirectory(files: BenchmarkFiles, directory: string): Promise<string[]> {
  const direct = `${directory}/result.json`;
  if (native(await files.json(direct))) return [direct];
  const children = await files.directories(directory);
  const found: string[] = [];
  for (const entry of children) {
    const candidate = `${directory}/${entry}/result.json`;
    if (native(await files.json(candidate))) found.push(candidate);
  }
  return found;
}

async function resultPaths(files: BenchmarkFiles, patterns: string[], notices: string[]): Promise<string[]> {
  const found: string[] = [];
  for (const input of patterns) {
    try {
      if (input.endsWith("/result.json")) {
        if (native(await files.json(input))) found.push(input);
        else notices.push(`No native Harbor trial result was available at ${input}.`);
        continue;
      }
      const star = input.indexOf("*");
      if (star === -1) {
        const paths = await pathsInDirectory(files, input);
        if (paths.length === 0) notices.push(`No native Harbor trial result was available in ${input}.`);
        found.push(...paths);
        continue;
      }
      const split = input.lastIndexOf("/");
      const base = input.slice(0, split);
      const name = input.slice(split + 1);
      const [prefix = "", suffix = ""] = name.split("*");
      const children = (await files.directories(base)).filter((entry) => entry.startsWith(prefix) && entry.endsWith(suffix));
      if (children.length === 0) notices.push(`No native Harbor directories match ${input}.`);
      for (const entry of children) {
        try {
          found.push(...await pathsInDirectory(files, `${base}/${entry}`));
        } catch (error) {
          notices.push(error instanceof Error ? error.message : `Could not read ${base}/${entry}.`);
        }
      }
    } catch (error) {
      notices.push(error instanceof Error ? error.message : `Could not read ${input}.`);
    }
  }
  return [...new Set(found)];
}

function readTrial(trial: Record<string, unknown>, relative: string, definition: CohortDefinition, quality: Map<string, QualityItem>): LeaderboardRow | null {
  const nativeId = string(trial.id);
  const config = child(trial, "config");
  const source = child(config, "source_trial");
  const originalId = string(source?.trial_id) ?? nativeId;
  const taskId = string(trial.task_name)?.split("/").at(-1);
  if (!originalId || !taskId) return null;
  const selectedQuality = quality.get(originalId);
  const agent = child(config, "agent");
  const functional = functionalOutcome(trial, definition.functionalKey);
  const qualityStatus = selectedQuality?.status ?? "unmeasured";
  const rewards = child(child(trial, "verifier_result"), "rewards");
  return {
    trialId: originalId,
    taskId,
    model: string(agent?.model_name),
    effort: string(child(agent, "kwargs")?.reasoning_effort),
    functional,
    quality: qualityStatus,
    combined: combinedOutcome(functional, qualityStatus),
    functionalReward: number(rewards?.reward),
    qualityScore: selectedQuality?.qualityScore ?? null,
    error: errorName(trial),
    evidencePath: relative,
    qualityEvidencePath: selectedQuality?.evidencePath ?? null,
  };
}

function metrics(rows: LeaderboardRow[]): LeaderboardMetrics {
  const count = (test: (row: LeaderboardRow) => boolean) => rows.filter(test).length;
  const functionalMeasured = count((row) => row.functional === "pass" || row.functional === "fail");
  const qualityPass = count((row) => row.quality === "pass");
  const qualityFail = count((row) => row.quality === "fail");
  return {
    functional: { passed: count((row) => row.functional === "pass"), denominator: functionalMeasured },
    quality: { passed: qualityPass, denominator: qualityPass + qualityFail },
    combined: { passed: count((row) => row.combined === "pass"), denominator: functionalMeasured },
    counts: {
      total: rows.length,
      functionalUnavailable: rows.length - functionalMeasured,
      qualityPass,
      qualityFail,
      qualityReviewRequired: count((row) => row.quality === "review_required"),
      qualityUnmeasured: count((row) => row.quality === "unmeasured"),
      combinedFail: count((row) => row.combined === "fail"),
      combinedReviewRequired: count((row) => row.combined === "review_required"),
      combinedUnmeasured: count((row) => row.combined === "unmeasured"),
    },
  };
}

export async function cohort(files: BenchmarkFiles, definition: CohortDefinition, defaultIndexes: string[], notices: string[]): Promise<LeaderboardCohort> {
  const queue = await qualityQueue(files, definition.qualityIndexes.length > 0 ? definition.qualityIndexes : defaultIndexes);
  if (definition.qualityIndexes.length || defaultIndexes.length) notices.push(...queue.notices);
  const qualities = new Map(queue.items.map((item) => [item.trialId, item]));
  const paths = await resultPaths(files, definition.results, notices);
  const rows = new Map<string, LeaderboardRow>();
  for (const relative of paths) {
    const trial = await files.json(relative);
    if (!trial) continue;
    const row = readTrial(trial, relative, definition, qualities);
    if (!row) continue;
    if (rows.has(row.trialId)) {
      notices.push(`Cohort ${definition.id} selects trial ${row.trialId} more than once; only its first selected result is counted.`);
      continue;
    }
    rows.set(row.trialId, row);
  }
  const values = [...rows.values()];
  return { id: definition.id, label: definition.label, benchmarkRevision: definition.revision, sourcePath: "benchmark.lighthouse", mode: "complete-rubric", metrics: metrics(values), rows: values };
}
