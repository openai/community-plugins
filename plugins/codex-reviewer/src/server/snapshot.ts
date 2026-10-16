import { createHash, randomUUID } from "node:crypto";
import { lstat, readFile, readlink, readdir, realpath } from "node:fs/promises";
import path from "node:path";
import { git, gitBuffer } from "./git.js";
import { listRepositories } from "./repository.js";
import type { ChangedFile, FileDiff, FileView, RepositoryInfo, RepositoryRef, ReviewScope, ReviewSnapshot, TreeEntry } from "./types.js";

const MAX_DIFF_FILE_BYTES = 1024 * 1024;
const snapshots = new Map<string, {
  snapshot: ReviewSnapshot;
  files: Map<string, FileDiff>;
  trackedPaths: Set<string>;
  views: Map<string, Promise<FileView>>;
  headTree?: Promise<Map<string, RevisionEntry>>;
}>();

async function mapConcurrent<T, R>(items: T[], limit: number, visit: (item: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const index = next++;
      results[index] = await visit(items[index]);
    }
  }));
  return results;
}

function normalizePath(filePath: string): string {
  return filePath.replaceAll(path.sep, "/");
}

async function walkTree(rootPath: string): Promise<TreeEntry[]> {
  const entries: TreeEntry[] = [];
  const pending = [""];

  // 1. 使用目录项类型，避免对完整文件树逐个 stat；全局最多并行读取 8 个目录。
  while (pending.length) {
    const directories = pending.splice(0, 8);
    await Promise.all(directories.map(async (directory) => {
      const children = await readdir(path.join(rootPath, directory), { withFileTypes: true });
      for (const child of children) {
        if (child.name === ".git") continue;
        const relativePath = directory ? path.join(directory, child.name) : child.name;
        const kind = child.isSymbolicLink() ? "symlink" : child.isDirectory() ? "directory" : "file";
        entries.push({ path: normalizePath(relativePath), kind, changed: false });
        if (kind === "directory") pending.push(relativePath);
      }
    }));
  }
  return entries;
}

function parseNameStatus(output: string): ChangedFile[] {
  const fields = output.split("\0").filter(Boolean);
  const changed: ChangedFile[] = [];

  for (let index = 0; index < fields.length;) {
    const statusField = fields[index++];
    const code = statusField[0];
    const isRename = code === "R";
    const isCopy = code === "C";
    let oldPath: string | null = null;
    let newPath: string | null = null;

    if (isRename || isCopy) {
      oldPath = normalizePath(fields[index++] ?? "");
      newPath = normalizePath(fields[index++] ?? "");
    } else {
      const changedPath = normalizePath(fields[index++] ?? "");
      if (code === "D") oldPath = changedPath;
      else newPath = changedPath;
      if (code !== "A" && code !== "D") oldPath = newPath = changedPath;
    }

    const status = isRename
      ? "renamed"
      : isCopy
        ? "copied"
        : code === "A"
          ? "added"
          : code === "D"
            ? "deleted"
            : "modified";
    const similarity = isRename || isCopy ? Number.parseInt(statusField.slice(1), 10) : null;
    const id = createHash("sha256")
      .update(`${status}\0${oldPath ?? ""}\0${newPath ?? ""}`)
      .digest("hex")
      .slice(0, 20);

    changed.push({ id, status, oldPath, newPath, similarity: Number.isNaN(similarity) ? null : similarity });
  }

  return changed;
}

async function treeWithChangedPaths(rootPath: string, changedFiles: ChangedFile[]): Promise<TreeEntry[]> {
  const tree = await walkTree(rootPath);
  const existingPaths = new Set(tree.map((entry) => entry.path));
  const changedPaths = new Set<string>();

  for (const file of changedFiles) {
    if (file.oldPath) changedPaths.add(file.oldPath);
    if (file.newPath) changedPaths.add(file.newPath);

    if (file.oldPath && !existingPaths.has(file.oldPath) && (file.status === "deleted" || file.status === "renamed")) {
      let parentPath = path.posix.dirname(file.oldPath);
      const missingParents: string[] = [];
      while (parentPath !== "." && !existingPaths.has(parentPath)) {
        missingParents.push(parentPath);
        parentPath = path.posix.dirname(parentPath);
      }
      for (const missingParent of missingParents.reverse()) {
        tree.push({
          path: missingParent,
          kind: "directory",
          changed: true,
        });
        existingPaths.add(missingParent);
      }
      tree.push({
        path: file.oldPath,
        kind: "file",
        changed: true,
      });
      existingPaths.add(file.oldPath);
    }
  }

  const changedDirectories = new Set<string>();
  for (const changedPath of changedPaths) {
    let parentPath = path.posix.dirname(changedPath);
    while (parentPath !== ".") {
      changedDirectories.add(parentPath);
      parentPath = path.posix.dirname(parentPath);
    }
  }

  for (const entry of tree) {
    entry.changed = changedPaths.has(entry.path) || changedDirectories.has(entry.path);
  }

  return tree.sort((left, right) => left.path.localeCompare(right.path));
}

