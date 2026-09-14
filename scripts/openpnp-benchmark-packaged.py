#!/usr/bin/env python3
"""Explicit packaged-baseline or separately labelled diagnostic native performance campaign."""
from pathlib import Path
import argparse,contextlib,datetime,hashlib,json,os,signal,statistics,subprocess,time,zipfile
if not __debug__:raise RuntimeError('Assertions required')
ROOT=Path(__file__).resolve().parents[1];BRIDGE='970e4635953f3bdc33499088ad560d6f6190d01e2285c152f6a56a109a7caf45';NATIVE='810773c1defa9a0200cce437d3f426d4c08419fb3f471d693f9e5c5f38f78f0b';PIN='5bd404cfc70f34103a3ca0fbb6b50c2b465f407c'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,d):p.write_text(json.dumps(d,indent=2)+'\n')
def inv(root):return {str(p.relative_to(root)):sha(p)for p in sorted(root.rglob('*'))if p.is_file()}
def clean():return {k:v for k,v in os.environ.items()if not k.upper().startswith(('OPENPNP_','LD_','DYLD_'))and k.upper()not in {'CLASSPATH','JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','JDK_JAVAC_OPTIONS','_JAVA_OPTIONS','JAVA_OPTS','LIBPATH','SHLIB_PATH'}}
def absent(pid):
 try:os.killpg(pid,0);return False
 except ProcessLookupError:return True
 except PermissionError:return False
# Signal handlers record intent; cleanup runs only after Popen ownership is known.
# This also prevents repeated signals from interrupting owned process cleanup.
_cancel_signal = None
class RunCancelled(BaseException):
 def __init__(self, signum):
  self.signum = signum
  super().__init__('Cancellation requested by ' + signal.Signals(signum).name)
def check_cancelled():
 if _cancel_signal is not None: raise RunCancelled(_cancel_signal)
def request_cancellation(signum, frame):
 global _cancel_signal
 if _cancel_signal is None: _cancel_signal = signum
@contextlib.contextmanager
def cancellation_signals():
 global _cancel_signal
 previous_flag = _cancel_signal
 _cancel_signal = None
 previous_handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
 for s in previous_handlers: signal.signal(s, request_cancellation)
 try: yield
 finally:
  for s, handler in previous_handlers.items(): signal.signal(s, handler)
  _cancel_signal = previous_flag
def stop_owned_process(process):
 """Terminate and reap only the session created by this runner."""
 try: os.killpg(process.pid, signal.SIGTERM)
 except ProcessLookupError: pass
 try: process.wait(timeout=3)
 except subprocess.TimeoutExpired: pass
 if not absent(process.pid):
  try: os.killpg(process.pid, signal.SIGKILL)
  except ProcessLookupError: pass
 if process.returncode is None: process.wait(timeout=5)
 deadline = time.monotonic() + 5
 while not absent(process.pid) and time.monotonic() < deadline: time.sleep(.05)
 return absent(process.pid)
def execute(command,where,timeout,env):
 check_cancelled()
 save(where/'command.json',{'command':list(map(str,command)),'timeout_seconds':timeout})
 started=time.monotonic();forced=False;error=None;group_gone=False
 with (where/'native.log').open('x') as out:
  check_cancelled()
  process=subprocess.Popen(list(map(str,command)),stdout=out,stderr=subprocess.STDOUT,env=env,stdin=subprocess.DEVNULL,start_new_session=True)
  try:
   # A signal arriving during Popen is acted upon after the process is assigned.
   check_cancelled()
   deadline=time.monotonic()+timeout
   while True:
    check_cancelled()
    remaining=deadline-time.monotonic()
    if remaining<=0: raise subprocess.TimeoutExpired(command,timeout)
    try: process.wait(timeout=min(.1,remaining));break
    except subprocess.TimeoutExpired: pass
   check_cancelled()
  except BaseException as caught:
   error=caught;forced=True
  finally:
   if forced or not absent(process.pid):
    forced=True;group_gone=stop_owned_process(process)
   else: group_gone=True
 result={'exit_code':process.returncode,'pid':process.pid,'elapsed_seconds':time.monotonic()-started,'forced_cleanup':forced,'owned_process_group_gone':group_gone,'error':type(error).__name__+': '+str(error) if error else None,'cancellation_signal':_cancel_signal,'log_sha256':sha(where/'native.log')}
 save(where/'lifecycle.json',result)
 if error is not None: raise error
 return result
