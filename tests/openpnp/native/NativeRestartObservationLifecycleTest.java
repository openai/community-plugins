/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.openpnp.codex.NativeReplacementContinuationTest.*;

/** Isolated coordinator lifecycle: real native executor Future and forced terminal records.
 * Initial sensor facts, operator decisions and exact root observation authority are explicit
 * synthetic fixtures. Combined production root authority is a separate Bridge qualification. */
public final class NativeRestartObservationLifecycleTest {
    static int assertions,refusals,nativeWrappers;static final List<Map<String,Object>> groups=new ArrayList<>();
    static final String EVENT="sensing_reconciliation_restart_observations_completed";
    interface Checked{void run()throws Exception;}
    static void check(boolean value,String why){assertions++;if(!value)throw new AssertionError(why);}
    static void reject(String why,Checked body)throws Exception{try{body.run();throw new AssertionError("Accepted: "+why);}catch(IOException expected){refusals++;check(true,why);}}
    static void same(Object expected,Object actual,String why)throws Exception{check(NativeFaultedJobReplacement.same(expected,actual),why);}
    static Map<String,Object> copy(Map<String,Object> row)throws IOException{return NativeSensingReconciliationTest.copy(row);}
    static final class Harness implements AutoCloseable {
        final NativeVacuumJournal vacuum=new NativeVacuumJournal();final NativeSensingReconciliation coordinator;
        final String taskId=id(),operation=id(),instance=id();final Object owner=new Object();final Path file;final FileChannel channel;
        final List<Map<String,Object>> events=new ArrayList<>();final AtomicBoolean rootAllowed=new AtomicBoolean(true);int rootCalls,liveCompletions,replayCompletions;long sequence;
        final CountDownLatch bodyReturned=new CountDownLatch(1),wrapperRelease=new CountDownLatch(1);volatile Future<Boolean> wrapper;
        volatile Map<String,Object> terminal;NativeSensingReconciliation.Capture faultCapture;NativeSensingReconciliation.Permit permit;Map<String,Object> scope,taskRecord,facts;
        Harness(String name,NativeFaultedJobReplacement.RestartCapture restart,boolean rootAuthority)throws Exception{
            file=root.resolve(name+".jsonl");channel=FileChannel.open(file,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);
            NativeSensingReconciliation.CompletionAuthority completion=(op,current,replay)->{
                if(replay)replayCompletions++;else liveCompletions++;Map<String,Object> forced=terminal;
                if(forced==null||!operation.equals(op)||!instance.equals(current)||!Objects.equals(forced.get("operation_id"),op)||!"succeeded".equals(forced.get("state")))return false;
                if(replay)return Boolean.TRUE.equals(forced.get("actual_wrapper_succeeded"));
                if(wrapper==null||!wrapper.isDone()||wrapper.isCancelled())return false;try{return Boolean.TRUE.equals(wrapper.get());}catch(InterruptedException e){Thread.currentThread().interrupt();return false;}catch(ExecutionException|CancellationException e){return false;}
            };
            NativeSensingReconciliation.DispositionAuthority forbidden=(capture,dispositions,replay)->{throw new AssertionError("Observation phase must never request sensing/material disposition");};
            NativeSensingReconciliation.RestartObservationAuthority observed=(capture,context,observations,replay)->{rootCalls++;return rootAllowed.get()&&NativeFaultedJobReplacement.same(capture,faultCapture.payload)&&NativeFaultedJobReplacement.same(context,taskRecord.get("restart_context"))&&NativeFaultedJobReplacement.same(observations,facts);};
            coordinator=rootAuthority?new NativeSensingReconciliation(vacuum,completion,forbidden,observed):new NativeSensingReconciliation(vacuum,completion,forbidden);
            Map<String,Object> oldContext=NativeSensingReconciliationTest.context();oldContext.put("scope","job");oldContext.put("job_context",restart.jobContext());oldContext.put("operation_id",((List<?>)restart.dependencies().get("operation_ids")).get(0));
            append("vacuum_observation_intent",NativeSensingReconciliationTest.event("read.before",1500,null,oldContext,NativeSensingReconciliationTest.SOURCE),NativeSensingReconciliationTest.INSTANCE);
            scope=copy(NativeSensingReconciliationTest.scope(instance,NativeSensingReconciliationTest.SOURCE));scope.put("config_revision","cfg-3");o(((List<?>)scope.get("nozzle_bindings")).get(0)).put("config_revision","cfg-3");scope.put("job_context",restart.jobContext());scope.put("dependencies",restart.dependencies());
            faultCapture=vacuum.captureFaultSet(scope);taskRecord=coordinator.taskRecordForRestart(taskId,id(),id(),faultCapture,restart,8,"2030-01-01T00:00:00Z");append("sensing_reconciliation_task",taskRecord,instance);append("sensing_reconciliation_intent",coordinator.intentRecord(taskId,operation,id(),"2026-09-14T00:00:00Z",8,"explicit-synthetic-local-decision"),instance);permit=coordinator.issuePermit(taskId,operation,faultCapture.digest,owner);
            List<Map<String,Object>> rows=new ArrayList<>();for(String type:List.of("sensing_source_restart_bootstrap_returned","board_restart_observation","material_restart_observation","material_restart_observation"))rows.add(m("type",type,"receipt_id",id(),"payload_sha256","a".repeat(64)));rows.sort(Comparator.comparing(r->(String)r.get("receipt_id")));
            facts=NativeSensingReconciliation.freeze(m("reattachment_id",id(),"replacement_attempt_id",restart.payload.get("replacement_attempt_id"),"restart_capture_sha256",restart.digest,"restart_attachment_sha256","b".repeat(64),"prior_process_disposition_id",id(),"prior_process_disposition_sha256","c".repeat(64),"observations",rows));
        }
        void append(String type,Map<String,Object> p,String current)throws Exception{
            Runnable commit=NativeSensingReconciliation.matches(type)?coordinator.prepare(type,p,current):vacuum.prepare(type,p,current);
            Map<String,Object> row=NativeSensingReconciliation.freeze(m("sequence",++sequence,"type",type,"payload",p,"bridge_instance_id",current));force(row);commit.run();events.add(row);
        }
        void force(Map<String,Object> row)throws Exception{ByteBuffer bytes=ByteBuffer.wrap((JSON.toJson(row)+"\n").getBytes(StandardCharsets.UTF_8));while(bytes.hasRemaining())channel.write(bytes);channel.force(true);}
        Map<String,Object> receipt()throws IOException{return coordinator.restartObservationsCompletedRecord(taskId,id(),facts);}
        void launch(boolean fail)throws Exception{
            wrapper=config.getMachine().submit(()->{if(!config.getMachine().isTask(Thread.currentThread()))throw new AssertionError("Expected actual native executor");counters();bodyReturned.countDown();if(!wrapperRelease.await(10,TimeUnit.SECONDS))throw new IOException("Native wrapper release timed out");if(fail)throw new IOException("Injected actual native wrapper failure after returned body");return true;},null,true);nativeWrappers++;check(bodyReturned.await(10,TimeUnit.SECONDS),"Actual native body reached controlled wrapper boundary");
        }
        void finishWrapper()throws Exception{wrapperRelease.countDown();try{wrapper.get(10,TimeUnit.SECONDS);}catch(ExecutionException|CancellationException expected){}}
        void forceTerminal(boolean claimSuccess)throws Exception{Map<String,Object> row=m("operation_id",operation,"bridge_instance_id",instance,"state",claimSuccess?"succeeded":"outcome_unknown","actual_wrapper_succeeded",claimSuccess&&wrapper!=null&&wrapper.isDone()&&!wrapper.isCancelled()&&successful());force(m("type","test_forced_local_terminal","payload",row));terminal=NativeSensingReconciliation.freeze(row);}
        boolean successful(){try{return wrapper!=null&&wrapper.isDone()&&!wrapper.isCancelled()&&Boolean.TRUE.equals(wrapper.get());}catch(Exception e){return false;}}
        void blocked()throws Exception{try{vacuum.requireNoFault();throw new AssertionError("Historical sensor fault was cleared");}catch(NativeVacuumJournal.Fault expected){check(true,"All sensor faults remain blocking");}}
        public void close()throws Exception{wrapperRelease.countDown();if(wrapper!=null&&!wrapper.isCancelled())try{wrapper.get(10,TimeUnit.SECONDS);}catch(ExecutionException expected){}coordinator.revokeAll();channel.close();}
    }
    static void schema(Harness h)throws Exception{
        Map<String,Object> record=h.receipt();List<Map<String,Object>> invalid=new ArrayList<>();
        for(String key:List.of("faults_resolved","execution_authority_restored","history_rewritten","old_operation_replayed","old_operation_outcome_changed","physical_occupancy_verified","hardware_qualified")){Map<String,Object> bad=copy(record);bad.put(key,true);invalid.add(bad);}
        for(String key:List.of("native_wrapper_completed","native_wrapper_succeeded","simulation_only")){Map<String,Object> bad=copy(record);bad.put(key,false);invalid.add(bad);}
        for(String key:List.of("request_id","request_operation_id","recovery_operation_id","fault_set_sha256")){Map<String,Object> bad=copy(record);bad.put(key,key.endsWith("sha256")?"e".repeat(64):id());invalid.add(bad);}
        Map<String,Object> bad=copy(record);bad.put("extra",true);invalid.add(bad);bad=copy(record);bad.remove("observations_sha256");invalid.add(bad);bad=copy(record);bad.put("observations_sha256","e".repeat(64));invalid.add(bad);
        for(String key:List.of("reattachment_id","replacement_attempt_id","restart_capture_sha256","restart_attachment_sha256","prior_process_disposition_id","prior_process_disposition_sha256")){bad=copy(record);o(bad.get("observations")).put(key,"malformed");bad.put("observations_sha256",NativeSensingReconciliation.digest(o(bad.get("observations"))));invalid.add(bad);}
        for(String mutation:List.of("omit-source","omit-board","omit-material","duplicate","unsorted","bad-type","extra-descriptor","extra-facts")){bad=copy(record);Map<String,Object> facts=o(bad.get("observations"));List<Object> rows=new ArrayList<>((List<?>)facts.get("observations"));
            if(mutation.startsWith("omit-")){String type=mutation.equals("omit-source")?"sensing_source_restart_bootstrap_returned":mutation.equals("omit-board")?"board_restart_observation":"material_restart_observation";rows.removeIf(raw->type.equals(((Map<?,?>)raw).get("type")));}
            else if(mutation.equals("duplicate"))rows.add(rows.get(0));else if(mutation.equals("unsorted"))Collections.reverse(rows);else if(mutation.equals("bad-type"))o(rows.get(0)).put("type","sensing_source_restart_bootstrap_intent");else if(mutation.equals("extra-descriptor"))o(rows.get(0)).put("extra",true);else facts.put("extra",true);
            facts.put("observations",rows);bad.put("observations_sha256",NativeSensingReconciliation.digest(facts));invalid.add(bad);
        }
        int before=h.rootCalls;long bytes=Files.size(h.file);for(Map<String,Object> item:invalid)reject("closed lifecycle schema",()->h.append(EVENT,item,h.instance));check(before==h.rootCalls,"Malformed schemas never reach independent root authority");check(bytes==Files.size(h.file),"Malformed lifecycle receipts append no bytes");
        bad=copy(record);List<?> rows=(List<?>)o(bad.get("observations")).get("observations");o(rows.get(0)).put("payload_sha256","e".repeat(64));bad.put("observations_sha256",NativeSensingReconciliation.digest(o(bad.get("observations"))));Map<String,Object> shaped=bad;reject("shaped but foreign root facts",()->h.append(EVENT,shaped,h.instance));check(h.rootCalls==before+1,"Closed valid shape still requires exact independent root authority");
        groups.add(m("name","closed-restart-completion-schema","invalid_shapes",invalid.size(),"independent_root_refusal",true));
    }
    static void positive(NativeFaultedJobReplacement.RestartCapture restart)throws Exception{
        try(Harness h=new Harness("restart-lifecycle-positive",restart,true)){
            Map<String,Object> original=h.faultCapture.payload;Map<String,Object> oldFault=o(((List<?>)original.get("faults")).get(0));Map<String,Object> oldContext=o(o(oldFault.get("payload")).get("context"));Map<String,Object> binding=o(((List<?>)original.get("nozzle_bindings")).get(0));
            check(!h.instance.equals(oldContext.get("bridge_instance_id"))&&h.instance.equals(binding.get("bridge_instance_id"))&&"cfg-3".equals(binding.get("config_revision")),"Current machine/instance/revision can capture historical fault provenance before a fresh source exists");same(NativeSensingReconciliationTest.source(NativeSensingReconciliationTest.SOURCE),binding.get("source"),"Historical source provenance remains exact");
            h.launch(false);Map<String,Object> receipt=h.receipt();reject("body return cannot finish restart task",()->h.append(EVENT,receipt,h.instance));h.forceTerminal(true);reject("forced succeeded DTO cannot substitute for actual wrapper completion",()->h.append(EVENT,receipt,h.instance));h.finishWrapper();h.terminal=null;reject("actual wrapper completion needs forced terminal record",()->h.append(EVENT,receipt,h.instance));h.forceTerminal(true);
            h.rootAllowed.set(false);reject("current wrapper cannot substitute for root observation authority",()->h.append(EVENT,receipt,h.instance));h.rootAllowed.set(true);schema(h);
            h.append(EVENT,receipt,h.instance);Map<String,Object> snapshot=h.coordinator.snapshot(h.taskId);check("restart_observations_completed".equals(snapshot.get("state")),"Observation phase gets its distinct terminal state");same(receipt,snapshot.get("receipt"),"Exact typed lifecycle receipt retained");check(Boolean.FALSE.equals(snapshot.get("live_resolution_activated"))&&Boolean.FALSE.equals(snapshot.get("execution_authority_restored")),"Completion cannot activate resolution or execution");h.blocked();same(original,h.vacuum.captureFaultSet(h.scope).payload,"All faults and disposition generation are unchanged");
            reject("completed phase revokes old local permit",()->h.coordinator.requirePermit(h.permit,h.owner));reject("completed receipt is one-use",()->h.append(EVENT,receipt,h.instance));reject("observation completion cannot issue a resolution wrapper token",()->h.coordinator.wrapperCompleted(h.taskId,h.operation));reject("restart phase cannot manufacture generic sensing resolution",()->h.coordinator.resolvedRecord(h.taskId,id(),Map.of(),List.of()));check(!h.coordinator.liveDisposesOperation((String)oldFault.get("operation_id"),h.instance),"Old operation still blocks outside a separate later disposition");
            Map<String,Object> laterScope=copy(h.scope);Map<String,Object> laterBinding=o(((List<?>)laterScope.get("nozzle_bindings")).get(0));laterBinding.put("source",NativeSensingReconciliationTest.source(NativeSensingReconciliationTest.NEW_SOURCE));NativeSensingReconciliation.Capture later=h.vacuum.captureFaultSet(laterScope);same(original.get("faults"),later.payload.get("faults"),"Later current-source capture preserves exact historical fault rows");same(original.get("prior_disposition_generation"),later.payload.get("prior_disposition_generation"),"Changing current source cannot disposition old faults");
            NativeVacuumJournal read=new NativeVacuumJournal();NativeSensingReconciliation replay=new NativeSensingReconciliation(read,(op,instance,historical)->historical&&op.equals(h.operation)&&instance.equals(h.instance)&&Boolean.TRUE.equals(h.terminal.get("actual_wrapper_succeeded")),(capture,dispositions,historical)->{throw new AssertionError("Replay attempted disposition");},(capture,context,facts,historical)->historical&&NativeFaultedJobReplacement.same(facts,h.facts));
            long bytes=Files.size(h.file);for(Map<String,Object> event:h.events){String type=(String)event.get("type"),instance=(String)event.get("bridge_instance_id");Map<String,Object> payload=o(event.get("payload"));if(NativeSensingReconciliation.matches(type))replay.recover(type,payload,instance);else read.recover(type,payload,instance);}Map<String,Object> historical=replay.snapshot(h.taskId);check(Boolean.TRUE.equals(historical.get("historical"))&&"restart_observations_completed".equals(historical.get("state")),"Historical lifecycle receipt remains readable");same(receipt,historical.get("receipt"),"Replay retains exact typed receipt");same(original,read.captureFaultSet(h.scope).payload,"Replay does not resolve sensing faults");reject("history cannot issue a new local permit",()->replay.issuePermit(h.taskId,h.operation,h.faultCapture.digest,new Object()));check(!replay.liveDisposesOperation((String)oldFault.get("operation_id"),h.instance)&&Files.size(h.file)==bytes,"History read restores no authority and appends no bytes");
            groups.add(m("name","actual-wrapper-and-historical-source-lifecycle","receipt",receipt,"faults_preserved",true,"root_authority_fixture","explicit synthetic exact-map callback"));
        }
    }
    static void failedWrappers(NativeFaultedJobReplacement.RestartCapture restart)throws Exception{
        for(String mode:List.of("failed","cancelled","missing-authority"))try(Harness h=new Harness("restart-lifecycle-"+mode,restart,!mode.equals("missing-authority"))){h.launch(mode.equals("failed"));if(mode.equals("cancelled"))check(h.wrapper.cancel(false),"Actual returned native Future cancellation accepted");h.finishWrapper();h.forceTerminal(true);long bytes=Files.size(h.file);reject("no completion for "+mode,()->h.append(EVENT,h.receipt(),h.instance));check(Files.size(h.file)==bytes&&"reconciling".equals(h.coordinator.snapshot(h.taskId).get("state")),"Rejected completion preserves live phase and bytes");h.blocked();same(h.faultCapture.payload,h.vacuum.captureFaultSet(h.scope).payload,"Rejected wrapper/authority leaves faults unchanged");groups.add(m("name",mode,"completion_refused",true));}
    }
    public static void main(String[] args)throws Exception{if(args.length!=1)throw new IllegalArgumentException("Expected exclusive native state directory");initialize(Path.of(args[0]));Throwable failure=null;int exit=0;try(NativeRestartSensingTaskTest.RestartFixture f=new NativeRestartSensingTaskTest.RestartFixture("lifecycle-native-root-capture","pending-material")){positive(f.capture);failedWrappers(f.capture);}catch(Throwable error){failure=error;exit=1;error.printStackTrace();}finally{config.getMachine().close();Map<String,Object> proof=m("passed",exit==0,"assertions",assertions,"refusals",refusals,"groups",groups,"error",failure==null?null:failure.toString(),"pid",ProcessHandle.current().pid(),"actual_native_wrappers",nativeWrappers,"actual_native_job_initializations",0,"actual_native_placements",0,"scope","Actual native executor Future + forced terminal records, typed native restart capture, synthetic sensing faults/root-facts authority/local decisions; isolated coordinator lifecycle and historical-source binding compatibility","combined_root_callback_qualified",false,"bridge_integration_qualified",false,"restart_execution_qualified",false,"hardware_qualified",false);Files.writeString(root.resolve("proof.json"),JSON.toJson(proof)+"\n",StandardOpenOption.CREATE_NEW);System.out.println("NATIVE_RESTART_OBSERVATION_LIFECYCLE_RESULT "+JSON.toJson(proof));}System.exit(exit);}
}
