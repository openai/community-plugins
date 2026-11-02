#!/usr/bin/env python3
"""Run isolated candidate native mains with immutable inputs and owned-process receipts.
Only process groups created by this runner are signalled. No machine process is discovered.
"""
import argparse,datetime,hashlib,json,os,re,signal,subprocess,time,traceback,zipfile
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
PIN='5bd404cfc70f34103a3ca0fbb6b50c2b465f407c'
INTERRUPTED=[]

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(file):
 with file.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def require(value,message):
 if not value:raise ValueError(message)
def interrupt(signum,_frame):
 # Flags preserve the Popen assignment and cannot raise through ownership cleanup.
 if len(INTERRUPTED)<16:INTERRUPTED.append(signum)
def clean_environment():
 return {k:v for k,v in os.environ.items() if not k.startswith(('OPENPNP_','LD_','DYLD_')) and k not in {'JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','JDK_JAVAC_OPTIONS','_JAVA_OPTIONS','CLASSPATH','JAVA_OPTS','LIBPATH','SHLIB_PATH'}}
def canonical_relative(value):
 require(isinstance(value,str) and 0<len(value)<=1024 and '\\' not in value,'Invalid input inventory path')
 p=PurePosixPath(value)
 require(not p.is_absolute() and all(x not in ('','.','..') for x in value.split('/')) and str(p)==value,'Noncanonical input inventory path')
 return Path(value)
def regular(file):
 file=Path(file)
 require(not file.is_symlink() and file.is_file(),f'Expected regular input: {file}')
 return file

def tree_files(root):
 require(root.is_dir() and not root.is_symlink(),f'Expected input directory: {root}')
 files=[]
 for file in sorted(root.rglob('*')):
  require(not file.is_symlink(),f'Symbolic input refused: {file}')
  if file.is_file():files.append(file)
  else:require(file.is_dir(),f'Nonregular input refused: {file}')
 return files

def read_json(file):
 def pairs(items):
  result={}
  for key,value in items:
   require(key not in result,'Duplicate manifest key: '+key);result[key]=value
  return result
 return json.loads(regular(file).read_text(),object_pairs_hook=pairs,parse_constant=lambda value:require(False,'Nonfinite manifest value'))

def write_json(file,value):
 # Every run is exclusively reserved. Atomic replacement preserves the last complete receipt.
 temporary=file.with_name(file.name+'.writing')
 with temporary.open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2);stream.write('\n')
 os.replace(temporary,file)

def group_exists(pgid,errors):
 try:os.killpg(pgid,0);return True
 except ProcessLookupError:return False
 except OSError as error:
  message=f'group status: {error}'
  if message not in errors:errors.append(message)
  return True

def stop_owned_group(process,grace=3,disappearance_timeout=5,natural_grace=.2):
 started=time.monotonic();before=process.poll()
 result={'pid':process.pid,'pgid':process.pid,'parent_exit_code_before_cleanup':before,'parent_exit_code':before,
         'parent_reaped':before is not None,'term_sent':False,'forced_kill':False,'forced_cleanup':False,'group_gone':False,'errors':[]}
 def exists():return group_exists(process.pid,result['errors'])
 def send(sig):
  try:os.killpg(process.pid,sig);return True
  except ProcessLookupError:return False
  except OSError as error:result['errors'].append(f'signal {sig}: {error}');return False
 # Allow already-finishing descendants a short passive window. A clean completed group
 # receives no signal, unlike the older runner's unconditional TERM/KILL sequence.
 if before is not None:
  deadline=time.monotonic()+natural_grace
  while exists() and time.monotonic()<deadline:time.sleep(.02)
 if exists():
  result['term_sent']=send(signal.SIGTERM);result['forced_cleanup']=result['term_sent']
  deadline=time.monotonic()+grace
  while exists() and time.monotonic()<deadline:process.poll();time.sleep(.02)
  if exists():result['forced_kill']=send(signal.SIGKILL);result['forced_cleanup']|=result['forced_kill']
 try:result['parent_exit_code']=process.wait(timeout=5);result['parent_reaped']=True
 except subprocess.TimeoutExpired:result['errors'].append('Owned parent did not exit after cleanup')
 deadline=time.monotonic()+disappearance_timeout
 while exists() and time.monotonic()<deadline:process.poll();time.sleep(.02)
 result['group_gone']=not exists();result['elapsed_seconds']=time.monotonic()-started
 return result

def clean_completion(entry):
 cleanup=entry.get('cleanup')
 return bool(entry.get('exit_code')==0 and not entry.get('timeout') and not entry.get('interrupted') and not entry.get('primary_error')
             and cleanup and cleanup['parent_reaped'] and cleanup['group_gone'] and not cleanup['forced_cleanup'] and not cleanup['errors'])

