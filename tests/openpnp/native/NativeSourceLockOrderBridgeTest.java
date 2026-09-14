/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeSensingReconciliationBridgeTest.*;
import java.lang.management.*;
import java.lang.reflect.*;
import java.io.IOException;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.concurrent.locks.LockSupport;
import org.openpnp.model.Configuration;

/** Actual source repair and capabilities readers; test-only scheduling delay around the real
 * local guard. All journal bytes and native authority checks are delegated unchanged. A proven monitor cycle terminates
 * this isolated simulator with an explicit failing exit rather than hanging cleanup. */
public final class NativeSourceLockOrderBridgeTest {
    static final ThreadMXBean MX=ManagementFactory.getThreadMXBean();
    static final AtomicBoolean hooked=new AtomicBoolean();
    static final AtomicReference<Throwable> readerFailure=new AtomicReference<>();
    static final CountDownLatch hookReached=new CountDownLatch(1);
    static final List<Map<String,Object>> schedule=Collections.synchronizedList(new ArrayList<>());
    static volatile Thread reader,statusReader,nativeThread;
    static volatile boolean readerReturned,statusReturned;
    static String targetName;
    static Object target;
    static Field field(Class<?> type,String name)throws Exception {Field f=type.getDeclaredField(name);f.setAccessible(true);return f;}
    static Object get(Object owner,String name)throws Exception {return field(owner.getClass(),name).get(owner);}
    static Map<String,Object> info(ThreadInfo value) {
        if(value==null)return Bridge.map("absent",true);
        List<Object> held=new ArrayList<>();for(MonitorInfo m:value.getLockedMonitors())held.add(Bridge.map("class",m.getClassName(),"identity",m.getIdentityHashCode(),"frame",String.valueOf(m.getLockedStackFrame())));
        return Bridge.map("id",value.getThreadId(),"name",value.getThreadName(),"state",String.valueOf(value.getThreadState()),"waiting_monitor",String.valueOf(value.getLockInfo()),"waiting_monitor_identity",value.getLockInfo()==null?null:value.getLockInfo().getIdentityHashCode(),"owner_id",value.getLockOwnerId(),"owner_name",value.getLockOwnerName(),"held_monitors",held,"stack",Arrays.stream(value.getStackTrace()).map(Object::toString).toArray());
    }
    static ThreadInfo info(Thread thread) {return MX.getThreadInfo(new long[]{thread.getId()},true,true)[0];}
    static boolean holds(ThreadInfo ti,Object value) {if(ti==null)return false;for(MonitorInfo m:ti.getLockedMonitors())if(m.getIdentityHashCode()==System.identityHashCode(value)&&m.getClassName().equals(value.getClass().getName()))return true;return false;}
    static boolean sourceGuardInstalled;
    static void installSchedule()throws Exception {
        field(Bridge.class,"journal").set(bridge,new ForceTap((FileChannel)get(bridge,"journal")));
    }
    static void wrapSourceGuard()throws Exception {
        if(sourceGuardInstalled)return;
        target=field(NativeVacuumSources.class,"fixture").get(null);
        Object intervention=get(target,"intervention");
        Field guardField=field(NativeVacuumSources.SourceIntervention.class,"guard");
        NativeVacuumSensing.Guard original=(NativeVacuumSensing.Guard)guardField.get(intervention);
        guardField.set(intervention,(NativeVacuumSensing.Guard)()->{
            if(config.getMachine().isTask(Thread.currentThread())&&Thread.holdsLock(target)&&hooked.compareAndSet(false,true)) {
                nativeThread=Thread.currentThread();Object pending=get(bridge,"pendingSensingReconciliation");String task=(String)get(pending,"id");
                boolean bridgeHeld=Thread.holdsLock(bridge);
                reader=new Thread(()->{try{Map<String,Object> response=map(bridge.call("openpnp_get_capabilities",object()));check(Boolean.FALSE.equals(map(response.get("sensing_reconciliation")).get("restart_request_available")),"Concurrent capabilities read observes current source without restart admission");readerReturned=true;}catch(Throwable failure){readerFailure.compareAndSet(null,failure);}},"recovery-lock-order-reconciliation-reader");
                reader.setDaemon(true);reader.start();long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);ThreadInfo waiting=null;
                while(System.nanoTime()<until){waiting=info(reader);if(waiting!=null&&waiting.getThreadState()==Thread.State.BLOCKED)break;LockSupport.parkNanos(TimeUnit.MILLISECONDS.toNanos(1));}
                if(waiting==null||waiting.getThreadState()!=Thread.State.BLOCKED)throw new AssertionError("Reader did not reach the controlled lock boundary");
                Object expected=bridgeHeld?bridge:target;
                check(waiting.getLockInfo().getIdentityHashCode()==System.identityHashCode(expected),"Reader waits on exact expected monitor at controlled schedule boundary");
                check(waiting.getLockOwnerId()==nativeThread.getId(),"Actual native executor owns the reader's contended monitor");
                check(bridgeHeld?holds(info(nativeThread),bridge):holds(waiting,bridge),"Bridge ownership is measured on actual contender, not inferred from polling timing");
                schedule.add(Bridge.map("target",targetName,"native_holds_bridge",bridgeHeld,"native",info(info(nativeThread)),"reader",info(waiting),"delegated_guard","actual local recovery guard"));
                statusReader=new Thread(()->{try{Map<String,Object> response=map(bridge.call("openpnp_get_status",object()));check(response.containsKey("config_revision"),"Concurrent status read returns an actual Bridge snapshot");statusReturned=true;}catch(Throwable failure){readerFailure.compareAndSet(null,failure);}},"recovery-lock-order-status-reader");
                statusReader.setDaemon(true);statusReader.start();hookReached.countDown();
            }
            original.check();
        });sourceGuardInstalled=true;
    }
    /** Each write and force delegates unchanged. A known forced repair intent provides a
     * scheduling point to wrap its existing guard before the native repair resumes. */
    static final class ForceTap extends FileChannel {
        final FileChannel delegate;String last="";
        ForceTap(FileChannel delegate){this.delegate=delegate;}
        public int write(ByteBuffer src)throws IOException{ByteBuffer b=src.duplicate();byte[] bytes=new byte[b.remaining()];b.get(bytes);last=new String(bytes,StandardCharsets.UTF_8);return delegate.write(src);}
        public void force(boolean metadata)throws IOException{delegate.force(metadata);if(!sourceGuardInstalled&&last.contains("sensing_source_intervention_intent")){try{Map<String,Object> event=NativeJournalJson.parseObject(last);if("sensing_source_intervention_intent".equals(event.get("type")))wrapSourceGuard();}catch(Exception failure){throw new IOException(failure);}}}
        public int read(ByteBuffer b)throws IOException{return delegate.read(b);}public long read(ByteBuffer[] b,int o,int l)throws IOException{return delegate.read(b,o,l);}
        public long write(ByteBuffer[] b,int o,int l)throws IOException{return delegate.write(b,o,l);}public int read(ByteBuffer b,long p)throws IOException{return delegate.read(b,p);}public int write(ByteBuffer b,long p)throws IOException{return delegate.write(b,p);}
        public long position()throws IOException{return delegate.position();}public FileChannel position(long p)throws IOException{delegate.position(p);return this;}public long size()throws IOException{return delegate.size();}public FileChannel truncate(long s)throws IOException{delegate.truncate(s);return this;}
        public long transferTo(long p,long c,WritableByteChannel t)throws IOException{return delegate.transferTo(p,c,t);}public long transferFrom(ReadableByteChannel s,long p,long c)throws IOException{return delegate.transferFrom(s,p,c);}
        public MappedByteBuffer map(MapMode m,long p,long s)throws IOException{return delegate.map(m,p,s);}public FileLock lock(long p,long s,boolean sh)throws IOException{return delegate.lock(p,s,sh);}public FileLock tryLock(long p,long s,boolean sh)throws IOException{return delegate.tryLock(p,s,sh);}
        protected void implCloseChannel()throws IOException{delegate.close();}
    }
    static void deadlock(long[] ids)throws Exception {
        Set<Long> found=new HashSet<>();for(long id:ids)found.add(id);
        if(nativeThread==null||reader==null||!found.contains(nativeThread.getId())||!found.contains(reader.getId()))throw new AssertionError("Unexpected deadlock outside controlled native/read pair");
        ThreadInfo n=info(nativeThread),r=info(reader);
        check(n.getLockOwnerId()==reader.getId()&&r.getLockOwnerId()==nativeThread.getId(),"Exact native/read monitor ownership cycle detected");
        check(n.getLockInfo().getIdentityHashCode()==System.identityHashCode(bridge)&&r.getLockInfo().getIdentityHashCode()==System.identityHashCode(target),"Deadlock is Bridge-to-selected-ledger inversion");
        List<Object> dump=new ArrayList<>();for(ThreadInfo t:MX.dumpAllThreads(true,true))dump.add(info(t));
        write("lock-order-thread-dump.json",dump);
        write("lock-order-proof.json",Bridge.map("passed",false,"deterministic_deadlock",true,"target",targetName,"schedule",schedule,"deadlocked_threads",Arrays.stream(ids).boxed().toArray(),"checks",checks,"assertions",checks.size(),"pid",ProcessHandle.current().pid(),"intentional_halt_exit",85,"physical_qualification",false,"scope","Real native source guard blocked by a real capabilities reader; test only delays and delegates the original guard"));
        System.err.println("DETERMINISTIC_RECOVERY_LOCK_INVERSION "+targetName);System.err.flush();Runtime.getRuntime().halt(85);
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=3||!"source".equals(args[2]))throw new IllegalArgumentException("Expected sample root, new state directory, source");
        targetName=args[2];state=Path.of(args[1]).toAbsolutePath();Files.createDirectory(state);Map<String,Object> proof=new LinkedHashMap<>();int exit=0;
        try {
            Path configDir=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");Map<String,Object> prepared=GuiSensingFixture.prepare(configDir,manifest,"invalid-read");
            Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();
            Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),"invalid-read"));write("fixture-attestation.json",claimed);
            Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);local=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));
            GuiOwnership restartCapable=(GuiOwnership)Proxy.newProxyInstance(GuiOwnership.class.getClassLoader(),new Class<?>[]{GuiOwnership.class},(proxy,method,arguments)->{
                if(method.getName().equals("supportsSensingRestart"))return true;
                try{return method.invoke(local,arguments);}catch(InvocationTargetException error){throw error.getCause();}
            });
            bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",restartCapable);installSchedule();
            FutureTask<Void> workload=new FutureTask<>(()->{exercise(proof);return null;});Thread worker=new Thread(workload,"recovery-lock-order-workload");worker.setDaemon(true);worker.start();
            long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(120);
            while(!workload.isDone()&&System.nanoTime()<deadline) {
                long[] dead=MX.findDeadlockedThreads();if(dead!=null)deadlock(dead);LockSupport.parkNanos(TimeUnit.MILLISECONDS.toNanos(5));
            }
            workload.get(1,TimeUnit.SECONDS);check(hookReached.getCount()==0,"Actual source repair reached fixture-held local guard under controlled scheduling");
            reader.join(5000);statusReader.join(5000);if(readerFailure.get()!=null)throw new AssertionError("Concurrent real reader failed",readerFailure.get());
            check(readerReturned&&statusReturned&&!reader.isAlive()&&!statusReader.isAlive(),"Both concurrent Bridge reads complete after native metadata transaction");
            check(MX.findDeadlockedThreads()==null,"No JVM monitor cycle remains after actual native continuation and placement");
            check(Boolean.TRUE.equals(schedule.get(0).get("native_holds_bridge")),"Native source repair acquired Bridge before fixture-held guard");proof.put("passed",true);
        } catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally {
            try{if(bridge!=null){if(exit!=0)bridge.recordGuiUnknownExit();bridge.close();}proof.put("bridge_closed",true);}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}
            try{if(local!=null)local.release();if(config!=null)config.getMachine().close();}catch(Throwable failure){proof.put("native_cleanup_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("target",targetName,"schedule",schedule,"checks",checks,"assertions",checks.size(),"public_calls",calls,"pid",ProcessHandle.current().pid(),"test_delay_only",true,"authority_answers_delegated_unchanged",true,"journal_bytes_and_forces_delegated_unchanged",true,"native_placements",0,"real_swing_qualified",false,"physical_qualification",false,"scope","Actual standalone Bridge invalid-read recovery, real source repair and probes, concurrent capabilities/status reads; optional restart capability label is test host declaration only, no restart admission, Swing or placement claim"));
            write("lock-order-proof.json",proof);System.out.println("NATIVE_RECOVERY_LOCK_ORDER_BRIDGE_RESULT "+JSON.toJson(proof));
        }
        System.exit(exit);
    }
}
