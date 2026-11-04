import assert from "node:assert/strict";
import { mkdtemp, mkdir, realpath, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { getAuthoringSnapshot } from "../snapshot.ts";
import { getBenchmarkOverview, getLeaderboard, getQualityQueue, getTask, resolveEvidencePath } from "./index.ts";

const ids = [
  "9a74b0d2-bd7e-45b7-94d7-595c08941601",
  "9a74b0d2-bd7e-45b7-94d7-595c08941602",
  "9a74b0d2-bd7e-45b7-94d7-595c08941603",
  "9a74b0d2-bd7e-45b7-94d7-595c08941604",
  "9a74b0d2-bd7e-45b7-94d7-595c08941605",
  "9a74b0d2-bd7e-45b7-94d7-595c08941606",
] as const;

async function write(root: string, relative: string, value: unknown): Promise<void> {
  const target = path.join(root, relative);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, typeof value === "string" ? value : JSON.stringify(value));
}

async function fixture(options: { id?: string; quality?: boolean } = {}): Promise<{ root: string; marker: string }> {
  const root = await mkdtemp(path.join(tmpdir(), "lighthouse-explorer-"));
  const id = options.id ?? "token-refresh";
  const quality = options.quality ?? false;
  await write(root, "benchmark.lighthouse", {
    schemaVersion: 1, name: `Benchmark ${id}`,
    cohorts: [{ id: "original", label: "Frozen originals", functionalKey: "reward", results: ["jobs/learner-*"], qualityIndexes: quality ? ["experiments/quality/quality-index.json"] : [] }],
    qualityIndexes: quality ? ["experiments/quality/quality-index.json"] : [],
  });
  await write(root, `tasks/${id}/task.toml`, `schema_version = "1.4"\n[task]\nname = "example/${id}"\nversion = "1.0.0"\ndescription = "Refresh | carefully"\nkeywords = ["typescript", "parser"]\n[metadata]\ndifficulty = "hard"\ncategory = "programming"\n`);
  await write(root, `tasks/${id}/instruction.md`, "# Learner request\n\nUpdate token refreshing.\n");
  await write(root, `review/${id}/requirements.md`, "# Curator rubric\n\n| promise | observable |\n| --- | --- |\n| safe refresh | exact evidence |\n");
  await write(root, `review/${id}/qualification.md`, "# Qualification\n");
  await write(root, `review/${id}/promotion.json`, { destination: `tasks/${id}`, promoted_at: "2026-09-26", review: `review/${id}/qualification.md` });
  await write(root, "leaderboard/README.md", "# Markdown claims 100/100\nThis is source text, never metric input.\n");
  return { root, marker: path.join(root, "benchmark.lighthouse") };
}

function trial(id: string, reward: number, exception = false): Record<string, unknown> {
  return { id, task_name: "example/token-refresh", finished_at: "2026-09-26T10:00:00Z", exception_info: exception ? { exception_type: "ApiRateLimitError", exception_message: "intentionally not returned" } : null, config: { agent: { model_name: "openai/model", kwargs: { reasoning_effort: "medium" } } }, verifier_result: { rewards: { reward } } };
}

function review(id: string, status: "pass" | "fail" | "review_required"): Record<string, unknown> {
  const changed = status === "pass" ? "pass" : status;
  return {
    policy: "code-quality-v1", source_trial_id: id, packet_sha256: "a".repeat(64), status,
    quality_pass: status === "pass" ? 1 : status === "fail" ? 0 : null,
    quality_score: status === "pass" ? 1 : status === "fail" ? 0.75 : null,
    dimensions: { readability: changed, maintainability: "pass", scope: "pass", tests: "pass" },
    primary: { model: "openai/gpt-6-sol", effort: "medium", error: null, decisions: { readability: { score: status === "pass" ? 1 : 0, reason: "src/refresh.ts:8 — the specific evidence" } } },
    reviewer: status === "pass" ? null : { model: "openai/gpt-6-astra", effort: "medium", error: null, decisions: { readability: { score: status === "fail" ? 0 : 1, reason: "src/refresh.ts:8 — independently reviewed" } } },
    adjudication: null,
  };
}

