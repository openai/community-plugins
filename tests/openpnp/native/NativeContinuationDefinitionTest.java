/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.io.IOException;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.Job;
import static org.openpnp.codex.NativeReplacementContinuationTest.*;
/** Actual schema2 first-intent journal and reconstructed native graphs; synthetic initial fault,
 * host ownership/assignment and terminal callback witnesses. No Bridge execution in this suite. */
public final class NativeContinuationDefinitionTest {
    static int checks,refusals;static final List<Map<String,Object>> groups=new ArrayList<>();
    interface Checked {void run()throws Exception;}
    static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
    static void reject(String why,Checked body)throws Exception {try{body.run();throw new AssertionError("Accepted: "+why);}catch(IOException|Bridge.Fault|NativeJobLineage.Fault expected){refusals++;check(true,why);}}
    static final String TYPE="faulted_job_replacement_continuation_definition";
    static final class Current implements AutoCloseable {
        final NativeMaterialLoads material;final NativeBoardLoads boards;final NativeJobLineage lineage;final NativeFaultedJobReplacement replacement;
        final FileChannel channel;final List<Map<String,Object>> events=new ArrayList<>();final Set<String> completed=new HashSet<>();long sequence;
        NativeFaultedJobReplacement.RestartPermit permit;NativeRestartSensingTaskTest.Journal decision;Job installed;String installedId,boundary,expectedOldId;NativeSensingReconciliation.Permit continuationRecovery;int ownershipChecks;
        Current(String name,List<Map<String,Object>> original)throws Exception {
            channel=FileChannel.open(root.resolve(name+".jsonl"),StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE);lineage=new NativeJobLineage(this::append);boards=new NativeBoardLoads(this::append);material=new NativeMaterialLoads(config,this::append);
            replacement=new NativeFaultedJobReplacement(config,material,boards,lineage,this::append,completed::contains,(job,id)->job==installed&&id.equals(installedId),this::ownership);
            replay(original);events.addAll(original);sequence=events.size();
        }
        void ownership(String operation,String instance,String revision,Job old,String oldId,Map<String,Object> prior)throws Exception {
            ownershipChecks++;if("callback-before".equals(boundary)||"callback-after".equals(boundary)&&ownershipChecks>1)throw new IOException("Synthetic exclusive ownership refusal");
            if(decision==null||!operation.equals(decision.operation)||!instance.equals(decision.instanceId)||!"cfg-2".equals(revision)||old!=permit.original()||!oldId.equals(expectedOldId))throw new AssertionError("Incorrect callback authority");
            for(Object value:prior.values())if(instance.equals(o(value).get("bridge_instance_id")))throw new AssertionError("Current-instance operation in disposition callback");
        }
        void replay(List<Map<String,Object>> source)throws Exception {for(Map<String,Object> event:source){String type=(String)event.get("type");Map<String,Object> p=o(event.get("payload"));Runnable common=replacement.prepareContinuationObservation(type,p);material.recoverEvent(type,p);boards.recoverEvent(type,p);boards.observeNativeEvent(type,p);lineage.observe(type,p);replacement.observe(type,p);if(type.startsWith("faulted_job_replacement_"))replacement.recover(type,p);common.run();}material.finishRecovery();boards.finishRecovery();}
        void append(String type,Map<String,Object> p)throws Exception {
            Runnable mat=material==null?()->{}:material.prepareEvent(type,p),lin=lineage==null||type.startsWith("job_lineage_")?()->{}:lineage.prepareObservation(type,p),common=replacement==null?()->{}:replacement.prepareContinuationObservation(type,p);
            boolean target=TYPE.equals(type);if(target&&"force-before".equals(boundary))throw new IOException("Before FileChannel.force");
            Map<String,Object> event=NativeFaultedJobReplacement.frozen(m("sequence",++sequence,"type",type,"payload",p));ByteBuffer b=ByteBuffer.wrap((JSON.toJson(event)+"\n").getBytes(StandardCharsets.UTF_8));while(b.hasRemaining())channel.write(b);channel.force(true);events.add(event);
            if(target&&"force-after".equals(boundary))throw new IOException("Unknown return after FileChannel.force");
            mat.run();lin.run();common.run();boards.observeNativeEvent(type,p);replacement.observe(type,p);
            if(target&&"revoke-after".equals(boundary))continuationRecovery.revoke();
            if(target&&"drift-after".equals(boundary))permit.candidate().job().getBoardLocations().get(0).getBoard().getPlacements().get(0).setRank(551);
        }
        Map<String,Object> op(String id)throws IOException{for(int i=events.size()-1;i>=0;i--)if("operation".equals(events.get(i).get("type"))&&id.equals(o(events.get(i).get("payload")).get("operation_id")))return o(events.get(i).get("payload"));throw new AssertionError("Missing operation");}
        public void close()throws Exception{try{replacement.close();}finally{channel.close();}}
    }
    static final class Fixture implements AutoCloseable {
        final Env original;final Current current;final NativeRestartSensingTaskTest.Journal decision;final String attempt,oldOperation,oldJobId;final Map<String,Object> originalIntent;final NativeFaultedJobReplacement.ContinuationPermit continuation;
        Fixture(String name)throws Exception {
            original=new Env(name+"-original",false);var first=task(()->new NativeReplacementAtomicAdmissionTest.Decision(original));original.permit=task(()->first.begin(original.candidate));attempt=original.permit.attemptId();oldOperation=first.operation;
            Map<String,Object> terminal=null;for(var event:original.events)if("operation".equals(event.get("type"))&&oldOperation.equals(o(event.get("payload")).get("operation_id")))terminal=NativeFaultedJobReplacement.mutable(o(event.get("payload")));terminal.put("state","outcome_unknown");original.append("operation",terminal);original.permit.close();original.append("sensing_reconciliation_unknown",original.coordinator.unknownRecord(original.taskId,"wrapper_unknown"));
            check(original.events.stream().noneMatch(e->"faulted_job_replacement_definition".equals(e.get("type"))),"Source journal truly has no definition event");
            current=new Current(name+"-root",original.events);originalIntent=o(o(current.replacement.status().get("intents")).get(attempt));var stage=task(()->current.replacement.stageRestart(attempt));var captured=task(()->current.replacement.captureRestart(stage));oldJobId=(String)captured.jobContext().get("job_id");current.expectedOldId=oldJobId;
            decision=new NativeRestartSensingTaskTest.Journal(name+"-decision");current.decision=decision;decision.fault(captured);decision.admit();current.append("operation",operation(decision.operation,decision.taskId,decision.capture.digest,(String)decision.record.get("request_id")));current.permit=task(()->current.replacement.beginRestart(captured,decision.coordinator,decision.permit,decision.owner));task(()->current.replacement.disposePriorProcess(current.permit));current.installed=task(()->current.permit.originalForHost());current.installedId=oldJobId;task(()->{current.permit.confirmOriginalHostAttachment();return null;});current.permit.close();
            Map<String,Object> restartTerminal=NativeFaultedJobReplacement.mutable(current.op(decision.operation));restartTerminal.put("state","outcome_unknown");restartTerminal.put("native_completion",m("native_wrapper_completed",true,"native_wrapper_succeeded",false,"physical_outcome_verified",false));current.append("operation",restartTerminal);current.completed.add(decision.operation);decision.append("sensing_reconciliation_unknown",decision.coordinator.unknownRecord(decision.taskId,"wrapper_unknown"));
            var capture=task(()->current.replacement.captureContinuationForHost(attempt));Map<String,Object> scope=NativeSensingReconciliationTest.scope(decision.instanceId,NativeSensingReconciliationTest.SOURCE);scope.put("dependencies",capture.dependencies());scope.put("job_context",capture.jobContext());var fault=decision.vacuum.captureFaultSet(scope);String taskId=id(),operationId=id();Object owner=new Object();var row=decision.coordinator.taskRecordForContinuation(taskId,id(),id(),fault,capture,3,"2030-01-01T00:00:00Z");decision.append("sensing_reconciliation_task",row);decision.append("sensing_reconciliation_intent",decision.coordinator.intentRecord(taskId,operationId,id(),"2026-09-14T00:00:00Z",3,"synthetic-fresh-continuation"));var recovery=decision.coordinator.issuePermit(taskId,operationId,fault.digest,owner);current.continuationRecovery=recovery;current.append("operation",operation(operationId,taskId,fault.digest,(String)row.get("request_id")));continuation=task(()->current.replacement.beginContinuation(capture,decision.coordinator,recovery,owner));
        }
        public void close()throws Exception{try{decision.close();}finally{try{current.close();}finally{original.close();}}}
    }
    static Map<String,Object> operation(String op,String task,String digest,String request){return m("operation_id",op,"request_id",id(),"request_digest",digest,"bridge_instance_id",NativeRestartSensingTaskTest.INSTANCE,"config_revision","cfg-2","method","local_native_sensing_reconciliation","task_id",task,"reconciliation_request_id",request,"state","running");}
    static void positive()throws Exception {
        try(Fixture f=new Fixture("definition-positive")){
            Map<String,Integer> counters=counters();Map<String,String> files=configurationFiles();long bytes=f.current.events.size();Map<String,Object> before=task(()->f.current.replacement.progress(f.attempt));
            check("untouched".equals(o(before.get("definition")).get("phase")),"Missing definition remains untouched after restart attachment");
            Map<String,Object> receipt=task(()->f.current.replacement.ensureContinuationDefinition(f.continuation));check(f.current.events.size()==bytes+1,"Exactly one definition record forced");
            check(!f.oldOperation.equals(receipt.get("recovery_operation_id"))&&f.continuation.continuationId().equals(receipt.get("continuation_id")),"Current continuation owns definition, never old operation");
            check(NativeFaultedJobReplacement.same(o(f.originalIntent.get("reconstruction_bundle")).get("source_mapping"),receipt.get("mapping")),"Definition maps exact original atomic reconstruction");
            Map<String,Object> expected=NativeFaultedJobReplacement.mutable(before),after=task(()->f.current.replacement.progress(f.attempt));expected.put("definition",after.get("definition"));check(NativeFaultedJobReplacement.same(expected,after),"Only definition phase advances");
            task(()->{f.continuation.check();return null;});check(NativeFaultedJobReplacement.same(receipt,task(()->f.current.replacement.ensureContinuationDefinition(f.continuation))),"Existing exact definition returns unchanged");check(f.current.events.size()==bytes+1,"Existing definition writes no additional record");
            check(counters.equals(counters())&&files.equals(configurationFiles()),"Definition publication has no feeds, placements or configuration writes");
            try(Current replay=new Current("definition-replay",f.current.events)){check(NativeFaultedJobReplacement.same(o(o(replay.replacement.status().get("definitions")).get(f.attempt)),receipt),"Replay preserves actual fresh definition receipt");check(((Number)replay.replacement.status().get("retained_candidate_count")).intValue()==0,"Replay creates no native attachment");}
            List<Map<String,Object>> prefix=new ArrayList<>();for(var event:f.current.events){if(TYPE.equals(event.get("type")))break;prefix.add(event);}
            for(String mutation:List.of("unknown-field","mapping","original-operation","progress","duplicate","terminal"))try(Current replay=new Current("definition-bad-"+mutation,prefix)){var bad=NativeFaultedJobReplacement.mutable(receipt);if(mutation.equals("unknown-field"))bad.put("grant_execution",true);else if(mutation.equals("mapping")){o(bad.get("mapping")).put("untrusted",true);bad.put("mapping_sha256",NativeFaultedJobReplacement.digest(o(bad.get("mapping"))));}else if(mutation.equals("original-operation"))bad.put("recovery_operation_id",f.oldOperation);else if(mutation.equals("progress"))bad.put("progress_sha256","0".repeat(64));else if(mutation.equals("duplicate"))replay.replacement.recover(TYPE,receipt);else{Map<String,Object> terminal=NativeFaultedJobReplacement.mutable(replay.op((String)receipt.get("recovery_operation_id")));terminal.put("state","outcome_unknown");replay.append("operation",terminal);}reject(mutation,()->replay.replacement.recover(TYPE,bad));}
            groups.add(m("case","positive","receipt",receipt));
        }
    }
    static void boundaries()throws Exception {
        for(String mode:List.of("force-before","force-after","revoke-after","drift-after","enabled-entry"))try(Fixture f=new Fixture(mode)){
            f.current.boundary=mode;if(mode.equals("enabled-entry"))task(()->{config.getMachine().setEnabled(true);return null;});
            reject(mode,()->task(()->f.current.replacement.ensureContinuationDefinition(f.continuation)));if(mode.equals("enabled-entry"))task(()->{config.getMachine().setEnabled(false);return null;});
            long forced=f.current.events.stream().filter(e->TYPE.equals(e.get("type"))).count();check(forced==(Set.of("force-before","enabled-entry").contains(mode)?0:1),"Exact definition force boundary "+mode);
            if(!mode.equals("enabled-entry"))reject("No continued capability after interrupted definition: "+mode,()->task(()->{f.continuation.check();return null;}));
            check(f.current.events.stream().noneMatch(e->"faulted_job_replacement_continuation_document_intent".equals(e.get("type"))),"No archive persistence begins after definition failure");groups.add(m("case",mode,"forced_definition_count",forced));
        }
    }
    public static void main(String[] args)throws Exception {initialize(Path.of(args[0]));Throwable error=null;try{positive();boundaries();}catch(Throwable failure){error=failure;failure.printStackTrace();}finally{config.getMachine().close();var result=m("passed",error==null,"checks",checks,"refusals",refusals,"groups",groups,"error",error==null?null:error.toString(),"pid",ProcessHandle.current().pid(),"scope","Real native first-intent/reconstruction and FileChannel force; synthetic fault and process/host/wrapper authorities; definition-only, no Bridge execution.","native_feeds",0,"native_placements",0,"native_job_initializations",0);Files.writeString(root.resolve("proof.json"),JSON.toJson(result)+"\n");System.out.println("NATIVE_CONTINUATION_DEFINITION "+JSON.toJson(result));}System.exit(error==null?0:1);}
}
