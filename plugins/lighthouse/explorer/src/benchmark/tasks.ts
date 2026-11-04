import { parse } from "smol-toml";

import { BenchmarkFiles, child, object, string, strings, validTaskId } from "./files.ts";
import type { AuthoringArtifact, BenchmarkDocument, Qualification, TaskAuthoring, TaskDetail, TaskSummary } from "./types.ts";

export async function document(files: BenchmarkFiles, relative: string | null): Promise<BenchmarkDocument | null> {
  if (relative === null) return null;
  const content = await files.read(relative);
  return content === null ? null : { path: relative, content };
}

async function artifact(files: BenchmarkFiles, relative: string): Promise<AuthoringArtifact> {
  return { path: relative, status: await files.locate(relative) ? "present" : "missing" };
}

function existing(item: AuthoringArtifact): string | null {
  return item.status === "missing" ? null : item.path;
}

async function qualification(files: BenchmarkFiles, id: string, notices: string[]): Promise<Qualification> {
  const receiptPath = `review/${id}/promotion.json`;
  const content = await files.read(receiptPath);
  if (content === null) return "unknown";
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    notices.push(`Invalid JSON qualification receipt: ${receiptPath}`);
    return "unknown";
  }
  const receipt = object(parsed);
  if (typeof receipt?.qualified === "boolean") return receipt.qualified ? "qualified" : "not_qualified";
  if (receipt && string(receipt.promoted_at) && string(receipt.review) && receipt.destination === `tasks/${id}`) return "qualified";
  return "unknown";
}

async function reviewStarted(files: BenchmarkFiles, id: string): Promise<boolean> {
  const candidates = ["requirements.md", "qualification.md", "promotion.json"];
  const artifacts = await Promise.all(candidates.map((name) => artifact(files, `review/${id}/${name}`)));
  return artifacts.some((item) => item.status === "present");
}

function taskHome(location: TaskAuthoring["location"], id: string): string {
  return location === "published" ? `tasks/${id}` : `.local/drafts/${id}`;
}

async function summary(files: BenchmarkFiles, id: string, location: TaskAuthoring["location"], notices: string[]): Promise<TaskSummary> {
  const home = taskHome(location, id);
  const [instruction, rubric, qualificationDocument, metadata, verifier, state] = await Promise.all([
    artifact(files, `${home}/instruction.md`),
    artifact(files, `review/${id}/requirements.md`),
    artifact(files, `review/${id}/qualification.md`),
    artifact(files, `${home}/task.toml`),
    artifact(files, `${home}/tests/test.sh`),
    qualification(files, id, notices),
  ]);
  const content = metadata.status === "present" ? await files.read(metadata.path) : null;
  let parsed: unknown;
  if (content !== null) {
    try {
      if (!content.trim()) throw new Error("Empty metadata");
      parsed = parse(content);
    } catch {
      metadata.status = "invalid";
      notices.push(`Invalid TOML task metadata: ${metadata.path}`);
    }
  }
  const root = object(parsed);
  const task = child(root, "task");
  const meta = child(root, "metadata");
  const description = string(task?.description) ?? string(root?.description);
  const name = string(task?.name) ?? string(root?.name) ?? id;
  return {
    id,
    name,
    title: description ?? name,
    description,
    version: string(task?.version) ?? string(root?.version),
    difficulty: string(meta?.difficulty) ?? string(task?.difficulty),
    category: string(meta?.category) ?? string(task?.category),
    tags: [...new Set([...strings(task?.keywords), ...strings(task?.tags), ...strings(meta?.tags)])],
    qualification: state,
    authoring: { location, artifacts: { instruction, rubric, qualification: qualificationDocument, metadata, verifier } },
    paths: { instruction: existing(instruction), rubric: existing(rubric), qualification: existing(qualificationDocument), metadata: existing(metadata), verifier: existing(verifier) },
  };
}

async function locations(files: BenchmarkFiles): Promise<{ published: Set<string>; drafts: Set<string>; reviews: Set<string> }> {
  const [published, drafts, reviews] = await Promise.all([
    files.directories("tasks"), files.directories(".local/drafts"), files.directories("review"),
  ]);
  return { published: new Set(published.filter(validTaskId)), drafts: new Set(drafts.filter(validTaskId)), reviews: new Set(reviews.filter(validTaskId)) };
}

async function locationFor(files: BenchmarkFiles, id: string, candidates: Awaited<ReturnType<typeof locations>>): Promise<TaskAuthoring["location"] | null> {
  if (candidates.published.has(id)) return "published";
  if (candidates.drafts.has(id)) return "draft";
  if (candidates.reviews.has(id) && await reviewStarted(files, id)) return "review_only";
  return null;
}

export async function tasks(files: BenchmarkFiles, notices: string[]): Promise<TaskSummary[]> {
  const candidates = await locations(files);
  const ids = [...new Set([...candidates.published, ...candidates.drafts, ...candidates.reviews])].sort();
  const outcomes = await Promise.allSettled(ids.map(async (id) => {
    const location = await locationFor(files, id, candidates);
    return location ? summary(files, id, location, notices) : null;
  }));
  const found: TaskSummary[] = [];
  for (const outcome of outcomes) {
    if (outcome.status === "fulfilled") {
      if (outcome.value) found.push(outcome.value);
    } else {
      notices.push(outcome.reason instanceof Error ? outcome.reason.message : "A task could not be read.");
    }
  }
  return found;
}

export async function task(files: BenchmarkFiles, id: string): Promise<TaskDetail | null> {
  if (!validTaskId(id)) throw new Error("Invalid benchmark task id.");
  const location = await locationFor(files, id, await locations(files));
  if (!location) return null;
  const item = await summary(files, id, location, []);
  const [instruction, rubric, qualificationDocument, metadata, verifier] = await Promise.all([
    document(files, item.paths.instruction), document(files, item.paths.rubric),
    document(files, item.paths.qualification), document(files, item.paths.metadata),
    document(files, item.paths.verifier),
  ]);
  return { ...item, instruction, rubric, qualificationDocument, metadata, verifier };
}