test("reads real TOML and keeps learner instructions, curator rubric, and qualification distinct", async () => {
  const { root, marker } = await fixture();
  const overview = await getBenchmarkOverview(marker);
  assert.equal(overview.taskCount, 1);
  assert.equal(overview.qualifiedTaskCount, 1);
  assert.equal(overview.tasks[0]?.title, "Refresh | carefully");
  assert.deepEqual(overview.tasks[0]?.tags, ["typescript", "parser"]);
  const task = await getTask(marker, "token-refresh");
  assert.match(task?.instruction?.content ?? "", /Learner request/);
  assert.match(task?.rubric?.content ?? "", /Curator rubric/);
  assert.doesNotMatch(task?.instruction?.content ?? "", /Curator rubric/);
  assert.equal(await resolveEvidencePath(marker, "review/token-refresh/requirements.md"), await realpath(path.join(root, "review/token-refresh/requirements.md")));
  assert.equal(await getTask(marker, "unknown"), null);
});

test("discovers an unfinished task draft before its TOML exists and exposes authoring progress", async () => {
  const { root, marker } = await fixture();
  await write(root, ".local/drafts/in-progress/instruction.md", "# A draft learner request\n\nKeep the learner version separate.\n");
  await write(root, ".local/drafts/in-progress/tests/test.sh", "#!/bin/sh\n# Draft deterministic verifier\n");
  await write(root, "review/in-progress/requirements.md", "# Draft curator rubric\n\nThis is intentionally private.\n");

  const overview = await getBenchmarkOverview(marker);
  assert.equal(overview.taskCount, 1);
  assert.equal(overview.qualifiedTaskCount, 1);
  assert.ok(overview.tasks.some((entry) => entry.id === "in-progress"));

  const task = await getTask(marker, "in-progress");
  assert.ok(task);
  assert.equal(task.qualification, "unknown");
  assert.equal(task.authoring.location, "draft");
  assert.deepEqual(task.authoring.artifacts, {
    instruction: { path: ".local/drafts/in-progress/instruction.md", status: "present" },
    rubric: { path: "review/in-progress/requirements.md", status: "present" },
    qualification: { path: "review/in-progress/qualification.md", status: "missing" },
    metadata: { path: ".local/drafts/in-progress/task.toml", status: "missing" },
    verifier: { path: ".local/drafts/in-progress/tests/test.sh", status: "present" },
  });
  assert.match(task.instruction?.content ?? "", /draft learner request/);
  assert.doesNotMatch(task.instruction?.content ?? "", /intentionally private/);
  assert.match(task.rubric?.content ?? "", /intentionally private/);
  assert.match(task.verifier?.content ?? "", /Draft deterministic verifier/);
  assert.equal(task.metadata, null);
  assert.equal(task.qualificationDocument, null);
});

test("finds review-only work by task documents while excluding unrelated review folders", async () => {
  const { root, marker } = await fixture();
  await write(root, "review/rubric-only/requirements.md", "# Working rubric\n");
  await write(root, "review/qualification-only/qualification.md", "# Qualification in progress\n");
  await write(root, "review/promotion-only/promotion.json", { qualified: false });
  await write(root, "review/adjudications/a/quality-review.json", { status: "review_required" });
  await write(root, "review/general/README.md", "# Repository-wide review notes\n");
  await write(root, "review/unrelated/quality-review.json", { status: "review_required" });

  const overview = await getBenchmarkOverview(marker);
  assert.deepEqual(overview.tasks.map((entry) => entry.id).sort(), ["promotion-only", "qualification-only", "rubric-only", "token-refresh"]);
  assert.equal(overview.qualifiedTaskCount, 1);
  assert.equal(await getTask(marker, "adjudications"), null);
  assert.equal(await getTask(marker, "general"), null);
  assert.equal(await getTask(marker, "unrelated"), null);

  const task = await getTask(marker, "rubric-only");
  assert.ok(task);
  assert.equal(task.authoring.location, "review_only");
  assert.equal(task.authoring.artifacts.instruction.status, "missing");
  assert.deepEqual(task.authoring.artifacts.rubric, { path: "review/rubric-only/requirements.md", status: "present" });
  assert.equal(task.authoring.artifacts.metadata.status, "missing");
  assert.equal(task.authoring.artifacts.verifier.status, "missing");
  assert.equal(task.instruction, null);
  assert.match(task.rubric?.content ?? "", /Working rubric/);
  assert.equal((await getTask(marker, "promotion-only"))?.qualification, "not_qualified");
});

