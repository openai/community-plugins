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

/** Real native reconstruction/root reducers/forced journals. Initial fault and host journal-lock,
 * host assignment and independent wrapper attestations are explicit synthetic test seams. */
public final class NativeRestartPriorProcessDispositionTest {
    static int checks,refusals;static final List<Map<String,Object>> groups=new ArrayList<>();
    interface Checked {void run()throws Exception;}
    static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
    static void reject(String why,Checked body)throws Exception {try{body.run();throw new AssertionError("Accepted: "+why);}catch(IOException|Bridge.Fault|NativeJobLineage.Fault expected){refusals++;check(true,why);}}
    static final String TYPE="faulted_job_replacement_restart_prior_process_disposition";
    static final class Current implements AutoCloseable {
        final NativeMaterialLoads material;final NativeBoardLoads boards;final NativeJobLineage lineage;final NativeFaultedJobReplacement replacement;
        final FileChannel channel;final List<Map<String,Object>> events=new ArrayList<>();final Set<String> completed=new HashSet<>();long sequence;
        NativeFaultedJobReplacement.RestartPermit permit;NativeRestartSensingTaskTest.Journal decision;Job installed;String installedId,boundary,expectedOldId;int ownershipChecks;
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
            if(target&&"revoke-after".equals(boundary))decision.permit.revoke();
            if(target&&"drift-after".equals(boundary))permit.candidate().job().getBoardLocations().get(0).getBoard().getPlacements().get(0).setRank(551);
        }
        Map<String,Object> op(String id)throws IOException{for(int i=events.size()-1;i>=0;i--)if("operation".equals(events.get(i).get("type"))&&id.equals(o(events.get(i).get("payload")).get("operation_id")))return o(events.get(i).get("payload"));throw new AssertionError("Missing operation");}
        public void close()throws Exception{try{replacement.close();}finally{channel.close();}}
    }
    static final class Fixture implements AutoCloseable {
        final NativeRestartSensingTaskTest.RestartFixture original;final Current current;final NativeRestartSensingTaskTest.Journal decision;final String attempt,oldOperation,oldJobId;final Map<String,Object> oldRecord;
        Fixture(String name)throws Exception {
            original=new NativeRestartSensingTaskTest.RestartFixture(name+"-seed","definition");attempt=original.attempt;current=new Current(name+"-current",original.source.events);
            var stage=task(()->current.replacement.stageRestart(attempt));var captured=task(()->current.replacement.captureRestart(stage));oldOperation=(String)o(o(current.replacement.status().get("intents")).get(attempt)).get("recovery_operation_id");oldJobId=(String)captured.jobContext().get("job_id");current.expectedOldId=oldJobId;oldRecord=current.op(oldOperation);
            decision=new NativeRestartSensingTaskTest.Journal(name+"-decision");current.decision=decision;decision.fault(captured);decision.admit();current.append("operation",operation(decision.operation,decision.taskId,decision.capture.digest,(String)decision.record.get("request_id"),decision.instanceId));current.permit=task(()->current.replacement.beginRestart(captured,decision.coordinator,decision.permit,decision.owner));
        }
        void attach()throws Exception {current.installed=task(()->current.permit.originalForHost());current.installedId=oldJobId;task(()->{current.permit.confirmOriginalHostAttachment();return null;});}
        void terminal(boolean wrapper,boolean independent)throws Exception {current.permit.close();Map<String,Object> row=NativeFaultedJobReplacement.mutable(current.op(decision.operation));row.put("state","outcome_unknown");if(wrapper)row.put("native_completion",m("native_wrapper_completed",true,"native_wrapper_succeeded",false,"physical_outcome_verified",false));current.append("operation",row);if(independent)current.completed.add(decision.operation);decision.append("sensing_reconciliation_unknown",decision.coordinator.unknownRecord(decision.taskId,"wrapper_unknown"));}
        public void close()throws Exception{try{decision.close();}finally{try{current.close();}finally{original.close();}}}
    }
    static Map<String,Object> operation(String op,String task,String digest,String request,String instance){return m("operation_id",op,"request_id",id(),"request_digest",digest,"bridge_instance_id",instance,"config_revision","cfg-2","method","local_native_sensing_reconciliation","task_id",task,"reconciliation_request_id",request,"state","running");}
    static void positiveAndReplay()throws Exception {
        try(Fixture f=new Fixture("positive")){
            var p=f.current.permit;Map<String,Integer> before=counters();Map<String,String> files=configurationFiles();
            reject("host access before forced prior disposition",()->task(()->p.originalForHost()));
            check(!f.oldRecord.containsKey("native_completion"),"Actual original recovery is unknown without invented wrapper");
            Map<String,Object> receipt=task(()->f.current.replacement.disposePriorProcess(p));check(f.current.ownershipChecks==2,"Independent host ownership checked before and after real force");
            check(o(receipt.get("prior_operations")).keySet().equals(Set.of(f.oldOperation)),"Disposition captures only exact old recovery operation");
            check(NativeFaultedJobReplacement.same(f.oldRecord,f.current.op(f.oldOperation)),"Unknown original operation remains byte-identical");
            reject("duplicate disposition",()->task(()->f.current.replacement.disposePriorProcess(p)));
            reject("wrong host job",()->task(()->{f.current.installed=new Job();f.current.installedId=f.oldJobId;p.confirmOriginalHostAttachment();return null;}));
            f.attach();reject("live restart blocks continuation",()->task(()->f.current.replacement.captureContinuationForHost(f.attempt)));
            check(!task(()->f.current.replacement.priorProcessDisposedForContinuation(f.attempt,f.oldOperation,false)),"Live restart cannot authorize continuation before own wrapper");
            p.close();check(!task(()->f.current.replacement.priorProcessDisposedForContinuation(f.attempt,f.oldOperation,false)),"Closing restart permit does not complete its operation");
            f.terminal(true,false);check(!task(()->f.current.replacement.priorProcessDisposedForContinuation(f.attempt,f.oldOperation,false)),"Serialized new wrapper alone lacks independent current completion");
            f.current.completed.add(f.decision.operation);
            check(task(()->f.current.replacement.priorProcessDisposedForContinuation(f.attempt,f.oldOperation,false)),"Exact old process disposition authorizes later independent recovery");
            check(!task(()->f.current.replacement.priorProcessDisposedForContinuation(f.attempt,f.decision.operation,false)),"Current restart operation never exempted");
            check(!task(()->f.current.replacement.priorProcessDisposedForContinuation(id(),f.oldOperation,false)),"Foreign attempt never exempted");
            var capture=task(()->f.current.replacement.captureContinuationForHost(f.attempt));check(o(capture.payload.get("prior_operations")).containsKey(f.decision.operation),"New restart stays in exact next-decision operation union");check(o(capture.payload.get("prior_process_dispositions")).containsKey(receipt.get("receipt_id")),"New capture binds actual durable disposition");
            try(var foreign=new NativeRestartSensingTaskTest.Journal("foreign-instance-next",id())){foreign.replay(f.decision.events);Map<String,Object> scope=NativeSensingReconciliationTest.scope(foreign.instanceId,NativeSensingReconciliationTest.SOURCE);scope.put("dependencies",capture.dependencies());scope.put("job_context",capture.jobContext());var fault=foreign.vacuum.captureFaultSet(scope);String taskId=id(),operationId=id();Object owner=new Object();var row=foreign.coordinator.taskRecordForContinuation(taskId,id(),id(),fault,capture,3,"2030-01-01T00:00:00Z");foreign.append("sensing_reconciliation_task",row);foreign.append("sensing_reconciliation_intent",foreign.coordinator.intentRecord(taskId,operationId,id(),"2026-09-14T00:00:00Z",3,"synthetic-foreign-instance"));var recovery=foreign.coordinator.issuePermit(taskId,operationId,fault.digest,owner);f.current.append("operation",operation(operationId,taskId,fault.digest,(String)row.get("request_id"),foreign.instanceId));reject("Current-process witness cannot mint a foreign-instance continuation",()->task(()->f.current.replacement.beginContinuation(capture,foreign.coordinator,recovery,owner)));}
            {var next=f.decision;Map<String,Object> scope=NativeSensingReconciliationTest.scope(next.instanceId,NativeSensingReconciliationTest.SOURCE);scope.put("dependencies",capture.dependencies());scope.put("job_context",capture.jobContext());var fault=f.decision.vacuum.captureFaultSet(scope);String task=id(),op=id();Object owner=new Object();var row=next.coordinator.taskRecordForContinuation(task,id(),id(),fault,capture,3,"2030-01-01T00:00:00Z");next.append("sensing_reconciliation_task",row);next.append("sensing_reconciliation_intent",next.coordinator.intentRecord(task,op,id(),"2026-09-14T00:00:00Z",3,"new-independent-continuation"));var sensing=next.coordinator.issuePermit(task,op,fault.digest,owner);f.current.append("operation",operation(op,task,fault.digest,(String)row.get("request_id"),next.instanceId));var permit=task(()->f.current.replacement.beginContinuation(capture,next.coordinator,sensing,owner));task(()->{permit.check();return null;});check(true,"Actual root continuation capability issued only under fresh local decision");permit.close();}
            check(before.equals(counters())&&files.equals(configurationFiles()),"No feeds, native job initialization, placement or configuration writes");
            try(Current replay=new Current("positive-replayed",f.current.events)){check(task(()->replay.replacement.priorProcessDisposedForContinuation(f.attempt,f.oldOperation,true)),"Replay validates historical relationship");check(!task(()->replay.replacement.priorProcessDisposedForContinuation(f.attempt,f.oldOperation,false)),"Replay cannot recreate local disposition witness");check(((Number)replay.replacement.status().get("retained_candidate_count")).intValue()==0,"Replay attaches no native candidate");}
            List<Map<String,Object>> prefix=new ArrayList<>();for(Map<String,Object> e:f.current.events){if(TYPE.equals(e.get("type")))break;prefix.add(e);}
            for(String mutation:List.of("unknown-field","infer-wrapper","current-operation","foreign-old-instance","duplicate"))try(Current replay=new Current("corrupt-"+mutation,prefix)){Map<String,Object> bad=NativeFaultedJobReplacement.mutable(receipt);if(mutation.equals("unknown-field"))bad.put("clear",true);else if(mutation.equals("infer-wrapper"))bad.put("prior_wrapper_completion_inferred",true);else if(mutation.equals("current-operation")){o(bad.get("prior_operations")).put(f.decision.operation,replay.op(f.decision.operation));bad.put("prior_operations_sha256",NativeFaultedJobReplacement.digest(o(bad.get("prior_operations"))));}else if(mutation.equals("foreign-old-instance")){o(o(bad.get("prior_operations")).get(f.oldOperation)).put("bridge_instance_id",f.decision.instanceId);bad.put("prior_operations_sha256",NativeFaultedJobReplacement.digest(o(bad.get("prior_operations"))));}else replay.replacement.recover(TYPE,receipt);reject(mutation,()->replay.replacement.recover(TYPE,bad));}
            groups.add(m("case","positive-and-replay","receipt",receipt,"history_unknown_preserved",true));
        }
    }
    static void boundaries()throws Exception {
        for(String mode:List.of("callback-before","callback-after","force-before","force-after","revoke-after","drift-after"))try(Fixture f=new Fixture(mode)){
            f.current.boundary=mode;reject(mode,()->task(()->f.current.replacement.disposePriorProcess(f.current.permit)));
            long forced=f.current.events.stream().filter(e->TYPE.equals(e.get("type"))).count();check(forced==(Set.of("callback-before","force-before").contains(mode)?0:1),"Exact before/after force receipt boundary: "+mode);
            check(NativeFaultedJobReplacement.same(f.oldRecord,f.current.op(f.oldOperation)),"Boundary preserves old unknown operation: "+mode);
            reject("No host handoff after incomplete disposition: "+mode,()->task(()->f.current.permit.originalForHost()));
            groups.add(m("case",mode,"forced_receipts",forced,"history_unknown_preserved",true));
        }
    }
    public static void main(String[] args)throws Exception {initialize(Path.of(args[0]));Throwable error=null;try{positiveAndReplay();boundaries();}catch(Throwable failure){error=failure;failure.printStackTrace();}finally{config.getMachine().close();Map<String,Object> result=m("passed",error==null,"checks",checks,"refusals",refusals,"groups",groups,"error",error==null?null:error.toString(),"pid",ProcessHandle.current().pid(),"scope","Actual native reconstruction and forced root journal; synthetic initial faults, journal ownership/host assignment/wrapper attestations. No Bridge execution or physical authority.","native_feeds",0,"native_placements",0,"native_job_initializations",0);Files.writeString(root.resolve("proof.json"),JSON.toJson(result)+"\n");System.out.println("NATIVE_RESTART_PRIOR_DISPOSITION "+JSON.toJson(result));}System.exit(error==null?0:1);}
}
