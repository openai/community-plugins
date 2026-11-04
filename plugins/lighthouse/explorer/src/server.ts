import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { getBenchmarkOverview, resolveEvidencePath } from "./benchmark/index.ts";
import { BenchmarkLibrary, libraryOptionsFromEnvironment } from "./benchmark-library.ts";
import { lighthouseIcons } from "./icon.ts";
import { requestBenchmarkDirectory } from "./native-picker.ts";
import { getAuthoringSnapshot, getExplorerSnapshot } from "./snapshot.ts";
import { authoringPane, hostThreadId, ThreadAuthoringStore } from "./thread-authoring.ts";

const WIDGET_URI = "ui://lighthouse/explorer";
const HOME_URI = "ui://lighthouse/explorer-home";
const THREAD_URI = "ui://lighthouse/explorer-thread";
const WIDGET_MIME = "text/html;profile=mcp-app";
const server = new McpServer({ name: "Lighthouse Explorer", version: "0.1.0", icons: lighthouseIcons });
const annotations = { readOnlyHint: true, destructiveHint: false, openWorldHint: false };
const fileInput = z.object({ name: z.string(), resourceUri: z.string() });
const benchmarkId = z.string().min(1).optional();
const taskId = z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/, "Use the Harbor task folder name.").optional();
const library = new BenchmarkLibrary(libraryOptionsFromEnvironment(process.env));
const authoring = new ThreadAuthoringStore();

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? Object.fromEntries(Object.entries(value)) : null;
}

function hostMarker(meta: unknown): string | undefined {
  const resource = object(object(meta)?.["openai/resource"]);
  if (!resource) return undefined;
  const candidate = resource?.["path"];
  if (typeof candidate !== "string" || !candidate) {
    throw new Error("Open the benchmark.lighthouse file in Codex to attach a benchmark.");
  }
  return candidate;
}

async function markerFor(meta: unknown, id?: string): Promise<string> {
  const marker = hostMarker(meta);
  if (marker) return marker;
  if (id) return library.resolve(id);
  throw new Error("Choose a benchmark in Lighthouse or open its benchmark.lighthouse file in Codex.");
}

function errorResult(error: unknown) {
  const message = error instanceof Error ? error.message : "Lighthouse could not read this benchmark.";
  return { isError: true, content: [textContent(message)] };
}

function textContent(text: string): { type: "text"; text: string } {
  return { type: "text", text };
}

for (const resource of [
  { name: "lighthouse-explorer-ui", uri: WIDGET_URI, surface: "file", preferredDisplayMode: "fullscreen", availableDisplayModes: ["inline", "fullscreen"] },
  { name: "lighthouse-explorer-home-ui", uri: HOME_URI, surface: "home", preferredDisplayMode: "inline", availableDisplayModes: ["inline"] },
  { name: "lighthouse-explorer-thread-ui", uri: THREAD_URI, surface: "thread", preferredDisplayMode: "fullscreen", availableDisplayModes: ["fullscreen"] },
]) {
  server.registerResource(resource.name, resource.uri, { mimeType: WIDGET_MIME }, async () => {
    const directory = dirname(resolve(process.argv[1] ?? "."));
    const bundle = await readFile(join(directory, "explorer.html"), "utf8");
    const html = bundle.replace(/<html(?=[\s>])/i, `<html data-lighthouse-surface="${resource.surface}"`);
    return { contents: [{ uri: resource.uri, mimeType: WIDGET_MIME, text: html, _meta: {
      ui: { prefersBorder: false, csp: { connectDomains: [], resourceDomains: [] } },
      "openai/ui": { preferredDisplayMode: resource.preferredDisplayMode, availableDisplayModes: resource.availableDisplayModes },
    } }] };
  });
}

