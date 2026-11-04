import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";
import { mountExplorer, mountLibrary } from "./app.ts";
import type { Evidence, ExplorerLibraryAdapter, ExplorerSnapshot, RubricMetrics, Trial } from "./contracts.ts";
import { markdown, rateValue } from "./elements.ts";
import { parseLaunchInput, parseLibrary, parseSnapshot } from "./host.ts";
import { createHostedNavigation, explorerPath } from "./navigation.ts";

const zero = { numerator: 0, denominator: 0 };
const metricsA: RubricMetrics = { functional: {numerator: 2, denominator: 3, unknown: 1}, quality: {numerator: 1, denominator: 2, unknown: 1}, overall: {numerator: 1, denominator: 3, unknown: 1} };
const metricsB: RubricMetrics = { functional: {numerator: 1, denominator: 1}, quality: zero, overall: {numerator: 0, denominator: 1, unknown: 1} };
const firstTrial: Trial = { id: "first-trial", taskId: "first-task", model: "model-a", functional: "pass", quality: "review_required", overall: "review_required", reviewId: "review-one" };
const secondTrial: Trial = { id: "second-trial", taskId: "second-task", model: "model-b", functional: "pass", quality: "unmeasured", overall: "unmeasured" };
const data: ExplorerSnapshot = {
  benchmark: {name: "Test Lighthouse", rootLabel: "/checkout/benchmark"},
  tasks: [{id: "first-task", title: "First task", status: "qualified", instruction: {path: "tasks/first-task/instruction.md", content: "# Request\nBuild the feature."}, rubric: {path: "review/first-task/requirements.md", content: "# Curator rubric\nThis is private evaluator guidance."}, qualification: null}, {id: "second-task", instruction: null, rubric: null, qualification: null}],
  leaderboard: { metrics: metricsA, trials: [firstTrial], cohorts: [{id: "first", label: "First comparison", metrics: metricsA, trials: [firstTrial]}, {id: "second", label: "Second comparison", metrics: metricsB, trials: [secondTrial]}] },
  reviews: [{ id: "review-one", trialId: "first-trial", taskId: "first-task", model: "model-a", status: "review_required", dimensions: [{id: "readability", label: "Readability", decision: "review_required", sol: {decision: "fail", reason: "Nested control flow", evidence: [{path: "src/feature.py", line: 12}]}, astra: {decision: "pass", reason: "The early return makes the logic clear."}}] }],
};

function dom(): Document {
  const { document } = parseHTML("<!doctype html><html><body><div id='root'></div></body></html>");
  Object.defineProperty(globalThis, "document", { configurable: true, value: document });
  return document;
}

function root(document: Document): HTMLElement {
  const found = document.getElementById("root");
  if (!found) throw new Error("test root missing");
  return found;
}

function control(document: Document, label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find((item) => item.textContent?.trim() === label);
  if (!found) throw new Error(`missing button ${label}`);
  return found;
}

async function settle(): Promise<void> { await new Promise<void>((resolve) => setImmediate(resolve)); }

const polars = {id: "polars", name: "Polars Bench", rootLabel: "/code/polars-bench"};
const grimoire = {id: "grimoire", name: "Grimoire Bench", rootLabel: "/code/grimoire-bench"};

function benchmarkControl(document: Document, name: string): HTMLButtonElement {
  const item = [...document.querySelectorAll("button")].find((candidate) => candidate.getAttribute("aria-label") === `Open ${name}`);
  if (!item) throw new Error(`missing benchmark ${name}`);
  return item;
}

function homeAdapter(overrides: Partial<ExplorerLibraryAdapter> = {}): ExplorerLibraryAdapter {
  return {
    library: async () => ({benchmarks: [polars, grimoire]}),
    choose: async () => null,
    attach: async () => polars,
    benchmark: (id) => ({load: async () => ({...data, benchmark: {name: id === polars.id ? polars.name : grimoire.name}})}),
    ...overrides,
  };
}

function submitPath(document: Document, path: string): void {
  const field = document.querySelector<HTMLInputElement>("#benchmark-path");
  const form = document.querySelector<HTMLFormElement>("form");
  if (!field || !form) throw new Error("benchmark path form missing");
  field.value = path;
  field.dispatchEvent(new (document.defaultView?.Event ?? Event)("input"));
  form.dispatchEvent(new (document.defaultView?.Event ?? Event)("submit", {cancelable: true}));
}

