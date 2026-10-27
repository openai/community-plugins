import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { NATIVE_CHANGE_SCHEMAS } from '../../src/openpnp/node/settings-contracts.mjs';
const schema=NATIVE_CHANGE_SCHEMAS.find(s=>s.properties.type.const==='set_axis_backlash_settings');
const validate=new Ajv({strict:true,coerceTypes:false}).compile(schema);
const good={type:'set_axis_backlash_settings',axis_id:'axis:X',method:'DirectionalSneakUp',offset_mm:-0.4,speed_factor:0.2,sneak_up_mm:0.8,acceptable_tolerance_mm:0.025};
test('closed backlash schema accepts five pinned native methods and explicit bounded units',()=>{for(const method of schema.properties.method.enum)assert.equal(validate({...good,method}),true);assert.equal(validate({...good,offset_mm:10,sneak_up_mm:10,speed_factor:0.001,acceptable_tolerance_mm:0.000001}),true);});
test('missing/coerced/nonfinite/out-of-range/enum/arbitrary fields are rejected',()=>{for(const key of Object.keys(good)){const c={...good};delete c[key];assert.equal(validate(c),false,key);}for(const key of ['offset_mm','speed_factor','sneak_up_mm','acceptable_tolerance_mm'])for(const value of ['0.1',null,true,NaN,Infinity])assert.equal(validate({...good,[key]:value}),false,`${key} ${value}`);for(const bad of [{offset_mm:10.01},{offset_mm:-10.01},{speed_factor:0},{speed_factor:1.1},{sneak_up_mm:-1},{sneak_up_mm:10.01},{acceptable_tolerance_mm:0},{method:'directional-compensation'},{gcode:'G0 X10'},{axis_id:'../X'},{units:'mm'},{driver_id:'new'}])assert.equal(validate({...good,...bad}),false,JSON.stringify(bad));});