def run_logged(command,env,logfile,timeout,cwd=ROOT,cleanup_grace=3):
 started=time.monotonic();process=None
 result={'command':list(map(str,command)),'pid':None,'pgid':None,'exit_code':None,'timeout':False,'timeout_seconds':timeout,
         'interrupted':False,'primary_error':None,'cleanup':None,'started_at':now()}
 try:
  with logfile.open('xb') as log:
   if INTERRUPTED:raise InterruptedError('Runner interrupted before process dispatch')
   process=subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
   result['pid']=result['pgid']=process.pid
   deadline=time.monotonic()+timeout
   while True:
    if INTERRUPTED:raise InterruptedError('Runner interrupted while owned process was running')
    remaining=deadline-time.monotonic()
    if remaining<=0:
     result['timeout']=True;raise subprocess.TimeoutExpired(command,timeout)
    try:result['exit_code']=process.wait(timeout=min(.2,remaining));break
    except subprocess.TimeoutExpired:continue
   if result['exit_code']!=0:result['primary_error']={'type':'NonZeroExit','message':f'Owned process exited with {result["exit_code"]}'}
 except BaseException as error:
  result['primary_error']={'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc()}
 finally:
  if process is not None:
   try:result['cleanup']=stop_owned_group(process,grace=cleanup_grace)
   except BaseException as error:result['cleanup']={'pid':process.pid,'pgid':process.pid,'parent_reaped':process.poll() is not None,'group_gone':False,'forced_cleanup':True,'forced_kill':False,'errors':[f'{type(error).__name__}: {error}']}
   # Keep the original failure/timeout as primary; post-cleanup exit is separately explicit.
   if result['exit_code'] is None:result['exit_code']=process.poll()
  result['interrupted']=bool(INTERRUPTED);result['interrupted_signals']=list(INTERRUPTED)
  result['elapsed_seconds']=time.monotonic()-started;result['log_sha256']=sha(logfile) if logfile.exists() else None
  result['passed']=clean_completion(result)
  write_json(logfile.with_suffix('.process.json'),result)
 return result

def inventory(build,runtime,java_home):
 """Validate declared compilation inputs, then hash complete actual class/runtime inventories."""
 manifest=read_json(build/'build-manifest.json');native=read_json(runtime/'codex-build-manifest.json')
 require(manifest.get('upstream_commit')==native.get('upstream_commit')==PIN,'Build/runtime pin mismatch')
 paths={Path(__file__).resolve(),regular(build/'build-manifest.json'),regular(runtime/'codex-build-manifest.json'),regular(build/'openpnp-codex-bridge.jar')}
 require(sha(build/'openpnp-codex-bridge.jar')==manifest.get('bridge_sha256'),'Selected Bridge differs from its build manifest')
 for key,prefix in [('production_source_sha256','src/openpnp/java/org/openpnp/codex'),('test_source_sha256','tests/openpnp/native')]:
  expected=manifest.get(key);require(isinstance(expected,dict) and expected,f'Missing {key} compilation inventory')
  actual={f.relative_to(ROOT).as_posix() for f in tree_files(ROOT/prefix) if f.suffix=='.java'}
  require(actual==set(expected),f'Current {key} source inventory differs from the selected build')
  for name,digest in expected.items():
   file=regular(ROOT/canonical_relative(name));require(sha(file)==digest,'Compiled source changed: '+name);paths.add(file)
 classes=tree_files(build/'test-classes');require(any(f.suffix=='.class' for f in classes),'Native test classes are absent');paths.update(classes)
 # Retain all test fixture bytes used by native mains, including additions/removals.
 fixtures=ROOT/'tests/openpnp/fixtures'
 if fixtures.exists():paths.update(tree_files(fixtures))
 for patch in manifest.get('patches',[]):
  file=regular(ROOT/'plugins/openpnp/bridge/upstream-patches'/canonical_relative(patch['path']));require(sha(file)==patch['sha256'],'Native patch changed: '+patch['path']);paths.add(file)
 for entry in manifest.get('native_classpath_in_resolution_order',[]):
  file=regular(build/canonical_relative(entry['path']));require(sha(file)==entry['sha256'],'Build classpath input changed: '+entry['path']);paths.add(file)
 declared=native.get('files');require(isinstance(declared,list) and declared,'Native runtime inventory required')
 expected={}
 for entry in declared:
  name=entry['path'];canonical_relative(name);require(name not in expected,'Duplicate native runtime entry: '+name);expected[name]=entry['sha256']
 actual={f.relative_to(runtime).as_posix():f for f in tree_files(runtime) if f!=runtime/'codex-build-manifest.json'}
 require(set(actual)==set(expected),'Native runtime contains missing or unlisted files')
 for name,file in actual.items():require(sha(file)==expected[name],'Native runtime artifact changed: '+name);paths.add(file)
 for entry in native.get('patched_source_files',[]):
  file=regular(build/'source/src/main/java'/canonical_relative(entry['path']));require(sha(file)==entry['sha256'],'Patched native source changed: '+entry['path']);paths.add(file)
 gui=canonical_relative(native['gui_jar']);require(gui.as_posix() in expected,'Native JAR is not inventoried')
 # JAR manifest Class-Path can append classes beyond -cp. Restrict it to inventoried
 # runtime entries, and prohibit a Bridge manifest from adding any implicit classpath.
 for file,allow in [(build/'openpnp-codex-bridge.jar',False),(runtime/gui,True)]:
  with zipfile.ZipFile(file) as jar:
   try:data=jar.read('META-INF/MANIFEST.MF')
   except KeyError:data=b''
   require(len(data)<=65536,'Oversized JAR manifest')
   for line in data.decode('utf-8').replace('\r\n','\n').replace('\n ','').splitlines():
    if line.lower().startswith('class-path:'):
     for value in line.split(':',1)[1].split():
      require(allow and canonical_relative(value).as_posix() in expected,'Uninventoried manifest Class-Path entry')
 if runtime==build/'runtime':
  require(sha(runtime/'codex-build-manifest.json')==manifest.get('runtime_manifest_sha256'),'Build/runtime manifest hash mismatch')
  require(sha(runtime/gui)==manifest.get('patched_native_jar_sha256'),'Build/native JAR hash mismatch')
 for relative in ['bin/java','release','lib/modules']:paths.add(regular(java_home/relative))
 return {str(f):sha(f) for f in sorted(paths)},manifest,native,expected

def observe_inputs(build,runtime,java_home,before):
 """Record changed/missing/additional bytes even when final manifest validation fails."""
 paths={Path(name) for name in (before or {})};errors=[]
 for directory,java_only in [(ROOT/'src/openpnp/java/org/openpnp/codex',True),(ROOT/'tests/openpnp/native',True),
                             (ROOT/'tests/openpnp/fixtures',False),(build/'test-classes',False),(runtime,False)]:
  if directory.exists():
   for file in directory.rglob('*'):
    if (file.is_file() or file.is_symlink()) and (not java_only or file.suffix=='.java'):paths.add(file)
 paths.update([Path(__file__).resolve(),build/'build-manifest.json',build/'openpnp-codex-bridge.jar',runtime/'codex-build-manifest.json'])
 result={}
 for file in sorted(paths):
  try:result[str(file)]=sha(regular(file))
  except Exception as error:result[str(file)]=None;errors.append(f'{file}: {type(error).__name__}: {error}')
 return result,errors

def timeout_for(name):return 600 if name=='NativeVacuumBridgeSuiteTest' else 240

def campaign_valid(result):
 return bool(not result.get('primary_error') and not INTERRUPTED and result.get('inputs_unchanged')
             and len(result['tests'])==len(result['requested_tests']) and all(t['passed'] for t in result['tests'])
             and result.get('jvm_process',{}).get('passed'))

def main(argv=None):
 parser=argparse.ArgumentParser();parser.add_argument('--build',type=Path,required=True);parser.add_argument('--runtime',type=Path);parser.add_argument('--java-home',type=Path,required=True);parser.add_argument('--run',type=Path,required=True);parser.add_argument('--test',action='append',required=True);a=parser.parse_args(argv)
 build=a.build.resolve();runtime=(a.runtime or build/'runtime').resolve();run=a.run.resolve();java_home=a.java_home.resolve()
 require(build.is_relative_to(ROOT) and run.is_relative_to(ROOT) and not run.is_relative_to(build) and not build.is_relative_to(run),'Candidate build/output must be distinct paths under this checkout')
 require(len(a.test)==len(set(a.test)) and a.test,'Native test names must be unique')
 for name in a.test:require(re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',name) and (ROOT/'tests/openpnp/native'/(name+'.java')).is_file(),'Unknown native test main: '+name)
 run.mkdir(parents=True,mode=0o700);require(not any(run.iterdir()),'Run directory must be freshly reserved')
 result={'started_at':now(),'requested_tests':a.test,'tests':[],'passed':False,'primary_error':None,'inputs_before':None,'inputs_after':None,'inputs_unchanged':False,
         'runtime_override':runtime!=build/'runtime','simulation_only':True,'jvm_environment_sanitized':True,'preferences_scope':'fresh in-memory PreferencesFactory; no OS persistence','cleanup_scope':'only Popen-created process groups','duration_clock':'time.monotonic'}
 started=time.monotonic();previous={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGINT)}
 for sig in previous:signal.signal(sig,interrupt)
 try:
  before,manifest,native,files=inventory(build,runtime,java_home);result['inputs_before']=before
  java=java_home/'bin/java';env=clean_environment();env['JAVA_HOME']=str(java_home)
  result.update({'bridge_sha256':sha(build/'openpnp-codex-bridge.jar'),'native_jar_sha256':sha(runtime/native['gui_jar']),'runtime_manifest_sha256':sha(runtime/'codex-build-manifest.json'),'patched_runtime':bool(native.get('gui_ownership'))})
  result['jvm_process']=run_logged([str(java),'-version'],env,run/'jvm-version.log',15)
  result['jvm']=(run/'jvm-version.log').read_text(errors='replace')
  if not result['jvm_process']['passed']:
   result['primary_error']=result['jvm_process']['primary_error'] or {'type':'ProcessCleanupFailure','message':'JVM version probe required cleanup'}
   raise RuntimeError('JVM version probe failed or required process cleanup')
  libs=[runtime/name for name in sorted(files) if name.startswith(native['libs_directory']+'/') and name.endswith('.jar')]
  cp=os.pathsep.join(map(str,[build/'test-classes',build/'openpnp-codex-bridge.jar',runtime/native['gui_jar'],*libs]))
  result['classpath_in_resolution_order']=cp.split(os.pathsep)
  base=[str(java),'-Djava.awt.headless=true','-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory','-Xmx2g','-XX:+ExitOnOutOfMemoryError','--add-opens=java.base/java.lang=ALL-UNNAMED','--add-opens=java.desktop/java.awt=ALL-UNNAMED','--add-opens=java.desktop/java.awt.color=ALL-UNNAMED','-cp',cp]
  write_json(run/'receipt.json',result)
  for name in a.test:
   if INTERRUPTED:raise InterruptedError('Runner interrupted before next test dispatch')
   home=run/(name+'-home');temporary=run/(name+'-tmp');home.mkdir();temporary.mkdir()
   command=base[:1]+['-Djava.io.tmpdir='+str(temporary),'-Duser.home='+str(home)]+base[1:]+['org.openpnp.codex.'+name]+([] if name in ('NativeJobDocumentsRestartTest','NativeDocumentStoreCrashTest') else [str(runtime/native['samples_directory'])])
   entry=run_logged(command,env,run/(name+'.log'),timeout_for(name));entry.update({'main':name,'source_sha256':before[str(ROOT/'tests/openpnp/native'/(name+'.java'))]});result['tests'].append(entry)
   write_json(run/'receipt.json',result);print(json.dumps({key:entry[key] for key in ('main','pid','exit_code','timeout','elapsed_seconds','passed')}),flush=True)
   if not entry['passed']:
    result['primary_error']=entry['primary_error'] or {'type':'ProcessCleanupFailure','message':'Owned test required cleanup or its group did not disappear'};break
 except BaseException as error:
  if result['primary_error'] is None:result['primary_error']={'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc()}
 finally:
  # Always re-inventory after execution, including failed/cancelled campaigns. Hashing
  # precedes the final interruption check so cancellation during provenance cannot pass.
  try:
   after,observation_errors=observe_inputs(build,runtime,java_home,result['inputs_before']);result['inputs_after']=after;result['input_observation_errors']=observation_errors
   validated,_,_,_=inventory(build,runtime,java_home);result['inputs_unchanged']=after==result['inputs_before'] and validated==after and not observation_errors
   if validated!=after:result['inputs_changed_during_final_verification']=True;result['final_validated_inputs']=validated
  except BaseException as error:result['input_verification_error']={'type':type(error).__name__,'message':str(error)};result['inputs_unchanged']=False
  if result['inputs_before'] is not None and result['inputs_after'] is not None:
   result['input_differences']=[name for name in sorted(set(result['inputs_before'])|set(result['inputs_after'])) if result['inputs_before'].get(name)!=result['inputs_after'].get(name)]
  result['interrupted_signals']=list(INTERRUPTED);result['finished_at']=now();result['elapsed_seconds']=time.monotonic()-started
  result['passed']=campaign_valid(result)
  write_json(run/'receipt.json',result)
  for sig,handler in previous.items():signal.signal(sig,handler)
 return 0 if result['passed'] else (128+INTERRUPTED[0] if INTERRUPTED else 1)
if __name__=='__main__':raise SystemExit(main())