test("native shell history restores a benchmark, task rubric, overview and the launcher without echo", async () => {
  const document = dom();
  const pushed: string[] = [];
  const host = createHostedNavigation("/", async (url) => { pushed.push(new URL(url).searchParams.get("path") ?? ""); });
  const app = mountLibrary(root(document), homeAdapter(), undefined, {navigation: host.navigation});
  await settle();
  benchmarkControl(document, polars.name).click();
  await settle();
  control(document, "Tasks & rubrics").click();
  control(document, "Curator rubric").click();
  assert.deepEqual(pushed, ["/benchmarks/polars", "/benchmarks/polars/tasks", "/benchmarks/polars/tasks/first-task?view=rubric"]);
  assert.match(document.body.textContent ?? "", /private evaluator guidance/);
  host.acceptHostPath(pushed[1] ?? "");
  assert.match(document.body.textContent ?? "", /Build the feature/);
  host.acceptHostPath(pushed[0] ?? "");
  assert.equal(document.querySelector("[aria-current=page]")?.textContent, "Overview");
  host.acceptHostPath("/");
  assert.ok(benchmarkControl(document, polars.name));
  host.acceptHostPath(pushed[2] ?? "");
  await settle();
  assert.match(document.body.textContent ?? "", /private evaluator guidance/);
  assert.equal(pushed.length, 3);
  assert.equal(explorerPath(host.navigation.current()), pushed[2]);
  app.destroy();
});

test("a reopened authoring sidebar starts on the requested rubric and refreshes the selected task", async () => {
  const document = dom();
  const loads: (string | undefined)[] = [];
  let rubric = "First saved rubric.";
  const adapter = homeAdapter({benchmark: () => ({load: async (id) => {
    loads.push(id);
    return {...data, tasks: data.tasks.map((task) => task.id === "first-task" ? {...task, rubric: {path: "review/first-task/requirements.md", content: rubric}} : task)};
  }})});
  const app = mountLibrary(root(document), adapter, undefined, {surface: "authoring", initialFocus: {benchmarkId: polars.id, taskId: "first-task", pane: "rubric"}});
  await settle();
  assert.match(document.body.textContent ?? "", /First saved rubric/);
  assert.doesNotMatch(document.body.textContent ?? "", /Build the feature/);
  assert.deepEqual(loads, ["first-task"]);
  rubric = "Updated without publishing the task.";
  control(document, "↻ Refresh").click();
  await settle();
  assert.match(document.body.textContent ?? "", /Updated without publishing/);
  const picker = document.querySelector<HTMLSelectElement>("#authoring-task");
  assert.ok(picker);
  Object.defineProperty(picker, "value", {configurable: true, value: "second-task"});
  picker.dispatchEvent(new (document.defaultView?.Event ?? Event)("change"));
  await settle();
  assert.deepEqual(loads, ["first-task", "first-task", "second-task"]);
  assert.doesNotMatch(document.body.textContent ?? "", /Updated without publishing/);
  app.destroy();
});

test("a task-count sidebar opens the catalog and only requests task documents after a row is selected", async () => {
  const document = dom();
  const loads: (string | undefined)[] = [];
  const adapter = homeAdapter({benchmark: () => ({load: async (id) => {
    loads.push(id);
    return {...data, tasks: data.tasks.map((task) => task.id === id ? task : {...task, instruction: null, rubric: null, qualification: null})};
  }})});
  const app = mountLibrary(root(document), adapter, undefined, {surface: "authoring", initialFocus: {benchmarkId: polars.id}});
  await settle();
  assert.match(document.body.textContent ?? "", /Benchmark tasks/);
  assert.match(document.body.textContent ?? "", /Published tasks/);
  assert.doesNotMatch(document.body.textContent ?? "", /Build the feature|private evaluator guidance/);
  assert.deepEqual(loads, [undefined]);
  const row = [...document.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes("First task"));
  assert.ok(row);
  row.click();
  await settle();
  assert.match(document.body.textContent ?? "", /Build the feature/);
  assert.deepEqual(loads, [undefined, "first-task"]);
  control(document, "← All benchmark tasks").click();
  await settle();
  assert.match(document.body.textContent ?? "", /Benchmark tasks/);
  assert.doesNotMatch(document.body.textContent ?? "", /Build the feature|private evaluator guidance/);
  assert.deepEqual(loads, [undefined, "first-task", undefined]);
  app.destroy();
});

test("learner and curator documents remain in distinct views with a working raw-evidence action", async () => {
  const document = dom();
  const opened: Evidence[] = [];
  const app = mountExplorer(root(document), {load: async () => data, openFile: async (evidence) => { opened.push(evidence); }});
  await settle();
  assert.match(document.body.textContent ?? "", /33%/);
  assert.match(document.body.textContent ?? "", /1 of 3/);
  control(document, "Tasks & rubrics").click();
  assert.match(document.body.textContent ?? "", /Build the feature/);
  assert.doesNotMatch(document.body.textContent ?? "", /private evaluator guidance/);
  control(document, "Curator rubric").click();
  assert.match(document.body.textContent ?? "", /private evaluator guidance/);
  assert.doesNotMatch(document.body.textContent ?? "", /Build the feature/);
  control(document, "Open source ↗").click();
  await settle();
  assert.deepEqual(opened, [{path: "review/first-task/requirements.md"}]);
  control(document, "Qualification").click();
  assert.match(document.body.textContent ?? "", /This document was not found/);
  assert.doesNotMatch(document.body.textContent ?? "", /private evaluator guidance/);
  app.destroy();
});

