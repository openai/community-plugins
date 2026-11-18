import { execFileSync, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { copyFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { createInterface } from 'node:readline';

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const { prompt, session_id: threadId, turn_id: currentTurnId } = JSON.parse(Buffer.concat(chunks).toString());
if (prompt !== 'forkchatfixed' && prompt !== 'forkchatinnewworktreefixed') process.exit(0);

function show(message) {
  console.log(JSON.stringify({ decision: 'block', reason: message, systemMessage: message }));
}

function git(cwd, ...args) {
  return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8', windowsHide: true, maxBuffer: 64 * 1024 * 1024 });
}

function createWorktree(cwd) {
  const root = resolve(git(cwd, 'rev-parse', '--show-toplevel').trim());
  git(root, 'rev-parse', '--verify', 'HEAD');
  const folder = join(process.env.CODEX_HOME || join(homedir(), '.codex'), 'worktrees', randomBytes(2).toString('hex'));
  const worktree = join(folder, basename(root));
  mkdirSync(folder, { recursive: true });
  writeFileSync(join(folder, '.codex-worktree-name'), '');
  git(root, 'worktree', 'add', '--detach', worktree, 'HEAD');
  const patch = git(root, 'diff', '--binary', 'HEAD');
  if (patch) execFileSync('git', ['-C', worktree, 'apply', '--binary', '-'], { input: patch, windowsHide: true });
  for (const name of git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0').filter(Boolean)) {
    const destination = join(worktree, name);
    mkdirSync(dirname(destination), { recursive: true });
    copyFileSync(join(root, name), destination);
  }
  return join(worktree, relative(root, cwd));
}

const server = spawn(process.env.CODEX_APP_RUNTIME_CLI_PATH || 'codex', ['app-server'], {
  stdio: ['pipe', 'pipe', 'ignore'], windowsHide: true, signal: AbortSignal.timeout(115_000),
});
const lines = createInterface({ input: server.stdout });
const replies = lines[Symbol.asyncIterator]();
let serverError;
server.on('error', error => { serverError = error; lines.close(); });
server.stdin.on('error', error => { serverError ??= error; lines.close(); });
function send(message) { server.stdin.write(JSON.stringify(message) + '\n'); }
async function request(id, method, params) {
  send({ id, method, params });
  for (let line = await replies.next(); !line.done; line = await replies.next()) {
    const response = JSON.parse(line.value);
    if (response.id !== id) continue;
    if (response.error) throw new Error(response.error.message);
    return response.result;
  }
  throw serverError ?? new Error(`${method} 无响应`);
}

let createdUrl;
try {
  await request(1, 'initialize', {
    clientInfo: { name: 'forkbuggedchat', title: 'Fork Bugged Chat', version: '1.0.4' },
  });
  send({ method: 'initialized' });
  const { data: turns } = await request(2, 'thread/turns/list', {
    threadId, limit: 10, sortDirection: 'desc',
  });
  const previous = turns.find(turn => turn.status === 'completed' && turn.id !== currentTurnId);
  const options = prompt === 'forkchatinnewworktreefixed'
    ? { cwd: createWorktree((await request(3, 'thread/read', { threadId })).thread.cwd) }
    : {};
  const { thread } = await request(4, 'thread/fork', {
    threadId, ...(previous && { lastTurnId: previous.id }), ...options, excludeTurns: true,
  });
  if (options.cwd) {
    const ownerFile = git(options.cwd, 'rev-parse', '--git-path', 'codex-thread.json').trim();
    writeFileSync(resolve(options.cwd, ownerFile), JSON.stringify({ version: 1, ownerThreadId: thread.id }, null, 2) + '\n');
  }
  createdUrl = `codex://threads/${thread.id}`;
  execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `Start-Process -FilePath '${createdUrl}'`], { windowsHide: true });
  show(`分叉已创建并打开：${createdUrl}`);
} catch (error) {
  show(createdUrl ? `分叉已创建，请打开 ${createdUrl}；自动打开失败：${error.message}` : `分叉失败：${error.message}`);
} finally {
  lines.close();
  server.stdin.end();
  server.kill();
}
