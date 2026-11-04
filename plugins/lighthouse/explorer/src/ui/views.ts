import type { DocumentEvidence, Evidence, ExplorerSnapshot, ExplorerTask, Gate, Judgment, QualityReview, Rate, RubricMetrics, Trial } from "./contracts.ts";
import { badge, button, el, empty, humanize, labelFor, markdown, rateDetail, rateValue, table } from "./elements.ts";

export type Screen = "overview" | "tasks" | "leaderboard" | "reviews";
export type TaskPane = "instruction" | "rubric" | "qualification";

export interface Actions {
  go: (screen: Screen, id?: string) => void;
  taskPane: (pane: TaskPane) => void;
  open: (evidence: Evidence) => void;
  canOpen: boolean;
}

function stat(title: string, rate: Rate, description: string, feature = false): HTMLElement {
  const card = el("section", feature ? "stat stat--feature" : "stat");
  card.append(el("p", "stat__title", title), el("p", "stat__value", rateValue(rate)), el("p", "stat__denominator", rateDetail(rate)), el("p", "stat__description", description));
  if (rate.unknown !== undefined && rate.unknown > 0) card.append(el("p", "stat__unknown", `${rate.unknown} unresolved or unavailable`));
  return card;
}

export function metrics(all: RubricMetrics): HTMLElement {
  return el("div", "stats", stat("Overall benchmark pass", all.overall, "Requires both functionality and engineering quality.", true), stat("Functional pass", all.functional, "Deterministic functional checks on eligible submissions."), stat("Engineering quality pass", all.quality, "Measured quality decisions; coverage is shown separately."));
}

function heading(title: string, description: string): HTMLElement {
  return el("div", "section-heading", el("h2", undefined, title), el("p", "muted", description));
}

function taskTitle(task: ExplorerTask): string { return task.title || humanize(task.id); }

function taskBadge(status?: string): HTMLElement {
  const label = status ? humanize(status) : "Status unavailable";
  return el("span", status?.toLowerCase().includes("qualif") ? "task-state task-state--qualified" : "task-state", label);
}

function taskButton(task: ExplorerTask, actions: Actions): HTMLElement {
  const item = button(taskTitle(task), "text-button", () => actions.go("tasks", task.id));
  return el("div", "primary-cell", item, el("span", "mono muted", task.id));
}

function qualityButton(trial: Trial, actions: Actions): HTMLElement {
  if (!trial.reviewId) return badge(trial.quality);
  const item = button("", "gate-button", () => actions.go("reviews", trial.reviewId));
  item.setAttribute("aria-label", `${labelFor(trial.quality)}: open quality review for ${trial.taskId}`);
  item.append(badge(trial.quality));
  return item;
}

function trialRows(trials: Trial[], actions: Actions): HTMLElement[][] {
  return trials.map((trial) => {
    const title = button(trial.taskId, "text-button", () => actions.go("tasks", trial.taskId));
    const detail = el("div", "primary-cell", title, el("span", "mono muted", trial.id));
    if (trial.error) detail.append(el("span", "trial-error", trial.error));
    return [detail, el("span", "model", trial.model), badge(trial.functional), qualityButton(trial, actions), badge(trial.overall)];
  });
}

function shortlist(snapshot: ExplorerSnapshot, actions: Actions): HTMLElement {
  const section = el("section", "panel", heading("Task catalog", "The request, evaluator rubric, and qualification evidence stay separate."));
  if (!snapshot.tasks.length) section.append(empty("No tasks found", "This benchmark has no discoverable Harbor tasks yet."));
  else {
    const list = el("div", "preview-list");
    for (const task of snapshot.tasks.slice(0, 4)) list.append(el("div", "preview-row", taskButton(task, actions), taskBadge(task.status)));
    section.append(list);
  }
  section.append(button("Browse all tasks →", "text-button text-button--more", () => actions.go("tasks")));
  return section;
}

function reviewPriority(snapshot: ExplorerSnapshot, actions: Actions): HTMLElement {
  const count = snapshot.reviews.filter((review) => review.status === "review_required").length;
  const failures = snapshot.reviews.filter((review) => review.status === "fail").length;
  const unmeasured = snapshot.reviews.filter((review) => review.status === "unmeasured" || review.status === "unknown").length;
  const section = el("section", "panel", heading("Quality review queue", "Sol Medium grades; Astra Medium independently checks Sol failures."));
  section.append(el("div", "queue-summary", el("div", undefined, el("span", "queue-summary__number", String(count)), el("span", "muted", " need human review")), el("div", undefined, el("span", "queue-summary__number", String(failures)), el("span", "muted", " confirmed failures")), el("div", undefined, el("span", "queue-summary__number", String(unmeasured)), el("span", "muted", " unmeasured"))));
  if (!snapshot.reviews.length || unmeasured === snapshot.reviews.length) section.append(el("p", "muted", "No compatible quality decisions were discovered. Missing evidence does not mean quality passed."));
  section.append(button("Open review queue →", "text-button text-button--more", () => actions.go("reviews")));
  return section;
}

