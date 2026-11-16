import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const suite = path.dirname(fileURLToPath(import.meta.url));
if (process.argv.slice(2).some(arg => arg !== '--static')) throw new Error('Only --static is supported');
const categories = process.argv.includes('--static') ? ['unit', 'security'] : ['unit', 'integration', 'security'];
if (!process.argv.includes('--static') && !process.env.DEVSECOPS_TEST_CONTEXT)
  throw new Error('Run npm run test:devsecops first, or supply its prepared DEVSECOPS_TEST_CONTEXT. Missing prerequisites are not a passing package check.');
const tests = categories.flatMap(category => fs.readdirSync(path.join(suite, category)).sort()
  .filter(name => name.endsWith('.test.mjs')).map(name => path.join(suite, category, name)));
const result = spawnSync(process.execPath, ['--test', '--test-concurrency=1', ...tests], { stdio: 'inherit', timeout: 10 * 60 * 1000 });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
