import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { BridgeClient } from '../../src/openpnp/node/client.mjs';
import { TOOL_DEFINITIONS, TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { createServer } from '../../src/openpnp/node/server.mjs';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { InMemoryTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/inMemory.js';
const methods=['openpnp_get_material_loads','openpnp_register_material_load'];
const request='04c0d3b0-453d-4b9e-b784-dbe92396b2c1', load='6115722e-8bbd-4d35-87ac-7a3e37927547';
const input=()=>({session_id:'session',request_id:request,expected_config_revision:'cfg-2',expected_material_revision:'material-1',feeder_id:'TRAY:1',part_id:'R0603-1K',expected_geometry_sha256:'a'.repeat(64),action:'bind-existing'});
const caps={schema_version:1,bridge_version:'0.1.0',upstream_commit:PINNED_UPSTREAM,tools:methods};
function fixture(tools=methods){const calls=[];const runtime=new OpenPnpRuntime({client:{async call(name,args,options){calls.push({name,args,options});return name==='openpnp_get_capabilities'?{...caps,tools}:{operation_id:'operation',request_id:args.request_id,state:'accepted'};}}});return{calls,runtime};}

test('61-tool catalog retains strict read and async material registration; route only advertised tools',async()=>{
 assert.equal(TOOL_DEFINITIONS.length,61);const inspectionNames=['openpnp_request_board_inspection','openpnp_get_board_inspection'];
 assert.equal(TOOL_DEFINITIONS.filter(x=>!inspectionNames.includes(x.name)).length,59);assert.deepEqual(TOOL_DEFINITIONS.filter(x=>inspectionNames.includes(x.name)).map(x=>x.name),inspectionNames);const{runtime,calls}=fixture();
 for(const args of [input(),{...input(),action:'replace-full-tray',expected_load_id:load}]){const copy=structuredClone(args);await runtime.call(methods[1],args);assert.deepEqual(calls.at(-1),{name:methods[1],args:copy,options:{mutating:true}});assert.deepEqual(args,copy);}
 await runtime.call(methods[0],{});assert.deepEqual(calls.at(-1),{name:methods[0],args:{},options:{mutating:false}});
 assert.equal(TOOL_BY_NAME.get(methods[0]).annotations.readOnlyHint,true);assert.equal(TOOL_BY_NAME.get(methods[1]).annotations.readOnlyHint,false);
 assert.equal(calls.length,6,'One discovery and one requested dispatch per action.');
 const absent=fixture(['openpnp_get_status']);for(const[name,args]of[[methods[0],{}],[methods[1],input()]])await assert.rejects(absent.runtime.call(name,args),{code:'UNSUPPORTED_CAPABILITY'});assert.ok(absent.calls.every(x=>x.name==='openpnp_get_capabilities'));
});

test('malformed IDs, incomplete guards and refill shortcuts reject before native dispatch',async()=>{
 const{runtime,calls}=fixture();const bad=[];for(const key of Object.keys(input())){const value=input();delete value[key];bad.push(value);}
 for(const [key,value]of[['request_id','1-1-1-1-1'],['request_id',request.toUpperCase()],['expected_load_id',load],['expected_material_revision','cfg-1'],['expected_geometry_sha256','G'.repeat(64)],['feeder_id',true],['part_id',''],['action','refill'],['quantity',4],['feed_count',0],['host','127.0.0.1'],['raw_gcode','M115'],['clear_history',true]])bad.push({...input(),[key]:value});
 bad.push({...input(),action:'replace-full-tray'},{...input(),action:'replace-full-tray',expected_load_id:'not-a-uuid'});
 for(const args of bad)await assert.rejects(runtime.call(methods[1],args),{code:'INVALID_ARGUMENT'});
 await assert.rejects(runtime.call(methods[0],{feeder_id:'TRAY:1'}),{code:'INVALID_ARGUMENT'});assert.equal(calls.length,0);
});

test('lost material mutation receipt stays unknown with original request and no automatic replay (transport fixture)',async t=>{
 const dir=await mkdtemp(path.join(os.tmpdir(),'material-client-'));t.after(()=>rm(dir,{recursive:true,force:true}));await writeFile(path.join(dir,'token'),'T'.repeat(48),{mode:0o600});await writeFile(path.join(dir,'connection.json'),JSON.stringify({url:'http://127.0.0.1:54321/',tokenFile:path.join(dir,'token')}),{mode:0o600});
 let calls=0;const client=new BridgeClient({connectionFile:path.join(dir,'connection.json'),fetchImpl:async()=>{calls++;return new Response(JSON.stringify({result:{load_id:load}}));}});
 await assert.rejects(client.call(methods[1],input(),{mutating:true}),e=>{assert.equal(e.code,'OUTCOME_UNKNOWN');assert.equal(e.details.request_id,request);return true;});assert.equal(calls,1);
});

test('official SDK carries exact material guards and rejects unadvertised or malformed calls (adapter fixture)',async t=>{
 const{runtime,calls}=fixture();const server=createServer(runtime);const client=new Client({name:'material-contract-fixture',version:'1'});const[a,b]=InMemoryTransport.createLinkedPair();t.after(async()=>{await client.close();await server.close();});await Promise.all([server.connect(b),client.connect(a)]);
 const catalog=await client.listTools();assert.ok(methods.every(name=>catalog.tools.some(x=>x.name===name)));
 let result=await client.callTool({name:methods[1],arguments:input()});assert.notEqual(result.isError,true);assert.equal(result.structuredContent.operation_id,'operation');assert.deepEqual(calls.at(-1).args,input());
 const before=calls.length;result=await client.callTool({name:methods[1],arguments:{...input(),action:'replace-full-tray'}});assert.equal(result.isError,true);assert.equal(result.structuredContent.error.code,'INVALID_ARGUMENT');assert.equal(calls.length,before);
});
