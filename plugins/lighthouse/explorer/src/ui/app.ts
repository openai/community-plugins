import type { BenchmarkPreference, Evidence, ExplorerAdapter, ExplorerLibrary, ExplorerLibraryAdapter, ExplorerSnapshot, Gate, LibraryBenchmark } from "./contracts.ts";
import { renderAuthoring, type AuthoringPane } from "./authoring.ts";
import { button, el, empty } from "./elements.ts";
import type { ExplorerNavigation, ExplorerRoute, ExplorerView } from "./navigation.ts";
import { leaderboard, overview, reviews, tasks, type Actions, type Screen, type TaskPane } from "./views.ts";

const navigation: {id: Screen; label: string}[] = [
  { id: "overview", label: "Overview" },
  { id: "tasks", label: "Tasks & rubrics" },
  { id: "leaderboard", label: "Leaderboard" },
  { id: "reviews", label: "Quality reviews" },
];

type LoadState = {kind: "loading"} | {kind: "ready"; snapshot: ExplorerSnapshot} | {kind: "error"; message: string};
type LibraryState = {kind: "loading"} | {kind: "ready"; library: ExplorerLibrary} | {kind: "error"; message: string};
type MountedView = {destroy: () => void};
type Surface = "dashboard" | "authoring";
interface InitialFocus {benchmarkId: string; taskId?: string; pane?: AuthoringPane}
interface ExplorerOptions {switchBenchmark?: () => void; onReady?: () => void; navigation?: ExplorerNavigation; benchmarkId?: string; surface?: Surface; initialTaskId?: string; initialPane?: AuthoringPane; pollMs?: number}
interface LibraryOptions {navigation?: ExplorerNavigation; surface?: Surface; initialFocus?: InitialFocus; pollMs?: number}

function failure(error: unknown): string {
  return error instanceof Error ? error.message : "An unknown error prevented this action.";
}

