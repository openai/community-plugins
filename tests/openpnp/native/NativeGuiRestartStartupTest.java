/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.awt.*;
import java.lang.reflect.Field;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.time.Instant;
import java.util.*;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.function.BooleanSupplier;
import javax.swing.*;
import org.openpnp.gui.MainFrame;
import org.openpnp.model.Configuration;
import org.openpnp.machine.reference.driver.NullDriver;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.spi.base.AbstractMachine;
import org.openpnp.spi.base.ExternalExecutionControl;

/** Actual native/EDT/controller component handoff; synthetic journal/task/local grant,
 * no Bridge admission, source install, restart execution or desktop gesture qualification. */
public final class NativeGuiRestartStartupTest {
    static Path root,manifest,state;static Configuration config;static String manifestHash,mode;static MainFrame frame;
    static int checks,refusals;static List<String> labels=new ArrayList<>();
    interface Checked {void run()throws Exception;}
    static void check(boolean condition,String label){checks++;labels.add(label);if(!condition)throw new AssertionError(label);}
    static void reject(String label,Checked action)throws Exception {try{action.run();throw new AssertionError("Accepted: "+label);}catch(java.io.IOException|Bridge.Fault|IllegalArgumentException|IllegalStateException|UnsupportedOperationException expected){refusals++;check(true,label);}}
    static <T>T edt(Callable<T> action)throws Exception {return GuiBootstrap.onEdt(action);}
    static Map<String,Object> map(Object... pairs){return Bridge.map(pairs);}
    static NullDriver driver(){return (NullDriver)config.getMachine().getDrivers().get(0);}
    static ReferenceTrayFeeder tray(){return (ReferenceTrayFeeder)config.getMachine().getFeeders().stream().filter(f->f instanceof ReferenceTrayFeeder).findFirst().orElseThrow();}
    static void prepare(Path location)throws Exception {
        root=location.toAbsolutePath();Files.createDirectory(root);manifest=root.resolve("prepared.json");state=root.resolve("state");
        Map<String,Object> prepared=GuiSensingFixture.prepare(root.resolve("config"),manifest,"success");manifestHash=(String)prepared.get("manifest_sha256");
        config=Configuration.get();config.getMachine().setProperty("Welcome2_0_Dialog_Shown",true);config.getMachine().submit(()->{tray().setFeedCount(tray().getFeedCount()+1);config.save();return null;},null,true).get(30,TimeUnit.SECONDS);config.getMachine().close();
        Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();
        if(mode.equals("handoff"))frame=edt(()->{MainFrame value=new MainFrame(config);value.setVisible(true);return value;});else config.load();
        Files.createDirectory(state,PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));Files.createDirectory(state.resolve("journal"));
        Files.writeString(state.resolve("journal/machine-id"),UUID.randomUUID().toString());
        // This prefix is explicitly synthetic. Startup records its digest only and marks it
        // unvalidated; real Bridge replay/admission is a separate integration test.
        Files.writeString(state.resolve("journal/operations.jsonl"),"{\"type\":\"synthetic-component-prefix\"}\n");
        check(driver().getControlledVacuumSource()==null,"Native reload has no source");
    }
    static GuiBootstrap.SensingStartup startup()throws Exception {return edt(()->GuiBootstrap.prepareSensingStartup(config,state,"restart",manifest,manifestHash,"success"));}
    static void startupCases()throws Exception {
        reject("EDT required for startup capture",()->GuiBootstrap.prepareSensingStartup(config,state,"restart",manifest,manifestHash,"success"));
        reject("Unknown startup value cannot fall back",()->edt(()->GuiBootstrap.prepareSensingStartup(config,state,"auto",manifest,manifestHash,"success")));
        reject("Default prepared path refuses changed saved inventory",()->edt(()->GuiBootstrap.prepareSensingStartup(config,state,null,manifest,manifestHash,"success")));
        reject("Restart refuses wrong original manifest hash",()->edt(()->GuiBootstrap.prepareSensingStartup(config,state,"restart",manifest,"0".repeat(64),"success")));
        reject("Restart refuses wrong declared scenario",()->edt(()->GuiBootstrap.prepareSensingStartup(config,state,"restart",manifest,manifestHash,"invalid-read")));
        Path identity=state.resolve("journal/machine-id"),events=state.resolve("journal/operations.jsonl");byte[] id=Files.readAllBytes(identity),history=Files.readAllBytes(events);
        Files.delete(identity);reject("Missing old machine identity refuses restart",NativeGuiRestartStartupTest::startup);Files.write(identity,id);
        Files.write(events,new byte[0]);reject("Empty old journal refuses restart",NativeGuiRestartStartupTest::startup);Files.write(events,history);
        Files.setPosixFilePermissions(state,PosixFilePermissions.fromString("rwxr-xr-x"));reject("Nonprivate old state refuses restart",NativeGuiRestartStartupTest::startup);Files.setPosixFilePermissions(state,PosixFilePermissions.fromString("rwx------"));
        Path real=state.resolve("journal/operations.real");Files.move(events,real);Files.createSymbolicLink(events,real);reject("Journal symlink refuses restart",NativeGuiRestartStartupTest::startup);Files.delete(events);Files.move(real,events);
        GuiBootstrap.SensingStartup startup=startup();check(startup.restart,"Explicit restart selected");Map<?,?> startupAtt=(Map<?,?>)startup.provenance.get("startup_attestation");
        check(Boolean.FALSE.equals(startup.provenance.get("source_authority_created"))&&Boolean.FALSE.equals(startup.provenance.get("execution_authority_restored")),"Startup declares neither source nor execution authority");
        Map<?,?> journal=(Map<?,?>)startup.provenance.get("journal_prefix");check(Boolean.FALSE.equals(journal.get("history_validated"))&&journal.get("operations_sha256").equals(GuiBootstrap.hash(history)),"Journal prefix exact digest is provenance, not validated recovery");
        reject("Fresh local capture requires EDT",startup::captureLocalRestart);
        GuiSensingFixture.RestartAttestation first=edt(startup::captureLocalRestart);check(!first.descriptor().get("attestation_id").equals(startupAtt.get("attestation_id")),"Local gesture gets fresh attestation identity");first.check();first.close();reject("Closed handoff loses authority",first::check);
        config.getMachine().submit(()->{tray().setFeedCount(tray().getFeedCount()+1);config.save();return null;},null,true).get(30,TimeUnit.SECONDS);
        GuiSensingFixture.RestartAttestation fresh=edt(startup::captureLocalRestart);check(!fresh.descriptor().get("current_files_sha256").equals(startupAtt.get("current_files_sha256")),"Later legitimate native save is captured anew");
        config.getMachine().submit(()->{fresh.check();return null;},null,true).get(30,TimeUnit.SECONDS);fresh.close();
        byte[] bytes=Files.readAllBytes(manifest);Files.writeString(manifest,"\n",StandardOpenOption.APPEND);reject("Tampered provenance never silently recaptured",()->edt(startup::captureLocalRestart));Files.write(manifest,bytes);
        check(driver().getControlledVacuumSource()==null,"All startup and recapture paths leave source absent");check(Arrays.equals(history,Files.readAllBytes(events)),"Startup never appends journal history");
        GuiOwnership.SensingReconciliationSubmission old=guard->CompletableFuture.completedFuture(Map.of());GuiSensingFixture.RestartAttestation unsupported=edt(startup::captureLocalRestart);
        reject("Old adapter has no implicit restart callback",()->old.submitRestart(unsupported,()->true));unsupported.close();
    }
    static Map<String,Object> task(){return map("task_id",UUID.randomUUID().toString(),"recovery_kind",NativeSensingReconciliation.RESTART_KIND,"expires_at",Instant.now().plusSeconds(90).toString(),"expires_in_ms",90000,"fault_set_sha256","a".repeat(64),"snapshot",map("context",map("scope","synthetic controller fixture"),"source",map("available",false,"current_source_created",false),"replacement",map("replacement_attempt_id",UUID.randomUUID().toString()),"faults",List.of(map("observation_id","synthetic-old","state","unknown")),"proposed_effects",List.of(map("kind","reattach-inactive-graph")),"prior_outcome_disposition","unknown preserved"));}
    static void field(Object owner,String name,Object value)throws Exception {Field f=owner.getClass().getDeclaredField(name);f.setAccessible(true);f.set(owner,value);}
    static JButton button(String name)throws Exception {return edt(()->{for(Window w:Window.getWindows()){Component c=find(w,name);if(c instanceof JButton&&w.isVisible())return (JButton)c;}throw new IllegalStateException("Button absent: "+name);});}
    static Component find(Component c,String name){if(name.equals(c.getName()))return c;if(c instanceof Container)for(Component child:((Container)c).getComponents()){Component found=find(child,name);if(found!=null)return found;}return null;}
    static void handoffCases()throws Exception {
        check(edt(()->GuiSimulatorController.restartGrantJob(frame.getJobTab().getJob()))==null,"Actual GUI default empty job does not acquire a Bridge job identity");
        reject("Restart job selection requires EDT",()->GuiSimulatorController.restartGrantJob(frame.getJobTab().getJob()));
        org.openpnp.model.Job opened=new org.openpnp.model.Job();opened.setFile(root.resolve("opened.job.xml").toFile());
        reject("Opened native file refuses restart grant",()->edt(()->GuiSimulatorController.restartGrantJob(opened)));
        org.openpnp.model.Job populated=new org.openpnp.model.Job();populated.addBoardOrPanelLocation(new org.openpnp.model.BoardLocation(new org.openpnp.model.Board()));populated.setDirty(false);
        reject("Even a clean nonempty native job refuses restart grant",()->edt(()->GuiSimulatorController.restartGrantJob(populated)));
        org.openpnp.model.Job historical=new org.openpnp.model.Job();historical.storePlacedStatus(historical.getRootPanelLocation(),"old",false);historical.setDirty(false);
        reject("Opaque false placed history refuses default-job substitution",()->edt(()->GuiSimulatorController.restartGrantJob(historical)));
        GuiBootstrap.SensingStartup startup=startup();Map<String,Object> provenance=map("sensing_restart_attested",true,"sensing_fixture_attested",false);
        GuiSimulatorController controller=edt(()->new GuiSimulatorController(config,frame,state,root,root.resolve("unused.js"),"0".repeat(64),provenance,new URLClassLoader(new URL[0]),startup));
        ExternalExecutionControl gate=((AbstractMachine)config.getMachine()).getExternalExecutionControl();Object token=edt(()->{Object owned=gate.claim("Synthetic GUI restart component owner");frame.getJobTab().claimExternalJobControl(owned);return owned;});
        // Test-only admission setup uses the real native gate; Bridge admission is not exercised.
        field(controller,"nativeToken",token);field(controller,"remoteAllowed",true);field(controller,"sensingRevoked",false);
        try {
            check(controller.supportsSensingRestart()&&controller.supportsSensingReconciliation(),"Source-absent controller advertises explicit local restart capability");
            Map<String,Object> bad=task();bad.put("snapshot",map("context",Map.of(),"faults",List.of("f"),"proposed_effects",List.of("p")));reject("Restart form requires complete source/replacement context",()->NativeSensingReconciliationDialog.validateTask(bad));
            for(String outcome:List.of("success","cancelled","failure")) {
                AtomicInteger submissions=new AtomicInteger(),legacy=new AtomicInteger(),cancellations=new AtomicInteger();AtomicReference<GuiSensingFixture.RestartAttestation> passed=new AtomicReference<>();AtomicReference<BooleanSupplier> authority=new AtomicReference<>();CountDownLatch entered=new CountDownLatch(1);CompletableFuture<Map<String,Object>> future=new CompletableFuture<>();Map<String,Object> task=task();
                controller.presentSensingReconciliation(task,new GuiOwnership.SensingReconciliationSubmission(){
                    public CompletionStage<Map<String,Object>> submit(BooleanSupplier guard){legacy.incrementAndGet();throw new AssertionError("Restart used legacy callback");}
                    public CompletionStage<Map<String,Object>> submitRestart(GuiSensingFixture.RestartAttestation attestation,BooleanSupplier guard)throws Exception {check(!SwingUtilities.isEventDispatchThread(),"Restart callback is off EDT");submissions.incrementAndGet();passed.set(attestation);authority.set(guard);controller.submitNative(()->{attestation.check();check(guard.getAsBoolean(),"Native consumer sees current local guard");return null;},true).get(30,TimeUnit.SECONDS);entered.countDown();return future;}
                    public void cancel(String reason){cancellations.incrementAndGet();}
                });edt(()->null);JButton submit=button("sensing_reconciliation.submit");edt(()->{check(submit.getText().equals("Reattach inactive replacement and observe restart state"),"Specific restart action displayed");submit.doClick();submit.doClick();return null;});
                check(entered.await(30,TimeUnit.SECONDS),"Native-safe restart handoff received");check(submissions.get()==1&&legacy.get()==0,"One gesture uses only explicit restart callback");
                edt(()->{check(true,"EDT responds while callback completion remains pending");return null;});check(driver().getControlledVacuumSource()==null,"Handoff itself installs no source");
                if(outcome.equals("cancelled")){controller.dismissSensingReconciliation((String)task.get("task_id"),"test-local-cancel");check(!authority.get().getAsBoolean(),"Cancellation immediately revokes local guard");reject("Cancellation closes pending attestation",passed.get()::check);future.complete(Map.of());}
                else if(outcome.equals("failure"))future.completeExceptionally(new java.io.IOException("test-native-refusal"));else future.complete(Map.of());
                edt(()->null);reject("Terminal callback closes handoff attestation",passed.get()::check);controller.dismissSensingReconciliation((String)task.get("task_id"),"test-finished");edt(()->null);
            }
            check(!config.getMachine().isEnabled()&&!config.getMachine().isHomed()&&!config.getMachine().isBusy(),"GUI handoff leaves native machine disabled unhomed and idle");
        }finally{field(controller,"remoteAllowed",false);edt(()->{frame.getJobTab().releaseExternalJobControl(token);return null;});gate.release(token);field(controller,"nativeToken",null);controller.closeAfterFailedStart();edt(()->null);}
    }
    public static void main(String[] args)throws Exception {if(args.length!=2)throw new IllegalArgumentException("new-state startup|handoff");mode=args[1];int exit=0;Throwable failure=null;try{prepare(Path.of(args[0]));if(mode.equals("startup"))startupCases();else if(mode.equals("handoff"))handoffCases();else throw new IllegalArgumentException("Unknown mode");}catch(Throwable t){failure=t;t.printStackTrace();exit=1;}finally{try{if(config!=null)config.getMachine().close();}catch(Throwable t){t.printStackTrace();exit=1;}if(frame!=null)edt(()->{for(Window w:Window.getWindows())w.dispose();return null;});Map<String,Object> proof=map("passed",exit==0,"checks",checks,"refusals",refusals,"mode",mode,"labels",labels,"failure",failure==null?null:failure.toString(),"pid",ProcessHandle.current().pid(),"native_jobs",0,"source_installs",0,"synthetic_task_journal_and_local_grant",true,"actual_native_gui",mode.equals("handoff"),"bridge_restart_qualified",false,"desktop_gestures",false,"hardware_qualified",false);if(root!=null)Files.writeString(root.resolve("proof.json"),new Gson().toJson(proof)+"\n");System.out.println("NATIVE_GUI_RESTART_STARTUP_RESULT "+new Gson().toJson(proof));}System.exit(exit);}
}
