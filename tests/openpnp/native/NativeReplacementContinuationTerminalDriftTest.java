/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeReplacementContinuationPublicationTest.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.model.*;

/** Final completion-thread native checks: synthetic fault/wrapper/host callbacks, actual native
 * tray setters and placement geometry. No mutation authority is recovered from journal replay. */
public final class NativeReplacementContinuationTerminalDriftTest {
    static final List<Map<String,Object>> results = new ArrayList<>();
    static final List<String> unexpectedAccepts = new ArrayList<>();
    static int assertions, actualCounterChanges, actualGeometryChanges;
    static void check(boolean value, String why) { assertions++; if (!value) throw new AssertionError(why); }
    static boolean verifies(Ready ready, Map<String,Object> dispositions) throws Exception {
        try { return ready.fixture.e.replacement.verifiedContinuation(ready.fresh.faultCapture.payload, dispositions, false); }
        catch (java.io.IOException refused) { return false; }
    }
    static void drift(String kind) throws Exception {
        int start = assertions;
        try (Ready ready = new Ready("terminal-native-drift-" + kind, "definition")) {
            var f = ready.fixture;
            var publication = begin(ready);
            ready.install(stage(ready, publication));
            finish(ready, publication);
            Map<String,Object> dispositions = f.e.replacement.continuationDispositions(publication.publicationId(), "none-present");
            Map<String,Object> terminal = freshOperation(ready.fresh);
            terminal.put("state", "succeeded");
            terminal.put("native_completion", m("native_wrapper_completed", true, "native_wrapper_succeeded", true, "physical_outcome_verified", false));
            f.e.append("operation", terminal);
            f.e.terminalWrappers.add(ready.fresh.operation);
            check(verifies(ready, dispositions), "Exact completed native publication verifies from the completion thread before drift");
            long bytes = Files.size(f.e.file);
            Map<String,String> files = configurationFiles();
            Map<String,Object> original = immutableOriginal(f.e);
            int initialIndex = tray.getFeedCount();
            Placement placement = f.e.freshJob.getBoardLocations().get(0).getBoard().getPlacements().get(0);
            Location initialLocation = placement.getLocation();
            if (kind.equals("tray-counter")) {
                task(() -> { tray.setFeedCount(initialIndex + 1); actualCounterChanges++; return null; });
                check(tray.getFeedCount() == initialIndex + 1, "The actual native tray counter changed without a durable replacement event");
            } else {
                task(() -> { placement.setLocation(initialLocation.derive(initialLocation.getX() + 1.0, null, null, null)); actualGeometryChanges++; return null; });
                check(!placement.getLocation().equals(initialLocation), "Actual detached native placement geometry changed without changing publication history");
            }
            boolean accepted;
            try {
                accepted = verifies(ready, dispositions);
                if (accepted) unexpectedAccepts.add(kind);
                check(Files.size(f.e.file) == bytes && files.equals(configurationFiles()), "Final verification performs no journal append or configuration save");
                check(NativeFaultedJobReplacement.same(original, immutableOriginal(f.e)), "Final verification preserves original operation and action outcomes");
                check(kind.equals("tray-counter") ? tray.getFeedCount() == initialIndex + 1 : !placement.getLocation().equals(initialLocation), "Final verification does not silently repair native state");
            } finally {
                // Restore only this deliberately perturbed fixture state. This is not a recovery API.
                task(() -> { if (kind.equals("tray-counter")) tray.setFeedCount(initialIndex); else placement.setLocation(initialLocation); return null; });
            }
            ready.preserved();
            check(verifies(ready, dispositions), "Read-only final checking remains usable after exact fixture state is restored");
            results.add(m("kind", kind, "assertions", assertions-start, "live_verifier_accepted_native_drift", accepted, "expected_acceptance", false));
        }
    }
    public static void main(String[] args) throws Exception {
        initialize(Path.of(args[0])); Throwable error = null;
        try {
            for (String kind : List.of("tray-counter", "placement-geometry")) drift(kind);
            check(unexpectedAccepts.isEmpty(), "Final native verifier accepted drift: " + unexpectedAccepts);
        } catch (Throwable failure) { error = failure; failure.printStackTrace(); }
        finally {
            config.getMachine().close();
            Map<String,Object> proof = m("passed", error == null, "assertions", assertions, "fixture_assertions", checks,
                "cases", results, "unexpected_accepts", unexpectedAccepts, "error", error == null ? null : error.toString(),
                "actual_native_counter_changes", actualCounterChanges, "actual_native_placement_geometry_changes", actualGeometryChanges,
                "scope", "Actual native tray/placement drift after forced publication and synthetic successful terminal wrapper; verifier called outside native executor; exact restoration is fixture cleanup",
                "actual_native_job_initializations", 0, "actual_native_feeds", 0, "actual_native_job_placements", 0,
                "bridge_continuation_qualified", false, "restart_reattachment_qualified", false, "hardware_qualified", false, "public_package_qualified", false);
            Files.writeString(root.resolve("proof.json"), JSON.toJson(proof) + "\n");
            System.out.println("NATIVE_REPLACEMENT_CONTINUATION_TERMINAL_DRIFT_RESULT " + JSON.toJson(proof));
        }
        System.exit(error == null ? 0 : 1);
    }
}
