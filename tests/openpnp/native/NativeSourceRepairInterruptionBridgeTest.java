/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import static org.openpnp.codex.NativeFaultedSensingReplacementContinuationBridgeTest.*;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.lang.reflect.Field;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.openpnp.model.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Real native lost-before-place job, source replacement, and one-use local authority revocation.
 * The test only wraps the actual journal force. It never assigns nozzle Part or fabricates events. */
public final class NativeSourceRepairInterruptionBridgeTest {
    static final AtomicBoolean decision=new AtomicBoolean(true);
    static RepairBoundary probe;
    static Part held;
    static Map<String,Object> forcedSource;
    static final class RepairBoundary extends FileChannel {
        final FileChannel delegate; final String taskId; boolean fired; String last=""; Map<String,Object> forcedRecord;
        RepairBoundary(FileChannel delegate,String taskId){this.delegate=delegate;this.taskId=taskId;}
        public int write(ByteBuffer src)throws IOException{ByteBuffer b=src.duplicate();byte[] bytes=new byte[b.remaining()];b.get(bytes);last=new String(bytes,StandardCharsets.UTF_8);return delegate.write(src);}
        public void force(boolean metadata)throws IOException{
            delegate.force(metadata);
            if(!fired&&last.contains("sensing_source_intervention_returned")){
                Map<String,Object> e=NativeJournalJson.parseObject(last),payload=NativeFaultedSensingReplacementContinuationBridgeTest.map(e.get("payload"));
                if("sensing_source_intervention_returned".equals(e.get("type"))&&taskId.equals(payload.get("task_id"))){
                    fired=true;forcedRecord=e;
                    try{
                        check(config.getMachine().isTask(Thread.currentThread()),"Source repair boundary executes on actual native task");
                        check(nozzle().getPart()==held&&held==config.getPart("R0603-1K"),"Actual native held Part remains unchanged at the forced repair-returned boundary");
                        check(!config.getMachine().isEnabled(),"Source replacement completes while disabled");
                        forcedSource=copy(NativeVacuumSources.reconciliationSnapshot(config));
                        check(Boolean.TRUE.equals(forcedSource.get("sensor_repaired"))&&((Number)forcedSource.get("source_generation")).intValue()==1,"Actual source successor is installed before local authority revocation");
                        decision.set(false);
                    }catch(Exception failure){throw new IOException(failure);}
                }
            }
        }
        public int read(ByteBuffer b)throws IOException{return delegate.read(b);}public long read(ByteBuffer[] b,int o,int l)throws IOException{return delegate.read(b,o,l);}
        public long write(ByteBuffer[] b,int o,int l)throws IOException{return delegate.write(b,o,l);}public int read(ByteBuffer b,long p)throws IOException{return delegate.read(b,p);}public int write(ByteBuffer b,long p)throws IOException{return delegate.write(b,p);}
        public long position()throws IOException{return delegate.position();}public FileChannel position(long p)throws IOException{delegate.position(p);return this;}public long size()throws IOException{return delegate.size();}public FileChannel truncate(long s)throws IOException{delegate.truncate(s);return this;}
        public long transferTo(long p,long c,WritableByteChannel t)throws IOException{return delegate.transferTo(p,c,t);}public long transferFrom(ReadableByteChannel s,long p,long c)throws IOException{return delegate.transferFrom(s,p,c);}
        public MappedByteBuffer map(MapMode m,long p,long s)throws IOException{return delegate.map(m,p,s);}public FileLock lock(long p,long s,boolean sh)throws IOException{return delegate.lock(p,s,sh);}public FileLock tryLock(long p,long s,boolean sh)throws IOException{return delegate.tryLock(p,s,sh);}
        protected void implCloseChannel()throws IOException{delegate.close();}
    }
    static void installProbe(String taskId)throws Exception{Field f=Bridge.class.getDeclaredField("journal");f.setAccessible(true);probe=new RepairBoundary((FileChannel)f.get(bridge),taskId);f.set(bridge,probe);}
    static long opEvents(String type,String operation)throws Exception{return events().stream().filter(e->type.equals(e.get("type"))&&operation.equals(map(e.get("payload")).get("operation_id"))).count();}
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
        held=nozzle().getPart();
        check(held!=null&&held==config.getPart("R0603-1K")&&oldFeeds==1,"Actual original native lost-before-place job retains its picked canonical Part after one feed");
        write("original-held-part.json",Bridge.map("part_id",held.getId(),"canonical_identity_equal",held==config.getPart("R0603-1K"),"native_nozzle_part_same",held==nozzle().getPart(),"model_assignment_by_test",false,"original_feed_count",oldFeeds));
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
        installProbe(taskId);
        try{write("initial-callback-result.json",shown.callback.submit(()->decision.get()&&local.currentAuthority()).toCompletableFuture().get(60,TimeUnit.SECONDS));}
        catch(ExecutionException failure){write("initial-callback-failure.json",Bridge.map("class",failure.getCause().getClass().getName(),"message",failure.getCause().getMessage()));}
        Map<String,Object> interrupted=awaitRecovery(taskId);write("interrupted-task.json",interrupted);String firstRecovery=(String)interrupted.get("recovery_operation_id");Map<String,Object> firstOperation=read("get_operation","operation_id",firstRecovery);write("interrupted-operation.json",firstOperation);
        check(probe.fired&&!decision.get(),"Exactly the initial forced source repair return revoked its one-use local decision");write("forced-source-returned.json",probe.forcedRecord);write("source-at-interruption.json",forcedSource);
        check("outcome_unknown".equals(firstOperation.get("state"))&&Boolean.TRUE.equals(map(firstOperation.get("native_completion")).get("native_wrapper_completed")),"Interrupted recovery is unknown after its actual native wrapper completed");
        check("reconciliation_unknown".equals(interrupted.get("state"))&&!Boolean.TRUE.equals(interrupted.get("live_resolution_activated")),"Interrupted source repair restores no sensing readiness");
        Map<String,Object> interruptedStatus=read("get_status");write("interrupted-status.json",interruptedStatus);
        check(Boolean.FALSE.equals(interruptedStatus.get("journal_fault"))&&local.currentAuthority(),"Successful journal force stays usable and native ownership survives decision revocation");
        check(nozzle().getPart()==held&&!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Actual held Part survives interrupted wrapper completion on disabled idle machine");
        check(opEvents("sensing_source_intervention_returned",firstRecovery)==1&&opEvents("sensing_source_disposal_intent",firstRecovery)==0&&opEvents("sensing_recovery_native_intent",firstRecovery)==0,"Initial recovery installed source once and performed no disposal, motion or probe");
        Map<String,Object> initialProgress=progress(interrupted);String attempt=(String)initialProgress.get("replacement_attempt_id");write("interrupted-progress.json",initialProgress);
        check("completed".equals(map(initialProgress.get("definition")).get("phase"))&&Boolean.TRUE.equals(map(initialProgress.get("definition")).get("candidate_retained_in_process")),"Original detached replacement candidate remains retained");
        check("completed".equals(map(initialProgress.get("document")).get("phase")),"Candidate document was durably saved before interrupted source repair");
        for(String stage:List.of("lineage","publication"))check("untouched".equals(map(initialProgress.get(stage)).get("phase")),"Interrupted repair leaves "+stage+" untouched");
        check("pending".equals(map(initialProgress.get("compound")).get("phase"))&&map(initialProgress.get("compound")).get("record")==null,"Original compound remains pending without a completion record");
        for(String component:List.of("material","boards"))for(Object row:(List<?>)map(initialProgress.get(component)).get("rows"))check("untouched".equals(map(row).get("phase")),"Interrupted repair leaves original "+component+" untouched");
        check(feedCount()==oldFeeds&&local.job==oldJob&&history.equals(copy(oldJob.getPlacedStatusSnapshot())),"Source repair interruption changes no native job history or tray counter");
        Map<String,Object> filesBefore=documentInventory(),firstForced=latestOperation(firstRecovery);List<Map<String,Object>> initialRecords=originalComponentRecords();
        refused(()->shown.callback.submit(local::currentAuthority).toCompletableFuture().get(10,TimeUnit.SECONDS),null,"Consumed original callback cannot reuse authority after repair interruption");
        check(count("sensing_source_disposal_returned")==0,"Consumed callback refusal performs no disposal");
        JsonObject continuationRequest=command("recovery_kind","continue-faulted-job-replacement","replacement_attempt_id",attempt);Map<String,Object> nextRequest=run("request_sensing_reconciliation",continuationRequest);
        Presentation nextShown=local.presentations.poll(10,TimeUnit.SECONDS);check(nextShown!=null,"Fresh exact continuation reaches another local decision");String nextTask=(String)nextShown.task.get("task_id");write("continuation-presented-task.json",nextShown.task);
        Map<String,Object> nextScope=map(nextShown.task.get("snapshot")),nextCapture=map(nextScope.get("replacement")),nextSource=map(nextScope.get("source"));
        check(!taskId.equals(nextTask)&&attempt.equals(nextCapture.get("replacement_attempt_id"))&&map(nextCapture.get("prior_operations")).containsKey(firstRecovery),"Fresh decision binds the retained candidate and ended unknown recovery operation");
        same(forcedSource,nextSource,"Fresh continuation captures exact repaired generation and unchanged held-material source state");
        check(nozzle().getPart()==held&&count("sensing_source_disposal_returned")==0,"Remote continuation request leaves actual held Part unchanged");
        same(map(initialProgress.get("document")).get("record"),map(map(nextCapture.get("progress")).get("document")).get("record"),"Fresh capture preserves the completed candidate document receipt");
        try{write("continuation-callback-result.json",nextShown.callback.submit(local::currentAuthority).toCompletableFuture().get(60,TimeUnit.SECONDS));}
        catch(ExecutionException failure){write("continuation-callback-failure.json",Bridge.map("class",failure.getCause().getClass().getName(),"message",failure.getCause().getMessage()));}
        Map<String,Object> resolved=awaitRecovery(nextTask);write("continuation-task.json",resolved);String recovery=(String)resolved.get("recovery_operation_id");Map<String,Object> finalProgress=progress(resolved);write("continuation-progress.json",finalProgress);
        proof.putAll(Bridge.map("initial_task_id",taskId,"initial_recovery_operation_id",firstRecovery,"continuation_task_id",nextTask,"continuation_operation_id",recovery,"replacement_attempt_id",attempt,"original_operation_id",original,"continuation_state",resolved.get("state"),"actual_original_held_part_id",held.getId(),"held_part_empty_after_continuation",nozzle().getPart()==null));
        check("resolved_for_current_scope".equals(resolved.get("state"))&&Boolean.TRUE.equals(resolved.get("live_resolution_activated")),"Fresh continuation repairs source, disposes actual held Part, probes, replaces and publishes");
        check(count("sensing_source_disposal_returned")==1&&opEvents("sensing_source_disposal_returned",recovery)==1,"Exactly one native disposal completes under the fresh continuation operation");
        long releases=events().stream().filter(e->"sensing_source_disposal_stage_returned".equals(e.get("type"))&&"native-release".equals(map(map(e.get("payload")).get("facts")).get("stage"))).count();
        check(releases==1,"Exactly one actual native release stage returned");
        check(nozzle().getPart()==null&&!config.getMachine().isEnabled()&&!config.getMachine().isBusy()&&Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Continuation leaves native model empty, disabled, idle and valve off");
        Map<String,Object> finalSource=copy(NativeVacuumSources.reconciliationSnapshot(config));write("source-after-continuation.json",finalSource);
        check(((Number)finalSource.get("source_generation")).intValue()==2&&!map(forcedSource.get("source")).get("source_id").equals(map(finalSource.get("source")).get("source_id")),"Fresh decision creates a further distinct native source generation");
        same(filesBefore,documentInventory(),"Completed candidate archive and receipt receive no redundant writes");same(map(initialProgress.get("document")).get("record"),map(finalProgress.get("document")).get("record"),"Original completed document receipt stays exact");
        same(firstForced,latestOperation(firstRecovery),"Successful continuation never upgrades interrupted recovery operation");check(failed.equals(read("get_operation","operation_id",original))&&oldActions.equals(originalActions(original))&&history.equals(copy(oldJob.getPlacedStatusSnapshot())),"Original manufacturing operation, actions and placed history stay exact");
        check(count("native_action_intent")==actionCount&&count("native_placement_checkpoint")==placementCount,"Recovery repeats no original manufacturing action");
        check(count("material_continuation_intent")==1&&count("board_continuation_intent")==oldBoardIds.size()&&count("job_lineage_continuation_replacement")==1,"Fresh continuation executes untouched lineage, material and board steps once");
        Map<String,Object> status=read("get_status");String newJob=(String)status.get("job_id");check(attempt.equals(newJob)&&local.job!=oldJob&&local.job.getPlacedStatusSnapshot().isEmpty(),"Continuation installs distinct native candidate with empty history");
        Map<String,Object> material=read("get_material_loads"),newBoards=read("get_board_loads");String freshMaterial=null;for(Object raw:(List<?>)material.get("loads"))if(Boolean.TRUE.equals(map(raw).get("active")))freshMaterial=(String)map(raw).get("load_id");check(freshMaterial!=null&&!freshMaterial.equals(oldMaterial)&&nativeTray.getFeedCount()==0,"Replacement tray has a distinct new identity and native zero counter");
        Set<String> freshBoards=ids((List<?>)newBoards.get("roots"),"load_id");check(Collections.disjoint(oldBoardIds,freshBoards)&&freshBoards.size()==oldBoardIds.size(),"Replacement boards have genuinely new load identities");for(String id:oldBoardIds)check(map(newBoards.get("quarantined_loads")).containsKey(id),"Original uncertain board remains quarantined");
        for(String field:List.of("current_index","observed_advances","state","initial_index","created_at"))same(oldMaterialRecord.get(field),load(material,oldMaterial).get(field),"Original native material "+field+" remains unchanged");
        List<Map<String,Object>> ordered=events();int publicationAt=eventIndex(ordered,"faulted_job_replacement_continuation_publication_outcome","recovery_operation_id",recovery,null),verifiedAt=eventIndex(ordered,"sensing_reconciliation_verified","task_id",nextTask,null),terminalAt=eventIndex(ordered,"operation","operation_id",recovery,"succeeded"),resolutionAt=eventIndex(ordered,"sensing_reconciliation_resolved","task_id",nextTask,null);
        check(publicationAt>=0&&publicationAt<verifiedAt&&verifiedAt<terminalAt&&terminalAt<resolutionAt,"Publication, fresh verification, actual wrapper terminal and final readiness resolve in forced order");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());Map<String,Object> refusal=call("start_job",command("job_id",newJob));check("failed".equals(refusal.get("state"))&&"JOB_NOT_VALIDATED".equals(result(refusal).get("code")),"Continuation cannot reuse original job validation");
        Map<String,Object> registration=run("locate_fiducials",command("job_id",newJob));check("native-simulator".equals(result(registration).get("registration")),"New candidate receives actual fresh native fiducial registration");Map<String,Object> validation=run("validate_job",command("job_id",newJob));check(Boolean.TRUE.equals(result(validation).get("valid")),"Replacement receives fresh native preflight");
        Map<String,Object> completed=run("start_job",command("job_id",newJob));write("continuation-completed-job.json",completed);check(((Number)result(completed).get("placed")).intValue()==1&&nativeTray.getFeedCount()==1,"Actual native processor completes exactly one new placement after source-repair interruption");
        check(((Number)load(read("get_material_loads"),freshMaterial).get("observed_advances")).intValue()==1,"Fresh placement consumes exactly one new tray position");same(firstForced,latestOperation(firstRecovery),"Fresh placement preserves initial unknown recovery");check(failed.equals(read("get_operation","operation_id",original))&&oldActions.equals(originalActions(original))&&history.equals(copy(oldJob.getPlacedStatusSnapshot())),"Fresh placement preserves original manufacturing outcomes");
        Map<String,Object> completedReplay=call("request_sensing_reconciliation",continuationRequest);check(nextRequest.get("operation_id").equals(completedReplay.get("operation_id"))&&local.presentations.isEmpty(),"Completed remote replay creates no new local work");refused(()->nextShown.callback.submit(local::currentAuthority).toCompletableFuture().get(10,TimeUnit.SECONDS),null,"Completed callback stays one-use");check(count("sensing_source_disposal_returned")==1,"Callback replay repeats no disposal");
        run("set_machine_enabled",command("enabled",false));read("release_control_session","session_id",session,"request_id",UUID.randomUUID().toString());byte[] after=Files.readAllBytes(state.resolve("journal/operations.jsonl"));check(Arrays.equals(prior,Arrays.copyOf(after,prior.length)),"Original journal remains exact append-only prefix");write("final-status.json",read("get_status"));
        proof.putAll(Bridge.map("replacement_actual_simulator_placements",1,"original_unknown_preserved",true,"actual_held_part_preserved_at_interruption",true,"test_assigned_nozzle_part",false,"native_disposals",count("sensing_source_disposal_returned"),"native_release_stages",releases,"native_source_generations",2,"journal_before_sha256",sha(prior),"journal_after_sha256",sha(after)));
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=3||!"lost-before-place".equals(args[2]))throw new IllegalArgumentException("Expected samples, exclusive state directory, lost-before-place");scenario="lost-before-place";state=Path.of(args[1]).toAbsolutePath();Files.createDirectory(state);int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try{Path cfg=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");Map<String,Object> prepared=GuiSensingFixture.prepare(cfg,manifest,scenario);Configuration.get().getMachine().close();Configuration.initialize(cfg.toFile());config=Configuration.get();config.load();Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),scenario));write("fixture-attestation.json",claimed);Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);local=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",local);exercise(proof);proof.put("passed",true);}
        catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally{try{if(bridge!=null){if(exit!=0)bridge.recordGuiUnknownExit();bridge.close();}proof.put("bridge_closed",true);}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}try{if(local!=null){local.release();check(!local.gate.owns(local.token),"Actual native ownership released during cleanup");}proof.put("native_gate_released",true);}catch(Throwable failure){proof.put("gate_release_failure",failure.toString());exit=1;}try{if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){proof.put("machine_close_failure",failure.toString());exit=1;}if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("checks",checks,"assertions",checks.size(),"public_calls",calls,"pid",ProcessHandle.current().pid(),"scenario",scenario,"injected",probe!=null&&probe.fired,"scope","Real Bridge/native lost-before-place leaves actual held Part; private successful journal force revokes initial decision after source repair and before disposal; new local continuation and one fresh native placement","mainframe_qualification",false,"restart_reattachment_qualified",false,"hardware_qualified",false,"public_package_qualified",false));write("live-proof.json",proof);System.out.println("NATIVE_SOURCE_REPAIR_INTERRUPTION_BRIDGE_RESULT "+JSON.toJson(proof));}System.exit(exit);
    }
}
