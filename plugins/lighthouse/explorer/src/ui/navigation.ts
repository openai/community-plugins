export type TaskRoutePane = "instruction" | "rubric" | "qualification";
export type AuthoringRoutePane = "instruction" | "rubric" | "evaluation";

export type ExplorerView =
  | {section: "overview"}
  | {section: "tasks"; taskId?: string; pane?: TaskRoutePane}
  | {section: "leaderboard"; cohortId?: string}
  | {section: "reviews"; reviewId?: string}
  | {section: "authoring"; taskId?: string; pane?: AuthoringRoutePane};

export type ExplorerRoute = {kind: "home"} | {kind: "benchmark"; benchmarkId: string; view: ExplorerView};

export interface ExplorerNavigation {
  current(): ExplorerRoute;
  go(route: ExplorerRoute, options?: {replace?: boolean}): void;
  subscribe(listener: (route: ExplorerRoute) => void): () => void;
}

const home: ExplorerRoute = {kind: "home"};

function component(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const decoded = decodeURIComponent(value);
    return decoded && !/[\u0000-\u001f/\\]/.test(decoded) ? decoded : undefined;
  } catch { return undefined; }
}

function pane<T extends string>(value: string | null, allowed: readonly T[]): T | undefined {
  return allowed.find((entry) => value === entry);
}

export function parseExplorerRoute(location: string): ExplorerRoute {
  if (!location.startsWith("/") || location.startsWith("//")) return home;
  let url: URL;
  try { url = new URL(location, "https://lighthouse.invalid"); } catch { return home; }
  if (url.origin !== "https://lighthouse.invalid" || url.hash) return home;
  const parts = url.pathname.split("/").filter(Boolean);
  if (!parts.length) return home;
  if (parts[0] !== "benchmarks" || parts.length < 2 || parts.length > 4) return home;
  const benchmarkId = component(parts[1]);
  if (!benchmarkId) return home;
  const section = parts[2];
  const selected = component(parts[3]);
  if (parts[3] && !selected) return home;
  switch (section) {
    case undefined: return {kind: "benchmark", benchmarkId, view: {section: "overview"}};
    case "tasks": {
      const selectedPane = pane(url.searchParams.get("view"), ["instruction", "rubric", "qualification"]);
      return {kind: "benchmark", benchmarkId, view: {section, ...(selected ? {taskId: selected} : {}), ...(selectedPane ? {pane: selectedPane} : {})}};
    }
    case "leaderboard":
      if (selected) return home;
      return {kind: "benchmark", benchmarkId, view: {section, ...(url.searchParams.get("cohort") ? {cohortId: url.searchParams.get("cohort") ?? ""} : {})}};
    case "reviews": return {kind: "benchmark", benchmarkId, view: {section, ...(selected ? {reviewId: selected} : {})}};
    case "authoring": {
      const selectedPane = pane(url.searchParams.get("view"), ["instruction", "rubric", "evaluation"]);
      return {kind: "benchmark", benchmarkId, view: {section, ...(selected ? {taskId: selected} : {}), ...(selectedPane ? {pane: selectedPane} : {})}};
    }
    default: return home;
  }
}

export function explorerPath(route: ExplorerRoute): string {
  if (route.kind === "home") return "/";
  const start = `/benchmarks/${encodeURIComponent(route.benchmarkId)}`;
  const view = route.view;
  switch (view.section) {
    case "overview": return start;
    case "leaderboard": return `${start}/leaderboard${view.cohortId ? `?cohort=${encodeURIComponent(view.cohortId)}` : ""}`;
    case "reviews": return `${start}/reviews${view.reviewId ? `/${encodeURIComponent(view.reviewId)}` : ""}`;
    case "tasks": case "authoring": return `${start}/${view.section}${view.taskId ? `/${encodeURIComponent(view.taskId)}` : ""}${view.pane && view.pane !== "instruction" ? `?view=${encodeURIComponent(view.pane)}` : ""}`;
  }
}

export function createBrowserNavigation(target: Window): ExplorerNavigation {
  const subscribers = new Set<(route: ExplorerRoute) => void>();
  const current = (): ExplorerRoute => parseExplorerRoute(`${target.location.pathname}${target.location.search}`);
  const changed = (): void => { for (const listener of subscribers) listener(current()); };
  return {
    current,
    go(route, options) {
      const destination = explorerPath(route);
      const old = `${target.location.pathname}${target.location.search}`;
      if (old !== destination) {
        if (options?.replace) target.history.replaceState(null, "", destination);
        else target.history.pushState(null, "", destination);
      }
      changed();
    },
    subscribe(listener) {
      if (!subscribers.size) target.addEventListener("popstate", changed);
      subscribers.add(listener);
      return () => { subscribers.delete(listener); if (!subscribers.size) target.removeEventListener("popstate", changed); };
    },
  };
}

export function nativeSelfLink(route: ExplorerRoute): string {
  return `codex://plugins/self/app/lighthouse_explorer_home?path=${encodeURIComponent(explorerPath(route))}`;
}

export function createHostedNavigation(initialPath: string, open: (url: string) => Promise<unknown>): {navigation: ExplorerNavigation; acceptHostPath: (path: string) => void} {
  let selected = parseExplorerRoute(initialPath);
  const subscribers = new Set<(route: ExplorerRoute) => void>();
  const update = (next: ExplorerRoute): boolean => {
    if (explorerPath(selected) === explorerPath(next)) return false;
    selected = next;
    for (const listener of subscribers) listener(selected);
    return true;
  };
  return {
    navigation: {
      current: () => selected,
      go(route, options) {
        if (update(route) && !options?.replace) void open(nativeSelfLink(route)).catch(() => { /* The local view remains usable if an older host rejects navigation. */ });
      },
      subscribe(listener) { subscribers.add(listener); return () => { subscribers.delete(listener); }; },
    },
    acceptHostPath(value) { update(parseExplorerRoute(value)); },
  };
}
