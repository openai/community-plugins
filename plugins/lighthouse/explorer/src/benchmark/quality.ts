import { BenchmarkFiles, child, number, object, pathFromIndex, string } from "./files.ts";
import type { JudgeDecision, JudgeReview, QualityItem, QualityQueue, QualityStatus } from "./types.ts";

const dimensions = ["readability", "maintainability", "scope", "tests"];
const sha256 = /^[a-f0-9]{64}$/;
const trialId = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

function qualityStatus(value: unknown): QualityStatus | null {
  return value === "pass" || value === "fail" || value === "review_required" || value === "unmeasured" ? value : null;
}

function judged(value: unknown): Record<string, JudgeDecision> {
  const source = object(value);
  const result: Record<string, JudgeDecision> = {};
  for (const [id, raw] of Object.entries(source ?? {})) {
    if (!dimensions.includes(id)) continue;
    const entry = object(raw);
    if (entry) result[id] = { score: number(entry.score) ?? (entry.decision === "pass" ? 1 : entry.decision === "fail" ? 0 : null), reason: string(entry.reason) };
  }
  return result;
}

function judge(value: unknown): JudgeReview | null {
  const entry = object(value);
  return entry ? { model: string(entry.model), effort: string(entry.effort), error: string(entry.error), decisions: judged(entry.decisions) } : null;
}

function readDimensions(value: unknown): Record<string, string> {
  const source = object(value);
  const result: Record<string, string> = {};
  for (const id of dimensions) {
    const status = qualityStatus(source?.[id]);
    if (status) result[id] = status;
  }
  return result;
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => canonical(item));
  const entry = object(value);
  if (entry) return Object.fromEntries(Object.keys(entry).sort().map((key) => [key, canonical(entry[key])]));
  return value;
}

function nativeInfo(value: Record<string, unknown> | null): { taskId: string | null; model: string | null; effort: string | null } {
  const config = child(value, "config");
  const agent = child(config, "agent");
  return {
    taskId: string(value?.task_name)?.split("/").at(-1) ?? null,
    model: string(agent?.model_name),
    effort: string(child(agent, "kwargs")?.reasoning_effort),
  };
}

function emptyItem(id: string, index: string, info = nativeInfo(null)): QualityItem {
  return { trialId: id, ...info, status: "unmeasured", qualityPass: null, qualityScore: null, dimensions: {}, primary: null, reviewer: null, adjudication: null, evidencePath: null, sourceIndexPath: index };
}

function readReview(raw: Record<string, unknown> | null, id: string): { status: QualityStatus; pass: number | null; score: number | null; dimensions: Record<string, string>; primary: JudgeReview | null; reviewer: JudgeReview | null; adjudication: QualityItem["adjudication"]; hash: string; stages: string; adjudicationKeysValid: boolean } | null {
  const status = qualityStatus(raw?.status);
  const hash = string(raw?.packet_sha256);
  if (raw?.policy !== "code-quality-v1" || raw.source_trial_id !== id || !status || !hash || !sha256.test(hash)) return null;
  const graded = readDimensions(raw.dimensions);
  const primary = judge(raw.primary);
  const reviewer = judge(raw.reviewer);
  const rawAdjudication = object(raw.adjudication);
  const adjudication = rawAdjudication ? { reviewer: string(rawAdjudication.reviewer), decisions: judged(rawAdjudication.decisions) } : null;
  const adjudicationKeysValid = !rawAdjudication || Object.keys(object(rawAdjudication.decisions) ?? {}).every((key) => dimensions.includes(key));
  const pass = number(raw.quality_pass);
  const score = number(raw.quality_score);
  const originalDimensions = object(raw.dimensions);
  const values = Object.values(graded);
  if (values.length !== 4 || Object.keys(originalDimensions ?? {}).length !== 4) return null;
  const calculated = values.includes("fail") ? "fail" : values.includes("review_required") ? "review_required" : values.includes("unmeasured") ? "unmeasured" : "pass";
  const expectedGate = calculated === "pass" ? 1 : calculated === "fail" ? 0 : null;
  const expectedScore = values.every((value) => value === "pass" || value === "fail") ? values.filter((value) => value === "pass").length / values.length : null;
  if (calculated !== status || raw.quality_pass !== expectedGate || raw.quality_score !== expectedScore) return null;
  return { status, pass, score, dimensions: graded, primary, reviewer, adjudication, hash, stages: JSON.stringify(canonical([raw.primary, raw.reviewer])), adjudicationKeysValid };
}

