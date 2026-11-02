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
 patches_root=ROOT/'plugins/openpnp/bridge/upstream-patches';detector=circular_spec(patches_root)
 if sha(tree/'src/main/java'/detector['source_path'])!=detector['stock_source_sha256']:raise ValueError('Pinned stock detector source mismatch')
 patches=[]
 for filename,identifier in [('gui-ownership.patch','codex-gui-ownership-v1'),('native-action-observer.patch','native-action-observer-v1'),('native-board-load-history.patch','native-board-load-history-v1'),('gui-topology-events.patch','gui-topology-events-v1'),('native-circular-symmetry.patch','native-circular-symmetry-v1'),('native-vacuum-sensing.patch','native-vacuum-sensing-v1')]:
  patch=ROOT/'plugins/openpnp/bridge/upstream-patches'/filename
  with patch.open('rb') as data:run(['patch','-p1','--batch','--forward','-d',tree],stdin=data)
  patches.append({'id':identifier,'path':filename,'sha256':sha(patch)})
 if sha(tree/'src/main/java'/detector['source_path'])!=detector['source_sha256']:raise ValueError('Reviewed detector patch postimage mismatch')
 changed=['org/openpnp/gui/MainFrame','org/openpnp/gui/JobPanel','org/openpnp/spi/base/AbstractMachine','org/openpnp/spi/base/ExternalExecutionControl','org/openpnp/scripting/Scripting','org/openpnp/model/Job','org/openpnp/gui/MachineControlsPanel','org/openpnp/gui/JogControlsPanel','org/openpnp/gui/support/AxesComboBoxModel','org/openpnp/gui/support/ActuatorsComboBoxModel',CIRCULAR_STEM,'org/openpnp/machine/reference/ReferenceNozzle','org/openpnp/machine/reference/ReferencePnpJobProcessor','org/openpnp/machine/reference/driver/NullDriver','org/openpnp/machine/reference/VacuumSensing']
 classes=out/'native-classes';classes.mkdir();cp=os.pathsep.join(map(str,[native,*libs]))
 run([java/'javac','--release','11','-cp',cp,'-d',classes,*[tree/'src/main/java'/(x+'.java') for x in changed]])
 detector_provenance=circular_provenance(classes,detector)
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
 native_manifest['native_vacuum_sensing']={'api_version':1,'patch_id':patches[5]['id'],'patch_sha256':patches[5]['sha256'],'controlled_sources_serialized':False,'physical_qualification':False}
 (dist/'codex-build-manifest.json').write_text(json.dumps(native_manifest,indent=2)+'\n')
 verify_circular_runtime(out,detector)
 bridgeclasses=out/'bridge-classes';bridgeclasses.mkdir();patchedcp=os.pathsep.join(map(str,[patched,*[dist/'lib'/x.name for x in libs]]))
 production=sorted((ROOT/'src/openpnp/java/org/openpnp/codex').rglob('*.java'))
 run([java/'javac','--release','11','-cp',patchedcp,'-d',bridgeclasses,*production])
 bridgejar=out/'openpnp-codex-bridge.jar';run([java/'jar','--create','--date=2026-09-11T00:00:00Z','--file',bridgejar,'-C',bridgeclasses,'.'])
 testclasses=out/'test-classes';testclasses.mkdir();tests=sorted((ROOT/'tests/openpnp/native').glob('*.java'))
 run([java/'javac','--release','11','-cp',str(bridgejar)+os.pathsep+patchedcp,'-d',testclasses,*tests])
 receipt={'upstream_commit':PIN,'bridge_version':'0.1.0','bootstrap_sha256':sha(ROOT/'plugins/openpnp/bridge/bootstrap.js'),'bridge_sha256':sha(bridgejar),'patched_native_jar_sha256':sha(patched),'runtime_manifest_sha256':sha(dist/'codex-build-manifest.json'),'production_source_sha256':{f.relative_to(ROOT).as_posix():sha(f) for f in production},'test_source_sha256':{f.relative_to(ROOT).as_posix():sha(f) for f in tests},'patches':patches,'native_classpath_in_resolution_order':[{'path':str(f.relative_to(out)),'sha256':sha(f)} for f in [patched,*[dist/'lib'/x.name for x in libs]]],'gui_tests_executed':False,'physical_qualification':False}
 receipt['native_circular_symmetry']=detector_provenance
 (out/'build-manifest.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'output':str(out),'bridge_sha256':receipt['bridge_sha256'],'native_runtime':str(dist),'tests_executed':False}))
if __name__=='__main__':main()
