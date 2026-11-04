import path from "node:path";

import { BenchmarkFiles } from "./files.ts";
import { cohort } from "./leaderboard.ts";
import { manifest } from "./manifest.ts";
import { qualityQueue } from "./quality.ts";
import { document, task, tasks } from "./tasks.ts";
import type { BenchmarkOverview, Leaderboard, QualityQueue, TaskDetail } from "./types.ts";

export type * from "./types.ts";

export async function getBenchmarkOverview(markerPath: string): Promise<BenchmarkOverview> {
  const files = await BenchmarkFiles.fromMarker(markerPath);
  const config = await manifest(files);
  const notices: string[] = [];
  const catalog = await tasks(files, notices);
  const published = catalog.filter((entry) => entry.authoring.location === "published");
  return {
    name: config.name,
    rootLabel: path.basename(files.root),
    markerPath: files.marker,
    description: config.description,
    taskCount: published.length,
    qualifiedTaskCount: published.filter((entry) => entry.qualification === "qualified").length,
    tasks: catalog,
    sources: ["benchmark.lighthouse", "tasks/", "review/", ".local/drafts/"],
    notices,
  };
}

export async function getTask(markerPath: string, taskId: string): Promise<TaskDetail | null> {
  const files = await BenchmarkFiles.fromMarker(markerPath);
  await manifest(files);
  return task(files, taskId);
}

export async function getLeaderboard(markerPath: string): Promise<Leaderboard> {
  const files = await BenchmarkFiles.fromMarker(markerPath);
  const config = await manifest(files);
  const notices: string[] = [];
  const markdown = await document(files, config.leaderboard);
  if (!markdown) notices.push("The benchmark Markdown leaderboard is not available in this checkout.");
  if (config.cohorts.length === 0) notices.push("No structured Harbor cohort is configured. The Markdown leaderboard is retained as source; it is not used to infer pass rates.");
  const cohorts = [];
  for (const definition of config.cohorts) cohorts.push(await cohort(files, definition, config.qualityIndexes, notices));
  return { sourcePath: markdown?.path ?? null, document: markdown, cohorts, notices: [...new Set(notices)] };
}

export async function getQualityQueue(markerPath: string): Promise<QualityQueue> {
  const files = await BenchmarkFiles.fromMarker(markerPath);
  const config = await manifest(files);
  const queue = await qualityQueue(files, [...config.qualityIndexes, ...config.cohorts.flatMap((entry) => entry.qualityIndexes)]);
  const known = new Set(queue.items.map((item) => item.trialId));
  for (const definition of config.cohorts) {
    const notices: string[] = [];
    const selected = await cohort(files, definition, config.qualityIndexes, notices);
    queue.notices.push(...notices);
    for (const row of selected.rows) {
      if (known.has(row.trialId)) continue;
      known.add(row.trialId);
      queue.items.push({ trialId: row.trialId, taskId: row.taskId, model: row.model, effort: row.effort, status: "unmeasured", qualityPass: null, qualityScore: null, dimensions: {}, primary: null, reviewer: null, adjudication: null, evidencePath: null, sourceIndexPath: "benchmark.lighthouse" });
      queue.counts.unmeasured += 1;
    }
  }
  queue.notices = [...new Set(queue.notices)];
  return queue;
}

export async function resolveEvidencePath(markerPath: string, relativePath: string): Promise<string | null> {
  const files = await BenchmarkFiles.fromMarker(markerPath);
  await manifest(files);
  return files.locate(relativePath);
}
