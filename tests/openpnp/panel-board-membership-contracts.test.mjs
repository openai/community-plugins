import { RECOVERY_TOOLS, normalizeMaterialDescription } from './fixtures/sensing79-retained-contracts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { InMemoryTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/inMemory.js';
import { TOOL_BY_NAME, TOOL_DEFINITIONS } from '../../src/openpnp/node/contracts.mjs';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { BridgeClient } from '../../src/openpnp/node/client.mjs';
import { createServer } from '../../src/openpnp/node/server.mjs';

const PLAN='openpnp_plan_placement_structure', APPLY='openpnp_apply_placement_structure';
const PROFILE='panel-board-membership-v1';
const INSPECTION_TOOLS=['openpnp_request_board_inspection','openpnp_get_board_inspection'];
const SENSING_TOOLS=['openpnp_measure_sensor','openpnp_verify_part_state'];
const envelope={request_id:'panel-request-original',session_id:'lease',expected_config_revision:'cfg-3',job_id:'job',expected_job_revision:'a'.repeat(64),expected_board_load_revision:'load-2'};
const clone=()=>({action:'clone_board_child',scope:'job_instance',parent_instance_id:'P1⇒nested',source_child_id:'A',new_child_id:'C',location:{frame:'holder',units:'mm',x:10,y:-20,z:0,rotation:-90},side:'Bottom',enabled:false,check_fiducials:true});
const remove=()=>({action:'remove_board_child',scope:'job_instance',parent_instance_id:'P1⇒nested',child_id:'B'});
const plan=(change=clone())=>({...envelope,changes:[change]});
const legacy=()=>({...envelope,changes:[{action:'remove',scope:'job_shared_definition',holder_instance_id:'P1⇒B1',placement_id:'R1'}]});
const validate=new Ajv({strict:true,allErrors:true,coerceTypes:false,useDefaults:false}).compile(TOOL_BY_NAME.get(PLAN).inputSchema);
const caps=(profile=PROFILE)=>({schema_version:1,bridge_version:'0.1.0',upstream_commit:PINNED_UPSTREAM,tools:[PLAN,APPLY],...(profile===undefined?{}:{panel_board_membership:{profile}})});
function fixture(capabilities=caps(),result={operation_id:'native-operation',state:'accepted'}){
 const calls=[];
 const runtime=new OpenPnpRuntime({client:{async call(name,args,options){calls.push({name,args,options});return name==='openpnp_get_capabilities'?capabilities:result;}}});
 return{runtime,calls};
}
async function rejectsBeforeRead(values){
 const {runtime,calls}=fixture();for(const value of values)await assert.rejects(runtime.call(PLAN,value),{code:'INVALID_ARGUMENT'},JSON.stringify(value));assert.equal(calls.length,0);
}

