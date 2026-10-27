/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.*;
import java.awt.event.*;
import java.beans.PropertyChangeListener;
import java.nio.file.*;
import java.util.*;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import javax.swing.*;
import javax.swing.event.*;
import org.openpnp.gui.MainFrame;
import org.openpnp.gui.support.HeadMountableItem;
import org.openpnp.gui.support.AxesComboBoxModel;
import org.openpnp.gui.support.ActuatorsComboBoxModel;
import org.openpnp.spi.base.AbstractMachine;
import org.openpnp.model.AbstractModelObject;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.ReferenceNozzle;
import static org.openpnp.codex.NativeGuiOwnershipTest.*;

/** Actual GUI/HTTP/native API fixture. No plugin Bridge class exists on application classpath. */
public final class NativeGuiTopologyTest {
    static final List<Map<String,Object>> modelEvents=new CopyOnWriteArrayList<>();
    static final List<String> edtFailures=new CopyOnWriteArrayList<>();
    static final Map<String,Object> proof=new LinkedHashMap<>();
    static int assertions;
    static final List<Map<String,Object>> auxiliaryEvents=new CopyOnWriteArrayList<>();
    static final List<Map<String,Object>> nativeEvents=new CopyOnWriteArrayList<>();
    static final List<String> assignmentChanges=new CopyOnWriteArrayList<>();
    static final List<JComboBox<?>> selectors=new ArrayList<>();
    static final List<String> initialSelections=new ArrayList<>();
    static final List<Integer> initialSizes=new ArrayList<>();
    static void observeModel(String label, ComboBoxModel<?> model) {
        model.addListDataListener(new ListDataListener(){
            void event(ListDataEvent e){auxiliaryEvents.add(map("surface",label,"type",e.getType(),"index0",e.getIndex0(),"index1",e.getIndex1(),"event_dispatch_thread",SwingUtilities.isEventDispatchThread(),"thread",Thread.currentThread().getName()));}
            public void intervalAdded(ListDataEvent e){event(e);}public void intervalRemoved(ListDataEvent e){event(e);}public void contentsChanged(ListDataEvent e){event(e);}
        });
    }
    static void installSelector(String label, DefaultComboBoxModel model, String selected) {
        JComboBox box=new JComboBox(model);box.setSelectedItem(selected);selectors.add(box);initialSelections.add(selected);initialSizes.add(model.getSize());
        observeModel(label,model);box.addItemListener(e->assignmentChanges.add(label+":"+e.getStateChange()+":"+e.getItem()));
    }
    static JButton actuatorButton(Component component,String name){
        if(component instanceof JButton&&((JButton)component).getText()!=null&&((JButton)component).getText().endsWith(":"+name))return (JButton)component;
        if(component instanceof Container)for(Component c:((Container)component).getComponents()){JButton b=actuatorButton(c,name);if(b!=null)return b;}return null;
    }
    static void observeTopology(ReferenceNozzle old)throws Exception {
        Head head=old.getHead();AbstractMachine machine=(AbstractMachine)config.getMachine();
        PropertyChangeListener nativeListener=e->nativeEvents.add(map("property",e.getPropertyName(),"event_dispatch_thread",SwingUtilities.isEventDispatchThread(),"native_machine_task",machine.isTask(Thread.currentThread()),"thread",Thread.currentThread().getName()));
        machine.addPropertyChangeListener("axes",nativeListener);machine.addPropertyChangeListener("nozzleTips",nativeListener);
        ((AbstractModelObject)head).addPropertyChangeListener("nozzles",nativeListener);((AbstractModelObject)head).addPropertyChangeListener("actuators",nativeListener);
        edt(()->{
            installSelector("axis-all",new AxesComboBoxModel(machine,Axis.class,null,true),old.getAxisX().getName());
            installSelector("axis-z",new AxesComboBoxModel(machine,Axis.class,Axis.Type.Z,true),old.getAxisZ().getName());
            installSelector("axis-rotation",new AxesComboBoxModel(machine,Axis.class,Axis.Type.Rotation,true),old.getAxisRotation().getName());
            String valve=head.getActuators().get(0).getName();
            installSelector("head-actuators",new ActuatorsComboBoxModel(head),valve);
            installSelector("combined-actuators",new ActuatorsComboBoxModel(machine,head),valve);
            JButton oldButton=actuatorButton(frame.getMachineControls().getJogControlsPanel(),valve);require(oldButton!=null,"actual JogControls actuator container found");
            oldButton.getParent().addContainerListener(new ContainerAdapter(){public void componentAdded(ContainerEvent e){auxiliaryEvents.add(map("surface","jog-actuator-container","event_dispatch_thread",SwingUtilities.isEventDispatchThread(),"thread",Thread.currentThread().getName(),"child",e.getChild() instanceof JButton?((JButton)e.getChild()).getText():e.getChild().getClass().getName()));}});
            return null;
        });
    }
    static void verifyTopologySwing(Nozzle created)throws Exception {
        edt(()->{ // EDT barrier also drains all ordered collection notifications from the finished native task.
            for(int i=0;i<selectors.size();i++)require(initialSelections.get(i).equals(selectors.get(i).getSelectedItem()),"existing wizard assignment retained "+i);
            require(assignmentChanges.isEmpty(),"adding devices never emits transient wizard assignment changes");
            require(selectors.get(0).getItemCount()==config.getMachine().getAxes().size()+1,"all axis model includes both new axes exactly once");
            require(selectors.get(1).getItemCount()==initialSizes.get(1)+1&&selectors.get(2).getItemCount()==initialSizes.get(2)+1,"filtered Z/rotation models include exactly one new axis");
            require(selectors.get(3).getItemCount()==initialSizes.get(3)+1&&selectors.get(4).getItemCount()==initialSizes.get(4)+1,"both actuator models include exactly one new valve");
            require(actuatorButton(frame.getMachineControls().getJogControlsPanel(),"GUI topology valve")!=null,"new valve has actual JogControls button");
            for(String surface:List.of("axis-all","axis-z","axis-rotation","head-actuators","combined-actuators","jog-actuator-container"))require(auxiliaryEvents.stream().anyMatch(e->surface.equals(e.get("surface"))),"actual event observed for "+surface);
            require(auxiliaryEvents.stream().allMatch(e->Boolean.TRUE.equals(e.get("event_dispatch_thread"))),"all selector and actuator button changes run on EDT");
            require(modelEvents.stream().allMatch(e->Boolean.TRUE.equals(e.get("event_dispatch_thread"))),"MachineControls events run on EDT");
            require(nativeEvents.size()==5&&nativeEvents.stream().allMatch(e->Boolean.FALSE.equals(e.get("event_dispatch_thread"))&&Boolean.TRUE.equals(e.get("native_machine_task"))),"five native collection events remain on owned native executor");
            return null;
        });
        proof.put("auxiliary_swing_events",auxiliaryEvents);proof.put("native_model_events",nativeEvents);proof.put("wizard_assignment_changes",assignmentChanges);proof.put("all_auxiliary_swing_events_on_edt",true);
    }
    static void require(boolean value,String message){assertions++;check(value,message);}
    static JComboBox<?> nozzleCombo(Component component,String originalId){
        if(component instanceof JComboBox){JComboBox<?> box=(JComboBox<?>)component;for(int i=0;i<box.getItemCount();i++){Object item=box.getItemAt(i);if(item instanceof HeadMountableItem&&((HeadMountableItem)item).getItem() instanceof Nozzle&&((HeadMountableItem)item).getItem().getId().equals(originalId))return box;}}
        if(component instanceof Container)for(Component child:((Container)component).getComponents()){JComboBox<?> found=nozzleCombo(child,originalId);if(found!=null)return found;}return null;
    }
    static List<String> comboNozzleIds(JComboBox<?> box){List<String> ids=new ArrayList<>();for(int i=0;i<box.getItemCount();i++){Object item=box.getItemAt(i);if(item instanceof HeadMountableItem&&((HeadMountableItem)item).getItem() instanceof Nozzle)ids.add(((HeadMountableItem)item).getItem().getId());}return ids;}
    static JsonObject change(JsonObject before){JsonObject old=before.getAsJsonObject("settings").getAsJsonArray("nozzles").get(0).getAsJsonObject();return object(
        "type","create_simulator_nozzle_assembly","head_id",old.get("head_id").getAsString(),"driver_id",before.getAsJsonArray("drivers").get(0).getAsJsonObject().get("id").getAsString(),"x_axis_id",old.get("x_axis_id").getAsString(),"y_axis_id",old.get("y_axis_id").getAsString(),
        "nozzle_name","GUI topology nozzle","tip_name","GUI topology tip","valve_name","GUI topology valve","head_offsets",object("x_mm",20,"y_mm",0,"z_mm",0),
        "z_axis",object("home_mm",0,"low_mm",-100,"high_mm",10,"safe_z_mm",0,"feedrate_mm_per_s",100,"acceleration_mm_per_s2",500,"jerk_mm_per_s3",1000),
        "rotation_axis",object("home_deg",0,"low_deg",-360,"high_deg",360,"feedrate_deg_per_s",500,"acceleration_deg_per_s2",1000,"jerk_deg_per_s3",10000),
        "tip",object("min_part_diameter_mm",0,"max_part_diameter_mm",20,"max_part_height_mm",10,"max_pick_tolerance_mm",0.5,"pick_dwell_ms",0,"place_dwell_ms",0),
        "pick_dwell_ms",0,"place_dwell_ms",0,"exclusive_package_ids",List.of("R0805"),"simulated_initial_tool_state","installed-on-new-nozzle");}
    static void recordModelEvent(ListDataEvent e){modelEvents.add(map("type",e.getType(),"index0",e.getIndex0(),"index1",e.getIndex1(),"event_dispatch_thread",SwingUtilities.isEventDispatchThread(),"thread",Thread.currentThread().getName()));}
    static void journalProof(Path root,JsonObject creation,JsonObject job,String oldId,String newId,String tipId)throws Exception{
        Path path=state.resolve("journal/operations.jsonl");List<JsonObject> rows=new ArrayList<>();for(String line:Files.readAllLines(path))if(!line.isBlank())rows.add(new JsonParser().parse(line).getAsJsonObject());String oid=job.get("operation_id").getAsString();Map<String,Integer> byNozzle=new LinkedHashMap<>();List<Object> complete=new ArrayList<>(),actions=new ArrayList<>();
        long preimage=-1,intent=-1,body=-1,outcome=-1,terminal=-1;String cid=creation.get("operation_id").getAsString();
        for(JsonObject row:rows){JsonObject p=row.getAsJsonObject("payload");String type=row.get("type").getAsString();if(p.has("operation_id")&&cid.equals(p.get("operation_id").getAsString())){long sequence=row.get("sequence").getAsLong();if(type.equals("topology_recovery_available"))preimage=sequence;if(type.equals("native_effect_intent"))intent=sequence;if(type.equals("topology_model_persisted"))body=sequence;if(type.equals("native_effect_outcome"))outcome=sequence;if(type.equals("operation")&&"succeeded".equals(p.get("state").getAsString()))terminal=sequence;}
            if(!p.has("operation_id")||!oid.equals(p.get("operation_id").getAsString()))continue;
            if(type.equals("native_placement_checkpoint")&&"native-placement-complete-hook".equals(p.get("state").getAsString())){JsonObject context=p.getAsJsonObject("context");String id=context.get("nozzle_id").getAsString();byNozzle.put(id,byNozzle.getOrDefault(id,0)+1);if(id.equals(newId))require(tipId.equals(context.get("nozzle_tip_id").getAsString()),"new native completion uses created tip");complete.add(JSON.fromJson(row,Map.class));}
            if(type.equals("native_action_outcome")&&"native_hook_returned".equals(p.get("state").getAsString()))actions.add(JSON.fromJson(row,Map.class));
        }
        require(preimage>0&&preimage<intent&&intent<body&&body<outcome&&outcome<terminal,"GUI assembly has ordered durable preimage/model/effect/wrapper lifecycle");require(complete.size()==32&&byNozzle.getOrDefault(oldId,0)>0&&byNozzle.getOrDefault(newId,0)>0,"both GUI native nozzles complete actual job placements");
        for(String id:List.of(oldId,newId))for(String kind:List.of("pick","release")){boolean found=false;for(Object raw:actions){Map<?,?> p=(Map<?,?>)((Map<?,?>)raw).get("payload");Map<?,?> ctx=(Map<?,?>)p.get("context");if(kind.equals(p.get("kind"))&&ctx!=null&&id.equals(ctx.get("nozzle_id")))found=true;}require(found,"actual native "+kind+" hook for "+id);}
        proof.put("journal",path.toString());proof.put("journal_sha256",sha(path));proof.put("native_complete_placements",complete.size());proof.put("by_nozzle",byNozzle);proof.put("complete_checkpoints",complete);proof.put("action_outcomes",actions);proof.put("assembly_lifecycle",map("preimage_sequence",preimage,"intent_sequence",intent,"body_sequence",body,"outcome_sequence",outcome,"terminal_sequence",terminal));
    }
    public static void main(String[] args)throws Exception{
        if(args.length!=4)throw new IllegalArgumentException("runtime-dir bridge-jar bootstrap-js new-test-root");Path runtime=Path.of(args[0]).toRealPath(),jar=Path.of(args[1]).toRealPath(),script=Path.of(args[2]).toRealPath(),root=Path.of(args[3]).toAbsolutePath();if(Files.exists(root))throw new IllegalArgumentException("New test root required");Files.createDirectories(root);
        Thread.setDefaultUncaughtExceptionHandler((thread,error)->{edtFailures.add(thread.getName()+": "+error.getClass().getName()+": "+error.getMessage());error.printStackTrace();});
        require("org.openpnp.codex.IsolatedPreferencesFactory".equals(System.getProperty("java.util.prefs.PreferencesFactory")),"isolated preferences required");try{Class.forName("org.openpnp.codex.Bridge",false,ClassLoader.getSystemClassLoader());throw new AssertionError("Bridge on application classpath");}catch(ClassNotFoundException expected){}
        int exit=0;JComboBox<?> combo=null;ListDataListener listener=null;
        try{
            Path configDir=root.resolve("config");Files.createDirectory(configDir);state=root.resolve("bridge-state");Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();config.getMachine().setProperty("Welcome2_0_Dialog_Shown",true);config.save();config.getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();frame=edt(()->{MainFrame value=new MainFrame(config);value.setVisible(true);return value;});accelerate();
            bootstrap=config.getScripting().getScriptsDirectory().toPath().resolve("codex-bootstrap.js");Files.copy(script,bootstrap);properties(runtime,jar,bootstrap,state);config.getScripting().execute(bootstrap.toFile());connect();JsonObject caps=rpc("openpnp_get_capabilities",object());require(caps.get("simulator_profile").getAsString().equals("gui-simulator"),"actual GUI attachment profile");require(caps.getAsJsonArray("configuration_changes").toString().contains("create_simulator_nozzle_assembly"),"GUI profile advertises topology creation");
            expect("LOCAL_GRANT_REQUIRED","openpnp_request_control_session",object("request_id","gui-topology-before-grant","ttl_seconds",600));click("Allow Codex control");awaitOwnership(true);session=rpc("openpnp_request_control_session",object("request_id","gui-topology-grant","ttl_seconds",600)).get("session_id").getAsString();JsonObject before=rpc("openpnp_get_configuration",object());require(!before.get("enabled").getAsBoolean(),"creation admitted only with disabled native machine");ReferenceNozzle old=(ReferenceNozzle)config.getMachine().getDefaultHead().getDefaultNozzle();String oldId=old.getId();combo=edt(()->nozzleCombo(frame.getMachineControls(),oldId));require(combo!=null&&edt(()->comboNozzleIds(nozzleCombo(frame.getMachineControls(),oldId))).equals(List.of(oldId)),"actual GUI nozzle selector initially has original nozzle");
            observeTopology(old);
            listener=new ListDataListener(){public void intervalAdded(ListDataEvent e){recordModelEvent(e);}public void intervalRemoved(ListDataEvent e){recordModelEvent(e);}public void contentsChanged(ListDataEvent e){recordModelEvent(e);}};JComboBox<?> observed=combo;ListDataListener observedListener=listener;edt(()->{observed.getModel().addListDataListener(observedListener);return null;});
            JsonArray changes=new JsonArray();changes.add(change(before));JsonObject plan=rpc("openpnp_plan_configuration",object("session_id",session,"expected_config_revision",before.get("config_revision").getAsString(),"changes",changes));require(config.getMachine().getDefaultHead().getNozzles().size()==1,"planning does not insert native objects");JsonObject request=mutation("plan_id",plan.get("plan_id").getAsString());JsonObject creation=run("openpnp_apply_configuration",request);JsonObject duplicate=rpc("openpnp_apply_configuration",request);require(creation.get("operation_id").equals(duplicate.get("operation_id")),"same GUI request is deduplicated");JsonObject ids=creation.getAsJsonObject("result").getAsJsonObject("created_assembly");String newId=ids.get("nozzle_id").getAsString(),tipId=ids.get("nozzle_tip_id").getAsString();Nozzle created=config.getMachine().getDefaultHead().getNozzle(newId);require(created!=null&&config.getMachine().getDefaultHead().getNozzles().get(0)==old&&created.getNozzleTip().getId().equals(tipId),"created native nozzle/tip and original identity attached");
            waitUntil(()->edt(()->comboNozzleIds(observed)).contains(newId),10000,"Swing nozzle-list native update");require(edt(()->comboNozzleIds(observed)).equals(List.of(oldId,newId)),"GUI nozzle selector refreshes exactly once");require(!config.getMachine().isEnabled()&&!config.getMachine().isHomed(),"creation invalidates homing and remains disabled");require(!modelEvents.isEmpty(),"actual Swing model emitted update events");verifyTopologySwing(created);proof.put("creation",JSON.fromJson(creation,Map.class));proof.put("gui_nozzle_ids_after_creation",edt(()->comboNozzleIds(observed)));proof.put("swing_model_events",new ArrayList<>(modelEvents));proof.put("all_swing_model_events_on_edt",modelEvents.stream().allMatch(e->Boolean.TRUE.equals(e.get("event_dispatch_thread"))));
            run("openpnp_set_machine_enabled",mutation("enabled",true));run("openpnp_home_machine",mutation());JsonObject prepared=run("openpnp_prepare_job",mutation("sample","pnp-test")).getAsJsonObject("result");require(edt(()->frame.getJobTab().getJob().getBoardLocations().size())>0,"native job appears in real GUI JobPanel");run("openpnp_validate_job",mutation());JsonObject done=run("openpnp_start_job",mutation("job_id",prepared.get("job_id").getAsString()));require(done.getAsJsonObject("result").get("placed").getAsInt()==32,"real GUI-owned native mixed job completes");run("openpnp_set_machine_enabled",mutation("enabled",false));journalProof(root,creation,done,oldId,newId,tipId);rpc("openpnp_release_control_session",object("session_id",session));awaitOwnership(false);
            edt(()->{frame.getMachineControls().setSelectedTool(created);return null;});require(edt(()->frame.getMachineControls().getSelectedTool())==created,"new nozzle can be selected in real GUI component after local release");click("Disconnect bridge");waitUntil(()->edt(()->frame.getRootPane().getClientProperty(KEY))==null,30000,"GUI bridge detach");require(edtFailures.isEmpty(),"no uncaught GUI/native event error");proof.put("functional_checks_passed",true);
        }catch(Throwable failure){failure.printStackTrace();proof.put("functional_checks_passed",false);proof.put("failure",failure.getClass().getName()+": "+failure.getMessage());exit=1;}finally{proof.put("assertions",assertions);proof.put("uncaught_errors",edtFailures);proof.put("desktop_gestures_performed",false);proof.put("native_gui_component_api",true);proof.put("physical_qualification",false);proof.put("bridge_on_application_classpath",false);proof.put("bridge_sha256",sha(jar));proof.put("runtime_manifest_sha256",sha(runtime.resolve("codex-build-manifest.json")));Files.writeString(root.resolve("topology-gui-proof.json"),JSON.toJson(proof));try{if(config!=null)config.getMachine().close();}catch(Exception ignored){}edt(()->{for(Window w:Window.getWindows())w.dispose();return null;});}
        System.out.println("OPENPNP_GUI_TOPOLOGY_RESULT "+JSON.toJson(map("exit_code",exit,"functional_checks_passed",proof.get("functional_checks_passed"),"assertions",assertions,"native_complete_placements",proof.get("native_complete_placements"),"all_swing_model_events_on_edt",proof.get("all_swing_model_events_on_edt"),"proof",root.resolve("topology-gui-proof.json").toString(),"desktop_gestures_performed",false)));System.exit(exit);
    }
}
