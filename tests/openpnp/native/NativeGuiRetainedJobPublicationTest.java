/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.awt.Window;
import java.beans.PropertyChangeListener;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.util.*;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.*;
import org.openpnp.gui.MainFrame;
import org.openpnp.gui.JobPanel;
import org.openpnp.model.*;
import org.openpnp.spi.base.AbstractMachine;
import org.openpnp.spi.base.ExternalExecutionControl;

/** Real native GUI publication with authored native graph/history; no Bridge job execution. */
public final class NativeGuiRetainedJobPublicationTest {
    static int checks,refusals;static Path root;static Configuration config;static MainFrame frame;static Job old,fresh;static Object token;static int beforeRoots,afterRoots;static Map<String,Boolean> beforeHistory;
    interface Checked {void run()throws Exception;}
    static void check(boolean yes,String label){checks++;if(!yes)throw new AssertionError(label);}
    static void refuse(Checked action)throws Exception {try{action.run();throw new AssertionError("Unexpected publication admission");}catch(IllegalStateException expected){checks++;refusals++;}}
    static <T>T edt(Callable<T> action)throws Exception {AtomicReference<T> v=new AtomicReference<>();AtomicReference<Throwable> e=new AtomicReference<>();SwingUtilities.invokeAndWait(()->{try{v.set(action.call());}catch(Throwable t){e.set(t);}});if(e.get() instanceof Exception)throw(Exception)e.get();if(e.get() instanceof Error)throw(Error)e.get();return v.get();}
    static Map<String,Object> map(Object... pairs){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)m.put((String)pairs[i],pairs[i+1]);return m;}
    static Job job(String id)throws Exception {
        Job job=new Job();Board board=new Board();Placement p=new Placement(id+"-placed"),q=new Placement(id+"-unplaced");p.setPart(config.getParts().get(0));q.setPart(config.getParts().get(0));p.setLocation(new Location(LengthUnit.Millimeters,1,2,0,0));q.setLocation(new Location(LengthUnit.Millimeters,3,4,0,90));board.addPlacement(p);board.addPlacement(q);BoardLocation location=new BoardLocation(board);location.setId(id+"-board");job.addBoardOrPanelLocation(location);
        Panel nested=new Panel();BoardLocation repeated=new BoardLocation(board);repeated.setId(id+"-nested-board");nested.addChild(repeated);PanelLocation panel=new PanelLocation(nested);panel.setId(id+"-panel");job.addBoardOrPanelLocation(panel);
        job.storePlacedStatus(location,p.getId(),true);job.storePlacedStatus(location,q.getId(),false);job.storePlacedStatus(location,"opaque-old",false);job.setFile(root.resolve(id+".job.xml").toFile());job.setDirty(false);return job;
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=1)throw new IllegalArgumentException("Exclusive state path required");root=Path.of(args[0]);Files.createDirectory(root);int exit=0;Throwable failure=null;
        try {
            Path dir=root.resolve("config");Files.createDirectory(dir);Configuration.initialize(dir.toFile());config=Configuration.get();config.load();config.getMachine().setProperty("Welcome2_0_Dialog_Shown",true);config.save();config.getMachine().close();Configuration.initialize(dir.toFile());config=Configuration.get();frame=edt(()->{MainFrame f=new MainFrame(config);f.setVisible(true);return f;});
            old=job("original");fresh=job("replacement");List<PlacementsHolderLocation<?>> originalNodes=new ArrayList<>(old.getBoardAndPanelLocations());List<PlacementsHolderLocation<?>> freshNodes=new ArrayList<>(fresh.getBoardAndPanelLocations());beforeRoots=old.getRootPanelLocation().getPanel().getChildren().size();beforeHistory=new TreeMap<>(old.getPlacedStatusSnapshot());Map<String,Boolean> freshHistory=new TreeMap<>(fresh.getPlacedStatusSnapshot());
            check(beforeRoots==2&&originalNodes.size()==4,"Fixture has flat and nested native board instances");check(beforeHistory.values().contains(true)&&beforeHistory.values().contains(false)&&beforeHistory.size()==3,"Fixture preserves known true, false and opaque false history");
            JobPanel panel=frame.getJobTab();ExternalExecutionControl gate=((AbstractMachine)config.getMachine()).getExternalExecutionControl();token=edt(()->{Object t=gate.claim("Retained native publication test");panel.claimExternalJobControl(t);return t;});
            refuse(()->panel.setExternalJob(token,old));refuse(()->edt(()->{panel.setExternalJob(new Object(),old);return null;}));
            edt(()->{panel.setExternalJob(token,old);return null;});check(edt(panel::getJob)==old,"First external publication selects exact original native Job");
            Field field=JobPanel.class.getDeclaredField("titlePropertyChangeListener");field.setAccessible(true);PropertyChangeListener title=(PropertyChangeListener)field.get(panel);
            check(old.isListener("dirty",title)&&old.isListener("file",title),"Original receives current title listeners");
            refuse(()->edt(()->{panel.setJob(fresh);return null;}));check(edt(panel::getJob)==old,"Unowned local setter leaves current Job unchanged");
            edt(()->{panel.setExternalJob(token,fresh);return null;});afterRoots=old.getRootPanelLocation().getPanel().getChildren().size();
            check(edt(panel::getJob)==fresh,"Second external publication selects exact replacement native Job");
            check(beforeRoots==afterRoots&&originalNodes.equals(old.getBoardAndPanelLocations()),"Retained original native graph survives external replacement");
            check(beforeHistory.equals(old.getPlacedStatusSnapshot()),"Original complete true/false/opaque placed history is byte-equivalent as native map");
            check(freshNodes.equals(fresh.getBoardAndPanelLocations())&&freshHistory.equals(fresh.getPlacedStatusSnapshot()),"Publication does not alter replacement graph or history");
            check(!old.isListener("dirty",title)&&!old.isListener("file",title),"Old title/file subscriptions detach without destroying graph");
            check(fresh.isListener("dirty",title)&&fresh.isListener("file",title),"Current replacement owns title/file subscriptions");
            check(edt(frame::getTitle).contains("replacement.job.xml"),"Actual window title follows selected replacement");
            edt(()->{panel.setExternalJob(token,fresh);return null;});check(originalNodes.equals(old.getBoardAndPanelLocations())&&freshNodes.equals(fresh.getBoardAndPanelLocations()),"Repeated same selected Job only refreshes UI");
            edt(()->{panel.releaseExternalJobControl(token);return null;});gate.release(token);token=null;
            edt(()->{panel.setJob(new Job());return null;});check(fresh.getRootPanelLocation().getPanel().getChildren().isEmpty(),"Ordinary local document switch retains native destructive cleanup behavior");
            check(!fresh.isListener("dirty",title)&&!fresh.isListener("file",title),"Local switch detaches prior title subscriptions");
            check(originalNodes.equals(old.getBoardAndPanelLocations())&&beforeHistory.equals(old.getPlacedStatusSnapshot()),"Retained original remains untouched after unrelated local switch");
            check(!config.getMachine().isEnabled()&&!config.getMachine().isBusy()&&!config.getMachine().isHomed(),"GUI publication performs no enable, home or native job");
        }catch(Throwable t){failure=t;t.printStackTrace();exit=1;}finally{
            if(token!=null&&frame!=null){try{edt(()->{frame.getJobTab().releaseExternalJobControl(token);return null;});((AbstractMachine)config.getMachine()).getExternalExecutionControl().release(token);}catch(Throwable t){t.printStackTrace();}}
            try{if(config!=null)config.getMachine().close();}catch(Throwable t){t.printStackTrace();exit=1;}if(frame!=null)edt(()->{for(Window w:Window.getWindows())w.dispose();return null;});
            Map<String,Object> proof=map("passed",exit==0,"checks",checks,"refusals",refusals,"pid",ProcessHandle.current().pid(),"failure",failure==null?null:failure.toString(),"original_root_children_before",beforeRoots,"original_root_children_after",afterRoots,"original_history_before",beforeHistory,"original_history_after",old==null?null:old.getPlacedStatusSnapshot(),"actual_mainframe",true,"synthetic_native_job_history",true,"native_job_execution",false,"bridge_restart_integration",false,"hardware_qualified",false);
            Files.writeString(root.resolve("proof.json"),new Gson().toJson(proof)+"\n");System.out.println("NATIVE_GUI_RETAINED_PUBLICATION_RESULT "+new Gson().toJson(proof));
        }System.exit(exit);
    }
}
