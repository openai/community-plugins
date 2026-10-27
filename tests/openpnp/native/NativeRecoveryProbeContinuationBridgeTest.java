/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import static org.openpnp.codex.NativeFaultedSensingReplacementContinuationBridgeTest.*;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.openpnp.model.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Actual Bridge/native recovery probe interruption. The private channel delegates the real
 * force, then revokes one local decision. No sample, native occupancy or journal outcome is fabricated. */
public final class NativeRecoveryProbeContinuationBridgeTest {
    static String boundary;
    static final AtomicBoolean injected=new AtomicBoolean(),initialDecision=new AtomicBoolean(true);
    static ForceGuard channel;
    static final List<Map<String,Object>> nativeActuatorChanges=Collections.synchronizedList(new ArrayList<>());
    static Object field(Object target,String name)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(target);}
    static void installProbe()throws Exception{Field f=Bridge.class.getDeclaredField("journal");f.setAccessible(true);channel=new ForceGuard((FileChannel)f.get(bridge));f.set(bridge,channel);
        ((org.openpnp.machine.reference.ReferenceActuator)nozzle().getVacuumActuator()).addPropertyChangeListener("lastActuationValue",event->{if(injected.get())nativeActuatorChanges.add(Bridge.map("old",event.getOldValue(),"new",event.getNewValue(),"native_executor",config.getMachine().isTask(Thread.currentThread())));});
    }
    static final class ForceGuard extends FileChannel {
        final FileChannel delegate;String last="";Map<String,Object> injectedRecord;Object preProbeValve;
        ForceGuard(FileChannel delegate){this.delegate=delegate;}
        public int write(ByteBuffer src)throws IOException{ByteBuffer b=src.duplicate();byte[] bytes=new byte[b.remaining()];b.get(bytes);last=new String(bytes,StandardCharsets.UTF_8);return delegate.write(src);}
        public void force(boolean metadata)throws IOException{
            delegate.force(metadata);
            if(!injected.get()&&last.contains("vacuum_observation_intent")){
                Map<String,Object> e=NativeJournalJson.parseObject(last),p=NativeFaultedSensingReplacementContinuationBridgeTest.map(e.get("payload"));
                if("vacuum_observation_intent".equals(e.get("type"))&&"check.before".equals(p.get("native_event"))&&"recovery".equals(NativeFaultedSensingReplacementContinuationBridgeTest.map(p.get("context")).get("scope"))){try{preProbeValve=nozzle().getVacuumActuator().getLastActuationValue();}catch(Exception ex){throw new IOException(ex);}}
                if("vacuum_observation_intent".equals(e.get("type"))&&boundary.equals(p.get("native_event"))&&"recovery".equals(NativeFaultedSensingReplacementContinuationBridgeTest.map(p.get("context")).get("scope"))&&injected.compareAndSet(false,true)){
                    check(config.getMachine().isTask(Thread.currentThread()),"Guard loss occurs after real FileChannel force inside actual native executor");
                    injectedRecord=e;initialDecision.set(false);
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
    static boolean sourceOrNativeEffect(Map<String,Object> event,String operation){
        String type=(String)event.get("type");Map<String,Object> payload=map(event.get("payload"));
        if(type.startsWith("sensing_source_")||type.equals("sensing_recovery_native_intent"))return operation.equals(payload.get("operation_id"));
        if(type.startsWith("vacuum_observation_")){Object context=payload.get("context");return context instanceof Map&&operation.equals(map(context).get("operation_id"));}
        return false;
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
        installProbe();
        try{write("initial-callback-result.json",shown.callback.submit(()->initialDecision.get()&&local.currentAuthority()).toCompletableFuture().get(60,TimeUnit.SECONDS));}
        catch(ExecutionException failure){write("initial-callback-failure.json",Bridge.map("class",failure.getCause().getClass().getName(),"message",failure.getCause().getMessage()));}
        Map<String,Object> interrupted=awaitRecovery(taskId);write("interrupted-task.json",interrupted);String firstRecovery=(String)interrupted.get("recovery_operation_id");Map<String,Object> firstOperation=read("get_operation","operation_id",firstRecovery);
        check(injected.get(),"Exact recovery-scoped probe intent force boundary fired");write("injected-forced-intent.json",channel.injectedRecord);
        check("outcome_unknown".equals(firstOperation.get("state"))&&Boolean.TRUE.equals(map(firstOperation.get("native_completion")).get("native_wrapper_completed")),"Interrupted recovery remains unknown after its real wrapper ends");
        check("reconciliation_unknown".equals(interrupted.get("state"))&&!Boolean.TRUE.equals(interrupted.get("live_resolution_activated")),"Interrupted probe does not resolve sensing readiness");
        check(Boolean.FALSE.equals(field(bridge,"journalFault")),"Successful force plus guard revocation leaves the journal usable");
        write("interrupted-native-state.json",Bridge.map("enabled",config.getMachine().isEnabled(),"busy",config.getMachine().isBusy(),"valve",nozzle().getVacuumActuator().getLastActuationValue(),"part_id",nozzle().getPart()==null?null:nozzle().getPart().getId()));
        check(!config.getMachine().isBusy()&&!config.getMachine().isEnabled(),"Actual interruption cleanup leaves machine disabled and idle");
        long probeValveOn=0,probeValveOff=0;
        for(Map<String,Object> e:events())if("vacuum_observation_outcome".equals(e.get("type"))){Map<String,Object> p=map(e.get("payload"));if("valve.returned".equals(p.get("native_event"))&&firstRecovery.equals(map(p.get("context")).get("operation_id"))){if(Boolean.TRUE.equals(map(p.get("data")).get("enabled")))probeValveOn++;else if(Boolean.FALSE.equals(map(p.get("data")).get("enabled")))probeValveOff++;}}
        if(boundary.equals("check.before")){check(probeValveOn==0&&probeValveOff==0,"Check-before interruption occurs before any current probe valve activation");same(channel.preProbeValve,nozzle().getVacuumActuator().getLastActuationValue(),"Unstarted probe preserves the exact observed native actuator value");}
        else check(probeValveOn==1&&Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue())&&nativeActuatorChanges.stream().anyMatch(e->Boolean.TRUE.equals(e.get("old"))&&Boolean.FALSE.equals(e.get("new"))&&Boolean.TRUE.equals(e.get("native_executor"))),"Read-before interruption pairs actual probe valve-on with native returned false actuator state after guard revocation");
        write("interrupted-native-actuator-changes.json",nativeActuatorChanges);
        proof.putAll(Bridge.map("initial_probe_valve_on",probeValveOn,"initial_probe_valve_off",probeValveOff,"pre_probe_valve",channel.preProbeValve,"interrupted_valve",nozzle().getVacuumActuator().getLastActuationValue()));
        Map<String,Object> initialProgress=progress(interrupted);String attempt=(String)initialProgress.get("replacement_attempt_id");
        check("completed".equals(map(initialProgress.get("definition")).get("phase"))&&Boolean.TRUE.equals(map(initialProgress.get("definition")).get("candidate_retained_in_process")),"Original native candidate survives probe interruption");
        check("completed".equals(map(initialProgress.get("document")).get("phase")),"Candidate archive was completed before interrupted native probe");
        Map<String,Object> pendingSource=NativeVacuumSources.reconciliationSnapshot(config);write("interrupted-source.json",pendingSource);
        Set<String> pendingIds=new TreeSet<>((List<String>)pendingSource.get("pending_observation_ids"));
        String injectedId=(String)map(map(channel.injectedRecord.get("payload")).get("data")).get("observation_id");
        check(!pendingIds.isEmpty()&&pendingIds.contains(injectedId),"Actual source retains exact forced pending probe observation");
        check(pendingIds.size()==(boundary.equals("check.before")?1:2),"Interrupted check/read retains exact nested native observation count");
        check(events().stream().noneMatch(e->"vacuum_observation_outcome".equals(e.get("type"))&&injectedId.equals(map(map(e.get("payload")).get("data")).get("observation_id"))),"Interrupted forced observation has no invented outcome");
        check(events().stream().filter(e->"sensing_source_disposal_returned".equals(e.get("type"))&&firstRecovery.equals(map(e.get("payload")).get("operation_id"))).count()==1,"Original retained material was actually disposed before the now-pending probe");
        for(String stage:List.of("lineage","compound","publication"))check(!"completed".equals(map(initialProgress.get(stage)).get("phase")),"Probe interruption has not completed "+stage);
        for(String component:List.of("material","boards"))for(Object row:(List<?>)map(initialProgress.get(component)).get("rows"))check("untouched".equals(map(row).get("phase")),"Probe interruption leaves "+component+" replacement untouched");
        check(feedCount()==oldFeeds&&local.job==oldJob&&history.equals(copy(oldJob.getPlacedStatusSnapshot())),"Probe interruption preserves native material/job history");
        Map<String,Object> interruptedFiles=documentInventory();write("interrupted-document-files.json",interruptedFiles);
        Map<String,Object> firstForced=latestOperation(firstRecovery);List<Map<String,Object>> initialRecords=originalComponentRecords();
        JsonObject continuationRequest=command("recovery_kind","continue-faulted-job-replacement","replacement_attempt_id",attempt);Map<String,Object> nextRequest=run("request_sensing_reconciliation",continuationRequest);
        Presentation nextShown=local.presentations.poll(10,TimeUnit.SECONDS);check(nextShown!=null,"Pending recovery probe receives a fresh exact process-owned continuation decision");String nextTask=(String)nextShown.task.get("task_id");write("continuation-presented-task.json",nextShown.task);
        Map<String,Object> nextCapture=map(map(nextShown.task.get("snapshot")).get("replacement"));check(attempt.equals(nextCapture.get("replacement_attempt_id"))&&map(nextCapture.get("prior_operations")).containsKey(firstRecovery),"Fresh local decision binds retained candidate and exact ended original recovery");
        check("completed".equals(map(map(nextCapture.get("progress")).get("document")).get("phase")),"Fresh capture retains completed original document receipt");
        try{write("continuation-callback-result.json",nextShown.callback.submit(local::currentAuthority).toCompletableFuture().get(60,TimeUnit.SECONDS));}
        catch(ExecutionException failure){write("continuation-callback-failure.json",Bridge.map("class",failure.getCause().getClass().getName(),"message",failure.getCause().getMessage()));}
        Map<String,Object> resolved=awaitRecovery(nextTask);write("continuation-task.json",resolved);String recovery=(String)resolved.get("recovery_operation_id");Map<String,Object> finalProgress=progress(resolved);
        List<Map<String,Object>> journal=events();List<Map<String,Object>> retirement=new ArrayList<>();long freshDisposals=0,freshChecks=0,freshReads=0;
        for(Map<String,Object> e:journal){String type=(String)e.get("type");Map<String,Object> p=map(e.get("payload"));
            if(recovery.equals(p.get("operation_id"))&&"sensing_source_scope_retirement_returned".equals(type))retirement.add(p);
            if(recovery.equals(p.get("operation_id"))&&"sensing_source_disposal_returned".equals(type))freshDisposals++;
            if("vacuum_observation_outcome".equals(type)&&p.get("context") instanceof Map&&recovery.equals(map(p.get("context")).get("operation_id"))){if("check.returned".equals(p.get("native_event")))freshChecks++;if("read.returned".equals(p.get("native_event")))freshReads++;}
        }
        Map<String,Object> finalSource=NativeVacuumSources.reconciliationSnapshot(config);write("continuation-source.json",finalSource);
        proof.putAll(Bridge.map("initial_task_id",taskId,"initial_recovery_operation_id",firstRecovery,"continuation_task_id",nextTask,"continuation_operation_id",recovery,"replacement_attempt_id",attempt,"original_operation_id",original,"continuation_state",resolved.get("state"),"pending_observation_ids",pendingIds,"injected_observation_id",injectedId,"fresh_disposals",freshDisposals,"fresh_returned_checks",freshChecks,"fresh_returned_reads",freshReads,"retirement_records",retirement));
        same(firstForced,latestOperation(firstRecovery),"Fresh decision preserves exact original unknown recovery operation even on continuation failure");
        check(retirement.size()==1&&new TreeSet<>((List<String>)map(retirement.get(0).get("facts")).get("pending_observation_ids")).equals(pendingIds),"Fresh local continuation retires only exact terminal native pending scope");
        check(freshDisposals==1,"Pending recovery observation requires a NEW real native disposal despite earlier disposed signal; actual fresh disposals="+freshDisposals);
        check(freshChecks>0&&freshReads>0,"Fresh decision performs new returned native read and check observations after disposal");
        check("resolved_for_current_scope".equals(resolved.get("state"))&&Boolean.TRUE.equals(resolved.get("live_resolution_activated")),"Fresh local continuation reconciles pending source, replaces job and completes exact native publication");
        Map<String,Object> document=map(finalProgress.get("document")),record=map(document.get("record"));check("completed".equals(document.get("phase"))&&record!=null,"Completed candidate document has a durable root record");
        Map<String,Object> manifest=map(record.get("manifest"));check(NativeFaultedJobReplacement.digest(manifest).equals(record.get("manifest_sha256")),"Root document receipt binds exact saved native manifest");
        same(map(map(initialProgress.get("definition")).get("record")).get("mapping"),manifest.get("source_mapping"),"Continuation preserves exact original candidate mapping");
        Map<String,Object> finalFiles=documentInventory();for(String filename:interruptedFiles.keySet())same(interruptedFiles.get(filename),finalFiles.get(filename),"Continuation preserves existing partial archive artifact "+filename);
        check(finalFiles.keySet().stream().filter(n->n.endsWith(".zip")).count()==1&&finalFiles.keySet().stream().filter(n->n.endsWith(".receipt.json")).count()==1,"Continuation reuses exactly one completed immutable archive and signed receipt");
        same(firstForced,latestOperation(firstRecovery),"Document completion never upgrades original unknown recovery operation");check(failed.equals(read("get_operation","operation_id",original))&&oldActions.equals(originalActions(original))&&history.equals(copy(oldJob.getPlacedStatusSnapshot())),"Original failed manufacturing operation/actions/placed history remain exact");
        check(count("native_action_intent")==actionCount&&count("native_placement_checkpoint")==placementCount,"Document completion and reconciliation repeat no original manufacturing action");
        check(count("material_continuation_intent")==1&&count("board_continuation_intent")==oldBoardIds.size()&&count("job_lineage_continuation_replacement")==1,"Untouched lineage/material/board steps each execute once under the fresh continuation decision");
        check(nozzle().getPart()==null&&!config.getMachine().isEnabled()&&!config.getMachine().isBusy()&&Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Completed document continuation leaves actual native simulator disabled, idle, empty and valve off");
        Map<String,Object> status=read("get_status");String newJob=(String)status.get("job_id");check(attempt.equals(newJob)&&local.job!=oldJob&&local.job.getPlacedStatusSnapshot().isEmpty(),"Continuation installs the exact fresh candidate with empty native history");
        Map<String,Object> material=read("get_material_loads");String freshMaterial=null;for(Object raw:(List<?>)material.get("loads"))if(Boolean.TRUE.equals(map(raw).get("active")))freshMaterial=(String)map(raw).get("load_id");check(freshMaterial!=null&&!freshMaterial.equals(oldMaterial)&&nativeTray.getFeedCount()==0,"Untouched tray replacement creates one distinct fresh native material identity");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());Map<String,Object> refusal=call("start_job",command("job_id",newJob));check("failed".equals(refusal.get("state"))&&"JOB_NOT_VALIDATED".equals(result(refusal).get("code")),"Document completion cannot reuse original failed validation");
        Map<String,Object> registration=run("locate_fiducials",command("job_id",newJob));check("native-simulator".equals(result(registration).get("registration")),"Completed document candidate receives actual fresh native fiducial registration");Map<String,Object> validation=run("validate_job",command("job_id",newJob));check(Boolean.TRUE.equals(result(validation).get("valid")),"Completed candidate receives fresh native preflight");
        Map<String,Object> completed=run("start_job",command("job_id",newJob));write("continuation-completed-job.json",completed);check(((Number)result(completed).get("placed")).intValue()==1&&nativeTray.getFeedCount()==1,"Actual native processor completes one fresh placement after document recovery");
        check(((Number)load(read("get_material_loads"),freshMaterial).get("observed_advances")).intValue()==1,"Fresh placement consumes exactly one new tray position");
        same(firstForced,latestOperation(firstRecovery),"Fresh placement preserves original unknown recovery outcome");check(failed.equals(read("get_operation","operation_id",original))&&oldActions.equals(originalActions(original))&&history.equals(copy(oldJob.getPlacedStatusSnapshot())),"Fresh placement preserves all original manufacturing outcomes");
        Map<String,Object> requestReplay=call("request_sensing_reconciliation",continuationRequest);check(nextRequest.get("operation_id").equals(requestReplay.get("operation_id"))&&local.presentations.isEmpty(),"Completed continuation request replay creates no new local work");
        run("set_machine_enabled",command("enabled",false));read("release_control_session","session_id",session,"request_id",UUID.randomUUID().toString());byte[] after=Files.readAllBytes(state.resolve("journal/operations.jsonl"));check(Arrays.equals(prior,Arrays.copyOf(after,prior.length)),"Original journal remains an exact append-only prefix");
        proof.putAll(Bridge.map("replacement_actual_simulator_placements",1,"pending_scope_disposed_before_fresh_probe",true,"original_unknown_preserved",true,"document_id",record.get("document_id"),"archive_inventory",finalFiles,"journal_before_sha256",sha(prior),"journal_after_sha256",sha(after)));
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=3)throw new IllegalArgumentException("Expected samples, exclusive state directory, native probe boundary");boundary=args[2];if(!Set.of("check.before","read.before").contains(boundary))throw new IllegalArgumentException("Unknown probe boundary");scenario="retained-after-place";state=Path.of(args[1]).toAbsolutePath();Files.createDirectory(state);int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try{Path cfg=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");Map<String,Object> prepared=GuiSensingFixture.prepare(cfg,manifest,scenario);Configuration.get().getMachine().close();Configuration.initialize(cfg.toFile());config=Configuration.get();config.load();Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),scenario));write("fixture-attestation.json",claimed);Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);local=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",local);exercise(proof);proof.put("passed",true);}
        catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally{try{if(bridge!=null){if(exit!=0)bridge.recordGuiUnknownExit();bridge.close();}proof.put("bridge_closed",true);}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}try{if(local!=null){local.release();check(!local.gate.owns(local.token),"Actual native ownership released during cleanup");}proof.put("native_gate_released",true);}catch(Throwable failure){proof.put("gate_release_failure",failure.toString());exit=1;}try{if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){proof.put("machine_close_failure",failure.toString());exit=1;}if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("checks",checks,"assertions",checks.size(),"public_calls",calls,"pid",ProcessHandle.current().pid(),"boundary",boundary,"scenario",scenario,"injected",injected.get(),"scope","Real Bridge and native simulator: test-only FileChannel delegates successful force then revokes only initial local decision at recovery probe intent; no fabricated journal outcomes/native samples","mainframe_qualification",false,"restart_reattachment_qualified",false,"hardware_qualified",false,"public_package_qualified",false));write("live-proof.json",proof);System.out.println("NATIVE_RECOVERY_PROBE_CONTINUATION_BRIDGE_RESULT "+JSON.toJson(proof));}System.exit(exit);
    }
}
