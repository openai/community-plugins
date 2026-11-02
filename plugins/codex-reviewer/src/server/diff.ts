import { getCapturedFileDiff } from "./snapshot.js";
import type { FileDiff } from "./types.js";

export async function getFileDiff(snapshotId: string, fileId: string): Promise<FileDiff> {
  return getCapturedFileDiff(snapshotId, fileId);
}