test("cohort switching updates counts and only shows that cohort’s trials", async () => {
  const document = dom();
  const app = mountExplorer(root(document), {load: async () => data});
  await settle();
  control(document, "Leaderboard").click();
  assert.match(document.body.textContent ?? "", /first-trial/);
  assert.doesNotMatch(document.body.textContent ?? "", /second-trial/);
  assert.match(document.body.textContent ?? "", /33%/);
  const select = document.querySelector<HTMLSelectElement>("#comparison-cohort");
  const next = select?.querySelector<HTMLOptionElement>('option[value="second"]');
  if (!select || !next) throw new Error("comparison selector not available");
  Object.defineProperty(select, "value", { configurable: true, value: "second" });
  select.dispatchEvent(new (document.defaultView?.Event ?? Event)("change"));
  assert.match(document.body.textContent ?? "", /second-trial/);
  assert.doesNotMatch(document.body.textContent ?? "", /first-trial/);
  assert.doesNotMatch(document.body.textContent ?? "", /33%/);
  assert.match(document.body.textContent ?? "", /0 of 0/);
  assert.match(document.body.textContent ?? "", /Unmeasured/);
  assert.match(document.body.textContent ?? "", /1 unresolved or unavailable/);
  app.destroy();
});

test("review disagreement stays visibly unresolved and preserves independent reasons and evidence", async () => {
  const document = dom();
  const opened: Evidence[] = [];
  const app = mountExplorer(root(document), {load: async () => data, openFile: async (evidence) => { opened.push(evidence); }});
  await settle();
  control(document, "Quality reviews").click();
  assert.match(document.body.textContent ?? "", /Human review required/);
  assert.match(document.body.textContent ?? "", /does not count as a pass/);
  assert.match(document.body.textContent ?? "", /Nested control flow/);
  assert.match(document.body.textContent ?? "", /The early return makes the logic clear/);
  control(document, "src/feature.py:12").click();
  await settle();
  assert.deepEqual(opened, [{path: "src/feature.py", line: 12}]);
  app.destroy();
});

test("queue placeholders never claim attached evidence or a completed judge decision", async () => {
  const document = dom();
  const missing: ExplorerSnapshot = {...data, reviews: [{id: "missing", trialId: "first-trial", taskId: "first-task", model: "model-a", status: "unmeasured", dimensions: [{id: "readability", label: "Readability", decision: "unmeasured"}]}]};
  const app = mountExplorer(root(document), {load: async () => missing});
  await settle();
  assert.match(document.body.textContent ?? "", /No compatible quality decisions were discovered/);
  control(document, "Quality reviews").click();
  assert.match(document.body.textContent ?? "", /1 original submission tracked/);
  assert.match(document.body.textContent ?? "", /No measured quality outcome/);
  assert.match(document.body.textContent ?? "", /No decision recorded/);
  assert.doesNotMatch(document.body.textContent ?? "", /with attached evidence/);
  app.destroy();
});

test("source HTML is text and an unavailable denominator never displays as zero percent", () => {
  dom();
  const content = markdown("# Finding\n<img src=x onerror=alert(1)>\n`<script>bad()</script>`\n\n| Dimension | Decision |\n| --- | --- |\n| readability | unresolved |");
  assert.equal(content.querySelector("img"), null);
  assert.equal(content.querySelector("script"), null);
  assert.match(content.textContent ?? "", /<img src=x onerror=alert\(1\)>/);
  assert.equal(content.querySelectorAll("table tbody tr").length, 1);
  assert.equal(rateValue(zero), "—");
  assert.equal(rateValue({numerator: 0, denominator: 3}), "0%");
});

test("host boundary validates the complete snapshot and refuses unrecognized gate outcomes", () => {
  assert.deepEqual(parseSnapshot(data), data);
  assert.throws(() => parseSnapshot({...data, leaderboard: {...data.leaderboard, trials: [{...firstTrial, quality: "presumed_pass"}]}}));
});