test("prefers published task files and retains invalid metadata without hiding neighboring tasks", async () => {
  const { root, marker } = await fixture();
  await write(root, "tasks/token-refresh/tests/test.sh", "#!/bin/sh\n# Published verifier\n");
  await write(root, ".local/drafts/token-refresh/instruction.md", "# An old draft copy\n");
  await write(root, ".local/drafts/token-refresh/tests/test.sh", "#!/bin/sh\n# An old draft verifier\n");
  await write(root, "tasks/broken-metadata/task.toml", "[task\ninvalid =\n");
  await write(root, "tasks/broken-metadata/instruction.md", "# Still a visible task\n");

  const overview = await getBenchmarkOverview(marker);
  assert.deepEqual(overview.tasks.map((entry) => entry.id).sort(), ["broken-metadata", "token-refresh"]);
  assert.match(overview.notices.join(" "), /tasks\/broken-metadata\/task\.toml/);
  const published = await getTask(marker, "token-refresh");
  assert.ok(published);
  assert.equal(published.authoring.location, "published");
  assert.equal(published.authoring.artifacts.instruction.path, "tasks/token-refresh/instruction.md");
  assert.equal(published.authoring.artifacts.verifier.path, "tasks/token-refresh/tests/test.sh");
  assert.match(published.instruction?.content ?? "", /Learner request/);
  assert.doesNotMatch(published.instruction?.content ?? "", /old draft/);
  assert.match(published.verifier?.content ?? "", /Published verifier/);

  const broken = await getTask(marker, "broken-metadata");
  assert.ok(broken);
  assert.equal(broken.authoring.location, "published");
  assert.deepEqual(broken.authoring.artifacts.metadata, { path: "tasks/broken-metadata/task.toml", status: "invalid" });
  assert.match(broken.instruction?.content ?? "", /Still a visible task/);
});

test("the authoring catalog exposes task counts and progress without loading any task documents", async () => {
  const { root, marker } = await fixture();
  await write(root, ".local/drafts/zz-panel-draft/instruction.md", "# Private initial draft learner request\n");
  await write(root, ".local/drafts/zz-panel-draft/tests/test.sh", "# Private draft deterministic verifier\n");
  await write(root, "review/zz-panel-draft/requirements.md", "# Private draft rubric\n");
  await write(root, "review/zz-panel-draft/qualification.md", "# Private draft qualification notes\n");
  await write(root, "review/aa-review-only/requirements.md", "# Private review-only rubric\n");
  await write(root, "jobs/learner-1/result.json", "deliberately unfinished learner run");
  const initial = await getAuthoringSnapshot(marker);
  assert.equal(initial.benchmark.status, "1 of 1 published tasks qualified");
  assert.deepEqual(initial.tasks.map((task) => [task.id, task.status, task.authoring?.location]), [
    ["aa-review-only", "unknown", "review_only"],
    ["token-refresh", "qualified", "published"],
    ["zz-panel-draft", "unknown", "draft"],
  ]);
  for (const task of initial.tasks) {
    assert.equal(task.instruction, null);
    assert.equal(task.rubric, null);
    assert.equal(task.qualification, null);
    assert.equal(task.verifier, null);
  }
  assert.equal(initial.tasks.find((task) => task.id === "token-refresh")?.authoring?.artifacts.instruction.status, "present");
  assert.equal(initial.tasks.find((task) => task.id === "zz-panel-draft")?.authoring?.artifacts.verifier.status, "present");
  assert.doesNotMatch(JSON.stringify(initial), /Private|Learner request|Curator rubric|unfinished learner run/);
  assert.deepEqual(initial.leaderboard.trials, []);
  assert.deepEqual(initial.reviews, []);
  assert.deepEqual(initial.warnings, []);
});