function sameStages(left: ReturnType<typeof readReview>, right: ReturnType<typeof readReview>): boolean {
  return Boolean(left && right && left.hash === right.hash && left.stages === right.stages);
}

function validAdjudication(original: NonNullable<ReturnType<typeof readReview>>, reviewed: NonNullable<ReturnType<typeof readReview>>): boolean {
  if (!sameStages(original, reviewed) || !reviewed.adjudication?.reviewer || !reviewed.adjudicationKeysValid) return false;
  const decisions = Object.entries(reviewed.adjudication.decisions);
  if (decisions.length === 0) return false;
  const expected = { ...original.dimensions };
  for (const [id, decision] of decisions) {
    if (expected[id] !== "review_required" || (decision.score !== 1 && decision.score !== 0) || !decision.reason) return false;
    expected[id] = decision.score === 1 ? "pass" : "fail";
  }
  return dimensions.every((id) => expected[id] === reviewed.dimensions[id]);
}

async function indexed(files: BenchmarkFiles, index: string, id: string, raw: unknown, notices: string[]): Promise<QualityItem> {
  const entry = object(raw);
  const regrade = string(entry?.regrade);
  const dir = regrade ? pathFromIndex(index, regrade) : null;
  if (!dir || !/^(?:regrades|jobs)\//.test(dir)) {
    notices.push(`Quality review ${id} has no allowed native regrade path.`);
    return emptyItem(id, index);
  }
  const native = await files.json(`${dir}/result.json`);
  const info = nativeInfo(native);
  const source = child(child(native, "config"), "source_trial");
  if (!native || source?.action !== "regrade" || source.trial_id !== id) {
    notices.push(`Quality review ${id} has no native regrade linked to that source trial.`);
    return emptyItem(id, index, info);
  }
  const originalPath = `${dir}/verifier/quality-review.json`;
  const original = readReview(await files.json(originalPath), id);
  if (!original) {
    notices.push(`Quality review ${id} has no valid machine-readable quality artifact.`);
    return emptyItem(id, index, info);
  }
  let review = original;
  let evidencePath = originalPath;
  const reference = string(entry?.adjudicated);
  if (reference) {
    const adjudicated = pathFromIndex(index, reference);
    const validPath = adjudicated && /^(?:review|regrades)\/.+\/quality-review\.json$/.test(adjudicated);
    const resolved = validPath ? readReview(await files.json(adjudicated), id) : null;
    if (!resolved || !adjudicated || !validAdjudication(original, resolved)) notices.push(`The human adjudication for ${id} could not be verified; the native model outcome is retained.`);
    else {
      review = resolved;
      evidencePath = adjudicated;
    }
  }
  return { trialId: id, ...info, status: review.status, qualityPass: review.pass, qualityScore: review.score, dimensions: review.dimensions, primary: review.primary, reviewer: review.reviewer, adjudication: review.adjudication, evidencePath, sourceIndexPath: index };
}

export async function qualityQueue(files: BenchmarkFiles, sources: string[]): Promise<QualityQueue> {
  const indexes = [...new Set(sources)];
  const items = new Map<string, QualityItem>();
  const notices: string[] = [];
  for (const index of indexes) {
    let entries: Record<string, unknown> | null;
    try {
      entries = await files.json(index);
    } catch (error) {
      notices.push(error instanceof Error ? error.message : `Could not open ${index}.`);
      continue;
    }
    if (!entries) {
      notices.push(`The quality index ${index} is not available in this checkout.`);
      continue;
    }
    for (const [id, reference] of Object.entries(entries)) {
      if (!trialId.test(id)) {
        notices.push(`The quality index ${index} contains an invalid source trial id.`);
        continue;
      }
      if (items.has(id)) {
        notices.push(`More than one configured quality index names ${id}; its quality outcome is withheld until one index is selected.`);
        items.set(id, emptyItem(id, index));
        continue;
      }
      try {
        items.set(id, await indexed(files, index, id, reference, notices));
      } catch (error) {
        notices.push(error instanceof Error ? error.message : `Quality review ${id} could not be read.`);
        items.set(id, emptyItem(id, index));
      }
    }
  }
  if (indexes.length === 0) notices.push("No code-quality review index is configured. Missing quality evidence is unmeasured and cannot yield a benchmark pass.");
  const counts: QualityQueue["counts"] = { pass: 0, fail: 0, review_required: 0, unmeasured: 0 };
  for (const item of items.values()) counts[item.status] += 1;
  return { sourcePaths: indexes, items: [...items.values()], counts, notices };
}