export function overview(snapshot: ExplorerSnapshot, actions: Actions): HTMLElement {
  const section = el("section", "view", heading("Benchmark overview", snapshot.benchmark.summary || "See what has been tested, how submissions were graded, and what still needs evidence."), metrics(snapshot.leaderboard.metrics));
  const defaultCohort = snapshot.leaderboard.cohorts?.[0];
  if (defaultCohort && (snapshot.leaderboard.cohorts?.length ?? 0) > 1) section.append(el("aside", "notice", el("h3", undefined, "Selected comparison"), el("p", undefined, `Overview rates describe ${defaultCohort.label}. Choose another compatible comparison in the leaderboard; results are not blended across comparisons.`)));
  section.append(el("div", "two-up", shortlist(snapshot, actions), reviewPriority(snapshot, actions)));
  const recent = el("section", "panel", heading("Recorded submissions", "Original submissions only; a saved regrade is attached to its source trial."));
  if (snapshot.leaderboard.trials.length) recent.append(table(["Task / original trial", "Model", "Functional", "Quality", "Overall"], trialRows(snapshot.leaderboard.trials.slice(0, 5), actions)));
  else recent.append(empty("No submission data", "The launcher can still browse tasks and rubrics. Raw jobs may be local and unavailable in a fresh clone."));
  recent.append(button("Open leaderboard →", "text-button text-button--more", () => actions.go("leaderboard")));
  section.append(recent);
  if (snapshot.warnings?.length) {
    const warnings = el("ul", "warnings-list");
    for (const warning of snapshot.warnings) warnings.append(el("li", undefined, warning));
    section.append(el("aside", "notice", el("h3", undefined, "Evidence notes"), warnings));
  }
  return section;
}

const panes: {id: TaskPane; label: string; description: string}[] = [
  { id: "instruction", label: "Learner request", description: "What the learner receives. Curator and evaluator materials are not included in this view." },
  { id: "rubric", label: "Curator rubric", description: "Evaluator and scoring material for benchmark curators. It is separate from the learner request." },
  { id: "qualification", label: "Qualification", description: "Retained evaluator qualification, controls, and review evidence when available." },
];

function documentContent(doc: DocumentEvidence | null, description: string, actions: Actions): HTMLElement {
  const section = el("section", "document", el("p", "document__description", description));
  if (!doc) {
    section.append(empty("This document was not found", "Missing material is shown as unavailable; it is not interpreted as a failed or passed check."));
    return section;
  }
  const path = el("span", "mono document__path", doc.path);
  const metadata = el("div", "document__metadata", path);
  if (actions.canOpen) metadata.append(button("Open source ↗", "text-button", () => actions.open({ path: doc.path })));
  section.append(metadata, markdown(doc.content));
  return section;
}

function matches(value: string, query: string): boolean { return value.toLowerCase().includes(query.trim().toLowerCase()); }

function search(label: string, value: string, update: (query: string) => void): HTMLInputElement {
  const field = el("input", "search");
  field.type = "search";
  field.placeholder = label;
  field.setAttribute("aria-label", label);
  field.value = value;
  field.addEventListener("input", () => update(field.value));
  return field;
}