def target_met(valid, pairs, repetitions):
 return valid and len(pairs)==repetitions and all(x['overhead_percent']<=5 for x in pairs)
def final_campaign_state(inputs, classes, compiled, failure, completed, expected):
 # Cancellation can arrive during the final file hashes; evaluate it afterward.
 unchanged=all(sha(Path(n))==h for n,h in inputs.items())and inv(classes)==compiled
 if _cancel_signal is not None: failure=failure or 'RunCancelled: '+signal.Signals(_cancel_signal).name
 valid=failure is None and completed==expected and unchanged and _cancel_signal is None
 return failure,unchanged,valid
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',type=Path,required=True);p.add_argument('--java-home',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--campaign',choices=['baseline','diagnostic'],default='baseline');p.add_argument('--sample-root',type=Path);p.add_argument('--harness-source',type=Path,default=ROOT/'tests/openpnp/native/NativePlacementBenchmark76.java');p.add_argument('--expected-native-sha256',default=NATIVE);p.add_argument('--bridge-jar',type=Path);p.add_argument('--expected-bridge-sha256',default=BRIDGE);p.add_argument('--diagnostic-overlay',type=Path);p.add_argument('--compile-only',action='store_true');p.add_argument('--count',type=int,choices=[100,200],default=100);p.add_argument('--warmup',type=int,default=20);p.add_argument('--repetitions',type=int,choices=range(3,7),default=3);a=p.parse_args()
 assert 10<=a.warmup<=50
 out=a.output.resolve();assert out!=ROOT and not out.exists();out.mkdir();build=a.build.resolve();runtime=build/'runtime';jhome=a.java_home.resolve();source=a.harness_source.resolve();jar=(a.bridge_jar or build/'openpnp-codex-bridge.jar').resolve();overlay=a.diagnostic_overlay.resolve()if a.diagnostic_overlay else None
 assert a.campaign=='diagnostic'or(overlay is None and jar==build/'openpnp-codex-bridge.jar')
 assert sha(jar)==a.expected_bridge_sha256
 rm=json.loads((runtime/'codex-build-manifest.json').read_text());bm=json.loads((build/'build-manifest.json').read_text());assert rm['upstream_commit']==PIN and sha(build/'openpnp-codex-bridge.jar')==bm['bridge_sha256'] and len(bm['production_source_sha256'])>=1
 assert len(rm['patches'])==5 and len(rm['patched_source_files'])==11 and sha(runtime/rm['gui_jar'])==a.expected_native_sha256
 inputs={str(source):sha(source),str(Path(__file__)):sha(Path(__file__)),str(jar):sha(jar),str(build/'build-manifest.json'):sha(build/'build-manifest.json'),str(runtime/'codex-build-manifest.json'):sha(runtime/'codex-build-manifest.json')}
 for row in rm['files']:
  q=runtime/row['path'];assert q.resolve().is_relative_to(runtime)and sha(q)==row['sha256'];inputs[str(q)]=row['sha256']
 for name in ['bin/java','bin/javac','release','lib/modules']:inputs[str(jhome/name)]=sha(jhome/name)
 samples=(a.sample_root or runtime/rm['samples_directory']).resolve();assert samples==(runtime/rm['samples_directory']).resolve(),'Sample root must be the exact manifest-bound runtime sample tree'
 libs=[runtime/x['path']for x in sorted(rm['files'],key=lambda x:x['path'])if x['path'].startswith(rm['libs_directory']+'/')and x['path'].endswith('.jar')]
 basecp=[jar,runtime/rm['gui_jar'],*libs]
 if overlay:
  assert a.campaign=='diagnostic'and overlay.is_dir();before_overlay=inv(overlay);assert before_overlay and all(n.endswith('.class')and n.startswith('org/openpnp/codex/')and 'NativePlacementBenchmark76'not in n for n in before_overlay)
  inputs.update({str(overlay/n):h for n,h in before_overlay.items()});basecp.insert(0,overlay)
 source_copy=out/'NativePlacementBenchmark76.java';source_copy.write_bytes(source.read_bytes());(out/'runner.py').write_bytes(Path(__file__).read_bytes());classes=out/'classes';classes.mkdir();env=clean();env['JAVA_HOME']=str(jhome)
 compile_dir=out/'compile';compile_dir.mkdir();compile_command=[jhome/'bin/javac','--release','11','-cp',os.pathsep.join(map(str,[jar,runtime/rm['gui_jar'],*libs])),'-d',classes,source_copy]
 comp=execute(compile_command,compile_dir,120,env);assert comp['exit_code']==0 and not comp['forced_cleanup']and comp['owned_process_group_gone']
 compiled=inv(classes);assert compiled and all(n.startswith('org/openpnp/codex/NativePlacementBenchmark76')and n.endswith('.class')for n in compiled);assert all(sha(Path(n))==h for n,h in inputs.items())
 with zipfile.ZipFile(jar)as z:assert 'org/openpnp/codex/NativePlacementBenchmark76.class'not in z.namelist()
 metadata={'campaign':a.campaign,'packaged_baseline':a.campaign=='baseline','bridge_sha256':sha(jar),'bridge_path':str(jar),'diagnostic_overlay':str(overlay)if overlay else None,'native_sha256':a.expected_native_sha256,'native_manifest_sha256':sha(runtime/'codex-build-manifest.json'),'upstream_pin':PIN,'java_home':str(jhome),'count':a.count,'warmup':a.warmup,'repetitions':a.repetitions,'predeclared_order':['direct','bridge','bridge','direct','direct','bridge']if a.repetitions==3 else [mode for i in range(a.repetitions)for mode in (['direct','bridge']if i%2==0 else ['bridge','direct'])],'poll_ms':2,'fixture':'native accelerated Unsorted preordered grid; finite10000-slot zero-pitch trays; native current action/step/checkpoint journal enabled','timing_boundary':'After job import/model preflight and before native processor initialize; end after native standstill and terminal result/count bookkeeping. HTTP/MCP excluded. Diagnostic getter before/after this interval; capture samples separate.','compile_scope':'Only benchmark harness compiled; no production source compiled or replaced','compiled_harness_classes':compiled,'inputs':inputs,'source_sha256':sha(source),'compile_only':a.compile_only,'native_workload_started':False,'physical_qualification':False,'target_added_cycle_time_percent':5,'target_rule':'Every observed pair must be <=5%; descriptive fixture evidence only'}
 save(out/'inputs.json',metadata)
 if a.compile_only:
  unchanged=all(sha(Path(n))==h for n,h in inputs.items())and inv(classes)==compiled
  valid=unchanged and _cancel_signal is None
  save(out/'receipt.json',{'passed':valid,'compile_only':True,'native_workload_started':False,'inputs_unchanged':unchanged,'metadata_sha256':sha(out/'inputs.json')});print(json.dumps({'compile_only':True,'passed':valid,'output':str(out)}));return 0 if valid else 1
 metadata['native_workload_started']=True;save(out/'inputs.json',metadata);cp=os.pathsep.join(map(str,[classes,*basecp]));runs=[];pairs=[];failure=None
 try:
  for pair in range(a.repetitions):
   check_cancelled()
   paired={}
   for mode in (['direct','bridge']if pair%2==0 else ['bridge','direct']):
    check_cancelled()
    name=f'pair-{pair+1}-{mode}';trial=out/name;trial.mkdir();home=trial/'home';tmp=trial/'tmp';home.mkdir();tmp.mkdir()
    cmd=[jhome/'bin/java','-Xmx2g','-XX:+ExitOnOutOfMemoryError','--add-opens=java.base/java.lang=ALL-UNNAMED','--add-opens=java.desktop/java.awt=ALL-UNNAMED','--add-opens=java.desktop/java.awt.color=ALL-UNNAMED','-Djava.awt.headless=true','-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory','-Duser.home='+str(home),'-Djava.io.tmpdir='+str(tmp),'-Dopenpnp.benchmark.root='+str(trial),'-cp',cp,'org.openpnp.codex.NativePlacementBenchmark76',mode,samples,a.count,a.warmup]
    print('Starting '+name,flush=True);life=execute(cmd,trial,180,env);assert life['exit_code']==0 and not life['forced_cleanup']and life['owned_process_group_gone'],(name,life)
    closure=json.loads((trial/'closure.json').read_text());assert closure['exit_code']==0 and closure['native_machine_idle_before_close']and closure['bridge_close_returned']and closure['machine_close_returned']and closure['pid']==life['pid']
    prefix='OPENPNP_NATIVE_PLACEMENT_BENCHMARK_RESULT ';matches=[json.loads(line[len(prefix):])for line in(trial/'native.log').read_text().splitlines()if line.startswith(prefix)];assert len(matches)==1;value=matches[0]
    assert value['mode']==mode and value['measured']['placed']==value['measured_native_feeds']==a.count and value['native_feed_count_final']==a.count+a.warmup and value['job_order']=='Unsorted'
    assert value['native_code_origin']==(runtime/rm['gui_jar']).as_uri().replace('file:///','file:/')and value['harness_code_origin']==classes.as_uri().replace('file:///','file:/')+'/'
    expected_origin=(overlay.as_uri().replace('file:///','file:/')+'/'if overlay else jar.as_uri().replace('file:///','file:/'));assert value['bridge_code_origin']==expected_origin
    if mode=='bridge':
     assert value['production_journal_force_enabled']and value['measured']['native_complete_checkpoints']==a.count and value['measured']['matched_native_actions']>=4*a.count
     assert value['measured']['diagnostics_before']['available']==(a.campaign=='diagnostic')and value['measured']['diagnostics_after']['available']==(a.campaign=='diagnostic')
     assert sha(Path(value['journal_path']))==value['journal_sha256']
    value['pair_index']=pair+1;save(trial/'result.json',value);runs.append(value);paired[mode]=value;print(json.dumps({'trial':name,'seconds':value['measured']['seconds'],'native_steps':value['measured']['native_steps']}),flush=True)
   for key in ['canonical_job_sha256','machine_speed','job_order','native_processor_class','class_sha256','capture_params']:assert paired['direct'][key]==paired['bridge'][key],key
   # JVM home/tmp root arguments differ by design; compare all other settings explicitly above.
   assert paired['direct']['runtime_limits']['max_heap_bytes']==paired['bridge']['runtime_limits']['max_heap_bytes'];assert paired['direct']['measured']['native_steps']==paired['bridge']['measured']['native_steps']
   direct=paired['direct']['measured']['seconds'];bridge=paired['bridge']['measured']['seconds'];pairs.append({'pair_index':pair+1,'direct_seconds':direct,'bridge_seconds':bridge,'overhead_seconds':bridge-direct,'overhead_percent':(bridge/direct-1)*100,'added_ms_per_placement':(bridge-direct)*1000/a.count})
 except BaseException as error:failure=type(error).__name__+': '+str(error)
 failure,unchanged,valid=final_campaign_state(inputs,classes,compiled,failure,len(runs),2*a.repetitions)
 result={'passed':valid,'failure':failure,'campaign':a.campaign,'packaged_baseline':a.campaign=='baseline','bridge_sha256':sha(jar),'native_sha256':a.expected_native_sha256,'metadata_sha256':sha(out/'inputs.json'),'inputs_unchanged':unchanged,'completed_trials':len(runs),'pairs':pairs,'measured_median_overhead_percent':statistics.median(x['overhead_percent']for x in pairs)if pairs else None,'target_5_percent_met':target_met(valid,pairs,a.repetitions),'target_rule':'Every observed pair <=5%; this is not a population guarantee or physical qualification','total_measured_placements':len(runs)*a.count,'total_warmup_placements':len(runs)*a.warmup,'source_and_runtime_unmodified':unchanged,'physical_qualification':False,'production_modified':False}
 save(out/'receipt.json',result);print(json.dumps(result),flush=True);return 0 if result['passed']else 1

def entrypoint():
 with cancellation_signals():
  try:
   code=main()
   check_cancelled()
   return code
  except RunCancelled as error:
   print(str(error),flush=True)
   return 128+error.signum
if __name__=='__main__': raise SystemExit(entrypoint())
