#!/usr/bin/env python3
"""Run one isolated real-GUI test using a candidate-owned patched runtime. Never uses a live connection."""
import argparse,hashlib,importlib.util,json,os,signal,time
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
# Bridge is deliberately absent from system classpath. The native script performs URLClassLoader attachment.
classpath=os.pathsep.join(map(str,[build/'test-classes',runtime/manifest['gui_launcher_jar'],runtime/manifest['gui_jar'],*libs]))
command=[str(a.java_home.resolve()/'bin/java'),'-Xmx2g','-XX:+ExitOnOutOfMemoryError','--add-opens=java.base/java.lang=ALL-UNNAMED','--add-opens=java.desktop/java.awt=ALL-UNNAMED','--add-opens=java.desktop/java.awt.color=ALL-UNNAMED','--add-exports=java.desktop/com.apple.eawt=ALL-UNNAMED','-cp',classpath,('org.openpnp.codex.NativeGuiBoardInspectionTest' if a.board_inspection else 'org.openpnp.codex.NativeGuiTitleTest' if a.title else 'org.openpnp.codex.NativeGuiPanelMembershipTakeoverTest' if a.panel_takeover else 'org.openpnp.codex.NativeGuiPanelMembershipTest' if a.panel_membership else 'org.openpnp.codex.NativeGuiTopologyTest' if a.topology else 'org.openpnp.codex.NativeGuiOwnershipTest'),str(runtime),str(build/'openpnp-codex-bridge.jar'),str(ROOT/'plugins/openpnp/bridge/bootstrap.js'),str(run/'fixture')]
if a.title:command=command[:-4]+[str(run/'fixture')]
for directory in ('home','system-preferences','tmp'): (run/directory).mkdir(mode=0o700)
command[1:1]=['-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory','-Djava.util.prefs.systemRoot='+str(run/'system-preferences'),'-Duser.home='+str(run/'home'),'-Djava.io.tmpdir='+str(run/'tmp')]
if a.unknown_exit:command.insert(1,'-Dopenpnp.codex.test.unknownExit=true')
if a.board_inspection:command[1:1]=['-Dopenpnp.codex.test.node='+str(a.node.resolve()),'-Dopenpnp.codex.test.mcpHelper='+str(ROOT/'tests/openpnp/native-inspection-gui-mcp.mjs'),'-Dopenpnp.codex.test.mcpServer='+str(ROOT/'plugins/openpnp/mcp/server.mjs')]
started=time.time();exit_code=None
try:
 exit_code=owned.run_logged(command,ROOT,owned.clean_environment(),run/'native.log',240)
finally:
 receipt={'exit_code':exit_code,'elapsed_seconds':time.time()-started,'bridge_sha256':sha(build/'openpnp-codex-bridge.jar'),'native_manifest_sha256':sha(runtime/'codex-build-manifest.json'),'native_log_sha256':sha(run/'native.log'),'test_source_sha256':sha(ROOT/('tests/openpnp/native/NativeGuiBoardInspectionTest.java' if a.board_inspection else 'tests/openpnp/native/NativeGuiTitleTest.java' if a.title else 'tests/openpnp/native/NativeGuiPanelMembershipTakeoverTest.java' if a.panel_takeover else 'tests/openpnp/native/NativeGuiPanelMembershipTest.java' if a.panel_membership else 'tests/openpnp/native/NativeGuiTopologyTest.java' if a.topology else 'tests/openpnp/native/NativeGuiOwnershipTest.java')),'bootstrap_sha256':sha(ROOT/'plugins/openpnp/bridge/bootstrap.js'),'simulation_only':True,'physical_qualification':False,'gui_test_application_classpath_has_bridge':False}
 receipt.update(runner_sha256=sha(Path(__file__)),owned_process_helper_sha256=sha(ROOT/'scripts/openpnp-test-native-mcp.py'),jvm_environment_sanitized=True,private_home_and_tmp=True,desktop_gestures_performed=False)
 if a.board_inspection:receipt.update(mcp_helper_sha256=sha(ROOT/'tests/openpnp/native-inspection-gui-mcp.mjs'),mcp_server_sha256=sha(ROOT/'plugins/openpnp/mcp/server.mjs'),node_executable_sha256=sha(a.node.resolve()))
 cleanup=run/'native.process.json'
 if cleanup.is_file():receipt['process_cleanup']=json.loads(cleanup.read_text())
 (run/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
if a.board_inspection:
 proof=json.loads((run/'fixture/board-inspection-gui-proof.json').read_text())
 assert exit_code==0 and proof['functional_checks_passed'] and proof['native_complete_placements']==8 and proof['native_action_pairs']==32
 assert proof['bridge_detached'] and proof['machine_closed'] and proof['all_owned_windows_disposed'] and proof['wrapper_test_listeners_detached'] and not proof['errors']
 assert proof['native_gui_component_api'] and proof['bridge_on_application_classpath'] is False and proof['desktop_gestures_performed'] is False and proof['physical_qualification'] is False
 assert proof['successful_task']['submission_operation']['state']=='succeeded' and proof['successful_task']['receipt']['result']['passed_count']==2
 assert proof['consumed_failure_task']['submission_operation']['state']=='failed' and proof['revoked_task']['submission_operation']['state']=='failed'
 assert proof['mcp']['passed'] and proof['mcp']['catalog_count']==59 and proof['mcp']['server_pid_gone'] and proof['mcp']['server_gone_before_sdk_close']
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
