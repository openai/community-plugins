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
public final class NativeFaultedSensingReplacementProgressBridgeTest {
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
    static Map<String,Object> assertProgress(Map<String,Object> task,boolean consumed,boolean historical)throws Exception {
        check(!task.containsKey("job_replacement_progress_error"),"Supported task read has no job_replacement_progress_error: "+task.get("job_replacement_progress_error"));
        check(task.get("job_replacements") instanceof List&&((List<?>)task.get("job_replacements")).size()==1,"Task exposes one exact replacement transaction");
        Map<String,Object> progress=map(((List<?>)task.get("job_replacements")).get(0)),deps=map(progress.get("original_dependencies"));
        check(Boolean.TRUE.equals(progress.get("original_outcomes_preserved"))&&Boolean.FALSE.equals(progress.get("execution_authority_restored"))&&Boolean.FALSE.equals(progress.get("continuation_supported")),"Progress preserves outcomes without granting continuation or authority");
        for(String field:List.of("material","boards")){
            Map<String,Object> component=map(progress.get(field));List<?> rows=(List<?>)component.get("rows");
            check(ids(rows,"old_load_id").equals(new TreeSet<>((List<String>)deps.get(field.equals("material")?"material_load_ids":"board_load_ids"))),"Progress contains complete original "+field+" union");
            check(Boolean.FALSE.equals(component.get("execution_authority_restored")),"Component progress grants no "+field+" authority");
            for(Object raw:rows){Map<String,Object> row=map(raw);check("completed".equals(row.get("phase")),"Exact historical "+field+" replacement phase is completed");
                for(String parentName:List.of("parent_intent","parent_outcome")){Map<String,Object> parent=map(row.get(parentName));journalParent((String)parent.get("type"),"receipt_id",map(parent.get("payload")),parent.get("payload_sha256"));}
                Map<String,Object> binding=map(row.get("binding_status"));
                if(field.equals("material")){
                    check(Boolean.TRUE.equals(row.get("replacement_still_active")),"Fresh replacement tray remains the active identity");check(Boolean.valueOf(!consumed).equals(row.get("current_matches_replacement_receipt")),"Current tray state is separate from immutable zero-index receipt");
                    check(((Number)map(row.get("replacement_load_current")).get("current_index")).intValue()==(consumed?1:0),"Progress reports actual replacement tray consumption");
                    if(historical)check(Boolean.FALSE.equals(binding.get("retained_binding_present")),"Restart phase read restores no native tray binding");
                }else if(historical)check(Boolean.FALSE.equals(binding.get("native_job_binding_retained"))&&Boolean.FALSE.equals(binding.get("root_confirmation_retained")),"Restart phase read restores no native board binding or confirmation");
            }
        }
        Map<String,Object> lineage=map(progress.get("lineage"));check("completed".equals(lineage.get("phase")),"Lineage retirement phase is completed");journalParent("job_lineage_replacement","receipt_id",map(lineage.get("receipt")),lineage.get("receipt_sha256"));
        Map<String,Object> definition=map(progress.get("definition")),compound=map(progress.get("compound")),publication=map(progress.get("publication"));
        check("completed".equals(definition.get("phase"))&&"completed".equals(compound.get("phase"))&&"completed".equals(publication.get("phase")),"Definition, compound and native publication phases are completed");
        journalParent("faulted_job_replacement_definition","replacement_attempt_id",map(definition.get("record")),definition.get("record_sha256"));journalParent("faulted_job_replacement_outcome","replacement_attempt_id",map(compound.get("record")),compound.get("record_sha256"));
        journalParent("faulted_job_replacement_publication_intent","replacement_attempt_id",map(publication.get("intent")),null);journalParent("faulted_job_replacement_publication_outcome","replacement_attempt_id",map(publication.get("outcome")),null);
        check(Objects.equals(map(publication.get("outcome")).get("definition_sha256"),definition.get("record_sha256"))&&Objects.equals(map(publication.get("outcome")).get("replacement_outcome_sha256"),compound.get("record_sha256")),"Native publication receipt binds exact definition and compound hashes");
        assertDocument(progress,definition);
        if(historical)check(Boolean.FALSE.equals(definition.get("candidate_retained_in_process")),"Restart read does not reconstruct process-local candidate");
        return progress;
    }
    static Map<String,Object> persistedOperation(String id)throws Exception {
        Map<String,Object> result=null;for(Map<String,Object> row:events())if("operation".equals(row.get("type"))&&id.equals(map(row.get("payload")).get("operation_id")))result=map(row.get("payload"));if(result==null)throw new AssertionError("No persisted operation");return result;
    }
    static void history(Path samples,Map<String,Object> proof)throws Exception {
        Map<String,Object> expected=NativeJournalJson.parseObject(Files.readString(state.resolve("expected-progress-history.json")));
        check(((Number)expected.get("original_pid")).longValue()!=ProcessHandle.current().pid(),"History read runs in a distinct actual JVM");
        Map<String,Object> documentBefore=documentInventory();same(expected.get("document_inventory"),documentBefore,"Fresh JVM starts with exact persisted candidate artifacts");
        byte[] before=Files.readAllBytes(state.resolve("journal/operations.jsonl"));Configuration.initialize(state.resolve("config").toFile());config=Configuration.get();config.load();
        org.openpnp.machine.reference.driver.NullDriver driver=(org.openpnp.machine.reference.driver.NullDriver)config.getMachine().getDrivers().get(0);
        check(driver.getControlledVacuumSource()==null,"Fresh configuration reload has no process-owned sensor source");
        bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),samples,0,true,"native-simulator");
        Map<String,Object> capabilities=read("get_capabilities");write("history-capabilities.json",capabilities);
        check(((List<?>)capabilities.get("tools")).contains("openpnp_get_sensing_reconciliation")&&!((List<?>)capabilities.get("tools")).contains("openpnp_request_sensing_reconciliation"),"Historical read is advertised while local recovery requests remain unavailable");
        check(Boolean.FALSE.equals(map(capabilities.get("vacuum_sensing")).get("available"))&&Boolean.FALSE.equals(map(capabilities.get("sensing_reconciliation")).get("available")),"History has neither live sensing nor local recovery availability");
        Map<String,Object> status=read("get_status");check(status.get("gui_ownership")==null&&read("get_control_session").get("session_id")==null,"Historical Bridge has no GUI ownership or lease");
        String taskId=(String)expected.get("task_id");Map<String,Object> task=read("get_sensing_reconciliation","task_id",taskId),progress=assertProgress(task,true,true),originalTask=map(expected.get("task")),originalProgress=map(expected.get("progress"));
        check(Boolean.TRUE.equals(task.get("historical"))&&Boolean.FALSE.equals(task.get("live_resolution_activated"))&&Boolean.FALSE.equals(task.get("execution_authority_restored")),"Replayed resolution remains historical with no current readiness");
        for(String key:List.of("task","state","recovery_operation_id","local_action","intervention","verification","receipt"))same(originalTask.get(key),task.get(key),"History preserves exact original task "+key);
        for(String key:List.of("replacement_attempt_id","task_id","recovery_operation_id","fault_set_sha256","capture_sha256","original_job_id","original_operation_id","original_dependencies","lineage","document","compound","publication","local_operation"))same(originalProgress.get(key),progress.get(key),"History preserves exact replacement "+key);
        for(String component:List.of("material","boards")){
            List<?> previous=(List<?>)map(originalProgress.get(component)).get("rows"),current=(List<?>)map(progress.get(component)).get("rows");check(previous.size()==current.size(),"Historical "+component+" row count unchanged");
            for(int i=0;i<previous.size();i++)for(String key:List.of("old_load_id","phase","old_load","current_load_id","current_load","new_load","parent_intent","parent_outcome"))same(map(previous.get(i)).get(key),map(current.get(i)).get(key),"Historical "+component+" preserves "+key);
        }
        for(String key:List.of("original_operation","recovery_operation")){Map<String,Object> original=map(expected.get(key)),now=read("get_operation","operation_id",original.get("operation_id"));for(Map.Entry<String,Object> field:original.entrySet())same(field.getValue(),now.get(field.getKey()),"Historical operation preserves forced field "+key+":"+field.getKey());}
        long beforeNative=feedCount();refused(()->call("request_sensing_reconciliation",object("session_id","unowned","request_id",UUID.randomUUID().toString(),"expected_config_revision",status.get("config_revision"),"recovery_kind","restore-sensing-readiness")),"SESSION_REQUIRED","Historical task does not authorize remote mutation");
        Map<String,Object> material=read("get_material_loads"),boards=read("get_board_loads");for(Object row:(List<?>)material.get("loads"))check(Boolean.FALSE.equals(map(row).get("native_authority")),"Historical material read exposes no native authority");
        check(Boolean.TRUE.equals(boards.get("restart_presence_confirmation_required")),"Board history explicitly requires restart presence confirmation");
        check(feedCount()==beforeNative&&!config.getMachine().isEnabled()&&!config.getMachine().isBusy()&&driver.getControlledVacuumSource()==null,"Historical reads leave native model inert and source absent");
        bridge.close();bridge=null;check(Arrays.equals(before,Files.readAllBytes(state.resolve("journal/operations.jsonl"))),"Fresh JVM construction, reads, refusal and close append no journal bytes");
        same(documentBefore,documentInventory(),"Fresh JVM construction, reads, refusal and close leave all candidate artifacts unchanged");
        write("history-task.json",task);write("history-status.json",status);proof.putAll(Bridge.map("task_id",taskId,"journal_unchanged",true,"journal_sha256",sha(before),"original_pid",expected.get("original_pid"),"source_available",false,"fresh_jvm_history_qualified",true,"candidate_document_persisted",true,"candidate_document_reattached",false,"document_inventory",documentBefore));
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
        Map<String,Object> initialProgress=assertProgress(resolved,false,false);write("initial-progress.json",initialProgress);
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
        Map<String,Object> afterPlacementTask=read("get_sensing_reconciliation","task_id",taskId),afterPlacementProgress=assertProgress(afterPlacementTask,true,false);write("after-placement-progress.json",afterPlacementProgress);
        for(String component:List.of("material","boards")){List<?> a=(List<?>)map(initialProgress.get(component)).get("rows"),b=(List<?>)map(afterPlacementProgress.get(component)).get("rows");for(int i=0;i<a.size();i++)for(String field:List.of("old_load","parent_intent","parent_outcome"))same(map(a.get(i)).get(field),map(b.get(i)).get(field),"Actual fresh placement preserves historical "+component+" "+field);}
        Map<String,Object> newLineage=map(result(completed).get("job_lineage")),oldLineage=map(failed.get("job_lineage"));
        check(!Objects.equals(newLineage.get("lineage_id"),oldLineage.get("lineage_id")),"Replacement uses a distinct lineage from the failed native job");
        run("set_machine_enabled",command("enabled",false));read("release_control_session","session_id",session,"request_id",UUID.randomUUID().toString());
        byte[] after=Files.readAllBytes(state.resolve("journal/operations.jsonl"));
        check(after.length>prior.length&&Arrays.equals(prior,Arrays.copyOf(after,prior.length)),"Original journal bytes remain an exact append-only prefix");
        write("final-status.json",read("get_status"));Map<String,Object> finalTask=read("get_sensing_reconciliation","task_id",taskId);Map<String,Object> finalProgress=assertProgress(finalTask,true,false);
        write("expected-progress-history.json",Bridge.map("original_pid",ProcessHandle.current().pid(),"task_id",taskId,"task",finalTask,"progress",finalProgress,"document_inventory",documentInventory(),"original_operation",persistedOperation(original),"recovery_operation",persistedOperation(recoveryId)));

        proof.putAll(Bridge.map("original_operation_id",original,"original_job_id",oldJobId,"replacement_job_id",newJobId,"task_id",taskId,
            "recovery_operation_id",resolved.get("recovery_operation_id"),"original_material_load_id",oldMaterial,"replacement_material_load_id",newMaterial,
            "original_board_load_ids",oldBoardIds,"replacement_board_load_ids",newBoardIds,"original_feed_count",oldFeeds,
            "replacement_actual_simulator_placements",1,"original_outcome",failed.get("state"),"original_native_history",history,
            "journal_before_sha256",sha(prior),"journal_after_sha256",sha(after),"source_latch_before",latchBefore,"source_latch_after",latchAfter));
    }
    public static void main(String[] args)throws Exception {
        if(args.length<2||args.length>4)throw new IllegalArgumentException("Expected pinned sample root and new exclusive evidence state directory");
        if(args.length>=3)scenario=args[2];if(args.length==4)phase=args[3];if(!Set.of("live","history").contains(phase))throw new IllegalArgumentException("Expected live/history phase");
        if(!Set.of("retained-after-place","lost-before-place","invalid-read").contains(scenario))throw new IllegalArgumentException("Unsupported source scenario");
        state=Path.of(args[1]).toAbsolutePath();if(phase.equals("live"))Files.createDirectory(state);
        int exit=0;Map<String,Object> proof=new LinkedHashMap<>();
        try {
            if(phase.equals("history")){history(Path.of(args[0]).toAbsolutePath(),proof);}else{
            Path configDir=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");
            Map<String,Object> prepared=GuiSensingFixture.prepare(configDir,manifest,scenario);
            Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();
            Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),scenario));
            write("fixture-attestation.json",claimed);
            check(Boolean.TRUE.equals(claimed.get("sensing_fixture_attested")),"Exact prepared native GUI-style fixture is attested in the current process");
            Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);
            local=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));
            bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",local);
            exercise(proof);}
            proof.put("passed",true);
        } catch(Throwable failure) {
            failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;
        } finally {
            try {if(bridge!=null){if(exit!=0&&phase.equals("live"))bridge.recordGuiUnknownExit();bridge.close();}proof.put("bridge_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("bridge_close_failure",failure.toString());exit=1;}
            try {if(local!=null){local.release();check(!local.gate.owns(local.token),"Owned native guard is released during cleanup");}proof.put("native_gate_released",true);}catch(Throwable failure){failure.printStackTrace();proof.put("gate_release_failure",failure.toString());exit=1;}
            try {if(config!=null)config.getMachine().close();proof.put("native_machine_closed",true);}catch(Throwable failure){failure.printStackTrace();proof.put("machine_close_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);
            proof.putAll(Bridge.map("checks",checks,"assertions",checks.size(),"public_calls",calls,"pid",ProcessHandle.current().pid(),
                "phase",phase,"synthetic_source_scenario",scenario,"local_adapter","actual native gate; non-Swing validation callback",
                "swing_qualification",false,"mainframe_qualification",false,"hardware_qualified",false,"faulted_job_replacement_qualified",phase.equals("live")&&Boolean.TRUE.equals(proof.get("passed")),
                "replacement_scope","initial current-process replacement of a terminal faulted native job",
                "interrupted_replacement_continuation_qualified",false,"restart_reattachment_qualified",false,"public_package_qualified",false));
            write(phase+"-proof.json",proof);System.out.println("NATIVE_FAULTED_SENSING_REPLACEMENT_PROGRESS_BRIDGE_RESULT "+JSON.toJson(proof));
        }
        System.exit(exit);
    }
}
