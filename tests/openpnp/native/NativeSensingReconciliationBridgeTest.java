/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.SwingUtilities;
import org.openpnp.machine.reference.ReferenceNozzle;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.base.*;

/** Public Bridge tools and its local callback, backed by an actual prepared native simulator
 * source and ownership gate. The local adapter is deliberately non-Swing: this test does not
 * qualify MainFrame, a human gesture, physical sensing, or recovery of a faulted job attempt. */
public final class NativeSensingReconciliationBridgeTest {
    static final Gson JSON=new GsonBuilder().serializeNulls().create();
    static final List<String> checks=Collections.synchronizedList(new ArrayList<>());
    static Configuration config;
    static Bridge bridge;
    static LocalAdapter local;
    static Path state;
    static String session;
    static int calls;

    static void check(boolean value,String label) {
        if(!value)throw new AssertionError(label);
        checks.add(label);
    }
    @SuppressWarnings("unchecked") static Map<String,Object> map(Object value) {
        return (Map<String,Object>)value;
    }
    static JsonObject object(Object... values) {
        return JSON.toJsonTree(plain(Bridge.map(values))).getAsJsonObject();
    }
    static Object plain(Object value) {
        if(value instanceof Map) {
            Map<String,Object> result=new LinkedHashMap<>();
            for(Map.Entry<?,?> entry:((Map<?,?>)value).entrySet())result.put((String)entry.getKey(),plain(entry.getValue()));
            return result;
        }
        if(value instanceof Iterable) {
            List<Object> result=new ArrayList<>();for(Object item:(Iterable<?>)value)result.add(plain(item));return result;
        }
        return value;
    }
    static Map<String,Object> copy(Object value) {
        return NativeJournalJson.parseObject(JSON.toJson(plain(value)));
    }
    static synchronized void recordCall(Map<String,Object> record)throws Exception {
        Files.writeString(state.resolve("calls.jsonl"),JSON.toJson(record)+"\n",StandardCharsets.UTF_8,
            StandardOpenOption.CREATE,StandardOpenOption.APPEND);
    }
    static Map<String,Object> call(String name,JsonObject arguments)throws Exception {
        Map<String,Object> record=Bridge.map("call",++calls,"method","openpnp_"+name,"arguments",copy(arguments));
        try {
            Map<String,Object> result=copy(bridge.call("openpnp_"+name,arguments));
            record.put("result",result);return result;
        } catch(Exception failure) {
            record.put("error",Bridge.map("class",failure.getClass().getName(),"message",failure.getMessage(),
                "code",failure instanceof Bridge.Fault?((Bridge.Fault)failure).code:null));throw failure;
        } finally {recordCall(record);}
    }
    static Map<String,Object> read(String name,Object... values)throws Exception {
        return call(name,object(values));
    }
    static JsonObject command(Object... values)throws Exception {
        JsonObject arguments=object(values);
        arguments.addProperty("session_id",session);
        arguments.addProperty("request_id",UUID.randomUUID().toString());
        arguments.addProperty("expected_config_revision",(String)read("get_status").get("config_revision"));
        return arguments;
    }
    static Map<String,Object> await(Map<String,Object> admission,String expected)throws Exception {
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(40);
        Map<String,Object> operation=admission;
        while(System.nanoTime()<deadline) {
            operation=read("get_operation","operation_id",admission.get("operation_id"));
            if(Set.of("succeeded","failed","aborted","outcome_unknown").contains(operation.get("state"))
                    && !config.getMachine().isBusy()) {
                if(!expected.equals(operation.get("state")))throw new AssertionError("Public "+operation.get("method")+" expected "+expected+": "+JSON.toJson(operation));
                check(true,"Public "+operation.get("method")+" reached "+expected);
                return operation;
            }
            Thread.sleep(10);
        }
        throw new AssertionError("Native operation did not settle within 40 seconds: "+JSON.toJson(operation));
    }
    static Map<String,Object> run(String name,JsonObject arguments)throws Exception {
        return await(call(name,arguments),"succeeded");
    }
    static Map<String,Object> result(Map<String,Object> operation) {return map(operation.get("result"));}
    static ReferenceNozzle nozzle()throws Exception {
        return (ReferenceNozzle)config.getMachine().getDefaultHead().getDefaultNozzle();
    }
    static Map<String,Object> nozzleState(Map<String,Object> status)throws Exception {
        for(Object raw:(List<?>)map(status.get("vacuum_sensing_journal")).get("nozzles"))
            if(nozzle().getId().equals(map(raw).get("nozzle_id")))return map(raw);
        throw new AssertionError("Default nozzle has no retained sensing state");
    }
    static <T>T edt(Callable<T> body)throws Exception {
        AtomicReference<T> value=new AtomicReference<>();AtomicReference<Throwable> error=new AtomicReference<>();
        SwingUtilities.invokeAndWait(()->{try{value.set(body.call());}catch(Throwable failure){error.set(failure);}});
        if(error.get()!=null) {if(error.get() instanceof Exception)throw (Exception)error.get();throw (Error)error.get();}
        return value.get();
    }
    static final class Presentation {
        final Map<String,Object> task;
        final GuiOwnership.SensingReconciliationSubmission callback;
        Presentation(Map<String,Object> task,GuiOwnership.SensingReconciliationSubmission callback) {
            this.task=copy(task);this.callback=callback;
        }
    }
    static final class LocalAdapter implements GuiOwnership {
        final AbstractMachine machine=(AbstractMachine)config.getMachine();
        final ExternalExecutionControl gate=machine.getExternalExecutionControl();
        final Object token;
        final Map<String,Object> provenance;
        final BlockingQueue<Presentation> presentations=new LinkedBlockingQueue<>();
        final List<String> dismissals=Collections.synchronizedList(new ArrayList<>());
        volatile boolean granted=true;
        volatile Job job;
        String displayedTask;
        LocalAdapter(Map<String,Object> provenance)throws Exception {
            this.provenance=copy(provenance);token=gate.claim("sensing79 non-Swing native Bridge test adapter");
        }
        public void checkAttachment(Configuration current,Machine attached)throws Exception {
            if(current!=config||attached!=machine)throw new Bridge.Fault("GUI_ATTACHMENT_CHANGED","Test fixture attachment changed");
            Bridge.verifyNativeSimulatorClasses(attached);
        }
        public void requireNativeOwnership()throws Exception {
            if(!gate.owns(token))throw new Bridge.Fault("LOCAL_GRANT_REQUIRED","Test adapter has lost native ownership");
        }
        public void requireRemoteGrant()throws Exception {
            requireNativeOwnership();if(!granted)throw new Bridge.Fault("OWNERSHIP_REVOKED","Test local grant was revoked");
        }
        public <T>T invokeNative(Callable<T> action)throws Exception {return gate.withOwner(token,action);}
        public <T>Future<T> submitNative(Callable<T> action,boolean ignoreEnabled)throws Exception {
            return gate.withOwner(token,()->machine.submit(action,null,ignoreEnabled));
        }
        public void publishJob(Job value) {
            check(machine.isTask(Thread.currentThread()),"Actual native job is published from the native executor");job=value;
        }
        public Map<String,Object> snapshot() {
            return Bridge.map("validation_only",true,"local_grant",granted,"native_ownership_held",gate.owns(token),
                "provenance",provenance,"swing_qualification",false);
        }
        public boolean supportsSensingReconciliation() {return true;}
        public synchronized void presentSensingReconciliation(Map<String,Object> task,SensingReconciliationSubmission callback) {
            if(displayedTask!=null)throw new IllegalStateException("Previous local task was not dismissed");
            displayedTask=(String)task.get("task_id");presentations.add(new Presentation(task,callback));
        }
        public synchronized void dismissSensingReconciliation(String id,String reason) {
            if(id.equals(displayedTask))displayedTask=null;dismissals.add(id+":"+reason);
        }
        boolean currentAuthority() {return granted&&gate.owns(token);}
        void release()throws Exception {if(gate.owns(token))gate.release(token);}
    }
    interface Action {void run()throws Exception;}
    static String refused(Action action,String expectedCode,String label)throws Exception {
        try {action.run();throw new AssertionError("Unexpected admission: "+label);}
        catch(Bridge.Fault failure) {
            check(expectedCode==null||expectedCode.equals(failure.code),label+"; refusal="+failure.code);return failure.code;
        } catch(ExecutionException failure) {
            Throwable cause=failure.getCause();
            if(expectedCode==null&&cause instanceof IllegalStateException&&"Local recovery is one-use".equals(cause.getMessage())) {
                check(true,label+"; exact consumed local callback refusal");return "IllegalStateException: Local recovery is one-use";
            }
            check(cause instanceof Bridge.Fault,label+" has a typed Bridge refusal: "+cause);
            String code=((Bridge.Fault)cause).code;
            check(expectedCode==null||expectedCode.equals(code),label+"; asynchronous refusal="+code);return code;
        }
    }
    static List<Map<String,Object>> events()throws Exception {
        List<Map<String,Object>> rows=new ArrayList<>();
        for(String line:Files.readAllLines(state.resolve("journal/operations.jsonl")))rows.add(NativeJournalJson.parseObject(line));
        return rows;
    }
    static long count(String type)throws Exception {return events().stream().filter(row->type.equals(row.get("type"))).count();}
    static long taskEvents(String type,String taskId)throws Exception {
        return events().stream().filter(row->type.equals(row.get("type"))&&taskId.equals(map(row.get("payload")).get("task_id"))).count();
    }
    static int eventIndex(List<Map<String,Object>> rows,String type,String key,Object value,String stateValue) {
        for(int index=0;index<rows.size();index++) {
            Map<String,Object> row=rows.get(index),payload=map(row.get("payload"));
            if(type.equals(row.get("type"))&&Objects.equals(value,payload.get(key))
                && (stateValue==null||stateValue.equals(payload.get("state"))))return index;
        }
        return -1;
    }
    static String sha(byte[] bytes)throws Exception {
        StringBuilder out=new StringBuilder();for(byte value:MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format("%02x",value));return out.toString();
    }
    static void write(String name,Object value)throws Exception {
        Files.writeString(state.resolve(name),JSON.toJson(value)+"\n",StandardOpenOption.CREATE_NEW);
    }
    static long feedCount() {
        long total=0;for(Feeder feeder:config.getMachine().getFeeders())if(feeder instanceof ReferenceTrayFeeder)total+=((ReferenceTrayFeeder)feeder).getFeedCount();return total;
    }
    static JsonObject canonical(Part part) {
        double height=part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();
        return object("schemaVersion",1,"id","sensing79-fresh-ordinary-job","units","mm","coordinateConvention","openpnp-top-view",
            "parts",List.of(Bridge.map("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height)),
            "boards",List.of(Bridge.map("id","board","widthMm",20,"heightMm",20,"placements",List.of(Bridge.map(
                "ref","R1","partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height,
                "x",10,"y",10,"z",0,"rotation",0,"side","top","enabled",true,"type","placement")))),
            "panels",List.of(),"instances",List.of(Bridge.map("id","instance","kind","board","definitionId","board",
                "x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true)));
    }
    static void exercise(Map<String,Object> proof)throws Exception {
        Map<String,Object> capabilities=read("get_capabilities");write("capabilities.json",capabilities);
        check(Boolean.TRUE.equals(map(capabilities.get("vacuum_sensing")).get("available")),"Actual prepared GUI-style native source is available");
        session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());
        JsonObject failedRequest=command("nozzle_id",nozzle().getId(),"samples",2);
        Map<String,Object> failed=await(call("measure_sensor",failedRequest),"outcome_unknown");
        String failedId=(String)failed.get("operation_id");write("original-failed-operation.json",failed);
        Map<String,Object> faultStatus=read("get_status");write("fault-status.json",faultStatus);
        check(Boolean.TRUE.equals(nozzleState(faultStatus).get("sticky_fault")),"Invalid native read leaves a sticky current sensing fault");
        long observations=count("vacuum_observation_intent"),effects=count("native_effect_intent");
        refused(()->call("measure_sensor",command("nozzle_id",nozzle().getId(),"samples",1)),"VACUUM_OUTCOME_UNKNOWN","Ordinary measurement refuses the retained fault");
        refused(()->call("home_machine",command()),"VACUUM_OUTCOME_UNKNOWN","Ordinary homing refuses the retained fault");
        check(observations==count("vacuum_observation_intent")&&effects==count("native_effect_intent"),"Refused ordinary work creates no native read or machine effect intent");
        run("set_machine_enabled",command("enabled",false));
        check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Public safe disable leaves the native machine idle and disabled");
        check(Boolean.TRUE.equals(nozzleState(read("get_status")).get("sticky_fault")),"Disable preserves the unresolved sensing fault");
        byte[] priorJournal=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        Files.write(state.resolve("journal-before-reconciliation.jsonl"),priorJournal,StandardOpenOption.CREATE_NEW);
        long initialFeeds=feedCount();
        run("request_sensing_reconciliation",command("recovery_kind","restore-sensing-readiness"));
        Presentation stale=local.presentations.poll(10,TimeUnit.SECONDS);
        check(stale!=null,"First local recovery request reaches the adapter for authority rejection");
        String staleId=(String)stale.task.get("task_id");write("stale-presented-task.json",stale.task);
        long beforeStaleReads=count("vacuum_observation_intent"),beforeStaleEffects=count("sensing_recovery_native_intent");
        proof.put("stale_authority_refusal",refused(()->stale.callback.submit(()->false).toCompletableFuture().get(10,TimeUnit.SECONDS),
            "SENSING_RECOVERY_AUTHORITY_STALE","Local authority rejection refuses recovery admission"));
        Map<String,Object> staleView=read("get_sensing_reconciliation","task_id",staleId);write("stale-closed-task.json",staleView);
        check("scope_stale".equals(staleView.get("state"))&&!Boolean.TRUE.equals(staleView.get("task_active")),"Rejected local authority closes the exact task as scope_stale");
        check(staleView.get("recovery_operation_id")==null&&taskEvents("sensing_reconciliation_intent",staleId)==0
            &&taskEvents("sensing_reconciliation_closed",staleId)==1,"Rejected callback retains one closed task without a recovery operation or intervention intent");
        check(beforeStaleReads==count("vacuum_observation_intent")&&beforeStaleEffects==count("sensing_recovery_native_intent")
            &&feedCount()==initialFeeds,"Rejected callback performs no native read, recovery effect, or feeder consumption");
        check(Boolean.TRUE.equals(nozzleState(read("get_status")).get("sticky_fault")),"Rejected callback preserves the original unresolved fault");
        check(local.dismissals.stream().anyMatch(value->value.startsWith(staleId+":"))&&local.displayedTask==null,"Bridge dismisses the rejected local request");
        refused(()->stale.callback.submit(local::currentAuthority).toCompletableFuture().get(10,TimeUnit.SECONDS),null,"Closed stale callback cannot be reused after authority returns");
        JsonObject recoveryRequest=command("recovery_kind","restore-sensing-readiness");
        Map<String,Object> request=run("request_sensing_reconciliation",recoveryRequest);
        Map<String,Object> replay=call("request_sensing_reconciliation",recoveryRequest);
        check(request.get("operation_id").equals(replay.get("operation_id")),"Exact request replay reattaches to its original public admission");
        Presentation shown=local.presentations.poll(10,TimeUnit.SECONDS);
        check(shown!=null,"Bridge delivers the local callback through GuiOwnership");
        check(!staleId.equals(shown.task.get("task_id")),"A fresh public request is available after rejected local authority");
        check(local.presentations.isEmpty(),"Replayed remote request creates no second local presentation");
        String taskId=(String)shown.task.get("task_id");write("presented-task.json",shown.task);
        Map<String,Object> pending=read("get_sensing_reconciliation","task_id",taskId);write("pending-task.json",pending);
        check("awaiting_local_action".equals(pending.get("state"))&&Boolean.TRUE.equals(pending.get("task_active")),"Read-only task view reports an active local decision");
        Map<String,Object> record=map(pending.get("task")),scope=map(shown.task.get("snapshot"));
        check(request.get("operation_id").equals(record.get("request_operation_id")),"Local task binds the exact public request operation");
        check(shown.task.get("fault_set_sha256").equals(record.get("fault_set_sha256")),"Local view binds the durable complete fault digest");
        check(scope.get("context") instanceof Map&&scope.get("faults") instanceof List&&!((List<?>)scope.get("faults")).isEmpty()
            &&scope.get("proposed_effects") instanceof List&&!((List<?>)scope.get("proposed_effects")).isEmpty(),"Local view exposes exact context, fault rows, and proposed effects");
        check(taskEvents("sensing_reconciliation_task",taskId)==1&&taskEvents("sensing_reconciliation_intent",taskId)==0,"Remote request only creates one pending task; no local intervention intent");
        check(feedCount()==initialFeeds&&Boolean.TRUE.equals(nozzleState(read("get_status")).get("sticky_fault")),"Pending local task neither consumes feeder stock nor clears the sensing fault");
        CompletionStage<Map<String,Object>> future=shown.callback.submit(local::currentAuthority);
        check(future!=null,"Local callback returns an asynchronous completion");
        Map<String,Object> callbackResult=future.toCompletableFuture().get(40,TimeUnit.SECONDS);write("local-callback-result.json",callbackResult);
        Map<String,Object> resolved=read("get_sensing_reconciliation","task_id",taskId);write("resolved-task.json",resolved);
        check("resolved_for_current_scope".equals(resolved.get("state"))&&Boolean.TRUE.equals(resolved.get("live_resolution_activated")),"Local intervention and native verification activate current scoped readiness");
        check(Boolean.FALSE.equals(resolved.get("execution_authority_restored")),"Reconciliation receipt itself grants no execution authority");
        Map<String,Object> receipt=map(resolved.get("receipt")),verification=map(receipt.get("verification")),intervention=map(receipt.get("intervention"));
        check(((List<?>)receipt.get("original_operation_ids")).contains(failedId),"Receipt retains the original failed operation identity");
        for(String field:List.of("history_rewritten","old_operation_replayed","old_operation_outcome_changed","physical_occupancy_verified","hardware_qualified"))
            check(Boolean.FALSE.equals(receipt.get(field)),"Receipt declares "+field+" false");
        check(Boolean.TRUE.equals(receipt.get("simulation_only")),"Receipt identifies its simulator-only scope");
        check(Boolean.TRUE.equals(verification.get("native_wrapper_completed"))&&Boolean.TRUE.equals(verification.get("native_wrapper_succeeded")),"Receipt binds actual successful native wrapper completion");
        check(intervention.get("synthetic_latch_before").equals(intervention.get("synthetic_latch_after")),"Standalone source repair does not dispose of or change synthetic nozzle material");
        List<?> oldBindings=(List<?>)intervention.get("old_bindings"),newBindings=(List<?>)intervention.get("new_bindings");
        check(!oldBindings.isEmpty()&&oldBindings.size()==newBindings.size(),"Source intervention covers the complete captured nozzle binding set");
        for(int index=0;index<oldBindings.size();index++) {
            Map<String,Object> before=map(oldBindings.get(index)),after=map(newBindings.get(index));
            check(!map(before.get("source")).get("source_id").equals(map(after.get("source")).get("source_id")),"Successful local repair creates a fresh native source generation");
            for(String field:List.of("machine_id","bridge_instance_id","config_revision","nozzle_id","nozzle_tip_id","sensor_id"))
                check(Objects.equals(before.get(field),after.get(field)),"Source repair preserves "+field);
        }
        List<?> probes=(List<?>)verification.get("probes");check(probes.size()==newBindings.size(),"Native part-off verification covers every shared-source nozzle");
        for(Object raw:probes) {
            Map<String,Object> probe=map(raw);check(Boolean.TRUE.equals(probe.get("native_verdict"))&&!((List<?>)probe.get("read_observation_ids")).isEmpty(),"Each native part-off verdict binds finite actual sensor reads");
            check(probe.get("valve_on_id")!=null&&probe.get("valve_off_id")!=null,"Each native check retains both valve actuation receipts");
        }
        String recoveryId=(String)resolved.get("recovery_operation_id");
        Map<String,Object> recoveryOperation=read("get_operation","operation_id",recoveryId);write("recovery-operation.json",recoveryOperation);
        check("succeeded".equals(recoveryOperation.get("state"))&&Boolean.TRUE.equals(map(recoveryOperation.get("native_completion")).get("native_wrapper_completed")),"Recovery has its own forced successful native operation");
        check(!recoveryId.equals(failedId)&&!recoveryId.equals(request.get("operation_id")),"Local native recovery has a separate operation identity");
        List<Map<String,Object>> rows=events();
        int taskAt=eventIndex(rows,"sensing_reconciliation_task","task_id",taskId,null),intentAt=eventIndex(rows,"sensing_reconciliation_intent","task_id",taskId,null),
            interventionAt=eventIndex(rows,"sensing_reconciliation_intervention","task_id",taskId,null),verifiedAt=eventIndex(rows,"sensing_reconciliation_verified","task_id",taskId,null),
            terminalAt=eventIndex(rows,"operation","operation_id",recoveryId,"succeeded"),resolvedAt=eventIndex(rows,"sensing_reconciliation_resolved","task_id",taskId,null);
        check(taskAt>=0&&taskAt<intentAt&&intentAt<interventionAt&&interventionAt<verifiedAt&&verifiedAt<terminalAt&&terminalAt<resolvedAt,"Journal orders local task, intent, intervention, probe, actual terminal operation, then resolved receipt");
        check(feedCount()==initialFeeds&&count("native_action_intent")==0,"Standalone reconciliation performs no feed, pick, alignment, release, or placement job action");
        check(Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Native verification finishes with the nozzle valve off");
        Map<String,Object> ready=read("get_status");write("reconciled-status.json",ready);
        check("observed_empty".equals(nozzleState(ready).get("state"))&&Boolean.FALSE.equals(nozzleState(ready).get("sticky_fault")),"Current nozzle readiness is freshly observed empty with no live sticky fault");
        check(Boolean.TRUE.equals(nozzleState(ready).get("recorded_sticky_fault")),"Readback preserves the historical recorded fault after current disposition");
        check(failed.equals(read("get_operation","operation_id",failedId)),"Original failed operation, result, request, and native completion remain unchanged");
        long recoveryIntents=taskEvents("sensing_reconciliation_intent",taskId),resolvedCount=taskEvents("sensing_reconciliation_resolved",taskId),readIntents=count("vacuum_observation_intent");
        proof.put("duplicate_callback_refusal",refused(()->shown.callback.submit(local::currentAuthority).toCompletableFuture().get(10,TimeUnit.SECONDS),null,"Consumed local callback rejects a duplicate invocation"));
        check(recoveryIntents==1&&resolvedCount==1&&taskEvents("sensing_reconciliation_intent",taskId)==1&&taskEvents("sensing_reconciliation_resolved",taskId)==1
            &&readIntents==count("vacuum_observation_intent"),"Duplicate local callback creates no new recovery, receipt, or native read");
        check(local.dismissals.stream().anyMatch(value->value.startsWith(taskId+":")),"Bridge dismisses the consumed local presentation");
        Map<String,Object> measured=run("measure_sensor",command("nozzle_id",nozzle().getId(),"samples",2));
        check(((Number)result(measured).get("sample_count")).intValue()==2,"Ordinary public sensing returns two fresh actual native samples after recovery");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());
        Map<String,Object> off=run("verify_part_state",command("nozzle_id",nozzle().getId(),"state","part_off"));
        check(Boolean.TRUE.equals(result(off).get("native_verdict")),"Ordinary public native part-off check succeeds under fresh authorization");
        Map<String,Object> prepared=run("prepare_job",command("canonical_job",canonical(config.getPart("R0603-1K"))));
        String jobId=(String)result(prepared).get("job_id");
        Map<String,Object> validated=run("validate_job",command("job_id",jobId));
        check(Boolean.TRUE.equals(result(validated).get("valid")),"New ordinary one-placement job receives fresh native validation");
        Map<String,Object> completed=run("start_job",command("job_id",jobId));write("fresh-completed-job.json",completed);
        check(((Number)result(completed).get("placed")).intValue()==1&&"completed".equals(read("get_status").get("job_state")),"Actual native job processor completes exactly one new placement");
        check(local.job!=null&&feedCount()==initialFeeds+1,"New job consumes exactly one tray position through the native processor");
        check(events().stream().filter(row->"native_placement_checkpoint".equals(row.get("type"))&&"Job.Placement.Complete".equals(map(row.get("payload")).get("hook"))).count()==1,"Exactly one native placement Complete checkpoint is retained");
        run("set_machine_enabled",command("enabled",false));
        read("release_control_session","session_id",session,"request_id",UUID.randomUUID().toString());
        check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Final native state is idle and disabled after public session release");
        check(failed.equals(read("get_operation","operation_id",failedId)),"Subsequent ordinary job and session release still preserve the original failed operation");
        byte[] finalJournal=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        check(finalJournal.length>priorJournal.length&&Arrays.equals(priorJournal,Arrays.copyOf(finalJournal,priorJournal.length)),"Every pre-reconciliation journal byte remains an exact append-only prefix");
        write("final-status.json",read("get_status"));
        proof.putAll(Bridge.map("task_id",taskId,"original_operation_id",failedId,"recovery_operation_id",recoveryId,"new_job_id",jobId,
            "journal_before_sha256",sha(priorJournal),"journal_after_sha256",sha(finalJournal),"actual_simulator_placements",1,
            "source_generation_changed",true,"native_wrapper_verified",true,"append_only_history_preserved",true));
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=2)throw new IllegalArgumentException("Expected pinned sample root and new exclusive evidence state directory");
        state=Path.of(args[1]).toAbsolutePath();Files.createDirectory(state);
        int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try {
            Path configDir=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");
            Map<String,Object> prepared=GuiSensingFixture.prepare(configDir,manifest,"invalid-read");
            Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();
            Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),"invalid-read"));
            write("fixture-attestation.json",claimed);
            check(Boolean.TRUE.equals(claimed.get("sensing_fixture_attested")),"Exact prepared native GUI-style fixture is attested in the current process");
            Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);
            local=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));
            bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",local);
            exercise(proof);proof.put("passed",true);
        } catch(Throwable failure) {
            failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;
        } finally {
            try {if(bridge!=null)bridge.close();proof.put("bridge_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("bridge_close_failure",failure.toString());exit=1;}
            try {if(local!=null){local.release();check(!local.gate.owns(local.token),"Owned native guard is released during cleanup");}proof.put("native_gate_released",true);}catch(Throwable failure){failure.printStackTrace();proof.put("gate_release_failure",failure.toString());exit=1;}
            try {if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("machine_close_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);
            proof.putAll(Bridge.map("checks",checks,"assertions",checks.size(),"public_calls",calls,"pid",ProcessHandle.current().pid(),
                "synthetic_source_scenario","invalid-read","local_adapter","actual native gate; non-Swing validation callback",
                "swing_qualification",false,"mainframe_qualification",false,"hardware_qualified",false,"faulted_job_replacement_qualified",false));
            write("proof.json",proof);System.out.println("NATIVE_SENSING_RECONCILIATION_BRIDGE_RESULT "+JSON.toJson(proof));
        }
        System.exit(exit);
    }
}
