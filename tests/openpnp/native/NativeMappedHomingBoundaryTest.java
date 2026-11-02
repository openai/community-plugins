/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeMappedAxisBridgeTest.*;
import java.beans.PropertyChangeListener;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import org.openpnp.machine.reference.feeder.ReferenceStripFeeder;

/** The retained source41 singleton counterexample, using actual native setters and Bridge.
 * setHomed(true) injects an observation; it is never evidence of a native home operation. */
public final class NativeMappedHomingBoundaryTest {
    private static int checks;

    private static void verify(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    private static void boundary() throws Exception {
        terminal("openpnp_backup_configuration", mutation(), "succeeded");
        seedDependencies();
        ReferenceStripFeeder feeder = (ReferenceStripFeeder) machine.getFeeders().stream()
                .filter(f -> f instanceof ReferenceStripFeeder && f.getPart() != null)
                .findFirst().orElseThrow();
        task(() -> {
            mapped.setInputAxis(other);
            mapped.setMapOutput1(mm(30));
            feeder.setPart(config.getParts().stream().filter(p -> p != feeder.getPart())
                    .findFirst().orElseThrow());
            return null;
        });
        String old = revision();
        com.google.gson.JsonObject desired = change();
        desired.addProperty("input_axis_id", raw.getId());
        Map<String, Object> proposal = plan(desired);
        Map<String, Integer> count = counts();
        AtomicInteger mappedEvents = new AtomicInteger(), ordinaryEvents = new AtomicInteger();
        PropertyChangeListener mapping = event -> {
            mappedEvents.incrementAndGet();
            try { machine.setHomed(true); }
            catch (Exception failure) { throw new RuntimeException(failure); }
        };
        PropertyChangeListener ordinary = event -> ordinaryEvents.incrementAndGet();
        mapped.addPropertyChangeListener("inputAxis", mapping);
        feeder.addPropertyChangeListener("part", ordinary);
        try {
            Map<String, Object> op = terminal("openpnp_apply_configuration",
                    mutation("plan_id", proposal.get("plan_id"), "expected_config_revision", old),
                    "outcome_unknown");
            // Preserve all six assertions from the original singleton counterexample.
            verify(Boolean.TRUE.equals(field(bridge, "configurationFault"))
                    && Boolean.TRUE.equals(op.get("native_effect_pending")),
                    "uncertain listener interference is fenced and retained");
            verify(!machine.isHomed() && !machine.isEnabled(),
                    "no surviving homed/enabled authority after failure");
            verify(count.equals(counts()), "consumption unchanged");
            verify(ordinaryEvents.get() == 0, "ordinary native setter count matches selected route");
            verify(mappedEvents.get() == 1 && mapped.getInputAxis() == raw,
                    "actual source setter completed before observation drift");
            verify(homed == 1, "synthetic true observation occurred once without native home call");
        } finally {
            mapped.removePropertyChangeListener("inputAxis", mapping);
            feeder.removePropertyChangeListener("part", ordinary);
        }
    }

    public static void main(String[] args) throws Exception {
        Path base = args.length == 1 ? Files.createTempDirectory("openpnp-mapped-homing-")
                : Paths.get(args[0]);
        Path samples = Paths.get(args[args.length - 1]);
        root = base.resolve("singleton-homing-observation");
        enabled = homed = activity = 0;
        Throwable failure = null;
        try {
            setup(samples);
            boundary();
        } catch (Throwable caught) {
            failure = caught;
        } finally {
            try { if (bridge != null) bridge.close(); }
            catch (Throwable cleanup) {
                if (failure == null) failure = cleanup; else failure.addSuppressed(cleanup);
            }
            try { if (machine != null) machine.close(); }
            catch (Throwable cleanup) {
                if (failure == null) failure = cleanup; else failure.addSuppressed(cleanup);
            }
            try { NativeFixtureExecutorCleanup.closeIdleExecutor(machine); }
            catch (Throwable cleanup) {
                if (failure == null) failure = cleanup; else failure.addSuppressed(cleanup);
            }
            bridge = null;
            machine = null;
        }
        System.out.println("MAPPED_HOMING_BOUNDARY " + G.toJson(obj(
                "passed", failure == null, "assertions", checks, "helper_assertions", assertions,
                "native_home_calls", 0, "synthetic_homed_observation_injection", true,
                "physical_qualification", false, "evidence_directory", base.toString())));
        if (failure instanceof Error) throw (Error) failure;
        if (failure instanceof Exception) throw (Exception) failure;
        if (failure != null) throw new AssertionError(failure);
    }
}