for (const entrypoint of [
  { name: "lighthouse_explorer_home", type: "global", uri: HOME_URI, title: process.env.LIGHTHOUSE_EXPLORER_TITLE ?? "Lighthouse", description: "Open the graphical Lighthouse dashboard in Codex to browse your local benchmarks." },
  { name: "lighthouse_explorer_thread", type: "thread", uri: THREAD_URI, title: "Lighthouse Tasks & Rubrics", description: "Open the Lighthouse task catalog or resume the selected task and rubric beside this conversation." },
]) {
  server.registerTool(entrypoint.name, {
    title: entrypoint.title,
    description: entrypoint.description,
    inputSchema: {},
    annotations,
    _meta: {
      ui: { resourceUri: entrypoint.uri, visibility: ["app"] },
      "openai/ui": { entrypoints: [{ type: entrypoint.type }] },
    },
  }, async () => ({ content: [], structuredContent: { page: "library" } }));
}

server.registerTool("lighthouse_open_benchmark", {
  title: "Lighthouse Benchmark Explorer",
  description: "Open a benchmark.lighthouse launcher to browse its tasks, evaluator rubric, recorded leaderboard and quality reviews. The viewer is read-only.",
  inputSchema: { file: fileInput },
  annotations,
  _meta: {
    ui: { resourceUri: WIDGET_URI },
    "openai/ui": { entrypoints: [{ type: "file", extensions: [".lighthouse"] }] },
  },
}, async (_args, extra) => {
  try {
    const benchmark = await getBenchmarkOverview(await markerFor(extra._meta));
    return {
      content: [textContent(`Opened ${benchmark.name}: ${benchmark.taskCount} benchmark tasks. Curator-only evidence is available inside the Explorer.`)],
      structuredContent: { name: benchmark.name, taskCount: benchmark.taskCount },
    };
  } catch (error) {
    return errorResult(error);
  }
});

server.registerTool("lighthouse_open_task_authoring", {
  title: "Open Lighthouse tasks and rubrics",
  description: "Open the graphical Lighthouse tasks side panel beside this Codex conversation. Call this when the user asks how many tasks a benchmark has, what its tasks are, or for task status, task details, authoring or rubrics; answer in chat too. Use an absolute benchmark folder or benchmark.lighthouse path. For a count or list, omit taskId and pane to show the task catalog and return public counts and titles. For one named task, set its Harbor task folder name and optionally an instruction, rubric or evaluation pane. Private evaluator documents stay in the side panel.",
  inputSchema: { benchmarkPath: z.string().min(1), taskId, pane: authoringPane.optional() },
  annotations,
  _meta: { ui: { resourceUri: THREAD_URI } },
}, async (args, extra) => {
  try {
    const threadId = hostThreadId(extra._meta);
    if (!threadId) throw new Error("Open Lighthouse tasks from a Codex conversation to attach its side panel.");
    const benchmark = await library.attach(args.benchmarkPath);
    const marker = await library.resolve(benchmark.id);
    const pane = args.pane ?? "instruction";
    const selected = args.taskId ? { taskId: args.taskId } : {};
    await authoring.set(threadId, { benchmarkPath: marker, ...selected, pane });
    if (!args.taskId) {
      const overview = await getBenchmarkOverview(marker);
      const drafts = overview.tasks.filter((task) => task.authoring.location === "draft").length;
      const reviewOnly = overview.tasks.filter((task) => task.authoring.location === "review_only").length;
      const tasks = overview.tasks.map((task) => ({id: task.id, title: task.title, location: task.authoring.location, qualification: task.qualification}));
      return {
        content: [textContent(`Opened the ${overview.name} task catalog beside this conversation: ${overview.taskCount} published, ${overview.qualifiedTaskCount} qualified, ${drafts} retained draft folders, ${reviewOnly} review-only folders.`)],
        structuredContent: {authoring: {benchmarkId: benchmark.id, pane}, catalog: {name: overview.name, published: overview.taskCount, qualified: overview.qualifiedTaskCount, drafts, reviewOnly, tasks}},
      };
    }
    return {
      content: [textContent("Opened the selected Lighthouse task beside this conversation.")],
      structuredContent: { authoring: { benchmarkId: benchmark.id, ...selected, pane } },
    };
  } catch (error) {
    return errorResult(error);
  }
});

