#!/usr/bin/env node
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseBomCsv, parsePlacementCsv } from '../lib/domain.mjs';
import { findKicad, exportKicad } from '../lib/kicad.mjs';
import { VERSION } from '../lib/version.mjs';
import { readJson, readText, digest, ProjectError, importProject, projectStatus, checkProject, planChange, applyChange, exportHandoff, verifyHandoff } from '../lib/project.mjs';

const help = `PCBA Engineering ${VERSION} — local assembly package workflow

node /absolute/plugin/root/scripts/pcba.mjs <command> [options]

doctor                         Report Node and optional KiCad availability
kicad-export --board BOARD.kicad_pcb --output NEW-DIRECTORY
       [--schematic SCHEMATIC.kicad_sch] [--kicad /path/to/kicad-cli]
import --project DIR --input PACKAGE.json
import --project DIR --brief BRIEF.json --bom BOM.csv --placements POS.csv
       --units mm|in|mil [--mapping COLUMNS.json] [--bottom-view top-view|negated-x]
       [--authoritative COMPLETE-POS.csv]
status|resume --project DIR     Read persistent project state in a fresh chat
check --project DIR [--expect REVISION]
plan-change --project DIR --use-authoritative --reason TEXT
plan-change --project DIR --placements POS.csv|POS.json --reason TEXT [--units mm]
plan-change --project DIR --population REF-BOOLEANS.json --reason TEXT
apply-change --project DIR --plan PLAN.json
export --project DIR [--output NEW-DIRECTORY]
verify-handoff --directory HANDOFF-DIRECTORY

All command results are JSON. Exit 0 = command succeeded; 2 = consistency
check has findings; 1 = invalid input or failed operation. Source files are
never edited. A passing document check is not physical manufacturing approval.
`;
const schema = {
  doctor: ['kicad'], 'kicad-export': ['board', 'schematic', 'output', 'kicad'], import: ['project', 'input', 'brief', 'bom', 'placements', 'units', 'mapping', 'bottom-view', 'authoritative'],
  status: ['project'], resume: ['project'], check: ['project', 'expect'],
  'plan-change': ['project', 'placements', 'population', 'use-authoritative', 'reason', 'units', 'mapping', 'bottom-view'],
  'apply-change': ['project', 'plan'], export: ['project', 'output'], 'verify-handoff': ['directory']
};
function options(command, args) {
  const result = Object.create(null);
  for (let i = 0; i < args.length; i++) {
    const key = args[i].startsWith('--') ? args[i].slice(2) : '';
    if (!schema[command]?.includes(key) || key in result) throw new ProjectError('INVALID_ARGUMENT', `Unknown or repeated option: ${args[i]}`);
    if (key === 'use-authoritative') result[key] = true;
    else {
      const value = args[++i];
      if (!value || value.startsWith('--')) throw new ProjectError('INVALID_ARGUMENT', `Missing value for --${key}`);
      result[key] = value;
    }
  }
  return result;
}
function required(args, key) {
  if (!args[key]) throw new ProjectError('MISSING_ARGUMENT', `Supply --${key}.`);
  return args[key];
}
function fileEvidence(filename) {
  const text = readText(filename);
  return { name: path.basename(filename), sha256: digest(text), bytes: Buffer.byteLength(text) };
}
function placementFile(filename, args) {
  if (filename.toLowerCase().endsWith('.json')) {
    const input = readJson(filename);
    return Array.isArray(input) ? input : input.placements;
  }
  return parsePlacementCsv(readText(filename), { units: args.units, bottomConvention: args['bottom-view'], mapping: args.mapping ? readJson(args.mapping).placements : undefined });
}
function doctor(args) {
  const kicad = { ...findKicad(args.kicad), requiredForSuppliedExports: false };
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major < 22 || (major === 22 && minor < 19)) throw new ProjectError('NODE_VERSION', 'Node 22.19 or newer is required.');
  return { schemaVersion: 1, node: process.versions.node, pluginRoot: fileURLToPath(new URL('..', import.meta.url)), coreReady: true, networkRequired: false, machineConnectionRequired: false, kicad };
}
function run(command, args) {
  switch (command) {
    case 'doctor': return doctor(args);
    case 'kicad-export': return exportKicad({ board: required(args, 'board'), schematic: args.schematic, output: required(args, 'output'), kicad: args.kicad });
    case 'import': {
      const project = required(args, 'project');
      if (args.input) {
        if (['brief', 'bom', 'placements', 'authoritative', 'units', 'mapping', 'bottom-view'].some(k => k in args)) throw new ProjectError('AMBIGUOUS_IMPORT', 'Use either one canonical JSON input or explicit CSV inputs.');
        return importProject(project, readJson(args.input), [fileEvidence(args.input)]);
      }
      const brief = readJson(required(args, 'brief'));
      const mappings = args.mapping ? readJson(args.mapping) : {};
      const pkg = { ...brief, bom: parseBomCsv(readText(required(args, 'bom')), mappings.bom), placements: placementFile(required(args, 'placements'), args) };
      if (args.authoritative) pkg.authoritativePlacements = placementFile(args.authoritative, args);
      return importProject(project, pkg, [args.brief, args.bom, args.placements, args.authoritative, args.mapping].filter(Boolean).map(fileEvidence));
    }
    case 'status': case 'resume': return projectStatus(required(args, 'project'));
    case 'check': return checkProject(required(args, 'project'), args.expect);
    case 'plan-change': return planChange(required(args, 'project'), { placements: args.placements ? placementFile(args.placements, args) : undefined, population: args.population ? readJson(args.population) : undefined, useAuthoritative: args['use-authoritative'] ?? false, reason: required(args, 'reason'), provenance: [args.placements, args.population, args.mapping].filter(Boolean).map(fileEvidence) });
    case 'apply-change': return applyChange(required(args, 'project'), required(args, 'plan'));
    case 'export': return exportHandoff(required(args, 'project'), args.output);
    case 'verify-handoff': return verifyHandoff(required(args, 'directory'));
    default: throw new ProjectError('UNKNOWN_COMMAND', 'Run pcba.mjs --help for supported commands.');
  }
}
const [command, ...argv] = process.argv.slice(2);
if (!command || ['--help', 'help', '-h'].includes(command)) process.stdout.write(help);
else {
  try {
    const result = run(command, options(command, argv));
    process.stdout.write(JSON.stringify({ ok: true, command, ...result }, null, 2) + '\n');
    if (command === 'check' && result.report.status === 'fail') process.exitCode = 2;
  } catch (error) {
    process.stderr.write(JSON.stringify({ ok: false, command, error: { code: error.code ?? 'OPERATION_FAILED', message: error.message, ...(error.details ? { details: error.details } : {}) } }, null, 2) + '\n');
    process.exitCode = 1;
  }
}
