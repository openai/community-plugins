/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.awt.*;
import java.awt.event.*;
import java.io.File;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.*;
import org.openpnp.gui.MainFrame;
import org.openpnp.model.Configuration;
import org.openpnp.model.Job;
import org.openpnp.spi.Machine;
import org.openpnp.spi.base.AbstractMachine;
import org.openpnp.spi.base.ExternalExecutionControl;
import org.openpnp.scripting.Scripting;

/** Visible local ownership plus native executor and JobPanel admission. Simulator only. */
public final class GuiSimulatorController implements GuiOwnership,Runnable {
    private final Configuration config;private final MainFrame frame;private final Machine machine;
    private final Path state,samples,bootstrap;private final String bootstrapHash;
    private final Map<String,Object> provenance;private final URLClassLoader loader;private final GuiBootstrap.SensingStartup sensingStartup;
    private final ScheduledExecutorService worker=Executors.newSingleThreadScheduledExecutor(r->{Thread t=new Thread(r,"openpnp-codex-gui-controller");t.setDaemon(true);return t;});
    private final AtomicBoolean actionPending=new AtomicBoolean();
    private final AtomicReference<InspectionView> inspection=new AtomicReference<>();
    private final AtomicReference<SensingView> sensingRecovery=new AtomicReference<>();
    private volatile boolean sensingRevoked=true;
    private volatile boolean inspectionRevoked=true;
    private final JDialog dialog;private final JLabel status=new JLabel("Local operator owns this simulator.");
    private final JButton grant=new JButton("Allow Codex control"),takeover=new JButton("Take local control"),disconnect=new JButton("Disconnect bridge"),unknownExit=new JButton("Exit simulator preserving unknown");
    private final WindowListener lifecycle;
    private volatile Map<String,Object> observation=Collections.emptyMap(),lastError=Collections.emptyMap();
    private volatile Object nativeToken;private volatile boolean remoteAllowed,closed,closing,seenLease;
    private volatile String phase="local";private long grantDeadline;
    private Scripting.ExecutionConstraint scriptConstraint;private Bridge bridge;private Thread shutdownHook;
    GuiSimulatorController(Configuration config,MainFrame frame,Path state,Path samples,Path bootstrap,String bootstrapHash,Map<String,Object> provenance,URLClassLoader loader) {
        this(config,frame,state,samples,bootstrap,bootstrapHash,provenance,loader,null);
    }
    GuiSimulatorController(Configuration config,MainFrame frame,Path state,Path samples,Path bootstrap,String bootstrapHash,Map<String,Object> provenance,URLClassLoader loader,GuiBootstrap.SensingStartup sensingStartup) {
        if(!SwingUtilities.isEventDispatchThread())throw new IllegalStateException("GUI controller construction requires EDT");
        this.config=config;this.frame=frame;this.machine=config.getMachine();this.state=state;this.samples=samples;this.bootstrap=bootstrap;this.bootstrapHash=bootstrapHash;this.provenance=Collections.unmodifiableMap(new LinkedHashMap<>(provenance));this.loader=loader;this.sensingStartup=sensingStartup;
        dialog=new JDialog(frame,"Codex — simulator control",Dialog.ModalityType.MODELESS);dialog.setDefaultCloseOperation(WindowConstants.DO_NOTHING_ON_CLOSE);
        JPanel content=new JPanel(new BorderLayout(12,12));content.setBorder(BorderFactory.createEmptyBorder(16,16,16,16));
        content.add(new JLabel("<html>Native simulator only. Physical machines are refused.<br>Allowing control blocks GUI edits until native work has stopped.<br>Taking local control finishes the current native step and aborts the job.<br>This is a cooperative control, not an emergency stop.</html>"),BorderLayout.NORTH);
        content.add(status,BorderLayout.CENTER);JPanel buttons=new JPanel();buttons.add(grant);buttons.add(takeover);buttons.add(disconnect);buttons.add(unknownExit);unknownExit.setVisible(false);content.add(buttons,BorderLayout.SOUTH);dialog.setContentPane(content);dialog.pack();dialog.setLocationRelativeTo(frame);
        grant.addActionListener(e->{setModal(true);enqueue(this::grantLocal);});
        takeover.addActionListener(e->{revokeInspection("local-operator");enqueue(()->beginTakeover("local-operator",false));});
        disconnect.addActionListener(e->{revokeInspection("local-disconnect");enqueue(()->beginTakeover("local-disconnect",true));});
        unknownExit.addActionListener(e->enqueue(()->{
            if(!"fenced".equals(phase)||nativeToken==null)throw new IllegalStateException("Unknown-exit is available only for a fenced owned simulator");
            // This local gesture exits this simulator JVM, without native cleanup or implicit resolution.
            // The previously forced operation/action intents remain the recovery authority.
            try{bridge.recordGuiUnknownExit();}catch(Exception storageUnavailable){System.err.println("Codex simulator exit: prior unknown journal retained; final exit record unavailable.");}
            System.exit(2);
        }));
        dialog.addWindowListener(new WindowAdapter(){@Override public void windowClosing(WindowEvent e){revokeInspection("local-window-close");enqueue(()->beginTakeover("local-window-close",true));}});
        lifecycle=new WindowAdapter(){@Override public void windowClosed(WindowEvent e){revokeInspection("main-window-closed");enqueue(()->beginTakeover("main-window-closed",true));}};frame.addWindowListener(lifecycle);
        publish("local",sensingStartup!=null&&sensingStartup.restart?"Restart recovery: sources are absent. A local recovery task may reattach inactive jobs and observe loads; execution remains unavailable.":"Local operator owns this simulator.");
    }
    void start(Path token)throws Exception {
        bridge=new Bridge(config,token,state.resolve("journal"),samples,0,true,"gui-simulator",this);bridge.start();
        GuiBootstrap.privateWrite(state.resolve("connection.json"),GuiBootstrap.JSON.toJson(Bridge.map("url","http://127.0.0.1:"+bridge.getPort()+"/","tokenFile",token.toString())).getBytes(StandardCharsets.UTF_8),true);
        shutdownHook=new Thread(()->{remoteAllowed=false;try{if(bridge!=null)bridge.close();}catch(Exception ignored){}},"openpnp-codex-gui-shutdown");Runtime.getRuntime().addShutdownHook(shutdownHook);
        worker.scheduleWithFixedDelay(()->{try{tick();}catch(Throwable error){showFailure(error);}},100,250,TimeUnit.MILLISECONDS);
        System.out.println("OPENPNP_CODEX_GUI_READY port="+bridge.getPort()+" mode=gui-simulator");
    }
    @Override public void run(){if(!SwingUtilities.isEventDispatchThread()){SwingUtilities.invokeLater(this);return;}if(!closed){dialog.setVisible(true);dialog.toFront();}}
    private void enqueue(Checked action){if(closed||!actionPending.compareAndSet(false,true))return;worker.execute(()->{try{action.run();}catch(Throwable error){showFailure(error);}finally{actionPending.set(false);}});}
    private void grantLocal()throws Exception {
        if(closing||nativeToken!=null)throw new IllegalStateException("Control is already held or closing");
        lastError=Collections.emptyMap();checkAttachment(config,machine);verifyScriptInventory();
        if(config.getScripting().getActiveExecutionCount()!=0)throw new IllegalStateException("A native script is still executing");
        Object token=GuiBootstrap.onEdt(()->{
            for(Window window:Window.getWindows())if(window!=dialog&&window!=frame&&window.isVisible()&&window instanceof Dialog)throw new IllegalStateException("Close other native dialogs/wizards before control grant");
            if(!"Stopped".equals(frame.getJobTab().getExecutionState()))throw new IllegalStateException("Stop the GUI job before granting control");
            ExternalExecutionControl gate=((AbstractMachine)machine).getExternalExecutionControl();Object acquired=gate.claim("Codex native simulator");
            try{frame.getJobTab().claimExternalJobControl(acquired);}catch(Throwable failure){gate.release(acquired);throw failure;}return acquired;
        });
        nativeToken=token;
        try {
            // Once bootstrap returned, no script eval is needed for owned simulator operations.
            scriptConstraint=config.getScripting().constrainExecution(file->{throw new ScriptRejected("Script eval is disabled while Codex owns the simulator");});
            remoteAllowed=true;seenLease=false;grantDeadline=System.nanoTime()+TimeUnit.MINUTES.toNanos(2);
            Job guiJob=GuiBootstrap.onEdt(()->{Job selected=frame.getJobTab().getJob();return supportsSensingRestart()?restartGrantJob(selected):selected;});bridge.acceptLocalGuiGrant(guiJob);inspectionRevoked=false;sensingRevoked=false;
            publish("granted","Codex may acquire one control lease. Waiting for connection…");
        }catch(Throwable failure){remoteAllowed=false;releaseNative();throw failure;}
    }
    /** Restart admission must not turn the GUI's new empty document into an admitted job. */
    static Job restartGrantJob(Job selected)throws Exception {
        if(!SwingUtilities.isEventDispatchThread())throw new IllegalStateException("Restart GUI job selection requires EDT");
        if(selected==null||selected.getClass()!=Job.class||selected.getFile()!=null||selected.isDirty()
                ||!selected.getBoardLocations().isEmpty()||selected.getPanelLocations().size()!=1
                ||!selected.getPlacedStatusSnapshot().isEmpty()||selected.getRootPanelLocation()==null
                ||selected.getRootPanelLocation().getPanel()==null||selected.getRootPanelLocation().getPanel().getFile()!=null
                ||!selected.getRootPanelLocation().getPanel().getChildren().isEmpty()
                ||!selected.getRootPanelLocation().getPanel().getPlacements().isEmpty()
                ||!selected.getRootPanelLocation().getPanel().getPseudoPlacements().isEmpty())
            throw new Bridge.Fault("GUI_RESTART_JOB_CONFLICT","Close the opened job and select a new empty native job before restart recovery");
        return null;
    }
    private void beginTakeover(String reason,boolean disconnectAfter)throws Exception {
        revokeInspection(reason);
        closing|=disconnectAfter;remoteAllowed=false;
        if(nativeToken==null){if(closing)closeIdle();else publish("local","Local operator owns this simulator.");return;}
        if(!"revoking".equals(phase)){bridge.requestLocalGuiTakeover(reason);publish("revoking","Revoking Codex control; waiting for native standstill and job cleanup…");}
    }
    @SuppressWarnings("unchecked") private void tick()throws Exception {
        if(closed||bridge==null)return;
        SensingView recovery=sensingRecovery.get();if(recovery!=null&&System.nanoTime()-recovery.deadline>=0)invalidateSensingRecovery(recovery.id,"local-task-expired",true);
        if("revoking".equals(phase)){
            if(bridge.drainLocalGuiTakeover()){releaseNative();if(closing){closeIdle();return;}publish("local","Native work stopped. Local operator owns this simulator.");setModal(false);}return;
        }
        if(nativeToken!=null && remoteAllowed){
            Map<String,Object> lease=(Map<String,Object>)bridge.call("openpnp_get_control_session",new com.google.gson.JsonObject());
            if(lease.get("session_id")!=null)seenLease=true;
            else if(seenLease||System.nanoTime()>grantDeadline){beginTakeover(seenLease?"remote-lease-ended":"local-grant-timeout",false);return;}
            Map<String,Object> state=(Map<String,Object>)bridge.call("openpnp_get_status",new com.google.gson.JsonObject());
            publish("granted","Codex control — job "+state.get("job_state")+"; lease "+lease.get("expires_in_ms")+" ms remaining");
        }
    }
    private void releaseNative()throws Exception {
        revokeInspection("native-ownership-released");
        Object token=nativeToken;if(token==null)return;
        ExternalExecutionControl gate=((AbstractMachine)machine).getExternalExecutionControl();
        if(gate.getPendingTaskCount()!=0||machine.isBusy())throw new IllegalStateException("Native executor has not drained");
        GuiBootstrap.onEdt(()->{frame.getJobTab().releaseExternalJobControl(token);return null;});
        gate.release(token);nativeToken=null;
        if(scriptConstraint!=null){scriptConstraint.close();scriptConstraint=null;}
    }
    private void closeIdle()throws Exception {
        revokeInspection("bridge-closed");
        if(nativeToken!=null||machine.isBusy())throw new IllegalStateException("Cannot close bridge while native work remains");
        bridge.close();closed=true;remoteAllowed=false;worker.shutdown();
        if(shutdownHook!=null)Runtime.getRuntime().removeShutdownHook(shutdownHook);
        GuiBootstrap.onEdt(()->{frame.removeWindowListener(lifecycle);if(frame.getRootPane().getClientProperty(GuiBootstrap.KEY)==this)frame.getRootPane().putClientProperty(GuiBootstrap.KEY,null);dialog.dispose();return null;});
        GuiBootstrap.privateWrite(state.resolve("gui-lifecycle.json"),GuiBootstrap.JSON.toJson(Bridge.map("state","closed","native_executor_drained",true,"journal_preserved",true)).getBytes(StandardCharsets.UTF_8),true);loader.close();
    }
    void closeAfterFailedStart(){remoteAllowed=false;revokeInspection("bridge-start-failed");try{if(nativeToken!=null)releaseNative();if(bridge!=null)bridge.close();}catch(Exception ignored){}closed=true;worker.shutdownNow();if(shutdownHook!=null)try{Runtime.getRuntime().removeShutdownHook(shutdownHook);}catch(IllegalStateException shutdown){}try{loader.close();}catch(java.io.IOException ignored){}SwingUtilities.invokeLater(()->{frame.removeWindowListener(lifecycle);dialog.dispose();});}
    private void verifyScriptInventory()throws Exception {
        File directory=config.getScripting().getScriptsDirectory();if(directory==null)throw new IllegalStateException("Native scripts directory is unavailable");Path root=directory.toPath().toRealPath();
        if(!bootstrap.startsWith(root)||!Files.isRegularFile(bootstrap,LinkOption.NOFOLLOW_LINKS)||Files.size(bootstrap)>65536||!GuiBootstrap.hash(Files.readAllBytes(bootstrap)).equals(bootstrapHash))throw new IllegalStateException("Verified bootstrap is not in this GUI configuration's script tree");
        Set<String> extensions=new HashSet<>(Arrays.asList(config.getScripting().getExtensions()));
        try(java.util.stream.Stream<Path> files=Files.walk(root)){for(Path file:(Iterable<Path>)files::iterator){if(Files.isSymbolicLink(file))throw new IllegalStateException("Script-tree symlinks are refused");if(!Files.isRegularFile(file))continue;String name=file.getFileName().toString();int dot=name.lastIndexOf('.');String extension=dot<0?"":name.substring(dot+1).toLowerCase(Locale.ROOT);if(extensions.contains(extension)&&!file.toRealPath().equals(bootstrap))throw new IllegalStateException("Unknown executable script must be removed before GUI control: "+root.relativize(file));}}
    }
    @Override public void checkAttachment(Configuration configuration,Machine actual)throws Exception {
        if(closed||configuration!=config||actual!=machine||MainFrame.get()!=frame||!(machine instanceof AbstractMachine))throw new Bridge.Fault("GUI_ATTACHMENT_CHANGED","Native GUI attachment changed");
        Bridge.verifyNativeSimulatorClasses(machine);
        if(((AbstractMachine)machine).getExternalExecutionControl().getApiVersion()!=1)throw new Bridge.Fault("GUI_RUNTIME_UNSUPPORTED","Native ownership API version mismatch");
    }
    @Override public void requireNativeOwnership()throws Exception {Object token=nativeToken;if(token==null||!((AbstractMachine)machine).getExternalExecutionControl().owns(token))throw new Bridge.Fault("LOCAL_GRANT_REQUIRED","Allow Codex control in the native GUI first");}
    @Override public void requireRemoteGrant()throws Exception {requireNativeOwnership();if(!remoteAllowed||closing)throw new Bridge.Fault("OWNERSHIP_REVOKED","Local operator revoked remote control");}
    @Override public <T>Future<T> submitNative(Callable<T> action,boolean ignoreEnabled)throws Exception {requireNativeOwnership();return ((AbstractMachine)machine).getExternalExecutionControl().withOwner(nativeToken,()->machine.submit(action,null,ignoreEnabled));}
    @Override public <T>T invokeNative(Callable<T> action)throws Exception {try{return action.call();}catch(Scripting.ExecutionRejected failure){throw new ScriptRejected("Native scripting policy rejected execution: "+failure.getMessage());}}
    @Override public void publishJob(Job job)throws Exception {Object token=nativeToken;GuiBootstrap.onEdt(()->{frame.getJobTab().setExternalJob(token,job);return null;});}
    @Override public Map<String,Object> snapshot(){return observation;}
    @Override public boolean supportsLoadedBoardInspection(){return true;}
    @Override public void presentLoadedBoardInspection(Map<String,Object> task,InspectionSubmission submission)throws Exception {
        requireRemoteGrant();
        if(inspectionRevoked)throw new Bridge.Fault("OWNERSHIP_REVOKED","Local operator revoked inspection authority");
        Map<String,Object> immutable=NativeInspectionDialog.immutableMap(task);
        Object id=immutable.get("task_id");
        if(!(id instanceof String)||!UUID.fromString((String)id).toString().equals(id))throw new IllegalArgumentException("Inspection task ID must be a canonical UUID");
        NativeInspectionDialog.placements(immutable);
        InspectionView view=new InspectionView((String)id,immutable,Objects.requireNonNull(submission));
        if(!inspection.compareAndSet(null,view))throw new IllegalStateException("A local inspection is already open");
        // Bridge may hold its monitor here. Creating/showing Swing windows must remain asynchronous.
        SwingUtilities.invokeLater(()->{
            if(!inspectionCurrent(view)){invalidateInspection(view.id,"local-authority-ended",true);return;}
            try {
                view.form=new NativeInspectionDialog(dialog,view.task,values->queueInspection(view,values),()->invalidateInspection(view.id,"local-inspection-closed",true));
                if(!inspectionCurrent(view)){view.form.dispose();return;}
                view.form.setVisible(true);
            }catch(Throwable failure){invalidateInspection(view.id,"inspection-form-failed",true);queueInspectionFailure(failure);}
        });
    }
    @Override public void dismissLoadedBoardInspection(String taskId,String reason){invalidateInspection(taskId,reason,true);}
    private boolean inspectionCurrent(InspectionView view){return inspection.get()==view&&!inspectionRevoked&&!closed&&!closing&&remoteAllowed&&nativeToken!=null;}
    private void queueInspection(InspectionView view,Map<String,Object> values){
        if(!inspectionCurrent(view)||!view.pending.compareAndSet(false,true))return;
        try {worker.execute(()->{
            if(!inspectionCurrent(view)){view.pending.set(false);return;}
            try {
                CompletionStage<Map<String,Object>> completion=view.submission.submit(values,()->inspectionCurrent(view));
                if(completion==null)throw new IllegalStateException("Inspection submission returned no completion");
                completion.whenComplete((receipt,failure)->SwingUtilities.invokeLater(()->{
                    view.pending.set(false);
                    if(inspection.get()!=view)return;
                    if(failure==null){inspection.compareAndSet(view,null);if(view.form!=null)view.form.submissionFinished(null);}
                    else if(view.form!=null)view.form.submissionFinished(failure);
                }));
            }catch(Throwable failure){view.pending.set(false);SwingUtilities.invokeLater(()->{if(inspection.get()==view&&view.form!=null)view.form.submissionFinished(failure);});}
        });}catch(RejectedExecutionException stopped){view.pending.set(false);invalidateInspection(view.id,"bridge-closed",false);}
    }
    private void invalidateInspection(String expectedId,String reason,boolean notify){
        InspectionView view=inspection.get();
        while(view!=null){
            if(expectedId!=null&&!expectedId.equals(view.id))return;
            if(inspection.compareAndSet(view,null)){
                InspectionView removed=view;
                SwingUtilities.invokeLater(()->{if(removed.form!=null)removed.form.dispose();});
                if(notify)try{worker.execute(()->{try{removed.submission.cancel(reason);}catch(Throwable failure){showFailure(failure);}});}catch(RejectedExecutionException stopped){/* Bridge shutdown also invalidates its pending tasks. */}
                return;
            }
            view=inspection.get();
        }
    }
    private void revokeInspection(String reason){inspectionRevoked=true;invalidateInspection(null,reason,true);sensingRevoked=true;invalidateSensingRecovery(null,reason,true);}
    private void queueInspectionFailure(Throwable failure){try{worker.execute(()->showFailure(failure));}catch(RejectedExecutionException stopped){}}
    private static final class InspectionView {
        final String id;final Map<String,Object> task;final InspectionSubmission submission;final AtomicBoolean pending=new AtomicBoolean();
        volatile NativeInspectionDialog form;
        InspectionView(String id,Map<String,Object> task,InspectionSubmission submission){this.id=id;this.task=task;this.submission=submission;}
    }
    @Override public boolean supportsSensingReconciliation(){return Boolean.TRUE.equals(provenance.get("sensing_fixture_attested"))||supportsSensingRestart();}
    @Override public boolean supportsSensingRestart(){return sensingStartup!=null&&sensingStartup.restart&&Boolean.TRUE.equals(provenance.get("sensing_restart_attested"));}
    @Override public void presentSensingReconciliation(Map<String,Object> task,SensingReconciliationSubmission submission)throws Exception {
        requireRemoteGrant();
        if(!supportsSensingReconciliation())throw new Bridge.Fault("SENSING_FIXTURE_REQUIRED","Launch and locally grant the explicit sensing simulator profile");
        if(sensingRevoked)throw new Bridge.Fault("OWNERSHIP_REVOKED","Local operator revoked sensing recovery authority");
        Map<String,Object> immutable=NativeSensingReconciliationDialog.validateTask(task);
        if(NativeSensingReconciliation.RESTART_KIND.equals(immutable.get("recovery_kind"))&&!supportsSensingRestart())throw new Bridge.Fault("SENSING_RESTART_FIXTURE_REQUIRED","Use explicit source-absent GUI restart startup");
        SensingView view=new SensingView((String)immutable.get("task_id"),immutable,Objects.requireNonNull(submission),nativeToken);
        if(!sensingRecovery.compareAndSet(null,view))throw new IllegalStateException("A local sensing recovery is already open");
        // Bridge may hold its monitor; never create Swing forms synchronously here.
        SwingUtilities.invokeLater(()->{
            if(!sensingCurrent(view)){invalidateSensingRecovery(view.id,"local-authority-ended",true);return;}
            try {
                view.form=new NativeSensingReconciliationDialog(dialog,view.task,()->queueSensingRecovery(view),()->invalidateSensingRecovery(view.id,"local-recovery-closed",true));
                if(!sensingCurrent(view)){view.form.dispose();return;}
                view.form.setVisible(true);
            }catch(Throwable failure){invalidateSensingRecovery(view.id,"recovery-form-failed",true);queueInspectionFailure(failure);}
        });
    }
    @Override public void dismissSensingReconciliation(String taskId,String reason){invalidateSensingRecovery(taskId,reason,true);}
    private boolean sensingCurrent(SensingView view){return sensingRecovery.get()==view&&!sensingRevoked&&!closed&&!closing&&remoteAllowed&&nativeToken!=null&&nativeToken==view.nativeToken&&System.nanoTime()-view.deadline<0;}
    private void queueSensingRecovery(SensingView view){
        if(!SwingUtilities.isEventDispatchThread())throw new IllegalStateException("Local sensing gesture requires EDT");
        if(!sensingCurrent(view)||!view.consumed.compareAndSet(false,true))return;
        if(NativeSensingReconciliation.RESTART_KIND.equals(view.task.get("recovery_kind"))) {
            try {
                if(!supportsSensingRestart())throw new IllegalStateException("Explicit restart startup is unavailable");
                view.attestation=sensingStartup.captureLocalRestart();
                if(!sensingCurrent(view)){view.closeAttestation();invalidateSensingRecovery(view.id,"local-authority-ended",true);return;}
            }catch(Throwable failure){view.closeAttestation();if(view.form!=null)view.form.submissionFinished(failure);return;}
        }
        try {worker.execute(()->{
            if(!sensingCurrent(view)){invalidateSensingRecovery(view.id,"local-authority-ended",true);return;}
            try {
                CompletionStage<Map<String,Object>> completion=view.attestation==null?view.submission.submit(()->sensingCurrent(view)):
                    view.submission.submitRestart(view.attestation,()->sensingCurrent(view));
                if(completion==null)throw new IllegalStateException("Sensing recovery submission returned no completion");
                completion.whenComplete((receipt,failure)->{view.closeAttestation();SwingUtilities.invokeLater(()->{
                    if(sensingRecovery.get()!=view)return;
                    if(failure==null){sensingRecovery.compareAndSet(view,null);if(view.form!=null)view.form.submissionFinished(null);}
                    else if(view.form!=null)view.form.submissionFinished(failure);
                });});
            }catch(Throwable failure){view.closeAttestation();SwingUtilities.invokeLater(()->{if(sensingRecovery.get()==view&&view.form!=null)view.form.submissionFinished(failure);});}
        });}catch(RejectedExecutionException stopped){invalidateSensingRecovery(view.id,"bridge-closed",false);}
    }
    private void invalidateSensingRecovery(String expectedId,String reason,boolean notify){
        SensingView view=sensingRecovery.get();
        while(view!=null){
            if(expectedId!=null&&!expectedId.equals(view.id))return;
            if(sensingRecovery.compareAndSet(view,null)){
                SensingView removed=view;removed.closeAttestation();SwingUtilities.invokeLater(()->{if(removed.form!=null)removed.form.dispose();});
                if(notify)try{worker.execute(()->{try{removed.submission.cancel(reason);}catch(Throwable failure){showFailure(failure);}});}catch(RejectedExecutionException stopped){/* Bridge shutdown independently invalidates tasks. */}
                return;
            }
            view=sensingRecovery.get();
        }
    }
    private static final class SensingView {
        final String id;final Map<String,Object> task;final SensingReconciliationSubmission submission;final Object nativeToken;final long deadline;final AtomicBoolean consumed=new AtomicBoolean();
        volatile NativeSensingReconciliationDialog form;volatile GuiSensingFixture.RestartAttestation attestation;
        void closeAttestation(){GuiSensingFixture.RestartAttestation current=attestation;if(current!=null)current.close();}
        SensingView(String id,Map<String,Object> task,SensingReconciliationSubmission submission,Object nativeToken){this.id=id;this.task=task;this.submission=submission;this.nativeToken=nativeToken;this.deadline=NativeSensingReconciliationDialog.deadlineNanos(task);}
    }
    private void publish(String phase,String message){this.phase=phase;observation=Collections.unmodifiableMap(Bridge.map("state",phase,"last_error",lastError,"local_grant",remoteAllowed,"native_ownership_held",nativeToken!=null,"provenance",provenance,"script_policy","verified-bootstrap-inventory; all eval refused during ownership","physical_qualification",false,"gui_qualification","pending-native-tests"));SwingUtilities.invokeLater(()->{if(!closed){status.setText(message);grant.setEnabled(nativeToken==null&&!closing);takeover.setEnabled(nativeToken!=null&&!"revoking".equals(this.phase));disconnect.setEnabled(!closing);unknownExit.setVisible("fenced".equals(this.phase)&&nativeToken!=null);dialog.pack();}});}
    private void showFailure(Throwable error){revokeInspection("gui-controller-fault");String message=String.valueOf(error.getMessage());lastError=Collections.unmodifiableMap(Bridge.map("type",error.getClass().getSimpleName(),"message",message.substring(0,Math.min(1000,message.length()))));System.err.println("Codex GUI control refused: "+lastError);remoteAllowed=false;if(nativeToken!=null&&bridge!=null)try{bridge.requestLocalGuiTakeover("gui-controller-fault");}catch(Exception ignored){}publish(nativeToken==null?"local":"fenced","Control refused: "+error.getClass().getSimpleName()+": "+String.valueOf(error.getMessage()));if(nativeToken==null)setModal(false);}
    private void setModal(boolean modal){SwingUtilities.invokeLater(()->{if(closed)return;dialog.setVisible(false);dialog.setModalityType(modal?Dialog.ModalityType.APPLICATION_MODAL:Dialog.ModalityType.MODELESS);dialog.setVisible(true);});}
    private interface Checked {void run()throws Exception;}
}
