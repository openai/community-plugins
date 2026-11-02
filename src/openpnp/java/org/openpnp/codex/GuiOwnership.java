/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Future;
import java.util.function.BooleanSupplier;
import org.openpnp.model.Configuration;
import org.openpnp.model.Job;
import org.openpnp.spi.Machine;

/** Optional GUI seam. This interface references only stock OpenPnP/JDK APIs. */
public interface GuiOwnership {
    void checkAttachment(Configuration configuration, Machine machine) throws Exception;
    void requireNativeOwnership() throws Exception;
    default <T> T invokeNative(Callable<T> action) throws Exception {return action.call();}
    void requireRemoteGrant() throws Exception;
    <T> Future<T> submitNative(Callable<T> action, boolean ignoreEnabled) throws Exception;
    void publishJob(Job job) throws Exception;
    Map<String,Object> snapshot();
    /** Optional local form. Headless adapters and existing test doubles do not gain authority. */
    default boolean supportsLoadedBoardInspection() { return false; }
    default void presentLoadedBoardInspection(Map<String,Object> task, InspectionSubmission submission) throws Exception {
        throw new UnsupportedOperationException("Local board inspection is unavailable");
    }
    default void dismissLoadedBoardInspection(String taskId, String reason) {}
    @FunctionalInterface interface InspectionSubmission {
        /** Returns promptly. Durable/native work and completion must not block the EDT or GUI worker. */
        CompletionStage<Map<String,Object>> submit(Map<String,Object> observations, BooleanSupplier currentLocalAuthority) throws Exception;
        /** Local cancellation notification; the adapter invokes this off the EDT without waiting. */
        default void cancel(String reason) {}
    }
    /** Optional local one-use sensing recovery form. Mere tool discovery confers no local authority. */
    default boolean supportsSensingReconciliation() { return false; }
    /** Explicit source-absent restart startup; no source or execution authority is implied. */
    default boolean supportsSensingRestart() { return false; }
    default void presentSensingReconciliation(Map<String,Object> task, SensingReconciliationSubmission submission) throws Exception {
        throw new UnsupportedOperationException("Local sensing reconciliation is unavailable");
    }
    default void dismissSensingReconciliation(String taskId, String reason) {}
    @FunctionalInterface interface SensingReconciliationSubmission {
        /** One local gesture; exact task, faults and effects are already captured by the Bridge callback. */
        CompletionStage<Map<String,Object>> submit(BooleanSupplier currentLocalAuthority) throws Exception;
        /** Captured by the local gesture on EDT, then handed off without an EDT call or wait.
         * The callback may consume it only under its independently issued native restart permit. */
        default CompletionStage<Map<String,Object>> submitRestart(GuiSensingFixture.RestartAttestation attestation,
                BooleanSupplier currentLocalAuthority) throws Exception {
            throw new UnsupportedOperationException("Local restart attestation handoff is unavailable");
        }
        default void cancel(String reason) {}
    }
    /** An execution policy refusal must bypass native retry-on-Exception loops. */
    final class ScriptRejected extends Error {
        public ScriptRejected(String message) { super(message); }
    }
}