test("refreshes only an explicitly selected author's documents and keeps learner runs out of the panel", async () => {
  const { root, marker } = await fixture();
  await write(root, ".local/drafts/zz-panel-draft/instruction.md", "# First draft\n");
  await write(root, "review/zz-panel-draft/requirements.md", "# Private draft rubric\n");
  await write(root, "jobs/learner-1/result.json", "deliberately unfinished learner run");
  const first = await getAuthoringSnapshot(marker, "zz-panel-draft");
  assert.match(first.tasks.find((task) => task.id === "zz-panel-draft")?.instruction?.content ?? "", /First draft/);
  assert.match(first.tasks.find((task) => task.id === "zz-panel-draft")?.rubric?.content ?? "", /Private draft rubric/);
  assert.equal(first.tasks.find((task) => task.id === "token-refresh")?.instruction, null);
  await write(root, ".local/drafts/zz-panel-draft/instruction.md", "# Revised request while the panel is open\n");
  const refreshed = await getAuthoringSnapshot(marker, "zz-panel-draft");
  assert.match(refreshed.tasks.find((task) => task.id === "zz-panel-draft")?.instruction?.content ?? "", /Revised request/);
  const switched = await getAuthoringSnapshot(marker, "token-refresh");
  assert.match(switched.tasks.find((task) => task.id === "token-refresh")?.instruction?.content ?? "", /Learner request/);
  assert.equal(switched.tasks.find((task) => task.id === "zz-panel-draft")?.rubric, null);
  const missing = await getAuthoringSnapshot(marker, "missing");
  assert.ok(missing.tasks.every((task) => task.instruction === null && task.rubric === null && task.qualification === null && task.verifier === null));
  assert.deepEqual(switched.leaderboard.trials, []);
  assert.deepEqual(switched.reviews, []);
});

test("denominators use explicit native trials; missing/disputed quality cannot produce a pass, infrastructure is separate", async () => {
  const { root, marker } = await fixture({ quality: true });
  const statuses = ["pass", "fail", "review_required", "pass"] as const;
  const qualityIndex: Record<string, { regrade: string }> = {};
  for (const [index, id] of ids.entries()) {
    await write(root, `jobs/learner-${index}/result.json`, { stats: { mean_reward: 1 } });
    await write(root, `jobs/learner-${index}/native/result.json`, trial(id, index === 3 ? 0 : index === 5 ? 0.5 : 1, index === 4));
    const state = statuses[index];
    if (!state) continue;
    qualityIndex[id] = { regrade: `../../regrades/quality/attempt-${index}` };
    const regrade = trial(`regrade-${index}`, 1);
    regrade.config = { agent: { model_name: "openai/model", kwargs: { reasoning_effort: "medium" } }, source_trial: { action: "regrade", trial_id: id } };
    await write(root, `regrades/quality/attempt-${index}/result.json`, regrade);
    await write(root, `regrades/quality/attempt-${index}/verifier/quality-review.json`, review(id, state));
  }
  await write(root, "experiments/quality/quality-index.json", qualityIndex);
  const leaderboard = await getLeaderboard(marker);
  const cohort = leaderboard.cohorts[0];
  assert.ok(cohort);
  assert.deepEqual(cohort.metrics.functional, { passed: 3, denominator: 4 });
  assert.deepEqual(cohort.metrics.quality, { passed: 2, denominator: 3 });
  assert.deepEqual(cohort.metrics.combined, { passed: 1, denominator: 4 });
  assert.equal(cohort.metrics.counts.functionalUnavailable, 2);
  assert.equal(cohort.metrics.counts.combinedFail, 2);
  assert.equal(cohort.metrics.counts.combinedReviewRequired, 1);
  assert.equal(cohort.rows.find((row) => row.trialId === ids[4])?.error, "ApiRateLimitError");
  assert.equal(cohort.rows.find((row) => row.trialId === ids[5])?.functionalReward, 0.5);
  assert.match(leaderboard.document?.content ?? "", /100\/100/);
  const queue = await getQualityQueue(marker);
  assert.deepEqual(queue.counts, { pass: 2, fail: 1, review_required: 1, unmeasured: 2 });
  assert.match(queue.items[2]?.reviewer?.decisions.readability?.reason ?? "", /independently/);
});

