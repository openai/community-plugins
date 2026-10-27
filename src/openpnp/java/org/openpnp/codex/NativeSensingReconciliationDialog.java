/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.awt.BorderLayout;
import java.awt.Dialog;
import java.awt.Dimension;
import java.awt.Window;
import java.awt.event.WindowAdapter;
import java.awt.event.WindowEvent;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.swing.BorderFactory;
import javax.swing.JButton;
import javax.swing.JDialog;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTextArea;
import javax.swing.SwingUtilities;
import javax.swing.Timer;

/** Local, one-use simulator intervention gesture. Contains no remote submission path or observation editor. */
final class NativeSensingReconciliationDialog extends JDialog {
    private final Map<String,Object> task;
    private final Runnable submitAction,cancelAction;
    private final long deadline;
    private final AtomicBoolean consumed=new AtomicBoolean(),cancelled=new AtomicBoolean();
    private final JButton submit,cancel=new JButton("Close recovery task");
    private final JTextArea message=new JTextArea();
    private final Timer expiry;

    NativeSensingReconciliationDialog(Window owner,Map<String,Object> task,Runnable submitAction,Runnable cancelAction) {
        super(owner,"Codex — simulator sensing recovery",Dialog.ModalityType.MODELESS);
        requireEdt();this.task=validateTask(task);this.submitAction=java.util.Objects.requireNonNull(submitAction);this.cancelAction=java.util.Objects.requireNonNull(cancelAction);
        deadline=deadlineNanos(this.task);
        submit=new JButton(actionLabel(this.task));submit.setName("sensing_reconciliation.submit");cancel.setName("sensing_reconciliation.cancel");
        setDefaultCloseOperation(DO_NOTHING_ON_CLOSE);
        addWindowListener(new WindowAdapter(){@Override public void windowClosing(WindowEvent event){cancel();}});
        JPanel content=new JPanel(new BorderLayout(10,10));content.setBorder(BorderFactory.createEmptyBorder(12,12,12,12));
        JTextArea explanation=readonly("Controlled simulator only. This local action selects the exact intervention below.\n"+
            "Synthetic readings do not establish physical pressure or independently inspected placement. Original failed and unknown outcomes remain recorded.\n"+
            "The local gesture is not an authenticated operator identity. This task can be submitted once; expiry, changed state or local takeover revokes it."+
            continuationExplanation(this.task));
        explanation.setName("sensing_reconciliation.explanation");explanation.setRows(isContinuation(this.task)?7:4);content.add(explanation,BorderLayout.NORTH);
        JTextArea context=readonly(new com.google.gson.GsonBuilder().setPrettyPrinting().serializeNulls().create().toJson(this.task));context.setName("sensing_reconciliation.context");
        JScrollPane scroll=new JScrollPane(context);scroll.setBorder(BorderFactory.createTitledBorder("Exact faults, affected scope, proposed native actions and prior-outcome disposition"));content.add(scroll,BorderLayout.CENTER);
        JPanel bottom=new JPanel(new BorderLayout(8,8));message.setEditable(false);message.setLineWrap(true);message.setWrapStyleWord(true);message.setOpaque(false);message.setRows(3);message.setName("sensing_reconciliation.message");
        message.setText("Expires at "+this.task.get("expires_at")+". Review the complete task before choosing the displayed action.");bottom.add(message,BorderLayout.CENTER);
        JPanel buttons=new JPanel();buttons.add(submit);buttons.add(cancel);bottom.add(buttons,BorderLayout.SOUTH);content.add(bottom,BorderLayout.SOUTH);
        submit.addActionListener(e->submit());cancel.addActionListener(e->cancel());
        expiry=new Timer(100,e->{if(expired())expire();});expiry.setRepeats(true);expiry.start();
        setContentPane(content);setMinimumSize(new Dimension(740,520));setSize(1050,760);setLocationRelativeTo(owner);
    }
    private void submit(){
        requireEdt();if(expired()){expire();return;}if(cancelled.get()||!consumed.compareAndSet(false,true))return;
        submit.setEnabled(false);message.setText("The one-use intervention has been submitted. Waiting for native execution and its durable receipt…");
        try{submitAction.run();}catch(Throwable failure){submissionFinished(failure);}
    }
    /** Receipt reporting never re-enables an already consumed form. A failed attempt requires a newly reviewed task. */
    void submissionFinished(Throwable failure){
        requireEdt();if(failure==null){dispose();return;}
        consumed.set(true);submit.setEnabled(false);
        Throwable cause=failure;while((cause instanceof java.util.concurrent.CompletionException||cause instanceof java.util.concurrent.ExecutionException)&&cause.getCause()!=null)cause=cause.getCause();
        String detail=cause.getMessage();if(detail==null||detail.isBlank())detail=cause.getClass().getSimpleName();
        message.setText("Recovery did not establish readiness: "+detail.substring(0,Math.min(detail.length(),1000))+"\nThis task is spent. Preserve the operation receipt and obtain a fresh task after resolving its reported dependencies.");
    }
    private void expire(){requireEdt();expiry.stop();submit.setEnabled(false);message.setText("This recovery task expired. Its prior faults and any submitted operation remain unresolved until the Bridge reports otherwise.");cancel();}
    private void cancel(){requireEdt();submit.setEnabled(false);if(cancelled.compareAndSet(false,true))cancelAction.run();}
    private boolean expired(){return System.nanoTime()-deadline>=0;}
    @Override public void dispose(){if(expiry!=null)expiry.stop();super.dispose();}
    static String actionLabel(Map<String,Object> task){
        Object kind=task.get("recovery_kind");if("restore-sensing-readiness".equals(kind))return "Repair simulator sensor and verify";
        if("replace-faulted-job-attempt".equals(kind))return "Dispose, retire this attempt and replace its board/material";
        if(isContinuation(task))return "Continue this replacement and verify sensing";
        if(isRestart(task))return "Reattach inactive replacement and observe restart state";
        throw new IllegalArgumentException("Unsupported simulator sensing recovery kind");
    }
    private static boolean isContinuation(Map<String,Object> task){return "continue-faulted-job-replacement".equals(task.get("recovery_kind"));}
    private static boolean isRestart(Map<String,Object> task){return NativeSensingReconciliation.RESTART_KIND.equals(task.get("recovery_kind"));}
    private static String continuationExplanation(Map<String,Object> task){return isRestart(task)?
        "\nThis restart task reattaches inactive job graphs and records fresh load/source observations. A source is installed only under the new native restart permit.\nPrior failed and unknown outcomes remain recorded. Reattachment selects an inactive original job. It does not initialize a job, grant execution readiness or confirm physical load presence.":isContinuation(task)?
        "\nReview the retained replacement below: completed steps are adopted without repeating their board or material replacement effects. Pending steps may create new loads as listed.\nFresh sensor repair, disposal and sensing checks follow the proposed effects for this new local decision. Earlier unknown outcomes remain unknown.":"";}
    static long deadlineNanos(Map<String,Object> task){
        long remaining=lifetimeMillis(task),wallRemaining=java.time.Duration.between(Instant.now(),Instant.parse((String)task.get("expires_at"))).toMillis();
        return System.nanoTime()+TimeUnit.MILLISECONDS.toNanos(Math.max(0,Math.min(remaining,wallRemaining)));
    }
    static long lifetimeMillis(Map<String,Object> task){
        Object value=task.get("expires_in_ms");if(!(value instanceof Number))throw new IllegalArgumentException("Recovery task needs its bounded remaining lifetime");
        try{long n=new BigDecimal(value.toString()).longValueExact();if(n<1||n>300000)throw new IllegalArgumentException("Recovery task lifetime must be 1 to 300000 milliseconds");return n;}
        catch(ArithmeticException|NumberFormatException e){throw new IllegalArgumentException("Recovery task lifetime must be an exact integer",e);}
    }
    static Map<String,Object> validateTask(Map<String,Object> source){
        Map<String,Object> value=NativeInspectionDialog.immutableMap(source);Object id=value.get("task_id");
        if(!(id instanceof String)||!UUID.fromString((String)id).toString().equals(id))throw new IllegalArgumentException("Recovery task ID must be a canonical UUID");
        actionLabel(value);lifetimeMillis(value);
        if(!(value.get("expires_at") instanceof String))throw new IllegalArgumentException("Recovery task needs an explicit expiry");Instant.parse((String)value.get("expires_at"));
        if(!(value.get("fault_set_sha256") instanceof String)||!((String)value.get("fault_set_sha256")).matches("[a-f0-9]{64}"))throw new IllegalArgumentException("Recovery task needs an exact fault-set SHA-256");
        if(!(value.get("snapshot") instanceof Map))throw new IllegalArgumentException("Recovery task needs its immutable affected scope");
        Map<?,?> snapshot=(Map<?,?>)value.get("snapshot");
        if(!(snapshot.get("context") instanceof Map))throw new IllegalArgumentException("Recovery task needs exact machine/source/owner context");
        if(isContinuation(value)||isRestart(value))for(String key:List.of("source","replacement"))if(!(snapshot.get(key) instanceof Map)||((Map<?,?>)snapshot.get(key)).isEmpty())throw new IllegalArgumentException("Replacement recovery task needs its complete "+key+" snapshot");
        for(String key:List.of("faults","proposed_effects"))if(!(snapshot.get(key) instanceof List)||((List<?>)snapshot.get(key)).isEmpty())throw new IllegalArgumentException("Recovery task needs nonempty "+key);
        String encoded=new com.google.gson.GsonBuilder().serializeNulls().create().toJson(value);
        if(encoded.length()>262144)throw new IllegalArgumentException("Recovery form context exceeds 256 KiB of characters; do not truncate its affected scope");
        return value;
    }
    private static JTextArea readonly(String text){JTextArea result=new JTextArea(text);result.setEditable(false);result.setLineWrap(true);result.setWrapStyleWord(true);result.setCaretPosition(0);return result;}
    private static void requireEdt(){if(!SwingUtilities.isEventDispatchThread())throw new IllegalStateException("Sensing recovery form requires EDT");}
}
