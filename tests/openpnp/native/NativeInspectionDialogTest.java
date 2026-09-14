/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.awt.Component;
import java.awt.Container;
import java.awt.Dialog;
import java.awt.Window;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.JButton;
import javax.swing.JDialog;
import javax.swing.JFrame;
import javax.swing.JTable;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import javax.swing.SwingUtilities;

/** Swing component/EDT qualification only: no desktop gestures, native job, or physical inspection. */
public final class NativeInspectionDialogTest {
    private static final AtomicInteger assertions=new AtomicInteger();
    public static void main(String[] args)throws Exception {
        int exit=0;
        try {
            Map<String,Object> placement=map("holder_instance_id","holder-A","placement_id","R1","part_id","DEMO_R","location",map("x",1.25,"y",2.5,"z",0.0,"rotation",90.0,"units","mm"),"native_placed",true);
            Map<String,Object> second=map("holder_instance_id","holder-A","placement_id","R2","part_id","DEMO_R","location",map("x",10.0,"y",20.0,"z",0.0,"rotation",0.0,"units","mm"),"native_placed",true);
            Map<String,Object> preview=map("task_id",UUID.randomUUID().toString(),"context",map("job_id","native-job-A","job_revision",7,"board_load_revision",3),"snapshot",map("board_side","bottom","required_count",2,"required_placements",new ArrayList<>(List.of(placement,second))));
            Map<String,Object> frozen=NativeInspectionDialog.immutableMap(preview); placement.put("placement_id","changed-after-copy");
            AtomicReference<Map<String,Object>> submitted=new AtomicReference<>();AtomicInteger submissions=new AtomicInteger();AtomicBoolean canceled=new AtomicBoolean(),takeover=new AtomicBoolean();
            JDialog owner=edt(()->{JFrame frame=new JFrame("inspection test owner");JDialog modal=new JDialog(frame,"ownership test",Dialog.ModalityType.APPLICATION_MODAL);JButton button=new JButton("Take local control");button.setName("test.takeover");button.addActionListener(e->takeover.set(true));modal.add(button);modal.pack();SwingUtilities.invokeLater(()->modal.setVisible(true));return modal;});
            NativeInspectionDialog form=edt(()->new NativeInspectionDialog(owner,frozen,values->{check(SwingUtilities.isEventDispatchThread(),"form emits bounded values on EDT");submitted.set(values);submissions.incrementAndGet();},()->canceled.set(true)));
            edt(()->{form.setVisible(true);check(form.getOwner()==owner,"inspector owned by ownership dialog");check(form.getModalityType()==Dialog.ModalityType.MODELESS,"inspector leaves ownership controls available");check(owner.getModalityType()==Dialog.ModalityType.APPLICATION_MODAL,"ownership parent remains application modal");check(form.isShowing(),"inspection child actually shown");return null;});
            JTable table=edt(()->(JTable)find(form,"inspection.observations"));
            edt(()->{
                check(table.getRowCount()==2,"all required placements shown");check("R1".equals(table.getValueAt(0,0)),"fixed placement identity survives mutable source change");
                check("unknown".equals(table.getValueAt(0,3)),"native placed does not infer presence");check("unknown".equals(table.getValueAt(0,4)),"native placed does not infer polarity");
                for(int i=5;i<10;i++){check("".equals(table.getValueAt(0,i)),"measurement starts blank");check(!table.isCellEditable(0,i),"unknown presence cannot accept numeric measurements");}
                ((JButton)find(form,"inspection.submit")).doClick();check(submissions.get()==0,"empty operator context cannot submit");
                ((JTextField)find(form,"inspection.operator_label")).setText("local component test");((JTextArea)find(form,"inspection.operator_note")).setText("Synthetic form values; no person or instrument was authenticated.");
                ((JTextField)find(form,"inspection.xy_tolerance")).setText("0.2");((JTextField)find(form,"inspection.rotation_tolerance")).setText("2");
                table.setValueAt("present",0,3);table.setValueAt("correct",0,4);
                ((JButton)find(form,"inspection.submit")).doClick();check(submissions.get()==0,"present placement needs explicit measurements and uncertainties");
                for(int i=5;i<10;i++)table.setValueAt("0",0,i);
                table.setValueAt("NaN",0,5);((JButton)find(form,"inspection.submit")).doClick();check(submissions.get()==0,"nonfinite measurements refused");table.setValueAt("0.0500000000000000000001",0,5);
                table.setValueAt("-0.1",0,8);((JButton)find(form,"inspection.submit")).doClick();check(submissions.get()==0,"negative uncertainty refused");table.setValueAt("0.01",0,8);
                table.setValueAt("present",1,3);table.setValueAt("correct",1,4);for(int i=5;i<10;i++)table.setValueAt("1",1,i);table.setValueAt("missing",1,3);
                check("unknown".equals(table.getValueAt(1,4))&&!table.isCellEditable(1,4),"missing presence clears and disables polarity observation");
                for(int i=5;i<10;i++)check("".equals(table.getValueAt(1,i)),"changing to missing clears prior numeric entry");
                ((JButton)find(form,"inspection.submit")).doClick();check(submissions.get()==1,"one explicit completed form submission");
                check(!((JButton)find(form,"inspection.submit")).isEnabled(),"duplicate submission disabled while pending");check(((JButton)find(form,"inspection.cancel")).isEnabled(),"close remains available while pending");
                return null;
            });
            AtomicBoolean heartbeat=new AtomicBoolean();edt(()->{heartbeat.set(true);((JButton)find(owner,"test.takeover")).doClick();return null;});
            check(heartbeat.get()&&takeover.get(),"pending submission leaves EDT and parent ownership action responsive");
            Map<String,Object> values=submitted.get();
            check(values.keySet().equals(java.util.Set.of("operator_label","operator_note","tolerances","records","artifact_refs")),"closed local submission top-level shape");
            @SuppressWarnings("unchecked") List<Map<String,Object>> records=(List<Map<String,Object>>)values.get("records");
            check(records.size()==2,"exact required coverage retained");check("R1".equals(records.get(0).get("placement_id"))&&"holder-A".equals(records.get(0).get("holder_instance_id")),"canonical identity copied, not editable");
            check(records.get(0).get("dx_mm").equals(new java.math.BigDecimal("0.0500000000000000000001")),"entered decimal residual preserved beyond binary precision");check(!records.get(1).containsKey("dx_mm"),"missing record excludes measurements");
            check(((List<?>)values.get("artifact_refs")).isEmpty(),"no invented evidence references");
            try{values.put("operator_label","changed");throw new AssertionError("submission mutable");}catch(UnsupportedOperationException expected){assertions.incrementAndGet();}
            try{records.get(0).put("dx_mm",9);throw new AssertionError("nested record mutable");}catch(UnsupportedOperationException expected){assertions.incrementAndGet();}
            edt(()->{form.submissionFinished(new IllegalArgumentException("Context changed before save"));check(((JButton)find(form,"inspection.submit")).isEnabled(),"asynchronous failure enables correction");check(((JTextArea)find(form,"inspection.message")).getText().contains("Context changed"),"failure presented without modal alert");((JButton)find(form,"inspection.cancel")).doClick();check(canceled.get(),"local close callback invoked");form.submissionFinished(null);check(!form.isDisplayable(),"successful completion disposes inspector");return null;});
            System.out.println("OPENPNP_NATIVE_INSPECTION_DIALOG_RESULT "+new com.google.gson.Gson().toJson(map("assertions",assertions.get(),"component_edt_test",true,"desktop_gestures",false,"native_job_execution",false,"physical_qualification",false)));
        }catch(Throwable failure){failure.printStackTrace();exit=1;}finally{edt(()->{for(Window window:Window.getWindows())window.dispose();return null;});}System.exit(exit);
    }
    private static Component find(Component component,String name){if(name.equals(component.getName()))return component;if(component instanceof Container)for(Component child:((Container)component).getComponents()){Component found=find(child,name);if(found!=null)return found;}return null;}
    private static <T>T edt(Callable<T> callable)throws Exception{if(SwingUtilities.isEventDispatchThread())return callable.call();AtomicReference<T> result=new AtomicReference<>();AtomicReference<Throwable> failure=new AtomicReference<>();SwingUtilities.invokeAndWait(()->{try{result.set(callable.call());}catch(Throwable error){failure.set(error);}});if(failure.get() instanceof Exception)throw(Exception)failure.get();if(failure.get() instanceof Error)throw(Error)failure.get();return result.get();}
    private static Map<String,Object> map(Object...pairs){Map<String,Object> result=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);return result;}
    private static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);assertions.incrementAndGet();}
}