test("an absent quality index never creates a complete-rubric pass", async () => {
  const { root, marker } = await fixture();
  await write(root, "jobs/learner-1/result.json", trial(ids[0], 1));
  const leaderboard = await getLeaderboard(marker);
  assert.deepEqual(leaderboard.cohorts[0]?.metrics.combined, { passed: 0, denominator: 1 });
  assert.deepEqual(leaderboard.cohorts[0]?.metrics.quality, { passed: 0, denominator: 0 });
  const queue = await getQualityQueue(marker);
  assert.equal(queue.items.length, 1);
  assert.equal(queue.items[0]?.status, "unmeasured");
  assert.match(queue.notices.join(" "), /cannot yield a benchmark pass/);
});

test("rejects traversal, credential paths, and symlink escapes from the selected benchmark", async () => {
  const first = await fixture({ id: "first" });
  const second = await fixture({ id: "second" });
  await write(first.root, ".env", "OPENAI_API_KEY=not-for-explorer");
  await write(first.root, "review/first/credentials.json", { sensitive: true });
  await symlink(path.join(second.root, "review/second/requirements.md"), path.join(first.root, "review/first/outside.md"));
  await symlink(path.join(first.root, ".env"), path.join(first.root, "review/first/inside.md"));
  await assert.rejects(resolveEvidencePath(first.marker, "../second/review/requirements.md"), /allowlist/);
  await assert.rejects(resolveEvidencePath(first.marker, ".env"), /allowlist/);
  await assert.rejects(resolveEvidencePath(first.marker, "review/first/credentials.json"), /allowlist/);
  await assert.rejects(resolveEvidencePath(first.marker, "review/first/outside.md"), /outside/);
  await assert.rejects(resolveEvidencePath(first.marker, "review/first/inside.md"), /allowlist/);
  await assert.rejects(getTask(first.marker, "../../second"), /Invalid/);
  assert.equal(await getTask(first.marker, "second"), null);
  const third = await mkdtemp(path.join(tmpdir(), "lighthouse-launcher-"));
  await symlink(second.marker, path.join(third, "benchmark.lighthouse"));
  await assert.rejects(getBenchmarkOverview(path.join(third, "benchmark.lighthouse")), /outside/);
});

test("allows exactly the draft authoring documents and refuses hidden, solution, and symlink escapes", async () => {
  const first = await fixture({ id: "first" });
  const second = await fixture({ id: "second" });
  const allowed = [
    ".local/drafts/working-task/instruction.md",
    ".local/drafts/working-task/task.toml",
    ".local/drafts/working-task/tests/test.sh",
  ];
  const blocked = [
    ".local/drafts/working-task/README.md",
    ".local/drafts/working-task/solution/solve.sh",
    ".local/drafts/working-task/hidden/instruction.md",
    ".local/drafts/working-task/tests/private.sh",
    ".local/drafts/working-task/tests/hidden/test.sh",
    ".local/drafts/working-task/.secret/instruction.md",
    ".local/drafts/.hidden/instruction.md",
    ".local/arbitrary/instruction.md",
  ];
  for (const relative of [...allowed, ...blocked]) await write(first.root, relative, "private fixture\n");
  for (const relative of allowed) assert.equal(await resolveEvidencePath(first.marker, relative), await realpath(path.join(first.root, relative)));
  for (const relative of blocked) await assert.rejects(resolveEvidencePath(first.marker, relative), /allowlist/);

  await mkdir(path.join(first.root, ".local/drafts/outside"), { recursive: true });
  await mkdir(path.join(first.root, ".local/drafts/inside"), { recursive: true });
  await symlink(path.join(second.root, "tasks/second/instruction.md"), path.join(first.root, ".local/drafts/outside/instruction.md"));
  await symlink(path.join(first.root, ".local/drafts/working-task/solution/solve.sh"), path.join(first.root, ".local/drafts/inside/instruction.md"));
  await assert.rejects(resolveEvidencePath(first.marker, ".local/drafts/outside/instruction.md"), /outside/);
  await assert.rejects(resolveEvidencePath(first.marker, ".local/drafts/inside/instruction.md"), /allowlist/);
});

