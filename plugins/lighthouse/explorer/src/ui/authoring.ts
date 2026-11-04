import type { DocumentEvidence, Evidence, ExplorerSnapshot, ExplorerTask } from "./contracts.ts";
import { button, el, empty, humanize, markdown } from "./elements.ts";

export type AuthoringPane = "instruction" | "rubric" | "evaluation";

export interface AuthoringActions {
  selectTask: (id: string) => void;
  selectPane: (pane: AuthoringPane) => void;
  open: (evidence: Evidence) => void;
  canOpen: boolean;
  refresh?: () => void;
  showCatalog?: () => void;
}

type ArtifactName = "instruction" | "rubric" | "qualification" | "metadata" | "verifier";
type ArtifactState = "present" | "missing" | "invalid" | "unavailable";

const panes: {id: AuthoringPane; label: string}[] = [
  {id: "instruction", label: "Learner request"},
  {id: "rubric", label: "Private rubric"},
  {id: "evaluation", label: "Evaluation"},
];

const artifacts: {id: ArtifactName; label: string; description: string}[] = [
  {id: "instruction", label: "Learner request", description: "What the learner is asked to do"},
  {id: "rubric", label: "Private rubric", description: "Requirements and evaluator controls"},
  {id: "verifier", label: "Functional verifier", description: "The deterministic test entry point"},
  {id: "qualification", label: "Qualification evidence", description: "Recorded evaluation and controls"},
  {id: "metadata", label: "Harbor task metadata", description: "The task's runtime configuration"},
];

const statusText: Record<ArtifactState, string> = {present: "Present", missing: "Missing", invalid: "Invalid", unavailable: "Not checked"};

function taskTitle(task: ExplorerTask): string { return task.title || humanize(task.id); }

function draft(task: ExplorerTask): boolean { return task.authoring?.location === "draft"; }

function published(task: ExplorerTask): boolean { return !task.authoring || task.authoring.location === "published"; }

function reviewOnly(task: ExplorerTask): boolean { return task.authoring?.location === "review_only"; }

function phase(task: ExplorerTask): {label: string; style: string; description: string} {
  if (draft(task)) return {label: "Draft", style: "draft", description: "This task has not been published to the benchmark."};
  if (task.authoring?.location === "review_only") return {label: "Review only", style: "draft", description: "Curator material exists; no published task was found."};
  if (task.status === "qualified") return {label: "Qualified", style: "qualified", description: "The benchmark recorded this task as qualified."};
  if (task.status === "not_qualified") return {label: "Not qualified", style: "draft", description: "The benchmark recorded that this task is not qualified."};
  return {label: "Qualification unknown", style: "unknown", description: "The task has no recorded qualification decision."};
}

function artifactState(task: ExplorerTask, id: ArtifactName): ArtifactState {
  const source = task.authoring?.artifacts[id];
  if (source) return source.status;
  if (id === "metadata") return "unavailable";
  if (id === "verifier" && task.verifier === undefined) return "unavailable";
  return task[id] ? "present" : "missing";
}

function sourceAction(doc: DocumentEvidence, actions: AuthoringActions): HTMLElement {
  const source = el("div", "authoring-source", el("span", "mono", doc.path));
  if (actions.canOpen) {
    const open = button("Open source ↗", "text-button", () => actions.open({path: doc.path}));
    open.setAttribute("aria-label", `Open source ${doc.path}`);
    source.append(open);
  }
  return source;
}

function relativeEvidence(source: string, target: string): Evidence | undefined {
  if (target.startsWith("/") || target.startsWith("#") || target.includes(":") || target.includes("?")) return undefined;
  const [relative, anchor] = target.split("#", 2);
  if (!relative) return undefined;
  const directories = source.split("/");
  directories.pop();
  for (const part of relative.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (!directories.length) return undefined;
      directories.pop();
    } else directories.push(part);
  }
  const path = directories.join("/");
  if (!path) return undefined;
  const match = /^L([1-9][0-9]*)(?:-L[1-9][0-9]*)?$/.exec(anchor ?? "");
  if (!match?.[1]) return {path};
  const line = Number(match[1]);
  return Number.isSafeInteger(line) ? {path, line} : {path};
}

