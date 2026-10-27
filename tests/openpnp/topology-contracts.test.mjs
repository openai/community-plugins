import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { NATIVE_CHANGE_SCHEMAS } from '../../src/openpnp/node/settings-contracts.mjs';
const schema=NATIVE_CHANGE_SCHEMAS.find(s=>s.properties.type.const==='create_simulator_nozzle_assembly');
const validate=new Ajv({strict:true,coerceTypes:false}).compile(schema);
const good={type:'create_simulator_nozzle_assembly',head_id:'H1',driver_id:'D1',x_axis_id:'X',y_axis_id:'Y',nozzle_name:'new nozzle',tip_name:'new tip',valve_name:'new valve',head_offsets:{x_mm:20,y_mm:0,z_mm:0},z_axis:{home_mm:0,low_mm:-100,high_mm:10,safe_z_mm:0,feedrate_mm_per_s:100,acceleration_mm_per_s2:500,jerk_mm_per_s3:1000},rotation_axis:{home_deg:0,low_deg:-360,high_deg:360,feedrate_deg_per_s:500,acceleration_deg_per_s2:1000,jerk_deg_per_s3:10000},tip:{min_part_diameter_mm:0,max_part_diameter_mm:20,max_part_height_mm:10,max_pick_tolerance_mm:0.5,pick_dwell_ms:0,place_dwell_ms:0},pick_dwell_ms:0,place_dwell_ms:0,exclusive_package_ids:['R0805'],simulated_initial_tool_state:'installed-on-new-nozzle'};
test('assembly schema names the complete reference graph and explicit angular/linear units',()=>{assert.equal(validate(good),true);assert.ok(Object.hasOwn(schema.properties.rotation_axis.properties,'home_deg'));assert.equal(Object.hasOwn(schema.properties.rotation_axis.properties,'home_mm'),false);});
test('closed assembly schema refuses omitted dependencies, native classes, implicit physical state, coercion and extra nested fields',()=>{
 for(const key of Object.keys(good)){const value=structuredClone(good);delete value[key];assert.equal(validate(value),false,key);}
 for(const key of ['head_offsets','z_axis','rotation_axis','tip']){
  for(const field of Object.keys(good[key])){const value=structuredClone(good);delete value[key][field];assert.equal(validate(value),false,`${key}.${field}`);}
  for(const extra of ['class','driver_id','gcode','units']){const value=structuredClone(good);value[key][extra]='untyped';assert.equal(validate(value),false,`${key}.${extra}`);}
 }
 for(const patch of [{nozzle_id:'invented'},{type:'create_arbitrary_device'},{exclusive_package_ids:[]},{exclusive_package_ids:['R0805','R0805']},{simulated_initial_tool_state:'physically-installed'},{nozzle_name:'a'.repeat(65)},{driver_id:'../driver'},{pick_dwell_ms:0.5}])assert.equal(validate({...good,...patch}),false,JSON.stringify(patch));
 for(const number of ['10',NaN,Infinity,null,true]){const value=structuredClone(good);value.rotation_axis.high_deg=number;assert.equal(validate(value),false,String(number));}
});
