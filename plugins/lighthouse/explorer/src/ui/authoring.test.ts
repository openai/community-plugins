import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";

import { renderAuthoring, type AuthoringActions, type AuthoringPane } from "./authoring.ts";
import type { AuthoringArtifact, Evidence, ExplorerSnapshot, ExplorerTask } from "./contracts.ts";

const zero = {numerator: 0, denominator: 0};

function artifact(path: string, status: AuthoringArtifact["status"]): AuthoringArtifact { return {path, status}; }

const draft: ExplorerTask = {
  id: "partial-draft", title: "Recognise late data", status: "unknown",
  instruction: {path: ".local/drafts/partial-draft/instruction.md", content: "# A realistic request\nKeep late records."},
  rubric: {path: "review/partial-draft/requirements.md", content: "# Graded behavior\nCurator control is private."},
  qualification: null,
  verifier: {path: ".local/drafts/partial-draft/tests/test.sh", content: "#!/bin/sh\n<script>must remain text</script>"},
  authoring: {location: "draft", artifacts: {
    instruction: artifact(".local/drafts/partial-draft/instruction.md", "present"), rubric: artifact("review/partial-draft/requirements.md", "present"),
    qualification: artifact("review/partial-draft/qualification.md", "missing"), metadata: artifact(".local/drafts/partial-draft/task.toml", "invalid"),
    verifier: artifact(".local/drafts/partial-draft/tests/test.sh", "present"),
  }},
};

const published: ExplorerTask = {
  id: "published-task", title: "Qualified task", status: "qualified", instruction: null, rubric: null,
  qualification: {path: "review/published-task/qualification.md", content: "# Result\nReference and adversarial controls ran. See [baseline](../../jobs/example/result.json#L12) and [outside](../../../outside). External [docs](https://example.com) stay explicit.\n`[verbatim](../../jobs/example/result.json)`"},
  verifier: null,
  authoring: {location: "published", artifacts: {
    instruction: artifact("tasks/published-task/instruction.md", "missing"), rubric: artifact("review/published-task/requirements.md", "missing"),
    qualification: artifact("review/published-task/qualification.md", "present"), metadata: artifact("tasks/published-task/task.toml", "present"),
    verifier: artifact("tasks/published-task/tests/test.sh", "missing"),
  }},
};

const review: ExplorerTask = {
  id: "review-only", title: "Private outline", instruction: null, rubric: null, qualification: null,
  authoring: {location: "review_only", artifacts: {
    instruction: artifact(".local/drafts/review-only/instruction.md", "missing"), rubric: artifact("review/review-only/requirements.md", "invalid"),
    qualification: artifact("review/review-only/qualification.md", "missing"), metadata: artifact(".local/drafts/review-only/task.toml", "missing"),
    verifier: artifact(".local/drafts/review-only/tests/test.sh", "missing"),
  }},
};

const snapshot: ExplorerSnapshot = {
  benchmark: {name: "Bench for humans"}, tasks: [published, draft, review],
  leaderboard: {metrics: {functional: zero, quality: zero, overall: zero}, trials: []}, reviews: [],
};

function install(data: ExplorerSnapshot = snapshot, initialTask?: string, initialPane: AuthoringPane = "instruction", sourceEnabled = true) {
  const {document} = parseHTML("<!doctype html><html><body></body></html>");
  Object.defineProperty(globalThis, "document", {configurable: true, value: document});
  const selected: string[] = [];
  const visited: AuthoringPane[] = [];
  const opened: Evidence[] = [];
  let task = initialTask;
  let pane = initialPane;
  let refreshes = 0;
  const actions: AuthoringActions = {
    selectTask: (id) => { task = id; selected.push(id); render(); },
    selectPane: (choice) => { pane = choice; visited.push(choice); render(); },
    open: (evidence) => { opened.push(evidence); }, canOpen: sourceEnabled,
    refresh: () => { refreshes += 1; },
    showCatalog: () => { task = undefined; pane = "instruction"; render(); },
  };
  function render() { document.body.replaceChildren(renderAuthoring(data, task, pane, actions)); }
  render();
  return {document, selected, visited, opened, refreshes: () => refreshes};
}

function control(document: Document, label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find((candidate) => candidate.textContent?.trim() === label);
  if (!found) throw new Error(`Missing ${label}`);
  return found;
}

function choose(document: Document, value: string): void {
  const select = document.querySelector("select");
  if (!select) throw new Error("Missing task selector");
  Object.defineProperty(select, "value", {configurable: true, value});
  select.dispatchEvent(new (document.defaultView?.Event ?? Event)("change"));
}

function keyboard(document: Document, button: HTMLButtonElement, key: string): Event {
  const event = new (document.defaultView?.Event ?? Event)("keydown", {cancelable: true});
  Object.defineProperty(event, "key", {value: key});
  button.dispatchEvent(event);
  return event;
}