test('panel extension retains the exact legacy placement schema and all earlier fields except the explicitly checked material descriptions',async()=>{
 const baseline=JSON.parse(await readFile(new URL('./fixtures/panel73-legacy-contract.json',import.meta.url),'utf8'));
 const current=structuredClone(TOOL_BY_NAME.get(PLAN));
 assert.deepEqual(current.inputSchema.properties.changes.oneOf[0],baseline.structure_tool.inputSchema.properties.changes);
 current.inputSchema.properties.changes=current.inputSchema.properties.changes.oneOf[0];current.description=baseline.structure_tool.description;
 assert.deepEqual(current,baseline.structure_tool);
 assert.match(TOOL_BY_NAME.get(PLAN).description,/panel-board-membership-v1/);
 // Panel74 changes only the portable tool's description; bind every other field to the exact retained Panel73 definition.
 const priorPortable=JSON.parse(await readFile(new URL('./fixtures/panel74-legacy-portable-tool.json',import.meta.url),'utf8')).tool;
 const portable=structuredClone(TOOL_BY_NAME.get('openpnp_export_portable_configuration'));
 assert.match(portable.description,/saved-board-child-panel-library-v1/);
 portable.description=priorPortable.description;
 assert.deepEqual(portable,priorPortable,'Portable request schema and annotations are unchanged');
 const other=Object.fromEntries(TOOL_DEFINITIONS.filter(x=>x.name!==PLAN&&![...INSPECTION_TOOLS,...SENSING_TOOLS,...RECOVERY_TOOLS].includes(x.name)).map(x=>{
  const retained=normalizeMaterialDescription(structuredClone(x.name===portable.name?portable:x));
  if(retained.name==='openpnp_plan_configuration'){const variants=retained.inputSchema.properties.changes.items.oneOf;const added=variants.filter(v=>v.properties.type.const==='set_vacuum_sensing_settings');assert.equal(added.length,1);variants.splice(variants.indexOf(added[0]),1);}
  return[x.name,createHash('sha256').update(JSON.stringify(retained)).digest('hex')];
 }));
 assert.deepEqual(other,baseline.other_tool_sha256);assert.equal(TOOL_DEFINITIONS.filter(x=>!INSPECTION_TOOLS.includes(x.name)).length,59);
 assert.deepEqual(TOOL_DEFINITIONS.filter(x=>INSPECTION_TOOLS.includes(x.name)).map(x=>x.name),INSPECTION_TOOLS);assert.equal(TOOL_DEFINITIONS.length,61);
});

test('clone and remove forward complete instance-local identities, units and explicit flags exactly once',async()=>{
 const inches=clone();inches.location={frame:'holder',units:'in',x:1,y:-2,z:0.25,rotation:360};inches.side='Top';inches.enabled=true;inches.check_fiducials=false;
 for(const args of [plan(),plan(inches),plan(remove())]){
  const h=fixture(),before=structuredClone(args);assert.equal(validate(args),true,JSON.stringify(validate.errors));
  assert.deepEqual(await h.runtime.call(PLAN,args),{operation_id:'native-operation',state:'accepted'});
  assert.deepEqual(h.calls,[{name:'openpnp_get_capabilities',args:{},options:undefined},{name:PLAN,args:before,options:{mutating:true}}]);assert.deepEqual(args,before);
 }
});

test('new panel actions require all envelope, action, and pose fields with no inferred defaults',async()=>{
 const bad=[];
 for(const key of Object.keys(envelope)){const x=plan();delete x[key];bad.push(x);}
 for(const action of [clone(),remove()])for(const key of Object.keys(action)){const x=plan(action);delete x.changes[0][key];bad.push(x);}
 for(const key of Object.keys(clone().location)){const x=plan();delete x.changes[0].location[key];bad.push(x);}
 await rejectsBeforeRead(bad);
});

test('closed one-action panel array refuses mixes, repeats, optional fields and client-declared authority',async()=>{
 const bad=[{...envelope,changes:[]},{...envelope,changes:[clone(),remove()]},{...envelope,changes:[clone(),clone()]},{...envelope,changes:[clone(),...legacy().changes]},{...envelope,changes:[...legacy().changes,remove()]}];
 for(const field of ['clear_history','lineage_id','expected_lineage_revision','physical_clearance','unexecuted','source_file'])bad.push({...plan(),[field]:true});
 for(const field of ['holder_instance_id','placement_id','new_parent','clear_history','lineage_id','scope_override','material_load_id','assembly_height']){const x=plan();x.changes[0][field]='invented';bad.push(x);}
 for(const field of ['location','enabled','check_fiducials','side','source_child_id','new_child_id']){const x=plan(remove());x.changes[0][field]=clone()[field];bad.push(x);}
 for(const value of ['job_shared_definition','global_definition','physical_board']){const x=plan();x.changes[0].scope=value;bad.push(x);}
 const extraPose=plan();extraPose.changes[0].location.clearance=3;bad.push(extraPose);
 const unsupported=plan();unsupported.changes[0].action='clone_panel_subtree';bad.push(unsupported);
 await rejectsBeforeRead(bad);
});

