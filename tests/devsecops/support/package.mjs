import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const repo = fileURLToPath(new URL('../../../', import.meta.url));
export const plugin = path.join(repo, 'plugins/devsecops');
export const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
export function files(root) {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const item = path.join(root, entry.name);
    assert(!entry.isSymbolicLink(), `Symlink in public package: ${item}`);
    return entry.isDirectory() ? files(item) : [item];
  });
}
export function privacyProblems(root) {
  const problems = [];
  for (const item of files(root)) {
    const relative = path.relative(root, item);
    if (relative.split(path.sep).some(part => ['work', '.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache'].includes(part))
        || /(?:^|[/\\])\.env(?:\.|$)|\.(?:pyc|pyo|mp4|mov|webm|aiff|wav)$/i.test(relative)) {
      problems.push(`${relative}: private/generated artifact`);
    }
    const text = fs.readFileSync(item, 'utf8');
    // These named guest/CI users are public runtime contracts, not personal homes.
    if (/\/Users\/[A-Za-z0-9_.-]+\/|\/home\/(?!runner\/|user\/|example\/|operator\/|gitlab-runner\/|demo\/)[A-Za-z0-9_.-]+\//.test(text))
      problems.push(`${relative}: personal absolute path`);
    if (/codex:\/\/threads\/|https:\/\/[^\s/]+\.(?:enterprise\.)?slack\.com\/archives\//.test(text))
      problems.push(`${relative}: private coordination link`);
    if (/-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]{100,}-----END/.test(text))
      problems.push(`${relative}: private key material`);
  }
  return problems;
}
