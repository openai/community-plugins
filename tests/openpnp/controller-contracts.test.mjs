import { RECOVERY_TOOLS, normalizeMaterialDescription } from './fixtures/sensing79-retained-contracts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { TOOL_DEFINITIONS,TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
const INSPECTION_TOOLS=['openpnp_request_board_inspection','openpnp_get_board_inspection'];
const SENSING_TOOLS=['openpnp_measure_sensor','openpnp_verify_part_state'];
function beforeSensing(){const definitions=structuredClone(TOOL_DEFINITIONS.filter(x=>![...SENSING_TOOLS,...RECOVERY_TOOLS].includes(x.name))).map(normalizeMaterialDescription);const variants=definitions.find(x=>x.name==='openpnp_plan_configuration').inputSchema.properties.changes.items.oneOf;const added=variants.filter(x=>x.properties.type.const==='set_vacuum_sensing_settings');assert.equal(added.length,1);variants.splice(variants.indexOf(added[0]),1);return definitions;}
const BASE=JSON.parse(readFileSync(new URL('./fixtures/controller-diagnostic/baseline52-definitions.json',import.meta.url),'utf8'));
import { OpenPnpRuntime,PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { CONTROLLER_PROFILE,CONTROLLER_PROTOCOL,CONTROLLER_DRIVER,CONTROLLER_TOOLS,CONTROLLER_TOOL,requireControllerProfile } from '../../plugins/openpnp/scripts/controller-profile.mjs';
const uuid='a134762f-2263-491d-a22b-c3cfb7f12145';
export const capabilities=()=>({schema_version:1,bridge_version:'0.1.0',upstream_commit:PINNED_UPSTREAM,bridge_artifact_sha256:'a'.repeat(64),bridge_instance_id:uuid,machine_id:'fixture-machine',simulation:true,simulator_profile:CONTROLLER_PROFILE,native_driver:CONTROLLER_DRIVER,controller_protocol:CONTROLLER_PROTOCOL,hardware_qualified:false,physical_standstill_verified:false,motion_completion_observed:false,configuration_changes:[],tools:[...CONTROLLER_TOOLS],fixed_recipe:['bind','connect:G21,G90','identify:M115','close'],fresh_launch_required:true,controller_diagnostic:{controller_instance_id:uuid,profile:CONTROLLER_PROFILE,generation_spent:false,physical_qualification:false}});
const envelope=()=>({session_id:'bound-session',request_id:uuid,expected_config_revision:'cfg-1',controller_instance_id:uuid});
test('inspection75 retains panel74 definitions except the explicitly checked material descriptions and separately closes the two new tools',()=>{
 const retained=beforeSensing().filter(x=>!INSPECTION_TOOLS.includes(x.name));
 assert.equal(retained.length,55);
 // Frozen publish74 source definitions, including descriptions, annotations and routing flags.
 assert.equal(createHash('sha256').update(JSON.stringify(retained)).digest('hex'),'2a28895422f26bcc6fb5062f5f665e53bb4ea3d89dff3c395f3e4c23fe731f88');
 assert.deepEqual(TOOL_DEFINITIONS.filter(x=>INSPECTION_TOOLS.includes(x.name)).map(x=>x.name),INSPECTION_TOOLS);
 const request=TOOL_BY_NAME.get(INSPECTION_TOOLS[0]),read=TOOL_BY_NAME.get(INSPECTION_TOOLS[1]);
 const expected=['request_id','session_id','expected_config_revision','job_id','expected_job_revision','expected_board_load_revision','loaded_board_id'];
 assert.deepEqual(request.inputSchema.required,expected);assert.deepEqual(Object.keys(request.inputSchema.properties),expected);
 assert.deepEqual(read.inputSchema.required,['task_id']);assert.deepEqual(Object.keys(read.inputSchema.properties),['task_id']);
 for(const tool of [request,read]){assert.equal(tool.inputSchema.additionalProperties,false);assert.equal(tool.offline,false);assert.equal(tool.annotations.openWorldHint,false);}
 assert.equal(request.mutating,true);assert.equal(request.annotations.readOnlyHint,false);assert.equal(request.annotations.idempotentHint,false);
 assert.equal(read.mutating,false);assert.equal(read.annotations.readOnlyHint,true);assert.equal(read.annotations.idempotentHint,true);
 const ajv=new Ajv({strict:true,allErrors:true,coerceTypes:false,useDefaults:false}),requestValid=ajv.compile(request.inputSchema),readValid=ajv.compile(read.inputSchema);
 const valid={request_id:uuid,session_id:'lease',expected_config_revision:'cfg-3',job_id:'job',expected_job_revision:'a'.repeat(64),expected_board_load_revision:'load-2',loaded_board_id:uuid};
 assert.equal(requestValid(valid),true);assert.equal(readValid({task_id:uuid}),true);
 for(const key of expected){const missing={...valid};delete missing[key];assert.equal(requestValid(missing),false,key);}
 for(const value of [{...valid,observations:[]},{...valid,request_id:uuid.toUpperCase()},{...valid,expected_config_revision:'load-2'},{...valid,expected_job_revision:'cfg-3'},{...valid,loaded_board_id:''}])assert.equal(requestValid(value),false);
 for(const value of [{},{task_id:uuid,submit:true},{task_id:uuid.toUpperCase()},{task_id:'1-1-1-1-1'}])assert.equal(readValid(value),false);
});
test('current catalog preserves baseline52 except the explicitly tested feature additions',()=>{
 assert.equal(TOOL_DEFINITIONS.length,61);
 assert.equal(TOOL_DEFINITIONS.filter(x=>!INSPECTION_TOOLS.includes(x.name)).length,59);
 assert.equal(new Set(TOOL_BY_NAME.get('openpnp_plan_configuration').inputSchema.properties.changes.items.oneOf.map(x=>x.properties.type.const)).size,34);
 const retained=beforeSensing().filter(x=>![CONTROLLER_TOOL,'openpnp_get_material_loads','openpnp_register_material_load',...INSPECTION_TOOLS].includes(x.name));
 const variants=retained.find(x=>x.name==='openpnp_plan_configuration').inputSchema.properties.changes.items.oneOf;
 assert.equal(new Set(variants.map(x=>x.properties.type.const)).size,33);
 assert.equal(variants.length,BASE.find(x=>x.name==='openpnp_plan_configuration').inputSchema.properties.changes.items.oneOf.length+2);
 const added=variants.filter(x=>x.properties.type.const==='set_axis_backlash_settings');assert.equal(added.length,1);
 assert.deepEqual(added[0].required,['type','axis_id','method','offset_mm','speed_factor','sneak_up_mm','acceptable_tolerance_mm']);
 assert.equal(added[0].additionalProperties,false);
 variants.splice(variants.indexOf(added[0]),1);
 const assembly=variants.filter(x=>x.properties.type.const==='create_simulator_nozzle_assembly');assert.equal(assembly.length,1);assert.equal(assembly[0].additionalProperties,false);
 assert.deepEqual(assembly[0].required,['type','head_id','driver_id','x_axis_id','y_axis_id','nozzle_name','tip_name','valve_name','head_offsets','z_axis','rotation_axis','tip','pick_dwell_ms','place_dwell_ms','exclusive_package_ids','simulated_initial_tool_state']);
 variants.splice(variants.indexOf(assembly[0]),1);
 const portable=retained.find(x=>x.name==='openpnp_export_portable_configuration');
 assert.equal(portable.description,'Export the disabled, idle supported native simulator configuration as a portable ZIP artifact for fresh-instance adoption. Preserves seven native XML documents and inventoried images; scripts are quarantined. Supports advertised saved flat-board and saved-board-child-panel-library-v1 profiles. The panel profile uses archive version 3 for clean saved panels with direct board children, exact board references and supported fiducials; inspect its advertised bounds. Refuses dirty or changed definitions, nested panels, custom outlines and unsupported pad shapes. Transfers no operational history, physical authority or calibration qualification; source feeder counters remain unqualified simulator data.');
 portable.description=BASE.find(x=>x.name===portable.name).description;
 const calibration=retained.find(x=>x.name==='openpnp_run_calibration');
 const previous=BASE.find(x=>x.name===calibration.name);
 assert.equal(calibration.inputSchema.additionalProperties,false);
 assert.equal(calibration.inputSchema.oneOf.length,2);
 const [runout,camera]=calibration.inputSchema.oneOf;
 assert.equal(runout.properties.recipe_id.const,'nozzle-tip-runout');
 assert.equal(camera.properties.recipe_id.const,'camera-planar-scale');
 assert.deepEqual(Object.keys(calibration.inputSchema.properties).sort(),[...new Set([...Object.keys(runout.properties),...Object.keys(camera.properties)])].sort());
 assert.deepEqual(calibration.inputSchema.properties.recipe_id.enum,['nozzle-tip-runout','camera-planar-scale']);
 const historicalRunout=structuredClone(runout);
 assert.deepEqual(historicalRunout,previous.inputSchema,'Every pre-existing nozzle command constraint is retained');
 // The new recipe has its separate strict positive/negative schema tests.
 calibration.inputSchema=historicalRunout;calibration.description=previous.description;
 const prepare=retained.find(x=>x.name==='openpnp_prepare_job');
 const oldPrepare=BASE.find(x=>x.name===prepare.name);
 assert.deepEqual(Object.keys(prepare.inputSchema.properties).sort(),[...Object.keys(oldPrepare.inputSchema.properties),'part_bindings'].sort());
 assert.equal(prepare.inputSchema.properties.part_bindings.minItems,1);
 assert.equal(prepare.inputSchema.properties.part_bindings.maxItems,1000);
 assert.equal(prepare.inputSchema.properties.part_bindings.items.additionalProperties,false);
 assert.deepEqual(prepare.inputSchema.allOf[0].if.required,['part_bindings']);
 assert.deepEqual(prepare.inputSchema.allOf[0].then.required,['artifact_id','expected_config_revision']);
 // The new conditional branch has strict admission and forwarding tests.
 delete prepare.inputSchema.properties.part_bindings;delete prepare.inputSchema.allOf;
 assert.deepEqual(prepare.inputSchema,oldPrepare.inputSchema,'Legacy prepare schema stays byte-equivalent after removing the explicit extension');
 prepare.description=oldPrepare.description;
 const structure=retained.find(x=>x.name==='openpnp_plan_placement_structure');
 const oldStructure=BASE.find(x=>x.name===structure.name);
 const structureArrays=structure.inputSchema.properties.changes.oneOf;
 assert.equal(structureArrays.length,2);
 assert.deepEqual(structureArrays[0],oldStructure.inputSchema.properties.changes,'Every old placement action and array limit is retained');
 const panelArray=structureArrays[1];
 assert.equal(panelArray.type,'array');assert.equal(panelArray.minItems,1);assert.equal(panelArray.maxItems,1);
 assert.deepEqual(panelArray.items.oneOf.map(x=>x.properties.action.const),['clone_board_child','remove_board_child']);
 for(const branch of panelArray.items.oneOf){assert.equal(branch.additionalProperties,false);assert.equal(branch.properties.scope.const,'job_instance');assert.equal(branch.properties.parent_instance_id.maxLength,512);}
 assert.deepEqual(panelArray.items.oneOf[0].required,['action','scope','parent_instance_id','source_child_id','new_child_id','location','side','enabled','check_fiducials']);
 assert.deepEqual(panelArray.items.oneOf[1].required,['action','scope','parent_instance_id','child_id']);
 assert.match(structure.description,/panel-board-membership-v1/);
 // New panel selectors, profile gating, closed fields and forwarding have focused tests.
 structure.inputSchema.properties.changes=structureArrays[0];structure.description=oldStructure.description;
 assert.deepEqual(retained,BASE);

 const tool=TOOL_BY_NAME.get(CONTROLLER_TOOL);assert.equal(tool.mutating,true);assert.equal(tool.annotations.idempotentHint,false);
 const validate=new Ajv({strict:true,coerceTypes:false}).compile(tool.inputSchema);assert.equal(validate(envelope()),true);
 for(const k of Object.keys(envelope())){const a=envelope();delete a[k];assert.equal(validate(a),false,k);}
 for(const a of [{...envelope(),request_id:'not-uuid'},{...envelope(),controller_instance_id:'not-uuid'},{...envelope(),expected_config_revision:1},...['host','port','command','profile','serial','retry'].map(k=>({...envelope(),[k]:'M115'}))])assert.equal(validate(a),false);
});
test('profile declaration accepts exact owned driver and refuses broadened authority',()=>{
 assert.equal(requireControllerProfile(capabilities(),{fresh:true,expectedBridgeSha256:'a'.repeat(64)}),true);
 const variants=[{native_driver:'org.openpnp.machine.reference.driver.NullDriver'},{controller_protocol:'gcode'},{hardware_qualified:true},{simulation:false},{physical_standstill_verified:true},{motion_completion_observed:true},{configuration_changes:['set_machine_speed']},{tools:[...CONTROLLER_TOOLS,'openpnp_home_machine']},{bridge_artifact_sha256:'b'.repeat(64)}];
 for(const change of variants)assert.throws(()=>requireControllerProfile({...capabilities(),...change},{fresh:true,expectedBridgeSha256:'a'.repeat(64)}),{code:'INCOMPATIBLE_BRIDGE'});
 const spent=capabilities();spent.controller_diagnostic.generation_spent=true;assert.equal(requireControllerProfile(spent),true);assert.throws(()=>requireControllerProfile(spent,{fresh:true}));
});
test('MCP dispatches exactly one mutation after discovery and preserves returned operation',async()=>{
 const calls=[];const result={operation_id:uuid,request_id:uuid,state:'accepted'};
 const runtime=new OpenPnpRuntime({client:{call:async(...a)=>{calls.push(a);return a[0]==='openpnp_get_capabilities'?capabilities():result;}},artifacts:{},responses:{}});
 assert.equal(await runtime.call(CONTROLLER_TOOL,envelope()),result);assert.equal(calls.length,2);assert.equal(calls[1][0],CONTROLLER_TOOL);assert.deepEqual(calls[1][1],envelope());assert.equal(calls[1][2].mutating,true);
 calls.length=0;await assert.rejects(runtime.call(CONTROLLER_TOOL,{...envelope(),controller_instance_id:'b134762f-2263-491d-a22b-c3cfb7f12145'}),{code:'CONTROLLER_INSTANCE_MISMATCH'});assert.equal(calls.length,1);
 calls.length=0;await assert.rejects(runtime.call('openpnp_home_machine',envelope()),{code:'INVALID_ARGUMENT'});assert.equal(calls.length,0);
 const a=envelope();delete a.controller_instance_id;await assert.rejects(runtime.call('openpnp_home_machine',a),{code:'UNSUPPORTED_CAPABILITY'});assert.equal(calls.length,1);
});
test('default simulator remains unchanged and does not advertise diagnostic',async()=>{
 const c={schema_version:1,bridge_version:'0.1.0',upstream_commit:PINNED_UPSTREAM,simulator_profile:'native-simulator',tools:['openpnp_get_capabilities','openpnp_home_machine']};let mutations=0;
 const runtime=new OpenPnpRuntime({client:{call:async name=>{if(name==='openpnp_get_capabilities')return c;mutations++;}},artifacts:{},responses:{}});
 const found=await runtime.call('openpnp_get_capabilities');assert.equal(found.connected,true);assert.equal(found.tools.includes(CONTROLLER_TOOL),false);await assert.rejects(runtime.call(CONTROLLER_TOOL,envelope()),{code:'UNSUPPORTED_CAPABILITY'});assert.equal(mutations,0);
});
test('controller progress retains original bindings, unknown and pending publication without claiming native freshness',async()=>{
 const {projectProgress}=await import('../../src/openpnp/node/progress.mjs');
 const status=projectProgress('openpnp_get_status',{controller_diagnostic:{controller_instance_id:uuid,profile:CONTROLLER_PROFILE,generation_spent:true,observation:{phase:'after-identify',observed_at:'2026-09-11T00:00:00Z',observation_sequence:6,physical_qualification:false},uncommitted_observation:{intent:{huge:'x'.repeat(100000)}}},controller_history:{binding:{operation_id:uuid,request_id:uuid,controller_instance_id:uuid},pending:{operation_id:uuid,step:'identify',step_index:3},steps:[{},{}],native_authority_restored:false}});
 assert.equal(status.controller_diagnostic.controller_instance_id,uuid);assert.equal(status.controller_diagnostic.uncommitted_observation_present,true);assert.equal(status.controller_history.pending.step,'identify');assert.equal(status.controller_history.native_authority_restored,false);assert.equal(status.response_view.full_details_included,false);
 const request=projectProgress('openpnp_get_request_status',{found:true,uncommitted_admission:{operation_id:uuid,request_id:uuid,controller_instance_id:uuid,durable_admission_confirmed:false,native_dispatch_performed:false}},{request_id:uuid});assert.equal(request.uncommitted_admission.operation_id,uuid);assert.equal(request.uncommitted_admission.durable_admission_confirmed,false);
 const operation=projectProgress('openpnp_get_operation',{operation_id:uuid,controller_instance_id:uuid,state:'outcome_unknown',result:{controller_instance_id:uuid,completed_recipe:false,steps_attempted:3,outcome_unknown:true,physical_standstill_verified:false}});assert.equal(operation.result.outcome_unknown,true);assert.equal(operation.result.physical_standstill_verified,false);
});
test('uncertain diagnostic transport response is not replayed by runtime',async()=>{
 let effects=0;const runtime=new OpenPnpRuntime({client:{call:async name=>{if(name==='openpnp_get_capabilities')return capabilities();effects++;throw Object.assign(new Error('fixture response lost'),{code:'OUTCOME_UNKNOWN'});}},artifacts:{},responses:{}});
 await assert.rejects(runtime.call(CONTROLLER_TOOL,envelope()),{code:'OUTCOME_UNKNOWN'});assert.equal(effects,1);
 const validate=new Ajv({strict:true}).compile(TOOL_BY_NAME.get(CONTROLLER_TOOL).inputSchema);assert.equal(validate({...envelope(),request_id:uuid.toUpperCase()}),false);
});