test('native finite mm/in bounds and exact typed flags are enforced before discovery',async()=>{
 const bad=[];
 for(const units of ['mm','in']){
  const scale=units==='mm'?1:25.4;
  for(const [key,bound] of [['x',10000/scale],['y',10000/scale],['z',1000/scale],['rotation',360]]){
   for(const sign of [-1,1]){const x=plan();x.changes[0].location.units=units;x.changes[0].location[key]=sign*bound;assert.equal(validate(x),true,JSON.stringify(validate.errors));}
   for(const value of [bound+0.001,-bound-0.001,NaN,Infinity,-Infinity,null,'0',false]){const x=plan();x.changes[0].location.units=units;x.changes[0].location[key]=value;bad.push(x);}
  }
 }
 for(const [key,value]of [['side','bottom'],['side',null],['enabled',0],['enabled','false'],['check_fiducials',1],['check_fiducials',null]]){const x=plan();x.changes[0][key]=value;bad.push(x);}
 for(const [key,value]of [['frame','machine'],['units','mil']]){const x=plan();x.changes[0].location[key]=value;bad.push(x);}
 await rejectsBeforeRead(bad);
});

test('native selectors reject controls and malformed surrogates; new child IDs use bounded ASCII grammar',async()=>{
 const bad=[];
 for(const field of ['parent_instance_id','source_child_id'])for(const value of ['',true,128,null,'a\0b','a\u001fb','a\u007fb','a\u009fb','a\ud800b','a\udfffb']){const x=plan();x.changes[0][field]=value;bad.push(x);}
 for(const value of ['A⇒B','a'.repeat(129)]){const x=plan();x.changes[0].source_child_id=value;bad.push(x);}
 for(const value of ['','-A','A⇒B','α','a b','a'.repeat(129),'A\n']){const x=plan();x.changes[0].new_child_id=value;bad.push(x);}
 const longParent=plan();longParent.changes[0].parent_instance_id='p'.repeat(513);bad.push(longParent);
 const invalidRemove=plan(remove());invalidRemove.changes[0].child_id='A⇒B';bad.push(invalidRemove);
 await rejectsBeforeRead(bad);
});

test('Unicode selectors enforce native UTF-16 limits rather than JSON Schema code-point lengths',async()=>{
 const args=plan();args.changes[0].parent_instance_id='😀'.repeat(256);args.changes[0].source_child_id='😀'.repeat(64);args.changes[0].new_child_id='A'.repeat(128);
 const h=fixture();await h.runtime.call(PLAN,args);assert.equal(h.calls.length,2);assert.equal(h.calls[1].args.changes[0].parent_instance_id.length,512);
 const parentOverflow=structuredClone(args);parentOverflow.changes[0].parent_instance_id+='x';assert.equal(validate(parentOverflow),true,'JSON Schema counts code points; native runtime must tighten UTF-16.');
 const childOverflow=structuredClone(args);childOverflow.changes[0].source_child_id+='x';assert.equal(validate(childOverflow),true);
 const removal=plan(remove());removal.changes[0].child_id='😀'.repeat(64)+'x';
 await rejectsBeforeRead([parentOverflow,childOverflow,removal]);
});

test('advertised old structure tools alone cannot authorize panel dispatch',async()=>{
 const noProfile=caps();delete noProfile.panel_board_membership;
 for(const capabilities of [noProfile,{...caps(),panel_board_membership:null},caps('panel-board-membership-v2'),caps(true),{...caps(),panel_board_membership:{enabled:true}},{...caps(),tools:[APPLY]}]){
  for(const args of [plan(),plan(remove())]){const h=fixture(capabilities);await assert.rejects(h.runtime.call(PLAN,args),{code:'UNSUPPORTED_CAPABILITY'});assert.deepEqual(h.calls.map(x=>x.name),['openpnp_get_capabilities']);}
 }
});

