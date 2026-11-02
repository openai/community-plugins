/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.Configuration;
import org.openpnp.spi.Machine;
import org.openpnp.spi.base.*;

/** Native task wrappers, cancellation and ownership reservation races; no machine movement. */
public final class NativeExecutorOwnershipTest {
    public static void main(String[] args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-native-executor-owner-");Configuration.initialize(root.toFile());Configuration.get().load();Machine machine=Configuration.get().getMachine();ExternalExecutionControl gate=((AbstractMachine)machine).getExternalExecutionControl();int exit=0;
        try {
            CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);Future<?> local=machine.submit(()->{entered.countDown();release.await();return null;},null,true);check(entered.await(5,TimeUnit.SECONDS),"local task entered");
            try{gate.claim("test");throw new AssertionError("claimed running local task");}catch(IllegalStateException expected){}release.countDown();local.get(5,TimeUnit.SECONDS);idle(machine,gate);
            Object owner=gate.claim("native-test-owner");try{machine.submit(()->null,null,true);throw new AssertionError("foreign task admitted");}catch(RejectedExecutionException expected){}
            CountDownLatch running=new CountDownLatch(1),finish=new CountDownLatch(1);Future<?> first=gate.withOwner(owner,()->machine.submit(()->{running.countDown();finish.await();return null;},null,true));check(running.await(5,TimeUnit.SECONDS),"owned task entered");
            Future<?> queued=gate.withOwner(owner,()->machine.submit(()->{throw new AssertionError("cancelled queued callable ran");},null,true));check(gate.getPendingTaskCount()==2,"both native reservations counted");check(queued.cancel(false),"queued cancel succeeds");check(gate.getPendingTaskCount()==1,"queued cancel releases only queued reservation");
            check(first.cancel(false),"running future cancel succeeds without stopping callable");check(gate.getPendingTaskCount()==1,"running cancel retains reservation until exit");try{gate.release(owner);throw new AssertionError("released cancelled but still running native callable");}catch(IllegalStateException expected){}
            finish.countDown();idle(machine,gate);gate.release(owner);check(gate.getOwnerLabel()==null,"owner released only after real native drain");
            try{gate.withOwner(owner,()->null);throw new AssertionError("stale token reused");}catch(RejectedExecutionException expected){}
            Object next=gate.claim("next-owner");Future<?> completed=gate.withOwner(next,()->machine.submit(()->42,null,true));check(completed.get(5,TimeUnit.SECONDS).equals(42),"fresh token executes native task");idle(machine,gate);gate.release(next);
            System.out.println("OPENPNP_NATIVE_EXECUTOR_OWNERSHIP_RESULT {\"passed_groups\":5,\"native_executor\":true,\"movement_performed\":false,\"physical_qualification\":false}");
        }catch(Throwable failure){failure.printStackTrace();exit=1;}finally{machine.close();}System.exit(exit);
    }
    static void idle(Machine machine,ExternalExecutionControl gate)throws Exception {long end=System.nanoTime()+5_000_000_000L;while(System.nanoTime()<end){if(!machine.isBusy()&&gate.getPendingTaskCount()==0)return;Thread.sleep(5);}throw new AssertionError("native executor failed to drain");}
    static void check(boolean yes,String message){if(!yes)throw new AssertionError(message);}
}
