/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.lang.reflect.Field;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.*;

/** Public Bridge imports and real native save/journal boundaries in fresh, passive simulator JVMs. */
public final class NativePartBindingsBridgeTest {
    static final Gson JSON=new Gson();
    static final String MARKER="OPENPNP_PART_BINDINGS_BRIDGE_CASE ";
    static final String EFFECT="canonical-part-binding-publication";
    static final List<String> checks=new ArrayList<>();
    static Configuration config;static Bridge bridge;static String session;static Path root;
    static int libraryEvents;
    static void check(boolean value,String why){if(!value)throw new AssertionError(why);checks.add(why);}
    static JsonObject obj(Object...pairs){return JSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
    static JsonArray rows(JsonObject...values){JsonArray out=new JsonArray();for(JsonObject value:values)out.add(value);return out;}
    static Object plain(Object value){if(value instanceof Map){Map<String,Object>out=new LinkedHashMap<>();((Map<?,?>)value).forEach((k,v)->out.put(String.valueOf(k),plain(v)));return out;}if(value instanceof Iterable){List<Object>out=new ArrayList<>();for(Object item:(Iterable<?>)value)out.add(plain(item));return out;}return value;}
    static String json(Object value){return JSON.toJson(plain(value));}
    @SuppressWarnings("unchecked") static Map<String,Object> call(String method,JsonObject args)throws Exception{return(Map<String,Object>)bridge.call("openpnp_"+method,args);}
    static Map<String,Object> read(String method,Object...pairs)throws Exception{return call(method,obj(pairs));}
    static JsonObject mutate(Object...pairs)throws Exception{JsonObject out=obj("session_id",session,"request_id",UUID.randomUUID().toString(),"expected_config_revision",read("get_status").get("config_revision"));for(Map.Entry<String,JsonElement>entry:obj(pairs).entrySet())out.add(entry.getKey(),entry.getValue());return out;}
    static JsonObject cloneJson(JsonObject value){return new JsonParser().parse(value.toString()).getAsJsonObject();}
    static Map<String,Object> await(Map<String,Object> op,String expected)throws Exception{
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(10);
        while(System.nanoTime()<deadline){op=read("get_operation","operation_id",op.get("operation_id"));if("publication-fault".equals(expected)&&op.get("publication_fault") instanceof Map&&!config.getMachine().isBusy())return op;if(List.of("succeeded","failed","outcome_unknown","aborted").contains(op.get("state"))&&!config.getMachine().isBusy()){check(expected.equals(op.get("state")),"Expected "+expected+": "+json(op));return op;}Thread.sleep(5);}
        throw new AssertionError("Native import timeout: "+json(op));
    }
    @SuppressWarnings("unchecked") static Map<String,Object> result(Map<String,Object> op){return op.get("result") instanceof Map?(Map<String,Object>)op.get("result"):Collections.emptyMap();}
    static boolean publicationFailure(String mode){return mode.startsWith("publish-")||mode.startsWith("post-bind-");}
    static JsonObject canonical(String id){
        JsonObject input=obj("schemaVersion",1,"id","bound-import","units","mm","coordinateConvention","openpnp-top-view");
        input.add("parts",rows(obj("id",id,"packageId","R0603","heightMm",0.75,"value","Supplier label")));
        JsonArray placements=new JsonArray();for(int i=0;i<2;i++)placements.add(obj("ref","R"+i,"partId",id,"packageId","R0603","heightMm",0.75,"x",10+i,"y",10,"z",0,"rotation",0,"enabled",true,"side","top","type","placement"));
        JsonObject board=obj("id","b","widthMm",150,"heightMm",150);board.add("placements",placements);input.add("boards",rows(board));input.add("panels",new JsonArray());
        input.add("instances",rows(obj("id","first","kind","board","definitionId","b","x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true),obj("id","second","kind","board","definitionId","b","x",10,"y",10,"z",0,"rotation",0,"side","top","enabled",true)));
        return input;
    }
    static JsonObject boundRequest(JsonObject canonical)throws Exception{
        return mutate("canonical_job",canonical,"canonical_artifact_id",NativePortableConfiguration.sha(canonical.toString().getBytes(StandardCharsets.UTF_8)),
            "part_bindings",rows(obj("source_part_id","supplier-1k","native_part_id","r0603-1k")));
    }
    static Job nativeJob()throws Exception{Field field=Bridge.class.getDeclaredField("job");field.setAccessible(true);return(Job)field.get(bridge);}
    static int listeners()throws Exception{Field field=Configuration.class.getDeclaredField("listeners");field.setAccessible(true);return((Set<?>)field.get(config)).size();}
    static String xml(Object value)throws Exception{StringWriter writer=new StringWriter();Configuration.createSerializer().write(value,writer);return writer.toString();}
    static final class Library {
        final List<Part> parts=config.getParts();final List<org.openpnp.model.Package> packages=config.getPackages();
        final IdentityHashMap<Object,String> serialized=new IdentityHashMap<>();final int beforeListeners=listeners(),events=libraryEvents;
        Library()throws Exception{for(Object item:parts)serialized.put(item,xml(item));for(Object item:packages)serialized.put(item,xml(item));}
        void unchanged()throws Exception{check(parts.equals(config.getParts())&&packages.equals(config.getPackages()),"Exact native Part/Package identities unchanged");check(beforeListeners==listeners()&&events==libraryEvents,"No constructor listeners or library publication events");for(Map.Entry<Object,String>entry:serialized.entrySet())check(entry.getValue().equals(xml(entry.getKey())),"Complete native library XML unchanged");}
    }
    static void expectFault(String method,JsonObject p,String code)throws Exception{try{call(method,p);throw new AssertionError("Expected "+code);}catch(Bridge.Fault fault){check(code.equals(fault.code),"Exact refusal "+code+", got "+fault.code);}}
    static List<Map<?,?>> journalEvents()throws Exception{List<Map<?,?>>out=new ArrayList<>();for(String line:Files.readAllLines(root.resolve("journal/operations.jsonl")))out.add(JSON.fromJson(line,Map.class));return out;}
    static long count(String type,String opId)throws Exception{return journalEvents().stream().filter(e->type.equals(e.get("type"))&&e.get("payload") instanceof Map&&opId.equals(((Map<?,?>)e.get("payload")).get("operation_id"))).count();}
    static long allEffects()throws Exception{return journalEvents().stream().filter(e->List.of("native_effect_intent","native_effect_outcome","native_action_intent","native_step_intent","feed_complete","placement_complete").contains(e.get("type"))).count();}
    static void noMachineActions()throws Exception{
        check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"Simulator stayed disabled and unhomed");
        for(Map<?,?>event:journalEvents()){
            String type=(String)event.get("type");
            check(!List.of("native_action_intent","native_step_intent","feed_intent","feed_complete","placement_complete").contains(type),"No machine action/step/feed/placement journal event");
            if(type.startsWith("native_effect_"))check(EFFECT.equals(((Map<?,?>)event.get("payload")).get("kind")),"Only job publication effects were requested");
        }
    }
    static void boot(Path path,Path samples,boolean first)throws Exception{
        root=path;Files.createDirectories(root.resolve("config"));Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();
        if(first){SimulatorMain.accelerateFixture(config);SimulatorMain.settleFreshFixture(root.resolve("config"));config=Configuration.get();Files.writeString(root.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);}
        bridge=new Bridge(config,root.resolve("token"),root.resolve("journal"),samples,0,true);
        config.addPropertyChangeListener("parts",e->libraryEvents++);config.addPropertyChangeListener("packages",e->libraryEvents++);
        if(first)session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
    }
    interface Injection { void run()throws Exception; }
    /** Delegates actual file writes/force. It never edits or synthesizes a journal record. */
    static final class JournalBoundary extends FileChannel {
        final FileChannel delegate;final String eventType;final Injection afterForce;final String writeFailure;
        boolean armed=true,pending;int hits;String stack;
        JournalBoundary(FileChannel delegate,String eventType,Injection afterForce,String writeFailure){this.delegate=delegate;this.eventType=eventType;this.afterForce=afterForce;this.writeFailure=writeFailure;}
        public int write(ByteBuffer src)throws IOException{
            if(armed){ByteBuffer copy=src.asReadOnlyBuffer();byte[]bytes=new byte[copy.remaining()];copy.get(bytes);JsonObject record=new JsonParser().parse(new String(bytes,StandardCharsets.UTF_8)).getAsJsonObject();
                if(eventType.equals(record.get("type").getAsString())){armed=false;pending=true;
                    if(writeFailure!=null){hits++;stack=Arrays.toString(Thread.currentThread().getStackTrace());if(writeFailure.equals("error"))throw new AssertionError("CONTROLLED_NATIVE_PUBLICATION_ERROR");throw new IOException("CONTROLLED_NATIVE_PUBLICATION_EXCEPTION");}
                }}
            return delegate.write(src);
        }
        public void force(boolean metadata)throws IOException{delegate.force(metadata);if(pending&&afterForce!=null){pending=false;hits++;stack=Arrays.toString(Thread.currentThread().getStackTrace());try{afterForce.run();}catch(IOException|RuntimeException e){throw e;}catch(Exception e){throw new IOException(e);}}}
        public int read(ByteBuffer dst)throws IOException{return delegate.read(dst);}public long read(ByteBuffer[]dst,int off,int len)throws IOException{return delegate.read(dst,off,len);}public int read(ByteBuffer dst,long p)throws IOException{return delegate.read(dst,p);}
        public long write(ByteBuffer[]src,int off,int len)throws IOException{return delegate.write(src,off,len);}public int write(ByteBuffer src,long p)throws IOException{return delegate.write(src,p);}
        public long position()throws IOException{return delegate.position();}public FileChannel position(long p)throws IOException{delegate.position(p);return this;}public long size()throws IOException{return delegate.size();}public FileChannel truncate(long size)throws IOException{delegate.truncate(size);return this;}
        public long transferTo(long p,long count,WritableByteChannel target)throws IOException{return delegate.transferTo(p,count,target);}public long transferFrom(ReadableByteChannel src,long p,long count)throws IOException{return delegate.transferFrom(src,p,count);}
        public MappedByteBuffer map(MapMode mode,long p,long size)throws IOException{return delegate.map(mode,p,size);}public FileLock lock(long p,long size,boolean shared)throws IOException{return delegate.lock(p,size,shared);}public FileLock tryLock(long p,long size,boolean shared)throws IOException{return delegate.tryLock(p,size,shared);}protected void implCloseChannel()throws IOException{delegate.close();}
    }
    static JournalBoundary intercept(String type,Injection afterForce,String failure)throws Exception{Field field=Bridge.class.getDeclaredField("journal");field.setAccessible(true);JournalBoundary channel=new JournalBoundary((FileChannel)field.get(bridge),type,afterForce,failure);field.set(bridge,channel);return channel;}
    /** Same part objects/lookup semantics; inject an Error only inside real native Configuration.saveParts. */
    static final class SaveErrorParts extends LinkedHashMap<String,Part> {
        boolean armed=true;int hits;String stack;
        SaveErrorParts(Map<String,Part> source){super(source);}
        @Override public Collection<Part> values(){if(armed)for(StackTraceElement frame:Thread.currentThread().getStackTrace())if(frame.getClassName().equals(Configuration.class.getName())&&frame.getMethodName().equals("saveParts")){armed=false;hits++;stack=Arrays.toString(Thread.currentThread().getStackTrace());throw new AssertionError("CONTROLLED_NATIVE_SAVE_PARTS_ERROR");}return super.values();}
    }
    static void assertPrior(Job previous,String previousId,Object revision)throws Exception{check(nativeJob()==previous&&previousId.equals(read("get_status").get("job_id")),"Failure before publication preserves prior native Job object and ID");check(revision.equals(read("get_status").get("config_revision")),"Failure before publication preserves configuration revision");}
    static void persistedPartsSame(byte[]parts,byte[]packages)throws Exception{check(Arrays.equals(parts,Files.readAllBytes(root.resolve("config/parts.xml"))),"Native persisted parts.xml unchanged");check(Arrays.equals(packages,Files.readAllBytes(root.resolve("config/packages.xml"))),"Native persisted packages.xml unchanged");}

    static void exercise(String mode,Path state,Path samples)throws Exception{
        boot(state,samples,true);
        Map<String,Object> initial=await(call("prepare_job",mutate("canonical_job",canonical("R0603-1K"))),"succeeded");
        Job previous=nativeJob();String previousId=(String)result(initial).get("job_id");Object revision=read("get_status").get("config_revision");
        Library library=new Library();byte[]parts=Files.readAllBytes(root.resolve("config/parts.xml")),packages=Files.readAllBytes(root.resolve("config/packages.xml"));
        JsonObject input=boundRequest(canonical("SUPPLIER-1K"));Part target=config.getPart("R0603-1K");Length oldHeight=target.getHeight();
        Map<String,Object> observation=new LinkedHashMap<>();JournalBoundary boundary=null;Field partsField=null;Object originalParts=null;SaveErrorParts saveError=null;
        if(mode.equals("validation")){
            JsonObject late=cloneJson(input);late.getAsJsonObject("canonical_job").getAsJsonArray("instances").get(1).getAsJsonObject().addProperty("definitionId","absent-late");
            Map<String,Object> failed=await(call("prepare_job",late),"failed");check("MISSING_DEFINITION".equals(result(failed).get("code")),"Late graph validation uses typed native refusal");assertPrior(previous,previousId,revision);
            check(count("native_effect_intent",(String)failed.get("operation_id"))==0,"Late validation never starts publication effect");
            JsonObject missing=cloneJson(input);missing.getAsJsonArray("part_bindings").get(0).getAsJsonObject().addProperty("native_part_id","Absent");missing.addProperty("request_id",UUID.randomUUID().toString());
            Map<String,Object> absent=await(call("prepare_job",missing),"failed");check("PART_UNMAPPED".equals(result(absent).get("code")),"Unknown existing target refuses");assertPrior(previous,previousId,revision);
            JsonObject stale=cloneJson(input);stale.addProperty("request_id",UUID.randomUUID().toString());stale.addProperty("expected_config_revision","cfg-999999");expectFault("prepare_job",stale,"REVISION_CONFLICT");
            JsonObject malformed=cloneJson(input);malformed.addProperty("request_id","NOT-A-UUID");expectFault("prepare_job",malformed,"INVALID_ARGUMENT");
            JsonObject missingRevision=cloneJson(input);missingRevision.addProperty("request_id",UUID.randomUUID().toString());missingRevision.remove("expected_config_revision");expectFault("prepare_job",missingRevision,"INVALID_ARGUMENT");
            JsonObject upper=cloneJson(input);upper.addProperty("request_id",UUID.randomUUID().toString());upper.addProperty("canonical_artifact_id","A".repeat(64));expectFault("prepare_job",upper,"INVALID_ARGUMENT");
            JsonObject mixed=cloneJson(input);mixed.addProperty("request_id",UUID.randomUUID().toString());mixed.addProperty("sample","pnp-test");expectFault("prepare_job",mixed,"UNKNOWN_FIELD");
            library.unchanged();persistedPartsSame(parts,packages);noMachineActions();observation.put("validation_failures_preserve_prior_job",true);
        }else{
            if(mode.endsWith("identity"))boundary=intercept(mode.startsWith("staged")?"canonical_part_bindings_staged":"native_effect_intent",()->target.setHeight(new Length(0.9,LengthUnit.Millimeters)),null);
            if(mode.endsWith("lease")){
                if(mode.startsWith("intent")){read("renew_control_session","session_id",session,"ttl_seconds",1);boundary=intercept("native_effect_intent",()->Thread.sleep(1200),null);}
                else boundary=intercept("canonical_part_bindings_staged",()->read("release_control_session","session_id",session),null);
            }
            if(mode.equals("save-exception")){Files.move(root.resolve("config/machine.xml"),root.resolve("machine-before-blocker.xml"));Files.createDirectory(root.resolve("config/machine.xml"));Files.writeString(root.resolve("config/machine.xml/blocker"),"native save path collision");}
            if(mode.equals("save-error")){partsField=Configuration.class.getDeclaredField("parts");partsField.setAccessible(true);originalParts=partsField.get(config);saveError=new SaveErrorParts((Map<String,Part>)originalParts);partsField.set(config,saveError);}
            if(mode.startsWith("publish-"))boundary=intercept("job_lineage_created",null,mode.endsWith("error")?"error":"exception");
            if(mode.startsWith("post-bind-"))boundary=intercept("native_effect_outcome",null,mode.endsWith("error")?"error":"exception");
            String expected=mode.equals("success")?"succeeded":mode.startsWith("staged")?"failed":publicationFailure(mode)?"publication-fault":"outcome_unknown";
            Map<String,Object> op=await(call("prepare_job",input),expected);String opId=(String)op.get("operation_id");
            observation.put("operation",op);observation.put("status",read("get_status"));
            if(publicationFailure(mode)){
                Map<?,?> fault=(Map<?,?>)op.get("publication_fault"),completion=(Map<?,?>)op.get("native_completion"),known=(Map<?,?>)fault.get("known_body_outcome"),finished=(Map<?,?>)fault.get("completion_observation");
                check("running".equals(op.get("state"))&&Boolean.FALSE.equals(fault.get("durable")),"Failed journal publication preserves last durable running state and disclaims a terminal receipt");
                check("publication-fault".equals(completion.get("phase"))&&Boolean.TRUE.equals(completion.get("ownership_retained")),"Completion publication fault retains native ownership fence");
                check(Boolean.TRUE.equals(finished.get("native_wrapper_completed")),"Publication fault follows actual native wrapper completion");
                check(Boolean.valueOf(!mode.endsWith("error")).equals(finished.get("native_wrapper_succeeded")),"Publication receipt distinguishes body Exception handling from native wrapper Error");
                if(mode.endsWith("error"))check(Boolean.FALSE.equals(known.get("captured"))&&known.get("body_failure") instanceof Map,"Error retains failure details without fabricating a returned body result");
                else check(Boolean.TRUE.equals(known.get("captured"))&&"outcome_unknown".equals(known.get("state")),"Caught publication Exception retains known unknown body outcome");
                check(Boolean.TRUE.equals(op.get("native_effect_pending"))&&EFFECT.equals(op.get("native_effect_kind")),"Outer publication remains pending after all nested work");
                check(Boolean.TRUE.equals(read("get_status").get("journal_fault"))&&opId.equals(read("get_status").get("active_operation_id")),"Journal fault retains original active operation");
            }
            if(boundary!=null){check(boundary.hits==1,"One actual journal boundary injection");observation.put("injection_stack",boundary.stack);}
            if(saveError!=null){check(saveError.hits==1,"Error originated once in real Configuration.saveParts");observation.put("injection_stack",saveError.stack);partsField.set(config,originalParts);}
            if(mode.endsWith("identity")){check(target.getHeight().getValue()==0.9,"Test-only target mutation occurred at retained journal boundary");observation.put("externally_injected_height_mm",0.9);target.setHeight(oldHeight);}
            if(mode.equals("save-exception")){check(json(op).contains("saving machine.xml"),"Exception originated in native Configuration.save");Files.move(root.resolve("config/machine.xml"),root.resolve("retained-save-blocker"));Files.move(root.resolve("machine-before-blocker.xml"),root.resolve("config/machine.xml"));}
            if(mode.startsWith("staged")||mode.startsWith("intent"))assertPrior(previous,previousId,revision);
            else if(!mode.equals("success")){
                check(nativeJob()!=previous&&!previousId.equals(read("get_status").get("job_id")),"Injected save/publication failure occurred after actual native Job replacement");
                check(!revision.equals(read("get_status").get("config_revision")),"Partial publication retains its actual advanced configuration revision");
                if(mode.startsWith("post-bind"))check(journalEvents().stream().filter(e->"board_load_outcome".equals(e.get("type"))).count()==4,"Completion failure occurred after both new virtual board loads completed");
            }
            if(mode.startsWith("staged")){check(!Boolean.TRUE.equals(read("get_status").get("configuration_fault")),"Pre-intent failure does not fault unchanged configuration");check(count("native_effect_intent",opId)==0,"Staged loss remains before native publication intent");}
            else if(!mode.equals("success")){check(Boolean.TRUE.equals(read("get_status").get("configuration_fault")),"Attempted publication failure fences configuration");check(count("native_effect_intent",opId)==1&&count("native_effect_outcome",opId)==0,"Unknown publication retains one unmatched intent");}
            if(mode.equals("success")){
                Map<String,Object> body=result(op);check("existing-parts-v1".equals(body.get("part_binding_profile")),"Exact binding profile reported");check(Boolean.FALSE.equals(body.get("part_library_changed"))&&Boolean.FALSE.equals(body.get("physical_equivalence_verified")),"No library or physical-equivalence claim");
                check(!previousId.equals(body.get("job_id"))&&nativeJob()!=previous,"Successful prepare publishes a distinct native job");
                check(!revision.equals(read("get_status").get("config_revision"))&&body.get("config_revision").equals(read("get_status").get("config_revision")),"Library invariance is separate from configuration revision advancement");
                for(BoardLocation board:nativeJob().getBoardLocations())for(Placement p:board.getBoard().getPlacements()){check(p.getPart()==target,"Actual native placement references configured target");check(!nativeJob().retrievePlacedStatus(board,p.getId()),"Import confers no completed placement history");}
                Map<?,?> artifact=(Map<?,?>)body.get("part_resolution");Map<String,Object> retrieved=read("get_artifact","artifact_id",artifact.get("artifact_id"));byte[] bytes=Base64.getDecoder().decode((String)retrieved.get("base64"));
                check(NativePortableConfiguration.sha(bytes).equals(artifact.get("sha256")),"Retained native provenance artifact bytes match hash");Map<?,?> provenance=JSON.fromJson(new String(bytes,StandardCharsets.UTF_8),Map.class);
                check(opId.equals(provenance.get("operation_id"))&&op.get("request_digest").equals(provenance.get("request_digest"))&&input.get("canonical_artifact_id").getAsString().equals(provenance.get("source_artifact_id")),"Provenance binds operation, exact request and supplied source artifact");
                check("existing-parts-v1".equals(provenance.get("profile"))&&Boolean.FALSE.equals(provenance.get("publication_authority")),"Resolution artifact grants no publication authority");
                List<?> resolved=(List<?>)provenance.get("parts");check(resolved.size()==1&&target.getId().equals(((Map<?,?>)resolved.get(0)).get("native_part_id"))&&((Number)((Map<?,?>)resolved.get(0)).get("definition_placement_count")).intValue()==2,"Provenance distinguishes two definition records from four native instances");
                check(count("canonical_part_bindings_staged",opId)==1&&count("native_effect_intent",opId)==1&&count("native_effect_outcome",opId)==1,"One staged receipt and one completed publication effect");observation.put("provenance",provenance);
                Map<String,Object> repeat=call("prepare_job",input);check(opId.equals(repeat.get("operation_id"))&&"succeeded".equals(repeat.get("state")),"Identical request returns original completed operation");
                JsonObject conflict=cloneJson(input);conflict.getAsJsonArray("part_bindings").get(0).getAsJsonObject().addProperty("native_part_id","R0603-1K");expectFault("prepare_job",conflict,"REQUEST_ID_CONFLICT");
            }
            long effects=allEffects();Map<String,Object> requestStatus=read("get_request_status","request_id",input.get("request_id").getAsString());check(opId.equals(((Map<?,?>)requestStatus.get("operation")).get("operation_id")),"Read-only request status retains original operation");
            if(publicationFailure(mode))expectFault("prepare_job",input,"JOURNAL_FAULT");
            else try { Map<String,Object> retry=call("prepare_job",input);check(opId.equals(retry.get("operation_id")),"Retry returns original operation only"); }
            catch(Bridge.Fault fault){check(List.of("CONFIGURATION_FAULT","SESSION_REQUIRED","RECOVERY_REQUIRED").contains(fault.code),"Guarded retry refuses without replay: "+fault.code);}
            check(effects==allEffects(),"Original request retry causes no new effects");
            library.unchanged();persistedPartsSame(parts,packages);check(config.getPart("SUPPLIER-1K")==null,"No alias Part installed");noMachineActions();
            Map<?,?> staged=null;for(Map<?,?> event:journalEvents())if("canonical_part_bindings_staged".equals(event.get("type"))&&opId.equals(((Map<?,?>)event.get("payload")).get("operation_id")))staged=(Map<?,?>)event.get("payload");
            check(staged!=null,"Operation retains its staged native provenance descriptor");Map<?,?> descriptor=(Map<?,?>)staged.get("part_resolution");Map<String,Object> retained=read("get_artifact","artifact_id",descriptor.get("artifact_id"));check(descriptor.get("sha256").equals(NativePortableConfiguration.sha(Base64.getDecoder().decode((String)retained.get("base64")))),"Provenance remains readable even when publication is fenced");
            Files.writeString(root.resolve("original-request.json"),input.toString());Files.writeString(root.resolve("closed-case.json"),json(Bridge.map("operation_id",opId,"state",publicationFailure(mode)?"outcome_unknown":expected,"live_state",op.get("state"),"request_id",input.get("request_id").getAsString(),"effect_event_count",effects,"bridge_instance_id",read("get_status").get("bridge_instance_id"),"resolution",descriptor)));
        }
        if(publicationFailure(mode)){try{bridge.close();throw new AssertionError("Publication-fault owner unexpectedly closed");}catch(Bridge.Fault fault){check("BUSY".equals(fault.code),"Normal close refuses while publication ownership is fenced");}observation.put("fenced_child_exit_without_native_cleanup",true);}
        else{bridge.close();config.getMachine().close();}
        writeResult("exercise",mode,observation);
    }
    static void restart(String mode,Path state,Path samples)throws Exception{
        root=state;Map<?,?> closed=JSON.fromJson(Files.readString(root.resolve("closed-case.json")),Map.class);JsonObject original=new JsonParser().parse(Files.readString(root.resolve("original-request.json"))).getAsJsonObject();
        boot(state,samples,false);Map<String,Object> status=read("get_status"),op=read("get_operation","operation_id",closed.get("operation_id"));
        check(!closed.get("bridge_instance_id").equals(status.get("bridge_instance_id")),"Fresh native Bridge instance on restart");check(closed.get("state").equals(op.get("state")),"Closed operation retains original success/unknown state");
        check(nativeJob()==null&&status.get("job_id")==null,"Restart never replays import or restores job execution authority");check(config.getPart("SUPPLIER-1K")==null,"Restart preserves existing-parts-only library");
        long effects=allEffects();check(effects==((Number)closed.get("effect_event_count")).longValue(),"Startup adds no native effects");
        Map<String,Object> request=read("get_request_status","request_id",closed.get("request_id"));check(closed.get("operation_id").equals(((Map<?,?>)request.get("operation")).get("operation_id")),"Original request remains queryable after restart");
        expectFault("prepare_job",original,"SESSION_REQUIRED");
        session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");JsonObject changed=cloneJson(original);changed.addProperty("session_id",session);expectFault("prepare_job",changed,"REQUEST_ID_CONFLICT");
        if(!mode.equals("success")){JsonObject fresh=boundRequest(canonical("SUPPLIER-1K"));expectFault("prepare_job",fresh,"RECOVERY_REQUIRED");}
        if(closed.get("resolution") instanceof Map){Map<?,?> descriptor=(Map<?,?>)closed.get("resolution");Map<String,Object> file=read("get_artifact","artifact_id",descriptor.get("artifact_id"));check(descriptor.get("sha256").equals(NativePortableConfiguration.sha(Base64.getDecoder().decode((String)file.get("base64")))),"Original provenance artifact remains readable after restart");}
        check(effects==allEffects(),"Queries/retries after restart add no effects");noMachineActions();bridge.close();config.getMachine().close();writeResult("restart",mode,Bridge.map("operation",op,"status",status));
    }
    static void writeResult(String phase,String mode,Object observations)throws Exception{Map<String,Object> result=Bridge.map("passed",true,"phase",phase,"mode",mode,"assertions",checks.size(),"checks",checks,"observations",observations,"physical_qualification",false,"machine_enable_calls",0,"native_feed_effects",0,"placements_executed",0,"bridge_origin",Bridge.class.getProtectionDomain().getCodeSource().getLocation().toString());Files.writeString(root.resolve(phase+"-result.json"),json(result));System.out.println(MARKER+json(result));}
    static Map<String,Object> child(String phase,String mode,Path state,Path samples)throws Exception{
        Files.createDirectories(state.resolve(phase+"-home"));Files.createDirectories(state.resolve(phase+"-tmp"));Path log=state.resolve(phase+".log");
        List<String> command=List.of(Path.of(System.getProperty("java.home"),"bin/java").toString(),"-Xmx768m","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Djava.io.tmpdir="+state.resolve(phase+"-tmp"),"-Duser.home="+state.resolve(phase+"-home"),"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path"),NativePartBindingsBridgeTest.class.getName(),phase,mode,state.toString(),samples.toString());
        ProcessBuilder builder=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile());builder.environment().keySet().removeIf(k->k.startsWith("OPENPNP_")||k.startsWith("LD_")||k.startsWith("DYLD_")||Set.of("JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","JDK_JAVAC_OPTIONS","_JAVA_OPTIONS","CLASSPATH").contains(k));
        Process process=builder.start();boolean finished=process.waitFor(25,TimeUnit.SECONDS);if(!finished){process.destroy();if(!process.waitFor(1,TimeUnit.SECONDS)){process.destroyForcibly();process.waitFor(2,TimeUnit.SECONDS);}}
        check(finished&&!process.isAlive()&&process.exitValue()==0,"Native child completed "+phase+"/"+mode+": "+Files.readString(log));
        Map<String,Object> result=JSON.fromJson(Files.readString(state.resolve(phase+"-result.json")),Map.class);check(Boolean.TRUE.equals(result.get("passed")),"Child retained a passing result");return result;
    }
    public static void main(String[]args)throws Exception{
        if(args.length==1){Path suiteRoot=Files.createTempDirectory("openpnp-part-binding-bridge-");List<Object> results=new ArrayList<>();
            for(String mode:List.of("success","validation","staged-identity","staged-lease","intent-identity","intent-lease","save-exception","save-error","publish-exception","publish-error","post-bind-exception","post-bind-error")){
                Path state=suiteRoot.resolve(mode);Files.createDirectories(state);results.add(child("exercise",mode,state,Path.of(args[0]).toRealPath()));
                if(mode.equals("success")||mode.startsWith("intent")||mode.startsWith("save")||mode.startsWith("publish")||mode.startsWith("post-bind"))results.add(child("restart",mode,state,Path.of(args[0]).toRealPath()));
            }
            Map<String,Object> report=Bridge.map("passed",true,"native_jvm_phases",results.size(),"results",results,"root",suiteRoot.toString(),"physical_qualification",false);Files.writeString(suiteRoot.resolve("report.json"),json(report));System.out.println("OPENPNP_NATIVE_PART_BINDINGS_BRIDGE_RESULT "+json(report));return;
        }
        try{if(args[0].equals("exercise"))exercise(args[1],Path.of(args[2]),Path.of(args[3]));else if(args[0].equals("restart"))restart(args[1],Path.of(args[2]),Path.of(args[3]));else throw new IllegalArgumentException("Unknown phase");System.exit(0);}
        catch(Throwable failure){failure.printStackTrace();System.exit(1);}
    }
}
