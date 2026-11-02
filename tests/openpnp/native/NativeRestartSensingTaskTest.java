/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.openpnp.codex.NativeReplacementContinuationTest.*;

/** Exact native reconstruction capture plus closed sensing reducer/permit tests. Sensing events,
 * initial action fault and local operator/wrapper authority are explicitly synthetic fixtures. */
public final class NativeRestartSensingTaskTest {
    static int assertions,refusals;static final List<Map<String,Object>> groups=new ArrayList<>();
    static final String INSTANCE=NativeSensingReconciliationTest.id(1200),SOURCE=NativeSensingReconciliationTest.SOURCE,NEW_SOURCE=NativeSensingReconciliationTest.NEW_SOURCE;
    interface Checked{void run()throws Exception;}
    static void check(boolean value,String why){assertions++;if(!value)throw new AssertionError(why);}
    static void reject(String why,Checked body)throws Exception{try{body.run();throw new AssertionError("Accepted: "+why);}catch(IOException|Bridge.Fault|NativeJobLineage.Fault expected){refusals++;check(true,why);}}
    static Map<String,Object> mutable(Map<String,Object> value){return NativeSensingReconciliationTest.copy(value);}
    static void write(String name,Object value)throws Exception{Files.writeString(root.resolve(name),JSON.toJson(value)+"\n",StandardOpenOption.CREATE_NEW);}
    static final class Journal implements AutoCloseable {
        final NativeVacuumJournal vacuum=new NativeVacuumJournal();final AtomicBoolean completed=new AtomicBoolean(true);
        final NativeSensingReconciliation coordinator=new NativeSensingReconciliation(vacuum,(op,instance,replay)->completed.get(),(capture,receipt,replay)->true);
        final Object owner=new Object();final List<Map<String,Object>> events=new ArrayList<>();final Path file;final FileChannel channel;
        final String taskId=id(),operation=id(),instanceId;NativeSensingReconciliation.Capture capture;NativeSensingReconciliation.Permit permit;Map<String,Object> record,context,verification;long sequence;
        Journal(String name)throws Exception{this(name,INSTANCE);}
        Journal(String name,String currentInstance)throws Exception{instanceId=currentInstance;file=root.resolve(name+".jsonl");channel=FileChannel.open(file,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);}
        void append(String type,Map<String,Object> payload)throws Exception{
            String instance=NativeSensingReconciliation.matches(type)?instanceId:(String)o(payload.get("context")).get("bridge_instance_id");
            Runnable commit=NativeSensingReconciliation.matches(type)?coordinator.prepare(type,payload,instance):vacuum.prepare(type,payload,instance);
            Map<String,Object> event=NativeSensingReconciliation.freeze(m("sequence",++sequence,"type",type,"payload",payload,"bridge_instance_id",instance));byte[] bytes=(JSON.toJson(event)+"\n").getBytes(StandardCharsets.UTF_8);ByteBuffer buffer=ByteBuffer.wrap(bytes);while(buffer.hasRemaining())channel.write(buffer);channel.force(true);commit.run();events.add(event);
        }
        void observation(Map<String,Object> event)throws Exception{append(((String)event.get("native_event")).endsWith(".before")?"vacuum_observation_intent":"vacuum_observation_outcome",event);}
        void fault(NativeFaultedJobReplacement.RestartCapture restart)throws Exception{
            Map<String,Object> nativeContext=NativeSensingReconciliationTest.context();nativeContext.put("scope","job");nativeContext.put("job_context",restart.jobContext());
            // Deliberate synthetic pending sensor fact, bound to the recorded original operation.
            nativeContext.put("operation_id",((List<?>)restart.dependencies().get("operation_ids")).get(0));
            observation(NativeSensingReconciliationTest.event("read.before",900,null,nativeContext,SOURCE));
            Map<String,Object> scope=NativeSensingReconciliationTest.scope(instanceId,SOURCE);scope.put("dependencies",restart.dependencies());scope.put("job_context",restart.jobContext());capture=vacuum.captureFaultSet(scope);
            record=coordinator.taskRecordForRestart(taskId,id(),id(),capture,restart,2,"2030-01-01T00:00:00Z");
        }
        void admit()throws Exception{append("sensing_reconciliation_task",record);append("sensing_reconciliation_intent",coordinator.intentRecord(taskId,operation,id(),"2026-09-14T00:00:00Z",2,"synthetic-new-local-restart-decision"));permit=coordinator.issuePermit(taskId,operation,capture.digest,owner);}
        void probe()throws Exception{
            Map<String,Object> latch=NativeSensingReconciliationTest.latch();append("sensing_reconciliation_intervention",coordinator.interventionRecord(taskId,List.of(NativeSensingReconciliationTest.binding(instanceId,SOURCE)),List.of(NativeSensingReconciliationTest.binding(instanceId,NEW_SOURCE)),List.of(),latch,latch));context=coordinator.observationContext(permit,"N1");
            coordinator.withPermit(permit,owner,()->{observation(NativeSensingReconciliationTest.event("check.before",910,null,context,NEW_SOURCE));valve("before",911,true);valve("returned",911,true);observation(NativeSensingReconciliationTest.event("read.before",912,910,context,NEW_SOURCE));observation(NativeSensingReconciliationTest.event("read.returned",912,910,context,NEW_SOURCE));valve("before",913,false);valve("returned",913,false);observation(NativeSensingReconciliationTest.event("check.returned",910,null,context,NEW_SOURCE));return null;});
            verification=m("native_probe_operation_id",operation,"probes",List.of(m("nozzle_id","N1","check_observation_id",NativeSensingReconciliationTest.id(910),"read_observation_ids",List.of(NativeSensingReconciliationTest.id(912)),"valve_on_id",NativeSensingReconciliationTest.id(911),"valve_off_id",NativeSensingReconciliationTest.id(913),"native_verdict",true,"exact_current_binding",NativeSensingReconciliationTest.binding(instanceId,NEW_SOURCE))));append("sensing_reconciliation_verified",coordinator.verifiedRecord(taskId,verification));
        }
        void valve(String phase,int id,boolean enabled)throws Exception{Map<String,Object> event=NativeSensingReconciliationTest.event("valve."+phase,id,910,context,NEW_SOURCE);o(event.get("data")).putAll(m("enabled",enabled,"cleanup_attempt",!enabled));observation(event);}
        void replay(List<Map<String,Object>> source)throws Exception{for(Map<String,Object> event:source){String type=(String)event.get("type"),instance=(String)event.get("bridge_instance_id");Map<String,Object> payload=o(event.get("payload"));if(NativeSensingReconciliation.matches(type))coordinator.recover(type,payload,instance);else vacuum.recover(type,payload,instance);}}
        public void close()throws Exception{coordinator.revokeAll();channel.close();}
    }
    /** Shared native test fixture, never a production authority. The new root replays actual
     * forced original records; only beginRestart can transfer its staged graph ownership. */
    static final class AdmissionEnv implements AutoCloseable {
        final NativeMaterialLoads material;final NativeBoardLoads boards;final NativeJobLineage lineage;final NativeFaultedJobReplacement replacement;
        final Path file;final FileChannel channel;final List<Map<String,Object>> history=new ArrayList<>();long sequence;String boundary;
        NativeFaultedJobReplacement.RestartStage stage;Journal decision;boolean emptyBeforeForce,emptyAfterForce;
        AdmissionEnv(String name,List<Map<String,Object>> original)throws Exception{
            file=root.resolve(name+".jsonl");channel=FileChannel.open(file,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);lineage=new NativeJobLineage(this::append);boards=new NativeBoardLoads(this::append);material=new NativeMaterialLoads(config,this::append);replacement=new NativeFaultedJobReplacement(config,material,boards,lineage,this::append,operation->false);
            replay(original);history.addAll(original);sequence=original.size();
        }
        void replay(List<Map<String,Object>> events)throws Exception{for(Map<String,Object> event:events){String type=(String)event.get("type");Map<String,Object> p=o(event.get("payload"));Runnable observation=replacement.prepareContinuationObservation(type,p);material.recoverEvent(type,p);boards.recoverEvent(type,p);boards.observeNativeEvent(type,p);lineage.observe(type,p);replacement.observe(type,p);if(type.startsWith("faulted_job_replacement_"))replacement.recover(type,p);observation.run();}material.finishRecovery();boards.finishRecovery();}
        void append(String type,Map<String,Object> p)throws Exception{
            Runnable mat=material==null?()->{}:material.prepareEvent(type,p),lin=lineage==null||type.startsWith("job_lineage_")?()->{}:lineage.prepareObservation(type,p),observation=replacement==null?()->{}:replacement.prepareContinuationObservation(type,p);
            boolean target="faulted_job_replacement_restart_attachment".equals(type);if(target){emptyBeforeForce=((Number)replacement.status().get("retained_candidate_count")).intValue()==0;if("before".equals(boundary))throw new IOException("Injected before actual restart attachment force");}
            Map<String,Object> event=NativeFaultedJobReplacement.frozen(m("sequence",++sequence,"type",type,"payload",p));ByteBuffer buffer=ByteBuffer.wrap((JSON.toJson(event)+"\n").getBytes(StandardCharsets.UTF_8));while(buffer.hasRemaining())channel.write(buffer);channel.force(true);history.add(event);
            if(target){emptyAfterForce=((Number)replacement.status().get("retained_candidate_count")).intValue()==0;if("after".equals(boundary))throw new IOException("Injected unknown return after actual restart attachment force");if("drift".equals(boundary))stage.candidate().job().getBoardLocations().get(0).getBoard().getPlacements().get(0).setRank(977);if("revoke".equals(boundary))decision.permit.revoke();}
            mat.run();lin.run();observation.run();if(boards!=null)boards.observeNativeEvent(type,p);if(replacement!=null)replacement.observe(type,p);
        }
        public void close()throws Exception{try{replacement.close();}finally{channel.close();}}
    }
    static final class RestartFixture implements AutoCloseable {
        final Env source;final AdmissionEnv replay;final String attempt;final NativeFaultedJobReplacement.RestartStage stage;final NativeFaultedJobReplacement.RestartCapture capture;final Journal decision;
        RestartFixture(String name,String prefix)throws Exception{this(name,prefix,INSTANCE);}
        RestartFixture(String name,String prefix,String instance)throws Exception{
            source=new Env(name+"-source",false);NativeReplacementAtomicAdmissionTest.Decision old=task(()->new NativeReplacementAtomicAdmissionTest.Decision(source));source.permit=task(()->old.begin(source.candidate));task(()->{source.replacement.retainCandidate(source.permit,source.candidate);return null;});attempt=source.permit.attemptId();
            if(Set.of("lineage","completed","mixed-board","pending-material").contains(prefix))task(()->{source.lineage.createReplacement(source.permit,attempt);return null;});
            if(prefix.equals("pending-material"))task(()->{source.failType="material_replacement_outcome";try{reject("fixture pending material outcome",()->source.material.replaceRetiredLoad(source.permit,(String)((List<?>)source.capture.dependencies().get("material_load_ids")).get(0)));}finally{source.failType=null;}return null;});
            if(Set.of("completed","mixed-board").contains(prefix))task(()->{for(Object oldLoad:(List<?>)source.capture.dependencies().get("material_load_ids"))source.material.replaceRetiredLoad(source.permit,(String)oldLoad);if(prefix.equals("mixed-board")){source.failType="board_replacement_outcome";source.failOccurrence=2;try{reject("fixture second board outcome pending",()->source.boards.quarantineAndBindReplacement(source.permit,source.oldJob,source.freshJob,attempt));}finally{source.failType=null;}}else source.boards.quarantineAndBindReplacement(source.permit,source.oldJob,source.freshJob,attempt);return null;});
            Map<String,Object> terminal=null;for(Map<String,Object> event:source.events)if("operation".equals(event.get("type"))&&old.operation.equals(o(event.get("payload")).get("operation_id")))terminal=mutable(o(event.get("payload")));if(terminal==null)throw new AssertionError("Original local admission absent");terminal.put("state","outcome_unknown");source.append("operation",terminal);source.permit.close();source.append("sensing_reconciliation_unknown",source.coordinator.unknownRecord(source.taskId,"wrapper_unknown"));
            List<Map<String,Object>> exact=new ArrayList<>();for(String line:Files.readAllLines(source.file))exact.add(NativeJournalJson.parseObject(line));if(!NativeFaultedJobReplacement.same(exact,source.events))throw new AssertionError("Original journal differs from its forced bytes");replay=new AdmissionEnv(name+"-restarted",exact);stage=task(()->replay.replacement.stageRestart(attempt));replay.stage=stage;capture=task(()->replay.replacement.captureRestart(stage));decision=new Journal(name+"-decision",instance);decision.fault(capture);replay.decision=decision;
        }
        void admit()throws Exception{decision.admit();Map<String,Object> row=m("operation_id",decision.operation,"request_id",id(),"request_digest",decision.capture.digest,"bridge_instance_id",decision.instanceId,"config_revision","cfg-2","method","local_native_sensing_reconciliation","task_id",decision.taskId,"reconciliation_request_id",decision.record.get("request_id"),"state","running");replay.append("operation",row);}
        NativeFaultedJobReplacement.RestartPermit begin()throws Exception{return task(()->replay.replacement.beginRestart(capture,decision.coordinator,decision.permit,decision.owner));}
        public void close()throws Exception{try{decision.close();}finally{try{replay.close();}finally{source.close();}}}
    }
    static void schemaAndTypedRefusals(NativeFaultedJobReplacement.RestartCapture restart)throws Exception{
        try(Journal j=new Journal("restart-task-shape")){j.fault(restart);long bytes=Files.size(j.file);Map<String,Object> context=o(j.record.get("restart_context"));
            check(context.keySet().equals(Set.of("replacement_attempt_id","restart_capture_sha256"))&&Objects.equals(context.get("replacement_attempt_id"),restart.payload.get("replacement_attempt_id"))&&Objects.equals(context.get("restart_capture_sha256"),restart.digest),"Typed task binds exact owned capture hash and attempt");
            reject("missing typed restart capture",()->j.coordinator.taskRecordForRestart(id(),id(),id(),j.capture,null,2,"2030-01-01T00:00:00Z"));
            reject("missing fault capture",()->j.coordinator.taskRecordForRestart(id(),id(),id(),null,restart,2,"2030-01-01T00:00:00Z"));
            for(String key:List.of("job_context","dependencies")){Map<String,Object> changed=mutable(j.capture.payload);if(key.equals("job_context"))o(changed.get(key)).put("job_revision","e".repeat(64));else o(changed.get(key)).put("material_load_ids",List.of(id()));NativeSensingReconciliation.Capture wrong=new NativeSensingReconciliation.Capture(changed);reject("typed factory mismatched "+key,()->j.coordinator.taskRecordForRestart(id(),id(),id(),wrong,restart,2,"2030-01-01T00:00:00Z"));}
            List<Map<String,Object>> invalid=new ArrayList<>();
            Map<String,Object> changed=mutable(j.record);changed.remove("restart_context");invalid.add(changed);
            changed=mutable(j.record);changed.put("replacement_context",changed.remove("restart_context"));invalid.add(changed);
            changed=mutable(j.record);changed.put("replacement_context",m("replacement_attempt_id",restart.payload.get("replacement_attempt_id"),"continuation_capture_sha256",restart.digest));invalid.add(changed);
            for(String field:List.of("replacement_attempt_id","restart_capture_sha256")){changed=mutable(j.record);o(changed.get("restart_context")).remove(field);invalid.add(changed);changed=mutable(j.record);o(changed.get("restart_context")).put(field,"malformed");invalid.add(changed);}
            changed=mutable(j.record);o(changed.get("restart_context")).put("foreign",true);invalid.add(changed);
            changed=mutable(j.record);o(changed.get("restart_context")).put("replacement_attempt_id",id());invalid.add(changed);
            for(String kind:List.of(NativeSensingReconciliation.CONTINUATION_KIND,"restore-sensing-readiness","replace-faulted-job-attempt")){changed=mutable(j.record);changed.put("recovery_kind",kind);invalid.add(changed);}
            changed=mutable(j.record);o(changed.get("fault_set")).put("job_context",null);changed.put("fault_set_sha256",NativeSensingReconciliation.digest(o(changed.get("fault_set"))));invalid.add(changed);
            changed=mutable(j.record);o(o(changed.get("fault_set")).get("dependencies")).put("job_attempt_ids",List.of());changed.put("fault_set_sha256",NativeSensingReconciliation.digest(o(changed.get("fault_set"))));invalid.add(changed);
            for(Map<String,Object> bad:invalid){reject("closed restart task shape/context",()->j.coordinator.prepare("sensing_reconciliation_task",bad,INSTANCE));reject("malformed task cannot enter replay history",()->j.coordinator.recover("sensing_reconciliation_task",bad,INSTANCE));}
            check(bytes==Files.size(j.file)&&j.coordinator.summaries().isEmpty(),"Rejected task shapes and typed mismatches force no records and create no tasks");groups.add(m("name","typed-capture-and-closed-context","invalid_shapes",invalid.size()));}
    }
    static void lifecycle(NativeFaultedJobReplacement.RestartCapture restart)throws Exception{
        try(Journal j=new Journal("restart-task-live")){j.fault(restart);reject("permit before task/local decision",()->j.coordinator.issuePermit(j.taskId,j.operation,j.capture.digest,j.owner));j.admit();j.coordinator.requirePermit(j.permit,j.owner);check(true,"Fresh local restart intent may issue a process-local observation permit");
            reject("duplicate restart permit",()->j.coordinator.issuePermit(j.taskId,j.operation,j.capture.digest,j.owner));reject("foreign local owner",()->j.coordinator.requirePermit(j.permit,new Object()));
            try(Journal foreign=new Journal("restart-task-foreign-owner")){reject("permit from another coordinator",()->foreign.coordinator.requirePermit(j.permit,j.owner));}
            Map<String,Object> snap=j.coordinator.snapshot(j.taskId);check(!Boolean.TRUE.equals(snap.get("historical"))&&Boolean.FALSE.equals(snap.get("execution_authority_restored")),"Live task still grants no execution readiness");
            List<Map<String,Object>> prefix=new ArrayList<>(j.events);try(Journal replay=new Journal("restart-task-replay-admission")){replay.replay(prefix);check(Boolean.TRUE.equals(replay.coordinator.snapshot(j.taskId).get("historical")),"Replayed restart task and intent are historical");reject("replay cannot issue fresh permit",()->replay.coordinator.issuePermit(j.taskId,j.operation,j.capture.digest,replay.owner));reject("live permit rejected by replayed coordinator",()->replay.coordinator.requirePermit(j.permit,j.owner));check(Files.size(replay.file)==0,"Replay itself writes no event");}
            j.probe();long bytes=Files.size(j.file);check("verified_pending_commit".equals(j.coordinator.snapshot(j.taskId).get("state")),"Exact fresh recovery probe can be recorded without restoring readiness");
            Map<String,Object> generic=m("sensing","fresh-observed-empty","nozzle_material","none-present","feeder_loads",List.of(),"board_loads",List.of(),"replacement_attempt_id",null,"unresolved_dependencies",List.of());
            reject("restart cannot resolve using generic standalone success",()->j.coordinator.prepare("sensing_reconciliation_resolved",j.coordinator.resolvedRecord(j.taskId,id(),generic,List.of()),INSTANCE));
            Map<String,Object> shaped=new LinkedHashMap<>(generic);shaped.putAll(m("replacement_attempt_id",restart.payload.get("replacement_attempt_id"),"continuation_id",id(),"continuation_receipt_sha256","a".repeat(64),"publication_id",id(),"publication_receipt_sha256","b".repeat(64)));
            reject("restart cannot resolve using shaped continuation success",()->j.coordinator.prepare("sensing_reconciliation_resolved",j.coordinator.resolvedRecord(j.taskId,id(),shaped,List.of()),INSTANCE));
            check(bytes==Files.size(j.file)&&!j.coordinator.liveDisposesOperation(NativeSensingReconciliationTest.OLDOP,INSTANCE),"Resolution refusals leave exact original uncertainty and no readiness");
            try{j.vacuum.requireNoFault();throw new AssertionError("Restart observations restored no-fault readiness");}catch(NativeVacuumJournal.Fault expected){check(true,"Original fault remains fenced after successful restart observations");}
            j.append("sensing_reconciliation_unknown",j.coordinator.unknownRecord(j.taskId,"wrapper_unknown"));reject("unknown outcome revokes restart permit",()->j.coordinator.requirePermit(j.permit,j.owner));
            List<Map<String,Object>> exact=new ArrayList<>();for(String line:Files.readAllLines(j.file))exact.add(NativeJournalJson.parseObject(line));check(NativeFaultedJobReplacement.same(exact,j.events),"Replay input equals exact FileChannel-forced bytes");
            try(Journal replay=new Journal("restart-task-replay-probes")){replay.replay(exact);check(Boolean.TRUE.equals(replay.coordinator.snapshot(j.taskId).get("historical"))&&"reconciliation_unknown".equals(replay.coordinator.snapshot(j.taskId).get("state")),"Replay preserves the original unknown outcome");reject("probe history cannot issue permit",()->replay.coordinator.issuePermit(j.taskId,j.operation,j.capture.digest,replay.owner));check(Files.size(replay.file)==0,"Replay of complete probe history creates no new journal facts");}
            write("restart-task-final-snapshot.json",j.coordinator.snapshot(j.taskId));groups.add(m("name","fresh-vs-historical-permit-lifecycle","synthetic_probe_count",1));}
    }
    static int attachmentCount(AdmissionEnv e)throws Exception{return o(e.replacement.status().get("restart_attachments")).size();}
    static void replayNoAuthority(String name,RestartFixture f,int expectedAttachments)throws Exception{
        try(AdmissionEnv replay=new AdmissionEnv(name+"-history-reader",f.replay.history)){
            check(attachmentCount(replay)==expectedAttachments,"Historical restart receipt count matches exact forced prefix");check(((Number)replay.replacement.status().get("retained_candidate_count")).intValue()==0,"Replayed restart receipt never restores native graph attachment");
            check(Files.size(replay.file)==0,"Restart receipt replay forces no new record");reject("live restart stage rejected by foreign replay root",()->task(()->replay.replacement.captureRestart(f.stage)));
            write(name+"-replay-status.json",replay.replacement.status());
        }
    }
    static void rootAdmission()throws Exception{
        try(RestartFixture f=new RestartFixture("restart-root-positive","definition")){
            Map<String,Integer> counts=counters();Map<String,String> files=configurationFiles();Map<String,Boolean> history=new TreeMap<>(f.source.oldJob.getPlacedStatusSnapshot());check(((Number)f.replay.replacement.status().get("retained_candidate_count")).intValue()==0,"Actual root candidate map is empty before new local admission");f.admit();
            reject("wrong process-local owner",()->task(()->f.replay.replacement.beginRestart(f.capture,f.decision.coordinator,f.decision.permit,new Object())));reject("missing actual restart capture",()->task(()->f.replay.replacement.beginRestart(null,f.decision.coordinator,f.decision.permit,f.decision.owner)));reject("restart admission off native executor",()->f.replay.replacement.beginRestart(f.capture,f.decision.coordinator,f.decision.permit,f.decision.owner));
            NativeFaultedJobReplacement.RestartPermit permit=f.begin();task(()->{permit.check();return null;});check(f.replay.emptyBeforeForce&&f.replay.emptyAfterForce,"No root native graph ownership exists before or immediately after actual force in sink");check(attachmentCount(f.replay)==1&&((Number)f.replay.replacement.status().get("retained_candidate_count")).intValue()==1,"Actual beginRestart attaches native graphs only after successful forced authorization");
            check(permit.original()!=f.source.oldJob&&permit.candidate().job()!=f.source.candidate.job()&&permit.attemptId().equals(f.attempt),"Restart permit binds newly reconstructed exact native identities and existing attempt");
            reject("duplicate root restart admission",f::begin);task(()->{reject("old staged ownership transferred exactly once",f.stage::requireCurrent);reject("unobserved replay material remains unready",f.replay.material::requireReady);reject("unobserved replay boards remain unready",()->f.replay.boards.requireReady(permit.candidate().job()));return null;});
            check(counts.equals(counters())&&files.equals(configurationFiles())&&history.equals(f.source.oldJob.getPlacedStatusSnapshot()),"Root graph attachment performs no native feeds, resets, config saves or old history change");
            replayNoAuthority("restart-root-positive",f,1);f.decision.permit.revoke();task(()->{reject("revoked local decision fences actual root restart capability",permit::check);return null;});permit.close();groups.add(m("name","actual-root-forced-attachment","old_wrapper_completion_inferred",false));
        }
        for(String mode:List.of("same-instance","wrong-context-hash","wrong-context-attempt","revoked","missing-admission"))try(RestartFixture f=new RestartFixture("restart-root-reject-"+mode,"definition",mode.equals("same-instance")?NativeSensingReconciliationTest.INSTANCE:INSTANCE)){
            if(mode.equals("wrong-context-hash")){f.decision.record=mutable(f.decision.record);o(f.decision.record.get("restart_context")).put("restart_capture_sha256","f".repeat(64));}
            if(mode.equals("wrong-context-attempt")){f.decision.record=mutable(f.decision.record);o(f.decision.record.get("restart_context")).put("replacement_attempt_id",f.source.jobId);}
            if(mode.equals("missing-admission"))f.decision.admit();else f.admit();if(mode.equals("revoked"))f.decision.permit.revoke();long bytes=Files.size(f.replay.file);Map<String,Integer> counts=counters();reject("actual root rejects "+mode,f::begin);
            check(bytes==Files.size(f.replay.file)&&attachmentCount(f.replay)==0&&((Number)f.replay.replacement.status().get("retained_candidate_count")).intValue()==0&&counts.equals(counters()),"Rejected root restart cannot append authorization or attach/mutate native graph");groups.add(m("name","root-refuses-"+mode));
        }
        for(String boundary:List.of("before","after","drift","revoke"))try(RestartFixture f=new RestartFixture("restart-root-force-"+boundary,"definition")){
            f.admit();f.replay.boundary=boundary;Map<String,Integer> counts=counters();Map<String,String> files=configurationFiles();Map<String,Boolean> history=new TreeMap<>(f.source.oldJob.getPlacedStatusSnapshot());long bytes=Files.size(f.replay.file);reject("actual forced root restart boundary "+boundary,f::begin);
            check(f.replay.emptyBeforeForce&&(!boundary.equals("before")?f.replay.emptyAfterForce:true),"Boundary callback observes zero prematurely attached root graphs");check(((Number)f.replay.replacement.status().get("retained_candidate_count")).intValue()==0,"Unknown or drifting force grants no native graph attachment");
            check((Files.size(f.replay.file)>bytes)==!boundary.equals("before"),"Actual forced journal bytes distinguish before-force refusal from post-force interruption");if(Set.of("before","after").contains(boundary))check(Boolean.TRUE.equals(f.replay.replacement.status().get("publication_fault")),"Uncertain sink return fences the live root journal");
            check(counts.equals(counters())&&files.equals(configurationFiles())&&history.equals(f.source.oldJob.getPlacedStatusSnapshot()),"Interrupted root attachment leaves native inventory and original history unchanged");replayNoAuthority("restart-root-force-"+boundary,f,boundary.equals("before")?0:1);groups.add(m("name","root-force-"+boundary,"forced_attachment",!boundary.equals("before")));
        }
    }
    static void ownedCaptureFixture()throws Exception{
        try(Env source=new Env("restart-source-native",false)){
            NativeReplacementAtomicAdmissionTest.Decision decision=task(()->new NativeReplacementAtomicAdmissionTest.Decision(source));source.permit=task(()->decision.begin(source.candidate));task(()->{source.replacement.retainCandidate(source.permit,source.candidate);return null;});String attempt=source.permit.attemptId();
            Map<String,Object> terminal=null;for(Map<String,Object> event:source.events)if("operation".equals(event.get("type"))&&decision.operation.equals(o(event.get("payload")).get("operation_id")))terminal=mutable(o(event.get("payload")));
            if(terminal==null)throw new AssertionError("Original admitted operation missing");terminal.put("state","outcome_unknown");source.append("operation",terminal);source.permit.close();source.append("sensing_reconciliation_unknown",source.coordinator.unknownRecord(source.taskId,"wrapper_unknown"));
            Map<String,Integer> counts=counters();Map<String,String> files=configurationFiles();Map<String,Boolean> history=new TreeMap<>(source.oldJob.getPlacedStatusSnapshot());List<Map<String,Object>> exact=new ArrayList<>();for(String line:Files.readAllLines(source.file))exact.add(NativeJournalJson.parseObject(line));check(NativeFaultedJobReplacement.same(exact,source.events),"Root replay begins with the exact actual forced schema2 journal");
            try(Env restarted=new Env("restart-source-replay",true)){restarted.replay(exact);task(()->{try(NativeFaultedJobReplacement.RestartStage stage=restarted.replacement.stageRestart(attempt)){
                NativeFaultedJobReplacement.RestartCapture capture=restarted.replacement.captureRestart(stage);check(stage.original()!=source.oldJob&&stage.candidate().job()!=source.candidate.job(),"Actual root stage owns newly reconstructed native graphs");check(Objects.equals(capture.payload.get("replacement_attempt_id"),attempt)&&capture.dependencies().get("job_attempt_ids") instanceof List&&((List<?>)capture.dependencies().get("job_attempt_ids")).contains(attempt),"Owned root restart capture includes the exact attempt and dependency union");
                write("owned-restart-capture.json",capture.payload);schemaAndTypedRefusals(capture);lifecycle(capture);stage.requireCurrent();return null;
            }});check(Files.size(restarted.file)==0,"Inert restart staging and sensing task fixtures do not mutate the root journal");}
            check(counts.equals(counters())&&files.equals(configurationFiles())&&history.equals(source.oldJob.getPlacedStatusSnapshot()),"Task/permit tests mutate no actual feeder counter, config file or original placement history");groups.add(m("name","actual-owned-reconstruction-capture","same_jvm_new_root_owner",true));
        }
    }
    public static void main(String[] args)throws Exception{
        if(args.length!=1)throw new IllegalArgumentException("Expected exclusive native state directory");initialize(Path.of(args[0]));int exit=0;Throwable failure=null;
        try{ownedCaptureFixture();rootAdmission();}catch(Throwable error){error.printStackTrace();failure=error;exit=1;}finally{config.getMachine().close();Map<String,Object> proof=m("passed",exit==0,"assertions",assertions,"refusals",refusals,"groups",groups,"error",failure==null?null:failure.toString(),"pid",ProcessHandle.current().pid(),"actual_native_job_initializations",0,"actual_native_feeds",0,"actual_native_placements",0,"scope","Actual native reconstruction/root capture, beginRestart graph attachment and FileChannel force; synthetic initial fault, sensing observations and local operator/wrapper authority; same-JVM fresh root and coordinator only","bridge_integration_qualified",false,"restart_execution_qualified",false,"physical_hardware_qualified",false);write("proof.json",proof);System.out.println("NATIVE_RESTART_SENSING_TASK_RESULT "+JSON.toJson(proof));}System.exit(exit);
    }
}
