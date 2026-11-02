/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeMappedAxisBridgeTest.*;
import com.google.gson.JsonObject;
import java.beans.PropertyChangeListener;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import org.openpnp.machine.reference.ReferenceNozzle;
import org.openpnp.machine.reference.ReferenceNozzleTip;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;
import org.openpnp.model.Configuration;
import org.openpnp.spi.Head;
import org.openpnp.spi.Machine;
import org.openpnp.spi.MachineListener;

/** Real callbacks after motion-limit and assembly effects must not grant homing.
 * Injection changes only the native observation, never performs or proves a native home. */
public final class NativeConfigurationHomingBoundaryTest {
    private static int checks;
    private static final List<Object> cases = new ArrayList<>();

    private static void verify(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    private static void initialize(Path samples) throws Exception {
        NativeNozzleAssemblyTest.initialize(root.resolve("config"));
        config = Configuration.get();
        machine = config.getMachine();
        raw = (ReferenceControllerAxis) ((ReferenceNozzle) machine.getDefaultHead()
                .getDefaultNozzle()).getAxisX();
        Path token = root.resolve("token");
        Files.writeString(token, UUID.randomUUID().toString() + UUID.randomUUID());
        bridge = new Bridge(config, token, root.resolve("journal"), samples, 0, true);
        session = (String) call("openpnp_request_control_session",
                obj("request_id", "configuration-homing-grant", "ttl_seconds", 600)).get("session_id");
        machine.addListener(new MachineListener.Adapter() {
            @Override public void machineEnabled(Machine m) { enabled++; }
            @Override public void machineHomed(Machine m, boolean value) { if (value) homed++; }
            @Override public void machineHeadActivity(Machine m, Head h) { activity++; }
        });
    }

    private static void scenario(String kind, boolean interfere) throws Exception {
        boolean assembly = "assembly".equals(kind);
        // The assembly's mandatory portable preimage excludes runout-compensation objects.
        // Keep board/vision/job validation seeds; this case does not claim runout invalidation coverage.
        NativeBoardLoads loads = seedDependencies(!assembly);
        if (assembly) for (org.openpnp.spi.NozzleTip candidate : machine.getNozzleTips()) {
            if (candidate.getClass() == ReferenceNozzleTip.class)
                verify(((Map<?, ?>) field(((ReferenceNozzleTip) candidate).getCalibration(),
                        "runoutCompensationLookup")).isEmpty(),
                        "Assembly starts without synthetic runout outside its portable preimage profile");
        }
        Map<String, Boolean> history = new TreeMap<>(job.getPlacedStatusSnapshot());
        Map<String, Integer> oldCounts = counts();
        String loadRevision = loads.revision(), oldRevision = revision();
        JsonObject change = assembly ? NativeNozzleAssemblyTest.proposal(config)
                : obj("type", "set_axis_motion_limits", "axis_id", raw.getId(),
                        "soft_limit_low_mm", -100, "soft_limit_high_mm", 100,
                        "feedrate_mm_per_s", 123, "acceleration_mm_per_s2", 400,
                        "jerk_mm_per_s3", 2000);
        verify(assembly || raw.getFeedratePerSecond().getValue() != 123,
                "Motion-limit callback is distinguished from pre-effect invalidation");
        Map<String, Object> proposal = plan(change);
        AtomicInteger callbacks = new AtomicInteger(), injections = new AtomicInteger();
        ReferenceNozzleTip tip = (ReferenceNozzleTip) machine.getDefaultHead()
                .getDefaultNozzle().getNozzleTip();
        org.openpnp.model.Package targetPackage = config.getPackage("R0805");
        PropertyChangeListener listener = event -> {
            // ResetAll also runs before the edit. Observe only the callback after actual geometry.
            if (!assembly && raw.getFeedratePerSecond().getValue() != 123) return;
            // Assembly removes old compatibility entries, then assigns the newly attached tip.
            if (assembly && targetPackage.getCompatibleNozzleTips().stream()
                    .noneMatch(value -> "Assembly61 tip".equals(value.getName()))) return;
            callbacks.incrementAndGet();
            try {
                verify(machine.isTask(Thread.currentThread()), "Callback belongs to the native executor");
                verify(!oldRevision.equals(revision()) && "prepared".equals(field(bridge, "jobState")),
                        "Configuration revision and job validation change before native callback");
                verify(field(bridge, "validatedBoardLoadRevision") == null,
                        "Board registration validation is revoked before native callback");
                String journal = Files.readString(root.resolve("journal/operations.jsonl"));
                verify(journal.contains("native_effect_intent") && journal.contains("configuration_change_started"),
                        "Durable intent and revision records precede native callback");
                verify(!machine.isEnabled(), "Configuration callback cannot enable the machine");
                if (interfere) { machine.setHomed(true); injections.incrementAndGet(); }
            } catch (Exception failure) { throw new RuntimeException(failure); }
        };
        if (assembly) targetPackage.addPropertyChangeListener("compatibleNozzleTips", listener);
        else tip.getCalibration().addPropertyChangeListener("calibrationInformation", listener);
        JsonObject request = mutation("plan_id", proposal.get("plan_id"), "expected_config_revision", oldRevision);
        Map<String, Object> op;
        try {
            op = terminal("openpnp_apply_configuration", request, interfere ? "outcome_unknown" : "succeeded");
        } finally {
            if (assembly) targetPackage.removePropertyChangeListener("compatibleNozzleTips", listener);
            else tip.getCalibration().removePropertyChangeListener("calibrationInformation", listener);
        }
        verify(callbacks.get() >= 1, "Actual final callback was reached");
        verify(!machine.isHomed() && !machine.isEnabled(), "No homed or enabled authority survives configuration");
        verify(Boolean.TRUE.equals(field(bridge, "configurationFault")) == interfere,
                "Only listener interference faults configuration");
        verify(Boolean.TRUE.equals(op.get("native_effect_pending")) == interfere,
                "Unknown effects stay pending and normal effects complete");
        verify(homed == injections.get() && enabled == 0,
                "Synthetic homed observations only; no native enable event");
        // ReferenceNozzle.setNozzleTip emits head activity for its model/UI update.
        // Assembly notification counts are observations, not a motion or actuation oracle.
        if (!assembly) verify(activity == 0, "Motion-limit edit emits no head-activity notification");
        verify(oldCounts.equals(counts()) && history.equals(job.getPlacedStatusSnapshot())
                && loadRevision.equals(loads.revision()), "Material, job history and load identities are preserved");
        verify(assembly ? machine.getDefaultHead().getNozzles().size() == 2
                : raw.getFeedratePerSecond().getValue() == 123, "Native effect remains; the bridge does not roll it back");
        if (interfere) {
            int originalCallbacks = callbacks.get();
            String retained = G.toJson(call("openpnp_get_operation", obj("operation_id", op.get("operation_id"))));
            expect("CONFIGURATION_FAULT", () -> call("openpnp_apply_configuration", request));
            verify(originalCallbacks == callbacks.get() && retained.equals(G.toJson(call("openpnp_get_operation",
                    obj("operation_id", op.get("operation_id"))))), "Rejected replay preserves callbacks and original outcome");
            verify(Boolean.TRUE.equals(call("openpnp_get_request_status",
                    obj("request_id", request.get("request_id").getAsString())).get("found")),
                    "Original failed request remains readable");
        }
        cases.add(Bridge.map("kind", kind, "interference", interfere, "callbacks", callbacks.get(),
                "synthetic_homing_injections", injections.get(), "synthetic_runout_seeded", !assembly,
                "assembly_preimage_scope", assembly ? "default-without-runout-compensation" : null,
                "head_activity_notifications", activity,
                "head_activity_semantics", assembly ? "native-model-topology-notification" : "expected-zero-notifications",
                "head_activity_proves_no_motion_or_driver_actuation", false,
                "operation", op));
    }

    private static void nonAxisControl() throws Exception {
        // Preserve an existing observation across a change that has never invalidated homing.
        task(() -> { machine.setHomed(true); return null; });
        Map<String, Object> op = apply(plan(obj("type", "set_machine_speed", "speed", 0.8)), "succeeded");
        verify(machine.isHomed(), "Non-axis configuration preserves its previous homing behavior");
        verify(!Boolean.TRUE.equals(field(bridge, "configurationFault")), "Non-axis change is not spuriously fenced");
        verify(!Boolean.TRUE.equals(op.get("native_effect_pending")), "Non-axis change acquires no pending axis effect");
        task(() -> { machine.setHomed(false); return null; });
    }

    public static void main(String[] args) throws Exception {
        Path base = args.length == 1 ? Files.createTempDirectory("openpnp-configuration-homing-") : Paths.get(args[0]);
        Path samples = Paths.get(args[args.length - 1]);
        List<String> failures = new ArrayList<>();
        for (String kind : List.of("motion-limits", "assembly", "non-axis")) {
            for (boolean interfere : "non-axis".equals(kind) ? List.of(false) : List.of(false, true)) {
                String label = kind + (interfere ? "-returning-listener" : "-control");
                root = base.resolve(label); enabled = homed = activity = 0;
                try {
                    initialize(samples);
                    if ("non-axis".equals(kind)) nonAxisControl(); else scenario(kind, interfere);
                } catch (Throwable failure) { failures.add(label + ": " + failure); failure.printStackTrace(); }
                finally {
                    try { if (bridge != null) bridge.close(); }
                    catch (Throwable failure) { failures.add(label + " Bridge cleanup: " + failure); failure.printStackTrace(); }
                    try { if (machine != null) machine.close(); }
                    catch (Throwable failure) { failures.add(label + " machine cleanup: " + failure); failure.printStackTrace(); }
                    try { NativeFixtureExecutorCleanup.closeIdleExecutor(machine); }
                    catch (Throwable failure) { failures.add(label + " native executor cleanup: " + failure); failure.printStackTrace(); }
                    bridge = null;
                    machine = null;
                }
            }
        }
        Map<String, Object> summary = Bridge.map("passed", failures.isEmpty(), "assertions", checks,
                "helper_assertions", assertions, "cases", cases, "failures", failures,
                "native_home_calls", 0, "synthetic_observation_injection", true,
                "physical_qualification", false, "evidence_directory", base.toString());
        Files.writeString(base.resolve("result.json"), G.toJson(summary) + "\n");
        System.out.println("CONFIGURATION_HOMING_BOUNDARIES " + G.toJson(summary));
        if (!failures.isEmpty()) throw new AssertionError("Configuration homing boundary failures: " + failures);
    }
}