server.registerTool("lighthouse_explorer_authoring_context", {
  title: "Read Lighthouse task authoring context",
  description: "Recover this Codex conversation's selected task and panel view. Available only to the Explorer UI.",
  inputSchema: {},
  annotations,
  _meta: { ui: { visibility: ["app"] } },
}, async (_args, extra) => {
  try {
    const threadId = hostThreadId(extra._meta);
    const binding = threadId ? await authoring.get(threadId) : null;
    if (!binding) return { content: [], structuredContent: { focus: null } };
    const benchmark = await library.attach(binding.benchmarkPath);
    const selected = binding.taskId ? { taskId: binding.taskId } : {};
    return { content: [], structuredContent: { focus: { benchmarkId: benchmark.id, ...selected, pane: binding.pane } } };
  } catch (error) {
    return errorResult(error);
  }
});

server.registerTool("lighthouse_explorer_authoring_snapshot", {
  title: "Read Lighthouse tasks and rubrics",
  description: "Refresh the benchmark's task catalog or only the explicitly selected task's documents, without loading learner runs. Available only to the Explorer UI.",
  inputSchema: { file: fileInput.optional(), benchmarkId, taskId },
  annotations,
  _meta: { ui: { visibility: ["app"] } },
}, async (args, extra) => {
  try {
    const snapshot = await getAuthoringSnapshot(await markerFor(extra._meta, args.benchmarkId), args.taskId);
    return { content: [], structuredContent: { ...snapshot } };
  } catch (error) {
    return errorResult(error);
  }
});

server.registerTool("lighthouse_explorer_library", {
  title: "List Lighthouse benchmarks",
  description: "Load this device’s known local benchmarks for the read-only Explorer. Available only to the Explorer UI.",
  inputSchema: {},
  annotations,
  _meta: { ui: { visibility: ["app"] } },
}, async () => {
  try {
    return { content: [], structuredContent: { ...await library.list() } };
  } catch (error) {
    return errorResult(error);
  }
});

server.registerTool("lighthouse_explorer_attach", {
  title: "Choose a Lighthouse benchmark",
  description: "Explicitly attach a local benchmark folder or benchmark.lighthouse file chosen in the Explorer UI. Does not modify the benchmark.",
  inputSchema: { path: z.string().min(1) },
  annotations,
  _meta: { ui: { visibility: ["app"] } },
}, async (args) => {
  try {
    return { content: [], structuredContent: { benchmark: { ...await library.attach(args.path) } } };
  } catch (error) {
    return errorResult(error);
  }
});

server.registerTool("lighthouse_explorer_choose", {
  title: "Choose a Lighthouse benchmark folder",
  description: "Ask Codex to show a native directory picker and open the benchmark the user chooses. Does not modify the benchmark.",
  inputSchema: {},
  annotations,
  _meta: { ui: { visibility: ["app"] } },
}, async () => {
  try {
    const selected = await requestBenchmarkDirectory(server);
    return { content: [], structuredContent: { benchmark: selected ? { ...await library.attach(selected) } : null } };
  } catch (error) {
    return errorResult(error);
  }
});

server.registerTool("lighthouse_explorer_snapshot", {
  title: "Read Lighthouse Explorer",
  description: "Load the attached benchmark’s curator-only read-only Explorer state. Available only to the Explorer UI.",
  inputSchema: { file: fileInput.optional(), benchmarkId },
  annotations,
  _meta: { ui: { visibility: ["app"] } },
}, async (args, extra) => {
  try {
    const snapshot = await getExplorerSnapshot(await markerFor(extra._meta, args.benchmarkId));
    return { content: [], structuredContent: { ...snapshot } };
  } catch (error) {
    return errorResult(error);
  }
});

server.registerTool("lighthouse_explorer_open_evidence", {
  title: "Open Lighthouse evidence",
  description: "Resolve a permitted benchmark evidence file for opening in the native Codex file viewer. Available only to the Explorer UI.",
  inputSchema: { path: z.string().min(1), file: fileInput.optional(), benchmarkId },
  annotations,
  _meta: { ui: { visibility: ["app"] } },
}, async (args, extra) => {
  try {
    const path = await resolveEvidencePath(await markerFor(extra._meta, args.benchmarkId), args.path);
    return { content: [], structuredContent: { path } };
  } catch (error) {
    return errorResult(error);
  }
});

server.connect(new StdioServerTransport()).catch((error: unknown) => {
  process.stderr.write(`${error instanceof Error ? error.message : "MCP connection failed"}\n`);
  process.exitCode = 1;
});
