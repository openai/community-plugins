#!/usr/bin/env python3
"""Build an isolated additive patched native runtime; never write the stock source/runtime.
Default is compile/package only. GUI/native workload tests are a separate explicit step.
"""
import argparse,datetime,hashlib,json,os,re,shutil,subprocess,tarfile,zipfile
from pathlib import Path
PIN='5bd404cfc70f34103a3ca0fbb6b50c2b465f407c'
ROOT=Path(__file__).resolve().parents[1]
CIRCULAR_STEM='org/openpnp/vision/pipeline/stages/DetectCircularSymmetry'
CIRCULAR_CLASSES=sorted(CIRCULAR_STEM+suffix+'.class' for suffix in ('','$1','$2','$ScoreRange','$SymmetryCircle','$SymmetryScore'))
VACUUM_FAMILIES={'org/openpnp/machine/reference/ReferenceNozzle': ['org/openpnp/machine/reference/ReferenceNozzle$1.class', 'org/openpnp/machine/reference/ReferenceNozzle$ManualLoadException.class', 'org/openpnp/machine/reference/ReferenceNozzle$ManualUnloadException.class', 'org/openpnp/machine/reference/ReferenceNozzle.class'], 'org/openpnp/machine/reference/ReferencePnpJobProcessor': ['org/openpnp/machine/reference/ReferencePnpJobProcessor$1.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Abort.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$AbstractOptimizationNozzlesStep.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Align.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$AlignLocator.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$CalibrateNozzleTips.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$ChangeNozzleTips.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Cleanup.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$EndCameraBatchOperation.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$FiducialCheck$ExtendedPlacementsHolderLocation.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$FiducialCheck.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Finish.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$FinishCycle.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$JobOrderHint.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Locator.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$OptimizeNozzlesForAlign.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$OptimizeNozzlesForPick.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$OptimizeNozzlesForPlace.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Pick.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$PickLocator.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Place.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$PlaceLocator.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$1.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$1JobPlacementNozzleTip.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$2.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$ReturnJobPlacementsAndNozzleTips.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan$ReturnListAndLocation.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Plan.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$PlannedPlacementStep.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$PreFlight$1.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$PreFlight.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$PrerotateAllNozzlesForAlign.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$PrerotateAllNozzlesForPick.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$PrerotateAllNozzlesForPlace.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$PrerotateAllNozzlesStep.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$SimplePnpJobPlanner$PlannerState.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$SimplePnpJobPlanner.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$StartCameraBatchOperation.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$Step.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor$TrivialPnpJobPlanner.class', 'org/openpnp/machine/reference/ReferencePnpJobProcessor.class'], 'org/openpnp/machine/reference/VacuumSensing': ['org/openpnp/machine/reference/VacuumSensing$CheckedBoolean.class', 'org/openpnp/machine/reference/VacuumSensing$CheckedString.class', 'org/openpnp/machine/reference/VacuumSensing$CheckedVoid.class', 'org/openpnp/machine/reference/VacuumSensing$ControlledSource.class', 'org/openpnp/machine/reference/VacuumSensing$Frame.class', 'org/openpnp/machine/reference/VacuumSensing$Observer.class', 'org/openpnp/machine/reference/VacuumSensing$ObserverFailure.class', 'org/openpnp/machine/reference/VacuumSensing$SampleSource.class', 'org/openpnp/machine/reference/VacuumSensing$Scope.class', 'org/openpnp/machine/reference/VacuumSensing$SensorValueException.class', 'org/openpnp/machine/reference/VacuumSensing.class'], 'org/openpnp/machine/reference/driver/NullDriver': ['org/openpnp/machine/reference/driver/NullDriver$1.class', 'org/openpnp/machine/reference/driver/NullDriver.class']}
VACUUM_CLASSES=sorted(name for names in VACUUM_FAMILIES.values() for name in names)
VACUUM_SOURCES=sorted(stem+'.java' for stem in VACUUM_FAMILIES)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def run(args,**kw):subprocess.run([str(a) for a in args],check=True,**kw)
def circular_spec(patches_root):
 spec=json.loads((patches_root/'native-circular-symmetry.json').read_text())
 expected={'schema_version':1,'upstream_commit':PIN,'patch_id':'native-circular-symmetry-v1','patch_file':'native-circular-symmetry.patch','license':'GPL-3.0-or-later','source_path':CIRCULAR_STEM+'.java','class_family':CIRCULAR_CLASSES,'java_release':11,'supported_api_changed':False,'thresholds_changed':False,'physical_calibration':False}
 for key,value in expected.items():
  if spec.get(key)!=value:raise ValueError('Circular detector source contract mismatch: '+key)
 if sorted(spec.get('reviewed_class_sha256',{}))!=CIRCULAR_CLASSES or any(not isinstance(d,str) or not re.fullmatch('[a-f0-9]{64}',d) for d in spec['reviewed_class_sha256'].values()):raise ValueError('Reviewed detector class identities are required')
 for name,key in [(spec['patch_file'],'patch_sha256'),('source/'+spec['source_path'],'source_sha256')]:
  if sha(patches_root/name)!=spec.get(key):raise ValueError('Circular detector corresponding source mismatch: '+name)
 if not isinstance(spec.get('stock_source_sha256'),str) or not re.fullmatch('[a-f0-9]{64}',spec['stock_source_sha256']):raise ValueError('Missing stock detector source identity')
 return spec
