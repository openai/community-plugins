/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeFaultedSensingReplacementContinuationBridgeTest.*;
import java.io.IOException;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Actual crashed-state fresh Bridge and native local adapter. The injected rejection and
 * grant changes are controlled lifecycle boundaries; this does not qualify GUI gestures. */
public final class NativeRestartAdmissionCleanupBridgeTest {
    static final List<Map<String,Object>> cases=new ArrayList<>();
    static final class RestartAdapter implements GuiOwnership {
        final LocalAdapter delegate;volatile boolean rejectNext,revokeAtEntry;int rejections,entryRevocations;
        RestartAdapter(LocalAdapter delegate){this.delegate=delegate;}
        public void checkAttachment(Configuration c,Machine m)throws Exception{delegate.checkAttachment(c,m);}
        public void requireNativeOwnership()throws Exception{delegate.requireNativeOwnership();}
        public void requireRemoteGrant()throws Exception{delegate.requireRemoteGrant();}
        public <T>T invokeNative(Callable<T> action)throws Exception{return delegate.invokeNative(action);}
        public <T>Future<T> submitNative(Callable<T> action,boolean ignored)throws Exception{
            if(rejectNext){rejectNext=false;rejections++;throw new RejectedExecutionException("Controlled native admission rejection after local restart attestation");}
            boolean revoke=revokeAtEntry;revokeAtEntry=false;
            return delegate.submitNative(()->{if(revoke){check(config.getMachine().isTask(Thread.currentThread()),"Grant revocation reaches actual owning native executor");entryRevocations++;delegate.granted=false;}return action.call();},ignored);
        }
        public void publishJob(Job job){delegate.publishJob(job);}
        public Map<String,Object> snapshot(){return delegate.snapshot();}
        public boolean supportsSensingReconciliation(){return true;}
        public boolean supportsSensingRestart(){return true;}
        public void presentSensingReconciliation(Map<String,Object> task,SensingReconciliationSubmission callback){delegate.presentSensingReconciliation(task,callback);}
        public void dismissSensingReconciliation(String id,String reason){delegate.dismissSensingReconciliation(id,reason);}
    }
    static Object inspectField(Object owner,String name)throws Exception{Field field=owner.getClass().getDeclaredField(name);field.setAccessible(true);return field.get(owner);}
    static NativeFaultedJobReplacement.RestartStage currentStage()throws Exception{return(NativeFaultedJobReplacement.RestartStage)inspectField(inspectField(bridge,"pendingSensingReconciliation"),"restartStage");}
    static Map<String,Object> counters(){Map<String,Object> result=new TreeMap<>();for(Feeder feeder:config.getMachine().getFeeders())if(feeder instanceof ReferenceTrayFeeder)result.put(feeder.getId(),((ReferenceTrayFeeder)feeder).getFeedCount());return result;}
    static Map<String,Object> files()throws Exception{Map<String,Object> result=new TreeMap<>();try(var paths=Files.walk(state.resolve("config"))){for(Path p:paths.filter(Files::isRegularFile).toList())result.put(state.resolve("config").relativize(p).toString(),sha(Files.readAllBytes(p)));}return result;}
    static void closed(NativeFaultedJobReplacement.RestartStage stage,GuiSensingFixture.RestartAttestation attestation)throws Exception{
        try{stage.original();throw new AssertionError("Captured restart stage still owns open reconstructed graphs");}catch(IllegalStateException expected){check("Restart reconstruction is closed".equals(expected.getMessage()),"Exact previously captured stage is closed");}
        if(attestation!=null)try{attestation.check();throw new AssertionError("Captured local attestation remains live");}catch(IOException expected){check("Restart fixture attestation is closed".equals(expected.getMessage()),"Exact handed-off local attestation is closed");}
    }
    static Map<String,Object> terminalTask(String id,String expected)throws Exception{
        long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(10);Map<String,Object> value=null;
        while(System.nanoTime()<until){value=read("get_sensing_reconciliation","task_id",id);if(expected.equals(value.get("state"))&&!config.getMachine().isBusy())return value;Thread.sleep(10);}
        throw new AssertionError("Restart task failed to reach "+expected+": "+JSON.toJson(value));
    }
    static void noEffects(List<Map<String,Object>> before,Map<String,Object> originalCounters,Map<String,Object> originalFiles,Map<String,Object> originalOperation,String oldRecovery)throws Exception{
        check(((NullDriver)config.getMachine().getDrivers().get(0)).getControlledVacuumSource()==null,"No restart source installed by failed or cancelled admission");
        check(local.job==null&&local.publicationAttempts==0,"No host job assignment or native publication attempted");
        check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"Rejected restart leaves machine disabled and unhomed");
        same(originalCounters,counters(),"All actual tray counters remain unchanged");same(originalFiles,files(),"All native configuration files remain unchanged");same(originalOperation,read("get_operation","operation_id",oldRecovery),"Original interrupted operation outcome remains exact");
        List<Map<String,Object>> after=events();same(before,after.subList(0,before.size()),"Prior journal records remain immutable");
        for(Map<String,Object> event:after.subList(before.size(),after.size()))check(Set.of("operation","native_wrapper_completed","sensing_reconciliation_task","sensing_reconciliation_closed").contains(event.get("type")),"Admission cleanup emits only lifecycle/operation records: "+event.get("type"));
    }
    static void exercise(String samples,Map<String,Object> proof)throws Exception{
        Map<String,Object> marker=NativeJournalJson.parseObject(Files.readString(state.resolve("crash-boundary.json")));String attempt=(String)map(marker.get("intent")).get("replacement_attempt_id"),oldRecovery=(String)map(marker.get("intent")).get("recovery_operation_id");byte[] crashPrefix=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        check(((Number)marker.get("pid")).longValue()!=ProcessHandle.current().pid(),"Cleanup test starts in a distinct JVM after actual Runtime.halt");same(marker.get("journal_sha256"),sha(crashPrefix),"Exact crashed journal is the initial input");
        Configuration.initialize(state.resolve("config").toFile());config=Configuration.get();config.load();check(((NullDriver)config.getMachine().getDrivers().get(0)).getControlledVacuumSource()==null,"Fresh configuration has no inherited controlled source");
        local=new LocalAdapter(Bridge.map("validation_only",true,"source_absent_restart",true));RestartAdapter adapter=new RestartAdapter(local);bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(samples),0,true,"gui-simulator",adapter);
        Map<String,Object> old=read("get_operation","operation_id",oldRecovery);check("outcome_unknown".equals(old.get("state"))&&!(old.get("native_completion") instanceof Map),"Old callback remains unknown without invented prior-process wrapper completion");
        session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");Map<String,Object> originalCounters=counters(),originalFiles=files();List<Map<String,Object>> journalBefore=events();Path manifest=state.resolve("prepared-gui-fixture.json");String manifestHash=sha(Files.readAllBytes(manifest));NativeFaultedJobReplacement.RestartStage previous=null;
        for(String mode:List.of("reject-submission","grant-before-callback","grant-at-native-entry")){
            var request=command("recovery_kind",NativeSensingReconciliation.RESTART_KIND,"replacement_attempt_id",attempt);Map<String,Object> requested=run("request_sensing_reconciliation",request);Presentation shown=local.presentations.poll(10,TimeUnit.SECONDS);check(shown!=null,"Fresh same-attempt restart stages after prior cleanup: "+mode);String taskId=(String)shown.task.get("task_id");NativeFaultedJobReplacement.RestartStage stage=currentStage();check(stage!=null&&stage!=previous&&stage.original()!=null,"New request owns a new inert reconstruction stage: "+mode);previous=stage;
            same(requested.get("operation_id"),call("request_sensing_reconciliation",request).get("operation_id"),"Exact duplicate request reuses remote operation");check(currentStage()==stage&&local.presentations.isEmpty(),"Duplicate remote request creates no extra stage or local form");
            GuiSensingFixture.RestartAttestation attestation=edt(()->GuiSensingFixture.captureRestart(config,manifest,manifestHash,"lost-before-place"));attestation.check();
            if(mode.equals("reject-submission"))adapter.rejectNext=true;else if(mode.equals("grant-before-callback"))local.granted=false;else adapter.revokeAtEntry=true;
            String code=refused(()->shown.callback.submitRestart(attestation,local::currentAuthority).toCompletableFuture().get(15,TimeUnit.SECONDS),mode.equals("grant-before-callback")?"SENSING_RECOVERY_AUTHORITY_STALE":"SENSING_RECOVERY_UNRESOLVED","Controlled restart admission boundary "+mode);
            Map<String,Object> terminal=terminalTask(taskId,"scope_stale");closed(stage,attestation);check(local.displayedTask==null&&local.dismissals.stream().anyMatch(d->d.startsWith(taskId+":")),"Failed local form is dismissed: "+mode);
            Object localOperation=terminal.get("recovery_operation_id");Map<String,Object> operation=null;
            if(mode.equals("grant-before-callback")){check(localOperation==null,"Revoked local decision creates no local recovery intent/operation");}
            else {
                // The sensing reducer has no intent before admission fails; inspect its actual
                // local operation through the request/task-bound journal record instead.
                for(Map<String,Object> event:events()){Map<String,Object> payload=map(event.get("payload"));if("operation".equals(event.get("type"))&&taskId.equals(payload.get("task_id"))&&"local_native_sensing_reconciliation".equals(payload.get("method")))operation=payload;}
                check(operation!=null&&"failed".equals(operation.get("state")),"Admitted local operation terminates failed before sensing intent");String expected=mode.equals("reject-submission")?"NATIVE_ADMISSION_REJECTED":"SENSING_RECOVERY_FAILED";check(expected.equals(map(operation.get("result")).get("code")),"Exact pre-intent failure recorded: "+expected);
                if(mode.equals("reject-submission"))check(Boolean.FALSE.equals(map(operation.get("result")).get("native_effect_started")),"Rejected submission records no native effect started");
            }
            check(taskEvents("sensing_reconciliation_intent",taskId)==0,"Failed boundary writes no sensing intervention intent");
            refused(()->shown.callback.submitRestart(attestation,local::currentAuthority).toCompletableFuture().get(5,TimeUnit.SECONDS),"SENSING_RESTART_ATTESTATION_REQUIRED","Failed task cannot consume its old attestation again");
            local.granted=true;noEffects(journalBefore,originalCounters,originalFiles,old,oldRecovery);cases.add(Bridge.map("mode",mode,"task_id",taskId,"request_operation_id",requested.get("operation_id"),"callback_refusal",code,"terminal_task",terminal,"local_operation",operation,"stage_closed",true,"attestation_closed",true,"source_or_host_installed",false));
        }
        Map<String,Object> finalRequest=run("request_sensing_reconciliation",command("recovery_kind",NativeSensingReconciliation.RESTART_KIND,"replacement_attempt_id",attempt));Presentation last=local.presentations.poll(10,TimeUnit.SECONDS);check(last!=null,"Another new request can stage after every failed local decision");NativeFaultedJobReplacement.RestartStage lastStage=currentStage();check(lastStage!=previous,"Final new request has a fresh stage");last.callback.cancel("local-test-cancel");Map<String,Object> cancelled=terminalTask((String)last.task.get("task_id"),"cancelled");closed(lastStage,null);check(local.displayedTask==null,"Local cancellation dismisses final form");noEffects(journalBefore,originalCounters,originalFiles,old,oldRecovery);
        check(adapter.rejections==1&&adapter.entryRevocations==1,"Both injected native admission boundaries occurred exactly once");byte[] after=Files.readAllBytes(state.resolve("journal/operations.jsonl"));check(Arrays.equals(crashPrefix,Arrays.copyOf(after,crashPrefix.length)),"Original actual-crash journal prefix remains byte-identical");
        proof.putAll(Bridge.map("passed",true,"crash_pid",marker.get("pid"),"replacement_attempt_id",attempt,"cases",cases,"final_cancelled_task",cancelled,"native_admission_rejections",adapter.rejections,"native_entry_revocations",adapter.entryRevocations,"old_unknown_preserved",true,"crash_prefix_unchanged",true,"stage_cleanup_verified",true,"attestation_cleanup_verified",true,"restart_native_placements",0,"source_installations",0,"host_publications",0,"gui_gestures_qualified",false,"public_package_qualified",false,"hardware_qualified",false));
    }
    public static void main(String[] args)throws Exception{if(args.length!=2)throw new IllegalArgumentException("Expected samples and exclusive crashed state directory");state=Path.of(args[1]).toAbsolutePath();phase="restart-cleanup";scenario="lost-before-place";int exit=0;Map<String,Object> proof=new LinkedHashMap<>();try{exercise(args[0],proof);}catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}finally{try{if(bridge!=null)bridge.close();proof.put("bridge_closed",true);}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}try{if(local!=null){local.granted=true;local.release();}}catch(Throwable failure){proof.put("gate_release_failure",failure.toString());exit=1;}try{if(config!=null)config.getMachine().close();proof.put("machine_closed",true);}catch(Throwable failure){proof.put("machine_close_failure",failure.toString());exit=1;}if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("assertions",checks.size(),"checks",checks,"public_calls",calls,"pid",ProcessHandle.current().pid()));write("restart-cleanup-proof.json",proof);System.out.println("NATIVE_RESTART_ADMISSION_CLEANUP_RESULT "+JSON.toJson(proof));}System.exit(exit);}
}
