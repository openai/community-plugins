#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the separate offline history helper into fresh output; does not publish or change Bridge/MCP."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile
ROOT = Path(__file__).resolve().parents[1]
PIN = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c'
SOURCE = Path('src/openpnp/controller-history/org/openpnp/codex/ControllerHistoryMain.java')
REDUCER = Path('src/openpnp/java/org/openpnp/codex/NativeControllerJournal.java')
JOURNAL_JSON = Path('src/openpnp/java/org/openpnp/codex/NativeJournalJson.java')
MAIN = 'org.openpnp.codex.ControllerHistoryMain'
def require(value, text):
    if not value: raise ValueError(text)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def archive(output, rows):
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name, content in sorted(rows):
            info=zipfile.ZipInfo(name, (2026,9,11,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644 << 16
            z.writestr(info,content)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--openpnp-home',required=True,type=Path);p.add_argument('--java-home',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    require(a.output.is_absolute() and not a.output.exists(),'Choose a fresh absolute build output.')
    bridge=ROOT/'plugins/openpnp/bridge/openpnp-codex-bridge.jar'; bm=json.loads((bridge.parent/'build-manifest.json').read_text())
    require(bm['upstream_commit']==PIN and digest(bridge)==bm['bridge_sha256'],'Bridge binding mismatch')
    for dependency in [REDUCER,JOURNAL_JSON]:
        require(digest(ROOT/dependency)==bm['production_source_sha256'][dependency.as_posix()],'Reducer dependency source mismatch')
    runtime=a.openpnp_home/'codex-build-manifest.json';require(digest(runtime)==bm['runtime_manifest_sha256'],'Runtime manifest mismatch')
    rm=json.loads(runtime.read_text());gson=a.openpnp_home/'lib/gson-2.2.3.jar';entry=[r for r in rm['files'] if r['path']=='lib/gson-2.2.3.jar'];require(len(entry)==1 and digest(gson)==entry[0]['sha256'],'Gson mismatch')
    with zipfile.ZipFile(bridge) as z:
        reducer_hash=hashlib.sha256(z.read('org/openpnp/codex/NativeControllerJournal.class')).hexdigest()
        journal_json_hash=hashlib.sha256(z.read('org/openpnp/codex/NativeJournalJson.class')).hexdigest()
    a.output.mkdir(parents=True);classes=a.output/'classes';classes.mkdir();package=a.output/'package';package.mkdir()
    javac=a.java_home/'bin/javac';command=[str(javac),'--release','11','-cp',str(bridge)+__import__('os').pathsep+str(gson),'-d',str(classes),str(ROOT/SOURCE)]
    env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'}
    run=subprocess.run(command,capture_output=True,text=True,timeout=30,env=env)
    (a.output/'compile.json').write_text(json.dumps({'argv':command,'exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr},indent=2)+'\n')
    require(run.returncode==0,'Helper compilation failed; inspect compile.json')
    class_files=sorted(classes.rglob('*.class'))
    require(class_files and all(f.name.startswith('ControllerHistoryMain') for f in class_files),'Unexpected helper class')
    archive(package/'openpnp-controller-history.jar',[(f.relative_to(classes).as_posix(),f.read_bytes()) for f in class_files])
    packaged_source=package/'source/org/openpnp/codex/ControllerHistoryMain.java';packaged_source.parent.mkdir(parents=True);shutil.copyfile(ROOT/SOURCE,packaged_source)
    license_file=ROOT/'plugins/openpnp/licenses/OpenPnP-GPL-3.0.txt';shutil.copyfile(license_file,package/'COPYING')
    source_rows=[('source/org/openpnp/codex/ControllerHistoryMain.java',(ROOT/SOURCE).read_bytes()),('reference/org/openpnp/codex/NativeControllerJournal.java',(ROOT/REDUCER).read_bytes()),('reference/org/openpnp/codex/NativeJournalJson.java',(ROOT/JOURNAL_JSON).read_bytes()),('COPYING',license_file.read_bytes()),('build/openpnp-build-controller-history.py',Path(__file__).read_bytes()),('build/Apache-2.0.txt',(ROOT/'plugins/openpnp/licenses/Apache-2.0.txt').read_bytes())]
    archive(package/'corresponding-source.zip',source_rows)
    manifest={'schema_version':2,'upstream_commit':PIN,'main_class':MAIN,'helper_sha256':digest(package/'openpnp-controller-history.jar'),'source_sha256':digest(ROOT/SOURCE),'source_archive_sha256':digest(package/'corresponding-source.zip'),'reducer_class_sha256':reducer_hash,'reducer_source_sha256':digest(ROOT/REDUCER),'journal_json_class_sha256':journal_json_hash,'journal_json_source_sha256':digest(ROOT/JOURNAL_JSON),'built_against_bridge_sha256':digest(bridge),'gson_sha256':digest(gson),'runtime_manifest_sha256':digest(runtime),'compiler_sha256':digest(javac),'java_release':11,'build_script_sha256':digest(Path(__file__)),'scope':'Offline recorded history; helper-only classes; matching actual Bridge reducer and exact-number dependency; no native authority or physical qualification'}
    (package/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    require(digest(bridge)==bm['bridge_sha256'],'Bridge changed during helper build')
    print(json.dumps({'output':str(a.output),'helper_sha256':manifest['helper_sha256'],'bridge_unchanged':True,'package_published':False}))
if __name__=='__main__':main()
