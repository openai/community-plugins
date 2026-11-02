// Offline timing/option policy; no native or endurance qualification.
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseOptions, eventPollDue } from '../../scripts/openpnp-soak.mjs';
const args=['--connection-file','/tmp/owned/connection.json','--evidence-dir','/tmp/owned/new-evidence'];
test('legacy event polling stays sixty seconds; new recipe can select ten minutes',()=>{
 assert.equal(parseOptions(args).eventPollSeconds,undefined);
 assert.equal(eventPollDue(59999,0),false);assert.equal(eventPollDue(60000,0),true);
 assert.equal(parseOptions([...args,'--event-poll-seconds','600']).eventPollSeconds,600);
 assert.equal(eventPollDue(599999,0,600),false);assert.equal(eventPollDue(600000,0,600),true);
});
test('event cadence rejects malformed, repeated or out-of-bounds input before execution',()=>{
 for(const value of ['0','59','601','60.1','Infinity','NaN'])assert.throws(()=>parseOptions([...args,'--event-poll-seconds',value]),{code:'SOAK_PRECONDITION'});
 assert.throws(()=>parseOptions([...args,'--event-poll-seconds','600','--event-poll-seconds','600']));
});
test('forced post-job event observation stays immediate, then resets periodic timing',()=>{
 assert.equal(eventPollDue(12345,0,600,true),true);
 assert.equal(eventPollDue(612344,12345,600),false);
 assert.equal(eventPollDue(612345,12345,600),true);
});
test('eight-hour ten-minute recipe plus one post-job force stays within declared fifty event opportunities',()=>{
 for(const jobFinishedSeconds of [1,1800,2700,7199,28000]){
  let last=0,calls=0,forced=false;
  for(let now=0;now<=8*3600+60;now++){
   const force=!forced&&now>=jobFinishedSeconds;
   if(eventPollDue(now*1000,last,600,force)){calls++;last=now*1000;if(force)forced=true;}
  }
  assert.ok(calls<=50,`job finish ${jobFinishedSeconds}: ${calls}`);
 }
});
