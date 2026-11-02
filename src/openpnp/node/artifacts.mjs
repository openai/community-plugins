import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, open, rename, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { PluginError } from './errors.mjs';

export function defaultStateDir() {
  if (process.env.OPENPNP_STATE_DIR) return path.resolve(process.env.OPENPNP_STATE_DIR);
  const parent = process.platform === 'darwin' ? path.join(os.homedir(), 'Library', 'Application Support') :
    process.platform === 'win32' ? (process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')) :
    (process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'));
  return path.join(parent, 'openpnp-codex');
}

export class ArtifactStore {
  constructor(root = defaultStateDir()) { this.directory = path.join(root, 'artifacts'); }
  async put(value) {
    const data = JSON.stringify(value);
    if (Buffer.byteLength(data) > 8 * 1024 * 1024) throw new PluginError('ARTIFACT_TOO_LARGE', 'Artifact exceeds 8 MiB.');
    const id = createHash('sha256').update(data).digest('hex');
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    const destination = path.join(this.directory, `${id}.json`);
    const temporary = path.join(this.directory, `.${randomUUID()}.tmp`);
    let handle;
    try {
      handle = await open(temporary, 'wx', 0o600);
      await handle.writeFile(data);
      await handle.sync();
      await handle.close(); handle = undefined;
      await rename(temporary, destination);
      if (process.platform !== 'win32') { const dir = await open(this.directory, 'r'); try { await dir.sync(); } finally { await dir.close(); } }
    } finally { await handle?.close(); await rm(temporary, { force: true }); }
    return { artifact_id: id, bytes: Buffer.byteLength(data), media_type: 'application/json' };
  }
  async get(id) {
    if (typeof id !== 'string' || !/^[a-f0-9]{64}$/.test(id)) throw new PluginError('INVALID_ARTIFACT_ID', 'Use a SHA-256 artifact identifier.');
    let data;
    try { data = await readFile(path.join(this.directory, `${id}.json`)); } catch { throw new PluginError('ARTIFACT_NOT_FOUND', 'The requested local artifact is unavailable.'); }
    if (data.length > 8 * 1024 * 1024 || createHash('sha256').update(data).digest('hex') !== id) throw new PluginError('ARTIFACT_INTEGRITY_FAILURE', 'The artifact content does not match its identifier.');
    return JSON.parse(data.toString('utf8'));
  }
}
