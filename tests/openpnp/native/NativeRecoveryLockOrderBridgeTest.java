/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeFaultedSensingReplacementContinuationBridgeTest.*;
import java.lang.management.*;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.concurrent.locks.LockSupport;
import org.openpnp.model.Configuration;

/** Actual Bridge recovery and status readers; test-only scheduling delay around the real
 * terminal callback. No authority answer is replaced. A proven monitor cycle terminates
 * this isolated simulator with an explicit failing exit rather than hanging cleanup. */
public final class NativeRecoveryLockOrderBridgeTest {
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
    static void installSchedule()throws Exception {
        target=get(bridge,Map.of("lineage","jobLineage","material","materialLoads","boards","boardLoads").get(targetName));
        Object replacement=get(bridge,"faultedJobReplacement");Field callback=field(NativeFaultedJobReplacement.class,"terminalAuthority");
        NativeFaultedJobReplacement.TerminalAuthority original=(NativeFaultedJobReplacement.TerminalAuthority)callback.get(replacement);
        callback.set(replacement,(NativeFaultedJobReplacement.TerminalAuthority)operation->{
            if(config.getMachine().isTask(Thread.currentThread())&&Thread.holdsLock(target)&&hooked.compareAndSet(false,true)) {
                nativeThread=Thread.currentThread();Object pending=get(bridge,"pendingSensingReconciliation");String task=(String)get(pending,"id");
                boolean bridgeHeld=Thread.holdsLock(bridge);
                reader=new Thread(()->{try{Map<String,Object> response=map(bridge.call("openpnp_get_sensing_reconciliation",object("task_id",task)));check(response.get("task") instanceof Map,"Concurrent reconciliation read returns an actual task");readerReturned=true;}catch(Throwable failure){readerFailure.compareAndSet(null,failure);}},"recovery-lock-order-reconciliation-reader");
                reader.setDaemon(true);reader.start();long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);ThreadInfo waiting=null;
                while(System.nanoTime()<until){waiting=info(reader);if(waiting!=null&&waiting.getThreadState()==Thread.State.BLOCKED)break;LockSupport.parkNanos(TimeUnit.MILLISECONDS.toNanos(1));}
                if(waiting==null||waiting.getThreadState()!=Thread.State.BLOCKED)throw new AssertionError("Reader did not reach the controlled lock boundary");
                Object expected=bridgeHeld?bridge:target;
                check(waiting.getLockInfo().getIdentityHashCode()==System.identityHashCode(expected),"Reader waits on exact expected monitor at controlled schedule boundary");
                check(waiting.getLockOwnerId()==nativeThread.getId(),"Actual native executor owns the reader's contended monitor");
                check(bridgeHeld?holds(info(nativeThread),bridge):holds(waiting,bridge),"Bridge ownership is measured on actual contender, not inferred from polling timing");
                schedule.add(Bridge.map("target",targetName,"native_holds_bridge",bridgeHeld,"native",info(info(nativeThread)),"reader",info(waiting),"delegated_authority_operation",operation));
                statusReader=new Thread(()->{try{Map<String,Object> response=map(bridge.call("openpnp_get_status",object()));check(response.containsKey("config_revision"),"Concurrent status read returns an actual Bridge snapshot");statusReturned=true;}catch(Throwable failure){readerFailure.compareAndSet(null,failure);}},"recovery-lock-order-status-reader");
                statusReader.setDaemon(true);statusReader.start();hookReached.countDown();
            }
            return original.completed(operation);
        });
    }
    static void deadlock(long[] ids)throws Exception {
        Set<Long> found=new HashSet<>();for(long id:ids)found.add(id);
        if(nativeThread==null||reader==null||!found.contains(nativeThread.getId())||!found.contains(reader.getId()))throw new AssertionError("Unexpected deadlock outside controlled native/read pair");
        ThreadInfo n=info(nativeThread),r=info(reader);
        check(n.getLockOwnerId()==reader.getId()&&r.getLockOwnerId()==nativeThread.getId(),"Exact native/read monitor ownership cycle detected");
        check(n.getLockInfo().getIdentityHashCode()==System.identityHashCode(bridge)&&r.getLockInfo().getIdentityHashCode()==System.identityHashCode(target),"Deadlock is Bridge-to-selected-ledger inversion");
        List<Object> dump=new ArrayList<>();for(ThreadInfo t:MX.dumpAllThreads(true,true))dump.add(info(t));
        write("lock-order-thread-dump.json",dump);
        write("lock-order-proof.json",Bridge.map("passed",false,"deterministic_deadlock",true,"target",targetName,"schedule",schedule,"deadlocked_threads",Arrays.stream(ids).boxed().toArray(),"checks",checks,"assertions",checks.size(),"pid",ProcessHandle.current().pid(),"intentional_halt_exit",85,"physical_qualification",false,"scope","Real native continuation callback blocked by a real reconciliation reader; test only delays and delegates original TerminalAuthority"));
        System.err.println("DETERMINISTIC_RECOVERY_LOCK_INVERSION "+targetName);System.err.flush();Runtime.getRuntime().halt(85);
    }
    public static void main(String[] args)throws Exception {
        if(args.length!=3||!Set.of("lineage","material","boards").contains(args[2]))throw new IllegalArgumentException("Expected sample root, new state directory, target lineage/material/boards");
        targetName=args[2];state=Path.of(args[1]).toAbsolutePath();Files.createDirectory(state);Map<String,Object> proof=new LinkedHashMap<>();int exit=0;
        try {
            Path configDir=state.resolve("config"),manifest=state.resolve("prepared-gui-fixture.json");Map<String,Object> prepared=GuiSensingFixture.prepare(configDir,manifest,scenario);
            Configuration.get().getMachine().close();Configuration.initialize(configDir.toFile());config=Configuration.get();config.load();
            Map<String,Object> claimed=edt(()->GuiSensingFixture.claimPreparedGuiFixture(config,manifest,(String)prepared.get("manifest_sha256"),scenario));write("fixture-attestation.json",claimed);
            Files.writeString(state.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);local=new LocalAdapter(Bridge.map("sensing_fixture_attested",true,"sensing_fixture",claimed));
            bridge=new Bridge(config,state.resolve("token"),state.resolve("journal"),Path.of(args[0]).toAbsolutePath(),0,true,"gui-simulator",local);installSchedule();
            FutureTask<Void> workload=new FutureTask<>(()->{exercise(proof);return null;});Thread worker=new Thread(workload,"recovery-lock-order-workload");worker.setDaemon(true);worker.start();
            long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(120);
            while(!workload.isDone()&&System.nanoTime()<deadline) {
                long[] dead=MX.findDeadlockedThreads();if(dead!=null)deadlock(dead);LockSupport.parkNanos(TimeUnit.MILLISECONDS.toNanos(5));
            }
            workload.get(1,TimeUnit.SECONDS);check(hookReached.getCount()==0,"Real continuation reached selected ledger callback under controlled scheduling");
            reader.join(5000);statusReader.join(5000);if(readerFailure.get()!=null)throw new AssertionError("Concurrent real reader failed",readerFailure.get());
            check(readerReturned&&statusReturned&&!reader.isAlive()&&!statusReader.isAlive(),"Both concurrent Bridge reads complete after native metadata transaction");
            check(MX.findDeadlockedThreads()==null,"No JVM monitor cycle remains after actual native continuation and placement");
            check(Boolean.TRUE.equals(schedule.get(0).get("native_holds_bridge")),"Native recovery acquired Bridge before selected ledger callback");proof.put("passed",true);
        } catch(Throwable failure){failure.printStackTrace();proof.put("passed",false);proof.put("failure",failure.toString());exit=1;}
        finally {
            try{if(bridge!=null){if(exit!=0)bridge.recordGuiUnknownExit();bridge.close();}proof.put("bridge_closed",true);}catch(Throwable failure){proof.put("bridge_close_failure",failure.toString());exit=1;}
            try{if(local!=null)local.release();if(config!=null)config.getMachine().close();}catch(Throwable failure){proof.put("native_cleanup_failure",failure.toString());exit=1;}
            if(exit!=0)proof.put("passed",false);proof.putAll(Bridge.map("target",targetName,"schedule",schedule,"checks",checks,"assertions",checks.size(),"public_calls",calls,"pid",ProcessHandle.current().pid(),"test_delay_only",true,"authority_answers_delegated_unchanged",true,"real_swing_qualified",false,"physical_qualification",false,"scope","Actual Bridge synthetic retained-part recovery plus failed publication, new local continuation, concurrent real reads, exact native placement; non-Swing adapter, no restart claim"));
            write("lock-order-proof.json",proof);System.out.println("NATIVE_RECOVERY_LOCK_ORDER_BRIDGE_RESULT "+JSON.toJson(proof));
        }
        System.exit(exit);
    }
}
