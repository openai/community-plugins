export type Qualification = "qualified" | "not_qualified" | "unknown";
export type QualityStatus = "pass" | "fail" | "review_required" | "unmeasured";
export type Outcome = "pass" | "fail" | "review_required" | "unmeasured" | "unavailable";

export interface BenchmarkDocument {
  path: string;
  content: string;
}

export interface AuthoringArtifact {
  path: string;
  status: "present" | "missing" | "invalid";
}

export interface TaskAuthoring {
  location: "draft" | "published" | "review_only";
  artifacts: {
    instruction: AuthoringArtifact;
    rubric: AuthoringArtifact;
    qualification: AuthoringArtifact;
    metadata: AuthoringArtifact;
    verifier: AuthoringArtifact;
  };
}

export interface TaskSummary {
  id: string;
  name: string;
  title: string;
  description: string | null;
  version: string | null;
  difficulty: string | null;
  category: string | null;
  tags: string[];
  qualification: Qualification;
  authoring: TaskAuthoring;
  paths: {
    instruction: string | null;
    rubric: string | null;
    qualification: string | null;
    metadata: string | null;
    verifier: string | null;
  };
}

export interface TaskDetail extends TaskSummary {
  instruction: BenchmarkDocument | null;
  rubric: BenchmarkDocument | null;
  qualificationDocument: BenchmarkDocument | null;
  metadata: BenchmarkDocument | null;
  verifier: BenchmarkDocument | null;
}

export interface BenchmarkOverview {
  name: string;
  rootLabel: string;
  markerPath: string;
  description: string | null;
  taskCount: number;
  qualifiedTaskCount: number;
  tasks: TaskSummary[];
  sources: string[];
  notices: string[];
}

export interface Rate {
  passed: number;
  denominator: number;
}

export interface LeaderboardMetrics {
  functional: Rate;
  quality: Rate;
  combined: Rate;
  counts: {
    total: number;
    functionalUnavailable: number;
    qualityPass: number;
    qualityFail: number;
    qualityReviewRequired: number;
    qualityUnmeasured: number;
    combinedFail: number;
    combinedReviewRequired: number;
    combinedUnmeasured: number;
  };
}

export interface LeaderboardRow {
  trialId: string;
  taskId: string;
  model: string | null;
  effort: string | null;
  functional: Outcome;
  quality: QualityStatus;
  combined: Outcome;
  functionalReward: number | null;
  qualityScore: number | null;
  error: string | null;
  evidencePath: string;
  qualityEvidencePath: string | null;
}

export interface LeaderboardCohort {
  id: string;
  label: string;
  benchmarkRevision: string | null;
  sourcePath: string;
  mode: "complete-rubric";
  metrics: LeaderboardMetrics;
  rows: LeaderboardRow[];
}

export interface Leaderboard {
  sourcePath: string | null;
  document: BenchmarkDocument | null;
  cohorts: LeaderboardCohort[];
  notices: string[];
}

export interface JudgeDecision {
  score: number | null;
  reason: string | null;
}

export interface JudgeReview {
  model: string | null;
  effort: string | null;
  error: string | null;
  decisions: Record<string, JudgeDecision>;
}

export interface QualityItem {
  trialId: string;
  taskId: string | null;
  model: string | null;
  effort: string | null;
  status: QualityStatus;
  qualityPass: number | null;
  qualityScore: number | null;
  dimensions: Record<string, string>;
  primary: JudgeReview | null;
  reviewer: JudgeReview | null;
  adjudication: { reviewer: string | null; decisions: Record<string, JudgeDecision> } | null;
  evidencePath: string | null;
  sourceIndexPath: string;
}

export interface QualityQueue {
  sourcePaths: string[];
  items: QualityItem[];
  counts: Record<QualityStatus, number>;
  notices: string[];
}
