"""Owned harmless child and synthetic input tests; never native OpenPnP qualification."""
import ctypes,hashlib,importlib.util,json,os,signal,subprocess,sys,tempfile,threading,time,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
RUNNER=Path(__file__).resolve().parents[2]/'scripts/openpnp-test-native-candidate.py'
def load():
 spec=importlib.util.spec_from_file_location('candidate_runner78',RUNNER);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def wait_for(test,seconds=5):
 deadline=time.monotonic()+seconds
 while not test():
  if time.monotonic()>deadline:raise AssertionError('Fixture checkpoint not reached')
  time.sleep(.01)
class LifecycleTests(unittest.TestCase):
 def setUp(self):
  self.runner=load();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
 def child(self,code,timeout=3):return self.runner.run_logged([sys.executable,'-c',code],self.runner.clean_environment(),self.root/'child.log',timeout,cleanup_grace=.15)
 def test_natural_success_is_reaped_without_signals(self):
  r=self.child("print('completed')");self.assertTrue(r['passed']);self.assertEqual(r['exit_code'],0);self.assertTrue(r['cleanup']['group_gone']);self.assertFalse(r['cleanup']['term_sent']);self.assertFalse(r['cleanup']['forced_kill']);self.assertEqual(r['pid'],r['pgid'])
  self.assertEqual(json.loads((self.root/'child.process.json').read_text()),r)
 def test_original_failure_is_retained(self):
  r=self.child("import sys;print('original failure');sys.exit(7)");self.assertFalse(r['passed']);self.assertEqual(r['exit_code'],7);self.assertEqual(r['primary_error']['type'],'NonZeroExit');self.assertFalse(r['cleanup']['forced_cleanup']);self.assertIn('original failure',(self.root/'child.log').read_text())
 def test_cleanup_reporting_failure_does_not_replace_original_exit_error(self):
  real=self.runner.stop_owned_group
  def reported(process,**kwargs):
   real(process,**kwargs);raise RuntimeError('controlled cleanup reporting failure')
  with patch.object(self.runner,'stop_owned_group',side_effect=reported):r=self.child('import sys;sys.exit(9)')
  self.assertEqual(r['exit_code'],9);self.assertEqual(r['primary_error']['type'],'NonZeroExit');self.assertIn('reporting failure',r['cleanup']['errors'][0]);self.assertFalse(r['passed'])
 def test_failed_spawn_retains_error_without_claiming_process_ownership(self):
  r=self.runner.run_logged([str(self.root/'missing-executable')],self.runner.clean_environment(),self.root/'spawn.log',1)
  self.assertEqual(r['primary_error']['type'],'FileNotFoundError');self.assertIsNone(r['pid']);self.assertIsNone(r['cleanup']);self.assertFalse(r['passed'])
 def test_timeout_forces_only_owned_group_and_preserves_timeout(self):
  sentinel=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'],start_new_session=True)
  try:
   r=self.child('import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)',.3)
   self.assertFalse(r['passed']);self.assertTrue(r['timeout']);self.assertEqual(r['primary_error']['type'],'TimeoutExpired');self.assertTrue(r['cleanup']['forced_kill']);self.assertTrue(r['cleanup']['group_gone']);self.assertIsNone(sentinel.poll())
  finally:sentinel.terminate();sentinel.wait(timeout=5)
 def test_cancel_before_and_during_popen_assignment(self):
  self.runner.interrupt(signal.SIGTERM,None)
  with patch.object(self.runner.subprocess,'Popen',side_effect=AssertionError('must not spawn')):r=self.child('pass')
  self.assertIsNone(r['pid']);self.assertTrue(r['interrupted']);self.assertFalse(r['passed']);self.assertEqual(r['primary_error']['type'],'InterruptedError')
  self.runner.INTERRUPTED.clear();real=subprocess.Popen
  def admitted(*a,**k):
   child=real(*a,**k);self.runner.interrupt(signal.SIGTERM,None);return child
  with patch.object(self.runner.subprocess,'Popen',side_effect=admitted):r=self.runner.run_logged([sys.executable,'-c','import time;time.sleep(20)'],self.runner.clean_environment(),self.root/'race.log',3,cleanup_grace=.15)
  self.assertIsNotNone(r['pid']);self.assertTrue(r['cleanup']['group_gone']);self.assertTrue(r['interrupted']);self.assertFalse(r['passed'])
 def test_repeated_sigterm_during_forced_cleanup_does_not_interrupt_reaping(self):
  ready=self.root/'ready';term=self.root/'term'
  code=f"import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,lambda s,f:Path({str(term)!r}).write_text('term'));Path({str(ready)!r}).write_text('ready');time.sleep(20)"
  old=signal.signal(signal.SIGTERM,self.runner.interrupt)
  def signals():
   wait_for(ready.exists);os.kill(os.getpid(),signal.SIGTERM);wait_for(term.exists);os.kill(os.getpid(),signal.SIGTERM)
  worker=threading.Thread(target=signals);worker.start()
  try:r=self.child(code,3)
  finally:worker.join(timeout=5);signal.signal(signal.SIGTERM,old)
  self.assertEqual(r['interrupted_signals'],[signal.SIGTERM,signal.SIGTERM]);self.assertTrue(r['cleanup']['forced_kill']);self.assertTrue(r['cleanup']['group_gone']);self.assertFalse(r['passed'])
 def test_exited_leader_with_lingering_descendant_cannot_pass(self):
  subreaper=False
  if sys.platform.startswith('linux'):subreaper=ctypes.CDLL(None).prctl(36,1,0,0,0)==0
  pidfile=self.root/'child.pid';child=f"import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(20)"
  parent="import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c',sys.argv[1]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);p=Path(sys.argv[2]);\nwhile not p.exists():time.sleep(.01)"
  process=subprocess.Popen([sys.executable,'-c',parent,child,str(pidfile)],start_new_session=True);process.wait(timeout=5);pid=int(pidfile.read_text());reaper=None
  if subreaper:reaper=threading.Thread(target=lambda:os.waitpid(pid,0));reaper.start()
  try:
   result=self.runner.stop_owned_group(process,grace=.15);self.assertEqual(result['parent_exit_code_before_cleanup'],0);self.assertTrue(result['forced_kill']);self.assertTrue(result['group_gone'])
  finally:
   if reaper:reaper.join(timeout=5)
 def test_wall_clock_change_cannot_change_duration_and_timeout_profile(self):
  with patch.object(self.runner.time,'time',side_effect=AssertionError('wall clock must not time commands')):r=self.child('pass')
  self.assertTrue(r['passed']);self.assertGreaterEqual(r['elapsed_seconds'],0);self.assertEqual(self.runner.timeout_for('NativeVacuumBridgeSuiteTest'),600);self.assertEqual(self.runner.timeout_for('NativeVacuumSourcesTest'),240)

