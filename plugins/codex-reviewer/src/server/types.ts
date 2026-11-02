export type ReviewScope =
  | { kind: "worktree" }
  | { kind: "branch"; baseRef: string };

export type RepositoryRef = {
  fullName: string;
  name: string;
  revision: string;
};

export type RepositoryInfo = {
  rootPath: string;
  currentBranch: string | null;
  refs: RepositoryRef[];
};

export type TreeEntry = {
  path: string;
  kind: "file" | "directory" | "symlink";
  changed: boolean;
};

export type ChangedFile = {
  id: string;
  status: "added" | "deleted" | "modified" | "renamed" | "copied";
  oldPath: string | null;
  newPath: string | null;
  similarity: number | null;
};

export type FileDiff = ChangedFile & {
  snapshotId: string;
  oldText: string | null;
  newText: string | null;
  supported: boolean;
  unsupportedReason: string | null;
};

export type FileView =
  | { mode: "diff"; fileDiff: FileDiff }
  | { mode: "file"; path: string; text: string | null; source: "worktree" | "revision"; supported: boolean; unsupportedReason: string | null };

export type ReviewSnapshot = {
  snapshotId: string;
  rootPath: string;
  scope: ReviewScope;
  currentBranch: string | null;
  refs: RepositoryRef[];
  baseRef: string | null;
  baseRevision: string | null;
  headRevision: string | null;
  tree: TreeEntry[];
  changedFiles: ChangedFile[];
  stateFingerprint: string;
  createdAt: string;
};