test('legacy placement arrays and opaque apply retain forwarding without panel capability',async()=>{
 const capabilities=caps();delete capabilities.panel_board_membership;
 const args=legacy();args.changes=Array.from({length:100},(_,i)=>({...args.changes[0],placement_id:'R'+i}));
 for(const [method,input]of [[PLAN,args],[APPLY,{...envelope,plan_id:'reviewed-opaque-plan'}]]){
  const h=fixture(capabilities);await h.runtime.call(method,input);assert.deepEqual(h.calls[1],{name:method,args:input,options:{mutating:true}});assert.equal(h.calls.length,2);
 }
 const tooMany=legacy();tooMany.changes=Array.from({length:101},()=>legacy().changes[0]);await rejectsBeforeRead([tooMany]);
});

test('unknown panel mutation outcome preserves original request and never dispatches replay (real client, fixture transport)',async t=>{
 const dir=await mkdtemp(path.join(os.tmpdir(),'panel73-transport-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 const token=path.join(dir,'token'),connection=path.join(dir,'connection.json');await writeFile(token,'T'.repeat(48),{mode:0o600});await writeFile(connection,JSON.stringify({url:'http://127.0.0.1:54321/',tokenFile:token}),{mode:0o600});
 for(const failure of ['transport','malformed','known-unknown']){
  const calls=[];const client=new BridgeClient({connectionFile:connection,fetchImpl:async(_url,options)=>{const body=JSON.parse(options.body);calls.push(body);if(body.method==='openpnp_get_capabilities')return new Response(JSON.stringify({result:caps()}));if(failure==='transport')throw new Error('fixture lost response');return new Response(JSON.stringify({result:failure==='malformed'?{}:{operation_id:'same-operation',request_id:envelope.request_id,state:'outcome_unknown'}}));}});
  const runtime=new OpenPnpRuntime({client}),args=plan();
  if(failure==='known-unknown')assert.equal((await runtime.call(PLAN,args)).state,'outcome_unknown');
  else await assert.rejects(runtime.call(PLAN,args),e=>{assert.equal(e.code,'OUTCOME_UNKNOWN');assert.equal(e.details.request_id,envelope.request_id);return true;});
  assert.equal(calls.length,2);assert.equal(calls[0].method,'openpnp_get_capabilities');assert.equal(calls[1].method,PLAN);assert.deepEqual(calls[1].params,args);
 }
});

test('official MCP SDK exposes the additive closed branch and enforces runtime profile gating (no native machine)',async t=>{
 const capabilities=caps(),h=fixture(capabilities);const server=createServer(h.runtime),client=new Client({name:'panel73-contract-fixture',version:'1'});const[a,b]=InMemoryTransport.createLinkedPair();t.after(async()=>{await client.close();await server.close();});await Promise.all([server.connect(b),client.connect(a)]);
 const catalog=await client.listTools();assert.equal(catalog.tools.length,61);assert.equal(catalog.tools.filter(x=>!INSPECTION_TOOLS.includes(x.name)).length,59);
 for(const name of INSPECTION_TOOLS)assert.deepEqual(catalog.tools.find(x=>x.name===name).inputSchema,TOOL_BY_NAME.get(name).inputSchema);assert.deepEqual(catalog.tools.find(x=>x.name===PLAN).inputSchema,TOOL_BY_NAME.get(PLAN).inputSchema);
 let result=await client.callTool({name:PLAN,arguments:plan()});assert.notEqual(result.isError,true);assert.equal(result.structuredContent.operation_id,'native-operation');assert.equal(h.calls.length,2);
 let before=h.calls.length;result=await client.callTool({name:PLAN,arguments:{...plan(),changes:[clone(),remove()]}});assert.equal(result.structuredContent.error.code,'INVALID_ARGUMENT');assert.equal(h.calls.length,before);
 delete capabilities.panel_board_membership;before=h.calls.length;result=await client.callTool({name:PLAN,arguments:plan()});assert.equal(result.structuredContent.error.code,'UNSUPPORTED_CAPABILITY');assert.equal(h.calls.length,before+1);assert.equal(h.calls.at(-1).name,'openpnp_get_capabilities');
});