export function mountExplorer(root: HTMLElement, adapter: ExplorerAdapter, options: ExplorerOptions = {}): MountedView {
  let state: LoadState = {kind: "loading"};
  const authoringOnly = options.surface === "authoring";
  let screen: Screen | "authoring" = authoringOnly ? "authoring" : "overview";
  let taskPane: TaskPane = "instruction";
  let authoringPane: AuthoringPane = options.initialPane ?? "instruction";
  let selectedTask = options.initialTaskId;
  let selectedReview: string | undefined;
  let taskQuery = "";
  let leaderboardQuery = "";
  let leaderboardFilter: Gate | "all" = "all";
  let leaderboardCohort: string | undefined;
  let reviewFilter: Gate | "all" = "all";
  let generation = 0;
  let disposed = false;
  let loading = false;
  let transient = "";

  function applyView(view: ExplorerView): void {
    if (authoringOnly && view.section !== "authoring") return;
    screen = view.section;
    switch (view.section) {
      case "overview": break;
      case "tasks": selectedTask = view.taskId ?? (state.kind === "ready" ? state.snapshot.tasks[0]?.id : undefined); taskPane = view.pane ?? "instruction"; break;
      case "authoring": selectedTask = view.taskId; authoringPane = view.pane ?? "instruction"; break;
      case "leaderboard": leaderboardCohort = view.cohortId; break;
      case "reviews": selectedReview = view.reviewId ?? (state.kind === "ready" ? (state.snapshot.reviews.find((review) => review.status === "review_required")?.id ?? state.snapshot.reviews[0]?.id) : undefined); reviewFilter = "all"; break;
    }
  }

  function acceptRoute(route: ExplorerRoute): void {
    if (route.kind !== "benchmark" || route.benchmarkId !== options.benchmarkId) return;
    const before = selectedTask;
    applyView(route.view);
    render();
    if (authoringOnly && before !== selectedTask) void refresh();
  }

  const current = options.navigation?.current();
  if (current?.kind === "benchmark" && current.benchmarkId === options.benchmarkId) applyView(current.view);
  const unsubscribe = options.navigation?.subscribe(acceptRoute);

  function visit(view: ExplorerView): void {
    if (options.navigation && options.benchmarkId) {
      options.navigation.go({kind: "benchmark", benchmarkId: options.benchmarkId, view});
    } else {
      const before = selectedTask;
      applyView(view);
      render();
      if (authoringOnly && before !== selectedTask) void refresh();
    }
  }

  const changeScreen = (next: Screen, id?: string): void => {
    switch (next) {
      case "overview": visit({section: next}); break;
      case "tasks": visit({section: next, ...(id ? {taskId: id} : {})}); break;
      case "leaderboard": visit({section: next}); break;
      case "reviews": visit({section: next, ...(id ? {reviewId: id} : {})}); break;
    }
    root.querySelector<HTMLElement>("main h2")?.focus();
  };

  const open = (evidence: Evidence): void => {
    if (!adapter.openFile) return;
    void adapter.openFile(evidence).catch((error: unknown) => {
      transient = `Could not open source: ${failure(error)}`;
      render();
    });
  };

  const actions: Actions = {
    go: changeScreen,
    taskPane: (pane) => { visit({section: "tasks", ...(selectedTask ? {taskId: selectedTask} : {}), pane}); },
    open,
    canOpen: adapter.openFile !== undefined,
  };

  async function refresh(quiet = false): Promise<void> {
    if (quiet && loading) return;
    const activeGeneration = ++generation;
    if (!quiet) {
      loading = true;
      transient = "";
      render();
    }
    let changed = false;
    try {
      const snapshot = await adapter.load(authoringOnly ? selectedTask : undefined);
      if (disposed || activeGeneration !== generation) return;
      if (quiet && state.kind === "ready" && JSON.stringify(state.snapshot) === JSON.stringify(snapshot)) return;
      state = {kind: "ready", snapshot};
      changed = true;
      if (!authoringOnly && (!selectedTask || !snapshot.tasks.some((task) => task.id === selectedTask))) selectedTask = snapshot.tasks[0]?.id;
      if (!selectedReview || !snapshot.reviews.some((review) => review.id === selectedReview)) selectedReview = snapshot.reviews.find((review) => review.status === "review_required")?.id ?? snapshot.reviews[0]?.id;
      options.onReady?.();
    } catch (error: unknown) {
      if (disposed || activeGeneration !== generation) return;
      if (state.kind === "ready") {
        if (!quiet) transient = `Could not refresh: ${failure(error)}`;
      } else state = {kind: "error", message: failure(error)};
    } finally {
      if (!disposed && activeGeneration === generation) {
        loading = false;
        if (!quiet || changed) render();
      }
    }
  }

  function render(): void {
    if (disposed) return;
    const previousFocus = authoringOnly && root.contains(root.ownerDocument.activeElement) ? root.ownerDocument.activeElement?.id : undefined;
    const app = el("div", "explorer");
    if (authoringOnly) app.classList.add("explorer--authoring");
    const icon = el("span", "brand__icon", "◈");
    icon.setAttribute("aria-hidden", "true");
    const brand = el("div", "brand", icon, el("div", undefined, el("span", "brand__title", "Lighthouse"), el("span", "brand__subtitle", authoringOnly ? "Tasks & rubrics" : "Benchmark Explorer")));
    const refreshButton = button(loading ? "Refreshing…" : "↻ Refresh", "refresh", () => { void refresh(); });
    refreshButton.disabled = loading;
    const headerActions = el("div", "topbar__actions", el("span", "read-only", "Read only"));
    if (!authoringOnly || state.kind !== "ready") headerActions.append(refreshButton);
    if (options.switchBenchmark) {
      const switcher = button("Switch benchmark", "refresh switch-benchmark", options.switchBenchmark);
      headerActions.prepend(switcher);
    }
    const header = el("header", "topbar", brand, headerActions);
    app.append(header);
    if (state.kind === "ready" && !authoringOnly) {
      const benchmark = state.snapshot.benchmark;
      const title = el("div", "benchmark", el("p", "eyebrow", "Local Harbor benchmark"), el("h1", undefined, benchmark.name));
      const about = el("div", "benchmark__meta");
      if (benchmark.rootLabel) about.append(el("span", "mono", benchmark.rootLabel));
      if (benchmark.source) about.append(el("span", undefined, `Evidence manifest: ${benchmark.source}`));
      if (benchmark.status) about.append(el("span", undefined, benchmark.status));
      if (about.childElementCount) title.append(about);
      app.append(title);
      const nav = el("nav", "navigation");
      nav.setAttribute("aria-label", "Explorer sections");
      for (const item of navigation) {
        const tab = button(item.label, "navigation__button", () => changeScreen(item.id));
        if (item.id === screen) tab.setAttribute("aria-current", "page");
        nav.append(tab);
      }
      app.append(nav);
    }
    if (transient) {
      const warning = el("div", "notice notice--review", transient);
      warning.setAttribute("role", "alert");
      app.append(warning);
    }
    const main = authoringOnly ? el("div", "main") : el("main", "main");
    if (state.kind === "loading") {
      const pending = empty(authoringOnly ? "Opening Lighthouse tasks…" : "Opening benchmark…", authoringOnly ? (selectedTask ? "Reading the selected task and its evaluator files from this checkout." : "Reading the benchmark task catalog from this checkout.") : "Reading the launcher and the benchmark evidence available in this checkout.");
      pending.setAttribute("role", "status");
      main.append(pending);
    } else if (state.kind === "error") {
      const problem = empty("Could not open this benchmark", state.message);
      problem.setAttribute("role", "alert");
      problem.append(button("Try again", "refresh", () => { void refresh(); }));
      main.append(problem);
    } else {
      const snapshot = state.snapshot;
      switch (screen) {
        case "overview": main.append(overview(snapshot, actions)); break;
        case "tasks": main.append(tasks(snapshot, selectedTask, taskPane, taskQuery, (value) => { taskQuery = value; }, actions)); break;
        case "leaderboard": main.append(leaderboard(snapshot, leaderboardQuery, leaderboardFilter, leaderboardCohort, (query, filter) => { leaderboardQuery = query; leaderboardFilter = filter; }, (cohortId) => { visit({section: "leaderboard", ...(cohortId ? {cohortId} : {})}); root.querySelector<HTMLElement>("#comparison-cohort")?.focus(); }, actions)); break;
        case "reviews": main.append(reviews(snapshot, selectedReview, reviewFilter, (value) => { reviewFilter = value; }, actions)); break;
        case "authoring": main.append(renderAuthoring(snapshot, selectedTask, authoringPane, {
          selectTask: (id) => { visit({section: "authoring", taskId: id, pane: "instruction"}); },
          selectPane: (pane) => { visit({section: "authoring", ...(selectedTask ? {taskId: selectedTask} : {}), pane}); },
          showCatalog: () => { visit({section: "authoring"}); },
          open, canOpen: adapter.openFile !== undefined, refresh: () => { void refresh(); },
        })); break;
      }
      main.querySelector("h2")?.setAttribute("tabindex", "-1");
    }
    app.append(main, el("footer", "footer", "Built from repository evidence · Harbor owns execution and grading · Missing evidence is never a pass."));
    root.replaceChildren(app);
    if (previousFocus && authoringOnly) root.ownerDocument.getElementById(previousFocus)?.focus();
  }

  const interval = authoringOnly && options.pollMs ? globalThis.setInterval(() => {
    if (root.ownerDocument.visibilityState !== "hidden") void refresh(true);
  }, options.pollMs) : undefined;
  const visible = (): void => { if (root.ownerDocument.visibilityState !== "hidden") void refresh(true); };
  if (interval !== undefined) root.ownerDocument.addEventListener("visibilitychange", visible);
  void refresh();
  return {destroy: () => {
    disposed = true; generation += 1; unsubscribe?.();
    if (interval !== undefined) { globalThis.clearInterval(interval); root.ownerDocument.removeEventListener("visibilitychange", visible); }
    root.replaceChildren();
  }};
}

