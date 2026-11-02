import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';import {createHash} from 'node:crypto';
import {projectProgress} from '../../src/openpnp/node/progress.mjs';
const fixture=async n=>JSON.parse(await readFile(new URL(`./fixtures/material-loads/${n}.json`,import.meta.url),'utf8'));
test('retained native material fixtures are bound to exact source receipts',async()=>{const p=await fixture('provenance');for(const[name,row]of Object.entries(p.entries)){const b=await readFile(new URL(`./fixtures/material-loads/${name}.json`,import.meta.url));assert.equal(createHash('sha256').update(b).digest('hex'),row.sha256);assert.match(row.source_sha256,/^[a-f0-9]{64}$/);}});
for(const name of ['completed','unknown','recovered'])test(`native ${name} material progress preserves revision, authority, counts and observation time without full detail`,async()=>{
 const material=await fixture(name);const input={bridge_instance_id:'bridge',material_setup_revision:material.material_setup_revision,material_loads:material};const p=projectProgress('openpnp_get_status',input);const actual=p.material_loads;
 for(const key of ['material_setup_revision','observed_at','available_trays_observed_at','physical_inventory_verified','native_authority_restored','recovered_from_journal'])assert.equal(actual[key],material[key]);
 for(const key of ['loads','pending_changes','pending_feeds','available_trays']){assert.equal(actual[`${key}_count`],material[key].length);assert.ok(!(key in actual));}
 assert.equal(p.material_setup_revision,material.material_setup_revision);assert.equal(p.response_view.retained_snapshot,false);assert.equal(p.response_view.full_read.arguments.view,'full');assert.ok(Buffer.byteLength(JSON.stringify(p))<32768);
 if(name!=='completed'){assert.equal(actual.pending_feeds_count,1);assert.equal(actual.native_authority_restored,false);}else assert.equal(actual.loads_count,2);
});
