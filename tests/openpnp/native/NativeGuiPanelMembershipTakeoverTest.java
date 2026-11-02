/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.Window;
import java.beans.PropertyChangeListener;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import javax.swing.*;
import org.openpnp.gui.MainFrame;
import org.openpnp.model.*;
import static org.openpnp.codex.NativeGuiOwnershipTest.*;
import static org.openpnp.codex.NativeGuiPanelMembershipTest.bound;
import static org.openpnp.codex.NativeGuiPanelMembershipTest.canonical;
import static org.openpnp.codex.NativeGuiPanelMembershipTest.cloneChild;
import static org.openpnp.codex.NativeGuiPanelMembershipTest.command;
import static org.openpnp.codex.NativeGuiPanelMembershipTest.array;

/** Separate actual attached GUI child: hold one native publication, press local takeover, observe once. */
public final class NativeGuiPanelMembershipTakeoverTest {
    static final List<String> checks=new CopyOnWriteArrayList<>();
    static final List<String> errors=new CopyOnWriteArrayList<>();
    static final Map<String,Object> proof=new LinkedHashMap<>();
    static Path root;static CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);static AtomicBoolean held=new AtomicBoolean(),once=new AtomicBoolean();
    static void require(boolean value,String label){check(value,label);checks.add(label);}
    static <T>T boundedEdt(Callable<T> action)throws Exception{FutureTask<T> task=new FutureTask<>(action);SwingUtilities.invokeLater(task);return task.get(2,TimeUnit.SECONDS);}
    static void write(Path jar,Path runtime)throws Exception{proof.putAll(map("assertions",checks.size(),"checks",checks,"uncaught_errors",errors,"bridge_sha256",sha(jar),"runtime_manifest_sha256",sha(runtime.resolve("codex-build-manifest.json")),"simulation_only",true,"physical_qualification",false,"desktop_gestures_performed",false,"bridge_on_application_classpath",false,"publication_listener_test_only",true,"hold_limit_seconds",8,"edt_probe_timeout_seconds",2,"native_job_started",false,"native_actions_replayed",false));Files.writeString(root.resolve("panel-membership-takeover-proof.json"),JSON.toJson(proof));}
    public static void main(String[]args)throws Exception{
        if(args.length!=4)throw new IllegalArgumentException("runtime bridge bootstrap new-root");Path runtime=Path.of(args[0]).toRealPath(),jar=Path.of(args[1]).toRealPath(),script=Path.of(args[2]).toRealPath();root=Path.of(args[3]).toAbsolutePath();if(Files.exists(root))throw new IllegalArgumentException("new root required");Files.createDirectories(root);Throwable failure=null;boolean machineClosed=false,windowsClosed=false,detached=false;PropertyChangeListener gate=null;Job job=null;
        Thread.setDefaultUncaughtExceptionHandler((t,e)->{errors.add(t.getName()+": "+e);e.printStackTrace();});
        try{
            require("org.openpnp.codex.IsolatedPreferencesFactory".equals(System.getProperty("java.util.prefs.PreferencesFactory")),"explicit isolated preferences");try{Class.forName("org.openpnp.codex.Bridge",false,ClassLoader.getSystemClassLoader());throw new AssertionError("Bridge on app classpath");}catch(ClassNotFoundException expected){}
            Path dir=root.resolve("config");Files.createDirectory(dir);state=root.resolve("bridge-state");Configuration.initialize(dir.toFile());config=Configuration.get();config.load();config.getMachine().setProperty("Welcome2_0_Dialog_Shown",true);config.save();config.getMachine().close();Configuration.initialize(dir.toFile());config=Configuration.get();frame=edt(()->{MainFrame f=new MainFrame(config);f.setVisible(true);return f;});accelerate();bootstrap=config.getScripting().getScriptsDirectory().toPath().resolve("codex-bootstrap.js");Files.copy(script,bootstrap);properties(runtime,jar,bootstrap,state);config.getScripting().execute(bootstrap.toFile());connect();JsonObject caps=rpc("openpnp_get_capabilities",object());require(caps.get("bridge_artifact_sha256").getAsString().equals(sha(jar)),"actual bootstrap attached exact selected Bridge");require(caps.getAsJsonObject("panel_board_membership").get("profile").getAsString().equals("panel-board-membership-v1"),"actual GUI panel profile");
            click("Allow Codex control");awaitOwnership(true);session=rpc("openpnp_request_control_session",object("request_id",UUID.randomUUID().toString(),"ttl_seconds",600)).get("session_id").getAsString();run("openpnp_prepare_job",command("canonical_job",canonical(config.getPart("R0805-1K"))));job=edt(()->frame.getJobTab().getJob());JsonObject plan=run("openpnp_plan_placement_structure",bound("changes",array(cloneChild()))).getAsJsonObject("result");
            gate=e->{if(once.compareAndSet(false,true)){held.set(true);require(config.getMachine().isTask(Thread.currentThread())&&!SwingUtilities.isEventDispatchThread(),"real native executor is held inside original inline-root publication listener");entered.countDown();try{if(!release.await(8,TimeUnit.SECONDS))throw new IllegalStateException("bounded publication listener gate expired");}catch(InterruptedException interrupted){Thread.currentThread().interrupt();throw new IllegalStateException(interrupted);}finally{held.set(false);}}};
            job.getRootPanelLocation().getPanel().addPropertyChangeListener("children",gate);JsonObject apply=bound("plan_id",plan.get("plan_id").getAsString());JsonObject handle=rpc("openpnp_apply_placement_structure",apply);proof.put("operation_id",handle.get("operation_id").getAsString());
            try{
                require(entered.await(3,TimeUnit.SECONDS),"selected native publication reached bounded listener");require(held.get(),"native publication remains held before UI probe");long start=System.nanoTime();boolean responsive=boundedEdt(()->frame.isShowing()&&frame.getJobTab().getPlacementsHolderLocationsTable().isShowing());proof.put("edt_probe_elapsed_ms",(System.nanoTime()-start)/1e6);require(responsive&&held.get(),"actual EDT responds while owned native setter is held");
                boundedEdt(()->{click("Take local control");return null;});require(held.get(),"actual local takeover button returns without waiting for held native setter");proof.put("takeover_button_invoked_while_native_publication_held",true);
            }finally{release.countDown();}
            String id=handle.get("operation_id").getAsString();JsonObject op=handle;long end=System.nanoTime()+30_000_000_000L;
            while(List.of("accepted","running").contains(op.get("state").getAsString())){require(System.nanoTime()<end,"bounded original operation observation");Thread.sleep(10);op=rpc("openpnp_get_operation",object("operation_id",id));}
            op=rpc("openpnp_get_operation",object("operation_id",id,"view","full"));String terminal=op.get("state").getAsString();require(List.of("succeeded","outcome_unknown").contains(terminal),"held native publication has known success or conservative unknown, never known failure after actual tree mutation");proof.put("original_operation",JSON.fromJson(op,Map.class));proof.put("observed_terminal_state",terminal);
            waitUntil(()->{String state=rpc("openpnp_get_status",object()).getAsJsonObject("gui_ownership").get("state").getAsString();return state.equals("local")||state.equals("fenced");},30000,"bounded local takeover disposition");JsonObject finalStatus=rpc("openpnp_get_status",object());proof.put("final_gui_ownership",JSON.fromJson(finalStatus.getAsJsonObject("gui_ownership"),Map.class));
            if(terminal.equals("succeeded")){expect("LOCAL_GRANT_REQUIRED","openpnp_plan_placement_structure",bound("changes",array(cloneChild())));require(true,"new agent panel admission refused after local takeover");proof.put("new_agent_admission_refused",true);}
            require(!config.getMachine().isEnabled()&&!config.getMachine().isBusy(),"no machine enable or continuing native work");List<JsonObject> rows=new ArrayList<>();for(String line:Files.readAllLines(state.resolve("journal/operations.jsonl")))rows.add(new JsonParser().parse(line).getAsJsonObject());int reservations=0;for(JsonObject row:rows){String type=row.get("type").getAsString();require(!type.equals("native_action_intent")&&!type.equals("native_action_outcome")&&!type.equals("native_placement_checkpoint"),"no native machine action or placement in takeover child");if(type.equals("job_lineage_reservation"))reservations++;}require(reservations==1,"one structural reservation and no mutation replay");proof.put("journal_sha256_before_exit",sha(state.resolve("journal/operations.jsonl")));proof.put("native_action_count",0);proof.put("lineage_reservations",reservations);require(errors.isEmpty(),"no uncaught GUI/native errors before disposition");
            if(terminal.equals("outcome_unknown")){
                require(finalStatus.getAsJsonObject("gui_ownership").get("state").getAsString().equals("fenced"),"unknown publication retains actual GUI ownership fence");proof.put("functional_checks_passed",true);proof.put("expected_process_exit_code",2);proof.put("explicit_unknown_exit_requested",true);write(jar,runtime);click("Exit simulator preserving unknown");Thread.sleep(10000);throw new AssertionError("explicit native unknown exit did not terminate owned process");
            }
            require(finalStatus.getAsJsonObject("gui_ownership").get("state").getAsString().equals("local"),"completed publication drains before local ownership returns");require(op.has("native_completion")&&op.getAsJsonObject("native_completion").get("native_wrapper_completed").getAsBoolean()&&op.getAsJsonObject("native_completion").get("native_wrapper_succeeded").getAsBoolean(),"known success has actual completed successful wrapper");click("Disconnect bridge");waitUntil(()->edt(()->frame.getRootPane().getClientProperty(KEY))==null,30000,"known completed takeover detaches cleanly");detached=true;proof.put("expected_process_exit_code",0);proof.put("explicit_unknown_exit_requested",false);
        }catch(Throwable t){failure=t;t.printStackTrace();}finally{release.countDown();if(job!=null&&gate!=null)job.getRootPanelLocation().getPanel().removePropertyChangeListener("children",gate);try{if(config!=null){config.getMachine().close();machineClosed=true;}}catch(Throwable t){errors.add("machine close: "+t);}try{edt(()->{for(Window w:Window.getWindows())w.dispose();return null;});windowsClosed=edt(()->Arrays.stream(Window.getWindows()).noneMatch(Window::isDisplayable));}catch(Throwable t){errors.add("window disposal: "+t);}}
        boolean ok=failure==null&&errors.isEmpty()&&machineClosed&&windowsClosed&&detached;proof.putAll(map("functional_checks_passed",ok,"failure",failure==null?null:failure.toString(),"machine_closed",machineClosed,"all_owned_windows_disposed",windowsClosed,"bridge_detached",detached));write(jar,runtime);System.out.println("OPENPNP_GUI_PANEL_TAKEOVER_RESULT "+JSON.toJson(map("passed",ok,"state",proof.get("observed_terminal_state"),"assertions",checks.size())));System.exit(ok?0:1);
    }
}
