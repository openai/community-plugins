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
 * qualify MainFrame, a human gesture, physical sensing. It proves current-process continuation after an actual native tray-counter setter interruption through public tools. */
public final class NativeFaultedSensingTrayContinuationBridgeTest {
    static final Gson JSON=new GsonBuilder().serializeNulls().create();
    static final List<String> checks=Collections.synchronizedList(new ArrayList<>());
    static Configuration config;
    static Bridge bridge;
    static LocalAdapter local;
    static Path state;
    static String session;
    static int calls;
    static String scenario="retained-after-place",phase="live";

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
        Files.writeString(state.resolve(phase+"-calls.jsonl"),JSON.toJson(record)+"\n",StandardCharsets.UTF_8,
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
        int publicationAttempts;
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
            check(machine.isTask(Thread.currentThread()),"Actual native job is published from the native executor");publicationAttempts++;
            job=value;
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
    static void same(Object expected,Object actual,String message)throws Exception {check(NativeFaultedJobReplacement.same(expected,actual),message);}
    static void journalParent(String type,String idKey,Map<String,Object> payload,Object digest)throws Exception {
        int matches=0;for(Map<String,Object> event:events())if(type.equals(event.get("type"))&&Objects.equals(payload.get(idKey),map(event.get("payload")).get(idKey))){matches++;same(payload,event.get("payload"),"Progress parent matches exact forced "+type);}
        check(matches==1,"Progress parent has one forced "+type+" record");if(digest!=null)check(digest.equals(NativeFaultedJobReplacement.digest(payload)),"Progress parent canonical digest matches "+type);
    }
    static Map<String,Object> documentInventory()throws Exception {
        Path root=state.resolve("journal/native-replacement-documents");Map<String,Object> result=new TreeMap<>();
        check(Files.isDirectory(root,LinkOption.NOFOLLOW_LINKS)&&!Files.isSymbolicLink(root),"Replacement document store remains an owned regular directory");
        try(java.util.stream.Stream<Path> files=Files.list(root)){for(Path path:(Iterable<Path>)files.sorted()::iterator){
            check(Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS)&&!Files.isSymbolicLink(path),"Replacement document artifact is a regular file");
            result.put(path.getFileName().toString(),Bridge.map("bytes",Files.size(path),"sha256",sha(Files.readAllBytes(path)),"permissions",java.nio.file.attribute.PosixFilePermissions.toString(Files.getPosixFilePermissions(path))));
        }}return result;
    }
    static void assertDocument(Map<String,Object> progress,Map<String,Object> definition)throws Exception {
        Map<String,Object> document=map(progress.get("document"));check(document!=null&&"completed".equals(document.get("phase")),"Persisted replacement candidate document phase is completed");
        check(Boolean.FALSE.equals(document.get("storage_verified_by_this_read"))&&Boolean.FALSE.equals(document.get("reattachment_performed")),"Historical document DTO claims neither storage verification nor reattachment");
        Map<String,Object> record=map(document.get("record")),manifest=map(record.get("manifest")),context=map(manifest.get("context"));
        journalParent("faulted_job_replacement_document","replacement_attempt_id",record,document.get("record_sha256"));
        check(NativeFaultedJobReplacement.digest(manifest).equals(record.get("manifest_sha256")),"Document receipt binds the exact candidate manifest digest");
        for(String key:List.of("replacement_attempt_id","recovery_operation_id","fault_set_sha256","capture_sha256"))same(progress.get(key),record.get(key),"Document receipt binds progress "+key);
        for(String key:List.of("replacement_attempt_id","recovery_operation_id","fault_set_sha256","original_job_id"))same(progress.get(key),context.get(key),"Candidate archive context binds progress "+key);
        same(map(definition.get("record")).get("mapping"),manifest.get("source_mapping"),"Candidate archive binds exact original-to-fresh definition mapping");
        check(NativeFaultedJobReplacement.digest(map(manifest.get("source_mapping"))).equals(manifest.get("source_mapping_sha256"))&&NativeFaultedJobReplacement.digest(map(manifest.get("candidate_graph"))).equals(manifest.get("candidate_graph_sha256")),"Candidate graph and source mapping retain canonical hashes");
        check("openpnp-replacement-candidate-v1".equals(manifest.get("format"))&&Bridge.UPSTREAM.equals(manifest.get("upstream_commit")),"Candidate document declares exact format and pinned OpenPnP upstream");
        check(((Number)manifest.get("candidate_history_entries")).intValue()==0&&Boolean.FALSE.equals(manifest.get("job_installed"))&&Boolean.FALSE.equals(manifest.get("native_job_initialized"))&&Boolean.FALSE.equals(manifest.get("execution_authority_restored"))&&Boolean.FALSE.equals(manifest.get("load_presence_verified")),"Saved candidate carries empty history and no job or execution authority");
        List<Map<String,Object>> journal=events();int documentIndex=-1,firstIntervention=-1,firstDisposal=-1;
        for(int i=0;i<journal.size();i++){Map<String,Object> event=journal.get(i),payload=map(event.get("payload"));String type=(String)event.get("type");
            if(type.equals("faulted_job_replacement_document")&&Objects.equals(record.get("replacement_attempt_id"),payload.get("replacement_attempt_id")))documentIndex=i;
            if(Objects.equals(progress.get("recovery_operation_id"),payload.get("operation_id"))){if(firstIntervention<0&&type.equals("sensing_source_intervention_intent"))firstIntervention=i;if(firstDisposal<0&&type.equals("sensing_source_disposal_intent"))firstDisposal=i;}
        }
        check(documentIndex>=0&&firstIntervention>documentIndex&&firstDisposal>documentIndex,"Candidate document receipt was forced before the first native source repair and disposal intent");
        String id=(String)record.get("document_id");check(id.matches("[a-f0-9]{64}"),"Candidate archive has a strict content identity");Path root=state.resolve("journal/native-replacement-documents");byte[] archive=Files.readAllBytes(root.resolve(id+".zip"));check(sha(archive).equals(id),"Persisted candidate ZIP still matches document content identity");
        Map<String,byte[]> entries=new TreeMap<>();try(java.util.zip.ZipInputStream zip=new java.util.zip.ZipInputStream(new java.io.ByteArrayInputStream(archive))){java.util.zip.ZipEntry entry;while((entry=zip.getNextEntry())!=null){check(!entry.isDirectory()&&!entries.containsKey(entry.getName()),"Candidate ZIP entries are unique files");entries.put(entry.getName(),zip.readAllBytes());}}
        check(entries.keySet().equals(Set.of("manifest.json","job.job.xml")),"Candidate ZIP contains only the closed manifest and native job document");
        same(manifest,NativeJournalJson.parseObject(new String(entries.get("manifest.json"),StandardCharsets.UTF_8)),"Actual persisted candidate manifest equals the forced document record");
        check(sha(entries.get("job.job.xml")).equals(manifest.get("native_job_xml_sha256")),"Actual persisted native job XML matches the manifest digest");
        Map<String,Object> wrapper=NativeJournalJson.parseObject(Files.readString(root.resolve(id+".receipt.json"))),receipt=NativeJournalJson.parseObject((String)wrapper.get("payload"));
        same(id,receipt.get("sha256"),"Private document receipt binds exact candidate ZIP");check(((Number)receipt.get("archive_bytes")).intValue()==archive.length,"Private receipt preserves archive size");
        Map<String,Object> hashes=new TreeMap<>();for(Map.Entry<String,byte[]> entry:entries.entrySet())hashes.put(entry.getKey(),sha(entry.getValue()));same(hashes,receipt.get("entry_hashes"),"Private receipt preserves every candidate entry hash");
        same(manifest.get("required_parts"),receipt.get("parts"),"Private document receipt binds required native parts");same(manifest.get("required_packages"),receipt.get("packages"),"Private document receipt binds required native packages");
        javax.crypto.Mac mac=javax.crypto.Mac.getInstance("HmacSHA256");mac.init(new javax.crypto.spec.SecretKeySpec(Files.readAllBytes(root.resolve("receipt-key")),"HmacSHA256"));
        check(java.util.HexFormat.of().formatHex(mac.doFinal(((String)wrapper.get("payload")).getBytes(StandardCharsets.UTF_8))).equals(wrapper.get("hmac_sha256")),"Private candidate receipt remains authenticated without invoking reconstruction");
    }
    static Map<String,Object> progress(Map<String,Object> task) {
        check(!task.containsKey("job_replacement_progress_error"),"Task progress read has no error: "+task.get("job_replacement_progress_error"));
        List<?> rows=(List<?>)task.get("job_replacements");check(rows!=null&&rows.size()==1,"Task names one retained replacement transaction");return map(rows.get(0));
    }
    static Map<String,Object> awaitRecovery(String taskId)throws Exception {
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(40);
        while(System.nanoTime()<deadline){Map<String,Object> snapshot=read("get_sensing_reconciliation","task_id",taskId);Object operation=snapshot.get("recovery_operation_id");if(operation!=null){Map<String,Object> current=read("get_operation","operation_id",operation);if(Set.of("succeeded","failed","outcome_unknown","aborted").contains(current.get("state"))&&current.get("native_completion") instanceof Map&&Boolean.TRUE.equals(map(current.get("native_completion")).get("native_wrapper_completed"))&&!config.getMachine().isBusy())return snapshot;}Thread.sleep(10);}
        throw new AssertionError("Local native recovery did not terminate");
    }
    static Map<String,Object> latestOperation(String operation)throws Exception {
        Map<String,Object> result=null;for(Map<String,Object> row:events())if("operation".equals(row.get("type"))&&operation.equals(map(row.get("payload")).get("operation_id")))result=map(row.get("payload"));if(result==null)throw new AssertionError("Missing forced operation");return copy(result);
    }
    static List<Map<String,Object>> originalComponentRecords()throws Exception {
        List<Map<String,Object>> result=new ArrayList<>();for(Map<String,Object> event:events())if(Set.of("job_lineage_replacement","material_replacement_intent","material_replacement_outcome","board_replacement_intent","board_replacement_outcome","faulted_job_replacement_definition","faulted_job_replacement_document","faulted_job_replacement_outcome","faulted_job_replacement_publication_intent","faulted_job_replacement_publication_outcome").contains(event.get("type")))result.add(copy(event));return result;
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
        check(nativeTray.getFeedCount()>0,"Actual failed native job consumed a nonzero tray index before setter injection");
        int originalNativeIndex=nativeTray.getFeedCount();int[] setterEvents={0};Map<String,Object>[] setterWitness=new Map[]{null};
        Map<String,String> settingsBefore=configurationFileHashes();
        java.beans.PropertyChangeListener listener=event->{
            if(((Number)event.getNewValue()).intValue()!=0||((Number)event.getOldValue()).intValue()<=0||setterEvents[0]!=0)return;
            setterEvents[0]++;
            check(event.getSource()==nativeTray&&config.getMachine().isTask(Thread.currentThread()),"Injected listener observes the exact real tray setter on the native executor");
            check(nativeTray.getFeedCount()==0,"Actual tray counter is zero before the listener throws");
            setterWitness[0]=Bridge.map("feeder_id",nativeTray.getId(),"old_index",event.getOldValue(),"new_index",event.getNewValue(),"observed_native_index",nativeTray.getFeedCount(),"property",event.getPropertyName(),"native_executor",true,"thread",Thread.currentThread().getName());
            try{write("native-setter-interruption.json",setterWitness[0]);}catch(Exception failure){throw new IllegalStateException(failure);}
            throw new NativeTraySetterInterruption();
        };
        nativeTray.addPropertyChangeListener("feedCount",listener);
        try{try{write("first-local-callback-result.json",shown.callback.submit(local::currentAuthority).toCompletableFuture().get(60,TimeUnit.SECONDS));}
            catch(ExecutionException failure){write("first-local-callback-failure.json",Bridge.map("class",failure.getCause().getClass().getName(),"message",failure.getCause().getMessage()));}}
        finally{nativeTray.removePropertyChangeListener("feedCount",listener);}
        Map<String,Object> interrupted=awaitRecovery(taskId);write("interrupted-task.json",interrupted);
        String firstRecovery=(String)interrupted.get("recovery_operation_id");Map<String,Object> firstOperation=read("get_operation","operation_id",firstRecovery);
        check(setterEvents[0]==1&&setterWitness[0]!=null&&nativeTray.getFeedCount()==0,"One actual nonzero-to-zero setter notification threw exactly once");
        check(((Number)setterWitness[0].get("old_index")).intValue()==originalNativeIndex,"Actual setter event binds the previously consumed index");
        check(settingsBefore.equals(configurationFileHashes()),"Counter interruption occurred before Configuration.save changed settings files");
        check("outcome_unknown".equals(firstOperation.get("state"))&&Boolean.TRUE.equals(map(firstOperation.get("native_completion")).get("native_wrapper_completed")),"First native recovery wrapper ended with an unknown outcome");
        check("SENSING_RECOVERY_FAILED".equals(result(firstOperation).get("code"))&&"Controlled native tray counter interruption".equals(result(firstOperation).get("message")),"Bridge retained the exact thrown native setter failure");
        check(!Boolean.TRUE.equals(read("get_status").get("journal_fault")),"Known native setter failure does not classify the healthy journal as damaged");
        check(!"resolved_for_current_scope".equals(interrupted.get("state"))&&!Boolean.TRUE.equals(interrupted.get("live_resolution_activated")),"Interrupted tray initialization establishes no sensing resolution");
        Map<String,Object> interruptedProgress=progress(interrupted);write("interrupted-progress.json",interruptedProgress);
        String attempt=(String)interruptedProgress.get("replacement_attempt_id");Map<String,Object> pending=materialRow(interruptedProgress,oldMaterial);
        check("pending".equals(pending.get("phase"))&&pending.get("parent_outcome")==null,"Original replacement remains an exact pending tray intent");
        Map<String,Object> parent=map(map(pending.get("parent_intent")).get("payload")),unknown=map(parent.get("new_load"));String unknownId=(String)unknown.get("load_id");
        check(!unknownId.equals(oldMaterial)&&"loading_unknown".equals(unknown.get("state")),"Forced unknown replacement has its own distinct load identity");
        check(unknownId.equals(pending.get("current_load_id"))&&Boolean.FALSE.equals(map(pending.get("binding_status")).get("retained_binding_present")),"Unknown zero-index load is current but has no execution binding");
        journalParent("material_replacement_intent","receipt_id",parent,map(pending.get("parent_intent")).get("sha256"));
        check(count("material_replacement_intent")==1&&count("material_replacement_outcome")==0,"One forced replacement intent has no fabricated successful outcome");
        check(count("faulted_job_replacement_outcome")==0&&count("faulted_job_replacement_publication_intent")==0&&count("faulted_job_replacement_publication_outcome")==0,"Counter interruption precedes aggregate and native publication");
        check("completed".equals(map(interruptedProgress.get("lineage")).get("phase")),"Original lineage retirement completed before the tray setter");
        for(Object raw:(List<?>)map(interruptedProgress.get("boards")).get("rows"))check("untouched".equals(map(raw).get("phase")),"Original board replacement has not run before the failed tray setter");
        assertDocument(interruptedProgress,map(interruptedProgress.get("definition")));
        check(local.job==oldJob&&nozzle().getPart()==null&&!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"Failed tray replacement leaves original job selected and actual machine disabled, idle and empty");
        check(Boolean.FALSE.equals(nozzle().getVacuumActuator().getLastActuationValue()),"Failed replacement leaves actual vacuum valve off");
        Map<String,Object> firstForcedOperation=latestOperation(firstRecovery),unknownPublic=load(read("get_material_loads"),unknownId);List<Map<String,Object>> immutableParents=originalComponentRecords();Map<String,Object> savedDocuments=documentInventory();
        long firstDisposals=count("sensing_source_disposal_returned"),firstFeeds=feedCount();check(firstDisposals>0&&firstFeeds==0,"Native disposal completed, while zero tray counter retains unknown initialization");
        JsonObject continuationRequest=command("recovery_kind","continue-faulted-job-replacement","replacement_attempt_id",attempt);Map<String,Object> nextRequest=run("request_sensing_reconciliation",continuationRequest);
        Presentation nextShown=local.presentations.poll(10,TimeUnit.SECONDS);check(nextShown!=null,"Fresh continuation request presents a new process-owned local decision");String nextTaskId=(String)nextShown.task.get("task_id");write("continuation-presented-task.json",nextShown.task);
        Map<String,Object> nextCapture=map(map(nextShown.task.get("snapshot")).get("replacement")),nextDependencies=map(nextCapture.get("dependencies"));
        check(!nextTaskId.equals(taskId)&&map(nextCapture.get("prior_operations")).containsKey(firstRecovery),"Fresh task captures exact prior completed unknown wrapper");
        check(((List<?>)nextDependencies.get("material_load_ids")).containsAll(List.of(oldMaterial,unknownId)),"Fresh continuation captures original and unknown tray identities");
        check(((List<?>)nextDependencies.get("operation_ids")).containsAll(List.of(original,firstRecovery)),"Fresh continuation captures both original native and failed local operations");
        check(feedCount()==firstFeeds&&immutableParents.equals(originalComponentRecords()),"Remote request performs no manufacturing or material replacement");
        Map<String,Object> callback=nextShown.callback.submit(local::currentAuthority).toCompletableFuture().get(60,TimeUnit.SECONDS);write("continuation-local-callback-result.json",callback);
        Map<String,Object> resolved=awaitRecovery(nextTaskId);write("continuation-resolved-task.json",resolved);check("resolved_for_current_scope".equals(resolved.get("state"))&&Boolean.TRUE.equals(resolved.get("live_resolution_activated")),"Fresh local continuation resolves after successful wrapper and final publication");
        Map<String,Object> continuedProgress=progress(resolved);write("continuation-progress.json",continuedProgress);Map<String,Object> material=materialRow(continuedProgress,oldMaterial);
        check("pending".equals(material.get("phase"))&&"completed".equals(material.get("effective_phase")),"Original pending phase remains while a distinct continuation completed");
        same(parent,map(map(material.get("parent_intent")).get("payload")),"Original forced pending intent is unchanged");check(material.get("parent_outcome")==null,"Original unknown load never receives a successful outcome");
        List<?> chain=(List<?>)material.get("continuation_chain");check(chain.size()==1,"Exactly one fresh material continuation is recorded");Map<String,Object> nextIntent=map(map(map(chain.get(0)).get("intent")).get("payload")),nextOutcome=map(map(map(chain.get(0)).get("outcome")).get("payload"));
        String newMaterial=(String)map(nextOutcome.get("new_load")).get("load_id");
        check("supersede-unknown".equals(nextIntent.get("mode"))&&unknownId.equals(nextIntent.get("previous_load_id"))&&"material_replacement_intent".equals(nextIntent.get("parent_type")),"Fresh intent explicitly supersedes the unknown parent without inferring success from zero");
        same(unknown,nextIntent.get("previous_load"),"Fresh intent retains the full exact unknown load");same(parent.get("receipt_id"),nextIntent.get("parent_receipt_id"),"Fresh intent binds original pending receipt identity");same(NativeFaultedJobReplacement.digest(parent),nextIntent.get("parent_sha256"),"Fresh intent binds original pending receipt hash");
        check(!Set.of(oldMaterial,unknownId).contains(newMaterial)&&"loaded".equals(map(nextOutcome.get("new_load")).get("state"))&&((Number)nextIntent.get("native_index_before")).intValue()==0,"Continuation creates a third load identity from observed zero with its own successful receipt");
        journalParent("material_continuation_intent","receipt_id",nextIntent,null);journalParent("material_continuation_outcome","receipt_id",nextOutcome,null);
        check(count("material_replacement_outcome")==0&&count("material_continuation_intent")==1&&count("material_continuation_outcome")==1&&count("material_continuation_adoption")==0,"Unknown replacement is superseded once, never adopted or retroactively completed");
        check(count("job_lineage_replacement")==1&&count("job_lineage_continuation_adoption")==1&&count("job_lineage_continuation_replacement")==0,"Completed lineage is adopted without repeating retirement");
        check(count("board_replacement_intent")==0&&count("board_continuation_intent")==oldBoardIds.size()&&count("board_continuation_outcome")==oldBoardIds.size(),"Untouched boards are replaced only by the new continuation");
        same(immutableParents,originalComponentRecords(),"Continuation preserves every original component record");same(savedDocuments,documentInventory(),"Continuation verifies and reuses the original native candidate archive unchanged");
        check(firstDisposals==count("sensing_source_disposal_returned")&&feedCount()==0,"Already-empty nozzle is not disposed again, and no manufacturing feed ran during continuation");
        same(firstForcedOperation,latestOperation(firstRecovery),"Prior local unknown operation remains exact");check(failed.equals(read("get_operation","operation_id",original))&&oldActions.equals(originalActions(original))&&history.equals(copy(oldJob.getPlacedStatusSnapshot())),"Original failed native job, actions and placed history remain unchanged");
        Map<String,Object> status=read("get_status"),newMaterials=read("get_material_loads"),newBoards=read("get_board_loads");write("continuation-status.json",status);String newJobId=(String)status.get("job_id");Job fresh=local.job;
        check(attempt.equals(newJobId)&&!oldJobId.equals(newJobId)&&fresh!=oldJob&&"prepared".equals(status.get("job_state")),"Continuation publishes a distinct prepared job with exact attempt identity");
        check(nativeTray.getFeedCount()==0&&Boolean.TRUE.equals(load(newMaterials,newMaterial).get("active"))&&Boolean.FALSE.equals(load(newMaterials,unknownId).get("active")),"New successful load is active and old unknown candidate is inactive");
        for(String field:List.of("current_index","observed_advances","state","initial_index","created_at")){same(oldMaterialRecord.get(field),load(newMaterials,oldMaterial).get(field),"Original consumed load preserves "+field);same(unknownPublic.get(field),load(newMaterials,unknownId).get(field),"Unknown load preserves "+field);}
        Set<String> newBoardIds=ids((List<?>)newBoards.get("roots"),"load_id");check(Collections.disjoint(oldBoardIds,newBoardIds),"Untouched boards receive distinct new identities");
        for(String id:oldBoardIds)check(((Map<?,?>)newBoards.get("quarantined_loads")).containsKey(id),"Old uncertain board remains quarantined");
        String recoveryId=(String)resolved.get("recovery_operation_id");List<Map<String,Object>> ordered=events();int publicationAt=eventIndex(ordered,"faulted_job_replacement_continuation_publication_outcome","recovery_operation_id",recoveryId,null),verifiedAt=eventIndex(ordered,"sensing_reconciliation_verified","task_id",nextTaskId,null),terminalAt=eventIndex(ordered,"operation","operation_id",recoveryId,"succeeded"),resolutionAt=eventIndex(ordered,"sensing_reconciliation_resolved","task_id",nextTaskId,null);
        check(publicationAt>=0&&publicationAt<verifiedAt&&verifiedAt<terminalAt&&terminalAt<resolutionAt,"Real Bridge orders publication, verification, wrapper terminal, and resolution");
        refused(()->nextShown.callback.submit(local::currentAuthority).toCompletableFuture().get(10,TimeUnit.SECONDS),null,"Consumed continuation callback cannot execute again");
        Map<String,Object> replay=call("request_sensing_reconciliation",continuationRequest);check(nextRequest.get("operation_id").equals(replay.get("operation_id"))&&local.presentations.isEmpty(),"Repeated remote request creates no new local task");
        run("set_machine_enabled",command("enabled",true));run("home_machine",command());Map<String,Object> refusedStart=call("start_job",command("job_id",newJobId));check("failed".equals(refusedStart.get("state"))&&"JOB_NOT_VALIDATED".equals(result(refusedStart).get("code")),"Fresh continuation cannot reuse old job validation");
        run("locate_fiducials",command("job_id",newJobId));Map<String,Object> validatedFresh=run("validate_job",command("job_id",newJobId));check(Boolean.TRUE.equals(result(validatedFresh).get("valid")),"Replacement passes fresh native registration and validation");
        Map<String,Object> completed=run("start_job",command("job_id",newJobId));write("continuation-completed-job.json",completed);check(((Number)result(completed).get("placed")).intValue()==1&&nativeTray.getFeedCount()==1,"Actual native job performs one placement from the third tray identity");
        Map<String,Object> finalMaterials=read("get_material_loads");check(((Number)load(finalMaterials,newMaterial).get("observed_advances")).intValue()==1,"Actual native feed advances only the new successful tray load");
        for(String field:List.of("current_index","observed_advances","state","initial_index","created_at")){same(oldMaterialRecord.get(field),load(finalMaterials,oldMaterial).get(field),"Placement preserves original consumed "+field);same(unknownPublic.get(field),load(finalMaterials,unknownId).get(field),"Placement preserves unknown candidate "+field);}
        same(firstForcedOperation,latestOperation(firstRecovery),"Placement never changes failed local wrapper outcome");check(oldActions.equals(originalActions(original))&&failed.equals(read("get_operation","operation_id",original))&&history.equals(copy(oldJob.getPlacedStatusSnapshot())),"Fresh placement preserves original native fault and history");
        run("set_machine_enabled",command("enabled",false));read("release_control_session","session_id",session,"request_id",UUID.randomUUID().toString());
        byte[] after=Files.readAllBytes(state.resolve("journal/operations.jsonl"));check(after.length>prior.length&&Arrays.equals(prior,Arrays.copyOf(after,prior.length)),"Original journal remains an exact append-only prefix");
        write("final-status.json",read("get_status"));proof.putAll(Bridge.map("original_operation_id",original,"original_job_id",oldJobId,"replacement_attempt_id",attempt,"initial_recovery_task_id",taskId,"initial_recovery_operation_id",firstRecovery,"continuation_task_id",nextTaskId,"continuation_recovery_operation_id",recoveryId,"actual_setter_interruptions",setterEvents[0],"setter_witness",setterWitness[0],"original_load_id",oldMaterial,"unknown_load_id",unknownId,"successful_continuation_load_id",newMaterial,"replacement_actual_simulator_placements",1,"original_outcome",failed.get("state"),"original_recovery_outcome",firstOperation.get("state"),"original_native_history",history,"completed_steps_repeated",false,"additional_disposals",count("sensing_source_disposal_returned")-firstDisposals,"journal_before_sha256",sha(prior),"journal_after_sha256",sha(after)));
    }
    static final class NativeTraySetterInterruption extends RuntimeException {NativeTraySetterInterruption(){super("Controlled native tray counter interruption");}}
    static Map<String,String> configurationFileHashes()throws Exception {Map<String,String> result=new TreeMap<>();try(var files=Files.list(state.resolve("config"))){for(Path p:files.filter(x->x.getFileName().toString().endsWith(".xml")).toList())result.put(p.getFileName().toString(),sha(Files.readAllBytes(p)));}return result;}
    static Map<String,Object> materialRow(Map<String,Object> progress,String oldId){for(Object raw:(List<?>)map(progress.get("material")).get("rows"))if(oldId.equals(map(raw).get("old_load_id")))return map(raw);throw new AssertionError("Missing original material row");}

