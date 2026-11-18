import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { McpClient } from '../support/client.mjs';
import { runtimeSpec } from '../../../plugins/boundaryxp/scripts/launch.mjs';

test('cold packaged install, actual native tools, multiple DEV tasks and persistent dictionaries', { timeout: 360_000, skip: !['win32','linux'].includes(process.platform) || process.arch !== 'x64' ? 'Native runtime requires Windows x64 or Linux x64/glibc >=2.36; not qualified on this host.' : false }, async t => {
  const work = await fs.mkdtemp(path.join(os.tmpdir(), 'bxp-plugin-e2e-'));
  const plugin = path.join(work, 'copied plugin');
  await fs.cp(fileURLToPath(new URL('../../../plugins/boundaryxp/', import.meta.url)), plugin, { recursive:true });
  const home = path.join(work, 'isolated data');
  const config = JSON.parse(await fs.readFile(path.join(plugin,'.mcp.json'),'utf8')).mcpServers.boundaryxp;
  const start = () => new McpClient(process.execPath,config.args,{cwd:path.resolve(plugin,config.cwd),env:{...process.env,BXP_PLUGIN_HOME:home}});
  let client = start(); let project; const dictionaryIds = [];
  const receipt = {version:runtimeSpec().version,platform:process.platform,node:process.version,model_requests:0,original_core_source_in_plugin:false};
  try {
    await client.connect();
    const tools = (await client.request('tools/list')).tools;
    const resources = (await client.request('resources/list')).resources;
    assert.equal(tools.length,21); assert.equal(resources.length,17);
    receipt.tools=tools.length; receipt.resources=resources.length;
    for (const language of ['en','zh-CN']) {
      const caps = await client.call('bx_capabilities',{language,help_topic:'tools'});
      assert.equal(caps.product.adapter_version,runtimeSpec().version);
      assert.equal(caps.business_decision,null); assert.equal(caps.execution_authorization,null);
    }
    const profiles = (await client.call('bx_profiles_list')).data.profiles;
    assert(profiles.some(p => p.id === 'host'));
    assert.equal((await client.call('bx_projects_list')).data.total,0);
    for (const layer of ['L2','L3']) {
      for (const language of ['en','zh-CN']) {
        const example = (await client.call('bx_dictionary_format',{layer,language})).data.example;
        assert.equal(example.schema,'boundaryxp-dictionary-directory/1');
        assert.equal(example.files.length,layer === 'L2' ? 9 : 4);
        const document = JSON.stringify(example);
        const validation = (await client.call('bx_dictionary_validate',{document})).data;
        assert.equal(validation.valid,true);
        if (language === 'en') {
          const imported = (await client.call('bx_dictionary_import',{document,sha256:validation.sha256})).data;
          assert.equal(imported.valid,true); assert.equal(imported.imported,true);
          const repeated = (await client.call('bx_dictionary_import',{document,sha256:validation.sha256})).data;
          assert.equal(repeated.valid,true);
          dictionaryIds.push(example.id);
        }
      }
    }
    const bad = (await client.call('bx_dictionary_validate',{document:'{"invalid":true}'})).data;
    assert.equal(bad.valid,false);
    const binding = (await client.call('bx_dictionaries_bind',{selection:{}})).data;
    const source = await fs.readFile(path.join(plugin,'examples/multi-dev.md'),'utf8');
    const preview = (await client.call('bx_task_import',{source,label:'Plugin package test',profile_id:'host',dictionaries:{selection:{},sha256:binding.sha256}})).data;
    assert.equal(preview.requires_parent_confirmation,false); assert.equal(preview.input.nodes.length,3);
    assert.equal(new Set(preview.input.nodes.map(n => n.id)).size,3);
    assert.equal(preview.source_artifact.sha256,createHash('sha256').update(source).digest('hex'));
    assert.equal((await client.call('bx_task_validate',{input:preview.input})).data.valid,true);
    const request_id = randomUUID();
    project = (await client.call('bx_project_create',{request_id,input:preview.input})).data;
    const repeated = (await client.call('bx_project_create',{request_id,input:preview.input})).data;
    assert.equal(repeated.project_id,project.project_id);
    const saved = (await client.call('bx_project_get',{project_id:project.project_id,revision:project.revision})).data;
    assert.equal(saved.nodes.length,3);
    const sourcePage = (await client.call('bx_artifact_get',{sha256:preview.source_artifact.sha256})).data;
    assert.equal(sourcePage.text,source);
    const workbench = (await client.call('bx_workbench_open',{project_id:project.project_id})).data;
    const url = new URL(workbench.url);
    assert.equal(url.hostname,'127.0.0.1'); assert.notEqual(url.port,'58618');
    assert.equal((await fetch(workbench.url)).status,200); assert.equal(workbench.model_requests,0);
    receipt.workbench_http=200; receipt.nodes=3; receipt.format_examples=4;
    const dictionariesBefore = (await client.call('bx_dictionaries_list')).data.packs.filter(p => p.custom);
    assert.equal(dictionariesBefore.length,2);
    const profileBytes = await fs.readFile(path.join(home,'host-profiles.json'));
    const runtimePath = path.join(home,`runtime-${runtimeSpec().version}`,'plugin-runtime.json');
    const runtimeStat = await fs.stat(runtimePath);
    await client.close(); client=start(); await client.connect();
    assert.equal((await client.call('bx_projects_list')).data.total,1);
    assert.equal((await client.call('bx_project_get',{project_id:project.project_id,revision:project.revision})).data.nodes.length,3);
    assert.deepEqual((await client.call('bx_dictionaries_list')).data.packs.filter(p=>p.custom),dictionariesBefore);
    assert.deepEqual(await fs.readFile(path.join(home,'host-profiles.json')),profileBytes);
    assert.equal((await fs.stat(runtimePath)).mtimeMs,runtimeStat.mtimeMs);
    receipt.reconnect_preserved=true; receipt.custom_dictionaries=2;
    await fs.writeFile(path.join(work,'receipt.json'),JSON.stringify(receipt,null,2));
    t.diagnostic('Real compiled-package receipt: '+JSON.stringify(receipt));
    t.diagnostic('Isolated evidence retained at '+work);
  } finally { await client.close(); }
});
