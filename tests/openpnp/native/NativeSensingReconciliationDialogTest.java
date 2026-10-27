/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.awt.Component;
import java.awt.Container;
import java.awt.Dialog;
import java.awt.Window;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.JButton;
import javax.swing.JDialog;
import javax.swing.JFrame;
import javax.swing.JTextArea;
import javax.swing.SwingUtilities;

/** Actual Swing component/EDT behavior, not native recovery or operator authentication qualification. */
public final class NativeSensingReconciliationDialogTest {
    private static int checks;
    public static void main(String[] args)throws Exception {
        int exit=0;
        try {
            Map<String,Object> original=task(30000);Map<String,Object> frozen=NativeSensingReconciliationDialog.validateTask(original);
            @SuppressWarnings("unchecked") Map<String,Object> snapshot=(Map<String,Object>)original.get("snapshot");snapshot.put("added_later","untrusted mutation");
            check(!((Map<?,?>)frozen.get("snapshot")).containsKey("added_later"),"snapshot deeply copied before presentation");
            try{frozen.put("recovery_kind","other");throw new AssertionError("mutable snapshot");}catch(UnsupportedOperationException expected){checks++;}
            for(String key:List.of("task_id","recovery_kind","expires_at","expires_in_ms","fault_set_sha256","snapshot")){Map<String,Object> broken=task(30000);broken.remove(key);reject(broken,"missing "+key);}
            for(Object n:List.of(0,-1,300001,1.5,Double.NaN,"1000")){Map<String,Object> broken=task(30000);broken.put("expires_in_ms",n);reject(broken,"invalid bounded remaining lifetime");}
            Map<String,Object> broken=task(30000);broken.put("recovery_kind","clear-all");reject(broken,"generic clear action refused");
            broken=task(30000);broken.put("task_id",UUID.randomUUID().toString().toUpperCase());reject(broken,"noncanonical task refused");
            broken=task(30000);broken.put("fault_set_sha256","A".repeat(64));reject(broken,"noncanonical fault digest refused");
            broken=task(30000);broken.put("snapshot",Map.of("faults",List.of(),"proposed_effects",List.of("repair")));reject(broken,"empty affected fault scope refused");
            broken=task(30000);broken.put("snapshot",Map.of("faults",List.of("f"),"proposed_effects",List.of()));reject(broken,"empty intervention effects refused");
            broken=task(30000);broken.put("oversized","x".repeat(262145));reject(broken,"oversized context rejected rather than truncated");
            AtomicInteger submits=new AtomicInteger(),cancels=new AtomicInteger(),parentClicks=new AtomicInteger();
            JDialog owner=edt(()->{JFrame frame=new JFrame("Sensing recovery component test");JDialog dialog=new JDialog(frame,"Local ownership",Dialog.ModalityType.APPLICATION_MODAL);JButton takeover=new JButton("Take local control");takeover.addActionListener(e->parentClicks.incrementAndGet());dialog.add(takeover);dialog.pack();SwingUtilities.invokeLater(()->dialog.setVisible(true));return dialog;});
            NativeSensingReconciliationDialog form=edt(()->new NativeSensingReconciliationDialog(owner,frozen,()->{check(SwingUtilities.isEventDispatchThread(),"gesture callback dispatched on EDT");submits.incrementAndGet();},cancels::incrementAndGet));
            edt(()->{form.setVisible(true);check(form.isShowing(),"actual local form displayed");check(form.getOwner()==owner,"form owned by local controller dialog");check(form.getModalityType()==Dialog.ModalityType.MODELESS,"local ownership controls remain accessible");
                JTextArea context=(JTextArea)find(form,"sensing_reconciliation.context");check(!context.isEditable(),"fault/scope/effect context is read-only");check(context.getText().contains("fault-A")&&context.getText().contains("source-old")&&context.getText().contains("repair_sensor_source")&&context.getText().contains("unknown-before"),"exact fault, source, effect and original unknown disposition displayed");check(!context.getText().contains("added_later"),"mutable source cannot revise displayed decision");
                JButton submit=(JButton)find(form,"sensing_reconciliation.submit");check(submit.getText().equals("Repair simulator sensor and verify"),"specific closed intervention label");submit.doClick();submit.doClick();check(submits.get()==1,"duplicate local gesture consumed once");check(!submit.isEnabled(),"submit disabled while pending");check(((JButton)find(form,"sensing_reconciliation.cancel")).isEnabled(),"close stays available while pending");((JButton)owner.getContentPane().getComponent(0)).doClick();check(parentClicks.get()==1,"parent takeover remains responsive during pending native callback");
                form.submissionFinished(new IllegalArgumentException("source generation changed"));check(!submit.isEnabled(),"failed callback cannot reuse spent local task");check(((JTextArea)find(form,"sensing_reconciliation.message")).getText().contains("source generation changed"),"specific failed dependency displayed");submit.doClick();check(submits.get()==1,"failure cannot repeat intervention");((JButton)find(form,"sensing_reconciliation.cancel")).doClick();((JButton)find(form,"sensing_reconciliation.cancel")).doClick();check(cancels.get()==1,"cancel callback once");form.dispose();return null;});
            AtomicInteger expires=new AtomicInteger(),expiredSubmits=new AtomicInteger();
            NativeSensingReconciliationDialog expired=edt(()->new NativeSensingReconciliationDialog(owner,task(30),expiredSubmits::incrementAndGet,expires::incrementAndGet));
            edt(()->{expired.setVisible(true);return null;});
            long deadline=System.nanoTime()+java.util.concurrent.TimeUnit.SECONDS.toNanos(3);while(expires.get()==0&&System.nanoTime()<deadline)Thread.sleep(20);
            edt(()->{check(expires.get()==1,"expiry cancels local authority without submission");((JButton)find(expired,"sensing_reconciliation.submit")).doClick();check(expiredSubmits.get()==0,"expired form cannot submit");expired.dispose();return null;});
            Map<String,Object> past=task(30000);past.put("expires_at",Instant.now().minusSeconds(1).toString());AtomicInteger pastSubmits=new AtomicInteger(),pastCancels=new AtomicInteger();
            NativeSensingReconciliationDialog pastForm=edt(()->new NativeSensingReconciliationDialog(owner,past,pastSubmits::incrementAndGet,pastCancels::incrementAndGet));
            edt(()->{((JButton)find(pastForm,"sensing_reconciliation.submit")).doClick();check(pastSubmits.get()==0&&pastCancels.get()==1,"absolute expiry cannot be extended by a stale remaining lifetime");pastForm.dispose();return null;});
            Map<String,Object> replacement=task(30000);replacement.put("recovery_kind","replace-faulted-job-attempt");check(NativeSensingReconciliationDialog.actionLabel(replacement).equals("Dispose, retire this attempt and replace its board/material"),"replacement label describes every compound disposition");
            NativeSensingReconciliationDialog succeeded=edt(()->new NativeSensingReconciliationDialog(owner,replacement,()->{},()->{}));edt(()->{succeeded.setVisible(true);succeeded.submissionFinished(null);check(!succeeded.isDisplayable(),"completed callback disposes local form");return null;});
            System.out.println("OPENPNP_NATIVE_SENSING_RECONCILIATION_DIALOG_RESULT "+new com.google.gson.Gson().toJson(map("checks",checks,"component_edt_test",true,"native_recovery_execution",false,"desktop_gestures",false,"physical_qualification",false)));
        }catch(Throwable failure){failure.printStackTrace();exit=1;}finally{edt(()->{for(Window window:Window.getWindows())window.dispose();return null;});}System.exit(exit);
    }
    private static Map<String,Object> task(long millis){return map("task_id",UUID.randomUUID().toString(),"recovery_kind","restore-sensing-readiness","expires_at",Instant.now().plusMillis(millis).toString(),"expires_in_ms",millis,"fault_set_sha256","a".repeat(64),"snapshot",map("context",map("machine_id","machine-A","config_revision","cfg-2","source_id","source-old"),"faults",new ArrayList<>(List.of(map("observation_id","fault-A","state","unknown-before"))),"proposed_effects",List.of(map("kind","repair_sensor_source"),map("kind","native-part-off-probe")),"prior_outcome_disposition","unknown-before remains unknown"));}
    private static void reject(Map<String,Object> task,String label){try{NativeSensingReconciliationDialog.validateTask(task);throw new AssertionError("accepted "+label);}catch(IllegalArgumentException expected){checks++;}}
    private static Component find(Component component,String name){if(name.equals(component.getName()))return component;if(component instanceof Container)for(Component child:((Container)component).getComponents()){Component found=find(child,name);if(found!=null)return found;}return null;}
    private static <T>T edt(Callable<T> action)throws Exception{AtomicReference<T> result=new AtomicReference<>();AtomicReference<Throwable> failure=new AtomicReference<>();SwingUtilities.invokeAndWait(()->{try{result.set(action.call());}catch(Throwable error){failure.set(error);}});if(failure.get() instanceof Exception)throw(Exception)failure.get();if(failure.get() instanceof Error)throw(Error)failure.get();return result.get();}
    private static Map<String,Object> map(Object...pairs){Map<String,Object> result=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);return result;}
    private static void check(boolean condition,String label){if(!condition)throw new AssertionError(label);checks++;}
}
