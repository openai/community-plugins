#!/usr/bin/env python3
"""Run one isolated real-GUI test using a candidate-owned patched runtime. Never uses a live connection."""
import argparse,hashlib,importlib.util,json,os,signal,time,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('openpnp_owned_native_runner',ROOT/'scripts/openpnp-test-native-mcp.py')
owned=importlib.util.module_from_spec(spec);spec.loader.exec_module(owned)
signal.signal(signal.SIGTERM,owned.interrupt);signal.signal(signal.SIGINT,owned.interrupt)
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);p.add_argument('--java-home',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--node',type=Path,help='Explicit Node 22+ executable required for --board-inspection');m=p.add_mutually_exclusive_group();m.add_argument('--unknown-exit',action='store_true');m.add_argument('--topology',action='store_true');m.add_argument('--panel-membership',action='store_true');m.add_argument('--panel-takeover',action='store_true');m.add_argument('--title',action='store_true');m.add_argument('--board-inspection',action='store_true');a=p.parse_args()
build=a.build.resolve();run=a.run.resolve();runtime=build/'runtime'
if a.board_inspection and (a.node is None or not a.node.resolve().is_file() or not os.access(a.node.resolve(),os.X_OK)):raise ValueError('--board-inspection requires an explicit executable --node (Node 22+)')
if not build.is_relative_to(ROOT) or not run.is_relative_to(ROOT) or run.exists():raise ValueError('Use candidate build and a new candidate run directory')
run.mkdir(parents=True);manifest=json.loads((runtime/'codex-build-manifest.json').read_text());files={f['path']:f['sha256'] for f in manifest['files']}
for name,digest in files.items():
 f=(runtime/name).resolve();assert f.is_relative_to(runtime) and sha(f)==digest
libs=[runtime/name for name in sorted(files) if name.startswith('lib/') and name.endswith('.jar')]
test_name=('NativeGuiBoardInspectionTest' if a.board_inspection else 'NativeGuiTitleTest' if a.title else 'NativeGuiPanelMembershipTakeoverTest' if a.panel_takeover else 'NativeGuiPanelMembershipTest' if a.panel_membership else 'NativeGuiTopologyTest' if a.topology else 'NativeGuiOwnershipTest')
# Compile the selected test and its explicit helper closure from immutable per-run snapshots.
# Never execute stale build/test-classes while attributing a receipt to current source bytes.
families={test_name}
if not a.title:families.add('NativeGuiOwnershipTest')
if a.panel_takeover or a.board_inspection:families.add('NativeGuiPanelMembershipTest')
source_dir=run/'sources';source_dir.mkdir();classes=run/'test-classes';classes.mkdir()
source_bindings={};snapshots=[]
for name in sorted(families):
 source=ROOT/'tests/openpnp/native'/(name+'.java');snapshot=source_dir/source.name
 if source.is_symlink() or not source.is_file():raise ValueError('Expected regular test source: '+str(source))
 snapshot.write_bytes(source.read_bytes());source_bindings[source.relative_to(ROOT).as_posix()]=sha(snapshot);snapshots.append(snapshot)
native_jars=[runtime/manifest['gui_launcher_jar'],runtime/manifest['gui_jar'],*libs]
native_classpath=os.pathsep.join(map(str,native_jars))
javac=a.java_home.resolve()/'bin/javac'
compile_exit=None;compiled={};compilation_verified=False
try:
 compile_exit=owned.run_logged([str(javac),'--release','17','-sourcepath','','-cp',native_classpath,'-d',str(classes),*map(str,snapshots)],ROOT,owned.clean_environment(),run/'compile.log',60)
 if compile_exit!=0:raise RuntimeError('Fresh GUI fixture compilation failed')
 compiled={p.relative_to(classes).as_posix():sha(p) for p in sorted(classes.rglob('*.class'))}
 expected={'org/openpnp/codex/'+name for name in families}
 if not compiled or any(name.removesuffix('.class').split('$')[0] not in expected for name in compiled) or any(name+'.class' not in compiled for name in expected):raise ValueError('Unexpected or missing GUI test class family')
 for artifact in [build/'openpnp-codex-bridge.jar',*native_jars]:
  with zipfile.ZipFile(artifact) as archive:
   if set(compiled).intersection(archive.namelist()):raise ValueError('GUI test overlay collides with production classes')
 if any(sha(ROOT/path)!=digest for path,digest in source_bindings.items()):raise ValueError('GUI test source changed during compilation')
 compilation_verified=True
finally:
 compile_receipt={'exit_code':compile_exit,'java_release':17,'source_sha256':source_bindings,'compiled_class_sha256':compiled,'compiler_executable_sha256':sha(javac),'compile_log_sha256':sha(run/'compile.log'),'native_classpath_sha256':{p.relative_to(runtime).as_posix():sha(p) for p in native_jars},'production_class_collisions_checked':compilation_verified}
 if (run/'compile.process.json').is_file():compile_receipt['process_cleanup']=json.loads((run/'compile.process.json').read_text())
 (run/'compile-receipt.json').write_text(json.dumps(compile_receipt,indent=2)+'\n')
# Bridge is deliberately absent from system classpath. The native script performs URLClassLoader attachment.
classpath=os.pathsep.join(map(str,[classes,*native_jars]))
command=[str(a.java_home.resolve()/'bin/java'),'-Xmx2g','-XX:+ExitOnOutOfMemoryError','--add-opens=java.base/java.lang=ALL-UNNAMED','--add-opens=java.desktop/java.awt=ALL-UNNAMED','--add-opens=java.desktop/java.awt.color=ALL-UNNAMED','--add-exports=java.desktop/com.apple.eawt=ALL-UNNAMED','-cp',classpath,'org.openpnp.codex.'+test_name,str(runtime),str(build/'openpnp-codex-bridge.jar'),str(ROOT/'plugins/openpnp/bridge/bootstrap.js'),str(run/'fixture')]
if a.title:command=command[:-4]+[str(run/'fixture')]
for directory in ('home','system-preferences','tmp'): (run/directory).mkdir(mode=0o700)
command[1:1]=['-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory','-Djava.util.prefs.systemRoot='+str(run/'system-preferences'),'-Duser.home='+str(run/'home'),'-Djava.io.tmpdir='+str(run/'tmp')]
if a.unknown_exit:command.insert(1,'-Dopenpnp.codex.test.unknownExit=true')
if a.board_inspection:command[1:1]=['-Dopenpnp.codex.test.node='+str(a.node.resolve()),'-Dopenpnp.codex.test.mcpHelper='+str(ROOT/'tests/openpnp/native-inspection-gui-mcp.mjs'),'-Dopenpnp.codex.test.mcpServer='+str(ROOT/'plugins/openpnp/mcp/server.mjs')]
started=time.time();exit_code=None
try:
 exit_code=owned.run_logged(command,ROOT,owned.clean_environment(),run/'native.log',240)
finally:
 receipt={'exit_code':exit_code,'elapsed_seconds':time.time()-started,'bridge_sha256':sha(build/'openpnp-codex-bridge.jar'),'native_manifest_sha256':sha(runtime/'codex-build-manifest.json'),'native_log_sha256':sha(run/'native.log'),'test_source_sha256':source_bindings['tests/openpnp/native/'+test_name+'.java'],'bootstrap_sha256':sha(ROOT/'plugins/openpnp/bridge/bootstrap.js'),'simulation_only':True,'physical_qualification':False,'gui_test_application_classpath_has_bridge':False}
 receipt.update(fresh_test_compilation=compile_receipt,compile_receipt_sha256=sha(run/'compile-receipt.json'))
 receipt['test_sources_unchanged']=all(sha(ROOT/path)==digest for path,digest in source_bindings.items())
 receipt['compiled_classes_unchanged']=compiled=={p.relative_to(classes).as_posix():sha(p) for p in sorted(classes.rglob('*.class'))}
 receipt.update(runner_sha256=sha(Path(__file__)),owned_process_helper_sha256=sha(ROOT/'scripts/openpnp-test-native-mcp.py'),jvm_environment_sanitized=True,private_home_and_tmp=True,desktop_gestures_performed=False)
 if a.board_inspection:receipt.update(mcp_helper_sha256=sha(ROOT/'tests/openpnp/native-inspection-gui-mcp.mjs'),mcp_server_sha256=sha(ROOT/'plugins/openpnp/mcp/server.mjs'),node_executable_sha256=sha(a.node.resolve()))
 cleanup=run/'native.process.json'
 if cleanup.is_file():receipt['process_cleanup']=json.loads(cleanup.read_text())
 (run/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
if not receipt['test_sources_unchanged'] or not receipt['compiled_classes_unchanged']:raise ValueError('GUI fixture source or compiled classes changed during execution')
if a.board_inspection:
 proof=json.loads((run/'fixture/board-inspection-gui-proof.json').read_text())
 assert exit_code==0 and proof['functional_checks_passed'] and proof['native_complete_placements']==8 and proof['native_action_pairs']==32
 assert proof['bridge_detached'] and proof['machine_closed'] and proof['all_owned_windows_disposed'] and proof['wrapper_test_listeners_detached'] and not proof['errors']
 assert proof['native_gui_component_api'] and proof['bridge_on_application_classpath'] is False and proof['desktop_gestures_performed'] is False and proof['physical_qualification'] is False
 assert proof['successful_task']['submission_operation']['state']=='succeeded' and proof['successful_task']['receipt']['result']['passed_count']==2
 assert proof['consumed_failure_task']['submission_operation']['state']=='failed' and proof['revoked_task']['submission_operation']['state']=='failed'
 assert proof['mcp']['passed'] and proof['mcp']['catalog_count']==61 and proof['mcp']['server_pid_gone'] and proof['mcp']['server_gone_before_sdk_close']
 assert proof['mcp_child']['exited'] and not proof['mcp_child']['forced_cleanup'] and proof['mcp_child']['captured_descendants_alive']==0
 assert proof['mcp']['mcp_server_sha256']==receipt['mcp_server_sha256'] and proof['mcp']['helper_sha256']==receipt['mcp_helper_sha256']
 assert proof['mcp_proof_sha256']==sha(run/'fixture/inspection-mcp-proof.json')
 assert proof['mcp']['task']['task_id']==proof['consumed_failure_task']['task_id']
 assert any(call['name']=='openpnp_request_board_inspection' and not call['is_error'] for call in proof['mcp']['tool_calls']) and any(call['name']=='openpnp_get_board_inspection' and not call['is_error'] for call in proof['mcp']['tool_calls'])
 receipt['board_inspection_gui_verified']=True;receipt['actual_native_complete_placements']=8;receipt['actual_native_action_pairs']=32;receipt['proof_sha256']=sha(run/'fixture/board-inspection-gui-proof.json');(run/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
if a.title:
 proof=json.loads((run/'fixture/result.json').read_text())
 assert exit_code==0 and proof['passed'] and proof['assertions']==33 and proof['native_executor_title_tasks']==4 and proof['bridge_used'] is False and proof['machine_closed'] and proof['all_owned_windows_disposed'] and not proof['cleanup_errors']
 receipt['gui_title_verified']=True;receipt['bridge_used']=False;receipt['title_assertions']=33;receipt['proof_sha256']=sha(run/'fixture/result.json');(run/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
if a.panel_takeover:
 proof=json.loads((run/'fixture/panel-membership-takeover-proof.json').read_text())
 assert proof['functional_checks_passed'] and exit_code==proof['expected_process_exit_code'] and proof['takeover_button_invoked_while_native_publication_held'] and proof['native_action_count']==0 and proof['lineage_reservations']==1
 if proof['observed_terminal_state']=='outcome_unknown':
  journal=[json.loads(line) for line in (run/'fixture/bridge-state/journal/operations.jsonl').read_text().splitlines()]
  assert exit_code==2 and any(x['type']=='gui_exit_preserving_unknown' and x['payload']['native_cleanup_performed'] is False and x['payload']['unknown_outcomes_resolved'] is False for x in journal)
 receipt['panel_membership_takeover_verified']=True;receipt['observed_terminal_state']=proof['observed_terminal_state'];receipt['proof_sha256']=sha(run/'fixture/panel-membership-takeover-proof.json');(run/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
if a.panel_membership:
 proof=json.loads((run/'fixture/panel-membership-gui-proof.json').read_text())
 assert exit_code==0 and proof['functional_checks_passed'] and proof['all_swing_model_events_on_edt'] and proof['all_title_events_on_edt'] and proof['native_complete_placements']==8 and proof['native_action_pairs']==32 and proof['bridge_detached'] and proof['machine_closed'] and proof['all_owned_windows_disposed'] and not proof['cleanup_errors']
 receipt['panel_membership_gui_verified']=True;receipt['actual_native_complete_placements']=8;receipt['proof_sha256']=sha(run/'fixture/panel-membership-gui-proof.json');(run/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
if a.topology:
 proof=json.loads((run/'fixture/topology-gui-proof.json').read_text())
 assert exit_code==0 and proof['functional_checks_passed'] and proof['all_swing_model_events_on_edt'] and proof['all_auxiliary_swing_events_on_edt'] and not proof['wizard_assignment_changes'] and proof['native_complete_placements']==32
 receipt['topology_gui_verified']=True;receipt['actual_native_complete_placements']=32;receipt['proof_sha256']=sha(run/'fixture/topology-gui-proof.json');(run/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
if a.unknown_exit:
 proof=json.loads((run/'fixture/unknown-exit-proof.json').read_text());journal=[json.loads(line) for line in (run/'fixture/bridge-state/journal/operations.jsonl').read_text().splitlines()]
 assert exit_code==2 and proof['state']=='outcome_unknown' and proof['actual_native_feed_count']==1
 assert any(e['type']=='gui_exit_preserving_unknown' and e['payload']['native_cleanup_performed'] is False and e['payload']['unknown_outcomes_resolved'] is False for e in journal)
 assert not any(e['type'] in ('cleanup_intent','cleanup_complete','feed_complete') for e in journal)
 receipt['local_unknown_exit_verified']=True;receipt['actual_native_feed_count']=1;(run/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print('PASS local unknown exit preserves uncertain operation with no native cleanup')
raise SystemExit(0 if exit_code==(proof['expected_process_exit_code'] if a.panel_takeover else 2 if a.unknown_exit else 0) else 1)
