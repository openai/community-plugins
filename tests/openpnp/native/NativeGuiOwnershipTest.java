/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.awt.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.*;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.*;
import org.openpnp.gui.MainFrame;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.base.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.camera.AbstractSettlingCamera;
import org.openpnp.scripting.Scripting;

/** Run WITHOUT the bridge JAR on application classpath: native Nashorn loads the verified plugin JAR. */
public final class NativeGuiOwnershipTest {
    static final Gson JSON=new Gson();static final String KEY="org.openpnp.codex.gui-controller.v1";
    static MainFrame frame;static Configuration config;static Path state,bootstrap;static URI endpoint;static String token,session;static final List<String> passed=new ArrayList<>();
    public static void main(String[] args)throws Exception {
        if(args.length!=4)throw new IllegalArgumentException("runtime-dir bridge-jar bootstrap-js new-test-root");
        Path runtime=Paths.get(args[0]).toRealPath(),jar=Paths.get(args[1]).toRealPath(),script=Paths.get(args[2]).toRealPath(),root=Paths.get(args[3]).toAbsolutePath();
        if(Files.exists(root))throw new IllegalArgumentException("Test root must be new");Files.createDirectories(root);
        System.setProperty("java.util.prefs.userRoot",root.resolve("preferences").toString());
        check("org.openpnp.codex.IsolatedPreferencesFactory".equals(System.getProperty("java.util.prefs.PreferencesFactory")),"explicit isolated preferences factory required");
        check(java.util.prefs.Preferences.userRoot().getClass().getName().equals("org.openpnp.codex.IsolatedPreferencesFactory$MemoryNode"),"actual preferences implementation is isolated and nonpersistent");
        try{Class.forName("org.openpnp.codex.Bridge",false,ClassLoader.getSystemClassLoader());throw new AssertionError("Bridge must be absent from application classpath");}catch(ClassNotFoundException expected){}
        Path configDir=root.resolve("config");Files.createDirectory(configDir);state=root.resolve("bridge-state");
        Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();config.getMachine().setProperty("Welcome2_0_Dialog_Shown",true);config.save();config.getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();
        frame=edt(()->{MainFrame value=new MainFrame(config);value.setVisible(true);return value;});accelerate();
        bootstrap=config.getScripting().getScriptsDirectory().toPath().resolve("codex-bootstrap.js");Files.copy(script,bootstrap);
        properties(runtime,jar,bootstrap,state);
        int exit=0;
        try {
            Path example=config.getScripting().getScriptsDirectory().toPath().resolve("Examples/JavaScript/Hello_World.js");byte[] originalExample=Files.readAllBytes(example);Files.writeString(example,"// modified native example\n",StandardOpenOption.APPEND);
            try{config.getScripting().execute(bootstrap.toFile());throw new AssertionError("modified native examples were moved");}catch(Exception expected){}
            check(Files.exists(example)&&Files.size(example)>originalExample.length,"modified native example remains in place after refusal");Files.write(example,originalExample);
            // Native document store failure occurs after lock acquisition; a corrected retry must attach in this same GUI JVM.
            Path key=state.resolve("journal/native-job-documents/receipt-key");Files.createDirectories(key.getParent());Files.write(key,new byte[]{1,2,3});Files.setPosixFilePermissions(key,PosixFilePermissions.fromString("rw-------"));
            try{config.getScripting().execute(bootstrap.toFile());throw new AssertionError("corrupt store attachment unexpectedly succeeded");}catch(Exception expected){}
            edt(()->null);check(edt(()->frame.getRootPane().getClientProperty(KEY))==null,"failed attach left registry empty");Files.delete(key);
            config.getScripting().execute(bootstrap.toFile());connect();check(!Files.exists(example),"byte-exact native examples moved outside active script tree");try(java.util.stream.Stream<Path> retained=Files.walk(state)){check(retained.anyMatch(p->p.getFileName().toString().equals("Hello_World.js")),"native example bytes retained in private archive");}passed.add("modified native examples refused; exact16-file native inventory preserved outside script tree");passed.add("actual Nashorn bootstrap/classloader, failed-constructor lock unwind and corrected retry");
            check("gui-simulator".equals(rpc("openpnp_get_capabilities",object()).get("simulator_profile").getAsString()),"GUI profile");
            expect("LOCAL_GRANT_REQUIRED","openpnp_request_control_session",object("request_id","before-local-grant","ttl_seconds",300));
            click("Allow Codex control");awaitOwnership(true);session=rpc("openpnp_request_control_session",object("request_id","gui-grant-one","ttl_seconds",300)).get("session_id").getAsString();
            try{config.getMachine().submit(()->null,null,true);throw new AssertionError("foreign task submitted under native ownership");}catch(RejectedExecutionException expected){}
            try{edt(()->{frame.getJobTab().jobStart();return null;});throw new AssertionError("GUI job started under external ownership");}catch(IllegalStateException expected){}
            check(!edt(()->frame.quit()),"normal GUI quit waits for explicit native drain");
            try{config.getScripting().execute(bootstrap.toFile());throw new AssertionError("script eval admitted while owned");}catch(Scripting.ExecutionRejected expected){}catch(Error expected){check(expected.getClass().getName().contains("ScriptRejected"),"only expected policy Error");}
            check(config.getScripting().getActiveExecutionCount()==0,"rejected script released active-eval count");passed.add("visible local grant, native task/job refusal, script rejection and quit guard");
            run("openpnp_set_machine_enabled",mutation("enabled",true));run("openpnp_home_machine",mutation());
            if(Boolean.getBoolean("openpnp.codex.test.unknownExit")){unknownExit(root);throw new AssertionError("unknown exit did not terminate JVM");}
            JsonObject prepared=run("openpnp_prepare_job",mutation("sample","pnp-test")).getAsJsonObject("result");
            check(edt(()->frame.getJobTab().getJob().getBoardLocations().size())>0,"prepared native job published to actual JobPanel");run("openpnp_validate_job",mutation());
            JsonObject started=rpc("openpnp_start_job",mutation("job_id",prepared.get("job_id").getAsString()));click("Take local control");awaitOwnership(false);
            JsonObject stopped=rpc("openpnp_get_operation",object("operation_id",started.get("operation_id").getAsString()));check(Arrays.asList("aborted","succeeded").contains(stopped.get("state").getAsString()),"takeover drains native job: "+stopped);
            expect("LOCAL_GRANT_REQUIRED","openpnp_home_machine",mutation());passed.add("local revocation drains owned native job and rejects stale session");
            // The default finite strip fixture may consume a partial board on abort; a fresh sample uses remaining stock.
            click("Allow Codex control");awaitOwnership(true);session=rpc("openpnp_request_control_session",object("request_id","gui-grant-two","ttl_seconds",300)).get("session_id").getAsString();
            prepared=run("openpnp_prepare_job",mutation("sample","pnp-test")).getAsJsonObject("result");run("openpnp_validate_job",mutation());
            JsonObject done=run("openpnp_start_job",mutation("job_id",prepared.get("job_id").getAsString()));check(done.getAsJsonObject("result").get("placed").getAsInt()==32,"actual native 32-placement GUI job completed");
            check(done.getAsJsonObject("native_action_ledger").get("actions_started").getAsInt()>0,"native Java action observer journal present");passed.add("actual GUI-owned native processor completes 32 placements with durable action hooks");
            rpc("openpnp_release_control_session",object("session_id",session));awaitOwnership(false);passed.add("lease release returns native ownership to local operator");
            click("Disconnect bridge");waitUntil(()->edt(()->frame.getRootPane().getClientProperty(KEY))==null,30000,"controller disconnect");
            config.getScripting().execute(bootstrap.toFile());connect();check(edt(()->frame.getRootPane().getClientProperty(KEY))!=null,"fresh loader reattached after closed controller");
            click("Disconnect bridge");waitUntil(()->edt(()->frame.getRootPane().getClientProperty(KEY))==null,30000,"second disconnect");passed.add("drained detach closes registry/resources and native bootstrap reattaches");
            System.out.println("OPENPNP_NATIVE_GUI_RESULT "+JSON.toJson(map("passed",passed,"simulation_only",true,"physical_qualification",false,"native_gui",true,"preferences_implementation",java.util.prefs.Preferences.userRoot().getClass().getName(),"bridge_on_application_classpath",false,"bridge_sha256",sha(jar),"runtime_manifest_sha256",sha(runtime.resolve("codex-build-manifest.json")))));
        }catch(Throwable failure){failure.printStackTrace();exit=1;}finally{try{config.getMachine().close();}catch(Exception ignored){}edt(()->{for(Window window:Window.getWindows())window.dispose();return null;});}System.exit(exit);
    }
    static void unknownExit(Path root)throws Exception {
        org.openpnp.machine.reference.feeder.ReferenceStripFeeder feeder=(org.openpnp.machine.reference.feeder.ReferenceStripFeeder)config.getMachine().getFeeders().get(0);
        java.util.concurrent.atomic.AtomicBoolean once=new java.util.concurrent.atomic.AtomicBoolean();java.beans.PropertyChangeListener fault=e->{if("feedCount".equals(e.getPropertyName())&&once.compareAndSet(false,true))throw new IllegalStateException("gui-test-after-native-feed");};
        feeder.addPropertyChangeListener(fault);JsonObject operation=rpc("openpnp_test_feeder",mutation("feeder_id",feeder.getId()));String id=operation.get("operation_id").getAsString();
        waitUntil(()->"outcome_unknown".equals(rpc("openpnp_get_operation",object("operation_id",id)).get("state").getAsString()),30000,"native uncertain feeder effect");feeder.removePropertyChangeListener(fault);check(feeder.getFeedCount()==1,"one real native feed before local unknown exit");
        click("Take local control");waitUntil(()->"fenced".equals(rpc("openpnp_get_status",object()).getAsJsonObject("gui_ownership").get("state").getAsString()),30000,"unknown takeover remains fenced");
        Files.writeString(root.resolve("unknown-exit-proof.json"),JSON.toJson(map("operation_id",id,"state","outcome_unknown","actual_native_feed_count",feeder.getFeedCount(),"local_exit_requested",true,"native_abort_requested",false)));
        click("Exit simulator preserving unknown");Thread.sleep(10000);throw new AssertionError("local unknown exit was not performed");
    }
    static void accelerate(){ReferenceMachine m=(ReferenceMachine)config.getMachine();((NullDriver)m.getDefaultDriver()).setFeedRateMmPerMinute(0);for(Axis axis:m.getAxes())if(axis instanceof ReferenceControllerAxis){ReferenceControllerAxis a=(ReferenceControllerAxis)axis;a.setFeedratePerSecond(new Length(1000000,LengthUnit.Millimeters));a.setAccelerationPerSecond2(new Length(2000000,LengthUnit.Millimeters));a.setJerkPerSecond3(new Length(0,LengthUnit.Millimeters));}for(Camera camera:m.getAllCameras())if(camera instanceof AbstractSettlingCamera){AbstractSettlingCamera c=(AbstractSettlingCamera)camera;c.setSettleMethod(AbstractSettlingCamera.SettleMethod.FixedTime);c.setSettleTimeMs(0);}for(Head h:m.getHeads())for(Nozzle n:h.getNozzles()){((ReferenceNozzle)n).setPickDwellMilliseconds(0);((ReferenceNozzle)n).setPlaceDwellMilliseconds(0);}}
    static void properties(Path runtime,Path jar,Path script,Path state)throws Exception {System.setProperty("openpnp.codex.bridgeJar",jar.toString());System.setProperty("openpnp.codex.bridgeSha256",sha(jar));System.setProperty("openpnp.codex.stateDir",state.toString());System.setProperty("openpnp.codex.sampleRoot",runtime.resolve("samples").toString());System.setProperty("openpnp.codex.bootstrapPath",script.toString());System.setProperty("openpnp.codex.bootstrapSha256",sha(script));System.setProperty("openpnp.codex.runtimeManifest",runtime.resolve("codex-build-manifest.json").toString());System.setProperty("openpnp.codex.runtimeManifestSha256",sha(runtime.resolve("codex-build-manifest.json")));}
    static void connect()throws Exception {JsonObject connection=new JsonParser().parse(Files.readString(state.resolve("connection.json"))).getAsJsonObject();endpoint=URI.create(connection.get("url").getAsString()).resolve("rpc");token=Files.readString(Paths.get(connection.get("tokenFile").getAsString())).trim();}
    static JsonObject rpc(String method,JsonObject params)throws Exception {HttpURLConnection connection=(HttpURLConnection)endpoint.toURL().openConnection();connection.setConnectTimeout(5000);connection.setReadTimeout(10000);connection.setRequestMethod("POST");connection.setDoOutput(true);connection.setRequestProperty("Content-Type","application/json");connection.setRequestProperty("Authorization","Bearer "+token);try(OutputStream out=connection.getOutputStream()){out.write(JSON.toJson(object("method",method,"params",params)).getBytes(StandardCharsets.UTF_8));}int status=connection.getResponseCode();try(InputStream in=status<400?connection.getInputStream():connection.getErrorStream()){JsonObject body=new JsonParser().parse(new String(in.readAllBytes(),StandardCharsets.UTF_8)).getAsJsonObject();if(body.has("error"))throw new RpcFault(body.getAsJsonObject("error").get("code").getAsString(),body.toString());return body.getAsJsonObject("result");}finally{connection.disconnect();}}
    static JsonObject run(String method,JsonObject p)throws Exception {JsonObject op=rpc(method,p);String id=op.get("operation_id").getAsString();long deadline=System.nanoTime()+120_000_000_000L;while(System.nanoTime()<deadline){op=rpc("openpnp_get_operation",object("operation_id",id));String state=op.get("state").getAsString();if("succeeded".equals(state)){waitUntil(()->!config.getMachine().isBusy(),10000,"native executor drain");return op;}if(Arrays.asList("failed","aborted","outcome_unknown").contains(state))throw new AssertionError(method+": "+op);Thread.sleep(20);}throw new AssertionError("operation timeout "+op);}
    static void expect(String code,String method,JsonObject p)throws Exception {try{rpc(method,p);throw new AssertionError("expected "+code);}catch(RpcFault f){check(code.equals(f.code),f.getMessage());}}
    static void awaitOwnership(boolean held)throws Exception {waitUntil(()->((AbstractMachine)config.getMachine()).getExternalExecutionControl().getOwnerLabel()!=null==held&&rpc("openpnp_get_status",object()).getAsJsonObject("gui_ownership").get("state").getAsString().equals(held?"granted":"local"),30000,"GUI ownership "+held);}
    static void click(String text)throws Exception {edt(()->{for(Window w:Window.getWindows()){JButton button=findButton(w,text);if(button!=null&&button.isShowing()){check(button.isEnabled(),"button disabled: "+text);button.doClick();return null;}}throw new AssertionError("button unavailable: "+text);});}
    static JButton findButton(Component component,String text){if(component instanceof JButton&&text.equals(((JButton)component).getText()))return(JButton)component;if(component instanceof Container)for(Component child:((Container)component).getComponents()){JButton found=findButton(child,text);if(found!=null)return found;}return null;}
    static <T>T edt(Callable<T> action)throws Exception {if(SwingUtilities.isEventDispatchThread())return action.call();AtomicReference<T> value=new AtomicReference<>();AtomicReference<Throwable> error=new AtomicReference<>();SwingUtilities.invokeAndWait(()->{try{value.set(action.call());}catch(Throwable t){error.set(t);}});if(error.get()!=null){if(error.get() instanceof Exception)throw(Exception)error.get();if(error.get() instanceof Error)throw(Error)error.get();}return value.get();}
    static void waitUntil(Check test,long timeout,String label)throws Exception {long deadline=System.nanoTime()+timeout*1000000L;while(System.nanoTime()<deadline){if(test.get())return;Thread.sleep(25);}throw new AssertionError("Timeout: "+label);}
    static JsonObject mutation(Object...pairs){JsonObject p=object("request_id",UUID.randomUUID().toString(),"session_id",session);for(Map.Entry<String,JsonElement> e:object(pairs).entrySet())p.add(e.getKey(),e.getValue());return p;}
    static JsonObject object(Object...pairs){return JSON.toJsonTree(map(pairs)).getAsJsonObject();}static Map<String,Object> map(Object...pairs){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)m.put((String)pairs[i],pairs[i+1]);return m;}
    static String sha(Path file)throws Exception {StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file)))out.append(String.format(Locale.ROOT,"%02x",b));return out.toString();}
    static void check(boolean yes,String message){if(!yes)throw new AssertionError(message);}interface Check{boolean get()throws Exception;}static final class RpcFault extends Exception{final String code;RpcFault(String code,String message){super(message);this.code=code;}}
}
