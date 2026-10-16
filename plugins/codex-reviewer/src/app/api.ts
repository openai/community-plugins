import type { App } from "@modelcontextprotocol/ext-apps";
import type { FileView, RepositoryInfo, ReviewScope, ReviewSnapshot } from "../server/types";

type ToolResult = {
  isError?: boolean;
  structuredContent?: unknown;
  content?: Array<{ type: string; text?: string }>;
};

async function call<T>(app: App, name: string, args: Record<string, unknown>): Promise<T> {
  const result = await app.callServerTool({ name, arguments: args }) as ToolResult;
  if (result.isError) {
    const message = result.content?.find((item) => item.type === "text")?.text;
    throw new Error(message || `Reviewer tool ${name} failed.`);
  }
  if (result.structuredContent === undefined) throw new Error("The review server returned no structured data.");
  return result.structuredContent as T;
}

export function openRepository(app: App, rootPath: string): Promise<RepositoryInfo> {
  return call(app, "code-review.listRepositories", { rootPath });
}

export function createReview(app: App, rootPath: string, scope: ReviewScope): Promise<ReviewSnapshot> {
  return call(app, "code-review.getReview", { rootPath, scope });
}

export function readFile(app: App, snapshotId: string, path: string): Promise<FileView> {
  return call(app, "code-review.getFile", { snapshotId, path });
}

export async function readAdjacentDiff(app: App, snapshotId: string, paths: string[], currentPath: string, direction: -1 | 1): Promise<{ path: string; view: FileView } | null> {
  const index = paths.indexOf(currentPath);
  const start = index >= 0 ? index : direction > 0 ? -1 : 0;
  // 最多遍历一轮；只读、不支持和无实际行级差异的文件不作为跳转目标。
  for (let offset = 1; offset <= paths.length; offset++) {
    const path = paths[(start + direction * offset + paths.length) % paths.length];
    const view = await readFile(app, snapshotId, path);
    if (view.mode !== "diff" || !view.fileDiff.supported) continue;
    const before = (view.fileDiff.oldText ?? "").replace(/\r\n|\r/g, "\n");
    const after = (view.fileDiff.newText ?? "").replace(/\r\n|\r/g, "\n");
    if (before !== after) return { path, view };
  }
  return null;
}

export async function checkReviewFreshness(app: App, snapshotId: string): Promise<boolean> {
  const result = await call<{ stale: boolean }>(app, "code-review.isReviewStale", { snapshotId });
  return result.stale;
}