function documentMarkdown(doc: DocumentEvidence, actions: AuthoringActions): HTMLElement {
  const output = markdown(doc.content);
  const textNodes: ChildNode[] = [];
  function collect(parent: Node): void {
    for (const node of parent.childNodes) {
      if (node.nodeType === 3) textNodes.push(node);
      else if (node.nodeName !== "CODE" && node.nodeName !== "PRE") collect(node);
    }
  }
  collect(output);
  for (const node of textNodes) {
    const source = node.textContent ?? "";
    const matches = [...source.matchAll(/\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g)];
    if (!matches.length) continue;
    const content = document.createDocumentFragment();
    let offset = 0;
    for (const match of matches) {
      const [full, label, target] = match;
      if (match.index === undefined || !label || !target) continue;
      content.append(source.slice(offset, match.index));
      const evidence = relativeEvidence(doc.path, target);
      if (!evidence) content.append(full);
      else if (!actions.canOpen) content.append(label);
      else {
        const link = button(label, "text-button authoring-inline-source", () => actions.open(evidence));
        link.setAttribute("aria-label", `Open source ${label}`);
        link.title = evidence.path;
        content.append(link);
      }
      offset = match.index + full.length;
    }
    content.append(source.slice(offset));
    node.replaceWith(content);
  }
  return output;
}

function missingDocument(task: ExplorerTask, id: ArtifactName, title: string, guidance: string): HTMLElement {
  const status = artifactState(task, id);
  const artifact = task.authoring?.artifacts[id];
  const detail = status === "invalid" ? `The file exists but could not be read as valid content. ${guidance}` : guidance;
  const notice = empty(status === "invalid" ? `${title} is invalid` : `${title} is not available yet`, detail);
  if (artifact) notice.append(el("p", "mono authoring-expected-path", `Expected: ${artifact.path}`));
  return notice;
}

function documentPane(task: ExplorerTask, pane: "instruction" | "rubric", actions: AuthoringActions): HTMLElement {
  const learner = pane === "instruction";
  const title = learner ? "What the learner is asked to do" : "What the evaluator will grade";
  const description = learner ? "Visible to the learner. It should read like a real engineering request." : "Private to benchmark curators. Keep scoring and hidden controls separate from the learner request.";
  const selected = el("section", "authoring-content", el("p", learner ? "authoring-audience" : "authoring-audience authoring-audience--private", learner ? "LEARNER-VISIBLE" : "CURATOR-ONLY"), el("h2", undefined, title), el("p", "muted authoring-description", description));
  const doc = task[pane];
  if (doc) selected.append(sourceAction(doc, actions), documentMarkdown(doc, actions));
  else selected.append(missingDocument(task, pane, learner ? "Learner request" : "Private rubric", learner ? "Add a realistic request and the essential constraints. The panel can show it before the task is complete." : "Add the observable requirements, scoring, valid alternatives and negative controls. A missing rubric is not evidence that the task is ready."));
  return selected;
}

function evaluationPane(task: ExplorerTask, actions: AuthoringActions): HTMLElement {
  const selected = el("section", "authoring-content", el("p", "authoring-audience authoring-audience--private", "CURATOR-ONLY"), el("h2", undefined, "How this task is evaluated"), el("p", "muted authoring-description", "Inspect how the verifier checks the change and what qualification evidence is recorded."));
  const gates = el("dl", "authoring-gates",
    el("div", undefined, el("dt", undefined, "01 · Functional behavior"), el("dd", undefined, "The task's deterministic verifier checks the behavior described in the learner request and private rubric.")),
    el("div", undefined, el("dt", undefined, "02 · Engineering quality"), el("dd", undefined, "For code-changing tasks, Lighthouse policy calls for Sol Medium to grade quality and Astra Medium to review Sol failures. File presence alone does not show that this is configured or passing.")));
  selected.append(gates);

  const verifier = el("section", "authoring-material", el("h3", undefined, "Functional verifier"));
  if (task.verifier) {
    const disclosure = el("details", "authoring-verifier", el("summary", undefined, "Read verifier source"), el("pre", undefined, el("code", undefined, task.verifier.content)));
    verifier.append(sourceAction(task.verifier, actions), disclosure);
  } else verifier.append(missingDocument(task, "verifier", "Verifier", "Until the entry point is available, this panel cannot show the actual functional checks."));
  selected.append(verifier);

  const qualification = el("section", "authoring-material", el("h3", undefined, "Qualification evidence"), el("p", "muted authoring-description", "A qualification file is evidence to read; its presence by itself does not mean the task qualified."));
  if (task.qualification) qualification.append(sourceAction(task.qualification, actions), documentMarkdown(task.qualification, actions));
  else qualification.append(missingDocument(task, "qualification", "Qualification evidence", "Record reference and alternative solutions, negative controls, and reviewer decisions here as the evaluator is challenged."));
  selected.append(qualification);
  return selected;
}