async function stateFingerprint(
  rootPath: string,
  refs: RepositoryRef[],
  currentBranch: string | null,
  headRevision: string | null,
  status: string,
): Promise<string> {
  const paths = new Set<string>();
  const fields = status.split("\0");
  for (let index = 0; index < fields.length; index++) {
    const field = fields[index];
    if (!field) continue;
    paths.add(field.slice(3));
    if (/[RC]/.test(field.slice(0, 2))) paths.add(fields[++index]);
  }
  const metadata = await mapConcurrent([...paths].sort(), 8, async (filePath) => {
    try {
      const info = await lstat(worktreePath(rootPath, filePath));
      return [filePath, info.size, info.mtimeMs, info.ctimeMs];
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [filePath, null];
      throw error;
    }
  });
  const state = JSON.stringify({
    currentBranch,
    headRevision,
    refs: refs.map((ref) => [ref.fullName, ref.revision]),
    status,
    metadata,
  });
  return createHash("sha256").update(state).digest("hex");
}

async function getHeadRevision(rootPath: string, repository: RepositoryInfo): Promise<string | null> {
  try {
    return (await git(rootPath, "rev-parse", "--verify", "HEAD^{commit}")).trim();
  } catch (error) {
    if (repository.currentBranch && !repository.refs.some((ref) => ref.fullName === `refs/heads/${repository.currentBranch}`)) return null;
    throw error;
  }
}

type RevisionEntry = { objectId: string; type: string; size: number };

async function revisionTree(rootPath: string, revision: string | null): Promise<Map<string, RevisionEntry>> {
  if (revision === null) return new Map();
  const output = await git(rootPath, "ls-tree", "-r", "-l", "-z", revision, "--");
  const tree = new Map<string, RevisionEntry>();
  for (const field of output.split("\0").filter(Boolean)) {
    const tab = field.indexOf("\t");
    const [, type, objectId, size] = field.slice(0, tab).trim().split(/\s+/);
    tree.set(field.slice(tab + 1), { objectId, type, size: Number(size) });
  }
  return tree;
}

async function revisionContent(rootPath: string, tree: Map<string, RevisionEntry>, filePath: string): Promise<Buffer | null> {
  const entry = tree.get(filePath);
  if (!entry) return null;
  if (entry.type !== "blob") throw new Error("Git links and non-text objects cannot be rendered.");
  if (entry.size > MAX_DIFF_FILE_BYTES) throw new Error("File is larger than the 1 MiB display limit.");
  return gitBuffer(rootPath, "cat-file", "blob", entry.objectId);
}

function worktreePath(rootPath: string, relativePath: string): string {
  const absolutePath = path.resolve(rootPath, ...relativePath.split("/"));
  const relative = path.relative(rootPath, absolutePath);
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error("A changed file resolved outside the selected repository.");
  }
  return absolutePath;
}

async function worktreeContent(rootPath: string, relativePath: string): Promise<Buffer | null> {
  const absolutePath = worktreePath(rootPath, relativePath);
  try {
    const parent = await realpath(path.dirname(absolutePath));
    const relative = path.relative(rootPath, parent);
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      throw new Error("The file's parent resolved outside the selected repository.");
    }
    const metadata = await lstat(absolutePath);
    if (metadata.size > MAX_DIFF_FILE_BYTES) throw new Error("File is larger than the 1 MiB display limit.");
    if (metadata.isSymbolicLink()) {
      return Buffer.from(await readlink(absolutePath), "utf8");
    }
    if (!metadata.isFile()) throw new Error("Directories and special files cannot be rendered.");
    const bytes = await readFile(absolutePath);
    if (bytes.length > MAX_DIFF_FILE_BYTES) throw new Error("File is larger than the 1 MiB display limit.");
    return bytes;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}

function decodeText(bytes: Buffer): string {
  if (bytes.includes(0)) throw new Error("Binary files cannot be rendered as text.");
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new Error("File is not valid UTF-8 text.");
  }
}

