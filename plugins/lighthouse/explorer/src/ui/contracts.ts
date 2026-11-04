export type Gate = "pass" | "fail" | "review_required" | "unmeasured" | "unknown";

export interface Evidence {
  path: string;
  line?: number;
  label?: string;
}

export interface DocumentEvidence {
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

export interface ExplorerTask {
  id: string;
  title?: string;
  status?: string;
  summary?: string;
  instruction: DocumentEvidence | null;
  rubric: DocumentEvidence | null;
  qualification: DocumentEvidence | null;
  verifier?: DocumentEvidence | null;
  authoring?: TaskAuthoring;
}

export interface Rate {
  numerator: number;
  denominator: number;
  unknown?: number;
}

export interface RubricMetrics { functional: Rate; quality: Rate; overall: Rate }

export interface Cohort {
  id: string;
  label: string;
  revision?: string;
  metrics: RubricMetrics;
  trials: Trial[];
}

export interface Trial {
  id: string;
  taskId: string;
  model: string;
  functional: Gate;
  quality: Gate;
  overall: Gate;
  reviewId?: string;
  error?: string;
}

export interface Judgment {
  decision: Gate;
  reason: string;
  evidence?: Evidence[];
}

export interface ReviewDimension {
  id: string;
  label: string;
  decision: Gate;
  sol?: Judgment;
  astra?: Judgment;
  adjudication?: Judgment;
}

export interface QualityReview {
  id: string;
  trialId: string;
  taskId: string;
  model: string;
  status: Gate;
  dimensions: ReviewDimension[];
  evidence?: Evidence[];
}

export interface ExplorerSnapshot {
  benchmark: {
    name: string;
    rootLabel?: string;
    summary?: string;
    source?: string;
    status?: string;
  };
  tasks: ExplorerTask[];
  leaderboard: {
    metrics: RubricMetrics;
    trials: Trial[];
    cohorts?: Cohort[];
  };
  reviews: QualityReview[];
  warnings?: string[];
}

export interface ExplorerAdapter {
  load(taskId?: string): Promise<ExplorerSnapshot>;
  openFile?: (evidence: Evidence) => Promise<void>;
}

export interface LibraryBenchmark {
  id: string;
  name: string;
  rootLabel: string;
}

export interface ExplorerLibrary {
  benchmarks: LibraryBenchmark[];
  preferredId?: string;
}

export interface ExplorerLibraryAdapter {
  library(): Promise<ExplorerLibrary>;
  choose(): Promise<LibraryBenchmark | null>;
  attach(path: string): Promise<LibraryBenchmark>;
  benchmark(id: string): ExplorerAdapter;
}

export interface BenchmarkPreference {
  read(): string | undefined;
  write(id: string): void;
}