function artifactChecklist(task: ExplorerTask): HTMLElement {
  const present = artifacts.filter((artifact) => artifactState(task, artifact.id) === "present").length;
  const section = el("section", "authoring-checklist", el("div", "authoring-checklist__heading", el("h2", undefined, "Authoring checklist"), el("span", "authoring-checklist__count", `${present} of ${artifacts.length} files present`)), el("p", "muted authoring-description", "Availability is shown separately from whether a check passed."));
  const list = el("ul", "authoring-artifacts");
  for (const artifact of artifacts) {
    const state = artifactState(task, artifact.id);
    const indicator = el("span", `authoring-artifact__mark authoring-artifact__mark--${state}`, state === "present" ? "✓" : state === "invalid" ? "!" : "–");
    indicator.setAttribute("aria-hidden", "true");
    const label = el("span", "authoring-artifact__label", el("span", undefined, artifact.label), el("small", undefined, artifact.description));
    list.append(el("li", "authoring-artifact", indicator, label, el("span", `authoring-artifact__status authoring-artifact__status--${state}`, statusText[state])));
  }
  section.append(list);
  return section;
}

function taskCatalog(all: ExplorerTask[], actions: AuthoringActions): HTMLElement {
  const publishedTasks = all.filter(published);
  const drafts = all.filter(draft);
  const reviews = all.filter(reviewOnly);
  const counts = el("dl", "authoring-catalog__counts");
  for (const [label, count] of [["Published tasks", publishedTasks.length], ["Qualified", publishedTasks.filter((task) => task.status === "qualified").length], ["Retained drafts", drafts.length], ["Review only", reviews.length]] satisfies [string, number][]) {
    counts.append(el("div", undefined, el("dt", undefined, label), el("dd", undefined, String(count))));
  }
  const heading = el("h2", undefined, "Tasks in this benchmark");
  heading.id = "authoring-catalog";
  heading.tabIndex = -1;
  const section = el("section", "authoring-catalog", heading, el("p", "muted authoring-description", "Qualified tasks are part of the published count. Retained drafts and review-only records are listed separately; they may be historical."), counts);
  section.setAttribute("aria-labelledby", heading.id);
  if (!all.length) {
    section.append(empty("No task drafts or published tasks found", "Start a Harbor task or create its curator rubric. When files are saved, refresh this panel to read them alongside the conversation."));
    return section;
  }

  for (const [label, entries] of [["Published tasks", publishedTasks], ["Retained drafts", drafts], ["Curator reviews", reviews]] satisfies [string, ExplorerTask[]][]) {
    if (!entries.length) continue;
    const list = el("ul", "authoring-catalog__list");
    const group = el("section", "authoring-catalog__group", el("h3", undefined, label), list);
    for (const task of entries) {
      const state = phase(task);
      const open = button("", "authoring-catalog__task", () => {
        actions.selectTask(task.id);
        document.getElementById("authoring-task-title")?.focus();
      });
      open.setAttribute("aria-label", `Open ${taskTitle(task)}`);
      open.append(el("span", "authoring-catalog__task-label", el("span", undefined, taskTitle(task)), el("small", "mono muted", task.id)), el("span", `authoring-phase authoring-phase--${state.style}`, state.label));
      list.append(el("li", undefined, open));
    }
    section.append(group);
  }
  return section;
}

