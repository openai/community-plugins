/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.lang.reflect.Field;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.spi.Machine;
import org.openpnp.spi.base.AbstractMachine;
import org.openpnp.spi.base.ExternalExecutionControl;

/** Test-only lifecycle for the exact fresh machine owned by one isolated fixture.
 * Call after closing its Bridge and devices, before creating another fixture.
 * Pinned ReferenceMachine.close() leaves its non-daemon executor alive.
 * Never cancels tasks, interrupts workers, submits work, or drains queues. */
final class NativeFixtureExecutorCleanup {
    private NativeFixtureExecutorCleanup() { }

    static void closeIdleExecutor(Machine fixture) throws Exception {
        if (fixture == null) return;
        if (fixture.getClass() != ReferenceMachine.class)
            throw new AssertionError("Cleanup requires the exact fixture ReferenceMachine");
        AbstractMachine machine = (AbstractMachine) fixture;
        ExternalExecutionControl control = machine.getExternalExecutionControl();
        Field field = AbstractMachine.class.getDeclaredField("executor");
        field.setAccessible(true);
        ThreadPoolExecutor executor = (ThreadPoolExecutor) field.get(machine);
        if (executor == null) return;
        requireIdle(machine, control, executor, field);
        // Future completion can precede runWorker's decrement of active count.
        // Wait only for that bookkeeping tail while rejecting any actual work.
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (executor.getActiveCount() != 0) {
            requireIdle(machine, control, executor, field);
            if (System.nanoTime() >= deadline)
                throw new AssertionError("Closed fixture executor did not become quiescent");
            TimeUnit.MILLISECONDS.sleep(1);
        }
        requireIdle(machine, control, executor, field);
        executor.shutdown();
        if (!executor.awaitTermination(5, TimeUnit.SECONDS) || !executor.isTerminated()
                || executor.getActiveCount() != 0)
            throw new AssertionError("Exact fixture executor did not terminate gracefully");
        requireIdle(machine, control, executor, field);
    }

    private static void requireIdle(AbstractMachine machine, ExternalExecutionControl control,
            ThreadPoolExecutor executor, Field field) throws Exception {
        if (machine.isTask(Thread.currentThread()) || machine.isBusy()
                || control.getPendingTaskCount() != 0 || !executor.getQueue().isEmpty()
                || field.get(machine) != executor)
            throw new AssertionError("Closed fixture has outstanding native work; cleanup refuses cancellation");
    }
}
