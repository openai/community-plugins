import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
export const repo = fileURLToPath(new URL('../../..', import.meta.url));
export const fixture = name => JSON.parse(fs.readFileSync(new URL(`../fixtures/${name}.json`, import.meta.url), 'utf8'));
export function workspace(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pcba-test-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}