class InputAndCampaignTests(unittest.TestCase):
 def setUp(self):
  self.runner=load();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.build=self.root/'validation/build';self.runtime=self.build/'runtime';self.java=self.root/'jdk';self.source=self.root/'tests/openpnp/native/NativeFixture.java'
  self.runner.ROOT=self.root
  for f,data in [(self.root/'src/openpnp/java/org/openpnp/codex/Production.java',b'fixture'),(self.source,b'fixture'),(self.build/'test-classes/NativeFixture.class',b'fixture'),(self.java/'bin/java',b'fixture'),(self.java/'release',b'fixture'),(self.java/'lib/modules',b'fixture')]:f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(data)
  self.runtime.mkdir();(self.runtime/'lib').mkdir();(self.runtime/'samples').mkdir()
  for jar in [self.runtime/'native.jar',self.build/'openpnp-codex-bridge.jar',self.runtime/'lib/dependency.jar']:
   with zipfile.ZipFile(jar,'w') as z:z.writestr('META-INF/MANIFEST.MF','Manifest-Version: 1.0\n')
  self.native={'upstream_commit':self.runner.PIN,'gui_jar':'native.jar','libs_directory':'lib','samples_directory':'samples','files':[{'path':p.relative_to(self.runtime).as_posix(),'sha256':self.runner.sha(p)} for p in sorted(self.runtime.rglob('*')) if p.is_file()]};(self.runtime/'codex-build-manifest.json').write_text(json.dumps(self.native))
  self.manifest={'upstream_commit':self.runner.PIN,'bridge_sha256':self.runner.sha(self.build/'openpnp-codex-bridge.jar'),'runtime_manifest_sha256':self.runner.sha(self.runtime/'codex-build-manifest.json'),'patched_native_jar_sha256':self.runner.sha(self.runtime/'native.jar'),'production_source_sha256':{'src/openpnp/java/org/openpnp/codex/Production.java':self.runner.sha(self.root/'src/openpnp/java/org/openpnp/codex/Production.java')},'test_source_sha256':{'tests/openpnp/native/NativeFixture.java':self.runner.sha(self.source)},'native_classpath_in_resolution_order':[{'path':'runtime/native.jar','sha256':self.runner.sha(self.runtime/'native.jar')}]};(self.build/'build-manifest.json').write_text(json.dumps(self.manifest))
 def test_exact_inputs_and_actual_post_drift_hashes_are_retained(self):
  before,*_=self.runner.inventory(self.build,self.runtime,self.java);self.assertIn(str(self.build/'test-classes/NativeFixture.class'),before)
  self.source.write_bytes(b'changed');after,errors=self.runner.observe_inputs(self.build,self.runtime,self.java,before);self.assertNotEqual(before[str(self.source)],after[str(self.source)]);self.assertEqual(errors,[])
  with self.assertRaisesRegex(ValueError,'Compiled source changed'):self.runner.inventory(self.build,self.runtime,self.java)
 def test_additional_class_missing_runtime_and_duplicate_paths_refused_or_bound(self):
  before,*_=self.runner.inventory(self.build,self.runtime,self.java);extra=self.build/'test-classes/Extra.class';extra.write_bytes(b'extra');after,_=self.runner.observe_inputs(self.build,self.runtime,self.java,before);self.assertIn(str(extra),after);self.assertNotEqual(before,after)
  (self.runtime/'lib/dependency.jar').unlink();after,errors=self.runner.observe_inputs(self.build,self.runtime,self.java,before);self.assertIsNone(after[str(self.runtime/'lib/dependency.jar')]);self.assertTrue(errors)
  with self.assertRaisesRegex(ValueError,'missing or unlisted'):self.runner.inventory(self.build,self.runtime,self.java)
 def test_failed_cancellation_in_final_provenance_cannot_pass(self):
  output=self.root/'validation/run';original=self.runner.observe_inputs
  def observed(*args):
   value=original(*args);self.runner.interrupt(signal.SIGTERM,None);return value
  def harmless(command,env,logfile,timeout,**kwargs):return self.runner_real([sys.executable,'-c',"print('harmless runner fixture')"],env,logfile,timeout,cleanup_grace=.1)
  self.runner_real=self.runner.run_logged
  with patch.object(self.runner,'run_logged',side_effect=harmless),patch.object(self.runner,'observe_inputs',side_effect=observed):code=self.runner.main(['--build',str(self.build),'--run',str(output),'--java-home',str(self.java),'--test','NativeFixture'])
  receipt=json.loads((output/'receipt.json').read_text());self.assertEqual(code,143);self.assertFalse(receipt['passed']);self.assertTrue(receipt['inputs_unchanged']);self.assertEqual(receipt['interrupted_signals'],[signal.SIGTERM]);self.assertEqual(len(receipt['tests']),1)
 def test_existing_output_is_never_reused_and_environment_sanitized(self):
  output=self.root/'validation/run';output.mkdir();sentinel=output/'receipt.json';sentinel.write_text('preserve')
  with self.assertRaises(FileExistsError):self.runner.main(['--build',str(self.build),'--run',str(output),'--java-home',str(self.java),'--test','NativeFixture'])
  self.assertEqual(sentinel.read_text(),'preserve')
  with patch.dict(os.environ,{'LD_PRELOAD':'bad','JAVA_TOOL_OPTIONS':'bad','OPENPNP_CONFIG':'bad','KEPT':'yes'}):env=self.runner.clean_environment()
  self.assertNotIn('LD_PRELOAD',env);self.assertNotIn('JAVA_TOOL_OPTIONS',env);self.assertNotIn('OPENPNP_CONFIG',env);self.assertEqual(env['KEPT'],'yes')
if __name__=='__main__':unittest.main()
