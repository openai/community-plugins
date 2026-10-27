/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.Window;
import java.beans.PropertyChangeListener;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.CopyOnWriteArrayList;
import javax.swing.*;
import javax.swing.event.*;
import org.openpnp.gui.MainFrame;
import org.openpnp.gui.tablemodel.PlacementsHolderLocationsTableModel;
import org.openpnp.model.*;
import org.openpnp.spi.base.AbstractMachine;
import static org.openpnp.codex.NativeGuiOwnershipTest.*;

/** Real native GUI/bootstrap/HTTP/processor, without Bridge on application classpath. */
public final class NativeGuiPanelMembershipTest {
    static final List<String> checks=new ArrayList<>();
    static final List<String> uncaught=new CopyOnWriteArrayList<>();
    static final List<Map<String,Object>> tableEvents=new CopyOnWriteArrayList<>(), titleEvents=new CopyOnWriteArrayList<>(), nativeEvents=new CopyOnWriteArrayList<>();
    static final Map<String,Object> proof=new LinkedHashMap<>();
    static PropertyChangeListener rootListener;
    static final List<String> BEFORE=List.of("P1","P1⇒A","P1⇒B","P2","P2⇒A","P2⇒B");
    static final List<String> CLONED=List.of("P1","P1⇒A","P1⇒B","P1⇒C","P2","P2⇒A","P2⇒B");
    static final List<String> AFTER=List.of("P1","P1⇒A","P1⇒C","P2","P2⇒A","P2⇒B");
    static void require(boolean value,String label){check(value,label);checks.add(label);}
    static JsonArray array(Object...values){return JSON.toJsonTree(Arrays.asList(values)).getAsJsonArray();}
    static JsonObject status()throws Exception{return rpc("openpnp_get_status",object());}
    static JsonObject command(Object...pairs)throws Exception{JsonObject p=mutation(pairs);p.add("expected_config_revision",status().get("config_revision"));return p;}
    static JsonObject bound(Object...pairs)throws Exception{JsonObject p=command(pairs),s=status();for(String key:List.of("job_id","job_revision","board_load_revision"))p.add(key.equals("job_id")?key:"expected_"+key,s.get(key));return p;}
    static JsonObject result(String method,JsonObject params)throws Exception{return run(method,params).getAsJsonObject("result");}
    static JsonObject instance(String id,String kind,String definition,double x,double y){return object("id",id,"kind",kind,"definitionId",definition,"x",x,"y",y,"z",0,"rotation",0,"side","top","enabled",true);}
    static JsonObject canonical(Part p){
        double height=p.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();String pid=p.getId(),pkg=p.getPackage().getId();JsonArray placements=new JsonArray();
        for(int i=0;i<2;i++)placements.add(object("ref","R"+(i+1),"partId",pid,"packageId",pkg,"heightMm",height,"x",12+8*i,"y",12,"z",0,"rotation",0,"side","top","enabled",true,"type","placement"));
        return object("schemaVersion",1,"id","gui-panel73","units","mm","coordinateConvention","openpnp-top-view",
            "parts",array(object("id",pid,"packageId",pkg,"heightMm",height,"value","1K")),
            "boards",array(object("id","gui-board","widthMm",40,"heightMm",30,"placements",placements)),
            "panels",array(object("id","repeated-panel","widthMm",100,"heightMm",100,"children",array(instance("A","board","gui-board",0,0),instance("B","board","gui-board",50,0)))),
            "instances",array(instance("P1","panel","repeated-panel",100,100),instance("P2","panel","repeated-panel",200,100)));
    }
    static JsonObject cloneChild(){return object("action","clone_board_child","scope","job_instance","parent_instance_id","P1","source_child_id","A","new_child_id","C",
        "location",object("frame","holder","units","mm","x",0,"y",40,"z",0,"rotation",0),"side","Top","enabled",true,"check_fiducials",false);}
    static JsonObject removeChild(String child){return object("action","remove_board_child","scope","job_instance","parent_instance_id","P1","child_id",child);}
    static List<String> visibleRows()throws Exception{return edt(()->{
        JTable table=frame.getJobTab().getPlacementsHolderLocationsTable();require(table.isShowing(),"actual native JobPanel holder table is showing");
        require(table.getModel().getClass()==PlacementsHolderLocationsTableModel.class,"actual native holder table model class");
        PlacementsHolderLocationsTableModel model=(PlacementsHolderLocationsTableModel)table.getModel();List<String> ids=new ArrayList<>();
        for(int i=0;i<table.getRowCount();i++)ids.add(model.getPlacementsHolderLocation(table.convertRowIndexToModel(i)).getUniqueId());return ids;
    });}
    static void rows(List<String> wanted,String label)throws Exception{edt(()->null);require(visibleRows().equals(wanted),label+" exact visible row identities");proof.put(label+"_visible_rows",visibleRows());}
    static void installObservers()throws Exception{
        edt(()->{frame.getJobTab().getPlacementsHolderLocationsTable().getModel().addTableModelListener(e->tableEvents.add(map("type",e.getType(),"first_row",e.getFirstRow(),"last_row",e.getLastRow(),"column",e.getColumn(),"edt",SwingUtilities.isEventDispatchThread(),"thread",Thread.currentThread().getName())));
            frame.addPropertyChangeListener("title",e->titleEvents.add(map("title",e.getNewValue(),"edt",SwingUtilities.isEventDispatchThread(),"thread",Thread.currentThread().getName())));return null;});
    }
    static void observeNativeRoot(Job job){rootListener=e->nativeEvents.add(map("property",e.getPropertyName(),"edt",SwingUtilities.isEventDispatchThread(),"native_task",config.getMachine().isTask(Thread.currentThread()),"thread",Thread.currentThread().getName()));job.getRootPanelLocation().getPanel().addPropertyChangeListener("children",rootListener);}
    static Map<String,String> modelFiles()throws Exception{Map<String,String> m=new LinkedHashMap<>();for(String name:List.of("machine.xml","parts.xml","packages.xml"))m.put(name,sha(config.getConfigurationDirectory().toPath().resolve(name)));return m;}
    static Map<String,Object> nativeCensus(Job job){List<Object> boards=new ArrayList<>();for(BoardLocation b:job.getBoardLocations()){List<String> refs=new ArrayList<>();for(Placement p:b.getBoard().getPlacements())refs.add(p.getId());boards.add(map("id",b.getUniqueId(),"refs",refs,"x",b.getLocation().getX(),"y",b.getLocation().getY(),"side",b.getSide(),"enabled",b.isLocallyEnabled()));}return map("boards",boards);}
    static JsonObject edit(JsonObject change,Job original,PanelLocation inlineRoot,List<String> expected)throws Exception{
        int eventCount=tableEvents.size();JsonObject p=bound("changes",array(change)),plan=result("openpnp_plan_placement_structure",p);
        require(edt(()->frame.getJobTab().getJob())==original,"planning preserves exact JobPanel job identity");
        JsonObject apply=bound("plan_id",plan.get("plan_id").getAsString());JsonObject op=run("openpnp_apply_placement_structure",apply);
        require(rpc("openpnp_apply_placement_structure",apply).get("operation_id").equals(op.get("operation_id")),"GUI structural request returns original receipt on replay");
        rows(expected,change.get("action").getAsString());
        require(edt(()->frame.getJobTab().getJob())==original&&original.getRootPanelLocation()==inlineRoot,"native publication preserves live Job and inline-root identity");
        require(tableEvents.size()>eventCount,"actual native JobPanel table emits structural refresh");
        require(op.getAsJsonObject("result").get("requires_explicit_load_rebinding").getAsBoolean(),"actual GUI apply reports explicit load requirement");return op;
    }
    static void expectOperation(String code,String method,JsonObject params)throws Exception{
        JsonObject op=rpc(method,params);String id=op.get("operation_id").getAsString();long deadline=System.nanoTime()+30_000_000_000L;
        while(List.of("accepted","running").contains(op.get("state").getAsString())){require(System.nanoTime()<deadline,"bounded refusal observation");Thread.sleep(10);op=rpc("openpnp_get_operation",object("operation_id",id));}
        require(op.get("state").getAsString().equals("failed")&&op.getAsJsonObject("result").get("code").getAsString().equals(code),"GUI native refusal "+code);waitUntil(()->!config.getMachine().isBusy(),10000,"refusal drain");
    }
    static void journalProof(JsonObject run)throws Exception{
        String oid=run.get("operation_id").getAsString();List<Object> kept=new ArrayList<>();List<String> complete=new ArrayList<>();Map<String,JsonObject> pending=new LinkedHashMap<>();Map<String,Integer> counts=new LinkedHashMap<>();int reservations=0;Set<String> eventIds=new HashSet<>();
        for(String line:Files.readAllLines(state.resolve("journal/operations.jsonl"))){JsonObject row=new JsonParser().parse(line).getAsJsonObject(),p=row.getAsJsonObject("payload");String type=row.get("type").getAsString();if(type.equals("job_lineage_reservation"))reservations++;
            if(type.equals("native_action_intent")||type.equals("native_action_outcome")||(type.equals("native_placement_checkpoint")&&p.get("state").getAsString().equals("native-placement-complete-hook"))){
                require(p.get("operation_id").getAsString().equals(oid),"all actual native effects belong to only GUI production run");JsonObject ctx=p.getAsJsonObject("context");String key=ctx.get("board_instance_id").getAsString()+"⇒"+ctx.get("placement_id").getAsString();require(!key.startsWith("P1⇒B⇒"),"removed board never performs native action");
                if(type.equals("native_action_intent")){require(pending.put(p.get("action_id").getAsString(),row)==null,"native action ID unique");String kind=p.get("kind").getAsString();counts.put(kind,counts.getOrDefault(kind,0)+1);}
                else if(type.equals("native_action_outcome")){JsonObject begin=pending.remove(p.get("action_id").getAsString());require(begin!=null&&begin.get("sequence").getAsLong()<row.get("sequence").getAsLong()&&p.get("state").getAsString().equals("native_hook_returned"),"ordered matching actual native action return");}
                else {complete.add(key);require(eventIds.add(p.get("event_id").getAsString()),"unique complete checkpoint");require(ctx.get("native_placed_status").getAsBoolean(),"actual native placed status at Complete hook");}
                kept.add(JSON.fromJson(row,Map.class));
            }
        }
        List<String> expected=new ArrayList<>();for(String b:List.of("P1⇒A","P1⇒C","P2⇒A","P2⇒B"))for(String ref:List.of("R1","R2"))expected.add(b+"⇒"+ref);Collections.sort(expected);Collections.sort(complete);
        require(complete.equals(expected)&&pending.isEmpty(),"exact eight complete identities and no pending native actions");for(String kind:List.of("feed","pick","align","release"))require(counts.getOrDefault(kind,0)==8,"eight actual native "+kind+" actions");require(reservations==2,"exact clone/removal reservations with no replay");proof.put("native_complete_placements",8);proof.put("native_action_pairs",32);proof.put("complete_identities",complete);proof.put("native_effect_records",kept);proof.put("journal_sha256",sha(state.resolve("journal/operations.jsonl")));
    }
    public static void main(String[]args)throws Exception{
        if(args.length!=4)throw new IllegalArgumentException("runtime-dir bridge-jar bootstrap-js new-test-root");Path runtime=Path.of(args[0]).toRealPath(),jar=Path.of(args[1]).toRealPath(),script=Path.of(args[2]).toRealPath(),root=Path.of(args[3]).toAbsolutePath();if(Files.exists(root))throw new IllegalArgumentException("new root required");Files.createDirectories(root);Throwable failure=null;List<String> cleanupErrors=new ArrayList<>();boolean machineClosed=false,windowsClosed=false,detached=false;
        Thread.setDefaultUncaughtExceptionHandler((thread,error)->{uncaught.add(thread.getName()+": "+error);error.printStackTrace();});
        try{
            require("org.openpnp.codex.IsolatedPreferencesFactory".equals(System.getProperty("java.util.prefs.PreferencesFactory")),"explicit isolated preferences");require(java.util.prefs.Preferences.userRoot().getClass().getName().endsWith("IsolatedPreferencesFactory$MemoryNode"),"actual nonpersistent preferences");
            try{Class.forName("org.openpnp.codex.Bridge",false,ClassLoader.getSystemClassLoader());throw new AssertionError("Bridge on app classpath");}catch(ClassNotFoundException expected){}
            Path dir=root.resolve("config");Files.createDirectory(dir);state=root.resolve("bridge-state");Configuration.initialize(dir.toFile());config=Configuration.get();config.load();config.getMachine().setProperty("Welcome2_0_Dialog_Shown",true);config.save();config.getMachine().close();Configuration.initialize(dir.toFile());config=Configuration.get();frame=edt(()->{MainFrame f=new MainFrame(config);f.setVisible(true);return f;});accelerate();installObservers();
            bootstrap=config.getScripting().getScriptsDirectory().toPath().resolve("codex-bootstrap.js");Files.copy(script,bootstrap);properties(runtime,jar,bootstrap,state);config.getScripting().execute(bootstrap.toFile());connect();JsonObject caps=rpc("openpnp_get_capabilities",object());require(caps.get("bridge_artifact_sha256").getAsString().equals(sha(jar)),"actual classloader attached exact selected Bridge");require(caps.get("simulator_profile").getAsString().equals("gui-simulator"),"actual native GUI attachment profile");require(caps.getAsJsonObject("panel_board_membership").get("profile").getAsString().equals("panel-board-membership-v1"),"GUI advertises exact panel profile");
            expect("LOCAL_GRANT_REQUIRED","openpnp_request_control_session",object("request_id",UUID.randomUUID().toString(),"ttl_seconds",600));click("Allow Codex control");awaitOwnership(true);session=rpc("openpnp_request_control_session",object("request_id",UUID.randomUUID().toString(),"ttl_seconds",600)).get("session_id").getAsString();
            try{edt(()->{frame.getJobTab().jobStart();return null;});throw new AssertionError("local GUI start bypassed ownership");}catch(IllegalStateException expected){checks.add("actual JobPanel local start refused while externally owned");}
            Part part=config.getPart("R0805-1K");require(part!=null&&part.getPackage()!=null,"actual native part/package selected");JsonObject canonical=canonical(part);Files.writeString(root.resolve("canonical-job.json"),JSON.toJson(canonical));JsonObject prepared=result("openpnp_prepare_job",command("canonical_job",canonical));require(prepared.get("requested").getAsInt()==8,"canonical GUI fixture has eight enabled native records");rows(BEFORE,"prepared");Job job=edt(()->frame.getJobTab().getJob());PanelLocation inlineRoot=job.getRootPanelLocation();observeNativeRoot(job);Map<String,String> modelBefore=modelFiles();proof.put("model_files_before_membership",modelBefore);proof.put("native_before",nativeCensus(job));
            List<PanelLocation> panels=new ArrayList<>(job.getPanelLocations());require(panels.remove(inlineRoot),"native panel inventory includes exact inline root");require(panels.size()==2&&panels.get(0).getPanel().getDefinition()!=panels.get(1).getPanel().getDefinition(),"public canonical fixture has distinct native panel definitions");
            JsonObject cloned=edit(cloneChild(),job,inlineRoot,CLONED),removed=edit(removeChild("B"),job,inlineRoot,AFTER);inlineRoot.getPanel().removePropertyChangeListener("children",rootListener);require(config.getPart(part.getId())==part,"native part library object identity unchanged by panel operations");require(modelFiles().equals(modelBefore),"machine and library files byteexact during GUI panel operations");proof.put("native_after",nativeCensus(job));proof.put("cloned",JSON.fromJson(cloned,Map.class));proof.put("removed",JSON.fromJson(removed,Map.class));
            edt(()->{PlacementsHolderLocationsTableModel model=(PlacementsHolderLocationsTableModel)frame.getJobTab().getPlacementsHolderLocationsTable().getModel();for(int i=0;i<model.getRowCount();i++)if("P1⇒C".equals(model.getPlacementsHolderLocation(i).getUniqueId())){JTable t=frame.getJobTab().getPlacementsHolderLocationsTable();int view=t.convertRowIndexToView(i);t.setRowSelectionInterval(view,view);}return null;});
            edt(()->null);require(edt(()->frame.getJobTab().getJobPlacementsPanel().getTable().getRowCount())==2,"actual GUI placement table shows both cloned-board records");
            JsonObject saved=result("openpnp_save_job",command());JsonObject reload=result("openpnp_load_job",command("artifact_id",saved.getAsJsonObject("artifact").get("artifact_id").getAsString()));rows(AFTER,"reloaded");require(reload.getAsJsonObject("job").get("requested").getAsInt()==8,"native saved GUI document reloads eight records");require(modelFiles().equals(modelBefore),"native document save/load preserves machine/library files");
            for(String id:List.of("P1","P2")){JsonObject loads=rpc("openpnp_get_board_loads",object()),load=null;for(JsonElement e:loads.getAsJsonArray("roots"))if(e.getAsJsonObject().get("root_instance_id").getAsString().equals(id))load=e.getAsJsonObject();require(load!=null&&load.get("load_state").getAsString().equals("job_binding_unconfirmed"),"GUI root needs explicit load confirmation "+id);require(load.get("definition_matches").getAsBoolean()==id.equals("P2"),"native load comparison identifies changed root "+id);JsonObject p=bound("root_instance_id",id,"action",id.equals("P1")?"replace":"same-load","side",load.get("side").getAsString(),"expected_load_id",load.get("load_id").getAsString());p.remove("expected_job_revision");result("openpnp_register_board_load",p);}
            run("openpnp_set_machine_enabled",command("enabled",true));run("openpnp_home_machine",command());require(result("openpnp_validate_job",command()).get("valid").getAsBoolean(),"actual GUI native job validates after explicit load decisions");JsonObject done=run("openpnp_start_job",command("job_id",status().get("job_id").getAsString()));require(done.getAsJsonObject("result").get("placed").getAsInt()==8,"actual GUI-owned native processor completes eight placements");run("openpnp_set_machine_enabled",command("enabled",false));rows(AFTER,"completed");
            Job doneJob=edt(()->frame.getJobTab().getJob());for(BoardLocation b:doneJob.getBoardLocations())for(Placement p:b.getBoard().getPlacements())require(doneJob.retrievePlacedStatus(b,p.getId()),"actual native GUI job retains placed status "+b.getUniqueId()+"⇒"+p.getId());
            JsonObject placed=result("openpnp_save_job",command());result("openpnp_load_job",command("artifact_id",placed.getAsJsonObject("artifact").get("artifact_id").getAsString()));rows(AFTER,"placed_reloaded");expectOperation("STRUCTURE_HISTORY_PRESENT","openpnp_plan_placement_structure",bound("changes",array(removeChild("C"))));
            journalProof(done);rpc("openpnp_release_control_session",object("session_id",session));awaitOwnership(false);click("Disconnect bridge");waitUntil(()->edt(()->frame.getRootPane().getClientProperty(KEY))==null,30000,"GUI native bridge detach");detached=true;edt(()->null);
            require(!tableEvents.isEmpty()&&tableEvents.stream().allMatch(e->Boolean.TRUE.equals(e.get("edt"))),"all actual JobPanel table events run on EDT");require(!titleEvents.isEmpty()&&titleEvents.stream().allMatch(e->Boolean.TRUE.equals(e.get("edt"))),"all actual JFrame title events run on EDT");require(nativeEvents.size()>=2&&nativeEvents.stream().allMatch(e->Boolean.FALSE.equals(e.get("edt"))&&Boolean.TRUE.equals(e.get("native_task"))),"structural child publication occurs on owned native executor");require(uncaught.isEmpty(),"no uncaught Swing/native errors");proof.put("all_swing_model_events_on_edt",true);proof.put("all_title_events_on_edt",true);
        }catch(Throwable t){failure=t;t.printStackTrace();}finally{
            try{if(config!=null){config.getMachine().close();machineClosed=true;}}catch(Throwable t){cleanupErrors.add("machine close: "+t);}
            try{edt(()->{for(Window w:Window.getWindows())w.dispose();return null;});windowsClosed=edt(()->Arrays.stream(Window.getWindows()).noneMatch(Window::isDisplayable));}catch(Throwable t){cleanupErrors.add("window disposal: "+t);}
        }
        boolean ok=failure==null&&cleanupErrors.isEmpty()&&machineClosed&&windowsClosed&&detached;proof.putAll(map("functional_checks_passed",ok,"assertions",checks.size(),"checks",checks,"table_events",tableEvents,"title_events",titleEvents,"native_publication_events",nativeEvents,"uncaught_errors",uncaught,"failure",failure==null?null:failure.toString(),"cleanup_errors",cleanupErrors,"machine_closed",machineClosed,"all_owned_windows_disposed",windowsClosed,"bridge_detached",detached,"bridge_sha256",sha(jar),"runtime_manifest_sha256",sha(runtime.resolve("codex-build-manifest.json")),"bridge_on_application_classpath",false,"native_gui_component_api",true,"desktop_gestures_performed",false,"physical_qualification",false,"genuine_shared_native_panel_definition",false,"fixture_scope","public canonical repeated-panel definitions; native sharing separately tested","fixture_acceleration","same declared test-only NullDriver/rates/fixed0ms settling/dwell as NativeGuiOwnershipTest; no production setting change"));Files.writeString(root.resolve("panel-membership-gui-proof.json"),JSON.toJson(proof));System.out.println("OPENPNP_GUI_PANEL_MEMBERSHIP_RESULT "+JSON.toJson(map("passed",ok,"assertions",checks.size(),"native_complete_placements",proof.get("native_complete_placements"),"desktop_gestures_performed",false)));System.exit(ok?0:1);
    }
}
