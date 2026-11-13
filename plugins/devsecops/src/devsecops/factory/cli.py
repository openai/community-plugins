"""Portable command surface for the developing Software Factory runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .contracts import PROFILES
from .credentials import initialize
from .doctor import inspect
from .lifecycle import LifecycleEngine, PACKAGE_ROOT, SCENARIO_ROOT, _load_inputs
from .state import MAX_ARTIFACT_BYTES, MAX_DOCUMENT_BYTES, RunJournal, StateError, _json, _read, _sha


def build_parser():
    parser = argparse.ArgumentParser(prog="factory-demo", description="DevSecOps Software Factory readiness and lifecycle")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Inspect local configuration and locks without connecting or creating infrastructure")
    doctor.add_argument("--profile", choices=PROFILES, required=True)
    doctor.add_argument("--config", type=Path, required=True)
    doctor.add_argument("--lock", type=Path)
    credentials = commands.add_parser("init-credentials", help="Create private bootstrap passwords for an authorized owned lab; does not create service accounts")
    credentials.add_argument("--directory", type=Path, required=True)
    credentials.add_argument("--environment-id", required=True)
    lifecycle = commands.add_parser("e2e", help="Start or resume the canonical lifecycle; missing operation handlers remain incomplete")
    location = lifecycle.add_mutually_exclusive_group(required=True)
    location.add_argument("--output", type=Path, help="New private run directory")
    location.add_argument("--resume", type=Path, help="Existing run directory with unchanged explicit inputs")
    lifecycle.add_argument("--scenario", choices=["ironbank-mission-recovery-v1"], default="ironbank-mission-recovery-v1")
    lifecycle.add_argument("--profile", choices=PROFILES, required=True)
    lifecycle.add_argument("--config", type=Path, required=True)
    lifecycle.add_argument("--lock", type=Path, required=True)
    lifecycle.add_argument("--environment", required=True, help="Owned environment identity matching the config and inventory")
    lifecycle.add_argument("--environment-inventory", type=Path, required=True, help="Explicit independently established environment inventory")
    lifecycle.add_argument("--package-manifest", type=Path, default=PACKAGE_ROOT / "SHA256SUMS")
    lifecycle.add_argument("--shared-lock-dir", type=Path, default=Path.home() / ".cache/devsecops/factory-locks")
    lifecycle.add_argument("--driver", choices=["current-host", "codex-cli"], default="current-host", help="current-host yields; codex-cli retains one native execution session")
    lifecycle.add_argument("--operation-backend", choices=["unavailable", "owned-linux"], default="unavailable",
                           help="Fixed owned Linux lifecycle; explicit production recipes and original execution evidence are required")
    lifecycle.add_argument("--resident-cleanup-controls", action="store_true",
        help="Keep the original codex-cli process for bounded explicit terminal cleanup reconciliation/continuation")
    lifecycle.add_argument("--native-selection", type=Path, help="Explicit pinned native client/model selection for codex-cli; resupply on resume")
    lifecycle.add_argument("--native-selection-sha256", help="Exact SHA256 of native selection bytes")
    lifecycle.add_argument("--plugin-id", help="Explicit installed plugin identity, for example devsecops@devsecops-local")
    lifecycle.add_argument("--plugin-version", help="Explicit installed version required by owned-linux")
    lifecycle.add_argument("--mission-git-invocation", type=Path, help="Explicit read-only mission selection; resupply on resume")
    lifecycle.add_argument("--mission-git-invocation-sha256", help="Exact SHA256 of the invocation bytes")
    lifecycle.add_argument("--owned-registry-recipe", type=Path, help="Explicit read-only registry recipe selection; resupply on resume")
    lifecycle.add_argument("--owned-registry-recipe-sha256", help="Exact SHA256 of the registry recipe bytes")
    for selection in ('owned-bootstrap-recipe', 'owned-operations-recipe', 'owned-cases-recipe'):
        lifecycle.add_argument('--' + selection, type=Path, help='Explicit pinned production selection; resupply on resume')
        lifecycle.add_argument('--' + selection + '-sha256', help='Exact SHA256 of selected recipe bytes')
    lifecycle.add_argument("--cleanup", choices=["always"], default="always")
    action = lifecycle.add_mutually_exclusive_group()
    action.add_argument("--native-result", type=Path, help="Exact pending native result; requires --resume")
    action.add_argument("--abort", action="store_true", help="Explicitly cancel a resumed run and perform any safely authorized cleanup")
    return parser


def _run_lifecycle(args):
    if getattr(args, 'resident_cleanup_controls', False) and args.driver != 'codex-cli':
        raise StateError('Resident cleanup controls require the codex-cli resident driver')
    if (args.native_result is not None or args.abort) and args.resume is None:
        raise StateError("Native results and explicit cancellation require --resume")
    if args.operation_backend == 'owned-linux':
        if not args.plugin_id or not args.plugin_version:
            raise StateError("The owned-linux backend requires explicit installed plugin ID and version")
    elif args.plugin_id is not None or args.plugin_version is not None:
        raise StateError("Installed plugin selection requires --operation-backend owned-linux")
    native_pair = (args.native_selection, args.native_selection_sha256)
    if args.driver == 'codex-cli':
        if (args.operation_backend != 'owned-linux' or any(value is None for value in native_pair)
                or args.native_result is not None):
            raise StateError('codex-cli requires owned-linux, an explicit native selection, and no imported result')
    elif any(value is not None for value in native_pair):
        raise StateError('Native client selection requires --driver codex-cli')
    auxiliary = {}
    if args.driver == 'codex-cli':
        auxiliary['native_selection'] = native_pair
    pairs = ((args.mission_git_invocation, args.mission_git_invocation_sha256),
             (args.owned_registry_recipe, args.owned_registry_recipe_sha256))
    if any((path is None) != (pin is None) for path, pin in pairs):
        raise StateError('Auxiliary paths and SHA256 pins must be supplied together')
    if any(path is not None for path, _ in pairs) and args.operation_backend != 'owned-linux':
        raise StateError('Auxiliary selection requires --operation-backend owned-linux')
    if args.mission_git_invocation is not None:
        from .mission_git_invocation import load_invocation
        invocation = load_invocation(*pairs[0])
        auxiliary['mission_git_invocation'] = pairs[0]
        auxiliary.update({'mission_git_' + name: (Path(pair['path']), pair['sha256'])
                          for name, pair in invocation['configurations'].items()})
    if args.owned_registry_recipe is not None:
        auxiliary['owned_registry_recipe'] = pairs[1]
    for name in ('owned_bootstrap_recipe', 'owned_operations_recipe', 'owned_cases_recipe'):
        pair = (getattr(args, name), getattr(args, name + '_sha256'))
        if (pair[0] is None) != (pair[1] is None):
            raise StateError('Production paths and SHA256 pins must be supplied together')
        if pair[0] is not None:
            if args.operation_backend != 'owned-linux':
                raise StateError('Production selection requires --operation-backend owned-linux')
            auxiliary[name] = pair
    inputs = {"scenario": SCENARIO_ROOT / "scenario.json", "assertions": SCENARIO_ROOT / "assertions.json",
              "config": args.config, "lock": args.lock, "package": args.package_manifest,
              "environment": args.environment_inventory}
    raw = {name: _read(path, MAX_ARTIFACT_BYTES if name == "package" else MAX_DOCUMENT_BYTES)
           for name, path in inputs.items()}
    identities = {name + "_sha256": _sha(value) for name, value in raw.items()}
    # Validate all explicit authority before creating a journal. Resume never
    # obtains config, credential references, or inventory paths from old evidence.
    _load_inputs(inputs, identities, args.environment)
    if _json(raw["config"])["profile"] != args.profile:
        raise StateError("Requested profile differs from the explicit operator configuration")
    native_selection = None
    if args.driver == 'codex-cli':
        from .native_driver import load_selection as load_native_selection
        native_selection = load_native_selection(*native_pair, identities=identities,
            environment_id=args.environment, plugin_id=args.plugin_id, plugin_version=args.plugin_version,
            config=_json(raw['config']), lock=_json(raw['lock']), package_manifest=args.package_manifest)
    from .auxiliary_inputs import load_selection, validate_temporal_scope
    selected_raw = load_selection(auxiliary, identities=identities, environment_id=args.environment,
                                  plugin_id=args.plugin_id, plugin_version=args.plugin_version)
    validate_temporal_scope(selected_raw, _json(raw['config']))
    journal = (RunJournal.open(args.resume, identities=identities) if args.resume is not None
               else RunJournal.create(args.output, identities=identities, environment_id=args.environment,
                                      cleanup=args.cleanup))
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=args.shared_lock_dir,
                             driver_id=args.operation_backend, plugin_id=args.plugin_id,
                             plugin_version=args.plugin_version, auxiliary_inputs=auxiliary)
    if args.abort:
        result = engine.interrupt()
    elif args.driver == 'codex-cli':
        from .native_driver import run
        result = run(engine, selection=native_selection,
                     cleanup_controls=getattr(args, 'resident_cleanup_controls', False))
    else:
        result = engine.advance(native_result=args.native_result)
    return {**result, "native_driver": args.driver, "operation_backend": engine.driver_id,
            "run_directory": str(journal.run_dir)}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init-credentials":
            print(json.dumps(initialize(args.directory, environment_id=args.environment_id), indent=2, sort_keys=True))
            return 0
        if args.command == "e2e":
            report = _run_lifecycle(args)
            print(json.dumps(report, indent=2, sort_keys=True))
            return report["exit_code"]
        report = inspect(args.config, profile=args.profile, lock_path=args.lock)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2 if report["gaps"] else 0
    except ValueError as error:
        message = str(error) if type(error) in (ValueError, StateError) else "Input validation failed"
        print(json.dumps({"execution_status": "error", "readiness": "unknown", "message": message}), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, RecursionError):
        print(json.dumps({"execution_status": "error", "readiness": "unknown", "message": "A required file is unavailable or invalid"}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"execution_status": "interrupted", "readiness": "unknown"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