export function tasks(snapshot: ExplorerSnapshot, selected: string | undefined, pane: TaskPane, initialQuery: string, setQuery: (query: string) => void, actions: Actions): HTMLElement {
  const section = el("section", "view", heading("Tasks & rubrics", "Browse the realistic learner request alongside clearly marked curator material."));
  const list = el("div", "task-list");
  const renderList = (query: string): void => {
    setQuery(query);
    list.replaceChildren();
    const filtered = snapshot.tasks.filter((task) => matches(`${task.id} ${task.title ?? ""} ${task.status ?? ""}`, query));
    if (!filtered.length) list.append(empty("No matching tasks", "Try a different task ID, title, or qualification state."));
    for (const task of filtered) {
      const item = button("", "task-item", () => actions.go("tasks", task.id));
      if (task.id === selected) item.setAttribute("aria-current", "true");
      item.append(el("span", "task-item__title", taskTitle(task)), el("span", "mono muted", task.id), taskBadge(task.status));
      list.append(item);
    }
  };
  renderList(initialQuery);
  const aside = el("aside", "task-sidebar", search("Filter tasks", initialQuery, renderList), el("p", "list-caption", `${snapshot.tasks.length} ${snapshot.tasks.length === 1 ? "task" : "tasks"} in this repository`), list);
  const selectedTask = snapshot.tasks.find((task) => task.id === selected);
  const detail = el("section", "panel task-detail");
  if (!selectedTask) detail.append(empty(snapshot.tasks.length ? "Select a task" : "No tasks found", "Choose a task to inspect its request, rubric, and qualification evidence."));
  else {
    detail.append(el("div", "task-detail__heading", el("div", undefined, el("p", "eyebrow", "Harbor task"), el("h3", undefined, taskTitle(selectedTask)), el("p", "mono muted", selectedTask.id)), taskBadge(selectedTask.status)));
    if (selectedTask.summary) detail.append(el("p", "muted task-summary", selectedTask.summary));
    const tabs = el("nav", "subnav");
    tabs.setAttribute("aria-label", "Task materials");
    for (const item of panes) {
      const tab = button(item.label, "subnav__button", () => actions.taskPane(item.id));
      if (item.id === pane) tab.setAttribute("aria-current", "page");
      tabs.append(tab);
    }
    detail.append(tabs, documentContent(selectedTask[pane], panes.find((item) => item.id === pane)?.description ?? "", actions));
  }
  section.append(el("div", "split", aside, detail));
  return section;
}

function gateFilter(value: Gate | "all", label: string, update: (choice: Gate | "all") => void): HTMLSelectElement {
  const select = el("select", "filter");
  select.setAttribute("aria-label", label);
  const options: (Gate | "all")[] = ["all", "pass", "fail", "review_required", "unmeasured", "unknown"];
  for (const choice of options) {
    const option = el("option", undefined, choice === "all" ? label : labelFor(choice));
    option.value = choice;
    if (choice === value) option.selected = true;
    select.append(option);
  }
  select.addEventListener("change", () => {
    const chosen = options.find((option) => option === select.value);
    if (chosen) update(chosen);
  });
  return select;
}

export function leaderboard(snapshot: ExplorerSnapshot, query: string, filter: Gate | "all", cohortId: string | undefined, setFilters: (query: string, filter: Gate | "all") => void, setCohort: (cohortId: string) => void, actions: Actions): HTMLElement {
  const cohorts = snapshot.leaderboard.cohorts ?? [];
  const active = cohorts.find((cohort) => cohort.id === cohortId) ?? cohorts[0];
  const section = el("section", "view", heading("Leaderboard", "The overall rate uses functionally measured original submissions, including submissions still awaiting quality."));
  if (cohorts.length) {
    const select = el("select", "filter");
    select.id = "comparison-cohort";
    for (const cohort of cohorts) {
      const option = el("option", undefined, cohort.label);
      option.value = cohort.id;
      if (cohort.id === active?.id) option.selected = true;
      select.append(option);
    }
    select.addEventListener("change", () => {
      if (cohorts.some((cohort) => cohort.id === select.value)) setCohort(select.value);
    });
    const label = el("label", "cohort__label", "Compatible comparison");
    label.htmlFor = select.id;
    const meta = el("div", "cohort", label, select, el("p", "muted", "Submissions from different arms or benchmark versions are not blended."));
    if (active?.revision) meta.append(el("p", "mono muted", active.revision));
    section.append(meta);
  }
  section.append(metrics(active?.metrics ?? snapshot.leaderboard.metrics));
  const results = el("div");
  let currentQuery = query;
  let currentFilter = filter;
  const renderRows = (): void => {
    setFilters(currentQuery, currentFilter);
    const trials = (active?.trials ?? snapshot.leaderboard.trials).filter((trial) => matches(`${trial.id} ${trial.taskId} ${trial.model}`, currentQuery) && (currentFilter === "all" || trial.overall === currentFilter));
    results.replaceChildren(trials.length ? table(["Task / original trial", "Model", "Functional", "Quality", "Overall"], trialRows(trials, actions)) : empty("No matching submissions", "No original trial matches these filters, or compatible job artifacts are unavailable."));
  };
  renderRows();
  const tools = el("div", "filters", search("Filter by task, model or trial", query, (value) => { currentQuery = value; renderRows(); }), gateFilter(filter, "All overall outcomes", (value) => { currentFilter = value; renderRows(); }));
  section.append(el("section", "panel", heading("Original submissions", "A missing or disputed quality decision cannot make a submission pass. Grading infrastructure errors remain visible."), tools, results), el("aside", "notice", el("h3", undefined, "How to read the rates"), el("p", undefined, "Functionality and quality are separate gates. Engineering quality uses only measured decisions; overall includes eligible submissions awaiting quality. No functional measurement means the submission is excluded from that headline denominator. There is no weighted average.")));
  return section;
}

