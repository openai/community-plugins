import {
  getBenchmarkOverview,
  getLeaderboard,
  getQualityQueue,
  getTask,
} from "./benchmark/index.ts";
import type {
  BenchmarkOverview,
  JudgeDecision,
  LeaderboardCohort,
  LeaderboardMetrics,
  LeaderboardRow,
  QualityItem,
  TaskDetail,
  TaskSummary,
} from "./benchmark/index.ts";
import type {
  ExplorerSnapshot,
  ExplorerTask,
  Gate,
  Judgment,
  QualityReview,
  ReviewDimension,
  Trial,
} from "./ui/contracts.ts";

function gate(value: string): Gate {
  switch (value) {
    case "pass":
    case "fail":
    case "review_required":
    case "unmeasured":
      return value;
    default:
      return "unknown";
  }
}

function rateSet(metrics: LeaderboardMetrics): ExplorerSnapshot["leaderboard"]["metrics"] {
  return {
    functional: { numerator: metrics.functional.passed, denominator: metrics.functional.denominator, unknown: metrics.counts.functionalUnavailable },
    quality: { numerator: metrics.quality.passed, denominator: metrics.quality.denominator, unknown: metrics.counts.qualityUnmeasured + metrics.counts.qualityReviewRequired },
    overall: { numerator: metrics.combined.passed, denominator: metrics.combined.denominator, unknown: metrics.counts.combinedUnmeasured + metrics.counts.combinedReviewRequired },
  };
}

const EMPTY_RATES: ExplorerSnapshot["leaderboard"]["metrics"] = {
  functional: { numerator: 0, denominator: 0 },
  quality: { numerator: 0, denominator: 0 },
  overall: { numerator: 0, denominator: 0 },
};

function trial(row: LeaderboardRow, reviewed: ReadonlySet<string>): Trial {
  return {
    id: row.trialId,
    taskId: row.taskId,
    model: [row.model, row.effort].filter(Boolean).join(" · ") || "Unknown model",
    functional: gate(row.functional),
    quality: gate(row.quality),
    overall: gate(row.combined),
    ...(reviewed.has(row.trialId) ? { reviewId: row.trialId } : {}),
    ...(row.error ? { error: row.error } : {}),
  };
}

function judgment(value: JudgeDecision | undefined): Judgment | undefined {
  if (!value) return undefined;
  return {
    decision: value.score === 1 ? "pass" : value.score === 0 ? "fail" : "unmeasured",
    reason: value.reason ?? "No reasoning was recorded.",
  };
}

const QUALITY_DIMENSIONS = ["readability", "maintainability", "scope", "tests"];
const DIMENSION_LABELS: Record<string, string> = {
  readability: "Readability",
  maintainability: "Maintainability",
  scope: "Scope and simplicity",
  tests: "Test value and proportionality",
};

function dimension(item: QualityItem, id: string): ReviewDimension {
  const sol = judgment(item.primary?.decisions[id]);
  const astra = judgment(item.reviewer?.decisions[id]);
  const adjudication = judgment(item.adjudication?.decisions[id]);
  return {
    id,
    label: DIMENSION_LABELS[id] ?? id,
    decision: gate(item.dimensions[id] ?? "unmeasured"),
    ...(sol ? { sol } : {}),
    ...(astra ? { astra } : {}),
    ...(adjudication ? { adjudication } : {}),
  };
}

function review(item: QualityItem, trialById: ReadonlyMap<string, LeaderboardRow>): QualityReview {
  const original = trialById.get(item.trialId);
  return {
    id: item.trialId,
    trialId: item.trialId,
    taskId: item.taskId ?? original?.taskId ?? "Unknown task",
    model: [item.model ?? original?.model, item.effort ?? original?.effort].filter(Boolean).join(" · ") || "Unknown model",
    status: gate(item.status),
    dimensions: QUALITY_DIMENSIONS.map((id) => dimension(item, id)),
    ...(item.evidencePath ? { evidence: [{ path: item.evidencePath, label: "Open the native quality review" }] } : {}),
  };
}

function cohort(item: LeaderboardCohort, reviewed: ReadonlySet<string>) {
  return {
    id: item.id,
    label: item.label,
    ...(item.benchmarkRevision ? { revision: item.benchmarkRevision } : {}),
    metrics: rateSet(item.metrics),
    trials: item.rows.map((row) => trial(row, reviewed)),
  };
}

function benchmark(overview: BenchmarkOverview): ExplorerSnapshot["benchmark"] {
  return {
    name: overview.name,
    rootLabel: overview.rootLabel,
    ...(overview.description ? { summary: overview.description } : {}),
    ...(overview.sources[0] ? { source: overview.sources[0] } : {}),
    status: `${overview.qualifiedTaskCount} of ${overview.taskCount} published tasks qualified`,
  };
}

function explorerTask(task: TaskSummary | TaskDetail): ExplorerTask {
  const detail = "instruction" in task ? task : null;
  return {
    id: task.id,
    title: task.title,
    status: task.qualification,
    ...(task.description ? { summary: task.description } : {}),
    authoring: task.authoring,
    instruction: detail?.instruction ?? null,
    rubric: detail?.rubric ?? null,
    qualification: detail?.qualificationDocument ?? null,
    verifier: detail?.verifier ?? null,
  };
}

/** Return the catalog or one task's authoring documents; never read Harbor runs or paid-judge evidence. */
export async function getAuthoringSnapshot(markerPath: string, selectedTaskId?: string): Promise<ExplorerSnapshot> {
  const overview = await getBenchmarkOverview(markerPath);
  const detail = selectedTaskId === undefined ? null : await getTask(markerPath, selectedTaskId);
  return {
    benchmark: benchmark(overview),
    tasks: overview.tasks.map((item) => explorerTask(detail?.id === item.id ? detail : item)),
    leaderboard: { metrics: EMPTY_RATES, trials: [] },
    reviews: [],
    warnings: overview.notices,
  };
}

/** Prepare app-only presentation data from the exact opened benchmark marker. */
export async function getExplorerSnapshot(markerPath: string): Promise<ExplorerSnapshot> {
  const [overview, leaderboard, queue] = await Promise.all([
    getBenchmarkOverview(markerPath),
    getLeaderboard(markerPath),
    getQualityQueue(markerPath),
  ]);
  const tasks = await Promise.all(overview.tasks.map((task) => getTask(markerPath, task.id)));
  const reviewIds = new Set(queue.items.map((item) => item.trialId));
  const trialById = new Map(leaderboard.cohorts.flatMap((item) => item.rows.map((row): [string, LeaderboardRow] => [row.trialId, row])));
  const cohorts = leaderboard.cohorts.map((item) => cohort(item, reviewIds));
  const first = cohorts[0];
  return {
    benchmark: benchmark(overview),
    tasks: tasks.filter((task) => task !== null).map(explorerTask),
    leaderboard: { metrics: first?.metrics ?? EMPTY_RATES, trials: first?.trials ?? [], cohorts },
    reviews: queue.items.map((item) => review(item, trialById)),
    warnings: [...new Set([...overview.notices, ...leaderboard.notices, ...queue.notices])],
  };
}