test("native source identity mismatch withholds an otherwise passing quality artifact", async () => {
  const { root, marker } = await fixture({ quality: true });
  await write(root, "jobs/learner-1/result.json", trial(ids[0], 1));
  await write(root, "experiments/quality/quality-index.json", { [ids[0]]: { regrade: "../../regrades/quality/a" } });
  await write(root, "regrades/quality/a/result.json", { ...trial("regrade", 1), config: { source_trial: { action: "regrade", trial_id: ids[1] } } });
  await write(root, "regrades/quality/a/verifier/quality-review.json", review(ids[0], "pass"));
  const queue = await getQualityQueue(marker);
  assert.equal(queue.items[0]?.status, "unmeasured");
  assert.match(queue.notices.join(" "), /linked to that source trial/);
  assert.equal((await getLeaderboard(marker)).cohorts[0]?.metrics.combined.passed, 0);
});

test("an adjudication can settle only a disputed dimension and must retain both judges", async () => {
  const { root, marker } = await fixture({ quality: true });
  await write(root, "jobs/learner-1/result.json", trial(ids[0], 1));
  await write(root, "experiments/quality/quality-index.json", { [ids[0]]: { regrade: "../../regrades/quality/a", adjudicated: "../../review/adjudications/a/quality-review.json" } });
  await write(root, "regrades/quality/a/result.json", { ...trial("regrade", 1), config: { source_trial: { action: "regrade", trial_id: ids[0] } } });
  const disputed = review(ids[0], "review_required");
  await write(root, "regrades/quality/a/verifier/quality-review.json", disputed);
  const settled = { ...disputed, status: "pass", quality_pass: 1, quality_score: 1, dimensions: { readability: "pass", maintainability: "pass", scope: "pass", tests: "pass" }, adjudication: { reviewer: "Curator", decisions: { readability: { score: 1, reason: "src/refresh.ts:8 checks the documented branch." } } } };
  await write(root, "review/adjudications/a/quality-review.json", settled);
  assert.equal((await getQualityQueue(marker)).items[0]?.status, "pass");
  await write(root, "review/adjudications/a/quality-review.json", { ...settled, adjudication: { reviewer: "Curator", decisions: { maintainability: { score: 1, reason: "This was never disputed." } } } });
  const rejected = await getQualityQueue(marker);
  assert.equal(rejected.items[0]?.status, "review_required");
  assert.match(rejected.notices.join(" "), /could not be verified/);
  await write(root, "review/adjudications/a/quality-review.json", { ...settled, reviewer: { model: "changed" } });
  assert.equal((await getQualityQueue(marker)).items[0]?.status, "review_required");
});

test("uses a regrade's original UUID for a deliberately separate amended cohort", async () => {
  const { root, marker } = await fixture();
  await write(root, "jobs/learner-1/result.json", { ...trial(ids[1], 1), config: { source_trial: { trial_id: ids[0] }, agent: { model_name: "model" } } });
  const amended = (await getLeaderboard(marker)).cohorts[0];
  assert.equal(amended?.rows[0]?.trialId, ids[0]);
  assert.equal(amended?.metrics.functional.passed, 1);
});
