import { readFile } from "node:fs/promises";
import { McpServer } from "@modelcontextprotocol/server";
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { OpenAIExtensions } from "@openai/mcp-extensions/server";
import { z } from "zod";
import { listRepositories } from "./repository.js";
import { getFileView, getReview, isReviewStale } from "./snapshot.js";
import { getFileDiff } from "./diff.js";
import { readThreadProjectRoot, resolveProjectRoot } from "./context.js";

const resourceUri = "ui://code-review/review.html";
const resourceMimeType = "text/html;profile=mcp-app";

export function createServer(): McpServer {
  const server = new McpServer({ name: "codex-reviewer", version: "0.3.9" });
  new OpenAIExtensions(server);

  server.registerTool(
    "code-review.open",
    {
      title: "Reviewer",
      description: "Open the embedded Reviewer panel to browse and compare local Git changes.",
      inputSchema: z.object({ rootPath: z.string().optional().describe("Inject the current conversation's absolute project folder when opening from chat. Static thread entrypoints resolve it from the host's thread metadata.") }),
      annotations: { readOnlyHint: true },
      _meta: {
        ui: { resourceUri, visibility: ["app", "model"] },
        "openai/ui": { entrypoints: [{ type: "thread" }] },
      },
    },
    async ({ rootPath }, ctx) => {
      // 1. 显式目录或当前会话的 cwd 优先，避免主项目 roots 覆盖会话所在的 worktree。
      let project = rootPath?.trim() ? resolveProjectRoot(rootPath, process.cwd(), []) : null;
      const threadId = ctx.mcpReq._meta?.threadId;
      if (!project && typeof threadId === "string") {
        try { project = await readThreadProjectRoot(threadId); }
        catch { /* 会话元数据不可用时，继续尝试宿主项目目录。 */ }
      }

      // 2. 旧宿主仅提供 roots 时保留兼容，不以插件安装目录或其他会话兜底。
      if (!project && server.server.getClientCapabilities()?.roots) {
        try {
          const { roots } = await server.server.listRoots(undefined, { timeout: 1500 });
          project = resolveProjectRoot(undefined, process.cwd(), roots);
        } catch { /* 缺少当前项目上下文时显示欢迎页面。 */ }
      }
      return {
        content: [{ type: "text", text: "Reviewer" }],
        structuredContent: { ready: true, rootPath: project, ...(!project && { contextError: "请在项目会话中打开 Reviewer" }) },
      };
    },
  );

  server.registerTool(
    "code-review.listRepositories",
    {
      title: "Open repository",
      description: "Validate a local Git repository path and list its branch refs.",
      inputSchema: z.object({ rootPath: z.string() }),
    },
    async ({ rootPath }) => {
      const repository = await listRepositories(rootPath);
      return {
        content: [{ type: "text", text: `Repository: ${repository.rootPath}` }],
        structuredContent: repository,
      };
    },
  );

  server.registerTool(
    "code-review.getReview",
    {
      title: "Create review snapshot",
      description: "Capture the selected Git comparison and repository file tree.",
      inputSchema: z.object({
        rootPath: z.string(),
        scope: z.discriminatedUnion("kind", [
          z.object({ kind: z.literal("worktree") }),
          z.object({ kind: z.literal("branch"), baseRef: z.string() }),
        ]),
      }),
    },
    async ({ rootPath, scope }) => {
      const review = await getReview(rootPath, scope);
      return {
        content: [{ type: "text", text: `Captured ${review.changedFiles.length} changed files.` }],
        structuredContent: review,
      };
    },
  );

  server.registerTool(
    "code-review.isReviewStale",
    {
      title: "Check review freshness",
      description: "Check whether the repository changed since the current review snapshot.",
      inputSchema: z.object({ snapshotId: z.string() }),
    },
    async ({ snapshotId }) => {
      const stale = await isReviewStale(snapshotId);
      return {
        content: [{ type: "text", text: stale ? "A newer repository state is available." : "Review is current." }],
        structuredContent: { stale },
      };
    },
  );

  server.registerTool(
    "code-review.getFileDiff",
    {
      title: "Read file diff",
      description: "Read one file from an immutable review snapshot.",
      inputSchema: z.object({ snapshotId: z.string(), fileId: z.string() }),
    },
    async ({ snapshotId, fileId }) => {
      const fileDiff = await getFileDiff(snapshotId, fileId);
      return {
        content: [{ type: "text", text: fileDiff.supported ? "Text diff is ready." : fileDiff.unsupportedReason ?? "Unsupported file." }],
        structuredContent: fileDiff,
      };
    },
  );

  server.registerTool(
    "code-review.getFile",
    {
      title: "Read repository file",
      description: "Read a file from the selected review as either a readonly source view or a Git diff.",
      inputSchema: z.object({ snapshotId: z.string(), path: z.string() }),
      annotations: { readOnlyHint: true },
      _meta: { ui: { visibility: ["app"] } },
    },
    async ({ snapshotId, path }) => ({
      content: [{ type: "text", text: "File view is ready." }],
      structuredContent: await getFileView(snapshotId, path),
    }),
  );

  server.registerResource(
    "code-review-ui",
    resourceUri,
    { mimeType: resourceMimeType },
    async (uri) => {
      const html = await readFile(new URL("../app/index.html", import.meta.url), "utf8");
      return {
        contents: [
          {
            uri: uri.href,
            mimeType: resourceMimeType,
            text: html,
            _meta: {
              "openai/ui": {
                preferredDisplayMode: "fullscreen",
                availableDisplayModes: ["fullscreen"],
              },
            },
          },
        ],
      };
    },
  );

  return server;
}

serveStdio(createServer);