function taskPicker(all: ExplorerTask[], active: ExplorerTask | undefined, actions: AuthoringActions): HTMLElement {
  const label = el("label", "authoring-picker__label", "Task");
  label.setAttribute("for", "authoring-task");
  const control = el("select", "filter authoring-picker__select");
  control.id = "authoring-task";
  if (!active) {
    const placeholder = el("option", undefined, "Choose a task");
    placeholder.value = "";
    placeholder.selected = true;
    placeholder.disabled = true;
    control.append(placeholder);
  }
  const groups = [{label: "Drafts", matches: draft}, {label: "Curator reviews", matches: reviewOnly}, {label: "Published tasks", matches: published}];
  for (const group of groups) {
    const choices = all.filter(group.matches);
    if (!choices.length) continue;
    const optgroup = el("optgroup");
    optgroup.setAttribute("label", group.label);
    for (const task of choices) {
      const option = el("option", undefined, taskTitle(task));
      option.value = task.id;
      if (task.id === active?.id) option.selected = true;
      optgroup.append(option);
    }
    control.append(optgroup);
  }
  control.addEventListener("change", () => {
    if (!all.some((task) => task.id === control.value)) return;
    actions.selectTask(control.value);
    document.getElementById("authoring-task")?.focus();
  });
  return el("div", "authoring-picker", label, control);
}

function navigation(pane: AuthoringPane, actions: AuthoringActions): HTMLElement {
  const nav = el("nav", "authoring-navigation");
  nav.setAttribute("aria-label", "Task authoring materials");
  function activate(id: AuthoringPane): void {
    actions.selectPane(id);
    document.getElementById(`authoring-pane-${id}`)?.focus();
  }
  for (const [index, item] of panes.entries()) {
    const tab = button(item.label, "authoring-navigation__button", () => activate(item.id));
    tab.id = `authoring-pane-${item.id}`;
    if (item.id === pane) tab.setAttribute("aria-current", "page");
    tab.addEventListener("keydown", (event) => {
      const key = event.key;
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(key)) return;
      event.preventDefault();
      const nextIndex = key === "Home" ? 0 : key === "End" ? panes.length - 1 : (index + (key === "ArrowRight" ? 1 : -1) + panes.length) % panes.length;
      const next = panes[nextIndex];
      if (!next) return;
      activate(next.id);
    });
    nav.append(tab);
  }
  return nav;
}

export function renderAuthoring(snapshot: ExplorerSnapshot, selectedTaskId: string | undefined, selectedPane: AuthoringPane, actions: AuthoringActions): HTMLElement {
  const workspace = el("main", "authoring");
  workspace.setAttribute("aria-label", selectedTaskId ? "Lighthouse task authoring" : "Lighthouse benchmark tasks");
  const header = el("header", "authoring-header", el("div", undefined, el("p", "eyebrow", selectedTaskId ? "Lighthouse · Task authoring" : "Lighthouse · Task catalog"), el("h1", undefined, selectedTaskId ? "What we’re evaluating" : "Benchmark tasks"), el("p", "muted", snapshot.benchmark.name)));
  if (actions.refresh) {
    const refresh = button("↻ Refresh", "refresh authoring-refresh", actions.refresh);
    refresh.setAttribute("aria-label", "Refresh authoring materials");
    header.append(refresh);
  }
  workspace.append(header);
  if (selectedTaskId === undefined) {
    workspace.append(taskCatalog(snapshot.tasks, actions));
    return workspace;
  }

  if (actions.showCatalog) {
    const showCatalog = actions.showCatalog;
    workspace.append(button("← All benchmark tasks", "text-button authoring-catalog-back", () => {
      showCatalog();
      document.getElementById("authoring-catalog")?.focus();
    }));
  }
  const active = snapshot.tasks.find((task) => task.id === selectedTaskId);
  if (snapshot.tasks.length) workspace.append(taskPicker(snapshot.tasks, active, actions));
  if (!active) {
    workspace.append(empty("This task is no longer available", "Choose another draft or published task. No other task's private materials have been opened for you."));
    return workspace;
  }

  const state = phase(active);
  const heading = el("h2", undefined, taskTitle(active));
  heading.id = "authoring-task-title";
  heading.tabIndex = -1;
  const taskHeader = el("div", "authoring-task", el("div", undefined, heading, el("p", "mono muted", active.id)), el("span", `authoring-phase authoring-phase--${state.style}`, state.label));
  workspace.append(taskHeader, el("p", "authoring-task-description muted", state.description));
  if (active.summary && active.summary !== active.title) workspace.append(el("p", "authoring-task-summary", active.summary));
  workspace.append(navigation(selectedPane, actions), selectedPane === "evaluation" ? evaluationPane(active, actions) : documentPane(active, selectedPane, actions), artifactChecklist(active));
  return workspace;
}
