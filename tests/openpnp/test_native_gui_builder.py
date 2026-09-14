"""Stock-input boundary tests; these do not qualify native machine behavior."""
import copy, hashlib, importlib.util, json, os, subprocess, sys, tempfile, unittest, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('openpnp_gui_builder',ROOT/'scripts/openpnp-build-native-gui.py')
BUILDER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(BUILDER)
class StockInputTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.runtime=self.root/'runtime';self.runtime.mkdir()
  self.jar=self.runtime/'stock.jar'
  with zipfile.ZipFile(self.jar,'w') as jar:jar.writestr('org/openpnp/gui/MainFrame.class',b'fixture')
  self.manifest={'upstream_commit':BUILDER.PIN,'gui_jar':'stock.jar','libs_directory':'lib','files':[{'path':'stock.jar','sha256':BUILDER.sha(self.jar)}]}
  self.write()
 def write(self): (self.runtime/'codex-build-manifest.json').write_text(json.dumps(self.manifest))
 def test_unpatched_inventoried_container_admitted(self):
  stock,files,jar=BUILDER.validate_stock_runtime(self.runtime);self.assertEqual(jar,self.jar);self.assertEqual(stock,self.manifest)
 def test_each_patch_marker_refused_even_empty(self):
  for marker in ['gui_ownership','native_action_observer','native_board_load_history','gui_topology_events','native_circular_symmetry','patches','patched_source_files','stock_native_jar_sha256']:
   with self.subTest(marker=marker):
    self.manifest[marker]=None;self.write()
    with self.assertRaisesRegex(ValueError,'patch provenance'):BUILDER.validate_stock_runtime(self.runtime)
    del self.manifest[marker]
 def test_patched_class_with_stripped_metadata_refused(self):
  with zipfile.ZipFile(self.jar,'a') as jar:jar.writestr('org/openpnp/spi/base/ExternalExecutionControl.class',b'fixture')
  self.manifest['files'][0]['sha256']=BUILDER.sha(self.jar);self.write()
  with self.assertRaisesRegex(ValueError,'ownership class'):BUILDER.validate_stock_runtime(self.runtime)
 def test_changed_artifact_refused(self):
  self.jar.write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'artifact changed'):BUILDER.validate_stock_runtime(self.runtime)
 def test_uninventoried_native_refused(self):
  self.manifest['files']=[];self.write()
  with self.assertRaisesRegex(ValueError,'must be inventoried'):BUILDER.validate_stock_runtime(self.runtime)
 def test_wrong_pin_refused(self):
  self.manifest['upstream_commit']='wrong';self.write()
  with self.assertRaisesRegex(ValueError,'pin mismatch'):BUILDER.validate_stock_runtime(self.runtime)
class CircularDetectorBuildTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.build=Path(self.tmp.name).resolve();self.classes=self.build/'native-classes';self.patches=ROOT/'plugins/openpnp/bridge/upstream-patches';self.spec=BUILDER.circular_spec(self.patches)
  for name in BUILDER.CIRCULAR_CLASSES:
   file=self.classes/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'\xca\xfe\xba\xbe\x00\x00\x00\x37'+name.encode())
  # Synthetic class headers exercise packaging, not OpenPnP execution.
  self.spec['reviewed_class_sha256']={name:BUILDER.sha(self.classes/name) for name in BUILDER.CIRCULAR_CLASSES}
  self.provenance=BUILDER.circular_provenance(self.classes,self.spec)
  source=self.build/'source/src/main/java'/self.spec['source_path'];source.parent.mkdir(parents=True);source.write_bytes((self.patches/'source'/self.spec['source_path']).read_bytes())
  self.runtime=self.build/'runtime';self.runtime.mkdir();self.jar=self.runtime/'native.jar';self.write_jar()
  self.manifest={'gui_jar':'native.jar','native_circular_symmetry':self.provenance,'patches':[{'id':self.spec['patch_id'],'path':self.spec['patch_file'],'sha256':self.spec['patch_sha256']}],'patched_source_files':[{'path':self.spec['source_path'],'sha256':self.spec['source_sha256']}]};self.write_manifest()
 def write_jar(self,omit=None,extra=None,changed=None):
  with zipfile.ZipFile(self.jar,'w') as jar:
   jar.writestr('org/openpnp/model/Board.class',b'unrelated fixture')
   for name in BUILDER.CIRCULAR_CLASSES:
    if name!=omit:jar.writestr(name,b'wrong bytes' if name==changed else (self.classes/name).read_bytes())
   if extra:jar.writestr(extra,b'unexpected family member')
 def write_manifest(self): (self.runtime/'codex-build-manifest.json').write_text(json.dumps(self.manifest))
 def test_complete_family_and_corresponding_source_admitted(self):
  self.assertEqual(len(self.provenance['class_family_sha256']),6)
  self.assertEqual(BUILDER.verify_circular_runtime(self.build,self.spec),self.provenance)
 def test_every_missing_class_refused_before_overlay(self):
  for name in BUILDER.CIRCULAR_CLASSES:
   with self.subTest(name=name):
    file=self.classes/name;content=file.read_bytes();file.unlink()
    with self.assertRaisesRegex(ValueError,'six-class'):BUILDER.circular_provenance(self.classes,self.spec)
    file.write_bytes(content)
 def test_extra_class_and_wrong_java_release_refused(self):
  extra=self.classes/(BUILDER.CIRCULAR_STEM+'$Unexpected.class');extra.write_bytes(b'not valid')
  with self.assertRaisesRegex(ValueError,'six-class'):BUILDER.circular_provenance(self.classes,self.spec)
  extra.unlink();file=self.classes/BUILDER.CIRCULAR_CLASSES[0];file.write_bytes(b'\xca\xfe\xba\xbe\x00\x00\x00\x3dwrong release')
  with self.assertRaisesRegex(ValueError,'Java 11'):BUILDER.circular_provenance(self.classes,self.spec)
 def test_each_native_jar_omission_and_wrong_overlay_refused(self):
  for name in BUILDER.CIRCULAR_CLASSES:
   with self.subTest(name=name):
    self.write_jar(omit=name)
    with self.assertRaisesRegex(ValueError,'omitted'):BUILDER.verify_circular_runtime(self.build,self.spec)
  self.write_jar(changed=BUILDER.CIRCULAR_CLASSES[0])
  with self.assertRaisesRegex(ValueError,'class mismatch'):BUILDER.verify_circular_runtime(self.build,self.spec)
  self.write_jar(extra=BUILDER.CIRCULAR_STEM+'$Unexpected.class')
  with self.assertRaisesRegex(ValueError,'omitted or duplicated'):BUILDER.verify_circular_runtime(self.build,self.spec)
 def test_missing_flag_wrong_patch_class_and_source_provenance_refused(self):
  original=copy.deepcopy(self.manifest)
  for field in ['missing','patch','class','source','list']:
   with self.subTest(field=field):
    self.manifest=copy.deepcopy(original)
    if field=='missing':del self.manifest['native_circular_symmetry']
    elif field=='patch':self.manifest['native_circular_symmetry']['patch_sha256']='0'*64
    elif field=='class':self.manifest['native_circular_symmetry']['class_family_sha256'].pop(BUILDER.CIRCULAR_CLASSES[0])
    elif field=='source':self.manifest['patched_source_files'][0]['sha256']='0'*64
    else:self.manifest['patches']=[]
    self.write_manifest()
    with self.assertRaises(ValueError):BUILDER.verify_circular_runtime(self.build,self.spec)
 def test_retained_gpl_source_drift_refused(self):
  (self.build/'source/src/main/java'/self.spec['source_path']).write_text('drift')
  with self.assertRaisesRegex(ValueError,'source drift'):BUILDER.verify_circular_runtime(self.build,self.spec)
 def test_valid_bytecode_header_with_wrong_reviewed_class_bytes_refused(self):
  file=self.classes/BUILDER.CIRCULAR_CLASSES[0];file.write_bytes(file.read_bytes()+b'changed')
  with self.assertRaisesRegex(ValueError,'differ from the reviewed'):BUILDER.circular_provenance(self.classes,self.spec)
 def test_packaged_patch_and_source_must_match_spec(self):
  import shutil
  copied=self.build/'patches';shutil.copytree(self.patches,copied)
  for relative in [self.spec['patch_file'],'source/'+self.spec['source_path']]:
   file=copied/relative;original=file.read_bytes();file.write_bytes(original+b'changed')
   with self.assertRaisesRegex(ValueError,'corresponding source mismatch'):BUILDER.circular_spec(copied)
   file.write_bytes(original)
  value=copy.deepcopy(self.spec);value['class_family']=value['class_family'][:-1];(copied/'native-circular-symmetry.json').write_text(json.dumps(value))
  with self.assertRaisesRegex(ValueError,'source contract mismatch'):BUILDER.circular_spec(copied)
if __name__=='__main__':unittest.main()
