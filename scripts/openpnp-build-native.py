#!/usr/bin/env python3
"""Build/test the current bridge in fresh owned output, without publishing a package."""
import argparse, datetime, hashlib, importlib.util, json, os, shutil, subprocess, tarfile, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
PIN = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c'
TESTS = (
    'NativeActionLedgerTest', 'NativeBridgeDocumentRestartTest',
    'NativeExecutorOwnershipTest', 'NativeLedgerRecoveryTest',
    'NativeJournalInputBoundaryTest', 'NativeEffectBoundaryTest',
    'NativeWrapperCompletionTest', 'NativeWrapperExceptionalTest',
    'NativeMaterialLoadsWorkflowTest', 'NativeMaterialLoadsBoundaryTest', 'NativeMaterialLoadsRetryTest', 'NativeControllerRecoveryOrderTest', 'NativeJournalExactNumbersTest',
    'NativeControllerDiagnosticTest', 'NativeControllerJournalReducerTest',
    'NativeControllerCrashRecoveryTest',
    'NativeBridgeTest', 'NativeCapabilityTest', 'NativeRecoveryTest', 'NativeLegacyOperationHistoryTest',
    'NativeBoundaryTest', 'NativeImportBoundaryTest', 'NativePartBindingsTest', 'NativePartBindingsBridgeTest', 'NativeTransformTest',
    'NativeGraphLimitsTest', 'NativeStorageBoundaryTest', 'NativeSettingsTest', 'NativeAxisBacklashTest', 'NativeAxisBacklashBridgeTest', 'NativeNozzleAssemblyTest', 'NativeNozzleAssemblyBridgeTest', 'NativeNozzleAssemblyCrashTest', 'NativeTopologyJournalTest',
    'PlannerSettingsTest', 'NativeConfigurationSnapshotsTest',
    'NativeConfigurationSnapshotsDecodeTest', 'NativeJobPreflightTest',
    'NativeJobDocumentsTest', 'NativeJobDocumentsRestartTest',
    'NativeDocumentStoreCrashTest', 'NativeSustainedTest',
    'NativeVisionSettingsTest', 'NativeVisionSettingsReviewTest', 'NativeVisionIntegrationTest',
    'NativePlacementEditsTest', 'NativeCanonicalIdentityTest',
    'NativeBoardHistoryAdmissionTest', 'NativeBoardHistoryScaleTest',
    'NativeBoardLoadsTest', 'NativeBoardLoadBridgeTest', 'NativeBoardLoadsReviewTest',
    'NativePortableBacklashJourneyTest', 'NativePortableBoardLibraryTest', 'NativePortablePanelLibraryTest', 'NativePortablePanelLibraryBoundaryTest', 'NativePortableConfigurationTest', 'NativePortableAllocationTest','NativePortablePipelineTest', 'NativePortableJourneyTest',
    'NativeCameraSettlingTest', 'NativeCameraSettlingBridgeTest', 'NativeCameraRegistrationSuiteTest',
    'NativeCameraScaleFitTest', 'NativeCameraScaleRuntimeTest', 'NativeCameraScaleBridgeTest',
    'NativeCameraScaleProposalBoundaryTest', 'NativeCameraScaleMeasurementSuiteTest',
    'NativeMappedAxisBridgeTest', 'NativeJobSteppingTest', 'NativeJobSteppingRaceTest',
    'NativeStepScalarResumeTest',
    'NativeJobLineageTest', 'NativeLineageBridgeTest', 'NativeDocumentLineageTest', 'NativePlacementStructureTest', 'NativePanelStructureTest', 'NativePanelBoardMembershipBridgeTest',
    'NativeLineageIdentityBoundaryTest', 'NativeLineageCompletedJobTest',
    'NativeLoadedBoardInspectionTest', 'NativeInspectionJournalTest', 'NativeBoardInspectionBridgeTest',
    'NativeVacuumSensingTest', 'NativeVacuumSettingsTest', 'NativeVacuumOperationsTest', 'NativeVacuumJournalTest',
    'NativeVacuumSourcesTest', 'NativeVacuumConfigurationIntegrationTest', 'NativeVacuumPortableTest', 'NativeVacuumBridgeSuiteTest',
)
def run(args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true', help=f'Run the {len(TESTS)} simulator native mains, including controller process-death cases; GUI and the broader crash matrix remain separate')
    parser.add_argument('--output', type=Path, help='New output directory below this checkout; otherwise validation/native-build-<unique>')
    args = parser.parse_args()
    source_value = os.environ.get('OPENPNP_SOURCE')
    if not source_value:
        parser.error('Set OPENPNP_SOURCE to the clean exact pinned OpenPnP checkout')
    source = Path(source_value).expanduser().resolve()
    if subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip() != PIN:
        raise ValueError('OpenPnP source commit does not match the required pin')
    run(['git', '-C', source, 'diff', '--quiet', 'HEAD', '--'])
    java_value = os.environ.get('JAVA_HOME')
    javac = shutil.which('javac')
    java_home = Path(java_value).expanduser().resolve() if java_value else Path(javac).resolve().parent.parent if javac else None
    if java_home is None or not (java_home / 'bin/javac').is_file():
        parser.error('Set JAVA_HOME to a JDK17+ installation (or put its javac on PATH)')
    work = (args.output or Path(os.environ.get('OPENPNP_BUILD_DIR', ROOT / 'validation' / ('native-build-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])))).resolve()
    if not work.is_relative_to(ROOT) or work == ROOT or work.exists():
        parser.error('Build output must be a new directory below this checkout')
    runtime_value = os.environ.get('OPENPNP_RUNTIME')
    runtime = Path(runtime_value).expanduser().resolve() if runtime_value else source / 'target/codex-runtime'
    if runtime_value and not (runtime / 'codex-build-manifest.json').is_file():
        parser.error('OPENPNP_RUNTIME requires an existing hash-inventoried stock distribution')
    if (runtime / 'codex-build-manifest.json').is_file():
        spec = importlib.util.spec_from_file_location('openpnp_native_gui_builder', ROOT / 'scripts/openpnp-build-native-gui.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        builder.validate_stock_runtime(runtime)
    work.mkdir(parents=True)
    stock_built = False
    if not (runtime / 'codex-build-manifest.json').is_file():
        # A first build uses a complete archive of tracked pinned source; Maven never
        # writes into OPENPNP_SOURCE, its target tree, or an existing runtime.
        maven = shutil.which('mvn')
        if not maven:
            parser.error('A first stock build needs Maven3.9+ on PATH; alternatively provide OPENPNP_RUNTIME')
        stock_source = work / 'stock-source'
        stock_source.mkdir()
        archive = work / 'stock-source.tar'
        run(['git', '-C', source, 'archive', '--format=tar', '--output', archive, PIN])
        with tarfile.open(archive) as tar:
            tar.extractall(stock_source, filter='data')
        archive.unlink()
        env = dict(os.environ, JAVA_HOME=str(java_home), PATH=str(java_home / 'bin') + os.pathsep + os.environ.get('PATH', ''))
        command = [maven, '-q', '-B', '-DskipTests']
        if os.environ.get('OPENPNP_MAVEN_CACHE'):
            command.append('-Dmaven.repo.local=' + str(Path(os.environ['OPENPNP_MAVEN_CACHE']).expanduser().resolve()))
        run([*command, 'package'], cwd=stock_source, env=env)
        runtime = work / 'stock-runtime'
        (runtime / 'lib').mkdir(parents=True)
        native = stock_source / 'target/openpnp-gui-0.0.1-alpha-SNAPSHOT.jar'
        shutil.copy2(native, runtime / native.name)
        libs = sorted((stock_source / 'target/lib').glob('*.jar'))
        if not libs:
            raise ValueError('Pinned stock build produced no runtime dependencies')
        for lib in libs:
            shutil.copy2(lib, runtime / 'lib' / lib.name)
        shutil.copytree(stock_source / 'samples/pnp-test', runtime / 'samples/pnp-test')
        shutil.copy2(stock_source / 'LICENSE.txt', runtime / 'LICENSE.txt')
        manifest = {'upstream_commit': PIN, 'gui_jar': native.name, 'samples_directory': 'samples', 'libs_directory': 'lib', 'build_method': 'Maven package in fresh archived pinned source; input checkout never modified', 'files': [{'path': f.relative_to(runtime).as_posix(), 'sha256': sha(f)} for f in sorted(runtime.rglob('*')) if f.is_file()]}
        (runtime / 'codex-build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        stock_built = True
    build = work / 'build'
    run([os.environ.get('PYTHON', 'python3'), ROOT / 'scripts/openpnp-build-native-gui.py', '--stock-source', source, '--stock-runtime', runtime, '--java-home', java_home, '--output', build])
    if args.test:
        command = [os.environ.get('PYTHON', 'python3'), ROOT / 'scripts/openpnp-test-native-candidate.py', '--build', build, '--java-home', java_home, '--run', work / 'native-tests']
        for test in TESTS:
            command += ['--test', test]
        run(command)
    result = {'build': str(build), 'runtime': str(build / 'runtime'), 'bridge_sha256': sha(build / 'openpnp-codex-bridge.jar'), 'stock_built_in_owned_output': stock_built, 'native_test_count': len(TESTS) if args.test else 0, 'gui_tests_executed': False, 'package_published': False, 'input_source_modified': False}
    (work / 'canonical-build-receipt.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
if __name__ == '__main__':
    main()