def circular_provenance(classes,spec):
 files={f.relative_to(classes).as_posix():f for f in classes.rglob('*.class') if f.relative_to(classes).as_posix()==CIRCULAR_STEM+'.class' or f.relative_to(classes).as_posix().startswith(CIRCULAR_STEM+'$')}
 if sorted(files)!=CIRCULAR_CLASSES:raise ValueError('Complete six-class circular detector family required')
 for file in files.values():
  data=file.read_bytes()
  if data[:4]!=b'\xca\xfe\xba\xbe' or data[6:8]!=b'\x00\x37':raise ValueError('Circular detector must target Java 11 bytecode')
 hashes={name:sha(files[name]) for name in CIRCULAR_CLASSES}
 if hashes!=spec['reviewed_class_sha256']:raise ValueError('Circular detector compiled classes differ from the reviewed Java 11 family')
 return {'api_version':1,'patch_id':spec['patch_id'],'patch_sha256':spec['patch_sha256'],'source_path':spec['source_path'],'source_sha256':spec['source_sha256'],'class_family_sha256':hashes,'physical_calibration':False}
def verify_circular_runtime(build,spec):
 runtime=build/'runtime';manifest=json.loads((runtime/'codex-build-manifest.json').read_text());provenance=manifest.get('native_circular_symmetry')
 expected=circular_provenance(build/'native-classes',spec)
 if provenance!=expected:raise ValueError('Circular detector runtime provenance mismatch')
 patch={'id':spec['patch_id'],'path':spec['patch_file'],'sha256':spec['patch_sha256']}
 if [p for p in manifest.get('patches',[]) if p.get('id')==spec['patch_id']]!=[patch]:raise ValueError('Circular detector runtime patch binding mismatch')
 if [p for p in manifest.get('patched_source_files',[]) if p.get('path')==spec['source_path']]!=[{'path':spec['source_path'],'sha256':spec['source_sha256']}]:raise ValueError('Circular detector runtime source binding mismatch')
 if sha(build/'source/src/main/java'/spec['source_path'])!=spec['source_sha256']:raise ValueError('Patched detector source drift')
 with zipfile.ZipFile(runtime/manifest['gui_jar']) as jar:
  names=[n for n in jar.namelist() if n==CIRCULAR_STEM+'.class' or n.startswith(CIRCULAR_STEM+'$')]
  if sorted(names)!=CIRCULAR_CLASSES:raise ValueError('Native JAR omitted or duplicated a circular detector class')
  if {n:hashlib.sha256(jar.read(n)).hexdigest() for n in names}!=expected['class_family_sha256']:raise ValueError('Native JAR circular detector class mismatch')
 return provenance
