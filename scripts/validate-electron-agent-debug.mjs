import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const plugin = path.join(root, 'plugins', 'electron-agent-debug');
const manifest = JSON.parse(fs.readFileSync(path.join(plugin, '.codex-plugin', 'plugin.json'), 'utf8'));
const readme = fs.readFileSync(path.join(plugin, 'README.md'), 'utf8');
const skillPath = path.join(plugin, 'skills', 'electron-debug', 'SKILL.md');
const skill = fs.readFileSync(skillPath, 'utf8');
const errors = [];
if (manifest.name !== 'electron-agent-debug') errors.push('manifest name mismatch');
if (manifest.skills !== './skills/') errors.push('manifest skills path mismatch');
if (!Array.isArray(manifest.interface?.capabilities) || manifest.interface.capabilities.join(',') !== 'Read,Write,Execute') errors.push('manifest must disclose Read, Write and Execute');
if (!skill.startsWith('---\n') || !/^name: electron-debug$/m.test(skill) || !/^description: .+$/m.test(skill)) errors.push('skill frontmatter is incomplete');
for (const required of ['MCP', 'BLOCKED', 'dynamic', 'loopback']) if (!readme.toLowerCase().includes(required.toLowerCase())) errors.push('README missing ' + required);
if (/127\.0\.0\.1:\d{2,5}/.test(skill) || /localhost:\d{2,5}/.test(skill)) errors.push('skill contains a fixed endpoint');
if (errors.length) { for (const error of errors) console.error('ERROR ' + error); process.exit(1); }
const runtime = JSON.parse(fs.readFileSync(path.join(plugin, 'skills/electron-debug/runtime/manifest.json'), 'utf8'));
if (manifest.version !== runtime.skillVersion) throw new Error('Plugin and Skill versions differ');
console.log('Electron Agent Debug plugin validation passed.');