    public static void main(String[] args)throws Exception {
        if(args.length<2||args.length>3)throw new IllegalArgumentException("Expected pinned sample root, exclusive state directory, optional scenario");
        if(args.length==3)scenario=args[2];if(!Set.of("retained-after-place","lost-before-place","invalid-read").contains(scenario))throw new IllegalArgumentException("Unsupported source scenario");
        state=Path.of(args[1]).toAbsolutePath();Files.createDirectory(state);int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try {
            Path configDir=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");Map<String,Object> prepared=GuiSensingFixture.prepare(configDir,manifest,scenario);
            Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();
            Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),scenario));write("fixture-attestation.json",claimed);
            check(Boolean.TRUE.equals(claimed.get("sensing_fixture_attested")),"Exact prepared native GUI-style source is attested in the current process");
            Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);local=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));
            bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",local);exercise(proof);proof.put("passed",true);
        } catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally {
            try{if(bridge!=null){if(exit!=0)bridge.recordGuiUnknownExit();bridge.close();}proof.put("bridge_closed",true);}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}
            try{if(local!=null){local.release();check(!local.gate.owns(local.token),"Owned native gate released during cleanup");}proof.put("native_gate_released",true);}catch(Throwable failure){proof.put("gate_release_failure",failure.toString());exit=1;}
            try{if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){proof.put("machine_close_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("checks",checks,"assertions",checks.size(),"public_calls",calls,"pid",ProcessHandle.current().pid(),"synthetic_source_scenario",scenario,"local_adapter","actual native gate; programmatic non-Swing local callback; one intentional actual native tray-counter property listener failure","swing_qualification",false,"mainframe_qualification",false,"hardware_qualified",false,"interrupted_replacement_continuation_qualified",Boolean.TRUE.equals(proof.get("passed")),"continuation_scope","current-process retained candidate after actual nonzero-to-zero tray setter interruption; explicit unknown load supersession and one fresh placement","restart_reattachment_qualified",false,"public_package_qualified",false));write("live-proof.json",proof);System.out.println("NATIVE_FAULTED_SENSING_TRAY_CONTINUATION_BRIDGE_RESULT "+JSON.toJson(proof));
        }
        System.exit(exit);
    }
}