test("no task selection opens a benchmark-wide catalog with distinct counts and no arbitrary private or learner content", () => {
  const publishedUnknown = {...published, id: "unqualified-task", title: "Another published task", status: "unknown"};
  const {document, selected, refreshes} = install({...snapshot, tasks: [...snapshot.tasks, publishedUnknown]});
  assert.equal(document.querySelector("h1")?.textContent, "Benchmark tasks");
  assert.equal(document.querySelector(".authoring-catalog h2")?.textContent, "Tasks in this benchmark");
  assert.deepEqual([...document.querySelectorAll(".authoring-catalog__counts > div")].map((item) => [item.querySelector("dt")?.textContent, item.querySelector("dd")?.textContent]), [["Published tasks", "2"], ["Qualified", "1"], ["Retained drafts", "1"], ["Review only", "1"]]);
  assert.deepEqual([...document.querySelectorAll(".authoring-catalog__group h3")].map((item) => item.textContent), ["Published tasks", "Retained drafts", "Curator reviews"]);
  assert.deepEqual([...document.querySelectorAll(".authoring-catalog__task")].map((item) => item.getAttribute("aria-label")), ["Open Qualified task", "Open Another published task", "Open Recognise late data", "Open Private outline"]);
  assert.match(document.body.textContent ?? "", /Qualified tasks are part of the published count/);
  assert.match(document.body.textContent ?? "", /may be historical/);
  assert.doesNotMatch(document.body.textContent ?? "", /Curator control|must remain text|Keep late records|adversarial controls ran/);
  assert.equal(document.querySelector(".authoring-picker"), null);
  control(document, "↻ Refresh").click();
  assert.equal(refreshes(), 1);
  const row = document.querySelector<HTMLButtonElement>('[aria-label="Open Recognise late data"]');
  assert.ok(row);
  row.click();
  assert.deepEqual(selected, [draft.id]);
  assert.match(document.body.textContent ?? "", /Keep late records/);
  control(document, "← All benchmark tasks").click();
  assert.ok(document.querySelector(".authoring-catalog"));
  assert.doesNotMatch(document.body.textContent ?? "", /Keep late records/);
});

test("an explicitly selected draft shows learner content without exposing the curator rubric or verifier", () => {
  const {document, opened, refreshes} = install(snapshot, draft.id);
  assert.equal(document.querySelector(".authoring-task h2")?.textContent, "Recognise late data");
  assert.equal(document.querySelector("label")?.getAttribute("for"), "authoring-task");
  assert.deepEqual([...document.querySelectorAll("optgroup")].map((item) => item.getAttribute("label")), ["Drafts", "Curator reviews", "Published tasks"]);
  assert.match(document.body.textContent ?? "", /has not been published/);
  assert.match(document.body.textContent ?? "", /LEARNER-VISIBLE/);
  assert.match(document.body.textContent ?? "", /Keep late records/);
  assert.doesNotMatch(document.body.textContent ?? "", /Curator control|must remain text/);
  assert.match(document.body.textContent ?? "", /3 of 5 files present/);
  assert.match(document.body.textContent ?? "", /Availability is shown separately/);
  assert.equal(document.querySelector(".authoring-artifact__status--invalid")?.textContent, "Invalid");
  control(document, "Open source ↗").click();
  assert.deepEqual(opened, [{path: ".local/drafts/partial-draft/instruction.md"}]);
  control(document, "↻ Refresh").click();
  assert.equal(refreshes(), 1);
});

test("curator and evaluation panes stay distinct and preserve qualification and raw verifier evidence", () => {
  const {document, visited, opened} = install(snapshot, draft.id);
  control(document, "Private rubric").click();
  assert.match(document.body.textContent ?? "", /CURATOR-ONLY/);
  assert.match(document.body.textContent ?? "", /Curator control is private/);
  assert.doesNotMatch(document.body.textContent ?? "", /Keep late records|must remain text/);
  control(document, "Open source ↗").click();
  assert.deepEqual(opened, [{path: "review/partial-draft/requirements.md"}]);
  control(document, "Evaluation").click();
  assert.match(document.body.textContent ?? "", /Functional behavior/);
  assert.match(document.body.textContent ?? "", /Astra Medium to review Sol failures/);
  assert.match(document.body.textContent ?? "", /does not show that this is configured or passing/);
  assert.match(document.body.textContent ?? "", /Qualification evidence is not available yet/);
  assert.match(document.body.textContent ?? "", /review\/partial-draft\/qualification.md/);
  assert.equal(document.querySelector("script"), null);
  assert.equal(document.querySelector("details summary")?.textContent, "Read verifier source");
  assert.match(document.querySelector("details code")?.textContent ?? "", /<script>must remain text<\/script>/);
  assert.doesNotMatch(document.body.textContent ?? "", /Curator control|Keep late records/);
  assert.deepEqual(visited, ["rubric", "evaluation"]);
  choose(document, "published-task");
  assert.equal(document.querySelector(".authoring-phase")?.textContent, "Qualified");
  assert.match(document.body.textContent ?? "", /Reference and adversarial controls ran/);
  assert.match(document.body.textContent ?? "", /presence by itself does not mean/);
  assert.match(document.body.textContent ?? "", /Verifier is not available yet/);
});

