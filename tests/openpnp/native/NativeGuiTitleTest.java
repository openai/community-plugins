/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.awt.Window;
import java.beans.PropertyChangeListener;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.SwingUtilities;
import org.openpnp.gui.MainFrame;
import org.openpnp.model.Configuration;
import org.openpnp.model.Job;
import org.openpnp.spi.base.AbstractMachine;
import org.openpnp.spi.base.ExternalExecutionControl;
import org.openpnp.machine.reference.driver.NullDriver;

/** Actual Swing/native executor fixture; no Bridge, controller, camera capture or job execution. */
public final class NativeGuiTitleTest {
    static MainFrame frame; static Configuration config; static AbstractMachine machine;
    static final List<Map<String,Object>> events=Collections.synchronizedList(new ArrayList<>());
    static final List<String> checks=new ArrayList<>(); static final Map<String,Object> observations=new LinkedHashMap<>();
    static Object owner; static ExternalExecutionControl control; static int nativeTasks;
    static Map<String,Object> map(Object...pairs){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)m.put((String)pairs[i],pairs[i+1]);return m;}
    static void check(boolean value,String label){if(!value)throw new AssertionError(label);checks.add(label);}
    static <T>T edt(Callable<T> action)throws Exception {if(SwingUtilities.isEventDispatchThread())return action.call();AtomicReference<T> result=new AtomicReference<>();AtomicReference<Throwable> failure=new AtomicReference<>();SwingUtilities.invokeAndWait(()->{try{result.set(action.call());}catch(Throwable x){failure.set(x);}});if(failure.get()!=null){if(failure.get() instanceof Exception)throw(Exception)failure.get();throw(Error)failure.get();}return result.get();}
    static <T>Future<T> submit(Callable<T> action)throws Exception{return control.withOwner(owner,()->machine.submit(()->{check(machine.isTask(Thread.currentThread()),"actual native executor owns title mutation "+(++nativeTasks));check(!SwingUtilities.isEventDispatchThread(),"native mutation runs off EDT "+nativeTasks);return action.call();},null,true));}
    static List<Map<String,Object>> snapshot(){synchronized(events){return new ArrayList<>(events);}}
    static void requireEdtEvents(String label){List<Map<String,Object>> rows=snapshot();observations.put(label,rows);check(!rows.isEmpty(),label+" observed actual JFrame title event");check(rows.stream().allMatch(x->Boolean.TRUE.equals(x.get("edt"))),label+" all JFrame title events on EDT");}
    public static void main(String[]args)throws Exception {
        if(args.length!=1)throw new IllegalArgumentException("new-owned-output-root");Path root=Paths.get(args[0]).toAbsolutePath();if(Files.exists(root))throw new IllegalArgumentException("new root required");Files.createDirectories(root);
        Throwable failure=null;List<String> cleanupErrors=new ArrayList<>();boolean machineClosed=false,windowsClosed=false;
        try {
            check("org.openpnp.codex.IsolatedPreferencesFactory".equals(System.getProperty("java.util.prefs.PreferencesFactory")),"isolated preferences explicitly selected");
            check(java.util.prefs.Preferences.userRoot().getClass().getName().endsWith("IsolatedPreferencesFactory$MemoryNode"),"actual nonpersistent preferences implementation");
            Path dir=root.resolve("config");Files.createDirectory(dir);Configuration.initialize(dir.toFile());config=Configuration.get();config.load();config.getMachine().setProperty("Welcome2_0_Dialog_Shown",true);config.save();config.getMachine().close();Configuration.initialize(dir.toFile());config=Configuration.get();
            frame=edt(()->{MainFrame f=new MainFrame(config);f.setVisible(true);return f;});machine=(AbstractMachine)config.getMachine();
            check(((org.openpnp.machine.reference.ReferenceMachine)machine).getDefaultDriver().getClass()==NullDriver.class,"only exact default simulator NullDriver admitted");check(!machine.isEnabled(),"machine remains disabled");
            check(edt(()->frame.isDisplayable()&&frame.isShowing()),"actual native GUI frame visible");
            PropertyChangeListener listener=e->{if("title".equals(e.getPropertyName()))events.add(map("title",e.getNewValue(),"old_title",e.getOldValue(),"edt",SwingUtilities.isEventDispatchThread(),"thread",Thread.currentThread().getName(),"native_task",machine.isTask(Thread.currentThread())));};
            edt(()->{frame.addPropertyChangeListener("title",listener);return null;});
            Job original=edt(()->{Job j=new Job();j.setFile(root.resolve("original.job.xml").toFile());j.setDirty(true);frame.getJobTab().setJob(j);events.clear();j.setDirty(false);return j;});
            requireEdtEvents("edt-clean-control");check(edt(()->frame.getTitle()).equals("OpenPnP - original.job.xml"),"EDT dirty=false title rendered");
            control=machine.getExternalExecutionControl();owner=control.claim("gui-title73-owned-native-fixture");events.clear();
            submit(()->{original.setDirty(true);return null;}).get(5,TimeUnit.SECONDS);edt(()->null);
            check(original.isDirty(),"native dirty flag committed");check(edt(()->frame.getTitle()).equals("OpenPnP - *original.job.xml"),"native dirty title displayed");requireEdtEvents("native-dirty");
            events.clear();submit(()->{original.setFile(root.resolve("renamed.job.xml").toFile());return null;}).get(5,TimeUnit.SECONDS);edt(()->null);
            check(edt(()->frame.getTitle()).equals("OpenPnP - *renamed.job.xml"),"native file title displayed");requireEdtEvents("native-file");
            edt(()->{original.setDirty(false);return null;});events.clear();
            CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);AtomicReference<Throwable> gateError=new AtomicReference<>();
            Job replacement=new Job();replacement.setFile(root.resolve("replacement.job.xml").toFile());replacement.setDirty(false);
            SwingUtilities.invokeLater(()->{entered.countDown();try{if(!release.await(5,TimeUnit.SECONDS))throw new AssertionError("bounded EDT gate timed out");frame.getJobTab().setJob(replacement);}catch(Throwable t){gateError.set(t);}});
            try {
                check(entered.await(2,TimeUnit.SECONDS),"EDT gate entered");
                submit(()->{original.setDirty(true);original.setFile(root.resolve("stale-queued.job.xml").toFile());return null;}).get(2,TimeUnit.SECONDS);
                check(release.getCount()==1,"native task completed while EDT remained gated");check(snapshot().isEmpty(),"off-EDT setters did not mutate frame while EDT gated");
            } finally {release.countDown();}
            edt(()->null);if(gateError.get()!=null)throw new AssertionError("EDT gate failed",gateError.get());
            check(edt(()->frame.getJobTab().getJob())==replacement,"new native job is current on EDT");
            check(edt(()->frame.getTitle()).equals("OpenPnP - replacement.job.xml"),"deferred title reads latest current job");requireEdtEvents("queued-latest-job");
            check(snapshot().stream().allMatch(x->"OpenPnP - replacement.job.xml".equals(x.get("title"))),"queued old-job events never publish stale title");
            events.clear();submit(()->{original.setDirty(false);original.setFile(root.resolve("detached.job.xml").toFile());return null;}).get(5,TimeUnit.SECONDS);edt(()->null);
            check(snapshot().isEmpty(),"detached old-job dirty/file changes do not update current title");check(!machine.isEnabled(),"no machine enable during all title cases");
            observations.put("final_title",edt(()->frame.getTitle()));observations.put("job_panel_origin",frame.getJobTab().getClass().getProtectionDomain().getCodeSource().getLocation().toString());
        } catch(Throwable t){failure=t;t.printStackTrace();}
        finally {
            try {if(control!=null&&owner!=null){long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(3);while((machine.isBusy()||control.getPendingTaskCount()!=0)&&System.nanoTime()<end)Thread.sleep(10);control.release(owner);}}catch(Throwable t){cleanupErrors.add("owner release: "+t);}
            try {if(config!=null){config.getMachine().close();machineClosed=true;}}catch(Throwable t){cleanupErrors.add("machine close: "+t);}
            try {edt(()->{for(Window w:Window.getWindows())w.dispose();return null;});windowsClosed=edt(()->Arrays.stream(Window.getWindows()).noneMatch(Window::isDisplayable));}catch(Throwable t){cleanupErrors.add("window disposal: "+t);}
        }
        boolean passed=failure==null&&cleanupErrors.isEmpty()&&machineClosed&&windowsClosed;
        Map<String,Object> result=map("passed",passed,"assertions",checks.size(),"checks",checks,"observations",observations,"last_title_events",snapshot(),"failure",failure==null?null:failure.toString(),"native_executor_title_tasks",nativeTasks,"native_machine_enabled",machine!=null&&machine.isEnabled(),"machine_closed",machineClosed,"all_owned_windows_disposed",windowsClosed,"cleanup_errors",cleanupErrors,"simulation_only",true,"physical_qualification",false,"desktop_gestures",false,"bridge_used",false);
        Files.writeString(root.resolve("result.json"),new Gson().toJson(result));System.out.println("OPENPNP_NATIVE_GUI_TITLE_RESULT "+new Gson().toJson(result));System.exit(passed?0:1);
    }
}
