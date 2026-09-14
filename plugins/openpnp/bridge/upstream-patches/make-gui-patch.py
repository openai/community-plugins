"""Generate the additive patch from the exact pinned stock source; never edit that source."""
from pathlib import Path
import difflib,sys
upstream=Path(sys.argv[1]);output=Path(__file__).with_name('gui-ownership.patch')
patches=[]
def emit(name,before,after):
 assert before!=after,name
 patches.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
def replace(text,old,new):
 assert text.count(old)==1,(old[:100],text.count(old))
 return text.replace(old,new)
name='src/main/java/org/openpnp/spi/base/AbstractMachine.java';before=(upstream/name).read_text();s=before
needle='    private synchronized boolean isQueueEmpty() {'
s=replace(s,needle,'''    // Additive simulator ownership API; no owner preserves stock submission behavior.
    private transient final ExternalExecutionControl externalExecutionControl = new ExternalExecutionControl(this);

    public final ExternalExecutionControl getExternalExecutionControl() {
        return externalExecutionControl;
    }

'''+needle)
s=replace(s,'                                    executor.remove(runnable);','''                                    if (executor.remove(runnable) && runnable instanceof java.util.concurrent.Future<?>) {
                                        ((java.util.concurrent.Future<?>) runnable).cancel(false);
                                    }''')
s=replace(s,'                                executor.shutdownNow();', '''                                for (Runnable queued : executor.shutdownNow()) {
                                    if (queued instanceof java.util.concurrent.Future<?>) ((java.util.concurrent.Future<?>) queued).cancel(false);
                                }''')
s=replace(s,'''                executor = new ThreadPoolExecutor(1, 1, 1, TimeUnit.SECONDS,
                        new LinkedBlockingQueue<>());''','''                executor = new ThreadPoolExecutor(1, 1, 1, TimeUnit.SECONDS,
                        new LinkedBlockingQueue<>()) {
                    @Override protected void afterExecute(Runnable task, Throwable failure) {
                        super.afterExecute(task, failure);
                        // Cancelled queued FutureTasks do not invoke the native wrapper's finally.
                        // Publish idle on the executor thread after every queue item drains.
                        if (!isShutdown() && AbstractMachine.this.isTask(Thread.currentThread()) && getQueue().isEmpty()) {
                            try { if (AbstractMachine.this.isBusy()) fireMachineBusy(false); }
                            finally { setTaskThread(null); }
                        }
                    }
                };''')
s=replace(s,'        return executor.submit(wrapper);','''        java.util.concurrent.FutureTask<T> admitted = externalExecutionControl.task(wrapper);
        try {
            executor.execute(admitted);
            return admitted;
        }
        catch (RuntimeException rejected) {
            admitted.cancel(false);
            throw rejected;
        }''')