function evidenceItem(item: Evidence, actions: Actions): HTMLElement {
  const label = item.label ?? `${item.path}${item.line ? `:${item.line}` : ""}`;
  return actions.canOpen ? button(label, "text-button evidence", () => actions.open(item)) : el("span", "mono evidence", label);
}

function judge(label: string, decision: Judgment | undefined, actions: Actions): HTMLElement {
  const card = el("section", "judge", el("p", "judge__label", label));
  if (!decision) { card.append(el("p", "muted", "No decision recorded")); return card; }
  card.append(badge(decision.decision), el("p", "judge__reason", decision.reason || "No reason was recorded."));
  for (const evidence of decision.evidence ?? []) card.append(evidenceItem(evidence, actions));
  return card;
}

function reviewDetail(review: QualityReview | undefined, actions: Actions): HTMLElement {
  const detail = el("section", "panel task-detail");
  if (!review) { detail.append(empty("Select a quality review", "Judge reasoning and evidence will appear here when a compatible artifact is available.")); return detail; }
  detail.append(el("div", "task-detail__heading", el("div", undefined, el("p", "eyebrow", "Original trial quality review"), el("h3", undefined, review.taskId), el("p", "mono muted", review.trialId), el("p", "muted", review.model)), badge(review.status)));
  if (review.status === "review_required") detail.append(el("aside", "notice notice--review", el("h4", undefined, "Human review required"), el("p", undefined, "Sol and Astra have not produced a compatible final decision. This submission does not count as a pass while the disagreement is unresolved.")));
  if (review.status === "unmeasured" || review.status === "unknown") detail.append(el("aside", "notice", el("h4", undefined, "No measured quality outcome"), el("p", undefined, "No compatible completed quality outcome was found for this submission. Missing or unavailable evidence does not count as a quality pass.")));
  if (!review.dimensions.length) detail.append(empty("No dimension evidence available", "The report may be absent or incompatible with this benchmark’s complete rubric."));
  for (const dimension of review.dimensions) {
    const judges = el("div", "judges", judge("Sol Medium · initial review", dimension.sol, actions), judge("Astra Medium · independent review", dimension.astra, actions));
    if (dimension.adjudication) judges.append(judge("Human adjudication", dimension.adjudication, actions));
    detail.append(el("section", "dimension", el("div", "dimension__heading", el("h4", undefined, dimension.label || humanize(dimension.id)), badge(dimension.decision)), judges));
  }
  if (review.evidence?.length) {
    const links = el("div", "evidence-list");
    for (const evidence of review.evidence) links.append(evidenceItem(evidence, actions));
    detail.append(el("section", "dimension", el("h4", undefined, "Source evidence"), links));
  }
  return detail;
}

export function reviews(snapshot: ExplorerSnapshot, selected: string | undefined, filter: Gate | "all", setFilter: (filter: Gate | "all") => void, actions: Actions): HTMLElement {
  const section = el("section", "view", heading("Quality review queue", "Read Sol and Astra’s evidence without treating a disagreement or missing judgment as a pass."));
  const list = el("div", "task-list");
  const ordered = [...snapshot.reviews].sort((left, right) => {
    const priority: Record<Gate, number> = { review_required: 0, fail: 1, unmeasured: 2, unknown: 3, pass: 4 };
    return priority[left.status] - priority[right.status];
  });
  const renderList = (current: Gate | "all"): void => {
    setFilter(current);
    const items = ordered.filter((review) => current === "all" || review.status === current);
    list.replaceChildren();
    if (!items.length) list.append(empty("No matching quality reviews", "Quality artifacts may not exist or may not match original trial identities. Missing artifacts are not passes."));
    for (const review of items) {
      const item = button("", "task-item", () => actions.go("reviews", review.id));
      if (review.id === selected) item.setAttribute("aria-current", "true");
      item.append(el("span", "task-item__title", review.taskId), el("span", "muted", review.model), badge(review.status));
      list.append(item);
    }
  };
  renderList(filter);
  const aside = el("aside", "task-sidebar", gateFilter(filter, "All quality outcomes", renderList), el("p", "list-caption", `${snapshot.reviews.length} original ${snapshot.reviews.length === 1 ? "submission" : "submissions"} tracked`), list);
  section.append(el("div", "split", aside, reviewDetail(snapshot.reviews.find((review) => review.id === selected), actions)));
  return section;
}
