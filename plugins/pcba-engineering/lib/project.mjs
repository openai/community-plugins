import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { normalizePackage, checkPackage, parseBomCsv, parsePlacementCsv, emitBomCsv, emitPlacementsCsv } from './domain.mjs';
import { renderHtml, renderSvg } from './render.mjs';
import { VERSION } from './version.mjs';

export class ProjectError extends Error {
  constructor(code, message) { super(message); this.name = 'ProjectError'; this.code = code; }
}
export const digest = value => createHash('sha256').update(typeof value === 'string' || Buffer.isBuffer(value) ? value : JSON.stringify(value)).digest('hex');
const json = value => JSON.stringify(value, null, 2) + '\n';
const fail = (code, message) => { throw new ProjectError(code, message); };
const exists = p => { try { fs.lstatSync(p); return true; } catch (e) { if (e.code === 'ENOENT') return false; throw e; } };
export function readText(file) {
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink()) fail('UNSAFE_FILE', `Expected a regular file: ${file}`);
  if (stat.size > 25 * 1024 * 1024) fail('INPUT_LIMIT', 'Input exceeds 25 MiB. Split the package into a bounded board variant.');
  return fs.readFileSync(file, 'utf8');
}
export function readJson(file) {
  try { return JSON.parse(readText(file)); } catch (e) { if (e instanceof SyntaxError) fail('INVALID_JSON', `Invalid JSON: ${file}`); throw e; }
}
function contained(root, relative) {
  if (typeof relative !== 'string' || !relative || path.isAbsolute(relative) || relative.split(/[\\/]/).some(p => p === '..' || p === '.')) fail('UNSAFE_PATH', 'A contained relative path is required.');
  const target = path.resolve(root, relative);
  if (!target.startsWith(root + path.sep)) fail('UNSAFE_PATH', 'Path escapes the project.');
  let cursor = root;
  for (const segment of path.relative(root, target).split(path.sep)) {
    cursor = path.join(cursor, segment);
    if (exists(cursor) && fs.lstatSync(cursor).isSymbolicLink()) fail('UNSAFE_PATH', 'Symlinks are not accepted inside project or handoff directories.');
  }
  return target;
}
function rootDir(directory, create = false) {
  const resolved = path.resolve(directory);
  if (create) fs.mkdirSync(resolved, { recursive: true });
  const stat = fs.lstatSync(resolved);
  if (!stat.isDirectory() || stat.isSymbolicLink()) fail('UNSAFE_PATH', 'Project must be a regular directory.');
  return fs.realpathSync(resolved);
}
function atomicFile(file, content) {
  if (exists(file) && !fs.lstatSync(file).isFile()) fail('UNSAFE_PATH', 'Refusing to replace a non-file.');
  const temp = `${file}.tmp-${randomUUID()}`;
  try { fs.writeFileSync(temp, content, { flag: 'wx', mode: 0o600 }); fs.renameSync(temp, file); }
  finally { if (exists(temp)) fs.unlinkSync(temp); }
}
function locked(root, action) {
  const lock = contained(root, '.pcba-lock');
  let fd;
  try { fd = fs.openSync(lock, 'wx', 0o600); }
  catch (e) { if (e.code === 'EEXIST') fail('PROJECT_BUSY', 'Another operation owns the project lock. If a process crashed, inspect its PID in .pcba-lock before removing the stale lock.'); throw e; }
  try { fs.writeFileSync(fd, json({ pid: process.pid, createdAt: new Date().toISOString() })); return action(); }
  finally { fs.closeSync(fd); fs.unlinkSync(lock); }
}
function revisionPath(root, id) {
  if (!/^r[1-9][0-9]*-[a-f0-9]{12}$/.test(id)) fail('INVALID_REVISION', 'Invalid revision identifier.');
  return contained(root, `revisions/${id}`);
}
function openProject(directory) {
  const root = rootDir(directory);
  const state = readJson(contained(root, 'project.json'));
  if (state.schemaVersion !== 1 || typeof state.currentRevision !== 'string' || !Array.isArray(state.revisions)) fail('INVALID_PROJECT', 'Unsupported or damaged project manifest.');
  const entry = state.revisions.find(r => r.id === state.currentRevision);
  if (!entry) fail('INVALID_PROJECT', 'Current revision is not in the project history.');
  const location = revisionPath(root, entry.id);
  const input = readJson(contained(location, 'input.json'));
  if (digest(input) !== entry.inputSha256 || entry.id !== `r${entry.number}-${entry.inputSha256.slice(0, 12)}`) fail('REVISION_TAMPERED', 'Immutable revision content no longer matches its recorded digest.');
  const pkg = normalizePackage(input);
  return { root, state, entry, input: pkg, location };
}
function commitRevision(root, state, input, change) {
  const number = state.revisions.length + 1;
  const inputSha256 = digest(input);
  const id = `r${number}-${inputSha256.slice(0, 12)}`;
  const final = revisionPath(root, id);
  const report = checkPackage(input);
  let entry = { id, number, inputSha256, parent: state.currentRevision ?? null, createdAt: new Date().toISOString(), change };
  const recovered = exists(final);
  if (recovered) {
    entry = readJson(contained(final, 'revision.json'));
    if (entry.id !== id || entry.number !== number || entry.inputSha256 !== inputSha256 || entry.parent !== (state.currentRevision ?? null) || digest(entry.change) !== digest(change) || typeof entry.createdAt !== 'string' || !Number.isFinite(Date.parse(entry.createdAt))) fail('REVISION_CONFLICT', 'An orphan revision exists but does not match this exact candidate. No project state was changed.');
  }
  const contents = {
    'input.json': json(input), 'readiness.json': json(report), 'change-record.json': json(change), 'revision.json': json(entry),
    'assembly-guide.html': renderHtml(input, report, { revision: id, history: [...state.revisions, entry] }),
    'assembly-view.svg': renderSvg(input, report, { revision: id })
  };
  const next = { ...state, currentRevision: id, revisions: [...state.revisions, entry] };
  if (recovered) {
    if (fs.readdirSync(final).length !== Object.keys(contents).length || Object.entries(contents).some(([name, content]) => readText(contained(final, name)) !== content)) fail('REVISION_CONFLICT', 'An orphan revision has changed artifacts; automatic recovery was refused.');
    atomicFile(contained(root, 'project.json'), json(next));
    return { schemaVersion: 1, project: root, revision: id, report, recovered: true, reportPath: path.join(final, 'assembly-guide.html') };
  }
  const staging = contained(root, `.revision-${randomUUID()}`);
  fs.mkdirSync(staging);
  try {
    for (const [name, content] of Object.entries(contents)) fs.writeFileSync(path.join(staging, name), content, { flag: 'wx' });
    fs.mkdirSync(contained(root, 'revisions'), { recursive: true });
    fs.renameSync(staging, final);
    atomicFile(contained(root, 'project.json'), json(next));
    return { schemaVersion: 1, project: root, revision: id, report, reportPath: path.join(final, 'assembly-guide.html') };
  } catch (e) {
    // Published orphan revisions can be recovered only after exact artifact validation.
    if (exists(staging)) fs.rmSync(staging, { recursive: true });
    throw e;
  }
}
export function importProject(directory, input, provenance = []) {
  const pkg = normalizePackage(input);
  const root = rootDir(directory, true);
  return locked(root, () => {
    if (exists(contained(root, 'project.json'))) fail('PROJECT_EXISTS', 'This project already exists. Use status/resume or select a new project directory.');
    const state = { schemaVersion: 1, boardId: pkg.board.id, currentRevision: null, revisions: [], importedFiles: provenance };
    return commitRevision(root, state, pkg, { kind: 'import', reason: 'Import supplied engineering data without editing sources.', sourceFiles: provenance });
  });
}
export function projectStatus(directory) {
  const p = openProject(directory);
  return { schemaVersion: 1, project: p.root, revision: p.entry.id, board: p.input.board, variant: p.input.variant, quantity: p.input.quantity, history: p.state.revisions, report: checkPackage(p.input), reportPath: path.join(p.location, 'assembly-guide.html') };
}
export function checkProject(directory, expected) {
  const p = openProject(directory);
  if (expected && expected !== p.entry.id) fail('STALE_REVISION', 'Expected revision differs from the current revision.');
  return { schemaVersion: 1, revision: p.entry.id, inputSha256: p.entry.inputSha256, report: checkPackage(p.input) };
}
function changesBetween(before, after) {
  const changes = [];
  // CSV line numbers locate evidence; inserting a line does not relocate all
  // following components. Retain those trace changes separately and hash the
  // complete candidate, including every sourceRow, in the plan as before.
  const assemblyRow = row => {
    if (row === null) return null;
    const { sourceRow, ...data } = row;
    return data;
  };
  for (const collection of ['placements', 'bom']) {
    const old = new Map(before[collection].map(row => [row.ref, row]));
    const next = new Map(after[collection].map(row => [row.ref, row]));
    for (const ref of new Set([...old.keys(), ...next.keys()])) {
      const a = old.get(ref) ?? null, b = next.get(ref) ?? null;
      if (JSON.stringify(assemblyRow(a)) !== JSON.stringify(assemblyRow(b))) changes.push({ collection, ref, before: a, after: b });
    }
  }
  return changes;
}
function sourceRowChangesBetween(before, after) {
  const old = new Map(before.placements.map(row => [row.ref, row]));
  return after.placements.filter(row => old.has(row.ref) && old.get(row.ref).sourceRow !== row.sourceRow)
    .map(row => ({ ref: row.ref, before: old.get(row.ref).sourceRow ?? null, after: row.sourceRow ?? null }));
}
function assertBoundedCandidate(before, after) {
  const invariant = p => Object.fromEntries(Object.entries(p).filter(([key]) => !['bom', 'placements'].includes(key)));
  const identityRows = p => p.bom.map(({ populated, ...identity }) => identity);
  if (digest(invariant(before)) !== digest(invariant(after)) || digest(identityRows(before)) !== digest(identityRows(after))) {
    fail('CHANGE_OUT_OF_SCOPE', 'A bounded assembly change may alter placement rows or existing population flags only. Board identity, quantity, variant, source evidence, part identities and authoritative geometry must remain unchanged.');
  }
}
export function planChange(directory, { placements, population, useAuthoritative = false, reason, provenance = [] }) {
  const root = rootDir(directory);
  return locked(root, () => {
    const p = openProject(root);
    if (typeof reason !== 'string' || !reason.trim()) fail('REASON_REQUIRED', 'Explain the bounded change and its authoritative evidence.');
    if ([placements !== undefined, population !== undefined, useAuthoritative].filter(Boolean).length !== 1) fail('CHANGE_REQUIRED', 'Select exactly one replacement placement source, population map, or the imported authoritative placement source.');
    const candidate = structuredClone(p.input);
    if (useAuthoritative) {
      if (!candidate.authoritativePlacements?.length) fail('AUTHORITATIVE_SOURCE_REQUIRED', 'No complete authoritative placement source was supplied; BOM data cannot establish coordinates.');
      candidate.placements = structuredClone(candidate.authoritativePlacements).filter(row => candidate.bom.some(b => b.ref.toUpperCase() === row.ref.toUpperCase() && (b.populated || ['mechanical', 'fiducial'].includes(b.category)))).map(row => ({ ...row, ref: candidate.bom.find(b => b.ref.toUpperCase() === row.ref.toUpperCase()).ref }));
    } else if (placements !== undefined) {
      candidate.placements = placements;
    } else {
      if (!population || typeof population !== 'object' || Array.isArray(population) || Object.keys(population).length === 0) fail('INVALID_POPULATION', 'Supply a nonempty map of reference to boolean population state.');
      for (const [ref, value] of Object.entries(population)) {
        const component = candidate.bom.find(b => b.ref.toUpperCase() === ref.toUpperCase());
        if (!component || typeof value !== 'boolean') fail('INVALID_POPULATION', 'Population edits require known references and boolean values.');
        component.populated = value;
        if (!value) candidate.placements = candidate.placements.filter(r => r.ref.toUpperCase() !== ref.toUpperCase());
        else if (component.category === 'smt' && !candidate.placements.some(r => r.ref.toUpperCase() === ref.toUpperCase())) {
          const source = candidate.authoritativePlacements?.find(r => r.ref.toUpperCase() === ref.toUpperCase());
          if (!source) fail('AUTHORITATIVE_SOURCE_REQUIRED', `No authoritative placement is available for ${ref}.`);
          candidate.placements.push({ ...structuredClone(source), ref: component.ref });
        }
      }
    }
    const normalized = normalizePackage(candidate);
    assertBoundedCandidate(p.input, normalized);
    const changes = changesBetween(p.input, normalized);
    if (!changes.length) fail('NO_CHANGE', 'The requested data already matches the current revision.');
    const sourceRowChanges = sourceRowChangesBetween(p.input, normalized);
    const plan = { schemaVersion: 1, boardId: p.input.board.id, baseRevision: p.entry.id, baseInputSha256: p.entry.inputSha256, candidateInputSha256: digest(normalized), reason, provenance, changes, sourceRowChanges, candidate: normalized, before: checkPackage(p.input), after: checkPackage(normalized) };
    const planId = digest(plan);
    const envelope = { ...plan, planId };
    fs.mkdirSync(contained(root, 'plans'), { recursive: true });
    const file = contained(root, `plans/${planId}.json`);
    if (!exists(file)) atomicFile(file, json(envelope));
    return { schemaVersion: 1, planId, planPath: file, baseRevision: p.entry.id, reason, changes, sourceRowChanges, before: plan.before, after: plan.after, candidateInputSha256: plan.candidateInputSha256 };
  });
}
export function applyChange(directory, planFile) {
  const root = rootDir(directory);
  return locked(root, () => {
    const p = openProject(root);
    const plan = readJson(planFile);
    const { planId, ...payload } = plan;
    if (typeof planId !== 'string' || digest(payload) !== planId) fail('PLAN_TAMPERED', 'The change plan no longer matches its digest.');
    const storedFile = contained(root, `plans/${planId}.json`);
    if (!exists(storedFile) || digest(readJson(storedFile)) !== digest(plan)) fail('UNKNOWN_PLAN', 'Use a change plan generated for this project.');
    const prior = p.state.revisions.find(r => r.change?.planId === planId);
    if (prior) return { schemaVersion: 1, applied: false, idempotent: true, revision: prior.id, currentRevision: p.entry.id };
    if (p.entry.id !== plan.baseRevision || p.entry.inputSha256 !== plan.baseInputSha256) fail('STALE_PLAN', 'The project changed after this plan was created. Preview a fresh plan; nothing was changed.');
    const candidate = normalizePackage(plan.candidate);
    assertBoundedCandidate(p.input, candidate);
    if (digest(candidate) !== plan.candidateInputSha256 || digest(changesBetween(p.input, candidate)) !== digest(plan.changes)) fail('PLAN_TAMPERED', 'Candidate content or exact diff does not match the plan.');
    if (digest(sourceRowChangesBetween(p.input, candidate)) !== digest(plan.sourceRowChanges ?? [])) fail('PLAN_TAMPERED', 'CSV source-row trace changes do not match the plan.');
    return { ...commitRevision(root, p.state, candidate, { kind: 'bounded-change', planId, baseRevision: plan.baseRevision, reason: plan.reason, provenance: plan.provenance, changes: plan.changes, sourceRowChanges: plan.sourceRowChanges ?? [] }), applied: true };
  });
}
const requiredArtifacts = ['input.json', 'brief.json', 'source-manifest.json', 'population.csv', 'placements.csv', 'assembly-view.svg', 'assembly-guide.html', 'readiness.json', 'change-record.json', 'inspection-plan.md', 'handoff.md'];
function markdownArtifacts(pkg, revision) {
  const md = value => String(value).replace(/[\r\n]+/g, ' ').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]).replace(/[\\`*_{}\[\]()#+.!|]/g, '\\$&');
  return {
    'inspection-plan.md': `# Assembly inspection plan\n\nBoard: ${md(pkg.board.name)}; revision ${md(pkg.board.revision)}; variant ${md(pkg.variant)}; quantity ${pkg.quantity}.\n\n1. Confirm the work order, board revision and variant with the responsible engineer.\n2. Confirm material identity, availability, polarity, pin 1 and orientation against authoritative component and assembly documentation.\n3. Compare populated references and placements with population.csv and placements.csv.\n4. Independently review bottom-side orientation and machine-specific offsets before any production use.\n5. Perform the electrical, process, soldering and inspection checks required by the responsible manufacturer.\n\nThis packet establishes document consistency only. Material availability, electrical qualification and manufacturing authorization are not established.\n`,
    'handoff.md': `# PCBA assembly handoff\n\nRevision: ${revision}\n\nOpen assembly-guide.html locally. The packet is self-contained and requires no web server.\n\nDocument consistency: verified by re-reading the exported population and placement files.\n\nMaterial readiness: unknown. Electrical qualification and manufacturing authorization: not assessed.\n\nRead readiness.json for remaining evidence gaps and source findings. Source identities and upstream notices are identified in source-manifest.json. Do not infer physical build acceptance from document consistency.\n`
  };
}
export function exportHandoff(directory, output) {
  const root = rootDir(directory);
  return locked(root, () => {
    const p = openProject(root);
    const report = checkPackage(p.input);
    if (report.status !== 'pass') fail('PACKAGE_INCONSISTENT', 'Document consistency has blocking findings. The review remains available; correct the package before exporting a verified handoff.');
    const dest = output ? path.resolve(output) : contained(root, `deliveries/${p.entry.id}`);
    if (dest.startsWith(root + path.sep)) contained(root, path.relative(root, dest));
    if (exists(dest)) fail('OUTPUT_EXISTS', 'Use a new output directory; existing handoffs are immutable.');
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    const parent = fs.realpathSync(path.dirname(dest));
    const final = path.join(parent, path.basename(dest));
    const staging = path.join(parent, `.pcba-export-${randomUUID()}`);
    fs.mkdirSync(staging);
    const pkg = p.input;
    const contents = {
      'input.json': json(pkg),
      'brief.json': json({ schemaVersion: 1, board: pkg.board, variant: pkg.variant, quantity: pkg.quantity, conventions: pkg.conventions }),
      'source-manifest.json': json({ schemaVersion: 1, importedFiles: p.state.importedFiles, source: pkg.source ?? {}, inputSha256: p.entry.inputSha256 }),
      'population.csv': emitBomCsv(pkg),
      'placements.csv': emitPlacementsCsv(pkg),
      'assembly-view.svg': renderSvg(pkg, report, { revision: p.entry.id }),
      'assembly-guide.html': renderHtml(pkg, report, { revision: p.entry.id, history: p.state.revisions }),
      'readiness.json': json(report),
      'change-record.json': json({ schemaVersion: 1, currentRevision: p.entry.id, history: p.state.revisions }),
      ...markdownArtifacts(pkg, p.entry.id)
    };
    try {
      const files = [];
      for (const [name, content] of Object.entries(contents)) {
        fs.writeFileSync(path.join(staging, name), content, { flag: 'wx' });
        files.push({ path: name, sha256: digest(content), bytes: Buffer.byteLength(content) });
      }
      const manifest = { schemaVersion: 1, plugin: 'pcba-engineering', pluginVersion: VERSION, revision: p.entry.id, inputSha256: p.entry.inputSha256, files };
      fs.writeFileSync(path.join(staging, 'manifest.json'), json(manifest), { flag: 'wx' });
      const verification = verifyHandoff(staging);
      if (!verification.valid) fail('EXPORT_VERIFICATION_FAILED', 'Independent readback rejected the candidate handoff.');
      // Exclusive directory reservation prevents replacement even if an empty
      // destination appears during verification. The manifest is the commit marker.
      try { fs.mkdirSync(final); } catch (e) { if (e.code === 'EEXIST') fail('OUTPUT_EXISTS', 'The output directory appeared during export and was preserved.'); throw e; }
      for (const file of files) fs.renameSync(path.join(staging, file.path), path.join(final, file.path));
      fs.renameSync(path.join(staging, 'manifest.json'), path.join(final, 'manifest.json'));
      fs.rmdirSync(staging);
      return { schemaVersion: 1, revision: p.entry.id, output: final, guide: path.join(final, 'assembly-guide.html'), verification: verifyHandoff(final) };
    } catch (e) { if (exists(staging)) fs.rmSync(staging, { recursive: true }); throw e; }
  });
}
export function verifyHandoff(directory) {
  const root = rootDir(directory);
  const manifest = readJson(contained(root, 'manifest.json'));
  if (manifest.schemaVersion !== 1 || manifest.plugin !== 'pcba-engineering' || !Array.isArray(manifest.files)) fail('INVALID_MANIFEST', 'Unsupported handoff manifest.');
  if (manifest.pluginVersion !== VERSION) fail('UNSUPPORTED_HANDOFF_VERSION', `Verify this packet with its recorded plugin version (${manifest.pluginVersion}); this verifier is ${VERSION}.`);
  const names = manifest.files.map(f => f.path);
  if (names.length !== requiredArtifacts.length || new Set(names).size !== names.length || requiredArtifacts.some(n => !names.includes(n))) fail('INVALID_MANIFEST', 'The handoff must list every required artifact exactly once.');
  if (fs.readdirSync(root).some(n => n !== 'manifest.json' && !names.includes(n))) fail('UNDECLARED_ARTIFACT', 'The handoff contains a file that is not declared in its manifest.');
  for (const file of manifest.files) {
    const data = readText(contained(root, file.path));
    if (digest(data) !== file.sha256 || Buffer.byteLength(data) !== file.bytes) fail('ARTIFACT_TAMPERED', `Artifact content does not match its digest: ${file.path}`);
  }
  const input = readJson(contained(root, 'input.json'));
  if (digest(input) !== manifest.inputSha256) fail('INPUT_TAMPERED', 'Handoff input differs from its recorded revision.');
  const pkg = normalizePackage(input);
  const bom = parseBomCsv(readText(contained(root, 'population.csv')));
  const placements = parsePlacementCsv(readText(contained(root, 'placements.csv')), { units: 'mm', bottomConvention: 'top-view' });
  const reconstructed = normalizePackage({ ...pkg, bom, placements });
  // Independently parse exported machine-readable documents; never trust the stored pass flag.
  const projectBom = rows => rows.map(r => [r.ref, r.partId, r.value, r.package, r.category, r.populated, r.mpn ?? null]);
  const projectPlacements = rows => rows.map(r => [r.ref, r.xMm, r.yMm, r.rotationDeg, r.side, r.package ?? '']);
  if (digest(projectBom(bom)) !== digest(projectBom(pkg.bom)) || digest(projectPlacements(placements)) !== digest(projectPlacements(pkg.placements))) fail('EXPORT_MISMATCH', 'Exported rows differ from the immutable source snapshot.');
  const report = checkPackage(reconstructed);
  if (report.status !== 'pass') fail('PACKAGE_INCONSISTENT', 'Exported files have document consistency failures.');
  const stored = readJson(contained(root, 'readiness.json'));
  if (digest(checkPackage(pkg)) !== digest(stored)) fail('READINESS_MISMATCH', 'Stored readiness no longer matches a fresh independent check.');
  const brief = readJson(contained(root, 'brief.json'));
  if (digest(brief.board) !== digest(pkg.board) || brief.variant !== pkg.variant || brief.quantity !== pkg.quantity || digest(brief.conventions) !== digest(pkg.conventions)) fail('BRIEF_MISMATCH', 'Brief and exported engineering data disagree.');
  const source = readJson(contained(root, 'source-manifest.json'));
  const history = readJson(contained(root, 'change-record.json'));
  if (source.inputSha256 !== manifest.inputSha256 || digest(source.source) !== digest(pkg.source ?? {})) fail('SOURCE_MISMATCH', 'Source manifest and immutable input disagree.');
  if (history.currentRevision !== manifest.revision || !history.history?.some(r => r.id === manifest.revision && r.inputSha256 === manifest.inputSha256)) fail('HISTORY_MISMATCH', 'Change history does not bind the current revision to this input.');
  if (readText(contained(root, 'assembly-view.svg')) !== renderSvg(pkg, stored, { revision: manifest.revision }) || readText(contained(root, 'assembly-guide.html')) !== renderHtml(pkg, stored, { revision: manifest.revision, history: history.history })) fail('GUIDE_MISMATCH', 'Visual assembly artifacts differ from the verified source snapshot and findings.');
  for (const [name, content] of Object.entries(markdownArtifacts(pkg, manifest.revision))) if (readText(contained(root, name)) !== content) fail('HANDOFF_TEXT_MISMATCH', 'Handoff or inspection text differs from the verified scope and source snapshot.');
  return { schemaVersion: 1, valid: true, directory: root, revision: manifest.revision, filesChecked: manifest.files.length, inputSha256: manifest.inputSha256, checks: ['all-artifact-hashes', 'complete-manifest', 'independent-csv-readback', 'population-and-placement-equality', 'fresh-document-consistency', 'brief-identity', 'source-and-history-identity', 'visual-artifact-consistency'], scope: report.scope };
}
