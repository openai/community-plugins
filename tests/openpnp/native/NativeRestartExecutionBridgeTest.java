/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeFaultedSensingReplacementContinuationBridgeTest.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.*;
import org.openpnp.spi.Machine;
import org.openpnp.machine.reference.driver.NullDriver;

/** Fresh JVM after an actual halted Bridge. Uses its public tools and one-use local
 * callbacks with real native execution; the local adapter does not qualify desktop gestures. */
public final class NativeRestartExecutionBridgeTest {
    static final class RestartAdapter implements GuiOwnership {
        final LocalAdapter delegate;
        RestartAdapter(LocalAdapter delegate){this.delegate=delegate;}
        public void checkAttachment(Configuration c,Machine m)throws Exception{delegate.checkAttachment(c,m);}
        public void requireNativeOwnership()throws Exception{delegate.requireNativeOwnership();}
        public void requireRemoteGrant()throws Exception{delegate.requireRemoteGrant();}
        public <T>T invokeNative(Callable<T> action)throws Exception{return delegate.invokeNative(action);}
        public <T>Future<T> submitNative(Callable<T> action,boolean ignored)throws Exception{return delegate.submitNative(action,ignored);}
        public void publishJob(Job job){delegate.publishJob(job);}
        public Map<String,Object> snapshot(){return delegate.snapshot();}
        public boolean supportsSensingReconciliation(){return true;}
        public boolean supportsSensingRestart(){return true;}
        public void presentSensingReconciliation(Map<String,Object> task,SensingReconciliationSubmission callback){delegate.presentSensingReconciliation(task,callback);}
        public void dismissSensingReconciliation(String id,String reason){delegate.dismissSensingReconciliation(id,reason);}
    }
    static void exercise(String samples,Map<String,Object> proof)throws Exception{
        Map<String,Object> marker=NativeJournalJson.parseObject(Files.readString(state.resolve("crash-boundary.json")));
        Map<String,Object> crash=NativeJournalJson.parseObject(Files.readString(state.resolve("crash-context.json")));
        String attempt=(String)map(marker.get("intent")).get("replacement_attempt_id");
        String oldRecovery=(String)map(marker.get("intent")).get("recovery_operation_id");
        byte[] prefix=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        check(((Number)marker.get("pid")).longValue()!=ProcessHandle.current().pid(),"Restart executes in a distinct process after actual Runtime.halt");
        same(marker.get("journal_sha256"),sha(prefix),"Restart starts with exact crashed Bridge journal bytes");
        Configuration.initialize(state.resolve("config").toFile());config=Configuration.get();config.load();
        NullDriver driver=(NullDriver)config.getMachine().getDrivers().get(0);
        check(driver.getControlledVacuumSource()==null,"Fresh OpenPnP configuration has no inherited source");
        Path manifest=state.resolve("prepared-gui-fixture.json");String manifestHash=sha(Files.readAllBytes(manifest));
        local=new LocalAdapter(Bridge.map("validation_only",true,"source_absent_restart",true));
        bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(samples),0,true,"gui-simulator",new RestartAdapter(local));
        Map<String,Object> old=read("get_operation","operation_id",oldRecovery);write("restart-original-operation.json",old);
        check("outcome_unknown".equals(old.get("state"))&&!(old.get("native_completion") instanceof Map),"Replay preserves the interrupted callback without a fabricated completion");
        Map<String,Object> caps=read("get_capabilities");
        check(((List<?>)caps.get("tools")).contains("openpnp_request_sensing_reconciliation"),"Source-absent Bridge advertises explicit restart request");
        session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
        long beforeActions=count("native_action_intent"),beforePlacements=count("native_placement_checkpoint");
        var request=command("recovery_kind",NativeSensingReconciliation.RESTART_KIND,"replacement_attempt_id",attempt);
        Map<String,Object> requested=run("request_sensing_reconciliation",request);
        Presentation shown=local.presentations.poll(10,TimeUnit.SECONDS);check(shown!=null,"Restart request presents an exact local task");
        write("restart-presented-task.json",shown.task);
        check(driver.getControlledVacuumSource()==null&&local.job==null&&count("native_action_intent")==beforeActions,"Remote restart request installs no source/job and performs no native action");
        same(requested.get("operation_id"),call("request_sensing_reconciliation",request).get("operation_id"),"Duplicate request returns its original operation");
        refused(()->shown.callback.submit(local::currentAuthority).toCompletableFuture().get(10,TimeUnit.SECONDS),"SENSING_RESTART_ATTESTATION_REQUIRED","Ordinary callback cannot bypass fresh local restart attestation");
        GuiSensingFixture.RestartAttestation attestation=edt(()->GuiSensingFixture.captureRestart(config,manifest,manifestHash,"lost-before-place"));
        Map<String,Object> observed=shown.callback.submitRestart(attestation,local::currentAuthority).toCompletableFuture().get(50,TimeUnit.SECONDS);
        write("restart-observations-completed.json",observed);
        check("restart_observations_completed".equals(map(observed.get("reconciliation")).get("state")),"Actual completed wrapper commits the separate observation lifecycle receipt");
        check(driver.getControlledVacuumSource()!=null&&local.job!=null&&!config.getMachine().isEnabled(),"Restart attaches fresh source and exact inactive native job while disabled");
        check(Boolean.FALSE.equals(map(read("get_capabilities").get("sensing_reconciliation")).get("restart_request_available")),"Installed but unready source no longer advertises source-absent restart");
        check("outcome_unknown".equals(read("get_status").get("job_state")),"Observed restart leaves job execution unresolved");
        check(count("native_action_intent")==beforeActions&&count("native_placement_checkpoint")==beforePlacements,"Restart observation phase makes no feed or placement");
        same(old,read("get_operation","operation_id",oldRecovery),"First restart phase preserves exact unknown prior callback record");
        Job original=local.job;
        Map<String,Object> continued=run("request_sensing_reconciliation",command("recovery_kind",NativeSensingReconciliation.CONTINUATION_KIND,"replacement_attempt_id",attempt));
        Presentation next=local.presentations.poll(10,TimeUnit.SECONDS);check(next!=null,"Separate continuation requires another exact local decision");
        write("restart-continuation-presented.json",next.task);
        Map<String,Object> completed=next.callback.submit(local::currentAuthority).toCompletableFuture().get(60,TimeUnit.SECONDS);
        write("restart-continuation-completed.json",completed);
        check("resolved_for_current_scope".equals(map(completed.get("reconciliation")).get("state")),"Continuation commits current source/material/board disposition");
        String replacement=(String)read("get_status").get("job_id");check(attempt.equals(replacement)&&local.job!=original,"Continuation publishes exact replacement candidate");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());
        Map<String,Object> refusedStart=call("start_job",command("job_id",replacement));
        check("failed".equals(refusedStart.get("state"))&&"JOB_NOT_VALIDATED".equals(result(refusedStart).get("code")),"Replacement cannot reuse validation from before crash");
        run("locate_fiducials",command("job_id",replacement));
        check(Boolean.TRUE.equals(result(run("validate_job",command("job_id",replacement))).get("valid")),"Replacement passes fresh native registration and validation");
        Map<String,Object> placed=run("start_job",command("job_id",replacement));write("restart-completed-job.json",placed);
        check(((Number)result(placed).get("placed")).intValue()==1,"Real OpenPnP completes one placement in the restarted process");
        run("set_machine_enabled",command("enabled",false));
        same(old,read("get_operation","operation_id",oldRecovery),"Successful placement preserves prior callback unknown outcome");
        byte[] after=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        check(Arrays.equals(prefix,Arrays.copyOf(after,prefix.length)),"Crash journal prefix remains byte-identical after full restart workflow");
        proof.putAll(Bridge.map("passed",true,"original_process_id",marker.get("pid"),"replacement_attempt_id",attempt,"post_restart_placements",1,"original_unknown_preserved",true,"original_prefix_unchanged",true,
            "bridge_restart_execution_qualified",true,"desktop_gestures_qualified",false,"physical_machine_qualified",false,"packaged_plugin_qualified",false));
    }
    static void history(String samples,Map<String,Object> proof)throws Exception{
        Map<String,Object> completed=NativeJournalJson.parseObject(Files.readString(state.resolve("restart-execution-proof.json")));
        check(Boolean.TRUE.equals(completed.get("passed"))&&((Number)completed.get("process_id")).longValue()!=ProcessHandle.current().pid(),"History is read by a third process after successful restart placement");
        byte[] before=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        Configuration.initialize(state.resolve("config").toFile());config=Configuration.get();config.load();
        bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(samples),0,true,"native-simulator");
        Map<String,Object> observed=NativeJournalJson.parseObject(Files.readString(state.resolve("restart-observations-completed.json")));
        String task=(String)map(map(observed.get("reconciliation")).get("task")).get("task_id");
        Map<String,Object> status=read("get_status"),receipt=read("get_sensing_reconciliation","task_id",task);
        check("restart_observations_completed".equals(receipt.get("state"))&&Boolean.TRUE.equals(receipt.get("historical")),"Full new restart receipt history replays as historical facts");
        check("absent".equals(status.get("job_state"))&&read("get_control_session").get("session_id")==null,"Completed restart history restores neither host job nor control lease");
        check(((NullDriver)config.getMachine().getDrivers().get(0)).getControlledVacuumSource()==null,"History restores no simulator source authority");
        check(Arrays.equals(before,Files.readAllBytes(state.resolve("journal/operations.jsonl"))),"Completed history and readonly queries append no journal record");
        proof.putAll(Bridge.map("passed",true,"history_only",true,"original_journal_unchanged",true,"source_authority_restored",false,"host_job_restored",false,"post_restart_placements",0));
    }
    public static void main(String[] args)throws Exception{
        if(args.length<2||args.length>3||args.length==3&&!args[2].equals("history"))throw new IllegalArgumentException("Expected sample directory, same state directory, optional history");
        boolean historical=args.length==3;state=Path.of(args[1]).toAbsolutePath();phase=historical?"restart-history":"restart";scenario="lost-before-place";int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try{if(historical)history(args[0],proof);else exercise(args[0],proof);}catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally{
            try{if(bridge!=null)bridge.close();proof.put("bridge_closed",true);}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}
            try{if(local!=null)local.release();}catch(Throwable failure){proof.put("ownership_release_failure",failure.toString());exit=1;}
            try{if(config!=null)config.getMachine().close();proof.put("machine_closed",true);}catch(Throwable failure){proof.put("machine_close_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("assertions",checks.size(),"checks",checks,"public_calls",calls,"process_id",ProcessHandle.current().pid()));
            write(historical?"restart-history-proof.json":"restart-execution-proof.json",proof);System.out.println("NATIVE_RESTART_EXECUTION_RESULT "+JSON.toJson(proof));
        }System.exit(exit);
    }
}