test("the native home lists and switches benchmarks while resetting the selected benchmark screen", async () => {
  const document = dom();
  const remembered: string[] = [];
  const app = mountLibrary(root(document), homeAdapter(), {read: () => undefined, write: (id) => { remembered.push(id); }});
  await settle();
  assert.match(document.body.textContent ?? "", /See how your agents really perform/);
  assert.match(document.body.textContent ?? "", /Polars Bench/);
  assert.match(document.body.textContent ?? "", /Grimoire Bench/);
  assert.match(document.body.textContent ?? "", /Choose benchmark folder/);
  benchmarkControl(document, polars.name).click();
  await settle();
  assert.equal(document.querySelector("h1")?.textContent, polars.name);
  control(document, "Quality reviews").click();
  assert.match(document.body.textContent ?? "", /Human review required/);
  control(document, "Switch benchmark").click();
  assert.match(benchmarkControl(document, polars.name).textContent ?? "", /Last opened/);
  benchmarkControl(document, grimoire.name).click();
  await settle();
  assert.equal(document.querySelector("h1")?.textContent, grimoire.name);
  assert.equal(document.querySelector("[aria-current=page]")?.textContent, "Overview");
  assert.deepEqual(remembered, [polars.id, grimoire.id]);
  app.destroy();
});

test("native picker cancellation stays on home and accepted folders open in the same view", async () => {
  const document = dom();
  let choices = 0;
  const app = mountLibrary(root(document), homeAdapter({library: async () => ({benchmarks: []}), choose: async () => ++choices === 1 ? null : grimoire}));
  await settle();
  control(document, "Choose benchmark folder").click();
  await settle();
  assert.match(document.body.textContent ?? "", /No benchmarks found yet/);
  assert.equal(document.querySelector("[role=alert]"), null);
  control(document, "Choose benchmark folder").click();
  await settle();
  assert.equal(document.querySelector("h1")?.textContent, grimoire.name);
  control(document, "Switch benchmark").click();
  assert.ok(benchmarkControl(document, grimoire.name));
  app.destroy();
});

test("an unavailable folder shows actionable feedback and the direct path remains usable", async () => {
  const document = dom();
  const submitted: string[] = [];
  const app = mountLibrary(root(document), homeAdapter({
    choose: async () => { throw new Error("Folder selection is unavailable on this host."); },
    attach: async (path) => { submitted.push(path); if (path !== grimoire.rootLabel) throw new Error("No benchmark.lighthouse exists in that folder."); return grimoire; },
  }));
  await settle();
  control(document, "Choose benchmark folder").click();
  await settle();
  assert.match(document.querySelector("[role=alert]")?.textContent ?? "", /selection is unavailable.+enter a path below/);
  submitPath(document, "/missing");
  await settle();
  assert.match(document.querySelector("#benchmark-path-error")?.textContent ?? "", /No benchmark.lighthouse/);
  assert.equal(document.querySelector("#benchmark-path")?.getAttribute("aria-invalid"), "true");
  assert.match(document.body.textContent ?? "", /Your benchmarks/);
  submitPath(document, `  ${grimoire.rootLabel}  `);
  await settle();
  assert.deepEqual(submitted, ["/missing", grimoire.rootLabel]);
  assert.equal(document.querySelector("h1")?.textContent, grimoire.name);
  app.destroy();
});

test("host preferred benchmark wins over a remembered one, and inaccessible saved benchmarks retain a switch route", async () => {
  const document = dom();
  const app = mountLibrary(root(document), homeAdapter({
    library: async () => ({benchmarks: [polars, grimoire], preferredId: grimoire.id}),
    benchmark: () => ({load: async () => { throw new Error("This checkout is no longer available."); }}),
  }), {read: () => polars.id, write: () => {}});
  await settle();
  assert.match(document.body.textContent ?? "", /Could not open this benchmark/);
  assert.match(document.body.textContent ?? "", /no longer available/);
  control(document, "Switch benchmark").click();
  assert.match(document.body.textContent ?? "", /Your benchmarks/);
  assert.match(benchmarkControl(document, polars.name).textContent ?? "", /Last opened/);
  assert.doesNotMatch(benchmarkControl(document, grimoire.name).textContent ?? "", /Last opened/);
  app.destroy();
});

test("empty native launch arguments resolve to home and file launch arguments preserve the opaque Codex resource", () => {
  const file = {name: "benchmark.lighthouse", resourceUri: "codex-resource://opaque"};
  assert.deepEqual(parseLaunchInput({}), {kind: "home"});
  assert.deepEqual(parseLaunchInput(undefined), {kind: "home"});
  assert.deepEqual(parseLaunchInput({file}), {kind: "file", input: {file}});
  assert.throws(() => parseLaunchInput({file: {name: "benchmark.lighthouse"}}));
  assert.deepEqual(parseLibrary({benchmarks: [polars]}), {benchmarks: [polars]});
  assert.throws(() => parseLibrary({benchmarks: [{...polars, id: ""}]}));
});