async function captureFileDiffs(rootPath: string, snapshot: ReviewSnapshot): Promise<Map<string, FileDiff>> {
  // 2. 每个版本只读取一次 blob 索引，文件内容有界并行读取，避免每个文件启动 4 次 Git。
  const [baseTree, headTree] = await Promise.all([
    revisionTree(rootPath, snapshot.baseRevision),
    snapshot.scope.kind === "branch" ? revisionTree(rootPath, snapshot.headRevision) : Promise.resolve(null),
  ]);
  const files = await mapConcurrent(snapshot.changedFiles, 6, async (file): Promise<[string, FileDiff]> => {
    let oldText: string | null = null;
    let newText: string | null = null;
    let unsupportedReason: string | null = null;

    try {
      if (file.oldPath) {
        const oldBytes = await revisionContent(rootPath, baseTree, file.oldPath);
        if (oldBytes === null) throw new Error("Original file contents were unavailable in the captured revision.");
        oldText = decodeText(oldBytes);
      }
      if (file.newPath) {
        const newBytes = snapshot.scope.kind === "worktree"
          ? await worktreeContent(rootPath, file.newPath)
          : await revisionContent(rootPath, headTree!, file.newPath);
        if (newBytes === null) throw new Error("New file contents were unavailable in the captured revision.");
        newText = decodeText(newBytes);
      }
    } catch (error) {
      oldText = null;
      newText = null;
      unsupportedReason = (error as Error).message || "File cannot be rendered.";
    }

    return [file.id, {
      ...file,
      snapshotId: snapshot.snapshotId,
      oldText,
      newText,
      supported: unsupportedReason === null,
      unsupportedReason,
    }];
  });
  return new Map(files);
}

async function changedFilesForScope(
  rootPath: string,
  scope: ReviewScope,
  headRevision: string | null,
  refs: RepositoryRef[],
): Promise<{ baseRef: string | null; baseRevision: string | null; changedFiles: ChangedFile[] }> {
  let baseRef: string | null = null;
  let baseRevision = headRevision;
  let output: string;

  if (scope.kind === "worktree") {
    // 3. 首次提交前没有旧版本，将当前存在的已暂存和未跟踪文件视为新增，不写入空树对象。
    if (headRevision === null) {
      const listed = await git(rootPath, "ls-files", "--cached", "--others", "--exclude-standard", "-z");
      const paths = [...new Set(listed.split("\0").filter(Boolean))];
      const existing = await mapConcurrent(paths, 8, async (filePath) => {
        try { await lstat(worktreePath(rootPath, filePath)); return `A\0${filePath}\0`; }
        catch (error) {
          if ((error as NodeJS.ErrnoException).code === "ENOENT") return "";
          throw error;
        }
      });
      return { baseRef, baseRevision, changedFiles: parseNameStatus(existing.join("")) };
    }
    output = await git(rootPath, "diff", "--no-ext-diff", "--no-textconv", "--find-renames", "--name-status", "-z", headRevision, "--");
    const untracked = await git(rootPath, "ls-files", "--others", "--exclude-standard", "-z");
    const changedFiles = parseNameStatus(output);
    for (const filePath of untracked.split("\0").filter(Boolean)) {
      const normalized = normalizePath(filePath);
      const id = createHash("sha256").update(`added\0\0${normalized}`).digest("hex").slice(0, 20);
      changedFiles.push({ id, status: "added", oldPath: null, newPath: normalized, similarity: null });
    }
    return { baseRef, baseRevision, changedFiles };
  }

  if (headRevision === null) throw new Error("Branch comparison requires the first commit.");
  const selectedRef = refs.find((ref) => ref.fullName === scope.baseRef);
  if (!selectedRef) throw new Error("Select an available local or remote-tracking branch.");
  baseRef = selectedRef.fullName;
  try {
    baseRevision = (await git(rootPath, "merge-base", "HEAD", baseRef)).trim();
  } catch {
    throw new Error("The selected branch has no merge base with the current HEAD.");
  }
  output = await git(
    rootPath,
    "diff",
    "--no-ext-diff",
    "--no-textconv",
    "--find-renames",
    "--name-status",
    "-z",
    baseRevision,
    headRevision,
    "--",
  );

  return { baseRef, baseRevision, changedFiles: parseNameStatus(output) };
}

async function currentState(rootPath: string, knownRepository?: RepositoryInfo): Promise<{
  rootPath: string;
  refs: RepositoryRef[];
  currentBranch: string | null;
  headRevision: string | null;
  fingerprint: string;
}> {
  const repository = knownRepository ?? await listRepositories(rootPath);
  const [headRevision, status] = await Promise.all([
    getHeadRevision(rootPath, repository),
    git(rootPath, "status", "--porcelain=v1", "-z", "--untracked-files=all"),
  ]);
  return {
    rootPath: repository.rootPath,
    refs: repository.refs,
    currentBranch: repository.currentBranch,
    headRevision,
    fingerprint: await stateFingerprint(rootPath, repository.refs, repository.currentBranch, headRevision, status),
  };
}

