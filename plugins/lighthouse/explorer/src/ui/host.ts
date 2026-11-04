import { App, applyDocumentTheme, applyHostStyleVariables, type McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { z } from "zod";
import { mountExplorer, mountLibrary } from "./app.ts";
import type { BenchmarkPreference, Evidence, ExplorerAdapter, ExplorerLibrary, ExplorerLibraryAdapter, ExplorerSnapshot, LibraryBenchmark } from "./contracts.ts";
import { createBrowserNavigation, createHostedNavigation, type AuthoringRoutePane, type ExplorerNavigation } from "./navigation.ts";

const gateSchema = z.enum(["pass", "fail", "review_required", "unmeasured", "unknown"]);
const evidenceSchema = z.object({ path: z.string(), line: z.number().int().positive().optional(), label: z.string().optional() });
const documentSchema = z.object({ path: z.string(), content: z.string() });
const artifactSchema = z.object({path: z.string(), status: z.enum(["present", "missing", "invalid"])});
const authoringSchema = z.object({location: z.enum(["draft", "published", "review_only"]), artifacts: z.object({instruction: artifactSchema, rubric: artifactSchema, qualification: artifactSchema, metadata: artifactSchema, verifier: artifactSchema})});
const rateSchema = z.object({ numerator: z.number().int().nonnegative(), denominator: z.number().int().nonnegative(), unknown: z.number().int().nonnegative().optional() });
const metricsSchema = z.object({ functional: rateSchema, quality: rateSchema, overall: rateSchema });
const judgmentSchema = z.object({ decision: gateSchema, reason: z.string(), evidence: z.array(evidenceSchema).optional() });
const trialSchema = z.object({ id: z.string(), taskId: z.string(), model: z.string(), functional: gateSchema, quality: gateSchema, overall: gateSchema, reviewId: z.string().optional(), error: z.string().optional() });

const snapshotSchema = z.object({
  benchmark: z.object({ name: z.string(), rootLabel: z.string().optional(), summary: z.string().optional(), source: z.string().optional(), status: z.string().optional() }),
  tasks: z.array(z.object({ id: z.string(), title: z.string().optional(), status: z.string().optional(), summary: z.string().optional(), instruction: documentSchema.nullable(), rubric: documentSchema.nullable(), qualification: documentSchema.nullable(), verifier: documentSchema.nullable().optional(), authoring: authoringSchema.optional() })),
  leaderboard: z.object({ metrics: metricsSchema, trials: z.array(trialSchema), cohorts: z.array(z.object({ id: z.string(), label: z.string(), revision: z.string().optional(), metrics: metricsSchema, trials: z.array(trialSchema) })).optional() }),
  reviews: z.array(z.object({ id: z.string(), trialId: z.string(), taskId: z.string(), model: z.string(), status: gateSchema, dimensions: z.array(z.object({ id: z.string(), label: z.string(), decision: gateSchema, sol: judgmentSchema.optional(), astra: judgmentSchema.optional(), adjudication: judgmentSchema.optional() })), evidence: z.array(evidenceSchema).optional() })),
  warnings: z.array(z.string()).optional(),
});

const fileSchema = z.object({ file: z.object({ name: z.string().min(1), resourceUri: z.string().min(1) }) });
const authoringPaneSchema = z.enum(["instruction", "rubric", "evaluation"]);
const authoringLaunchSchema = z.object({benchmarkPath: z.string().trim().min(1), taskId: z.string().min(1).optional(), pane: authoringPaneSchema.optional()});
type FileInput = z.infer<typeof fileSchema>;
type BenchmarkInput = FileInput | {benchmarkId: string};
type AuthoringInput = {benchmarkPath: string; taskId?: string; pane?: AuthoringRoutePane};
type AuthoringFocus = {benchmarkId: string; taskId?: string; pane?: AuthoringRoutePane};
type LaunchInput = {kind: "home"} | {kind: "file"; input: FileInput} | {kind: "authoring"; input: AuthoringInput};
const benchmarkSchema = z.object({id: z.string().min(1), name: z.string().min(1), rootLabel: z.string()});
const librarySchema = z.object({benchmarks: z.array(benchmarkSchema), preferredId: z.string().optional()});
const authoringContextSchema = z.object({focus: z.object({benchmarkId: z.string().min(1), taskId: z.string().min(1).optional(), pane: authoringPaneSchema.optional()}).nullable()});

export function parseSnapshot(value: unknown): ExplorerSnapshot {
  // Transport values are JSON; absent optional properties cannot carry `undefined`.
  return snapshotSchema.parse(value) as ExplorerSnapshot;
}

export function parseLaunchInput(value: unknown): LaunchInput {
  if (value === undefined) return {kind: "home"};
  const input = z.object({file: z.unknown().optional(), benchmarkPath: z.unknown().optional()}).parse(value);
  if (input.file !== undefined) return {kind: "file", input: fileSchema.parse(value)};
  if (input.benchmarkPath === undefined) return {kind: "home"};
  const authoring = authoringLaunchSchema.parse(value);
  return {kind: "authoring", input: {benchmarkPath: authoring.benchmarkPath, ...(authoring.taskId ? {taskId: authoring.taskId} : {}), ...(authoring.pane ? {pane: authoring.pane} : {})}};
}

export function parseLibrary(value: unknown): ExplorerLibrary {
  const result = librarySchema.parse(value);
  return result.preferredId === undefined ? {benchmarks: result.benchmarks} : {benchmarks: result.benchmarks, preferredId: result.preferredId};
}

export function parseAuthoringFocus(value: unknown): AuthoringFocus | undefined {
  const {focus} = authoringContextSchema.parse(value);
  return focus ? {benchmarkId: focus.benchmarkId, ...(focus.taskId ? {taskId: focus.taskId} : {}), ...(focus.pane ? {pane: focus.pane} : {})} : undefined;
}

export function hostDeepLink(context: Partial<McpUiHostContext>): string | undefined {
  const link = z.object({url: z.string().min(1)}).safeParse(context["openai/deepLink"]);
  return link.success ? link.data.url : undefined;
}

function browserNavigation(): ExplorerNavigation | undefined {
  try {
    if (!["http:", "https:"].includes(window.location.protocol) || window.location.origin === "null") return undefined;
    window.history.replaceState(window.history.state, "", window.location.href);
    return createBrowserNavigation(window);
  } catch { return undefined; }
}

function localPreference(): BenchmarkPreference {
  const key = "lighthouse-explorer-last-benchmark";
  return {
    read() { try { return window.localStorage.getItem(key) ?? undefined; } catch { return undefined; } },
    write(id) { try { window.localStorage.setItem(key, id); } catch { /* Sandboxed widgets may disable local storage. */ } },
  };
}

function toolsError(content: unknown): string {
  const result = z.array(z.object({ type: z.string(), text: z.string().optional() })).safeParse(content);
  return result.success ? result.data.find((item) => item.type === "text")?.text ?? "The benchmark tool returned no usable data." : "The benchmark tool returned no usable data.";
}

function theme(context: Partial<McpUiHostContext>): void {
  if (context.theme) applyDocumentTheme(context.theme);
  if (context.styles?.variables) applyHostStyleVariables(context.styles.variables);
}

export async function connectHost(root: HTMLElement): Promise<void> {
  const app = new App({ name: "Lighthouse Explorer", version: "0.1.0" }, {}, { autoResize: false });
  const surface = root.ownerDocument.documentElement.dataset.lighthouseSurface;
  let receive: (input: unknown) => void = () => {};
  let acceptHostPath: ((path: string) => void) | undefined;
  let latestHostPath: string | undefined;
  const launchReady = new Promise<LaunchInput>((resolve, reject) => {
    receive = (input) => { try { resolve(parseLaunchInput(input)); } catch (error: unknown) { reject(error); } };
  });
  app.addEventListener("toolinput", (notification) => {
    receive(notification.arguments);
  });
  app.addEventListener("hostcontextchanged", (context) => {
    theme(context);
    const path = hostDeepLink(context);
    if (path !== undefined) {
      latestHostPath = path;
      acceptHostPath?.(path);
    }
  });
  const [, launch] = await Promise.all([app.connect(), launchReady]);
  const initialContext = app.getHostContext() ?? {};
  theme(initialContext);

  const call = async (name: string, args: Record<string, unknown>): Promise<unknown> => {
    const result = await app.callServerTool({name, arguments: args});
    if (result.isError) throw new Error(toolsError(result.content));
    return result.structuredContent;
  };

  function benchmarkAdapter(input: BenchmarkInput, authoring = false): ExplorerAdapter {
    const adapter: ExplorerAdapter = {
      async load(taskId) { return parseSnapshot(await call(authoring ? "lighthouse_explorer_authoring_snapshot" : "lighthouse_explorer_snapshot", {...input, ...(authoring && taskId ? {taskId} : {})})); },
    };
    if (app.getHostCapabilities()?.experimental?.["openai/files"]) {
      adapter.openFile = async (evidence: Evidence) => {
        const opened = z.object({ path: z.string().min(1) }).parse(await call("lighthouse_explorer_open_evidence", {...input, path: evidence.path}));
        await app.request({method: "openai/files/open", params: {path: opened.path}}, z.object({}));
      };
    }
    return adapter;
  }

  if (launch.kind === "file") {
    mountExplorer(root, benchmarkAdapter(launch.input));
  } else {
    const authoring = surface === "thread" || launch.kind === "authoring";
    const library: ExplorerLibraryAdapter = {
      async library() { return parseLibrary(await call("lighthouse_explorer_library", {})); },
      async choose() { return z.object({benchmark: benchmarkSchema.nullable()}).parse(await call("lighthouse_explorer_choose", {})).benchmark; },
      async attach(path: string): Promise<LibraryBenchmark> { return z.object({benchmark: benchmarkSchema}).parse(await call("lighthouse_explorer_attach", {path})).benchmark; },
      benchmark(id) { return benchmarkAdapter({benchmarkId: id}, authoring); },
    };
    if (authoring) {
      let focus: AuthoringFocus | undefined;
      if (launch.kind === "authoring") {
        const benchmark = await library.attach(launch.input.benchmarkPath);
        focus = {benchmarkId: benchmark.id, ...(launch.input.taskId ? {taskId: launch.input.taskId} : {}), ...(launch.input.pane ? {pane: launch.input.pane} : {})};
      } else focus = parseAuthoringFocus(await call("lighthouse_explorer_authoring_context", {}));
      mountLibrary(root, library, localPreference(), {surface: "authoring", ...(focus ? {initialFocus: focus} : {}), pollMs: 5_000});
    } else {
      let navigation: ExplorerNavigation | undefined;
      if (app.getHostCapabilities()?.experimental?.["openai/selfDeepLinks"]) {
        const hosted = createHostedNavigation(hostDeepLink(initialContext) ?? latestHostPath ?? "/", async (url) => {
          const result = await app.openLink({url});
          if (result.isError) throw new Error("Codex could not open this Lighthouse location.");
        });
        navigation = hosted.navigation;
        acceptHostPath = hosted.acceptHostPath;
      } else navigation = browserNavigation();
      mountLibrary(root, library, localPreference(), {...(navigation ? {navigation} : {})});
    }
  }
}
