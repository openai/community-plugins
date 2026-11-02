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

/** Public Bridge panel edits and real native save/journal boundaries in fresh, passive simulator JVMs. */
public final class NativePanelBoardMembershipBridgeTest {
    static final Gson JSON=new Gson();
    static final String MARKER="OPENPNP_PANEL_MEMBERSHIP_BRIDGE_CASE ";
    static final String EFFECT="native-placement-structure-publication";
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
        while(System.nanoTime()<deadline){op=read("get_operation","operation_id",op.get("operation_id"));if("publication-fault".equals(expected)&&op.get("publication_fault") instanceof Map&&!config.getMachine().isBusy())return op;if(List.of("succeeded","failed","outcome_unknown","aborted").contains(op.get("state"))&&!config.getMachine().isBusy()){if(!expected.equals(op.get("state")))throw new AssertionError("Expected "+expected+": "+json(op));check(true,"Native wrapper reached "+expected);return op;}Thread.sleep(5);}
        throw new AssertionError("Native import timeout: "+json(op));
    }
    @SuppressWarnings("unchecked") static Map<String,Object> result(Map<String,Object> op){return op.get("result") instanceof Map?(Map<String,Object>)op.get("result"):Collections.emptyMap();}
    static JsonObject canonical(String id){
        JsonObject input=obj("schemaVersion",1,"id","bound-import","units","mm","coordinateConvention","openpnp-top-view");
        input.add("parts",rows(obj("id",id,"packageId","R0603","heightMm",0.75,"value","Supplier label")));
        JsonArray placements=new JsonArray();for(int i=0;i<2;i++)placements.add(obj("ref","R"+i,"partId",id,"packageId","R0603","heightMm",0.75,"x",10+i,"y",10,"z",0,"rotation",0,"enabled",true,"side","top","type","placement"));
        JsonObject board=obj("id","b","widthMm",150,"heightMm",150);board.add("placements",placements);input.add("boards",rows(board));input.add("panels",new JsonArray());
        input.add("instances",rows(obj("id","first","kind","board","definitionId","b","x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true),obj("id","second","kind","board","definitionId","b","x",10,"y",10,"z",0,"rotation",0,"side","top","enabled",true)));
        return input;
    }
    static Job nativeJob()throws Exception{Field field=Bridge.class.getDeclaredField("job");field.setAccessible(true);return(Job)field.get(bridge);}
    static int listeners()throws Exception{Field field=Configuration.class.getDeclaredField("listeners");field.setAccessible(true);return((Set<?>)field.get(config)).size();}
    static String xml(Object value)throws Exception{StringWriter writer=new StringWriter();Configuration.createSerializer().write(value,writer);return writer.toString();}
    static final class Library {
        final List<Part> parts=config.getParts();final List<org.openpnp.model.Package> packages=config.getPackages();
        final IdentityHashMap<Object,String> serialized=new IdentityHashMap<>();final int beforeListeners=listeners(),events=libraryEvents;
        Library()throws Exception{for(Object item:parts)serialized.put(item,xml(item));for(Object item:packages)serialized.put(item,xml(item));}
        void unchanged()throws Exception{unchanged(0);}
        void unchanged(int injectedEvents)throws Exception{check(parts.equals(config.getParts())&&packages.equals(config.getPackages()),"Exact native Part/Package identities unchanged");check(beforeListeners==listeners()&&events+injectedEvents==libraryEvents,"Library listeners stable; only explicitly counted injection events");for(Map.Entry<Object,String>entry:serialized.entrySet())check(entry.getValue().equals(xml(entry.getKey())),"Complete native library XML unchanged");}
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
            if(type.startsWith("native_effect_"))check(!Set.of("motion","home","machine-enable","feed","actuator","nozzle-tip-change").contains(((Map<?,?>)event.get("payload")).get("kind")),"No machine effect was requested");
        }
    }
    static void boot(Path path,Path samples,boolean first)throws Exception{
        root=path;Files.createDirectories(root.resolve("config"));Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();
        if(first){SimulatorMain.accelerateFixture(config);SimulatorMain.settleFreshFixture(root.resolve("config"));config=Configuration.get();Files.writeString(root.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);}
        bridge=guiMode?new Bridge(config,root.resolve("token"),root.resolve("journal"),samples,0,true,"gui-simulator",ownership=new TestOwnership()):new Bridge(config,root.resolve("token"),root.resolve("journal"),samples,0,true);
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
                if(eventType.equals(record.get("type").getAsString())||eventType.equals("terminal-operation")&&record.get("type").getAsString().equals("operation")&&record.getAsJsonObject("payload").has("native_completion")){armed=false;pending=true;
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
    static void assertPrior(Job previous,String previousId,Object revision)throws Exception{check(nativeJob()==previous&&previousId.equals(read("get_status").get("job_id")),"Failure before publication preserves prior native Job object and ID");check(revision.equals(read("get_status").get("config_revision")),"Failure before publication preserves configuration revision");}
    static void persistedPartsSame(byte[]parts,byte[]packages)throws Exception{check(Arrays.equals(parts,Files.readAllBytes(root.resolve("config/parts.xml"))),"Native persisted parts.xml unchanged");check(Arrays.equals(packages,Files.readAllBytes(root.resolve("config/packages.xml"))),"Native persisted packages.xml unchanged");}

    static boolean guiMode;static TestOwnership ownership;
    /** Validation-only non-Swing adapter, backed by the pinned actual native ownership gate. */
    static final class TestOwnership implements GuiOwnership {
        final org.openpnp.spi.base.AbstractMachine machine=(org.openpnp.spi.base.AbstractMachine)config.getMachine();
        final org.openpnp.spi.base.ExternalExecutionControl gate=machine.getExternalExecutionControl();final Object token;boolean granted=true;
        TestOwnership()throws Exception{token=gate.claim("panel73 native ownership fixture");}
        public void checkAttachment(Configuration c,org.openpnp.spi.Machine m)throws Exception{if(c!=config||m!=machine)throw new Bridge.Fault("GUI_ATTACHMENT_CHANGED","Validation attachment changed");Bridge.verifyNativeSimulatorClasses(m);}
        public void requireNativeOwnership()throws Exception{if(!gate.owns(token))throw new Bridge.Fault("LOCAL_GRANT_REQUIRED","Native gate not owned");}
        public void requireRemoteGrant()throws Exception{requireNativeOwnership();if(!granted)throw new Bridge.Fault("OWNERSHIP_REVOKED","Validation grant revoked");}
        public <T>T invokeNative(Callable<T> action)throws Exception{return gate.withOwner(token,action);}
        public <T>Future<T> submitNative(Callable<T> action,boolean disabled)throws Exception{return gate.withOwner(token,()->machine.submit(action,null,disabled));}
        public void publishJob(Job job)throws Exception{check(machine.isTask(Thread.currentThread()),"Publication stays on actual native executor in non-Swing fixture");}
        public Map<String,Object> snapshot(){return Bridge.map("validation_only",true,"native_ownership_held",gate.owns(token),"local_grant",granted,"swing_qualification",false);}
        void release()throws Exception{gate.release(token);}
    }
    static JsonObject instance(String id,String kind,String def,int x){return obj("id",id,"kind",kind,"definitionId",def,"x",x,"y",0,"z",0,"rotation",0,"side","top","enabled",true);}
    static JsonObject panelJob(){
        JsonObject input=canonical("R0603-1K");input.addProperty("id","panel-membership");
        input.add("panels",rows(obj("id","panel","widthMm",200,"heightMm",150,"children",rows(instance("A","board","b",0),instance("B","board","b",20)))));
        input.add("instances",rows(instance("P1","panel","panel",0),instance("P2","panel","panel",100)));return input;
    }
    static JsonObject cloneBoard(String from,String id){return obj("action","clone_board_child","scope","job_instance","parent_instance_id","P1","source_child_id",from,"new_child_id",id,"location",obj("frame","holder","units","mm","x",40,"y",0,"z",0,"rotation",0),"side","Top","enabled",true,"check_fiducials",false);}
    static JsonObject removeBoard(String id){return obj("action","remove_board_child","scope","job_instance","parent_instance_id","P1","child_id",id);}
    static JsonObject jobArgs(Object...fields)throws Exception{
        Map<String,Object> status=read("get_status");JsonObject p=mutate("job_id",status.get("job_id"),"expected_job_revision",status.get("job_revision"),"expected_board_load_revision",status.get("board_load_revision"));
        for(Map.Entry<String,JsonElement>e:obj(fields).entrySet())p.add(e.getKey(),e.getValue());return p;
    }
    static Map<String,Object> plan(JsonObject change)throws Exception{return await(call("plan_placement_structure",jobArgs("changes",rows(change))),"succeeded");}
    static JsonObject applyArgs(Map<String,Object> plan)throws Exception{return jobArgs("plan_id",result(plan).get("plan_id"));}
    static Map<String,Object> apply(JsonObject change)throws Exception{return await(call("apply_placement_structure",applyArgs(plan(change))),"succeeded");}
    static Map<String,Object> effects(Map<String,Object> plan){return JSON.fromJson(JSON.toJson(((Map<?,?>)result(plan).get("plan")).get("effects")),Map.class);}
    static BoardLocation board(Job job,String id){for(BoardLocation b:job.getBoardLocations())if(b.getUniqueId().equals(id))return b;throw new AssertionError("Missing board "+id);}
    static Map<String,Object> snapshot(Job job){Map<String,Object> out=new TreeMap<>();for(PlacementsHolderLocation<?> holder:job.getBoardAndPanelLocations())if(holder!=job.getRootPanelLocation()){List<Object> p=new ArrayList<>();for(Placement x:holder.getPlacementsHolder().getPlacements())p.add(Bridge.map("id",x.getId(),"location",x.getLocation().toString(),"side",x.getSide().name(),"part",x.getPart().getId(),"enabled",x.isEnabled()));out.put(holder.getUniqueId(),Bridge.map("location",holder.getLocation().toString(),"side",holder.getSide().name(),"enabled",holder.isLocallyEnabled(),"check",holder.isCheckFiducials(),"placements",p));}return out;}
    static NativeJobLineage lineage()throws Exception{Field f=Bridge.class.getDeclaredField("jobLineage");f.setAccessible(true);return(NativeJobLineage)f.get(bridge);}
    static NativeBoardLoads loads()throws Exception{Field f=Bridge.class.getDeclaredField("boardLoads");f.setAccessible(true);return(NativeBoardLoads)f.get(bridge);}
    static String holderKey(String id){return "holder-v1:"+Base64.getUrlEncoder().withoutPadding().encodeToString(id.getBytes(StandardCharsets.UTF_8));}
    static long events(String type)throws Exception{return journalEvents().stream().filter(e->type.equals(e.get("type"))).count();}
    static long noReplayEvents()throws Exception{return journalEvents().stream().filter(e->Set.of("job_lineage_reservation","job_lineage_published","board_load_intent","native_effect_intent","native_action_intent","native_step_intent").contains(e.get("type"))).count();}
    static void noReservations()throws Exception{check(events("job_lineage_reservation")==0,"Refusal before reservation preserves durable structure authority");}
    static void sameJob(Job before,String jobId,Map<String,Object> inventory)throws Exception{check(nativeJob()==before&&jobId.equals(read("get_status").get("job_id")),"Original native job identity retained");check(snapshot(before).equals(inventory),"Original complete native holder inventory retained");}
    static void expectPlan(JsonObject row,String code)throws Exception{Map<String,Object> failed=await(call("plan_placement_structure",jobArgs("changes",rows(row))),"failed");check(code.equals(result(failed).get("code")),"Exact native planning refusal "+code+": "+json(failed));}
    static Part replacement(Part original){Part copy=new Part(original.getId());copy.setPackage(original.getPackage());copy.setHeight(original.getHeight());copy.setName(original.getName());return copy;}
    static void exercise(String mode,Path state,Path samples)throws Exception{
        guiMode=mode.equals("listener-takeover");boot(state,samples,true);
        await(call("prepare_job",mutate("canonical_job",panelJob())),"succeeded");Job previous=nativeJob();Panel originalInlineRoot=previous.getRootPanelLocation().getPanel();String jobId=(String)read("get_status").get("job_id");Map<String,Object> before=snapshot(previous);Object revision=read("get_status").get("config_revision");Part spare=mode.endsWith("-object")?replacement(config.getPart("R0603-1K")):null;Library library=new Library();
        byte[] parts=Files.readAllBytes(root.resolve("config/parts.xml")),packages=Files.readAllBytes(root.resolve("config/packages.xml"));
        Map<String,Object> observation=new LinkedHashMap<>();observation.put("native_panel_definitions_shared",previous.getRootPanelLocation().getPanel().getChild(0).getPlacementsHolder().getDefinition()==previous.getRootPanelLocation().getPanel().getChild(1).getPlacementsHolder().getDefinition());
        if(mode.equals("validation")){
            expectPlan(cloneBoard("A","B"),"STRUCTURE_ID_REUSED");expectPlan(cloneBoard("absent","C"),"HOLDER_NOT_FOUND");expectPlan(removeBoard("absent"),"HOLDER_NOT_FOUND");
            JsonObject extra=cloneBoard("A","C");extra.addProperty("authority",true);expectPlan(extra,"UNKNOWN_FIELD");JsonObject missing=cloneBoard("A","C");missing.remove("enabled");expectPlan(missing,"INVALID_FIELD");
            JsonObject mixed=jobArgs("changes",rows(cloneBoard("A","C"),removeBoard("B")));Map<String,Object> bad=await(call("plan_placement_structure",mixed),"failed");check("INVALID_CHANGES".equals(result(bad).get("code")),"Native Bridge rejects mixed/multiple panel actions");
            JsonObject stale=jobArgs("changes",rows(cloneBoard("A","C")));stale.addProperty("expected_job_revision","job-stale");expectFault("plan_placement_structure",stale,"JOB_REVISION_CONFLICT");
            sameJob(previous,jobId,before);noReservations();library.unchanged();persistedPartsSame(parts,packages);noMachineActions();closeNative(false);writeResult("exercise",mode,observation);return;
        }
        if(mode.startsWith("history-")){
            BoardLocation selected=board(previous,"P1⇒A");previous.storePlacedStatus(selected,mode.equals("history-false")?"R0":"ORPHAN",mode.equals("history-true"));
            if(mode.equals("history-retired")){
                loads().checkpoint(previous);Map<String,Object> stateLoads=read("get_board_loads");Map<?,?> rootLoad=(Map<?,?>)((List<?>)stateLoads.get("roots")).get(0);
                await(call("register_board_load",mutate("job_id",jobId,"expected_board_load_revision",stateLoads.get("board_load_revision"),"root_instance_id","P1","action","replace","side","top","expected_load_id",rootLoad.get("load_id"))),"succeeded");check(previous.getPlacedStatusSnapshot().isEmpty(),"Actual load replacement retires all current keys");
                expectPlan(cloneBoard("A","C"),"LINEAGE_HISTORY_PRESENT");
            }else expectPlan(cloneBoard("A","C"),"STRUCTURE_HISTORY_PRESENT");
            noReservations();check(nativeJob()==previous,"History refusal never publishes a job");noMachineActions();closeNative(false);writeResult("exercise",mode,observation);return;
        }
        Map<String,Object> planned=plan(cloneBoard("A","C"));Map<String,Object> preview=effects(planned);check("panel-board-membership-v1".equals(preview.get("profile")),"Exact newly built native profile advertised");
        check(((List<?>)preview.get("before_inventory")).size()==6&&((List<?>)preview.get("result_inventory")).size()==7,"Complete preview contains two panels and every existing/proposed board");
        JsonObject input=applyArgs(planned);Part target=config.getPart("R0603-1K");String oldName=target.getName();JournalBoundary boundary=null;java.util.concurrent.atomic.AtomicInteger listenerHits=new java.util.concurrent.atomic.AtomicInteger();
        if(mode.equals("pre-identity"))target.setName("changed after preview");if(mode.equals("pre-object"))config.addPart(spare);
        if(mode.equals("pre-lease")){read("release_control_session","session_id",session);expectFault("apply_placement_structure",input,"SESSION_REQUIRED");noReservations();sameJob(previous,jobId,before);noMachineActions();closeNative(false);writeResult("exercise",mode,observation);return;}
        if(mode.equals("reserved-object"))boundary=intercept("job_lineage_reservation",()->config.addPart(spare),null);
        if(mode.equals("reserved-identity"))boundary=intercept("job_lineage_reservation",()->target.setName("changed after durable reserve"),null);
        if(mode.equals("reserved-lease")){read("renew_control_session","session_id",session,"ttl_seconds",1);boundary=intercept("job_lineage_reservation",()->Thread.sleep(1200),null);}
        if(mode.equals("force-before"))boundary=intercept("job_lineage_reservation",null,"exception");
        if(mode.equals("force-after"))boundary=intercept("job_lineage_reservation",()->{throw new IOException("CONTROLLED_POST_FORCE_EXCEPTION");},null);
        if(mode.startsWith("published-"))boundary=intercept("job_lineage_published",null,mode.endsWith("error")?"error":"exception");
        if(mode.equals("terminal-outcome"))boundary=intercept("terminal-operation",null,"exception");
        if(mode.startsWith("listener-")){
            java.beans.PropertyChangeListener listener=e->{if(listenerHits.getAndIncrement()!=0)return;check(config.getMachine().isTask(Thread.currentThread()),"Synchronous model listener ran on real native executor");check(Thread.holdsLock(bridge),"Synchronous model notification occurred inside Bridge ownership monitor");
                if(mode.equals("listener-flags"))board(previous,"P1⇒C").setLocallyEnabled(false);
                else if(mode.equals("listener-dirty"))target.setName("changed in dirty listener");
                else if(mode.equals("listener-root-swap")){Panel original=previous.getRootPanelLocation().getPanel();Panel replacement=new Panel();org.openpnp.util.IdentifiableList<PlacementsHolderLocation<?>> children=new org.openpnp.util.IdentifiableList<>();children.addAll(original.getChildren());replacement.setChildren(children);previous.getRootPanelLocation().setPanel(replacement);}
                else if(mode.equals("listener-root-name"))previous.getRootPanelLocation().getPanel().setName("unexpected root name");
                else if(mode.equals("listener-root-dimensions"))previous.getRootPanelLocation().getPanel().setDimensions(new Location(LengthUnit.Millimeters,42,37,0,0));
                else if(mode.equals("listener-root-file"))previous.getRootPanelLocation().getPanel().setFile(root.resolve("not-written.panel.xml").toFile());
                else if(mode.equals("listener-exception"))throw new IllegalStateException("CONTROLLED_MODEL_LISTENER_EXCEPTION");
                else if(mode.equals("listener-error"))throw new AssertionError("CONTROLLED_MODEL_LISTENER_ERROR");
                else if(mode.equals("listener-takeover"))try{ownership.granted=false;bridge.requestLocalGuiTakeover("reentrant-native-panel-test");}catch(Exception e1){throw new RuntimeException(e1);}
            };
            if(mode.equals("listener-dirty")){previous.setDirty(false);previous.addPropertyChangeListener("dirty",listener);}else previous.getRootPanelLocation().getPanel().addPropertyChangeListener("children",listener);
        }
        boolean journalFailure=mode.startsWith("force-")||mode.startsWith("published-")||mode.equals("terminal-outcome");String expected=mode.equals("success")?"succeeded":(mode.equals("pre-identity")||mode.equals("pre-object"))?"failed":journalFailure?"publication-fault":"outcome_unknown";
        Map<String,Object> op=await(call("apply_placement_structure",input),expected);String opId=(String)op.get("operation_id");observation.put("operation",op);observation.put("status",read("get_status"));
        if(boundary!=null){check(boundary.hits==1,"One exact real journal boundary injection");observation.put("injection_stack",boundary.stack);}
        if(mode.startsWith("listener-")){check(listenerHits.get()>=1,"Native synchronous model listener actually executed");observation.put("listener_calls",listenerHits.get());
            check("STRUCTURE_PUBLICATION_FAILED".equals(result(op).get("code")),"Synchronous listener refused through native publication fence");
            if(mode.equals("listener-root-swap"))check(previous.getRootPanelLocation().getPanel()!=originalInlineRoot,"Exact published inline Panel was replaced by listener");
            if(mode.equals("listener-root-name"))check("unexpected root name".equals(originalInlineRoot.getName()),"Inline root name mutation really occurred");
            if(mode.equals("listener-root-dimensions"))check(originalInlineRoot.getDimensions().getX()==42,"Inline root dimensions mutation really occurred");
            if(mode.equals("listener-root-file"))check(root.resolve("not-written.panel.xml").toFile().equals(originalInlineRoot.getFile())&&!Files.exists(root.resolve("not-written.panel.xml")),"Inline root file reference changed without writing a file");
        }
        target.setName(oldName);if(spare!=null){check(config.getPart(target.getId())==spare,"Exact configured Part object replacement happened at requested boundary");config.addPart(target);observation.put("test_injected_part_library_events",2);}
        if(mode.equals("pre-identity")||mode.equals("pre-object")){check("PART_IDENTITY".equals(result(op).get("code")),"Part drift rejected before reservation");noReservations();sameJob(previous,jobId,before);check(!Boolean.TRUE.equals(read("get_status").get("configuration_fault")),"Pre-reservation refusal does not fault configuration");}
        else if(!mode.equals("success")){
            check(journalFailure?Boolean.TRUE.equals(read("get_status").get("journal_fault")):Boolean.TRUE.equals(read("get_status").get("configuration_fault")),"Uncertain panel publication fences further work");
            check(nativeJob()==previous&&jobId.equals(read("get_status").get("job_id")),"Panel edits never replace selected native Job identity");
            if(mode.startsWith("reserved")||mode.startsWith("force"))check(before.equals(snapshot(previous)),"Boundary before native setter preserves original graph");
            if(!mode.equals("force-before"))check(events("job_lineage_reservation")==1,"Real durable reservation remains in journal");
            if(mode.equals("terminal-outcome"))check(events("job_lineage_published")==1,"Body publication is known while operation completion is unknown");
            else check(events("job_lineage_published")==0,"Unfinished native panel publication never claims lineage success");
            if(journalFailure)check(op.get("publication_fault") instanceof Map&&"running".equals(op.get("state")),"Completion publication failure preserves last durable running state");
        }else{
            Map<String,Object> body=result(op);check(Boolean.TRUE.equals(body.get("applied"))&&Boolean.TRUE.equals(body.get("job_identity_preserved")),"Actual panel publication preserves Job object");check(nativeJob()==previous,"Actual Job object preserved");check(revision.equals(read("get_status").get("config_revision")),"Job edit does not change machine configuration revision");
            for(String id:List.of("P2","P2⇒A","P2⇒B"))check(before.get(id).equals(snapshot(previous).get(id)),"Repeated sibling occurrence unchanged: "+id);
            check(lineage().snapshot(jobId).reservedLogicalIds.contains(holderKey("P1⇒C")),"Successful clone retires empty-capable exact holder identity");
            long savedEvents=noReplayEvents();Map<String,Object> repeat=call("apply_placement_structure",input);check(opId.equals(repeat.get("operation_id"))&&"succeeded".equals(repeat.get("state")),"Identical request returns completed original operation");JsonObject conflict=cloneJson(input);conflict.addProperty("plan_id","different-plan");expectFault("apply_placement_structure",conflict,"REQUEST_ID_CONFLICT");check(savedEvents==noReplayEvents(),"Request retry/conflict cannot publish again");
            apply(removeBoard("B"));check(!snapshot(previous).containsKey("P1⇒B")&&snapshot(previous).size()==6,"Actual clone then removal leaves complete desired child sequence");
            Map<String,Object> saved=await(call("save_job",mutate()),"succeeded");Map<?,?> artifact=(Map<?,?>)result(saved).get("artifact");observation.put("saved_artifact",artifact);
            Map<String,Object> inventory=snapshot(previous);await(call("load_job",mutate("artifact_id",artifact.get("artifact_id"))),"succeeded");check(inventory.equals(snapshot(nativeJob())),"Actual native signed save/load preserves panel graph");
            String alias=(String)read("get_status").get("job_id");check(!alias.equals(jobId)&&lineage().snapshot(alias).lineageId.equals(lineage().snapshot(jobId).lineageId),"Saved document binds fresh Job alias to same durable lineage");
            expectPlan(cloneBoard("A","B"),"STRUCTURE_ID_REUSED");expectPlan(cloneBoard("A","C"),"STRUCTURE_ID_REUSED");
            Files.writeString(root.resolve("saved-artifact.json"),json(artifact));observation.put("final_inventory",snapshot(nativeJob()));
        }
        library.unchanged(spare==null?0:2);persistedPartsSame(parts,packages);noMachineActions();long beforeRetry=noReplayEvents();Map<String,Object> statusRequest=read("get_request_status","request_id",input.get("request_id").getAsString());check(opId.equals(((Map<?,?>)statusRequest.get("operation")).get("operation_id")),"Original panel request remains readable");
        try{Map<String,Object> retry=call("apply_placement_structure",input);check(opId.equals(retry.get("operation_id")),"Retry only returns original operation");}catch(Bridge.Fault fault){check(Set.of("JOURNAL_FAULT","CONFIGURATION_FAULT","SESSION_REQUIRED","OWNERSHIP_REVOKED","RECOVERY_REQUIRED").contains(fault.code),"Fenced retry refusal: "+fault.code);}
        check(beforeRetry==noReplayEvents(),"Unknown request is never replayed");
        Files.writeString(root.resolve("original-request.json"),input.toString());Files.writeString(root.resolve("closed-case.json"),json(Bridge.map("operation_id",opId,"request_id",input.get("request_id").getAsString(),"state",journalFailure?"outcome_unknown":expected,"bridge_instance_id",read("get_status").get("bridge_instance_id"),"job_id",jobId,"mutation_event_count",noReplayEvents(),"lineage_reservations",events("job_lineage_reservation"),"lineage_publications",events("job_lineage_published"))));
        closeNative(journalFailure);writeResult("exercise",mode,observation);
    }
    static void closeNative(boolean fenced)throws Exception{
        if(fenced){try{bridge.close();throw new AssertionError("Unknown owner closed");}catch(Bridge.Fault fault){check("BUSY".equals(fault.code),"Pending completion owner refuses ordinary close");}return;}
        bridge.close();if(ownership!=null)ownership.release();config.getMachine().close();
    }
    static void restart(String mode,Path state,Path samples)throws Exception{
        guiMode=false;root=state;Map<?,?> closed=JSON.fromJson(Files.readString(root.resolve("closed-case.json")),Map.class);JsonObject original=new JsonParser().parse(Files.readString(root.resolve("original-request.json"))).getAsJsonObject();boot(state,samples,false);
        Map<String,Object> status=read("get_status"),op=read("get_operation","operation_id",closed.get("operation_id"));check(!closed.get("bridge_instance_id").equals(status.get("bridge_instance_id")),"Fresh Bridge instance reads retained journal");check(closed.get("state").equals(op.get("state")),"Restart preserves original operation success/unknown outcome");
        check(nativeJob()==null&&status.get("job_id")==null,"Restart restores no job or execution authority");check(noReplayEvents()==((Number)closed.get("mutation_event_count")).longValue(),"Startup performs no native model replay");
        NativeJobLineage.Snapshot s=lineage().snapshot((String)closed.get("job_id"));long reservations=((Number)closed.get("lineage_reservations")).longValue(),published=((Number)closed.get("lineage_publications")).longValue();
        check((s.pendingTransaction!=null)==(reservations>published),"Durable pending reservation status survives fresh JVM");if(reservations>0)check(s.reservedLogicalIds.contains(holderKey("P1⇒C")),"Exact holder tombstone survives fresh JVM reducer");
        expectFault("apply_placement_structure",original,"SESSION_REQUIRED");session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");JsonObject changed=cloneJson(original);changed.addProperty("session_id",session);expectFault("apply_placement_structure",changed,"REQUEST_ID_CONFLICT");
        if(!mode.equals("success")){changed.addProperty("request_id",UUID.randomUUID().toString());expectFault("apply_placement_structure",changed,"RECOVERY_REQUIRED");}
        else{Map<?,?> saved=JSON.fromJson(Files.readString(root.resolve("saved-artifact.json")),Map.class);await(call("load_job",mutate("artifact_id",saved.get("artifact_id"))),"succeeded");expectPlan(cloneBoard("A","B"),"STRUCTURE_ID_REUSED");check(snapshot(nativeJob()).size()==6,"Saved model recovery remains separate from operation replay");}
        noMachineActions();closeNative(false);writeResult("restart",mode,Bridge.map("operation",op,"status",status,"pending_transaction",s.pendingTransaction));
    }
    static void writeResult(String phase,String mode,Object observations)throws Exception{Map<String,Object> result=Bridge.map("passed",true,"phase",phase,"mode",mode,"assertions",checks.size(),"checks",checks,"observations",observations,"physical_qualification",false,"machine_enable_calls",0,"native_feed_effects",0,"placements_executed",0,"bridge_origin",Bridge.class.getProtectionDomain().getCodeSource().getLocation().toString(),"model_origin",NativePlacementEdits.class.getProtectionDomain().getCodeSource().getLocation().toString(),"native_origin",org.openpnp.model.Panel.class.getProtectionDomain().getCodeSource().getLocation().toString(),"swing_qualification",false,"test_gui_ownership_adapter",guiMode);Files.writeString(root.resolve(phase+"-result.json"),json(result));System.out.println(MARKER+json(result));}
    static Map<String,Object> child(String phase,String mode,Path state,Path samples)throws Exception{
        Files.createDirectories(state.resolve(phase+"-home"));Files.createDirectories(state.resolve(phase+"-tmp"));Path log=state.resolve(phase+".log");
        List<String> command=List.of(Path.of(System.getProperty("java.home"),"bin/java").toString(),"-Xmx768m","-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","-Djava.io.tmpdir="+state.resolve(phase+"-tmp"),"-Duser.home="+state.resolve(phase+"-home"),"--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-cp",System.getProperty("java.class.path"),NativePanelBoardMembershipBridgeTest.class.getName(),phase,mode,state.toString(),samples.toString());
        ProcessBuilder builder=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile());builder.environment().keySet().removeIf(k->k.startsWith("OPENPNP_")||k.startsWith("LD_")||k.startsWith("DYLD_")||Set.of("JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","JDK_JAVAC_OPTIONS","_JAVA_OPTIONS","CLASSPATH").contains(k));
        Process process=builder.start();boolean finished=process.waitFor(25,TimeUnit.SECONDS);if(!finished){process.destroy();if(!process.waitFor(1,TimeUnit.SECONDS)){process.destroyForcibly();process.waitFor(2,TimeUnit.SECONDS);}}
        if(!finished||process.isAlive()||process.exitValue()!=0)throw new AssertionError("Native child failed "+phase+"/"+mode+": "+Files.readString(log));check(true,"Native child completed "+phase+"/"+mode);
        Map<String,Object> result=JSON.fromJson(Files.readString(state.resolve(phase+"-result.json")),Map.class);check(Boolean.TRUE.equals(result.get("passed")),"Child retained a passing result");return result;
    }
    public static void main(String[]args)throws Exception{
        if(args.length==1){Path suiteRoot=Files.createTempDirectory("openpnp-panel-membership-bridge-");List<Object> results=new ArrayList<>();
            for(String mode:List.of("success","validation","history-true","history-false","history-orphan","history-retired","pre-identity","pre-object","pre-lease","reserved-identity","reserved-object","reserved-lease","force-before","force-after","published-exception","published-error","terminal-outcome","listener-flags","listener-dirty","listener-root-swap","listener-root-name","listener-root-dimensions","listener-root-file","listener-exception","listener-error","listener-takeover")){
                Path state=suiteRoot.resolve(mode);Files.createDirectories(state);results.add(child("exercise",mode,state,Path.of(args[0]).toRealPath()));
                if(!Set.of("validation","pre-identity","pre-object","pre-lease").contains(mode)&&!mode.startsWith("history-"))results.add(child("restart",mode,state,Path.of(args[0]).toRealPath()));
            }
            Map<String,Object> report=Bridge.map("passed",true,"native_jvm_phases",results.size(),"results",results,"root",suiteRoot.toString(),"physical_qualification",false);Files.writeString(suiteRoot.resolve("report.json"),json(report));System.out.println("OPENPNP_NATIVE_PANEL_MEMBERSHIP_BRIDGE_RESULT "+json(report));return;
        }
        try{if(args[0].equals("exercise"))exercise(args[1],Path.of(args[2]),Path.of(args[3]));else if(args[0].equals("restart"))restart(args[1],Path.of(args[2]),Path.of(args[3]));else throw new IllegalArgumentException("Unknown phase");System.exit(0);}
        catch(Throwable failure){failure.printStackTrace();System.exit(1);}
    }
}