test("keyboard pane selection works, unavailable selected tasks cannot fall through into another task's private content", () => {
  const {document, visited, selected} = install(snapshot, "stale-task", "rubric");
  assert.match(document.body.textContent ?? "", /This task is no longer available/);
  assert.doesNotMatch(document.body.textContent ?? "", /Curator control/);
  choose(document, "review-only");
  assert.deepEqual(selected, ["review-only"]);
  assert.match(document.body.textContent ?? "", /Review only/);
  assert.match(document.body.textContent ?? "", /Private rubric is invalid/);
  assert.match(document.body.textContent ?? "", /review\/review-only\/requirements.md/);
  assert.equal(keyboard(document, control(document, "Private rubric"), "ArrowRight").defaultPrevented, true);
  assert.equal(document.querySelector("[aria-current]")?.textContent, "Evaluation");
  keyboard(document, control(document, "Evaluation"), "Home");
  assert.equal(document.querySelector("[aria-current]")?.textContent, "Learner request");
  keyboard(document, control(document, "Learner request"), "ArrowLeft");
  assert.equal(document.querySelector("[aria-current]")?.textContent, "Evaluation");
  assert.deepEqual(visited, ["evaluation", "instruction", "evaluation"]);
});

test("empty benchmarks remain useful and source controls are omitted when the host cannot open them", () => {
  const {document: emptyDocument} = install({...snapshot, tasks: []});
  assert.match(emptyDocument.body.textContent ?? "", /No task drafts or published tasks found/);
  assert.match(emptyDocument.body.textContent ?? "", /When files are saved, refresh this panel/);
  assert.equal(emptyDocument.querySelector("select"), null);
  assert.deepEqual([...emptyDocument.querySelectorAll(".authoring-catalog__counts dd")].map((item) => item.textContent), ["0", "0", "0", "0"]);
  const {document} = install(snapshot, draft.id, "instruction", false);
  assert.match(document.body.textContent ?? "", /partial-draft\/instruction.md/);
  assert.ok([...document.querySelectorAll("button")].every((item) => item.textContent !== "Open source ↗"));
});

test("relative evidence links are human-readable, resolve inside the benchmark, and never activate paths outside it", () => {
  const {document, opened} = install(snapshot, published.id, "evaluation");
  const evidence = control(document, "baseline");
  assert.equal(evidence.title, "jobs/example/result.json");
  evidence.click();
  assert.deepEqual(opened, [{path: "jobs/example/result.json", line: 12}]);
  assert.match(document.querySelector(".markdown")?.textContent ?? "", /\[outside\]\(\.\.\/\.\.\/\.\.\/outside\)/);
  assert.match(document.querySelector(".markdown")?.textContent ?? "", /https:\/\/example.com/);
  assert.match(document.querySelector(".markdown code")?.textContent ?? "", /\[verbatim\]/);
  assert.equal(document.querySelectorAll(".authoring-inline-source").length, 1);
});

test("pane and task selection restores focus on the replacement control after the view rerenders", () => {
  const {document} = install(snapshot, draft.id);
  const prototype = document.defaultView?.HTMLElement.prototype;
  if (!prototype) throw new Error("HTMLElement prototype missing");
  const original = Object.getOwnPropertyDescriptor(prototype, "focus");
  const focused: string[] = [];
  Object.defineProperty(prototype, "focus", {configurable: true, value: function(this: HTMLElement) { focused.push(this.id); }});
  try {
    control(document, "Private rubric").click();
    choose(document, published.id);
    keyboard(document, control(document, "Private rubric"), "ArrowRight");
    control(document, "← All benchmark tasks").click();
    const row = document.querySelector<HTMLButtonElement>('[aria-label="Open Recognise late data"]');
    assert.ok(row);
    row.click();
    assert.deepEqual(focused, ["authoring-pane-rubric", "authoring-task", "authoring-pane-evaluation", "authoring-catalog", "authoring-task-title"]);
  } finally {
    if (original) Object.defineProperty(prototype, "focus", original);
    else Reflect.deleteProperty(prototype, "focus");
  }
});
