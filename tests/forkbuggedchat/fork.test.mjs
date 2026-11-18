import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const hook = fileURLToPath(new URL('../../plugins/forkbuggedchat/hooks/fork.mjs', import.meta.url));
const appServer = `const fs = require('node:fs');
require('node:readline').createInterface({ input: process.stdin }).on('line', line => {
  const q = JSON.parse(line);
  if (!q.id) return;
  let result = {};
  if (q.method === 'thread/turns/list') result = { data: [{ id: 'done', status: 'completed' }] };
  if (q.method === 'thread/read') result = { thread: { cwd: process.env.TEST_REPO } };
  if (q.method === 'thread/fork') {
    fs.writeFileSync(process.env.TEST_OUTPUT, JSON.stringify(q.params));
    result = { thread: { id: process.env.TEST_THREAD } };
  }
  console.log(JSON.stringify({ id: q.id, result }));
});`;
function git(dir, ...args) {
  return execFileSync('git', ['-C', dir, ...args], { encoding: 'utf8' }).trim();
}

test('exact commands fork locally and into a new worktree', () => {
  const dir = mkdtempSync(join(tmpdir(), 'fork-chat-fixed-test-'));
  try {
    const repo = join(dir, 'repo');
    const home = join(dir, 'codex-home');
    mkdirSync(repo);
    mkdirSync(home);
    writeFileSync(join(repo, 'tracked.txt'), 'original\n');
    git(repo, 'init', '-q');
    git(repo, 'config', 'user.name', 'Test');
    git(repo, 'config', 'user.email', 'test@example.invalid');
    git(repo, 'add', '.');
    git(repo, 'commit', '-qm', 'initial');
    writeFileSync(join(repo, 'tracked.txt'), 'changed\n');
    writeFileSync(join(repo, 'untracked.txt'), 'new file\n');
    copyFileSync(process.execPath, join(dir, 'codex.exe'));
    writeFileSync(join(dir, 'app-server'), appServer);
    const output = join(dir, 'fork.json');
    const env = { ...process.env, CODEX_HOME: home, CODEX_APP_RUNTIME_CLI_PATH: join(dir, 'codex.exe'), TEST_REPO: repo, TEST_OUTPUT: output, TEST_THREAD: '00000000-0000-7000-8000-000000000001' };
    const run = prompt => {
      const result = spawnSync(process.execPath, [hook], { cwd: dir, env, encoding: 'utf8', timeout: 30_000, input: JSON.stringify({ prompt, session_id: env.TEST_THREAD, turn_id: 'pending' }) });
      assert.equal(result.status, 0, result.stderr);
      return result.stdout;
    };
    for (const prompt of ['forkbuggedchat', ' forkchatfixed', 'forkchatfixed ', '`forkchatfixed`', '/forkchatfixed', 'forkchatfixed more', 'forkchatinnewworktreefixed ']) {
      assert.equal(run(prompt), '');
      assert.equal(existsSync(output), false);
    }
    assert.match(run('forkchatfixed'), /codex:\/\/threads\//);
    assert.equal(JSON.parse(readFileSync(output, 'utf8')).cwd, undefined);
    assert.match(run('forkchatinnewworktreefixed'), /codex:\/\/threads\//);
    const { cwd, lastTurnId } = JSON.parse(readFileSync(output, 'utf8'));
    assert.equal(lastTurnId, 'done');
    assert.ok(cwd.startsWith(join(home, 'worktrees') + sep));
    assert.equal(readFileSync(join(cwd, 'tracked.txt'), 'utf8').trim(), 'changed');
    assert.equal(readFileSync(join(cwd, 'untracked.txt'), 'utf8'), 'new file\n');
    const owner = git(cwd, 'rev-parse', '--git-path', 'codex-thread.json');
    assert.deepEqual(JSON.parse(readFileSync(resolve(cwd, owner), 'utf8')), { version: 1, ownerThreadId: env.TEST_THREAD });
  } finally {
    assert.ok(resolve(dir).startsWith(resolve(tmpdir()) + sep));
    rmSync(dir, { recursive: true, force: true });
  }
});
