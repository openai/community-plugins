/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.awt.Component;
import java.awt.Container;
import java.awt.Window;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.Callable;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.*;

/** Focused native Swing label/context/one-use callback test. No actual recovery,
 * operator authentication, desktop gesture or remote submission is exercised. */
public final class NativeSensingContinuationDialogTest {
    static int checks;
    static Map<String,Object> map(Object...pairs){Map<String,Object>m=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)m.put((String)pairs[i],pairs[i+1]);return m;}
    static void check(boolean value,String why){checks++;if(!value)throw new AssertionError(why);}
    static Map<String,Object> task(){return map("task_id",UUID.randomUUID().toString(),"recovery_kind","continue-faulted-job-replacement","expires_at",Instant.now().plusSeconds(30).toString(),"expires_in_ms",30000,"fault_set_sha256","a".repeat(64),"snapshot",map("context",map("replacement_attempt_id",UUID.randomUUID().toString(),"config_revision","cfg-4"),"source",map("source_id","source-new","state","invalid-read"),"replacement",map("progress",map("material",List.of(map("old_load_id","completed-tray","phase","completed"),map("old_load_id","pending-tray","phase","pending")),"prior_disposition","adopted")),"faults",List.of(map("observation_id","retained-fault","state","outcome_unknown")),"proposed_effects",List.of("adopt completed-tray without another reset","replace pending-tray with a fresh load","repair source and perform fresh probes")));}
    @SuppressWarnings("unchecked")static Map<String,Object> object(Object raw){return(Map<String,Object>)raw;}
    static void reject(Map<String,Object> task,String why){try{NativeSensingReconciliationDialog.validateTask(task);throw new AssertionError("Accepted "+why);}catch(IllegalArgumentException expected){checks++;}}
    static Component find(Component c,String name){if(name.equals(c.getName()))return c;if(c instanceof Container)for(Component child:((Container)c).getComponents()){Component result=find(child,name);if(result!=null)return result;}return null;}
    static <T>T edt(Callable<T> action)throws Exception{AtomicReference<T>result=new AtomicReference<>();AtomicReference<Throwable>failure=new AtomicReference<>();SwingUtilities.invokeAndWait(()->{try{result.set(action.call());}catch(Throwable error){failure.set(error);}});if(failure.get() instanceof Error)throw(Error)failure.get();if(failure.get() instanceof Exception)throw(Exception)failure.get();return result.get();}
    public static void main(String[]args)throws Exception{
        int exit=0;try{
            Map<String,Object> input=task(),frozen=NativeSensingReconciliationDialog.validateTask(input);check(NativeSensingReconciliationDialog.actionLabel(frozen).equals("Continue this replacement and verify sensing"),"Exact continuation action label");
            object(object(input.get("snapshot")).get("source")).put("source_id","changed-after-review");check("source-new".equals(object(object(frozen.get("snapshot")).get("source")).get("source_id")),"Source snapshot detached before presentation");
            for(String key:List.of("context","source","replacement","proposed_effects")){Map<String,Object>missing=task();object(missing.get("snapshot")).remove(key);reject(missing,"missing continuation snapshot "+key);}
            for(String key:List.of("source","replacement")){Map<String,Object>empty=task();object(empty.get("snapshot")).put(key,Map.of());reject(empty,"empty continuation snapshot "+key);}
            AtomicInteger submits=new AtomicInteger(),cancels=new AtomicInteger();NativeSensingReconciliationDialog form=edt(()->new NativeSensingReconciliationDialog(null,frozen,()->{check(SwingUtilities.isEventDispatchThread(),"Local callback on EDT");submits.incrementAndGet();},cancels::incrementAndGet));
            edt(()->{form.setVisible(true);check(form.isShowing(),"Actual continuation form displayed");JTextArea context=(JTextArea)find(form,"sensing_reconciliation.context");check(!context.isEditable(),"Full captured scope is read-only");for(String token:List.of("cfg-4","source-new","completed-tray","pending-tray","adopted","retained-fault","fresh probes"))check(context.getText().contains(token),"Displayed exact snapshot token: "+token);check(!context.getText().contains("changed-after-review"),"Later input mutation does not alter displayed decision");
                JTextArea explanation=(JTextArea)find(form,"sensing_reconciliation.explanation");check(explanation.getText().contains("completed steps are adopted without repeating"),"Completed effects distinguished from pending replacement effects");check(explanation.getText().contains("Pending steps may create new loads"),"Pending effects visible");check(explanation.getText().contains("Fresh sensor repair, disposal and sensing checks follow the proposed effects"),"Source work remains bound to locally proposed effects");
                JButton submit=(JButton)find(form,"sensing_reconciliation.submit");check(submit.getText().equals("Continue this replacement and verify sensing"),"Actual button uses continuation label");submit.doClick();submit.doClick();check(submits.get()==1&&!submit.isEnabled(),"Local continuation callback is one-use");form.submissionFinished(new IllegalStateException("replacement scope changed"));submit.doClick();check(submits.get()==1&&!submit.isEnabled(),"Failure cannot repeat completed effects from a spent form");((JButton)find(form,"sensing_reconciliation.cancel")).doClick();check(cancels.get()==1,"Local close callback preserved");form.dispose();return null;});
            if(args.length>1){Path directory=Path.of(args[1]);Files.createDirectory(directory);Files.writeString(directory.resolve("proof.json"),new com.google.gson.Gson().toJson(map("passed",true,"assertions",checks,"actual_swing_component",true,"native_recovery_execution",false,"desktop_gestures",false,"remote_submission",false,"hardware_qualified",false))+"\n");}
            System.out.println("NATIVE_SENSING_CONTINUATION_DIALOG_PASS "+checks+" assertions");
        }catch(Throwable error){error.printStackTrace();exit=1;}finally{edt(()->{for(Window window:Window.getWindows())window.dispose();return null;});}System.exit(exit);
    }
}
