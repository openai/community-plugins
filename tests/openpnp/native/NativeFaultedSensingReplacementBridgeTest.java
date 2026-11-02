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
 * qualify MainFrame, a human gesture, physical sensing. It proves faulted simulator job replacement through the public tools. */
public final class NativeFaultedSensingReplacementBridgeTest {
    static final Gson JSON=new GsonBuilder().serializeNulls().create();
    static final List<String> checks=Collections.synchronizedList(new ArrayList<>());
    static Configuration config;
    static Bridge bridge;
    static LocalAdapter local;
    static Path state;
    static String session;
    static int calls;
    static String scenario="retained-after-place";

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
        // Fiducial coordinates and nominal board origin match the pinned ImageCamera pnp-test image.
        // The importer requires positive height; this explicit simulator fiducial uses the canonical stock footprint.
        Part stockFiducial=config.getPart("FIDUCIAL-1X2-FIDUCIAL1X2");String fid="S79-SIM-FIDUCIAL";
        List<Object> placements=new ArrayList<>();
        placements.add(Bridge.map("ref","R1","partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height,
            "x",6,"y",6,"z",0,"rotation",0,"side","top","enabled",true,"type","placement"));
        double[][] coordinates={{2,2},{34.5,26.5},{34.5,2}};
        for(int i=0;i<coordinates.length;i++)placements.add(Bridge.map("ref","FID"+(i+1),"partId",fid,"packageId",stockFiducial.getPackage().getId(),"heightMm",0.001,
            "x",coordinates[i][0],"y",coordinates[i][1],"z",0,"rotation",0,"side","top","enabled",true,"type","fiducial"));
        return object("schemaVersion",1,"id","sensing79-faulted-replacement-job","units","mm","coordinateConvention","openpnp-top-view",
            "parts",List.of(Bridge.map("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height),
                Bridge.map("id",fid,"packageId",stockFiducial.getPackage().getId(),"heightMm",0.001)),
            "boards",List.of(Bridge.map("id","board","widthMm",37,"heightMm",30,"placements",placements)),
            "panels",List.of(),"instances",List.of(Bridge.map("id","instance","kind","board","definitionId","board",
                "x",3.994594817218938,"y",4.4131849081484384,"z",0,"rotation",0.007894774268749627,"side","top","enabled",true)));
    }
    static Set<String> ids(List<?> rows,String field) {
        Set<String> result=new TreeSet<>();for(Object row:rows)result.add((String)map(row).get(field));return result;
    }
    static Map<String,Object> load(Map<String,Object> snapshot,String id) {
        for(Object row:(List<?>)snapshot.get("loads"))if(id.equals(map(row).get("load_id")))return map(row);
        throw new AssertionError("Missing retained load "+id);
    }
    static List<Map<String,Object>> originalActions(String id)throws Exception {
        List<Map<String,Object>> result=new ArrayList<>();for(Map<String,Object> row:events())
            if(((String)row.get("type")).startsWith("native_action_")&&id.equals(map(row.get("payload")).get("operation_id")))result.add(row);
        return result;
    }
    static void exercise(Map<String,Object> proof)throws Exception {
        Map<String,Object> capabilities=read("get_capabilities");write("capabilities.json",capabilities);
        check(Boolean.TRUE.equals(map(capabilities.get("vacuum_sensing")).get("available")),"Prepared native GUI sensing source is available");
        check(Boolean.TRUE.equals(map(capabilities.get("material_loads")).get("available")),"GUI sensing profile advertises finite native material enrollment");
        session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
        Map<String,Object> configuration=read("get_configuration"),settings=map(configuration.get("settings")),traySettings=null;
        for(Object row:(List<?>)settings.get("feeders"))if("R0603-1K".equals(map(row).get("part_id")))traySettings=map(row);
        check(traySettings!=null,"Public native settings identify the resistor supply tray");
        Map<String,Object> geometry=Bridge.map("type","set_tray_feeder_geometry","feeder_id",traySettings.get("feeder_id"),
            "location",traySettings.get("location"),"count_x",2,"count_y",2,"pitch_x_mm",2,"pitch_y_mm",2,"feed_count",0);
        Map<String,Object> setupPlan=call("plan_configuration",command("changes",List.of(geometry)));
        run("apply_configuration",command("plan_id",setupPlan.get("plan_id")));
        Map<String,Object> inventory=read("get_material_loads");write("initial-material-inventory.json",inventory);
        Map<String,Object> tray=null;
        for(Object row:(List<?>)inventory.get("available_trays"))if(Boolean.TRUE.equals(map(row).get("supported"))&&"R0603-1K".equals(map(row).get("part_id")))tray=map(row);
        check(tray!=null,"Prepared native fixture provides a supported finite resistor tray");
        String feederId=(String)tray.get("feeder_id");ReferenceTrayFeeder nativeTray=(ReferenceTrayFeeder)config.getMachine().getFeeder(feederId);
        check(!config.getMachine().isEnabled(),"Finite native tray is enrolled while disabled");
        Map<String,Object> registered=run("register_material_load",command("feeder_id",feederId,"part_id",tray.get("part_id"),
            "expected_geometry_sha256",tray.get("geometry_sha256"),"expected_material_revision",inventory.get("material_setup_revision"),"action","bind-existing"));
        String oldMaterial=(String)map(result(registered).get("load")).get("load_id");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());
        Map<String,Object> prepared=run("prepare_job",command("canonical_job",canonical(config.getPart("R0603-1K"))));
        String oldJobId=(String)result(prepared).get("job_id");Job oldJob=local.job;
        check(oldJob!=null&&oldJob.getBoardLocations().size()==1,"Bridge publishes one actual native board job");
        BoardLocation oldBoard=oldJob.getBoardLocations().get(0);Board oldDefinition=oldBoard.getBoard();
        Map<String,Object> validated=run("validate_job",command("job_id",oldJobId));
        check(Boolean.TRUE.equals(result(validated).get("valid")),"Original finite-tray job passes actual native preflight");
        Map<String,Object> failed=await(call("start_job",command("job_id",oldJobId)),"outcome_unknown");
        String original=(String)failed.get("operation_id");write("original-failed-operation.json",failed);
        check(Boolean.TRUE.equals(map(failed.get("native_completion")).get("native_wrapper_completed")),"Original faulted native task wrapper really completed");
        Map<String,Object> history=copy(oldJob.getPlacedStatusSnapshot()),faultStatus=read("get_status");
        List<Map<String,Object>> oldActions=originalActions(original);
        check(!oldActions.isEmpty(),"Original fault contains real native action history");
        if(scenario.equals("lost-before-place"))check("not_detected".equals(nozzleState(faultStatus).get("state"))
            &&Boolean.FALSE.equals(map(map(nozzleState(faultStatus).get("last_record")).get("data")).get("verdict")),"Native lost-part check retains its known negative verdict");
        else check(Boolean.TRUE.equals(nozzleState(faultStatus).get("sticky_fault")),"Original job retains a sticky sensing fault");
        check("outcome_unknown".equals(faultStatus.get("job_state")),"Native job outcome remains explicitly unknown");
        long oldFeeds=feedCount(),actionCount=count("native_action_intent"),placementCount=count("native_placement_checkpoint");
        if(scenario.equals("retained-after-place"))check(nozzle().getPart()==null&&oldFeeds==1,"Native release cleared model Part after one feed, while sensing retains material");
        run("set_machine_enabled",command("enabled",false));
        check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Faulted native simulator is safely disabled and idle");
        Map<String,Object> before=read("get_status"),oldMaterials=read("get_material_loads"),oldBoards=read("get_board_loads");
        Map<String,Object> oldMaterialRecord=load(oldMaterials,oldMaterial);Set<String> oldBoardIds=ids((List<?>)oldBoards.get("roots"),"load_id");
        byte[] prior=Files.readAllBytes(state.resolve("journal/operations.jsonl"));Files.write(state.resolve("journal-before-reconciliation.jsonl"),prior,StandardOpenOption.CREATE_NEW);
        JsonObject recoveryRequest=command("recovery_kind","replace-faulted-job-attempt","job_id",oldJobId,
            "expected_job_revision",before.get("job_revision"),"expected_board_load_revision",before.get("board_load_revision"),
            "expected_material_revision",before.get("material_setup_revision"),"original_operation_id",original);
        Map<String,Object> request=run("request_sensing_reconciliation",recoveryRequest);
        Presentation shown=local.presentations.poll(10,TimeUnit.SECONDS);check(shown!=null,"Exact replacement request reaches the process-owned local callback");
        String taskId=(String)shown.task.get("task_id");write("presented-task.json",shown.task);
        Map<String,Object> scope=map(shown.task.get("snapshot")),capture=map(scope.get("replacement")),dependencies=map(capture.get("dependencies"));
        check(original.equals(capture.get("original_operation_id"))&&oldJobId.equals(capture.get("job_id")),"Local replacement capture binds the exact old job and failed native operation");
        check(((List<?>)dependencies.get("operation_ids")).contains(original)&&((List<?>)dependencies.get("job_attempt_ids")).contains(oldJobId),"Captured union contains the original operation and native job attempt");
        check(new TreeSet<>((List<String>)dependencies.get("material_load_ids")).equals(Set.of(oldMaterial)),"Captured material union exactly contains the consumed finite tray load");
        check(new TreeSet<>((List<String>)dependencies.get("board_load_ids")).equals(oldBoardIds),"Captured board union exactly contains original native board loads");
        Set<String> actionIds=new TreeSet<>();for(Map<String,Object> row:oldActions)if("native_action_intent".equals(row.get("type")))actionIds.add((String)map(row.get("payload")).get("action_id"));
        check(new TreeSet<>((List<String>)dependencies.get("action_ids")).equals(actionIds),"Captured action union uses exact native action identifiers");
        check(feedCount()==oldFeeds&&count("native_action_intent")==actionCount&&taskEvents("sensing_reconciliation_intent",taskId)==0,"Remote request performs no feed, job action, or local intervention");
        Map<String,Object> replayRequest=call("request_sensing_reconciliation",recoveryRequest);
        check(request.get("operation_id").equals(replayRequest.get("operation_id"))&&local.presentations.isEmpty(),"Duplicate remote request returns its original receipt without another local presentation");
        Map<String,Object> callback=shown.callback.submit(local::currentAuthority).toCompletableFuture().get(60,TimeUnit.SECONDS);write("local-callback-result.json",callback);
        Map<String,Object> resolved=read("get_sensing_reconciliation","task_id",taskId);write("resolved-task.json",resolved);
        check("resolved_for_current_scope".equals(resolved.get("state"))&&Boolean.TRUE.equals(resolved.get("live_resolution_activated")),"Actual local disposal, replacement and probe resolve the captured current scope");
        Map<String,Object> receipt=map(resolved.get("receipt")),verification=map(receipt.get("verification")),intervention=map(receipt.get("intervention"));
        check(Boolean.FALSE.equals(resolved.get("execution_authority_restored")),"Replacement receipt grants no execution authority");
        check(Boolean.TRUE.equals(verification.get("native_wrapper_completed"))&&Boolean.TRUE.equals(verification.get("native_wrapper_succeeded")),"Resolution follows an actual successful native wrapper");
        for(String field:List.of("history_rewritten","old_operation_replayed","old_operation_outcome_changed","physical_occupancy_verified","hardware_qualified"))check(Boolean.FALSE.equals(receipt.get(field)),"Receipt declares "+field+" false");
        Map<String,Object> latchBefore=map(intervention.get("synthetic_latch_before")),latchAfter=map(intervention.get("synthetic_latch_after"));
        check(!"empty".equals(latchBefore.get("state"))&&"empty".equals(latchAfter.get("state")),"Native discard resolves explicit retained, lost, or unknown source material");
        check(original.equals(latchBefore.get("original_operation_id")),"Material latch retains its original native operation provenance");
        check((scenario.equals("retained-after-place")?"retained":scenario.equals("lost-before-place")?"lost":"unknown").equals(latchBefore.get("state")),"Recovery preserves the exact original material state category");
        List<?> pendingIds=(List<?>)map(scope.get("source")).get("pending_observation_ids");
        if(scenario.equals("invalid-read"))check(!pendingIds.isEmpty(),"Invalid native job read leaves actual pending source check bookkeeping");
        List<Map<String,Object>> retiredScopes=new ArrayList<>();
        for(Map<String,Object> row:events())if("sensing_source_scope_retirement_returned".equals(row.get("type"))&&taskId.equals(map(row.get("payload")).get("task_id")))retiredScopes.add(map(map(row.get("payload")).get("facts")));
        if(!pendingIds.isEmpty()) {
            check(retiredScopes.size()==1&&new TreeSet<>((List<String>)retiredScopes.get(0).get("pending_observation_ids")).equals(new TreeSet<>((List<String>)pendingIds)),"One native terminal-scope retirement names the entire exact original pending observation set");
            check(Boolean.FALSE.equals(retiredScopes.get(0).get("original_outcomes_known"))&&Boolean.FALSE.equals(retiredScopes.get(0).get("occupancy_cleared")),"Pending scope retirement preserves unknown outcomes and cannot clear material occupancy");
        } else check(retiredScopes.isEmpty(),"Completed original observation scope needs no fabricated pending retirement");
        List<?> oldBindings=(List<?>)intervention.get("old_bindings"),newBindings=(List<?>)intervention.get("new_bindings");
        check(!oldBindings.isEmpty()&&oldBindings.size()==newBindings.size()&&((List<?>)verification.get("probes")).size()==newBindings.size(),"Fresh native part-off probes cover every shared-source nozzle");
        for(int i=0;i<oldBindings.size();i++)check(!map(map(oldBindings.get(i)).get("source")).get("source_id").equals(map(map(newBindings.get(i)).get("source")).get("source_id")),"Every nozzle binding uses a new source generation");
        check(count("sensing_source_disposal_returned")==newBindings.size(),"Each affected native nozzle completes the bounded discard geometry and release");
        check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy()&&nozzle().getPart()==null,"Replacement callback finishes native machine disabled, idle, and model-empty");
        check(Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Native disposal and probe finish with the vacuum valve off");
        check(failed.equals(read("get_operation","operation_id",original))&&oldActions.equals(originalActions(original)),"Original operation and native actions remain byte-equivalent JSON records");
        check(history.equals(copy(oldJob.getPlacedStatusSnapshot())),"Original native board placed history is unchanged");
        Map<String,Object> ready=read("get_status");write("replacement-status.json",ready);
        String newJobId=(String)ready.get("job_id");Job fresh=local.job;
        check(!oldJobId.equals(newJobId)&&fresh!=oldJob&&fresh!=null,"Replacement publishes a distinct native job and attempt identity");
        check("prepared".equals(ready.get("job_state"))&&fresh.getPlacedStatusSnapshot().isEmpty(),"Replacement starts prepared with empty native placed history");
        BoardLocation newBoard=fresh.getBoardLocations().get(0);Board newDefinition=newBoard.getBoard();
        check(newBoard!=oldBoard&&newDefinition!=oldDefinition&&newDefinition.getPlacements().get(0)!=oldDefinition.getPlacements().get(0),"Replacement native board location, definition and placement objects are disjoint");
        check(newDefinition.getFile()==null,"Detached replacement native definition cannot overwrite an old file binding");
        Map<String,Object> newBoards=read("get_board_loads"),newMaterials=read("get_material_loads");write("replacement-board-loads.json",newBoards);write("replacement-material-loads.json",newMaterials);
        Set<String> newBoardIds=ids((List<?>)newBoards.get("roots"),"load_id");check(Collections.disjoint(oldBoardIds,newBoardIds),"Replacement binds distinct native board load identities");
        check(((Map<?,?>)newBoards.get("quarantined_jobs")).containsKey(oldJobId),"Original board job remains quarantined");
        for(String id:oldBoardIds)check(((Map<?,?>)newBoards.get("quarantined_loads")).containsKey(id),"Original uncertain board load remains quarantined");
        Set<String> activeMaterial=new TreeSet<>();for(Object row:(List<?>)newMaterials.get("loads"))if(Boolean.TRUE.equals(map(row).get("active")))activeMaterial.add((String)map(row).get("load_id"));
        check(activeMaterial.size()==1&&!activeMaterial.contains(oldMaterial),"Replacement binds exactly one distinct full native tray load");
        String newMaterial=activeMaterial.iterator().next();Map<String,Object> retired=load(newMaterials,oldMaterial),freshMaterial=load(newMaterials,newMaterial);
        check(Boolean.FALSE.equals(retired.get("active"))&&retired.get("retired_by")!=null,"Original consumed or unknown tray load is retained as retired");
        for(String field:List.of("current_index","observed_advances","state","initial_index","created_at"))check(Objects.equals(oldMaterialRecord.get(field),retired.get(field)),"Old material "+field+" is preserved");
        check(((Number)freshMaterial.get("current_index")).intValue()==0&&nativeTray.getFeedCount()==0,"Authorized full-tray replacement initializes only the fresh active load");
        check(count("native_action_intent")==actionCount&&count("native_placement_checkpoint")==placementCount,"Replacement performs no old job action or placement replay");
        Map<String,Object> disposition=map(receipt.get("dispositions"));
        check("disposed-synthetic".equals(disposition.get("nozzle_material"))&&((List<?>)disposition.get("unresolved_dependencies")).isEmpty(),"Receipt separates explicit nozzle disposal from completed material and board replacement");
        check(ids((List<?>)disposition.get("feeder_loads"),"old_load_id").equals(Set.of(oldMaterial))&&ids((List<?>)disposition.get("board_loads"),"old_load_id").equals(oldBoardIds),"Durable subreceipts disposition the complete captured old load union");
        String recoveryId=(String)resolved.get("recovery_operation_id");
        List<Map<String,Object>> ordered=events();
        int intentAt=eventIndex(ordered,"sensing_reconciliation_intent","task_id",taskId,null),
            disposalAt=eventIndex(ordered,"sensing_source_disposal_returned","task_id",taskId,null),
            interventionAt=eventIndex(ordered,"sensing_reconciliation_intervention","task_id",taskId,null),
            replacementAt=eventIndex(ordered,"faulted_job_replacement_outcome","recovery_operation_id",recoveryId,null),
            verifiedAt=eventIndex(ordered,"sensing_reconciliation_verified","task_id",taskId,null),
            terminalAt=eventIndex(ordered,"operation","operation_id",recoveryId,"succeeded"),
            resolutionAt=eventIndex(ordered,"sensing_reconciliation_resolved","task_id",taskId,null);
        check(intentAt>=0&&intentAt<disposalAt&&disposalAt<interventionAt&&interventionAt<replacementAt&&replacementAt<verifiedAt&&verifiedAt<terminalAt&&terminalAt<resolutionAt,"Journal orders intent, returned native discard, source intervention, load replacement, verification, actual wrapper terminal, then resolution");
        long dispositionEvents=count("sensing_source_disposal_returned")+count("material_replacement_outcome")+count("board_replacement_outcome");
        Map<String,Object> resolvedReplay=call("request_sensing_reconciliation",recoveryRequest);
        check(request.get("operation_id").equals(resolvedReplay.get("operation_id"))&&!recoveryId.equals(resolvedReplay.get("operation_id")),"Completed remote request replay retains original request operation instead of remapping it to local recovery");
        check(dispositionEvents==count("sensing_source_disposal_returned")+count("material_replacement_outcome")+count("board_replacement_outcome")&&local.presentations.isEmpty(),"Completed remote request replay causes no native effects or new local presentation");
        long disposals=count("sensing_source_disposal_returned"),materialReplacements=count("material_replacement_outcome"),boardReplacements=count("board_replacement_outcome");
        refused(()->shown.callback.submit(local::currentAuthority).toCompletableFuture().get(10,TimeUnit.SECONDS),null,"Consumed local replacement callback cannot run twice");
        check(count("sensing_source_disposal_returned")==disposals&&count("material_replacement_outcome")==materialReplacements&&count("board_replacement_outcome")==boardReplacements,"Duplicate callback cannot repeat disposal or load replacement");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());
        Map<String,Object> refusedStart=call("start_job",command("job_id",newJobId));
        check("failed".equals(refusedStart.get("state"))&&"JOB_NOT_VALIDATED".equals(result(refusedStart).get("code"))&&"rejected-before-native-admission".equals(refusedStart.get("job_admission")),"Replacement cannot start using the failed attempt's validation");
        Map<String,Object> registration=run("locate_fiducials",command("job_id",newJobId));write("replacement-registration.json",registration);
        check("native-simulator".equals(result(registration).get("registration")),"Fresh native registration call returns for the replacement geometry");
        for(String id:newBoardIds)check("native-fiducial-call-returned".equals(map(load(read("get_board_loads"),id).get("registration")).get("state")),"New board load records its fresh native registration call");
        Map<String,Object> freshValidation=run("validate_job",command("job_id",newJobId));
        check(Boolean.TRUE.equals(result(freshValidation).get("valid")),"Replacement receives fresh native validation against new material and board loads");
        Map<String,Object> completed=run("start_job",command("job_id",newJobId));write("replacement-completed-job.json",completed);
        check(((Number)result(completed).get("placed")).intValue()==1&&"completed".equals(read("get_status").get("job_state")),"Replacement actual native processor completes exactly one placement");
        check(nativeTray.getFeedCount()==1&&((Number)load(read("get_material_loads"),newMaterial).get("observed_advances")).intValue()==1,"Replacement consumes one position from only its fresh finite tray load");
        check(oldActions.equals(originalActions(original))&&failed.equals(read("get_operation","operation_id",original))&&history.equals(copy(oldJob.getPlacedStatusSnapshot())),"New placement leaves original failed operation, action history and native board outcomes unchanged");
        Map<String,Object> newLineage=map(result(completed).get("job_lineage")),oldLineage=map(failed.get("job_lineage"));
        check(!Objects.equals(newLineage.get("lineage_id"),oldLineage.get("lineage_id")),"Replacement uses a distinct lineage from the failed native job");
        run("set_machine_enabled",command("enabled",false));read("release_control_session","session_id",session,"request_id",UUID.randomUUID().toString());
        byte[] after=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        check(after.length>prior.length&&Arrays.equals(prior,Arrays.copyOf(after,prior.length)),"Original journal bytes remain an exact append-only prefix");
        write("final-status.json",read("get_status"));
        proof.putAll(Bridge.map("original_operation_id",original,"original_job_id",oldJobId,"replacement_job_id",newJobId,"task_id",taskId,
            "recovery_operation_id",resolved.get("recovery_operation_id"),"original_material_load_id",oldMaterial,"replacement_material_load_id",newMaterial,
            "original_board_load_ids",oldBoardIds,"replacement_board_load_ids",newBoardIds,"original_feed_count",oldFeeds,
            "replacement_actual_simulator_placements",1,"original_outcome",failed.get("state"),"original_native_history",history,
            "journal_before_sha256",sha(prior),"journal_after_sha256",sha(after),"source_latch_before",latchBefore,"source_latch_after",latchAfter));
    }
    public static void main(String[] args)throws Exception {
        if(args.length<2||args.length>3)throw new IllegalArgumentException("Expected pinned sample root and new exclusive evidence state directory");
        if(args.length==3)scenario=args[2];
        if(!Set.of("retained-after-place","lost-before-place","invalid-read").contains(scenario))throw new IllegalArgumentException("Unsupported source scenario");
        state=Path.of(args[1]).toAbsolutePath();Files.createDirectory(state);
        int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try {
            Path configDir=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");
            Map<String,Object> prepared=GuiSensingFixture.prepare(configDir,manifest,scenario);
            Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();
            Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),scenario));
            write("fixture-attestation.json",claimed);
            check(Boolean.TRUE.equals(claimed.get("sensing_fixture_attested")),"Exact prepared native GUI-style fixture is attested in the current process");
            Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);
            local=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));
            bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",local);
            exercise(proof);proof.put("passed",true);
        } catch(Throwable failure) {
            failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;
        } finally {
            try {if(bridge!=null){if(exit!=0)bridge.recordGuiUnknownExit();bridge.close();}proof.put("bridge_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("bridge_close_failure",failure.toString());exit=1;}
            try {if(local!=null){local.release();check(!local.gate.owns(local.token),"Owned native guard is released during cleanup");}proof.put("native_gate_released",true);}catch(Throwable failure){failure.printStackTrace();proof.put("gate_release_failure",failure.toString());exit=1;}
            try {if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("machine_close_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);
            proof.putAll(Bridge.map("checks",checks,"assertions",checks.size(),"public_calls",calls,"pid",ProcessHandle.current().pid(),
                "synthetic_source_scenario",scenario,"local_adapter","actual native gate; non-Swing validation callback",
                "swing_qualification",false,"mainframe_qualification",false,"hardware_qualified",false,"faulted_job_replacement_qualified",Boolean.TRUE.equals(proof.get("passed")),
                "replacement_scope","initial current-process replacement of a terminal faulted native job",
                "interrupted_replacement_continuation_qualified",false,"restart_reattachment_qualified",false,"public_package_qualified",false));
            write("proof.json",proof);System.out.println("NATIVE_FAULTED_SENSING_REPLACEMENT_BRIDGE_RESULT "+JSON.toJson(proof));
        }
        System.exit(exit);
    }
}