def vacuum_spec(patches_root):
 spec=json.loads((patches_root/'native-vacuum-sensing.json').read_text())
 expected={'schema_version':1,'upstream_commit':PIN,'patch_id':'native-vacuum-sensing-v1','patch_file':'native-vacuum-sensing.patch','license':'GPL-3.0-or-later','java_release':11,'api_version':1,'class_families':VACUUM_FAMILIES,'controlled_sources_serialized':False,'physical_qualification':False}
 if set(spec)!=set(expected)|{'patch_sha256','source_sha256','stock_source_sha256','reviewed_class_sha256','reviewed_native_build'}:raise ValueError('Vacuum source contract fields mismatch')
 for key,value in expected.items():
  if spec.get(key)!=value:raise ValueError('Vacuum source contract mismatch: '+key)
 for key,keys in [('source_sha256',VACUUM_SOURCES),('stock_source_sha256',VACUUM_SOURCES),('reviewed_class_sha256',VACUUM_CLASSES),('reviewed_native_build',['build_manifest_sha256','native_jar_sha256','runtime_manifest_sha256'])]:
  values=spec.get(key)
  if not isinstance(values,dict) or sorted(values)!=sorted(keys):raise ValueError('Vacuum source/class identity inventory mismatch: '+key)
  for name,digest in values.items():
   if key=='stock_source_sha256' and name=='org/openpnp/machine/reference/VacuumSensing.java':
    if digest is not None:raise ValueError('New VacuumSensing source must be absent in the pinned stock tree')
   elif not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid reviewed vacuum digest: '+key+'/'+name)
 if not isinstance(spec['patch_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',spec['patch_sha256']):raise ValueError('Invalid reviewed vacuum patch digest')
 for relative,digest in [(spec['patch_file'],spec['patch_sha256']),*[("source/"+name,digest) for name,digest in spec['source_sha256'].items()]]:
  file=patches_root/relative
  if file.is_symlink() or not file.is_file() or file.stat().st_size>1024*1024 or sha(file)!=digest:raise ValueError('Vacuum corresponding source/patch mismatch: '+relative)
 if 'GNU GENERAL PUBLIC LICENSE' not in (patches_root.parent.parent/'licenses/OpenPnP-GPL-3.0.txt').read_text():raise ValueError('Corresponding GPL license is required')
 return spec
def verify_vacuum_sources(source_root,spec,stock=False):
 for name,digest in spec['stock_source_sha256' if stock else 'source_sha256'].items():
  file=source_root/name
  if digest is None:
   if file.exists() or file.is_symlink():raise ValueError('Pinned stock vacuum source must be absent: '+name)
  elif file.is_symlink() or not file.is_file() or sha(file)!=digest:raise ValueError(('Pinned stock vacuum source mismatch: ' if stock else 'Patched vacuum source drift: ')+name)
def vacuum_provenance(classes,spec):
 files={f.relative_to(classes).as_posix():f for f in classes.rglob('*.class') if any(f.relative_to(classes).as_posix()==stem+'.class' or f.relative_to(classes).as_posix().startswith(stem+'$') for stem in VACUUM_FAMILIES)}
 if sorted(files)!=VACUUM_CLASSES:raise ValueError('Complete four-family, 58-class vacuum overlay required')
 for file in files.values():
  data=file.read_bytes()
  if file.is_symlink() or data[:4]!=b'\xca\xfe\xba\xbe' or data[6:8]!=b'\x00\x37':raise ValueError('Vacuum sensing must target Java 11 bytecode')
 hashes={name:sha(files[name]) for name in VACUUM_CLASSES}
 if hashes!=spec['reviewed_class_sha256']:raise ValueError('Vacuum compiled classes differ from the reviewed Java 11 families')
 return {'api_version':1,'patch_id':spec['patch_id'],'patch_sha256':spec['patch_sha256'],'java_release':11,'source_sha256':spec['source_sha256'],'class_family_sha256':hashes,'controlled_sources_serialized':False,'physical_qualification':False}
def verify_vacuum_runtime(build,spec,verify_build_manifest=False):
 runtime=build/'runtime';manifest_path=runtime/'codex-build-manifest.json';manifest=json.loads(manifest_path.read_text());expected=vacuum_provenance(build/'native-classes',spec)
 if manifest.get('upstream_commit')!=PIN or manifest.get('native_vacuum_sensing')!=expected:raise ValueError('Vacuum runtime provenance mismatch')
 patch={'id':spec['patch_id'],'path':spec['patch_file'],'sha256':spec['patch_sha256']}
 if [p for p in manifest.get('patches',[]) if p.get('id')==spec['patch_id']]!=[patch]:raise ValueError('Vacuum runtime patch binding mismatch')
 for name,digest in spec['source_sha256'].items():
  if [p for p in manifest.get('patched_source_files',[]) if p.get('path')==name]!=[{'path':name,'sha256':digest}]:raise ValueError('Vacuum runtime source binding mismatch')
 verify_vacuum_sources(build/'source/src/main/java',spec)
 name=manifest.get('gui_jar')
 if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.jar',name):raise ValueError('Vacuum native JAR must use an owned runtime basename')
 native=runtime/name
 if native.is_symlink() or not native.is_file():raise ValueError('Vacuum native JAR must be an owned regular file')
 if [p for p in manifest.get('files',[]) if p.get('path')==name]!=[{'path':name,'sha256':sha(native)}]:raise ValueError('Vacuum runtime JAR inventory mismatch')
 with zipfile.ZipFile(native) as jar:
  names=[n for n in jar.namelist() if any(n==stem+'.class' or n.startswith(stem+'$') for stem in VACUUM_FAMILIES)]
  if sorted(names)!=VACUUM_CLASSES:raise ValueError('Native JAR omitted, added or duplicated a vacuum class')
  for n in names:
   data=jar.read(n)
   if data[:4]!=b'\xca\xfe\xba\xbe' or data[6:8]!=b'\x00\x37' or hashlib.sha256(data).hexdigest()!=expected['class_family_sha256'][n]:raise ValueError('Native JAR vacuum Java 11 class mismatch')
 if verify_build_manifest:
  receipt=json.loads((build/'build-manifest.json').read_text())
  if receipt.get('upstream_commit')!=PIN or receipt.get('native_vacuum_sensing')!=expected:raise ValueError('Vacuum build provenance mismatch')
  if [p for p in receipt.get('patches',[]) if p.get('id')==spec['patch_id']]!=[patch]:raise ValueError('Vacuum build patch binding mismatch')
  if receipt.get('runtime_manifest_sha256')!=sha(manifest_path) or receipt.get('patched_native_jar_sha256')!=sha(native):raise ValueError('Vacuum build/runtime/JAR hash binding mismatch')
  cp=receipt.get('native_classpath_in_resolution_order')
  if not isinstance(cp,list) or not cp or cp[0]!={'path':'runtime/'+name,'sha256':sha(native)}:raise ValueError('Vacuum build native classpath binding mismatch')
  if sha(build/'openpnp-codex-bridge.jar')!=receipt.get('bridge_sha256'):raise ValueError('Vacuum build Bridge artifact binding mismatch')
 return expected
def validate_stock_runtime(runtime):
 stock=json.loads((runtime/'codex-build-manifest.json').read_text())
 if stock.get('upstream_commit')!=PIN:raise ValueError('Stock runtime pin mismatch')
 # A previously overlaid runtime is not the original input distribution, even if
 # every self-reported digest still matches. Reject it before creating output.
 markers={'gui_ownership','native_action_observer','native_board_load_history','gui_topology_events','native_circular_symmetry','native_vacuum_sensing','patches','patched_source_files','stock_native_jar_sha256'}
 if markers.intersection(stock):raise ValueError('Original stock runtime required; Codex patch provenance found')
 inventory={x['path']:x['sha256'] for x in stock['files']}
 for name,digest in inventory.items():
  f=(runtime/name).resolve()
  if not f.is_relative_to(runtime) or not f.is_file() or sha(f)!=digest:raise ValueError('Stock artifact changed: '+name)
 native=(runtime/stock['gui_jar']).resolve()
 if not native.is_relative_to(runtime) or stock['gui_jar'] not in inventory:raise ValueError('Stock native JAR must be inventoried inside its runtime')
 with zipfile.ZipFile(native) as jar:
  if 'org/openpnp/spi/base/ExternalExecutionControl.class' in jar.namelist():raise ValueError('Original stock runtime required; Codex ownership class found')
  if 'org/openpnp/machine/reference/VacuumSensing.class' in jar.namelist():raise ValueError('Original stock runtime required; Codex vacuum class found')
 return stock,inventory,native

def main():
 p=argparse.ArgumentParser();p.add_argument('--stock-source',type=Path,required=True);p.add_argument('--stock-runtime',type=Path,required=True);p.add_argument('--java-home',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 out=a.output.resolve();source=a.stock_source.resolve();runtime=a.stock_runtime.resolve();java=a.java_home.resolve()/'bin'
 if not out.is_relative_to(ROOT) or out.exists():raise ValueError('Choose a new output directory inside this isolated candidate')
 if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=PIN:raise ValueError('Stock source pin mismatch')
 run(['git','-C',source,'diff','--quiet','HEAD','--'])
 stock,inventory,native=validate_stock_runtime(runtime)
 libs=[runtime/name for name in sorted(inventory) if name.startswith(stock['libs_directory']+'/') and name.endswith('.jar')];assert libs
 out.mkdir(parents=True);tree=out/'source';tree.mkdir();archive=out/'pinned-source.tar'
 run(['git','-C',source,'archive','--format=tar','--output',archive,PIN])
 with tarfile.open(archive) as tar:tar.extractall(tree,filter='data')
 archive.unlink()
 patches_root=ROOT/'plugins/openpnp/bridge/upstream-patches';detector=circular_spec(patches_root);vacuum=vacuum_spec(patches_root)
 if sha(tree/'src/main/java'/detector['source_path'])!=detector['stock_source_sha256']:raise ValueError('Pinned stock detector source mismatch')
 verify_vacuum_sources(tree/'src/main/java',vacuum,stock=True)
 patches=[]
 for filename,identifier in [('gui-ownership.patch','codex-gui-ownership-v1'),('native-action-observer.patch','native-action-observer-v1'),('native-board-load-history.patch','native-board-load-history-v1'),('gui-topology-events.patch','gui-topology-events-v1'),('native-circular-symmetry.patch','native-circular-symmetry-v1'),('native-vacuum-sensing.patch','native-vacuum-sensing-v1')]:
  patch=ROOT/'plugins/openpnp/bridge/upstream-patches'/filename
  with patch.open('rb') as data:run(['patch','-p1','--batch','--forward','-d',tree],stdin=data)
  patches.append({'id':identifier,'path':filename,'sha256':sha(patch)})
 if sha(tree/'src/main/java'/detector['source_path'])!=detector['source_sha256']:raise ValueError('Reviewed detector patch postimage mismatch')
 verify_vacuum_sources(tree/'src/main/java',vacuum)
 changed=['org/openpnp/gui/MainFrame','org/openpnp/gui/JobPanel','org/openpnp/spi/base/AbstractMachine','org/openpnp/spi/base/ExternalExecutionControl','org/openpnp/scripting/Scripting','org/openpnp/model/Job','org/openpnp/gui/MachineControlsPanel','org/openpnp/gui/JogControlsPanel','org/openpnp/gui/support/AxesComboBoxModel','org/openpnp/gui/support/ActuatorsComboBoxModel',CIRCULAR_STEM,'org/openpnp/machine/reference/ReferenceNozzle','org/openpnp/machine/reference/ReferencePnpJobProcessor','org/openpnp/machine/reference/driver/NullDriver','org/openpnp/machine/reference/VacuumSensing']
 classes=out/'native-classes';classes.mkdir();cp=os.pathsep.join(map(str,[native,*libs]))
 run([java/'javac','--release','11','-cp',cp,'-d',classes,*[tree/'src/main/java'/(x+'.java') for x in changed]])
 detector_provenance=circular_provenance(classes,detector);vacuum_native_provenance=vacuum_provenance(classes,vacuum)
 dist=out/'runtime';dist.mkdir();patched=dist/'openpnp-gui-codex-patched.jar'
 # Replace entire selected class families, including their anonymous/inner classes.
 with zipfile.ZipFile(native) as original,zipfile.ZipFile(patched,'w',zipfile.ZIP_DEFLATED) as target:
  for entry in original.infolist():
   if any(entry.filename==x+'.class' or entry.filename.startswith(x+'$') for x in changed):continue
   target.writestr(entry,original.read(entry.filename))
  for f in sorted(classes.rglob('*.class')):
   entry=zipfile.ZipInfo(f.relative_to(classes).as_posix(),(2026,9,11,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;target.writestr(entry,f.read_bytes())
 (dist/'lib').mkdir()
 for lib in libs:shutil.copy2(lib,dist/'lib'/lib.name)
 shutil.copytree(tree/'samples/pnp-test',dist/'samples/pnp-test');shutil.copy2(tree/'LICENSE.txt',dist/'LICENSE.txt')
 launcherclasses=out/'launcher-classes';launcherclasses.mkdir();launcher_source=ROOT/'src/openpnp/java/org/openpnp/codex/IsolatedPreferencesFactory.java'
 run([java/'javac','--release','11','-d',launcherclasses,launcher_source]);launcherjar=dist/'openpnp-codex-gui-launcher.jar'
 run([java/'jar','--create','--date=2026-09-11T00:00:00Z','--file',launcherjar,'-C',launcherclasses,'.'])
 native_manifest={'upstream_commit':PIN,'gui_jar':patched.name,'gui_launcher_jar':launcherjar.name,'preferences_factory':'org.openpnp.codex.IsolatedPreferencesFactory','ui_preferences_persistence':False,'launcher_source_sha256':sha(launcher_source),'samples_directory':'samples','libs_directory':'lib','gui_ownership':{'api_version':1,'patch_id':patches[0]['id'],'patch_sha256':patches[0]['sha256']},'native_action_observer':{'api_version':1,'patch_id':patches[1]['id'],'patch_sha256':patches[1]['sha256']},'native_board_load_history':{'api_version':1,'patch_id':patches[2]['id'],'patch_sha256':patches[2]['sha256']},'gui_topology_events':{'api_version':1,'patch_id':patches[3]['id'],'patch_sha256':patches[3]['sha256']},'stock_native_jar_sha256':sha(native),'patches':patches,'build_method':'Exact stock JAR with complete recompiled class-family overlays; full pinned patched source retained alongside runtime','compiler':subprocess.check_output([str(java/'javac'),'-version'],text=True,stderr=subprocess.STDOUT).strip(),'patched_source_files':[{ 'path':x+'.java','sha256':sha(tree/'src/main/java'/(x+'.java'))} for x in changed],'files':[{'path':f.relative_to(dist).as_posix(),'sha256':sha(f)} for f in sorted(dist.rglob('*')) if f.is_file()]}
 native_manifest['native_circular_symmetry']=detector_provenance
 native_manifest['native_vacuum_sensing']=vacuum_native_provenance
 (dist/'codex-build-manifest.json').write_text(json.dumps(native_manifest,indent=2)+'\n')
 verify_circular_runtime(out,detector);verify_vacuum_runtime(out,vacuum)
 bridgeclasses=out/'bridge-classes';bridgeclasses.mkdir();patchedcp=os.pathsep.join(map(str,[patched,*[dist/'lib'/x.name for x in libs]]))
 production=sorted((ROOT/'src/openpnp/java/org/openpnp/codex').rglob('*.java'))
 run([java/'javac','--release','11','-cp',patchedcp,'-d',bridgeclasses,*production])
 bridgejar=out/'openpnp-codex-bridge.jar';run([java/'jar','--create','--date=2026-09-11T00:00:00Z','--file',bridgejar,'-C',bridgeclasses,'.'])
 testclasses=out/'test-classes';testclasses.mkdir();tests=sorted((ROOT/'tests/openpnp/native').glob('*.java'))
 # Test-only crash/restart fixtures use JDK 17 APIs. Production above remains
 # compiled against Java 11; the documented build/test runtime is JDK 17+.
 run([java/'javac','--release','17','-cp',str(bridgejar)+os.pathsep+patchedcp,'-d',testclasses,*tests])
 receipt={'upstream_commit':PIN,'bridge_version':'0.1.0','bootstrap_sha256':sha(ROOT/'plugins/openpnp/bridge/bootstrap.js'),'bridge_sha256':sha(bridgejar),'patched_native_jar_sha256':sha(patched),'runtime_manifest_sha256':sha(dist/'codex-build-manifest.json'),'production_source_sha256':{f.relative_to(ROOT).as_posix():sha(f) for f in production},'test_source_sha256':{f.relative_to(ROOT).as_posix():sha(f) for f in tests},'patches':patches,'native_classpath_in_resolution_order':[{'path':str(f.relative_to(out)),'sha256':sha(f)} for f in [patched,*[dist/'lib'/x.name for x in libs]]],'gui_tests_executed':False,'physical_qualification':False}
 receipt['native_circular_symmetry']=detector_provenance
 receipt['native_vacuum_sensing']=vacuum_native_provenance
 receipt['production_java_release']=11
 receipt['test_java_release']=17
 receipt['gui_sensing_restart']={'schema_version':1,'profile':'native-gui-source-absent-restart-v1','startup_mode':'restart','prepared_manifest_profile':'prepared-native-gui-vacuum-fixture-v1','source_installed_at_startup':False}
 (out/'build-manifest.json').write_text(json.dumps(receipt,indent=2)+'\n');verify_vacuum_runtime(out,vacuum,verify_build_manifest=True);print(json.dumps({'output':str(out),'bridge_sha256':receipt['bridge_sha256'],'native_runtime':str(dist),'tests_executed':False}))
if __name__=='__main__':main()