emit(name,before,s)
name='src/main/java/org/openpnp/spi/base/ExternalExecutionControl.java'
s='''package org.openpnp.spi.base;

import java.util.concurrent.Callable;
import java.util.concurrent.FutureTask;
import java.util.concurrent.RejectedExecutionException;

/** Opaque ownership for cooperating in-process controllers. Not a Java security sandbox. */
public final class ExternalExecutionControl {
    public static final int API_VERSION = 1;
    public static final String PATCH_ID = "codex-gui-ownership-v1";
    private final AbstractMachine machine;
    private final ThreadLocal<Object> context = new ThreadLocal<>();
    private Object owner;
    private String label;
    private int pending;
    ExternalExecutionControl(AbstractMachine machine) { this.machine=machine; }

    public int getApiVersion() { return API_VERSION; }
    public synchronized Object claim(String label) {
        if (label==null || label.isEmpty() || label.length()>120) throw new IllegalArgumentException("Bounded owner label required");
        if (owner!=null || pending!=0 || machine.isBusy()) throw new IllegalStateException("Machine has an owner, running task or queued task");
        owner=new Object();this.label=label;return owner;
    }
    public synchronized boolean owns(Object token) { return token!=null && token==owner; }
    public synchronized String getOwnerLabel() { return label; }
    public synchronized int getPendingTaskCount() { return pending; }
    public synchronized void release(Object token) {
        require(token);
        if(pending!=0 || machine.isBusy()) throw new IllegalStateException("Ownership cannot release before all native tasks drain");
        owner=null;label=null;
    }
    private void require(Object token) {
        if(token==null || owner!=token) throw new RejectedExecutionException("Native execution ownership was revoked");
    }
    public <T> T withOwner(Object token, Callable<T> action) throws Exception {
        synchronized(this) { require(token); }
        Object prior=context.get();context.set(token);
        try { return action.call(); }
        finally { if(prior==null)context.remove();else context.set(prior); }
    }
    synchronized <T> FutureTask<T> task(Callable<T> action) {
        final Object admitted=context.get();
        if(owner!=null && admitted!=owner || owner==null && admitted!=null)
            throw new RejectedExecutionException("Another controller owns native machine execution");
        pending++;
        final boolean[] ticket={false,false}; // begun, released; guarded by this control
        return new FutureTask<T>(() -> {
            synchronized(ExternalExecutionControl.this) {
                if(ticket[1])throw new RejectedExecutionException("Native task was cancelled before entry");
                ticket[0]=true;
                if(owner!=admitted){ticket[1]=true;pending--;throw new RejectedExecutionException("Native execution owner changed before dispatch");}
            }
            Object prior=context.get();
            if(admitted==null)context.remove();else context.set(admitted);
            try { return action.call(); }
            finally {
                if(prior==null)context.remove();else context.set(prior);
                synchronized(ExternalExecutionControl.this){if(!ticket[1]){ticket[1]=true;pending--;}}
            }
        }) {
            @Override protected void done() {
                synchronized(ExternalExecutionControl.this){if(!ticket[0] && !ticket[1]){ticket[1]=true;pending--;}}
            }
        };
    }
}
'''
patches.extend(difflib.unified_diff([],s.splitlines(True),fromfile='/dev/null',tofile='b/'+name))
name='src/main/java/org/openpnp/gui/JobPanel.java';before=(upstream/name).read_text();s=before
s=replace(s,'    private State state = State.Stopped;','''    private State state = State.Stopped;
    private Object externalJobOwner;
    private boolean externalJobPublication;

    public String getExecutionState() { return state.name(); }
    public boolean hasExternalJobControl() { return externalJobOwner!=null; }
    public void claimExternalJobControl(Object token) {
        if(!SwingUtilities.isEventDispatchThread())throw new IllegalStateException("Job ownership requires EDT");
        if(token==null || externalJobOwner!=null || state!=State.Stopped)throw new IllegalStateException("GUI job is not stopped and unowned");
        externalJobOwner=token;updateJobActions();
    }
    public void releaseExternalJobControl(Object token) {
        if(!SwingUtilities.isEventDispatchThread())throw new IllegalStateException("Job ownership requires EDT");
        if(token==null || externalJobOwner!=token || state!=State.Stopped)throw new IllegalStateException("GUI job ownership cannot release");
        externalJobOwner=null;updateJobActions();
    }
    public void setExternalJob(Object token, Job next) {
        if(!SwingUtilities.isEventDispatchThread() || token==null || externalJobOwner!=token)
            throw new IllegalStateException("Only the external owner may publish the job on EDT");
        if(next==job){refresh();return;}
        externalJobPublication=true;
        try{setJob(next);}finally{externalJobPublication=false;}
    }
    private void requireLocalJobControl() {
        if(externalJobOwner!=null)throw new IllegalStateException("External controller owns the job; use its local takeover control first");
    }''')
s=replace(s,'    void setState(State newState) {','''    void setState(State newState) {
        if(externalJobOwner!=null && newState!=State.Stopped)requireLocalJobControl();''')
s=replace(s,'    public void setJob(Job job) {','''    public void setJob(Job job) {
        if(!externalJobPublication)requireLocalJobControl();''')
s=replace(s,'            this.job.getRootPanelLocation().getPanel().removeAllChildren();','''            if (!externalJobPublication) this.job.getRootPanelLocation().getPanel().removeAllChildren();''')
for signature in ['    public void jobStart() throws Exception {','    public void jobRun() {','    public void jobResume() {','    private void jobAbort() {']:
 s=replace(s,signature,signature+'\n        requireLocalJobControl();')
s=replace(s,'    private void updateTitle() {','''    private void updateTitle() {
        // Dirty/file notifications can originate on the native executor.
        // Read the latest current job only after returning to the event thread.
        if (!SwingUtilities.isEventDispatchThread()) {
            SwingUtilities.invokeLater(this::updateTitle);
            return;
        }''')
s=replace(s,'    private void updateJobActions() {','''    private void updateJobActions() {
        if(externalJobOwner!=null){startPauseResumeJobAction.setEnabled(false);stopJobAction.setEnabled(false);stepJobAction.setEnabled(false);return;}''')
emit(name,before,s)
name='src/main/java/org/openpnp/gui/MainFrame.java';before=(upstream/name).read_text();s=before
s=replace(s,'    public boolean quit() {','''    public boolean quit() {
        // The visible external-controller panel performs cooperative drain and ownership release.
        // Native processor ownership must not be bypassed by an application-menu Quit callback.
        if(jobPanel!=null && jobPanel.hasExternalJobControl())return false;''')
emit(name,before,s)
output.write_text(''.join(patches));print(output)