export function mountLibrary(root: HTMLElement, adapter: ExplorerLibraryAdapter, preference?: BenchmarkPreference, options: LibraryOptions = {}): MountedView {
  let state: LibraryState = {kind: "loading"};
  let active: MountedView | undefined;
  let activeId: string | undefined;
  let lastSelected = preference?.read();
  let pathValue = "";
  let formError = "";
  let pickerError = "";
  let pending: "path" | "picker" | undefined;
  let generation = 0;
  let disposed = false;

  function showLibrary(): void {
    active?.destroy();
    active = undefined;
    activeId = undefined;
    render();
    root.querySelector<HTMLElement>("h1")?.focus();
  }

  function back(): void {
    if (options.navigation) options.navigation.go({kind: "home"});
    else showLibrary();
  }

  function showBenchmark(id: string, focus?: InitialFocus): void {
    if (disposed) return;
    if (activeId === id) return;
    active?.destroy();
    activeId = id;
    let remembered = false;
    active = mountExplorer(root, adapter.benchmark(id), {switchBenchmark: back,
      ...(options.navigation ? {navigation: options.navigation} : {}), benchmarkId: id,
      ...(options.surface ? {surface: options.surface} : {}),
      ...(options.pollMs ? {pollMs: options.pollMs} : {}),
      ...(focus?.taskId ? {initialTaskId: focus.taskId} : {}),
      ...(focus?.pane ? {initialPane: focus.pane} : {}), onReady: () => {
      if (remembered) return;
      remembered = true;
      lastSelected = id;
      preference?.write(id);
    }});
  }

  function select(benchmark: LibraryBenchmark, replace = false): void {
    if (options.navigation) options.navigation.go({kind: "benchmark", benchmarkId: benchmark.id, view: {section: options.surface === "authoring" ? "authoring" : "overview"}}, {replace});
    if (activeId !== benchmark.id) showBenchmark(benchmark.id);
  }

  function acceptRoute(route: ExplorerRoute): void {
    if (route.kind === "home") showLibrary();
    else showBenchmark(route.benchmarkId, options.initialFocus?.benchmarkId === route.benchmarkId ? options.initialFocus : undefined);
  }

  const unsubscribe = options.navigation?.subscribe(acceptRoute);

  async function refresh(restorePreferred = false): Promise<void> {
    const activeGeneration = ++generation;
    state = {kind: "loading"};
    render();
    try {
      const library = await adapter.library();
      if (disposed || activeGeneration !== generation) return;
      state = {kind: "ready", library};
      if (restorePreferred) {
        const currentRoute = options.navigation?.current();
        if (currentRoute?.kind === "benchmark") { acceptRoute(currentRoute); return; }
        if (options.initialFocus) { showBenchmark(options.initialFocus.benchmarkId, options.initialFocus); return; }
        const preferredId = library.preferredId ?? lastSelected;
        const preferred = library.benchmarks.find((item) => item.id === preferredId);
        if (preferred && options.surface !== "authoring") { select(preferred); return; }
      }
    } catch (error: unknown) {
      if (disposed || activeGeneration !== generation) return;
      state = {kind: "error", message: failure(error)};
    }
    render();
  }

  function rememberAndSelect(benchmark: LibraryBenchmark): void {
    const benchmarks = state.kind === "ready" ? state.library.benchmarks : [];
    state = {kind: "ready", library: {benchmarks: [benchmark, ...benchmarks.filter((item) => item.id !== benchmark.id)]}};
    pathValue = "";
    select(benchmark);
  }

  async function choose(): Promise<void> {
    if (pending) return;
    pending = "picker";
    pickerError = "";
    render();
    try {
      const benchmark = await adapter.choose();
      if (disposed) return;
      if (benchmark) rememberAndSelect(benchmark);
    } catch (error: unknown) {
      if (disposed) return;
      pickerError = failure(error);
    } finally {
      if (!disposed) {
        pending = undefined;
        render();
        if (!active) root.querySelector<HTMLElement>("#choose-benchmark")?.focus();
      }
    }
  }

  async function attach(): Promise<void> {
    if (pending) return;
    const path = pathValue.trim();
    if (!path) { formError = "Enter the absolute path to a local benchmark folder or its benchmark.lighthouse file."; render(); root.querySelector<HTMLElement>("#benchmark-path")?.focus(); return; }
    pending = "path";
    formError = "";
    render();
    try {
      const benchmark = await adapter.attach(path);
      if (disposed) return;
      rememberAndSelect(benchmark);
    } catch (error: unknown) {
      if (disposed) return;
      formError = failure(error);
    } finally {
      if (!disposed) {
        pending = undefined;
        render();
        if (formError) root.querySelector<HTMLElement>("#benchmark-path")?.focus();
      }
    }
  }

  function render(): void {
    if (disposed || active) return;
    const app = el("div", "explorer library");
    const icon = el("span", "brand__icon", "◈");
    icon.setAttribute("aria-hidden", "true");
    const brand = el("div", "brand", icon, el("div", undefined, el("span", "brand__title", "Lighthouse"), el("span", "brand__subtitle", "Benchmark Explorer")));
    app.append(el("header", "topbar", brand, el("div", "topbar__actions", el("span", "read-only", "Read only"), el("span", "library__native", "In Codex"))));

    const heading = el("h1", undefined, "See how your agents really perform.");
    heading.tabIndex = -1;
    const hero = el("section", "library-hero", el("div", "library-hero__copy", el("p", "eyebrow", "Your evaluation workspace"), heading, el("p", "library-hero__description", "Open a local benchmark to explore its tasks, see how solutions were graded, and follow quality decisions back to the evidence.")));
    const signal = el("div", "library-signal");
    signal.setAttribute("aria-label", "Three distinct evaluation views");
    for (const [number, label, detail] of [["01", "Functionality", "Did it work?"], ["02", "Code quality", "Is it maintainable?"], ["03", "Overall", "Did it pass both?"]]) {
      signal.append(el("div", "library-signal__row", el("span", "library-signal__number", number), el("span", "library-signal__label", label), el("span", "library-signal__detail", detail)));
    }
    hero.append(signal);
    const main = el("main", "main library-main", hero);

    const workspace = el("div", "library-grid");
    const listHeading = el("div", "library-section__heading", el("div", undefined, el("h2", undefined, "Your benchmarks"), el("p", "muted", "Choose a checkout available on this computer.")));
    const reload = button("↻ Refresh", "text-button library__refresh", () => { void refresh(); });
    reload.setAttribute("aria-label", "Refresh benchmarks");
    listHeading.append(reload);
    const library = el("section", "library-section", listHeading);
    if (state.kind === "loading") {
      const pending = empty("Finding benchmarks…", "Checking the benchmark folders available in this Codex session.");
      pending.setAttribute("role", "status");
      library.append(pending);
    } else if (state.kind === "error") {
      const problem = empty("Could not load your benchmarks", state.message);
      problem.setAttribute("role", "alert");
      problem.append(button("Try again", "refresh", () => { void refresh(); }));
      library.append(problem);
    } else if (!state.library.benchmarks.length) {
      library.append(empty("No benchmarks found yet", "Open a benchmark from its local folder. It will appear here while this Explorer is running."));
    } else {
      const list = el("div", "library-list");
      for (const benchmark of state.library.benchmarks) {
        const item = button("", "library-benchmark", () => select(benchmark));
        item.setAttribute("aria-label", `Open ${benchmark.name}`);
        const mark = el("span", "library-benchmark__icon", "◈");
        mark.setAttribute("aria-hidden", "true");
        const detail = el("span", "library-benchmark__detail", el("span", "library-benchmark__name", benchmark.name), el("span", "mono", benchmark.rootLabel));
        item.append(mark, detail);
        if (benchmark.id === lastSelected) item.append(el("span", "library-benchmark__recent", "Last opened"));
        const arrow = el("span", "library-benchmark__arrow", "→");
        arrow.setAttribute("aria-hidden", "true");
        item.append(arrow);
        list.append(item);
      }
      library.append(list);
    }
    workspace.append(library);

    const chooser = button(pending === "picker" ? "Opening folder picker…" : "Choose benchmark folder", "library-form__submit library-connect__chooser", () => { void choose(); });
    chooser.id = "choose-benchmark";
    chooser.disabled = pending !== undefined;
    const connect = el("section", "library-connect", el("p", "eyebrow", "Another checkout?"), el("h2", undefined, "Open from this computer"), el("p", "muted", "Choose an existing Harbor benchmark folder. Lighthouse reads its evidence without changing benchmark files."), chooser);
    if (pickerError) {
      const problem = el("p", "library-form__error", `${pickerError} You can also enter a path below.`);
      problem.setAttribute("role", "alert");
      connect.append(problem);
    }

    const fieldLabel = el("label", "library-form__label", "Local benchmark path");
    fieldLabel.htmlFor = "benchmark-path";
    const field = el("input", "search library-form__input");
    field.type = "text";
    field.id = "benchmark-path";
    field.name = "benchmark-path";
    field.placeholder = "/path/to/my-bench";
    field.autocomplete = "off";
    field.spellcheck = false;
    field.value = pathValue;
    field.setAttribute("aria-describedby", formError ? "benchmark-path-help benchmark-path-error" : "benchmark-path-help");
    if (formError) field.setAttribute("aria-invalid", "true");
    field.addEventListener("input", () => { pathValue = field.value; });
    const hint = el("p", "muted library-form__hint", "Use an absolute folder path or the path to benchmark.lighthouse on this Codex host.");
    hint.id = "benchmark-path-help";
    const submit = el("button", "library-form__submit library-form__submit--secondary", pending === "path" ? "Opening…" : "Open benchmark");
    submit.type = "submit";
    submit.disabled = pending !== undefined;
    const form = el("form", "library-form", fieldLabel, field, hint);
    form.addEventListener("submit", (event) => { event.preventDefault(); void attach(); });
    if (formError) {
      const error = el("p", "library-form__error", formError);
      error.id = "benchmark-path-error";
      error.setAttribute("role", "alert");
      form.append(error);
    }
    form.append(submit);
    const manual = el("details", "library-manual", el("summary", undefined, "Enter a path instead"), form);
    manual.open = formError !== "" || pathValue !== "" || pending === "path";
    connect.append(manual);
    workspace.append(connect);
    main.append(workspace);
    app.append(main, el("footer", "footer", "Built from repository evidence · Harbor owns execution and grading · Missing evidence is never a pass."));
    root.replaceChildren(app);
  }

  void refresh(true);
  return {destroy: () => { disposed = true; generation += 1; unsubscribe?.(); active?.destroy(); root.replaceChildren(); }};
}
