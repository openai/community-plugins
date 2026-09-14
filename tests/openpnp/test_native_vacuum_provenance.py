"""Vacuum package boundaries using synthetic class bytes; no native machine work is launched."""
import copy,importlib.util,json,shutil,tempfile,unittest,warnings,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('vacuum_native_builder',ROOT/'scripts/openpnp-build-native-gui.py');B=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(B)
class VacuumProvenanceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.classes=self.root/'native-classes';self.packages=ROOT/'plugins/openpnp/bridge/upstream-patches';self.spec=B.vacuum_spec(self.packages)
  for name in B.VACUUM_CLASSES:
   f=self.classes/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'\xca\xfe\xba\xbe\x00\x00\x00\x37'+name.encode())
  self.spec['reviewed_class_sha256']={n:B.sha(self.classes/n) for n in B.VACUUM_CLASSES};self.provenance=B.vacuum_provenance(self.classes,self.spec)
  for name in B.VACUUM_SOURCES:
   dst=self.root/'source/src/main/java'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(self.packages/'source'/name,dst)
  self.runtime=self.root/'runtime';self.runtime.mkdir();self.jar=self.runtime/'native.jar';self.bridge=self.root/'openpnp-codex-bridge.jar';self.bridge.write_bytes(b'owned Bridge fixture')
  self.patch={'id':self.spec['patch_id'],'path':self.spec['patch_file'],'sha256':self.spec['patch_sha256']}
  self.manifest={'upstream_commit':B.PIN,'gui_jar':'native.jar','native_vacuum_sensing':copy.deepcopy(self.provenance),'patches':[self.patch],'patched_source_files':[{'path':p,'sha256':v} for p,v in self.spec['source_sha256'].items()]}
  self.write_jar()
 def write_manifests(self):
  self.manifest['files']=[{'path':'native.jar','sha256':B.sha(self.jar)}];(self.runtime/'codex-build-manifest.json').write_text(json.dumps(self.manifest))
  self.receipt={'upstream_commit':B.PIN,'native_vacuum_sensing':copy.deepcopy(self.provenance),'patches':[self.patch],'bridge_sha256':B.sha(self.bridge),'runtime_manifest_sha256':B.sha(self.runtime/'codex-build-manifest.json'),'patched_native_jar_sha256':B.sha(self.jar),'native_classpath_in_resolution_order':[{'path':'runtime/native.jar','sha256':B.sha(self.jar)}]};self.write_receipt()
 def write_receipt(self):(self.root/'build-manifest.json').write_text(json.dumps(self.receipt))
 def write_jar(self,omit=(),extra=None,changed=None,duplicate=None):
  with warnings.catch_warnings():
   warnings.simplefilter('ignore',UserWarning)
   with zipfile.ZipFile(self.jar,'w') as z:
    z.writestr('unrelated.class',b'unrelated stock class')
    for name in B.VACUUM_CLASSES:
     if name not in omit:z.writestr(name,b'bad Java bytes' if name==changed else (self.classes/name).read_bytes())
    if extra:z.writestr(extra,b'new unreviewed class')
    if duplicate:z.writestr(duplicate,(self.classes/duplicate).read_bytes())
  self.write_manifests()
 def verify(self):return B.verify_vacuum_runtime(self.root,self.spec,verify_build_manifest=True)
 def test_complete_four_families_and_build_runtime_jar_binding(self):
  self.assertEqual(len(self.provenance['class_family_sha256']),58);self.assertEqual(self.verify(),self.provenance)
 def test_every_missing_compiled_class_refused(self):
  for name in B.VACUUM_CLASSES:
   with self.subTest(name=name):
    f=self.classes/name;data=f.read_bytes();f.unlink()
    with self.assertRaisesRegex(ValueError,'58-class'):B.vacuum_provenance(self.classes,self.spec)
    f.write_bytes(data)
 def test_each_family_jar_missing_changed_duplicate_and_extra_refused_after_rehash(self):
  for stem,names in B.VACUUM_FAMILIES.items():
   with self.subTest(stem=stem):
    for mutation in [{'omit':names},{'changed':names[0]},{'duplicate':names[0]},{'extra':stem+'$Unreviewed.class'}]:
     self.write_jar(**mutation)
     with self.assertRaisesRegex(ValueError,'Native JAR'):self.verify()
  self.write_jar();self.verify()
 def test_extra_compiled_class_wrong_major_and_reviewed_byte_drift(self):
  name=B.VACUUM_CLASSES[0];f=self.classes/name;data=f.read_bytes();extra=self.classes/(next(iter(B.VACUUM_FAMILIES))+'$Extra.class');extra.write_bytes(data)
  with self.assertRaisesRegex(ValueError,'58-class'):self.verify()
  extra.unlink();f.write_bytes(data[:6]+b'\x00\x3d'+data[8:])
  with self.assertRaisesRegex(ValueError,'Java 11'):self.verify()
  f.write_bytes(data+b'changed')
  with self.assertRaisesRegex(ValueError,'reviewed'):self.verify()
 def test_each_source_postimage_and_runtime_marker_mismatch(self):
  for name in B.VACUUM_SOURCES:
   f=self.root/'source/src/main/java'/name;data=f.read_bytes();f.write_bytes(data+b'changed')
   with self.assertRaisesRegex(ValueError,'source drift'):self.verify()
   f.write_bytes(data)
  original=copy.deepcopy(self.manifest)
  for mutate in [lambda m:m.pop('native_vacuum_sensing'),lambda m:m['patches'].append(copy.deepcopy(m['patches'][0])),lambda m:m['patched_source_files'].pop(),lambda m:m['native_vacuum_sensing'].__setitem__('controlled_sources_serialized',True),lambda m:m['native_vacuum_sensing']['class_family_sha256'].pop(B.VACUUM_CLASSES[0])]:
   self.manifest=copy.deepcopy(original);mutate(self.manifest);self.write_manifests()
   with self.assertRaises(ValueError):self.verify()
 def test_build_binding_mismatches(self):
  original=copy.deepcopy(self.receipt)
  for key in ['bridge_sha256','runtime_manifest_sha256','patched_native_jar_sha256']:
   self.receipt=copy.deepcopy(original);self.receipt[key]='0'*64;self.write_receipt()
   with self.assertRaisesRegex(ValueError,'binding'):self.verify()
  for mutate in [lambda r:r.pop('native_vacuum_sensing'),lambda r:r['native_classpath_in_resolution_order'][0].__setitem__('path','outside.jar'),lambda r:r['patches'].append(copy.deepcopy(r['patches'][0]))]:
   self.receipt=copy.deepcopy(original);mutate(self.receipt);self.write_receipt()
   with self.assertRaises(ValueError):self.verify()
 def test_corresponding_source_and_manifest_closure(self):
  copied=self.root/'plugin/bridge/upstream-patches';shutil.copytree(self.packages,copied);shutil.copytree(ROOT/'plugins/openpnp/licenses',self.root/'plugin/licenses')
  for relative in [self.spec['patch_file'],*['source/'+n for n in B.VACUUM_SOURCES]]:
   f=copied/relative;data=f.read_bytes();f.write_bytes(data+b'changed')
   with self.assertRaisesRegex(ValueError,'corresponding source'):B.vacuum_spec(copied)
   f.write_bytes(data)
  value=json.loads((copied/'native-vacuum-sensing.json').read_text());value['class_families'].pop(next(iter(value['class_families'])));(copied/'native-vacuum-sensing.json').write_text(json.dumps(value))
  with self.assertRaisesRegex(ValueError,'source contract'):B.vacuum_spec(copied)
 def test_stock_preimage_guard_without_source_mutation(self):
  source=self.root/'stock';source.mkdir();spec=copy.deepcopy(self.spec)
  for name in B.VACUUM_SOURCES:
   if spec['stock_source_sha256'][name] is not None:
    f=source/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(name.encode());spec['stock_source_sha256'][name]=B.sha(f)
  B.verify_vacuum_sources(source,spec,stock=True)
  f=source/'org/openpnp/machine/reference/VacuumSensing.java';f.write_bytes(b'pre-existing source')
  with self.assertRaisesRegex(ValueError,'must be absent'):B.verify_vacuum_sources(source,spec,stock=True)
  f.unlink();f=source/B.VACUUM_SOURCES[0];f.write_bytes(b'drift')
  with self.assertRaisesRegex(ValueError,'stock vacuum source mismatch'):B.verify_vacuum_sources(source,spec,stock=True)
 def test_stripped_vacuum_marker_cannot_make_runtime_stock(self):
  manifest={'upstream_commit':B.PIN,'gui_jar':'native.jar','libs_directory':'lib','files':[{'path':'native.jar','sha256':B.sha(self.jar)}]};(self.runtime/'codex-build-manifest.json').write_text(json.dumps(manifest))
  with self.assertRaisesRegex(ValueError,'vacuum class'):B.validate_stock_runtime(self.runtime)
  manifest['native_vacuum_sensing']=None;(self.runtime/'codex-build-manifest.json').write_text(json.dumps(manifest))
  with self.assertRaisesRegex(ValueError,'patch provenance'):B.validate_stock_runtime(self.runtime)
if __name__=='__main__':unittest.main()
