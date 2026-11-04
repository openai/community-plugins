import assert from "node:assert/strict";
import test from "node:test";
import { createHostedNavigation, explorerPath, nativeSelfLink, parseExplorerRoute, type ExplorerRoute } from "./navigation.ts";

test("dashboard locations round-trip tasks, rubric, review, authoring and cohort without filesystem paths", () => {
  const cases: ExplorerRoute[] = [
    {kind:"home"},
    {kind:"benchmark",benchmarkId:"benchmark_a-b",view:{section:"overview"}},
    {kind:"benchmark",benchmarkId:"benchmark_a-b",view:{section:"tasks",taskId:"cache mapping",pane:"rubric"}},
    {kind:"benchmark",benchmarkId:"benchmark_a-b",view:{section:"tasks",taskId:"cache",pane:"qualification"}},
    {kind:"benchmark",benchmarkId:"benchmark_a-b",view:{section:"leaderboard",cohortId:"Sol / medium"}},
    {kind:"benchmark",benchmarkId:"benchmark_a-b",view:{section:"reviews",reviewId:"opaque"}},
    {kind:"benchmark",benchmarkId:"benchmark_a-b",view:{section:"authoring",taskId:"cache",pane:"evaluation"}},
  ];
  for (const example of cases) assert.deepEqual(parseExplorerRoute(explorerPath(example)),example);
});

test("invalid external, fragmented and encoded traversal locations return home", () => {
  for(const url of ["https://other.test/", "//other.test/", "/benchmarks", "/benchmarks/a/nope", "/benchmarks/a/tasks/%2fetc", "/benchmarks/a/tasks/%5Cenv", "/benchmarks/a#anchor", "/benchmarks/a/leaderboard/extra"]){
    assert.deepEqual(parseExplorerRoute(url),{kind:"home"});
  }
});

test("user navigation enters Codex shell history while host back/forward restores without echoing", async () => {
  const opened: string[]=[];
  const {navigation,acceptHostPath}=createHostedNavigation("/",async(url)=>{opened.push(url);});
  const seen:string[]=[];
  navigation.subscribe(route=>seen.push(explorerPath(route)));
  const overview=parseExplorerRoute('/benchmarks/a');
  const task=parseExplorerRoute('/benchmarks/a/tasks/example?view=rubric');
  navigation.go(overview,{replace:true});
  navigation.go(task);
  assert.deepEqual(opened,[nativeSelfLink(task)]);
  acceptHostPath('/benchmarks/a');
  acceptHostPath('/benchmarks/a/tasks/example?view=rubric');
  acceptHostPath('/benchmarks/a/tasks/example?view=rubric');
  assert.deepEqual(opened,[nativeSelfLink(task)]);
  assert.deepEqual(seen,['/benchmarks/a','/benchmarks/a/tasks/example?view=rubric','/benchmarks/a','/benchmarks/a/tasks/example?view=rubric']);
});
