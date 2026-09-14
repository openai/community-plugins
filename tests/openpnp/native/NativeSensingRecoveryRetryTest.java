/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeSensingReconciliationBridgeTest.*;
import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.base.*;

/** Actual native Future fails after the recovery body/probe returns. A new local capability
 * must explicitly disposition that old unknown operation before ordinary work is possible.
 * The sole injected failure is an IOException in the actual native callable wrapper; no
 * operation result, observation, receipt, ownership result, or native Future is fabricated. */
public final class NativeSensingRecoveryRetryTest {
    static final String WRAPPER_FAILURE="CONTROLLED_NATIVE_RECOVERY_WRAPPER_FAILURE_AFTER_BODY";
    static Adapter adapter;
    static final class Adapter implements GuiOwnership {
        final AbstractMachine machine=(AbstractMachine)config.getMachine();
        final ExternalExecutionControl gate=machine.getExternalExecutionControl();
        final Object token;
        final Map<String,Object> provenance;
        final BlockingQueue<Presentation> presentations=new LinkedBlockingQueue<>();
        final List<String> dismissals=Collections.synchronizedList(new ArrayList<>());
        final AtomicBoolean failNext=new AtomicBoolean();
        final AtomicInteger injectedFailures=new AtomicInteger();
        final CountDownLatch bodyReturned=new CountDownLatch(1),releaseWrapper=new CountDownLatch(1);
        volatile boolean granted=true;
        volatile Future<?> failedFuture;
        volatile Job job;
        String displayed;
        Adapter(Map<String,Object> provenance)throws Exception {
            this.provenance=copy(provenance);token=gate.claim("sensing79 wrapper retry native adapter fixture");
        }
        public void checkAttachment(Configuration current,Machine attached)throws Exception {
            if(current!=config||attached!=machine)throw new Bridge.Fault("GUI_ATTACHMENT_CHANGED","Test fixture attachment changed");
            Bridge.verifyNativeSimulatorClasses(attached);
        }
        public void requireNativeOwnership()throws Exception {
            if(!gate.owns(token))throw new Bridge.Fault("LOCAL_GRANT_REQUIRED","Native gate was lost");
        }
        public void requireRemoteGrant()throws Exception {
            requireNativeOwnership();if(!granted)throw new Bridge.Fault("OWNERSHIP_REVOKED","Local grant was revoked");
        }
        public <T>T invokeNative(Callable<T> action)throws Exception {return gate.withOwner(token,action);}
        public <T>Future<T> submitNative(Callable<T> action,boolean ignoreEnabled)throws Exception {
            boolean inject=failNext.compareAndSet(true,false);
            Future<T> actual=gate.withOwner(token,()->machine.submit(()->{
                T result=action.call();
                if(inject) {
                    bodyReturned.countDown();
                    if(!releaseWrapper.await(30,TimeUnit.SECONDS))throw new IOException("Test wrapper release timed out");
                    injectedFailures.incrementAndGet();throw new IOException(WRAPPER_FAILURE);
                }
                return result;
            },null,ignoreEnabled));
            if(inject)failedFuture=actual;
            return actual;
        }
        public void publishJob(Job value) {
            check(machine.isTask(Thread.currentThread()),"Retried ordinary job is published from the actual native executor");job=value;
        }
        public Map<String,Object> snapshot() {
            return Bridge.map("validation_only",true,"local_grant",granted,"native_ownership_held",gate.owns(token),
                "provenance",provenance,"swing_qualification",false,"wrapper_failure_injection","after actual Bridge callable returns");
        }
        public boolean supportsSensingReconciliation() {return true;}
        public synchronized void presentSensingReconciliation(Map<String,Object> task,SensingReconciliationSubmission callback) {
            if(displayed!=null)throw new IllegalStateException("Prior local view was not dismissed");
            displayed=(String)task.get("task_id");presentations.add(new Presentation(task,callback));
        }
        public synchronized void dismissSensingReconciliation(String id,String reason) {
            if(id.equals(displayed))displayed=null;dismissals.add(id+":"+reason);
        }
        boolean currentAuthority() {return granted&&gate.owns(token);}
        void release()throws Exception {if(gate.owns(token))gate.release(token);}
    }
    static Presentation request()throws Exception {
        Map<String,Object> admitted=run("request_sensing_reconciliation",command("recovery_kind","restore-sensing-readiness"));
        Presentation shown=adapter.presentations.poll(10,TimeUnit.SECONDS);
        check(shown!=null,"Public recovery request delivers its exact local callback");
        Map<String,Object> view=read("get_sensing_reconciliation","task_id",shown.task.get("task_id"));
        check("awaiting_local_action".equals(view.get("state"))&&Boolean.TRUE.equals(view.get("task_active")),"New recovery task is awaiting a fresh local action");
        check(admitted.get("operation_id").equals(map(view.get("task")).get("request_operation_id")),"Local task binds its exact new public request operation");
        return shown;
    }
    static Set<Object> probeIds(Map<String,Object> task) {
        Set<Object> ids=new HashSet<>();
        for(Object raw:(List<?>)map(task.get("verification")).get("probes")) {
            Map<String,Object> probe=map(raw);ids.add(probe.get("check_observation_id"));ids.addAll((List<?>)probe.get("read_observation_ids"));
            ids.add(probe.get("valve_on_id"));ids.add(probe.get("valve_off_id"));
        }
        return ids;
    }
    static void exerciseRetry(Map<String,Object> proof)throws Exception {
        session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());
        Map<String,Object> original=await(call("measure_sensor",command("nozzle_id",nozzle().getId(),"samples",1)),"outcome_unknown");
        String originalId=(String)original.get("operation_id");write("original-failed-sensor-operation.json",original);
        run("set_machine_enabled",command("enabled",false));
        check(Boolean.TRUE.equals(nozzleState(read("get_status")).get("sticky_fault")),"Original native sensor failure is retained before recovery");
        long initialFeeds=feedCount();Presentation first=request();String firstTask=(String)first.task.get("task_id");
        write("first-presented-task.json",first.task);adapter.failNext.set(true);
        CompletionStage<Map<String,Object>> firstCompletion=first.callback.submit(adapter::currentAuthority);
        check(adapter.bodyReturned.await(20,TimeUnit.SECONDS),"Actual recovery body returned while its enclosing native wrapper is still held");
        check(adapter.failedFuture!=null&&!adapter.failedFuture.isDone()&&!firstCompletion.toCompletableFuture().isDone(),"Actual native Future and local completion remain pending after successful body return");
        Map<String,Object> verified=read("get_sensing_reconciliation","task_id",firstTask);write("first-body-returned-task.json",verified);
        check("verified_pending_commit".equals(verified.get("state")),"Successful actual native probes remain verified_pending_commit before wrapper completion");
        check(verified.get("receipt")==null&&Boolean.FALSE.equals(verified.get("live_resolution_activated")),"Successful body alone creates no receipt or fresh readiness");
        check(taskEvents("sensing_reconciliation_verified",firstTask)==1&&taskEvents("sensing_reconciliation_resolved",firstTask)==0,"Exactly one native verification is forced without a resolved record");
        String failedRecoveryId=(String)verified.get("recovery_operation_id");
        check(Boolean.TRUE.equals(nozzleState(read("get_status")).get("sticky_fault")),"Old sensing fault still fences readiness while native wrapper is incomplete");
        adapter.releaseWrapper.countDown();
        refused(()->firstCompletion.toCompletableFuture().get(30,TimeUnit.SECONDS),"SENSING_RECOVERY_UNRESOLVED","Actual wrapper failure causes local recovery to remain unresolved");
        Map<String,Object> failedRecovery=await(read("get_operation","operation_id",failedRecoveryId),"outcome_unknown");
        Map<String,Object> unknownTask=read("get_sensing_reconciliation","task_id",firstTask);
        write("first-unknown-operation.json",failedRecovery);write("first-unknown-task.json",unknownTask);
        check(adapter.injectedFailures.get()==1&&adapter.failedFuture.isDone()&&!adapter.failedFuture.isCancelled(),"Exactly one actual native wrapper threw after returning from the successful body");
        check("NATIVE_WRAPPER_FAILED".equals(result(failedRecovery).get("code")),"Bridge classifies actual Future failure as NATIVE_WRAPPER_FAILED");
        check(JSON.toJson(result(failedRecovery)).contains(WRAPPER_FAILURE),"Public failed operation retains the exact controlled IOException cause");
        Map<String,Object> failedNative=map(failedRecovery.get("native_completion"));
        check(Boolean.TRUE.equals(failedNative.get("native_wrapper_completed"))&&Boolean.FALSE.equals(failedNative.get("native_wrapper_succeeded")),"Unknown recovery retains a completed but unsuccessful actual native wrapper");
        check("reconciliation_unknown".equals(unknownTask.get("state"))&&unknownTask.get("receipt")==null
            &&Boolean.FALSE.equals(unknownTask.get("live_resolution_activated")),"Failed wrapper retains unknown reconciliation with no resolution receipt or live readiness");
        check(taskEvents("sensing_reconciliation_unknown",firstTask)==1&&taskEvents("sensing_reconciliation_resolved",firstTask)==0,"Failed wrapper has one durable unknown record and zero resolved records");
        check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Failed wrapper settles idle and disabled after the actual body cleanup");
        check(Boolean.TRUE.equals(nozzleState(read("get_status")).get("sticky_fault")),"Completed failed wrapper has no authority to clear the old fault");
        refused(()->call("measure_sensor",command("nozzle_id",nozzle().getId(),"samples",1)),"VACUUM_OUTCOME_UNKNOWN","Ordinary native sensing remains refused after failed recovery");
        refused(()->first.callback.submit(adapter::currentAuthority).toCompletableFuture().get(10,TimeUnit.SECONDS),null,"Failed first local capability cannot be replayed");
        check(feedCount()==initialFeeds&&count("native_action_intent")==0,"Failed recovery consumes no feeder stock or ordinary native job action");
        check(original.equals(read("get_operation","operation_id",originalId)),"Failed recovery does not alter the original sensor operation");
        byte[] prior=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        Files.write(state.resolve("journal-before-fresh-recovery.jsonl"),prior,StandardOpenOption.CREATE_NEW);
        Presentation second=request();String secondTask=(String)second.task.get("task_id");write("second-presented-task.json",second.task);
        check(!firstTask.equals(secondTask),"Retry requires a distinct explicit local task capability");
        Map<String,Object> secondPending=read("get_sensing_reconciliation","task_id",secondTask);write("second-pending-task.json",secondPending);
        Map<String,Object> capture=map(map(secondPending.get("task")).get("fault_set")),dependencies=map(capture.get("dependencies"));
        check(((List<?>)dependencies.get("operation_ids")).equals(List.of(failedRecoveryId)),"Fresh task captures the exact prior unknown local recovery as its operation dependency");
        for(String key:List.of("action_ids","board_load_ids","material_load_ids","job_attempt_ids","unresolved_dependencies"))
            check(((List<?>)dependencies.get(key)).isEmpty(),"Standalone retry has no "+key+" workpiece dependency");
        check(((List<?>)capture.get("faults")).stream().anyMatch(raw->originalId.equals(map(raw).get("operation_id"))),"Fresh capture still retains the original failed sensor observation");
        check(Boolean.TRUE.equals(nozzleState(read("get_status")).get("sticky_fault")),"A new remote request alone does not resolve either old unknown operation");
        Map<String,Object> callbackResult=second.callback.submit(adapter::currentAuthority).toCompletableFuture().get(40,TimeUnit.SECONDS);
        write("second-local-callback-result.json",callbackResult);
        Map<String,Object> resolved=read("get_sensing_reconciliation","task_id",secondTask);write("second-resolved-task.json",resolved);
        check("resolved_for_current_scope".equals(resolved.get("state"))&&Boolean.TRUE.equals(resolved.get("live_resolution_activated")),"Fresh actual source repair and probes resolve only the new task's exact current scope");
        Map<String,Object> receipt=map(resolved.get("receipt"));String secondRecoveryId=(String)resolved.get("recovery_operation_id");
        check(!failedRecoveryId.equals(secondRecoveryId)&&new HashSet<>((List<?>)receipt.get("original_operation_ids")).equals(Set.of(originalId,failedRecoveryId)),"New receipt separately dispositions exactly the original sensor and failed local recovery operations");
        for(String key:List.of("history_rewritten","old_operation_replayed","old_operation_outcome_changed","physical_occupancy_verified","hardware_qualified"))
            check(Boolean.FALSE.equals(receipt.get(key)),"Retry receipt declares "+key+" false");
        check(Boolean.TRUE.equals(map(receipt.get("verification")).get("native_wrapper_completed"))
            &&Boolean.TRUE.equals(map(receipt.get("verification")).get("native_wrapper_succeeded")),"New receipt requires its own successful actual native wrapper");
        Map<String,Object> firstIntervention=map(unknownTask.get("intervention")),secondIntervention=map(resolved.get("intervention"));
        check(firstIntervention.get("new_bindings").equals(secondIntervention.get("old_bindings")),"Second intervention begins from the exact source generation left by the failed first wrapper");
        check(!secondIntervention.get("old_bindings").equals(secondIntervention.get("new_bindings")),"New local capability performs another actual source generation replacement");
        check(Collections.disjoint(probeIds(unknownTask),probeIds(resolved)),"New recovery uses fresh native check, read, valve-on, and valve-off observation identities");
        check(taskEvents("sensing_reconciliation_verified",firstTask)==1&&taskEvents("sensing_reconciliation_verified",secondTask)==1
            &&taskEvents("sensing_reconciliation_resolved",firstTask)==0&&taskEvents("sensing_reconciliation_resolved",secondTask)==1,"Fresh success appends only its own resolution without rewriting the first unknown task");
        List<Map<String,Object>> rows=events();
        int firstTerminal=eventIndex(rows,"operation","operation_id",failedRecoveryId,"outcome_unknown"),firstUnknown=eventIndex(rows,"sensing_reconciliation_unknown","task_id",firstTask,null),
            secondCapture=eventIndex(rows,"sensing_reconciliation_task","task_id",secondTask,null),secondVerified=eventIndex(rows,"sensing_reconciliation_verified","task_id",secondTask,null),
            secondTerminal=eventIndex(rows,"operation","operation_id",secondRecoveryId,"succeeded"),secondResolved=eventIndex(rows,"sensing_reconciliation_resolved","task_id",secondTask,null);
        check(firstTerminal>=0&&firstTerminal<firstUnknown&&firstUnknown<secondCapture&&secondCapture<secondVerified&&secondVerified<secondTerminal&&secondTerminal<secondResolved,"Durable order retains first actual wrapper failure before fresh task, fresh verification, successful wrapper, and resolution");
        check(failedRecovery.equals(read("get_operation","operation_id",failedRecoveryId)),"First local operation remains exactly outcome_unknown after later current-scope disposition");
        Map<String,Object> oldView=read("get_sensing_reconciliation","task_id",firstTask);write("first-task-after-second-resolution.json",oldView);
        for(String key:List.of("task","state","intervention","verification","receipt","historical","live_resolution_activated"))
            check(Objects.equals(unknownTask.get(key),oldView.get(key)),"Original task retains its old "+key+" after fresh resolution");
        Map<String,Object> ready=read("get_status");write("ready-status.json",ready);
        check("observed_empty".equals(nozzleState(ready).get("state"))&&Boolean.FALSE.equals(nozzleState(ready).get("sticky_fault")),"Only the fresh successfully wrapped resolution grants current observed-empty readiness");
        Map<String,Object> measured=run("measure_sensor",command("nozzle_id",nozzle().getId(),"samples",2));
        check(((Number)result(measured).get("sample_count")).intValue()==2,"Ordinary public sensing succeeds with two fresh native samples after retry resolution");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());
        Map<String,Object> empty=run("verify_part_state",command("nozzle_id",nozzle().getId(),"state","part_off"));
        check(Boolean.TRUE.equals(result(empty).get("native_verdict")),"Ordinary native empty verification succeeds after retry resolution");
        Map<String,Object> prepared=run("prepare_job",command("canonical_job",canonical(config.getPart("R0603-1K"))));String jobId=(String)result(prepared).get("job_id");
        check(Boolean.TRUE.equals(result(run("validate_job",command("job_id",jobId))).get("valid")),"New ordinary job is freshly validated after retry resolution");
        Map<String,Object> completed=run("start_job",command("job_id",jobId));write("completed-new-job.json",completed);
        check(((Number)result(completed).get("placed")).intValue()==1&&feedCount()==initialFeeds+1&&adapter.job!=null,"Actual native processor completes exactly one new placement and consumes one tray position");
        check(events().stream().filter(row->"native_placement_checkpoint".equals(row.get("type"))&&"Job.Placement.Complete".equals(map(row.get("payload")).get("hook"))).count()==1,"Exactly one actual native placement Complete checkpoint follows the successful retry");
        run("set_machine_enabled",command("enabled",false));read("release_control_session","session_id",session,"request_id",UUID.randomUUID().toString());
        check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Final simulator state is idle and disabled with the public session released");
        check(original.equals(read("get_operation","operation_id",originalId))&&failedRecovery.equals(read("get_operation","operation_id",failedRecoveryId)),"Both original unknown outcomes remain byte-equivalent DTOs after the subsequent ordinary job");
        byte[] after=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        check(after.length>prior.length&&Arrays.equals(prior,Arrays.copyOf(after,prior.length)),"All journal bytes through the first failed recovery remain an exact append-only prefix");
        write("final-status.json",read("get_status"));
        proof.putAll(Bridge.map("original_sensor_operation_id",originalId,"failed_task_id",firstTask,"failed_recovery_operation_id",failedRecoveryId,
            "successful_task_id",secondTask,"successful_recovery_operation_id",secondRecoveryId,"new_job_id",jobId,"actual_simulator_placements",1,
            "injected_actual_wrapper_failures",adapter.injectedFailures.get(),"journal_before_retry_sha256",sha(prior),"journal_after_sha256",sha(after),
            "old_operations_unchanged",true,"old_journal_prefix_unchanged",true,"fresh_native_source_and_probe_required",true));
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=2)throw new IllegalArgumentException("Expected pinned sample root and new exclusive evidence state directory");
        state=Path.of(args[1]).toAbsolutePath();Files.createDirectory(state);int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try {
            Path configDir=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");Map<String,Object> prepared=GuiSensingFixture.prepare(configDir,manifest,"invalid-read");
            Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();
            Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),"invalid-read"));write("fixture-attestation.json",claimed);
            check(Boolean.TRUE.equals(claimed.get("sensing_fixture_attested")),"Fresh real native GUI-style sensing fixture is attested");
            Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);
            adapter=new Adapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));
            bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",adapter);
            exerciseRetry(proof);proof.put("passed",true);
        } catch(Throwable failure) {failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally {
            if(adapter!=null)adapter.releaseWrapper.countDown();
            try{if(bridge!=null)bridge.close();proof.put("bridge_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("bridge_close_failure",failure.toString());exit=1;}
            try{if(adapter!=null){adapter.release();check(!adapter.gate.owns(adapter.token),"Owned native guard is released during retry-test cleanup");}proof.put("native_gate_released",true);}catch(Throwable failure){failure.printStackTrace();proof.put("gate_release_failure",failure.toString());exit=1;}
            try{if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("machine_close_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);
            proof.putAll(Bridge.map("checks",checks,"assertions",checks.size(),"public_calls",calls,"pid",ProcessHandle.current().pid(),
                "source_scenario","invalid-read","actual_native_wrapper_failure_injected",true,"fabricated_native_observations",false,
                "adapter","non-Swing local adapter with real native ownership gate and native Future","mainframe_qualified",false,
                "crash_restart_qualified",false,"cancelled_wrapper_qualified",false,"material_disposal_qualified",false,"hardware_qualified",false));
            write("proof.json",proof);System.out.println("NATIVE_SENSING_RECOVERY_RETRY_RESULT "+JSON.toJson(proof));
        }
        System.exit(exit);
    }
}
