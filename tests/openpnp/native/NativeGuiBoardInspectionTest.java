/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.Component;
import java.awt.Container;
import java.awt.Dialog;
import java.awt.Window;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import javax.swing.*;
import org.openpnp.gui.MainFrame;
import org.openpnp.model.*;
import org.openpnp.spi.Machine;
import org.openpnp.spi.MachineListener;
import static org.openpnp.codex.NativeGuiOwnershipTest.*;
import static org.openpnp.codex.NativeGuiPanelMembershipTest.bound;
import static org.openpnp.codex.NativeGuiPanelMembershipTest.canonical;
import static org.openpnp.codex.NativeGuiPanelMembershipTest.command;

/** Actual bootstrap, GUI owner, HTTP, native job and local form. Programmatic Swing inputs only. */
public final class NativeGuiBoardInspectionTest {
    static final List<String> checks=new CopyOnWriteArrayList<>(),errors=new CopyOnWriteArrayList<>();
    static final Map<String,Object> proof=new LinkedHashMap<>();
    static final String FORM="org.openpnp.codex.NativeInspectionDialog";
    static final List<WrapperGate> gates=new ArrayList<>();
    static void require(boolean value,String label){check(value,label);checks.add(label);}
    static <T>T boundedEdt(Callable<T> action)throws Exception{FutureTask<T> task=new FutureTask<>(action);SwingUtilities.invokeLater(task);return task.get(2,TimeUnit.SECONDS);}
    static JsonObject status()throws Exception{return rpc("openpnp_get_status",object());}
    static JsonObject inspection(String id)throws Exception{return rpc("openpnp_get_board_inspection",object("task_id",id));}
    static Component named(Component component,String name){if(name.equals(component.getName()))return component;if(component instanceof Container)for(Component child:((Container)component).getComponents()){Component match=named(child,name);if(match!=null)return match;}return null;}
    static Window currentForm()throws Exception{return edt(()->{Window found=null;for(Window window:Window.getWindows())if(FORM.equals(window.getClass().getName())&&window.isShowing()){if(found!=null)throw new AssertionError("Multiple visible inspection forms");found=window;}return found;});}
    static Window awaitForm()throws Exception{waitUntil(()->currentForm()!=null,10000,"local inspection form presentation");return currentForm();}
    static void awaitNoForm()throws Exception{waitUntil(()->currentForm()==null,10000,"local inspection form disposal");}
    static JsonObject terminalInspection(String task,String state)throws Exception{
        AtomicReference<JsonObject> last=new AtomicReference<>();
        waitUntil(()->{JsonObject view=inspection(task);last.set(view);return view.has("submission_operation")&&view.getAsJsonObject("submission_operation").get("state").getAsString().equals(state)&&!view.get("task_active").getAsBoolean();},30000,"inspection terminal "+state);
        waitUntil(()->{JsonElement pending=status().get("native_submission");return !config.getMachine().isBusy()&&(pending==null||pending.isJsonNull());},10000,"inspection native completion drain");return last.get();
    }
    static String selectedLoad()throws Exception{
        JsonObject loads=rpc("openpnp_get_board_loads",object());
        for(JsonElement raw:loads.getAsJsonArray("roots"))for(JsonElement board:raw.getAsJsonObject().getAsJsonArray("boards"))if("P1⇒A".equals(board.getAsJsonObject().get("board_instance_id").getAsString()))return board.getAsJsonObject().get("loaded_board_id").getAsString();
        throw new AssertionError("Selected native loaded board missing");
    }
    static JsonObject request(String loaded)throws Exception{return bound("loaded_board_id",loaded);}
    static String requestAndPresent(String loaded)throws Exception{
        JsonObject operation=run("openpnp_request_board_inspection",request(loaded));String task=operation.getAsJsonObject("result").get("task_id").getAsString();
        awaitForm();require(inspection(task).get("task_active").getAsBoolean(),"newly presented task has live local authority");return task;
    }
    static String requestViaPackagedMcp(String loaded,Path root,Path jar)throws Exception{
        Path node=Path.of(System.getProperty("openpnp.codex.test.node")).toRealPath(),helper=Path.of(System.getProperty("openpnp.codex.test.mcpHelper")).toRealPath(),server=Path.of(System.getProperty("openpnp.codex.test.mcpServer")).toRealPath();
        Path arguments=root.resolve("inspection-mcp-arguments.json"),output=root.resolve("inspection-mcp-proof.json");
        Files.createFile(arguments,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
        Files.writeString(arguments,JSON.toJson(object("connection_file",state.resolve("connection.json").toString(),"output_file",output.toString(),"mcp_state",root.resolve("inspection-mcp-state").toString(),"request",request(loaded),"bridge_sha256",sha(jar),"mcp_server_sha256",sha(server))));
        ProcessBuilder builder=new ProcessBuilder(node.toString(),helper.toString(),arguments.toString());builder.directory(root.toFile());builder.redirectErrorStream(true).redirectOutput(root.resolve("inspection-mcp.log").toFile());
        for(String key:List.of("NODE_OPTIONS","NODE_PATH","OPENPNP_CONNECTION_FILE","OPENPNP_STATE_DIR"))builder.environment().remove(key);
        Process child=builder.start();boolean forced=false,completed=false;List<ProcessHandle> descendants=new ArrayList<>();
        try{completed=child.waitFor(30,TimeUnit.SECONDS);}finally{
            if(child.isAlive()){
                forced=true;child.descendants().forEach(descendants::add);for(ProcessHandle process:descendants)process.destroy();child.destroy();child.waitFor(2,TimeUnit.SECONDS);
                for(ProcessHandle process:descendants)if(process.isAlive())process.destroyForcibly();if(child.isAlive())child.destroyForcibly();child.waitFor(3,TimeUnit.SECONDS);
            }
            proof.put("mcp_child",map("pid",child.pid(),"exited",!child.isAlive(),"forced_cleanup",forced,"captured_descendants_alive",descendants.stream().filter(ProcessHandle::isAlive).count()));
        }
        require(completed&&!forced&&!child.isAlive()&&child.exitValue()==0,"bounded official SDK helper exits successfully and is reaped");
        JsonObject mcp=new JsonParser().parse(Files.readString(output)).getAsJsonObject();
        require(mcp.get("passed").getAsBoolean()&&mcp.get("server_pid_gone").getAsBoolean(),"packaged MCP request/read and owned server cleanup pass");
        require(mcp.get("mcp_server_sha256").getAsString().equals(sha(server))&&mcp.get("helper_sha256").getAsString().equals(sha(helper)),"MCP evidence binds unchanged exact packaged server and helper bytes");
        proof.put("mcp_proof_sha256",sha(output));proof.put("mcp",JSON.fromJson(mcp,Map.class));
        String task=mcp.getAsJsonObject("task").get("task_id").getAsString();awaitForm();
        require(inspection(task).get("task_active").getAsBoolean(),"MCP-created task presents the same active local GUI form");return task;
    }
    static void fill(Window form)throws Exception{edt(()->{
        ((JTextField)named(form,"inspection.operator_label")).setText("GUI integration fixture");
        ((JTextArea)named(form,"inspection.operator_note")).setText("Synthetic programmatic observations for this simulator test. No person or instrument was authenticated.");
        ((JTextField)named(form,"inspection.xy_tolerance")).setText("0.2");((JTextField)named(form,"inspection.rotation_tolerance")).setText("2");
        JTable table=(JTable)named(form,"inspection.observations");require(table.getRowCount()==2,"one selected board has exactly two form rows");
        require("R1".equals(table.getValueAt(0,0))&&"R2".equals(table.getValueAt(1,0)),"form displays exact required native placement IDs");
        for(int i=0;i<table.getRowCount();i++){
            require("unknown".equals(table.getValueAt(i,3))&&"unknown".equals(table.getValueAt(i,4)),"native completion does not infer inspection observations");
            table.setValueAt("present",i,3);table.setValueAt("correct",i,4);
            table.setValueAt("0.0500000000000000000001",i,5);table.setValueAt("0",i,6);table.setValueAt("0.1",i,7);table.setValueAt("0.01",i,8);table.setValueAt("0.1",i,9);
        }
        return null;
    });}
    static void addReference(Window form,String id)throws Exception{edt(()->{
        findButton(form,"Add evidence reference").doClick();JTable table=(JTable)named(form,"inspection.artifacts");int row=table.getRowCount()-1;
        table.setValueAt(id,row,0);table.setValueAt("0".repeat(64),row,1);table.setValueAt("image",row,2);return null;
    });}
    static void submit(Window form)throws Exception{boundedEdt(()->{JButton button=(JButton)named(form,"inspection.submit");require(button.isEnabled(),"local form submit is enabled");button.doClick();return null;});}
    static List<JsonObject> journal()throws Exception{List<JsonObject> rows=new ArrayList<>();for(String line:Files.readAllLines(state.resolve("journal/operations.jsonl")))rows.add(new JsonParser().parse(line).getAsJsonObject());return rows;}
    static long taskEvents(String task,String type)throws Exception{return journal().stream().filter(row->type.equals(row.get("type").getAsString())&&row.getAsJsonObject("payload").has("task_id")&&task.equals(row.getAsJsonObject("payload").get("task_id").getAsString())).count();}
    static Map<String,String> modelFiles()throws Exception{Map<String,String> out=new TreeMap<>();for(String name:List.of("machine.xml","parts.xml","packages.xml"))out.put(name,sha(config.getConfigurationDirectory().toPath().resolve(name)));return out;}
    static void nativeEffects(String operation)throws Exception{
        Set<String> complete=new TreeSet<>(),actionIds=new HashSet<>();Map<String,JsonObject> active=new HashMap<>();Map<String,Integer> kinds=new TreeMap<>();int returns=0;
        for(JsonObject row:journal()){
            String type=row.get("type").getAsString();JsonObject p=row.getAsJsonObject("payload");
            if(type.equals("native_action_intent")||type.equals("native_action_outcome")||type.equals("native_placement_checkpoint")){
                require(operation.equals(p.get("operation_id").getAsString()),"inspection never creates a native placement or action record");
                if(type.equals("native_action_intent")){String id=p.get("action_id").getAsString();require(actionIds.add(id)&&active.put(id,row)==null,"actual native action identity is unique");String kind=p.get("kind").getAsString();kinds.put(kind,kinds.getOrDefault(kind,0)+1);}
                else if(type.equals("native_action_outcome")){JsonObject start=active.remove(p.get("action_id").getAsString());require(start!=null&&start.get("sequence").getAsLong()<row.get("sequence").getAsLong()&&"native_hook_returned".equals(p.get("state").getAsString()),"actual native action has ordered matching return");returns++;}
                else if("native-placement-complete-hook".equals(p.get("state").getAsString())){JsonObject c=p.getAsJsonObject("context");require(c.get("native_placed_status").getAsBoolean(),"native Complete hook reports actual placed history");require(complete.add(c.get("board_instance_id").getAsString()+"⇒"+c.get("placement_id").getAsString()),"native Complete identity unique");}
            }
        }
        Set<String> wanted=new TreeSet<>();for(String board:List.of("P1⇒A","P1⇒B","P2⇒A","P2⇒B"))for(String placement:List.of("R1","R2"))wanted.add(board+"⇒"+placement);
        require(complete.equals(wanted)&&active.isEmpty()&&returns==32,"exact eight native completions and 32 matched native actions");for(String kind:List.of("feed","pick","align","release"))require(kinds.getOrDefault(kind,0)==8,"eight actual native "+kind+" actions");
        proof.put("native_complete_placements",8);proof.put("native_action_pairs",32);proof.put("complete_identities",complete);
    }
    static final class WrapperGate extends MachineListener.Adapter implements AutoCloseable {
        final boolean holdBusy;final CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);final AtomicBoolean once=new AtomicBoolean(),held=new AtomicBoolean();
        boolean detached;
        WrapperGate(boolean holdBusy){this.holdBusy=holdBusy;config.getMachine().addListener(this);gates.add(this);}
        @Override public void machineBusy(Machine machine,boolean busy){if(busy==holdBusy&&once.compareAndSet(false,true)){
            held.set(true);require(!SwingUtilities.isEventDispatchThread()&&machine.isTask(Thread.currentThread()),"bounded gate runs on actual native wrapper thread");entered.countDown();
            try{if(!release.await(8,TimeUnit.SECONDS))throw new IllegalStateException("Inspection test wrapper gate expired");}catch(InterruptedException interrupted){Thread.currentThread().interrupt();throw new IllegalStateException(interrupted);}finally{held.set(false);}
        }}
        @Override public void close(){release.countDown();}
        void detach(){require(!config.getMachine().isBusy(),"test wrapper listener detaches only after native iteration has drained");config.getMachine().removeListener(this);detached=true;}
    }
    public static void main(String[]args)throws Exception{
        if(args.length!=4)throw new IllegalArgumentException("runtime bridge bootstrap new-root");Path runtime=Path.of(args[0]).toRealPath(),jar=Path.of(args[1]).toRealPath(),script=Path.of(args[2]).toRealPath(),root=Path.of(args[3]).toAbsolutePath();if(Files.exists(root))throw new IllegalArgumentException("New test root required");Files.createDirectories(root);
        Throwable failure=null;boolean machineClosed=false,windowsClosed=false,detached=false;
        Thread.setDefaultUncaughtExceptionHandler((thread,error)->{errors.add(thread.getName()+": "+error);error.printStackTrace();});
        try{
            require("org.openpnp.codex.IsolatedPreferencesFactory".equals(System.getProperty("java.util.prefs.PreferencesFactory")),"explicit isolated preferences factory");require(java.util.prefs.Preferences.userRoot().getClass().getName().endsWith("IsolatedPreferencesFactory$MemoryNode"),"actual nonpersistent preferences");
            try{Class.forName("org.openpnp.codex.Bridge",false,ClassLoader.getSystemClassLoader());throw new AssertionError("Bridge on application classpath");}catch(ClassNotFoundException expected){}
            Path dir=root.resolve("config");Files.createDirectory(dir);state=root.resolve("bridge-state");Configuration.initialize(dir.toFile());config=Configuration.get();config.load();config.getMachine().setProperty("Welcome2_0_Dialog_Shown",true);config.save();config.getMachine().close();Configuration.initialize(dir.toFile());config=Configuration.get();
            frame=edt(()->{MainFrame f=new MainFrame(config);f.setVisible(true);return f;});accelerate();bootstrap=config.getScripting().getScriptsDirectory().toPath().resolve("codex-bootstrap.js");Files.copy(script,bootstrap);properties(runtime,jar,bootstrap,state);config.getScripting().execute(bootstrap.toFile());connect();
            JsonObject caps=rpc("openpnp_get_capabilities",object());require(caps.get("bridge_artifact_sha256").getAsString().equals(sha(jar)),"actual native bootstrap attached selected packaged Bridge");require(caps.getAsJsonObject("loaded_board_inspection").get("request_available").getAsBoolean(),"actual GUI controller advertises inspection form");
            require("native-loaded-board-inspection-v1".equals(caps.getAsJsonObject("loaded_board_inspection").get("profile").getAsString()),"exact native inspection profile advertised");
            expect("UNSUPPORTED_OPERATION","local_native_inspection_submission",object());expect("UNSUPPORTED_OPERATION","openpnp_submit_board_inspection",object());
            click("Allow Codex control");awaitOwnership(true);session=rpc("openpnp_request_control_session",object("request_id",UUID.randomUUID().toString(),"ttl_seconds",600)).get("session_id").getAsString();
            Part part=config.getPart("R0805-1K");require(part!=null,"native fixture part available");JsonObject input=canonical(part);Files.writeString(root.resolve("canonical-job.json"),JSON.toJson(input));run("openpnp_prepare_job",command("canonical_job",input));
            run("openpnp_set_machine_enabled",command("enabled",true));run("openpnp_home_machine",command());run("openpnp_validate_job",command());JsonObject done=run("openpnp_start_job",command("job_id",status().get("job_id").getAsString()));
            require(done.getAsJsonObject("result").get("placed").getAsInt()==8,"actual GUI-owned native job completes eight placements");run("openpnp_set_machine_enabled",command("enabled",false));
            Job job=edt(()->frame.getJobTab().getJob());Map<String,Boolean> history=new TreeMap<>(job.getPlacedStatusSnapshot());Map<String,String> files=modelFiles();String loaded=selectedLoad();
            JsonObject params=request(loaded),handle;
            WrapperGate requestGate=new WrapperGate(false);
            try(WrapperGate gate=requestGate){
                handle=rpc("openpnp_request_board_inspection",params);require(gate.entered.await(3,TimeUnit.SECONDS),"request reaches actual native post-body wrapper gate");
                JsonObject held=status();require(gate.held.get()&&held.getAsJsonObject("native_submission").get("body_exited").getAsBoolean(),"request body exited but wrapper remains held");
                require(currentForm()==null,"no inspection form before native Future terminal publication");
                JsonObject still=rpc("openpnp_get_operation",object("operation_id",handle.get("operation_id").getAsString()));require(!"succeeded".equals(still.get("state").getAsString()),"request is not published successful while native wrapper is held");
                require(boundedEdt(()->frame.isShowing()),"EDT stays responsive while inspection request wrapper is held");
            }
            JsonObject requestDone=run("openpnp_request_board_inspection",params);requestGate.detach();require(requestDone.get("operation_id").equals(handle.get("operation_id")),"request replay retains original operation");String first=requestDone.getAsJsonObject("result").get("task_id").getAsString();Window form=awaitForm();
            require(edt(()->form instanceof JDialog&&((JDialog)form).getModalityType()==Dialog.ModalityType.MODELESS&&form.getOwner() instanceof JDialog&&((JDialog)form.getOwner()).getModalityType()==Dialog.ModalityType.APPLICATION_MODAL),"actual inspector is modeless child of modal ownership dialog");
            JsonObject task=inspection(first);require(task.get("task_active").getAsBoolean(),"first displayed inspection is active");require(task.getAsJsonObject("pending").getAsJsonObject("snapshot").get("loaded_board_id").getAsString().equals(loaded),"displayed task binds exact native loaded board");
            expect("INSPECTION_PENDING","openpnp_request_board_inspection",request(loaded));require(currentForm()==form,"duplicate request retains exactly one existing view");
            fill(form);String duplicate=UUID.randomUUID().toString();addReference(form,duplicate);addReference(form,duplicate);submit(form);
            waitUntil(()->edt(()->((JButton)named(form,"inspection.submit")).isEnabled()&&((JTextArea)named(form,"inspection.message")).getText().contains("uniqueness")),10000,"correctable native model field refusal");
            JsonObject correctable=inspection(first);require(correctable.get("task_active").getAsBoolean()&&!correctable.has("submission_operation"),"invalid form does not consume local capability");require(currentForm()==form,"correctable asynchronous failure retains same local view");
            edt(()->{JTable refs=(JTable)named(form,"inspection.artifacts");while(refs.getRowCount()>0){refs.setRowSelectionInterval(0,0);findButton(form,"Remove reference").doClick();}return null;});submit(form);
            JsonObject firstDone=terminalInspection(first,"succeeded");awaitNoForm();require(firstDone.getAsJsonObject("submission_operation").getAsJsonObject("native_completion").get("native_wrapper_succeeded").getAsBoolean(),"local receipt operation has successful actual native wrapper");
            JsonObject receipt=firstDone.getAsJsonObject("receipt"),result=receipt.getAsJsonObject("result");require("passed".equals(result.get("outcome").getAsString())&&result.get("passed_count").getAsInt()==2,"explicit synthetic local observations produce two reported passes");
            require(!result.get("hardware_qualified").getAsBoolean()&&!result.get("production_authority_granted").getAsBoolean()&&!result.get("instrument_authenticity_verified").getAsBoolean(),"reported inspection grants no physical or instrument authority");
            String artifact=receipt.getAsJsonObject("artifact").get("artifact_id").getAsString();JsonObject retained=rpc("openpnp_get_artifact",object("artifact_id",artifact));byte[] bytes=Base64.getDecoder().decode(retained.get("base64").getAsString());JsonObject document=new JsonParser().parse(new String(bytes,StandardCharsets.UTF_8)).getAsJsonObject();
            require(document.getAsJsonObject("submission").getAsJsonArray("records").get(0).getAsJsonObject().get("dx_mm").getAsBigDecimal().compareTo(new BigDecimal("0.0500000000000000000001"))==0,"local GUI decimal survives native durable receipt exactly");
            require(taskEvents(first,"inspection_submission_intent")==1&&taskEvents(first,"inspection_receipt")==1&&taskEvents(first,"inspection_task_closed")==0,"successful one-time task has exactly one intent and receipt, no cancellation closure");
            proof.put("successful_task",JSON.fromJson(firstDone,Map.class));proof.put("receipt_artifact_sha256",retained.get("sha256").getAsString());

            String failed=requestViaPackagedMcp(loaded,root,jar);Window failedForm=currentForm();fill(failedForm);addReference(failedForm,UUID.randomUUID().toString());submit(failedForm);JsonObject failedDone=terminalInspection(failed,"failed");awaitNoForm();
            require("INSPECTION_ARTIFACT".equals(failedDone.getAsJsonObject("submission_operation").getAsJsonObject("result").get("code").getAsString()),"missing owned artifact fails after local capability consumption");
            require(taskEvents(failed,"inspection_submission_intent")==0&&taskEvents(failed,"inspection_receipt")==0&&taskEvents(failed,"inspection_task_closed")==1,"failed pre-intent submission retains one closure and no invented receipt");proof.put("consumed_failure_task",JSON.fromJson(failedDone,Map.class));
            String revoked=requestAndPresent(loaded);Window revokedForm=currentForm();require(revokedForm!=failedForm,"fresh request displays after consumed failure disposal");fill(revokedForm);
            WrapperGate takeoverGate=new WrapperGate(true);
            try(WrapperGate gate=takeoverGate){
                submit(revokedForm);require(gate.entered.await(3,TimeUnit.SECONDS),"local submission reaches actual native pre-body wrapper gate");
                long start=System.nanoTime();boundedEdt(()->{click("Take local control");return null;});proof.put("takeover_edt_elapsed_ms",(System.nanoTime()-start)/1e6);
                require(gate.held.get(),"actual takeover button returns while native wrapper remains held");require(boundedEdt(()->frame.isShowing()),"EDT remains responsive after immediate local inspection revocation");
                awaitNoForm();require(gate.held.get(),"revoked inspection closes before native wrapper releases");
            }
            JsonObject revokedDone=terminalInspection(revoked,"failed");awaitOwnership(false);takeoverGate.detach();
            require("INSPECTION_AUTHORITY_STALE".equals(revokedDone.getAsJsonObject("submission_operation").getAsJsonObject("result").get("code").getAsString()),"revoked form is rejected before native inspection intent");
            require(taskEvents(revoked,"inspection_submission_intent")==0&&taskEvents(revoked,"inspection_receipt")==0&&taskEvents(revoked,"inspection_task_closed")==1,"takeover consumes no receipt and closes pending history once");
            expect("LOCAL_GRANT_REQUIRED","openpnp_request_board_inspection",request(loaded));proof.put("revoked_task",JSON.fromJson(revokedDone,Map.class));
            require(edt(()->frame.getJobTab().getJob())==job&&job.getPlacedStatusSnapshot().equals(history),"inspection and takeover preserve exact native Job identity and complete placed history");require(modelFiles().equals(files),"inspection preserves machine, parts and packages file bytes");require(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"inspection ends disabled and idle");nativeEffects(done.get("operation_id").getAsString());
            click("Disconnect bridge");waitUntil(()->edt(()->frame.getRootPane().getClientProperty(KEY))==null,30000,"actual GUI bridge detaches after known inspection outcomes");detached=true;require(errors.isEmpty(),"no uncaught native or EDT errors");proof.put("journal_sha256",sha(state.resolve("journal/operations.jsonl")));
        }catch(Throwable thrown){failure=thrown;thrown.printStackTrace();}finally{
            for(WrapperGate gate:gates)gate.close();
            try{if(config!=null){config.getMachine().close();machineClosed=true;}}catch(Throwable thrown){errors.add("machine close: "+thrown);}
            try{edt(()->{for(Window window:Window.getWindows())window.dispose();return null;});windowsClosed=edt(()->Arrays.stream(Window.getWindows()).noneMatch(Window::isDisplayable));}catch(Throwable thrown){errors.add("window disposal: "+thrown);}
        }
        boolean ok=failure==null&&errors.isEmpty()&&machineClosed&&windowsClosed&&detached;
        proof.putAll(map("functional_checks_passed",ok,"assertions",checks.size(),"checks",checks,"errors",errors,"failure",failure==null?null:failure.toString(),"machine_closed",machineClosed,"all_owned_windows_disposed",windowsClosed,"bridge_detached",detached,"bridge_sha256",sha(jar),"runtime_manifest_sha256",sha(runtime.resolve("codex-build-manifest.json")),"bridge_on_application_classpath",false,"native_gui_component_api",true,"desktop_gestures_performed",false,"observations","synthetic programmatic local form input","authenticated_human_or_instrument",false,"physical_qualification",false,"wrapper_listener_test_only",true,"hold_limit_seconds",8,"edt_probe_timeout_seconds",2,"wrapper_test_listeners_detached",gates.stream().allMatch(gate->gate.detached)));
        Files.writeString(root.resolve("board-inspection-gui-proof.json"),JSON.toJson(proof));System.out.println("OPENPNP_GUI_BOARD_INSPECTION_RESULT "+JSON.toJson(map("passed",ok,"assertions",checks.size(),"native_complete_placements",proof.get("native_complete_placements"),"native_action_pairs",proof.get("native_action_pairs"),"desktop_gestures_performed",false)));System.exit(ok?0:1);
    }
}
