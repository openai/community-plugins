import path from "node:path";

import { BenchmarkFiles, configuredPath, object, string, strings } from "./files.ts";

export interface CohortDefinition {
  id: string;
  label: string;
  revision: string | null;
  functionalKey: string;
  results: string[];
  qualityIndexes: string[];
}

export interface Manifest {
  name: string;
  description: string | null;
  leaderboard: string;
  cohorts: CohortDefinition[];
  qualityIndexes: string[];
}

function validPatterns(value: unknown): string[] {
  return strings(value).filter((item) => {
    if (!configuredPath(item)) return false;
    const parts = item.split("/");
    return (parts[0] === "jobs" || parts[0] === "regrades") && parts.slice(0, -1).every((part) => !part.includes("*")) && (item.match(/\*/g)?.length ?? 0) <= 1;
  });
}

function qualityIndexes(value: unknown): string[] {
  return strings(value).filter((item) => configuredPath(item) && /^experiments\/(?:[^/]+\/)+quality-index\.json$/.test(item));
}

export async function manifest(files: BenchmarkFiles): Promise<Manifest> {
  const raw = await files.json("benchmark.lighthouse");
  if (!raw || raw.schemaVersion !== 1) throw new Error("This launcher must be a JSON Lighthouse schemaVersion 1 file.");
  const rawCohorts: unknown[] = Array.isArray(raw.cohorts) ? raw.cohorts : [];
  const cohorts: CohortDefinition[] = [];
  for (const entry of rawCohorts) {
    const item = object(entry);
    const id = string(item?.id);
    const label = string(item?.label);
    if (!id || !label || !/^[a-zA-Z0-9._-]+$/.test(id) || cohorts.some((cohort) => cohort.id === id)) throw new Error("Launcher cohorts require unique ids and labels.");
    const inputs = Array.isArray(item?.results) ? item.results : [];
    const results = validPatterns(inputs);
    if (inputs.length !== results.length) throw new Error(`Cohort ${id} contains an unsupported result path.`);
    const functionalKey = string(item?.functionalKey) ?? "solved";
    if (!/^[a-zA-Z0-9_-]+$/.test(functionalKey)) throw new Error(`Cohort ${id} has an invalid functional key.`);
    const rawIndexes = Array.isArray(item?.qualityIndexes) ? item.qualityIndexes : [];
    const indexes = qualityIndexes(rawIndexes);
    if (rawIndexes.length !== indexes.length) throw new Error(`Cohort ${id} contains an unsupported quality index path.`);
    cohorts.push({ id, label, revision: string(item?.revision), functionalKey, results, qualityIndexes: indexes });
  }
  const indexes = raw.qualityIndexes === undefined ? [] : qualityIndexes(raw.qualityIndexes);
  if (raw.qualityIndexes !== undefined && (!Array.isArray(raw.qualityIndexes) || raw.qualityIndexes.length !== indexes.length)) throw new Error("The launcher contains an unsupported quality index path.");
  const leaderboard = string(raw.leaderboard) ?? "leaderboard/README.md";
  if (!/^leaderboard\/(?:README|index)\.md$/i.test(leaderboard)) throw new Error("The launcher leaderboard must be a Markdown file inside leaderboard/.");
  return {
    name: string(raw.name) ?? path.basename(files.root),
    description: string(raw.description),
    leaderboard,
    cohorts,
    qualityIndexes: indexes,
  };
}
