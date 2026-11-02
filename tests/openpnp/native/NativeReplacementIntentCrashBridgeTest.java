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
import org.openpnp.model.*;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Actual forced crash boundaries. Restart reconstructs inert native documents only;
 * no source, material binding, local continuation permit or old wrapper completion is restored. */
public final class NativeReplacementIntentCrashBridgeTest {
    static String boundary,mode;static Map<String,Object> crashContext;
    static Map<String,Object> documentInventory()throws Exception{Path dir=state.resolve("journal/native-replacement-documents");if(!Files.exists(dir,LinkOption.NOFOLLOW_LINKS))return new LinkedHashMap<>();return NativeFaultedSensingReplacementContinuationBridgeTest.documentInventory();}
    static int exitCode(){return boundary.equals("intent-force")?73:74;}
    static void forceFile(String name,Object value)throws Exception{byte[] bytes=(JSON.toJson(value)+"\n").getBytes(StandardCharsets.UTF_8);try(FileChannel channel=FileChannel.open(state.resolve(name),StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE)){ByteBuffer b=ByteBuffer.wrap(bytes);while(b.hasRemaining())channel.write(b);channel.force(true);}try(FileChannel directory=FileChannel.open(state,StandardOpenOption.READ)){directory.force(true);}}
    static Map<String,Object> snapshotDependencies(String prefix)throws Exception{Map<String,Object> hashes=new LinkedHashMap<>();for(String kind:List.of("part","package")){Object value=kind.equals("part")?config.getPart("R0603-1K"):config.getPackage("R0603");java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream();Configuration.createSerializer().write(value,bytes);byte[] xml=bytes.toByteArray();String name=prefix+"-"+kind+"-R0603.xml";try(FileChannel f=FileChannel.open(state.resolve(name),StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE)){ByteBuffer buffer=ByteBuffer.wrap(xml);while(buffer.hasRemaining())f.write(buffer);f.force(true);}hashes.put(kind,sha(xml));}forceFile(prefix+"-dependency-hashes.json",hashes);return hashes;}
    static Object field(Object target,String name)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(target);}
    static void installCrashProbe()throws Exception{
        if(boundary.equals("intent-force")){Field f=Bridge.class.getDeclaredField("journal");f.setAccessible(true);f.set(bridge,new CrashForce((FileChannel)f.get(bridge)));}
        else {NativeReplacementDocuments documents=new NativeReplacementDocuments(config,state.resolve("journal/native-replacement-documents"),state.resolve("journal"),(target,renamed)->{if(target.getFileName().toString().endsWith(".zip")&&!renamed)haltAtBoundary();});Field f=Bridge.class.getDeclaredField("replacementDocuments");f.setAccessible(true);f.set(bridge,documents);}
    }
    static void haltAtBoundary()throws Exception{
        Thread watchdog=new Thread(()->{try{Thread.sleep(5000);StringBuilder dump=new StringBuilder();for(var entry:Thread.getAllStackTraces().entrySet()){dump.append(entry.getKey()).append("\n");for(StackTraceElement frame:entry.getValue())dump.append("  ").append(frame).append("\n");}Files.writeString(state.resolve("marker-stall-thread-dump.txt"),dump.toString(),StandardOpenOption.CREATE_NEW);}catch(Throwable ignored){}});watchdog.setDaemon(true);watchdog.start();
        check(config.getMachine().isTask(Thread.currentThread()),"Crash occurs inside actual OpenPnP native executor");
        List<Map<String,Object>> rows=events();Map<String,Object> intent=null;for(Map<String,Object> e:rows)if("faulted_job_replacement_intent".equals(e.get("type")))intent=map(e.get("payload"));
        check(intent!=null&&((Number)intent.get("schema_version")).intValue()==2,"Actual forced replacement intent is schema2");
        check(intent.get("reconstruction_bundle") instanceof Map&&NativeFaultedJobReplacement.digest(map(intent.get("reconstruction_bundle"))).equals(intent.get("reconstruction_sha256")),"Forced intent already contains exact reconstruction bundle and digest");
        long definitions=rows.stream().filter(e->"faulted_job_replacement_definition".equals(e.get("type"))).count();check(definitions==(boundary.equals("intent-force")?0:1),"Crash boundary has exact expected definition prefix");
        check(rows.stream().noneMatch(e->"faulted_job_replacement_document".equals(e.get("type"))),"No root archive receipt exists at crash boundary");
        String recovery=(String)intent.get("recovery_operation_id");check(rows.stream().noneMatch(e->((String)e.get("type")).startsWith("sensing_source_")&&recovery.equals(map(e.get("payload")).get("operation_id"))),"Reconstruction is durable before source repair or disposal");
        Map<String,Object> files=documentInventory();check(files.keySet().stream().noneMatch(n->n.endsWith(".zip")||n.endsWith(".receipt.json")),"Neither candidate archive nor signed receipt is published at crash boundary");
        snapshotDependencies("producer");
        forceFile("crash-boundary.json",Bridge.map("boundary",boundary,"expected_exit",exitCode(),"pid",ProcessHandle.current().pid(),"journal_sha256",sha(Files.readAllBytes(state.resolve("journal/operations.jsonl"))),"intent",intent,"definition_count",definitions,"document_inventory",files,"native_executor",true,"assertions",checks.size(),"checks",checks));
        Runtime.getRuntime().halt(exitCode());
        throw new AssertionError("Runtime.halt returned");
    }
    static final class CrashForce extends FileChannel {
        final FileChannel delegate;String last="";boolean fired;
        CrashForce(FileChannel delegate){this.delegate=delegate;}
        public int write(ByteBuffer src)throws IOException{ByteBuffer b=src.duplicate();byte[] bytes=new byte[b.remaining()];b.get(bytes);last=new String(bytes,StandardCharsets.UTF_8);return delegate.write(src);}
        public void force(boolean metadata)throws IOException{delegate.force(metadata);if(!fired&&last.contains("faulted_job_replacement_intent")){Map<String,Object> e=NativeJournalJson.parseObject(last);if("faulted_job_replacement_intent".equals(e.get("type"))){fired=true;try{haltAtBoundary();}catch(Throwable error){error.printStackTrace();throw new IOException(error);}}}}
        public int read(ByteBuffer b)throws IOException{return delegate.read(b);}public long read(ByteBuffer[] b,int o,int l)throws IOException{return delegate.read(b,o,l);}
        public long write(ByteBuffer[] b,int o,int l)throws IOException{return delegate.write(b,o,l);}public int read(ByteBuffer b,long p)throws IOException{return delegate.read(b,p);}public int write(ByteBuffer b,long p)throws IOException{return delegate.write(b,p);}
        public long position()throws IOException{return delegate.position();}public FileChannel position(long p)throws IOException{delegate.position(p);return this;}public long size()throws IOException{return delegate.size();}public FileChannel truncate(long s)throws IOException{delegate.truncate(s);return this;}
        public long transferTo(long p,long c,WritableByteChannel t)throws IOException{return delegate.transferTo(p,c,t);}public long transferFrom(ReadableByteChannel s,long p,long c)throws IOException{return delegate.transferFrom(s,p,c);}
        public MappedByteBuffer map(MapMode m,long p,long s)throws IOException{return delegate.map(m,p,s);}public FileLock lock(long p,long s,boolean sh)throws IOException{return delegate.lock(p,s,sh);}public FileLock tryLock(long p,long s,boolean sh)throws IOException{return delegate.tryLock(p,s,sh);}
        protected void implCloseChannel()throws IOException{delegate.close();}
    }
    static void crashExercise(Map<String,Object> proof)throws Exception {
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
        crashContext=Bridge.map("task_id",taskId,"original_operation_id",original,"old_job_id",oldJobId,"original_placed_history",history,"original_actions",oldActions,"old_material_load_id",oldMaterial,"old_board_load_ids",oldBoardIds,"feed_count",oldFeeds,"original_native_operation",latestOperation(original),"expected_crash_exit",exitCode(),"boundary",boundary,"pid",ProcessHandle.current().pid());
        forceFile("crash-context.json",crashContext);
        installCrashProbe();
        shown.callback.submit(local::currentAuthority).toCompletableFuture().get(60,TimeUnit.SECONDS);
        throw new AssertionError("Expected exact forced crash boundary was never reached");
    }
    static void readAndReconstruct(Map<String,Object> proof,String samples)throws Exception {
        Map<String,Object> marker=NativeJournalJson.parseObject(Files.readString(state.resolve("crash-boundary.json"))),context=NativeJournalJson.parseObject(Files.readString(state.resolve("crash-context.json")));
        check(((Number)marker.get("pid")).longValue()!=ProcessHandle.current().pid(),"Read/reconstruction runs in a distinct JVM after actual Runtime.halt");
        byte[] journal=Files.readAllBytes(state.resolve("journal/operations.jsonl"));Map<String,Object> files=documentInventory();same(marker.get("journal_sha256"),sha(journal),"Copied reader uses exact journal bytes present at crash");
        Configuration.initialize(state.resolve("config").toFile());config=Configuration.get();config.load();NullDriver driver=(NullDriver)config.getMachine().getDrivers().get(0);check(driver.getControlledVacuumSource()==null,"Fresh native configuration has no inherited sensing source");
        bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(samples).toAbsolutePath(),0,true,"native-simulator");
        byte[] replayJournal=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        Map<String,Object> status=read("get_status"),task=read("get_sensing_reconciliation","task_id",context.get("task_id")),p=progress(task);write("replayed-task.json",task);write("replayed-status.json",status);
        check(replayJournal.length>journal.length&&Arrays.equals(journal,Arrays.copyOf(replayJournal,journal.length)),"Bridge recovery preserves the entire original forced crash journal prefix");
        String suffix=new String(replayJournal,journal.length,replayJournal.length-journal.length,StandardCharsets.UTF_8);List<String> suffixRows=suffix.lines().filter(line->!line.isBlank()).toList();
        check(suffixRows.size()==1,"Fresh Bridge appends exactly one interrupted local-operation transition");
        Map<String,Object> recoveryEvent=NativeJournalJson.parseObject(suffixRows.get(0)),recoveredOp=map(recoveryEvent.get("payload")),originalIntent=map(marker.get("intent"));
        same("operation",recoveryEvent.get("type"),"Recovery suffix contains only an operation record and no native/source/load/definition effects");
        same(status.get("bridge_instance_id"),recoveryEvent.get("bridge_instance_id"),"Recovery event envelope names this fresh Bridge instance");
        same(originalIntent.get("recovery_operation_id"),recoveredOp.get("operation_id"),"Recovery transition names exactly the halted local operation");
        same("outcome_unknown",recoveredOp.get("state"),"Halted local operation receives an explicit unknown outcome");
        same(Bridge.map("recovery","prior-instance-interrupted","repeat_action_performed",false),recoveredOp.get("result"),"Recovery records no repeated action");
        Map<String,Object> priorLocal=null;for(String line:new String(journal,StandardCharsets.UTF_8).lines().toList()){Map<String,Object> row=NativeJournalJson.parseObject(line);if("operation".equals(row.get("type"))&&recoveredOp.get("operation_id").equals(map(row.get("payload")).get("operation_id")))priorLocal=map(row.get("payload"));}
        check(priorLocal!=null&&Set.of("accepted","running").contains(priorLocal.get("state")),"The crashed prefix has a genuinely unfinished local operation");
        for(Map.Entry<String,Object> entry:priorLocal.entrySet())if(!Set.of("state","result","updated_at","vacuum_sensing_journal").contains(entry.getKey()))same(entry.getValue(),recoveredOp.get(entry.getKey()),"Recovery retains original local operation "+entry.getKey());
        check(!(recoveredOp.get("native_completion") instanceof Map)||!Boolean.TRUE.equals(map(recoveredOp.get("native_completion")).get("native_wrapper_completed")),"Recovery suffix does not fabricate halted native-wrapper completion");
        check(Boolean.TRUE.equals(task.get("historical"))&&!Boolean.TRUE.equals(task.get("live_resolution_activated"))&&!Boolean.TRUE.equals(task.get("execution_authority_restored")),"Crashed recovery remains historical with no live authority");
        check(task.get("receipt")==null,"Crash cannot synthesize a successful local recovery receipt");
        check(status.get("gui_ownership")==null&&read("get_control_session").get("session_id")==null,"Fresh Bridge has no GUI owner or control lease");
        check(Boolean.FALSE.equals(map(p.get("definition")).get("candidate_retained_in_process")),"Journal replay restores no process-owned candidate");
        check("untouched".equals(map(p.get("document")).get("phase")),"Archive remains unrecorded at either crash boundary");
        for(String component:List.of("material","boards"))for(Object item:(List<?>)map(p.get(component)).get("rows"))check("untouched".equals(map(item).get("phase")),"Crash prefix restores no "+component+" mutation or binding");
        Map<String,Object> intent=map(marker.get("intent"));String attempt=(String)intent.get("replacement_attempt_id");
        NativeFaultedJobReplacement replacement=(NativeFaultedJobReplacement)field(bridge,"faultedJobReplacement");
        Map<String,Object> bundle=replacement.reconstructionBundle(attempt);
        same(intent.get("reconstruction_bundle"),bundle,"Extracted bundle is exactly the reducer-validated forced intent payload");
        NativeReplacementDocuments.validateReconstruction(bundle);
        snapshotDependencies("reader");
        Map<String,Object> reconstructed=config.getMachine().submit(()->{
            NativeFaultedJobReplacement.RestartStage loaded=replacement.stageRestart(attempt);
            Map<String,Object> result;
            try(loaded){
                loaded.requireCurrent();Job original=loaded.original();NativeReplacementJob.Candidate candidate=loaded.candidate();Job fresh=candidate.job();
                same(context.get("original_placed_history"),copy(original.getPlacedStatusSnapshot()),"Reconstructed inert original preserves exact true/false/opaque placed history");
                check(fresh!=original&&fresh.getPlacedStatusSnapshot().isEmpty(),"Reconstructed candidate is distinct and unexecuted");
                check(fresh.getRootPanelLocation()!=original.getRootPanelLocation()&&fresh.getRootPanelLocation().getPanel()!=original.getRootPanelLocation().getPanel(),"Original/candidate root graphs are distinct native objects");
                for(PlacementsHolderLocation<?> old:original.getBoardAndPanelLocations())for(PlacementsHolderLocation<?> next:fresh.getBoardAndPanelLocations())check(old!=next&&old.getPlacementsHolder()!=next.getPlacementsHolder()&&old.getPlacementsHolder().getDefinition()!=next.getPlacementsHolder().getDefinition(),"Reconstruction shares no native board/definition between inert original and replacement");
                for(Job j:List.of(original,fresh))for(PlacementsHolderLocation<?> location:j.getBoardAndPanelLocations())for(Placement placement:location.getPlacementsHolder().getPlacements())if(placement.getPart()!=null)check(config.getPart(placement.getPart().getId())==placement.getPart(),"Reconstructed graph uses exact current canonical native Part");
                same(bundle.get("source_mapping"),candidate.mapping(),"Inert candidate mapping exactly matches atomically forced reconstruction identity and history");
                candidate.requireCurrent();loaded.requireCurrent();
                result=Bridge.map("original_placed_history",copy(original.getPlacedStatusSnapshot()),"candidate_placed_history",copy(fresh.getPlacedStatusSnapshot()),"candidate_mapping",candidate.mapping(),"original_board_locations",original.getBoardLocations().size(),"candidate_board_locations",fresh.getBoardLocations().size(),"native_executor",config.getMachine().isTask(Thread.currentThread()));
            }
            try{loaded.requireCurrent();throw new AssertionError("Closed inert stage admitted");}catch(IOException expected){check(true,"Closed inert restart stage refuses use");}
            try(NativeFaultedJobReplacement.RestartStage fresh=replacement.stageRestart(attempt)){fresh.requireCurrent();same(result.get("candidate_mapping"),fresh.candidate().mapping(),"Fresh inert stage reconstructs same exact mapping after prior stage closes");}
            return result;
        },null,true).get(30,TimeUnit.SECONDS);
        write("reconstructed-native-models.json",reconstructed);
        Map<String,Object> operation=read("get_operation","operation_id",context.get("original_operation_id"));for(Map.Entry<String,Object> entry:map(context.get("original_native_operation")).entrySet())same(entry.getValue(),operation.get(entry.getKey()),"Original durable operation preserves exact "+entry.getKey());
        same(context.get("original_actions"),originalActions((String)context.get("original_operation_id")),"Original native action facts survive crash and inert reconstruction unchanged");
        Map<String,Object> localOp=read("get_operation","operation_id",intent.get("recovery_operation_id"));check(!"succeeded".equals(localOp.get("state")),"Crashed local operation gains no successful wrapper outcome");
        check(!(localOp.get("native_completion") instanceof Map)||!Boolean.TRUE.equals(map(localOp.get("native_completion")).get("native_wrapper_completed")),"Reconstruction never invents completion of the halted native wrapper");
        check(!config.getMachine().isBusy()&&!config.getMachine().isEnabled()&&driver.getControlledVacuumSource()==null,"Inert native reconstruction leaves machine idle disabled and source absent");
        Map<String,Object> after=read("get_sensing_reconciliation","task_id",context.get("task_id"));check(!Boolean.TRUE.equals(map(progress(after).get("definition")).get("candidate_retained_in_process"))&&!Boolean.TRUE.equals(after.get("execution_authority_restored")),"Explicit inert reconstruction does not attach candidate or grant Bridge execution authority");
        bridge.close();bridge=null;check(Arrays.equals(replayJournal,Files.readAllBytes(state.resolve("journal/operations.jsonl"))),"Inert reconstruction and historical reads append no journal bytes after Bridge recovery");same(files,documentInventory(),"Reconstruction consumes only forced bundle and writes no archive/XML/receipt");
        proof.putAll(Bridge.map("passed",true,"original_crash_pid",marker.get("pid"),"original_journal_prefix_unchanged",true,"recovery_suffix_events",1,"recovery_suffix_operation_only",true,"reconstruction_journal_unchanged",true,"journal_after_recovery_sha256",sha(replayJournal),"document_inventory_unchanged",true,"original_unknown_preserved",true,"native_reconstruction_qualified",true,"inert_restart_stage_qualified",true,"live_authority_restored",false,"native_placements",0));
    }
    public static void main(String[] args)throws Exception{
        if(args.length!=4)throw new IllegalArgumentException("Expected samples, exclusive state directory, crash|read, intent-force|zip-before");state=Path.of(args[1]).toAbsolutePath();mode=args[2];boundary=args[3];scenario="lost-before-place";phase=mode;
        if(!Set.of("crash","read").contains(mode)||!Set.of("intent-force","zip-before").contains(boundary))throw new IllegalArgumentException("Unknown phase/boundary");if(mode.equals("crash"))Files.createDirectory(state);int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try{if(mode.equals("read"))readAndReconstruct(proof,args[0]);else{Path cfg=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");Map<String,Object> prepared=GuiSensingFixture.prepare(cfg,manifest,scenario);Configuration.get().getMachine().close();Configuration.initialize(cfg.toFile());config=Configuration.get();config.load();Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),scenario));write("fixture-attestation.json",claimed);Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);local=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",local);crashExercise(proof);}}
        catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally{try{if(bridge!=null)bridge.close();proof.put("bridge_closed",true);}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}try{if(local!=null)local.release();}catch(Throwable failure){proof.put("gate_release_failure",failure.toString());exit=1;}try{if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){proof.put("machine_close_failure",failure.toString());exit=1;}if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("assertions",checks.size(),"checks",checks,"public_calls",calls,"pid",ProcessHandle.current().pid(),"mode",mode,"boundary",boundary,"scenario",scenario,"restart_execution_qualified",false,"restart_reattachment_qualified",false,"hardware_qualified",false,"public_package_qualified",false));write(mode+"-proof.json",proof);System.out.println("NATIVE_REPLACEMENT_INTENT_CRASH_RESULT "+JSON.toJson(proof));}System.exit(exit);
    }
}