export async function getReview(rootPath: string, scope: ReviewScope): Promise<ReviewSnapshot> {
  const selected = await listRepositories(rootPath);
  const repository = await currentState(selected.rootPath, selected);
  const headRevision = repository.headRevision;
  const changes = await changedFilesForScope(repository.rootPath, scope, headRevision, repository.refs);
  const tree = await treeWithChangedPaths(repository.rootPath, changes.changedFiles);
  const snapshot: ReviewSnapshot = {
    snapshotId: randomUUID(),
    rootPath: repository.rootPath,
    scope,
    currentBranch: repository.currentBranch,
    refs: repository.refs,
    baseRef: changes.baseRef,
    baseRevision: changes.baseRevision,
    headRevision,
    tree,
    changedFiles: changes.changedFiles,
    stateFingerprint: repository.fingerprint,
    createdAt: new Date().toISOString(),
  };

  const [files, tracked] = await Promise.all([
    captureFileDiffs(repository.rootPath, snapshot),
    git(repository.rootPath, "ls-files", "--cached", "-z"),
  ]);
  const afterCapture = await currentState(repository.rootPath);
  if (afterCapture.fingerprint !== snapshot.stateFingerprint) {
    throw new Error("The repository changed while the review was being captured. Refresh and try again.");
  }

  snapshots.set(snapshot.snapshotId, { snapshot, files, trackedPaths: new Set(tracked.split("\0").filter(Boolean)), views: new Map() });
  while (snapshots.size > 8) snapshots.delete(snapshots.keys().next().value!);
  return snapshot;
}

export async function isReviewStale(snapshotId: string): Promise<boolean> {
  const record = snapshots.get(snapshotId);
  if (!record) throw new Error("This review snapshot has expired. Refresh the review.");
  const snapshot = record.snapshot;
  const current = await currentState(snapshot.rootPath);
  return current.fingerprint !== snapshot.stateFingerprint;
}

export function getCapturedFileDiff(snapshotId: string, fileId: string): FileDiff {
  const record = snapshots.get(snapshotId);
  if (!record) throw new Error("This review snapshot has expired. Refresh the review.");
  const file = record.files.get(fileId);
  if (!file) throw new Error("The file is not part of this review snapshot.");
  return file;
}

export async function getFileView(snapshotId: string, filePath: string): Promise<FileView> {
  const record = snapshots.get(snapshotId);
  if (!record) throw new Error("This review snapshot has expired. Refresh the review.");
  const { snapshot } = record;
  const entry = snapshot.tree.find((item) => item.path === filePath && item.kind !== "directory");
  if (!entry) throw new Error("This file is not part of this review snapshot.");
  const changed = snapshot.changedFiles.find((file) => file.newPath === filePath || file.oldPath === filePath);
  if (changed && (snapshot.scope.kind === "branch" || changed.oldPath !== null || record.trackedPaths.has(filePath))) {
    return { mode: "diff", fileDiff: getCapturedFileDiff(snapshotId, changed.id) };
  }

  // 4. 普通文件按需读取，首次打开后固定内容；分支比较优先读取捕获的 HEAD。
  let captured = record.views.get(filePath);
  if (!captured) {
    captured = (async (): Promise<FileView> => {
      let source: "worktree" | "revision" = "worktree";
      try {
        if (changed) {
          const file = getCapturedFileDiff(snapshotId, changed.id);
          return { mode: "file", path: filePath, text: file.newText, source, supported: file.supported, unsupportedReason: file.unsupportedReason };
        }
        let bytes: Buffer | null;
        if (snapshot.scope.kind === "branch" && snapshot.headRevision) {
          const tree = await (record.headTree ??= revisionTree(snapshot.rootPath, snapshot.headRevision));
          source = tree.has(filePath) ? "revision" : "worktree";
          bytes = source === "revision" ? await revisionContent(snapshot.rootPath, tree, filePath) : await worktreeContent(snapshot.rootPath, filePath);
        } else bytes = await worktreeContent(snapshot.rootPath, filePath);
        if (bytes === null) throw new Error("The file is no longer available. Refresh the review.");
        return { mode: "file", path: filePath, text: decodeText(bytes), source, supported: true, unsupportedReason: null };
      } catch (error) {
        return { mode: "file", path: filePath, text: null, source, supported: false, unsupportedReason: (error as Error).message };
      }
    })();
    record.views.set(filePath, captured);
  }
  return captured;
}
