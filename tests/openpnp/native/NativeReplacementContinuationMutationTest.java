/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeReplacementContinuationTest.*;
import java.beans.PropertyChangeListener;
import java.io.IOException;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.spi.Feeder;

/** Independent component fixture: synthetic initial sensing fault and terminal-wrapper observation,
 * actual retained native models, tray setters/configuration saves and FileChannel.force records.
 * This does not execute a Bridge continuation or restore native bindings on replay. */
public final class NativeReplacementContinuationMutationTest {
    static int assertions, rejected;
    static final List<Map<String,Object>> results = new ArrayList<>();
    interface Checked { void run() throws Exception; }
    static void check(boolean condition, String why) {
        assertions++;
        if (!condition) throw new AssertionError(why);
    }
    static void refuse(String why, Checked action) throws Exception {
        try { action.run(); throw new AssertionError("Accepted " + why); }
        catch (IOException | Bridge.Fault | NativeJobLineage.Fault expected) { assertions++; rejected++; }
    }
    static List<Map<String,Object>> rows(Fixture fixture, String kind) throws Exception {
        List<Map<String,Object>> result = new ArrayList<>();
        for (Object raw : (List<?>) o(fixture.e.replacement.progress(fixture.attempt).get(kind)).get("rows")) result.add(o(raw));
        return result;
    }
    static Map<String,Object> row(Fixture fixture, String kind, String oldId) throws Exception {
        return rows(fixture, kind).stream().filter(r -> oldId.equals(r.get("old_load_id"))).findFirst().orElseThrow();
    }
    static String phase(Map<String,Object> row) { return (String) row.getOrDefault("effective_phase", row.get("phase")); }
    static Map<String,Object> newLoad(Map<String,Object> row) throws Exception {
        return o(row.containsKey("effective_new_load") ? row.get("effective_new_load") : row.get("new_load"));
    }
    static Map<String,Object> originalFacts(Fixture f) throws Exception {
        Map<String,Object> facts = m("operations_actions", immutableOriginal(f.e), "native_history", new TreeMap<>(f.e.oldJob.getPlacedStatusSnapshot()));
        for (String kind : List.of("material", "boards")) {
            Map<String,Object> loads = new TreeMap<>();
            for (Map<String,Object> r : rows(f, kind)) {
                Map<String,Object> values = m("old_load", r.get("old_load"), "phase", r.get("phase"), "parent_intent", r.get("parent_intent"), "parent_outcome", r.get("parent_outcome"));
                if (kind.equals("material")) values.put("old_feed_facts", r.get("old_feed_facts"));
                loads.put((String) r.get("old_load_id"), values);
            }
            facts.put(kind, loads);
        }
        return NativeFaultedJobReplacement.frozen(facts);
    }
    static void preserved(Fixture f, Map<String,Object> original, List<Map<String,Object>> prefix) throws Exception {
        check(NativeFaultedJobReplacement.same(original, originalFacts(f)), "Original operation/action/load/phase/native placed facts remain exact");
        check(f.e.events.size() >= prefix.size() && NativeFaultedJobReplacement.same(prefix, f.e.events.subList(0, prefix.size())), "All pre-continuation forced records remain byte-equivalent");
        check(Boolean.FALSE.equals(f.e.replacement.progress(f.attempt).get("execution_authority_restored")), "Progress grants no execution readiness");
        check(f.e.freshJob.getPlacedStatusSnapshot().isEmpty(), "The actual retained detached candidate has no placed history");
    }
    static ReferenceTrayFeeder nativeTray(Map<String,Object> r) {
        String id = (String) r.get("feeder_id");
        for (Feeder f : config.getMachine().getFeeders()) if (id.equals(f.getId())) return (ReferenceTrayFeeder) f;
        throw new AssertionError("Missing actual native tray");
    }
    static Map<String,Object> mutate(Fixture f, NativeFaultedJobReplacement.ContinuationPermit permit, String kind, String oldId) throws Exception {
        return task(() -> kind.equals("material") ? f.e.material.continueReplacement(permit, oldId) : f.e.boards.continueReplacement(permit, f.e.oldJob, f.e.freshJob, f.attempt, oldId));
    }
    static Map<String,Object> adopt(Fixture f, NativeFaultedJobReplacement.ContinuationPermit permit, String kind, String oldId) throws Exception {
        return task(() -> kind.equals("material") ? f.e.material.adoptCompletedReplacement(permit, oldId) : f.e.boards.adoptCompletedReplacement(permit, f.e.oldJob, f.e.freshJob, f.attempt, oldId));
    }
    static void terminalize(Fresh fresh, NativeFaultedJobReplacement.ContinuationPermit permit) throws Exception {
        Map<String,Object> terminal = freshOperation(fresh);
        terminal.put("state", "outcome_unknown");
        terminal.put("native_completion", m("native_wrapper_completed", true, "native_wrapper_succeeded", false, "physical_outcome_verified", false));
        fresh.fixture.e.append("operation", terminal);
        fresh.fixture.e.terminalWrappers.add(fresh.operation); // Explicit synthetic wrapper authority, not a native completion claim.
        fresh.revoke();
        permit.close();
    }
    static void replayEvents(Env target, List<Map<String,Object>> events) throws Exception {
        // Current production Bridge order: common validation BEFORE child reducers, commit AFTER.
        // Frozen legacy Env.replay remains untouched and is deliberately not used for new mutations.
        for (Map<String,Object> event : events) {
            String type = (String) event.get("type"); Map<String,Object> payload = o(event.get("payload"));
            Runnable continuation = target.replacement.prepareContinuationObservation(type, payload);
            target.material.recoverEvent(type, payload);
            target.boards.recoverEvent(type, payload); target.boards.observeNativeEvent(type, payload);
            target.lineage.observe(type, payload); target.replacement.observe(type, payload);
            if (type.startsWith("faulted_job_replacement_")) target.replacement.recover(type, payload);
            continuation.run();
        }
        target.material.finishRecovery(); target.boards.finishRecovery();
    }
    static void commonValidator(Fixture f, String name) throws Exception {
        int index = -1;
        for (int i = 0; i < f.e.events.size(); i++) if (Set.of("material_continuation_intent", "board_continuation_intent").contains(f.e.events.get(i).get("type"))) { index = i; break; }
        if (index < 0) throw new AssertionError("No forced mutation intent available for common-validator qualification");
        Map<String,Object> event = f.e.events.get(index), payload = o(event.get("payload")); String type = (String) event.get("type");
        Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
        try (Env reduced = new Env(name + "-common-before-force", true)) {
            replayEvents(reduced, f.e.events.subList(0, index));
            check(reduced.replacement.prepareContinuationObservation(type, payload) != null, "Canonical intent validates against exact reducer prefix before force");
            for (String key : List.of("old_load_id", "recovery_operation_id", "continuation_id", "phase_row_sha256", "mode")) {
                Map<String,Object> malformed = NativeFaultedJobReplacement.mutable(payload);
                malformed.put(key, key.endsWith("sha256") ? "f".repeat(64) : key.equals("mode") ? "adopt-zero-counter" : id());
                refuse("common pre-force validator rejects altered " + key, () -> reduced.replacement.prepareContinuationObservation(type, malformed));
            }
            Map<String,Object> omitted = NativeFaultedJobReplacement.mutable(payload); omitted.remove("phase_row_sha256");
            refuse("common pre-force validator rejects omitted exact phase", () -> reduced.replacement.prepareContinuationObservation(type, omitted));
            check(Files.size(reduced.file) == 0, "Pre-force validation creates no receipt or native authority");
        }
        List<Map<String,Object>> badRecords = new ArrayList<>(f.e.events);
        Map<String,Object> badEvent = NativeFaultedJobReplacement.mutable(event), badPayload = o(badEvent.get("payload"));
        badPayload.put("recovery_operation_id", id()); badRecords.set(index, badEvent);
        try (Env reduced = new Env(name + "-common-bad-replay", true)) {
            refuse("common replay validator rejects changed local operation", () -> replayEvents(reduced, badRecords));
            check(Files.size(reduced.file) == 0, "Malformed replay appends no substitute event");
        }
        check(counts.equals(counters()) && files.equals(configurationFiles()), "Valid and refused pure common validation/replay repeats no setter/save");
    }
    static void replay(Fixture f, String name, String kind, String oldId, String expectedPhase) throws Exception {
        Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
        try (Env replay = new Env(name + "-replay", true)) {
            replayEvents(replay, f.e.events);
            Map<String,Object> progress = replay.replacement.progress(f.attempt), selected = null;
            for (Object value : (List<?>) o(progress.get(kind)).get("rows")) if (oldId.equals(o(value).get("old_load_id"))) selected = o(value);
            check(selected != null && expectedPhase.equals(phase(selected)), "Replay classifies exactly the actually forced effective phase");
            check(counters().equals(counts) && configurationFiles().equals(files) && Files.size(replay.file) == 0, "Read/replay performs zero tray reset, native save, or forced append");
            check(!replay.replacement.ownsCandidate(f.e.candidate), "Replay acquires no retained candidate identity");
            for (Object raw : (List<?>) replay.material.snapshot().get("loads")) check(Boolean.FALSE.equals(o(raw).get("native_authority")), "Replay restores no material authority");
            refuse("replay native board readiness", () -> task(() -> { replay.boards.requireReady(f.e.freshJob); return null; }));
            refuse("replay continuation capture without native candidate", () -> task(() -> replay.replacement.captureContinuation(f.attempt, f.e.oldJob)));
            check(Boolean.FALSE.equals(progress.get("execution_authority_restored")), "Replay grants no execution readiness");
        }
    }
    static void rejectLateOutcome(Fixture f, String kind, Map<String,Object> intentLink, String name) throws Exception {
        String type = ((String) intentLink.get("type")).replace("_intent", "_outcome");
        Map<String,Object> late = NativeFaultedJobReplacement.mutable(o(intentLink.get("payload"))); o(late.get("new_load")).put("state", "loaded");
        Map<String,Object> before = f.e.replacement.progress(f.attempt); long bytes = Files.size(f.e.file);
        Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
        refuse(name, () -> {
            if (kind.equals("material")) f.e.append(type, late); // Material validates before this sink can force.
            else {
                // Read-only call to the same private native-board pre-force reducer validation.
                Method validator = NativeBoardLoads.class.getDeclaredMethod(type.startsWith("board_continuation_") ? "prepareContinuationStep" : "prepareReplacement", String.class, Map.class);
                validator.setAccessible(true);
                try { validator.invoke(f.e.boards, type, late); }
                catch (InvocationTargetException failure) { if (failure.getCause() instanceof Error) throw (Error) failure.getCause(); throw (Exception) failure.getCause(); }
            }
        });
        check(bytes == Files.size(f.e.file) && counts.equals(counters()) && files.equals(configurationFiles()) && NativeFaultedJobReplacement.same(before, f.e.replacement.progress(f.attempt)), "Late superseded outcome is rejected before forced record, reducer state, or native effects");
    }
    static void stepAuthorityNegatives() throws Exception {
        int start = assertions;
        try (Fixture f = new Fixture("mutation-step-authority", "lineage")) {
            f.closeOriginal("outcome_unknown"); Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin();
            String oldId = (String) rows(f, "material").get(0).get("old_load_id");
            long bytes = Files.size(f.e.file); Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
            task(() -> {
                refuse("foreign original material load ID", () -> permit.beginMaterialStep(f.e.material, id()));
                refuse("omitted original material load ID", () -> permit.beginMaterialStep(f.e.material, null));
                refuse("foreign native candidate Job", () -> permit.beginBoardStep(f.e.boards, f.e.oldJob, f.e.oldJob, f.attempt, (String) rows(f, "boards").get(0).get("old_load_id")));
                return null;
            });
            var step = task(() -> permit.beginMaterialStep(f.e.material, oldId));
            Map<String,Object> fabricated = m("receipt_id", step.stepId(), "step_id", step.stepId(), "old_load_id", oldId);
            task(() -> {
                refuse("Step outcome before any forced intent", () -> step.acceptForcedEvent("material_continuation_outcome", fabricated));
                refuse("Step intent not actually forced", () -> step.acceptForcedEvent("material_continuation_intent", fabricated));
                permit.check(); return null;
            });
            check(bytes == Files.size(f.e.file) && counts.equals(counters()) && files.equals(configurationFiles()), "Step capability refusals force no records and perform no native mutation");
            step.close(); task(() -> { refuse("abandoned uncompleted step closes its parent permit", permit::check); return null; });
            terminalize(fresh, permit);
            results.add(m("name", "step-authority-negatives", "assertions", assertions - start));
        }
    }
    static void positive(String prefix) throws Exception {
        int start = assertions;
        try (Fixture f = new Fixture("mutation-positive-" + prefix, prefix)) {
            f.closeOriginal("outcome_unknown");
            Map<String,Object> original = originalFacts(f); List<Map<String,Object>> oldEvents = List.copyOf(f.e.events);
            Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement");
            var permit = fresh.begin();
            task(() -> f.e.lineage.adoptCompletedReplacement(permit, f.attempt));
            int mutations = 0, adoptions = 0;
            for (String kind : List.of("material", "boards")) for (Map<String,Object> before : rows(f, kind)) {
                String oldId = (String) before.get("old_load_id");
                Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
                if ("completed".equals(phase(before))) {
                    adopt(f, permit, kind, oldId); adoptions++;
                    check(counts.equals(counters()) && files.equals(configurationFiles()), "Completed step adoption performs no setter/save");
                    long size = Files.size(f.e.file);
                    refuse("completed step reset", () -> mutate(f, permit, kind, oldId));
                    check(size == Files.size(f.e.file), "Completed-step mutation refusal precedes any new intent");
                } else {
                    refuse("untouched or unknown state is not completed adoption", () -> adopt(f, permit, kind, oldId));
                    Set<String> priorIds = new HashSet<>(); priorIds.add(oldId);
                    if (before.get("new_load") != null) priorIds.add((String) o(before.get("new_load")).get("load_id"));
                    Map<String,Object> receipt = mutate(f, permit, kind, oldId); mutations++;
                    Map<String,Object> after = row(f, kind, oldId);
                    check("completed".equals(phase(after)), "Only successful native work plus forced outcome completes the new generation");
                    check(!priorIds.contains(newLoad(after).get("load_id")), "Continuation allocates a further new load identity");
                    check(oldId.equals(receipt.get("old_load_id")), "Outcome binds exact original old load");
                    check(before.get("phase").equals(after.get("phase")), "Original phase remains immutable beside effective progress");
                    if (kind.equals("material")) {
                        check(nativeTray(after).getFeedCount() == 0, "Actual native tray setter establishes the new generation counter");
                        check(!files.equals(configurationFiles()), "Actual Configuration.save changed private persisted configuration");
                    } else check(counts.equals(counters()) && files.equals(configurationFiles()), "Board identity records do not reset trays or save unrelated native configuration");
                    if ("pending".equals(before.get("phase"))) rejectLateOutcome(f, kind, o(before.get("parent_intent")), "late original outcome after a new generation supersedes it");
                    long size = Files.size(f.e.file);
                    refuse("same permit cannot reset a completed new generation", () -> mutate(f, permit, kind, oldId));
                    check(size == Files.size(f.e.file), "Repeated completed mutation forces no second intent");
                }
                preserved(f, original, oldEvents);
                task(() -> { permit.check(); return null; });
            }
            refuse("component continuation alone does not confirm boards", () -> task(() -> { f.e.boards.requireReady(f.e.freshJob); return null; }));
            terminalize(fresh, permit);
            commonValidator(f, "mutation-positive-" + prefix);
            for (String kind : List.of("material", "boards")) for (Map<String,Object> r : rows(f, kind)) replay(f, "mutation-positive-" + prefix + "-" + kind + "-" + r.get("old_load_id"), kind, (String) r.get("old_load_id"), "completed");
            Files.writeString(root.resolve("mutation-positive-" + prefix + "-progress.json"), JSON.toJson(f.e.replacement.progress(f.attempt)) + "\n");
            results.add(m("name", "positive-" + prefix, "assertions", assertions - start, "mutations", mutations, "completed_adoptions", adoptions));
        }
    }
    static void forcedBoundary(String kind, String eventSuffix, boolean afterForce) throws Exception {
        String name = "mutation-force-" + kind + "-" + eventSuffix + "-" + (afterForce ? "after" : "before");
        int start = assertions;
        try (Fixture f = new Fixture(name, "lineage")) {
            f.closeOriginal("outcome_unknown"); Map<String,Object> original = originalFacts(f); List<Map<String,Object>> oldEvents = List.copyOf(f.e.events);
            String oldId = (String) rows(f, kind).get(0).get("old_load_id");
            Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin();
            Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
            String type = kind.equals("material") ? "material_continuation_" + eventSuffix : "board_continuation_" + eventSuffix;
            f.e.failType = type; f.e.failAfterForce = afterForce;
            refuse("controlled " + type + " force boundary", () -> mutate(f, permit, kind, oldId)); f.e.failType = null;
            List<Map<String,Object>> relevant = new ArrayList<>();
            for (Map<String,Object> event : f.e.events) if (type.equals(event.get("type"))) relevant.add(event);
            check(relevant.size() == (afterForce ? 1 : 0), "Only completed FileChannel.force creates the targeted durable record");
            if (eventSuffix.equals("intent")) check(counts.equals(counters()) && files.equals(configurationFiles()), "Intent force failure prevents the native setter/save");
            else if (kind.equals("material")) {
                check(nativeTray(row(f, kind, oldId)).getFeedCount() == 0, "Outcome-force interruption follows actual native counter reset");
                check(!files.equals(configurationFiles()), "Outcome-force interruption follows actual successful native save");
            }
            long bytes = Files.size(f.e.file); Map<String,Integer> failedCounts = counters(); Map<String,String> failedFiles = configurationFiles();
            refuse("same failed step cannot resume automatically", () -> mutate(f, permit, kind, oldId));
            check(bytes == Files.size(f.e.file) && failedCounts.equals(counters()) && failedFiles.equals(configurationFiles()), "Failure replay has no repeated native effects or intent");
            preserved(f, original, oldEvents); permit.close();
            String expected = eventSuffix.equals("intent") && !afterForce ? "untouched" : eventSuffix.equals("outcome") && afterForce ? "completed" : "pending";
            replay(f, name, kind, oldId, expected);
            results.add(m("name", name, "assertions", assertions - start, "target_forced", afterForce, "replayed_effective_phase", expected));
        }
    }
    static final class NativeSetterInterruption extends RuntimeException {
        NativeSetterInterruption() { super("controlled native setter interruption after feedCount changed"); }
    }
    static void observedNativeFailure(String expected, Checked action) throws Exception {
        try { action.run(); throw new AssertionError("Expected native failure: " + expected); }
        catch (Exception failure) {
            boolean found = false;
            for (Throwable cause = failure; cause != null; cause = cause.getCause()) if (String.valueOf(cause.getMessage()).contains(expected)) found = true;
            if (!found) throw failure;
            assertions++; rejected++;
        }
    }
    static void successiveUnknownMaterial() throws Exception {
        int start = assertions;
        try (Fixture f = new Fixture("mutation-successive-native-failures", "lineage")) {
            f.closeOriginal("outcome_unknown");
            Map<String,Object> original = originalFacts(f); List<Map<String,Object>> oldEvents = List.copyOf(f.e.events);
            String oldId = (String) rows(f, "material").get(0).get("old_load_id");
            ReferenceTrayFeeder nativeFeeder = nativeTray(row(f, "material", oldId));
            Map<String,Map<String,Object>> unknownLoads = new LinkedHashMap<>();
            List<String> priorRecoveryOperations = new ArrayList<>();
            Map<String,Object> priorIntent = null;
            for (int generation = 1; generation <= 3; generation++) {
                var capture = f.capture();
                check(((List<?>) capture.dependencies().get("operation_ids")).containsAll(priorRecoveryOperations), "Fresh generation binds every previous local operation");
                Fresh fresh = f.fresh(capture, "continue-faulted-job-replacement"); var permit = fresh.begin();
                Map<String,String> files = configurationFiles(); int eventCount = f.e.events.size();
                if (generation == 1) {
                    check(nativeFeeder.getFeedCount() > 0, "First actual setter interruption starts from a nonzero native counter");
                    int[] observed = {0};
                    PropertyChangeListener listener = event -> { observed[0]++; throw new NativeSetterInterruption(); };
                    nativeFeeder.addPropertyChangeListener("feedCount", listener);
                    try { observedNativeFailure("controlled native setter interruption", () -> mutate(f, permit, "material", oldId)); }
                    finally { nativeFeeder.removePropertyChangeListener("feedCount", listener); }
                    check(observed[0] == 1 && nativeFeeder.getFeedCount() == 0, "Actual native setter returned through its property event after changing the counter");
                    check(files.equals(configurationFiles()), "Post-setter interruption precedes Configuration.save");
                } else if (generation == 2) {
                    check(nativeFeeder.getFeedCount() == 0 && "pending".equals(phase(row(f, "material", oldId))), "Native zero still has a pending previous-generation outcome");
                    refuse("unknown zero counter is never adopted as completed", () -> adopt(f, permit, "material", oldId));
                    Path blockedBackups = root.resolve("native-save-backups-is-file"); Files.writeString(blockedBackups, "controlled private save failure");
                    String previous = System.getProperty("backups"); System.setProperty("backups", blockedBackups.toString());
                    try { observedNativeFailure("Error while saving machine.xml", () -> mutate(f, permit, "material", oldId)); }
                    finally { if (previous == null) System.clearProperty("backups"); else System.setProperty("backups", previous); }
                    check(files.equals(configurationFiles()), "Actual Configuration.save failure leaves original configuration file facts intact");
                } else {
                    refuse("latest unknown zero is never adopted as completed", () -> adopt(f, permit, "material", oldId));
                    mutate(f, permit, "material", oldId);
                    check(!files.equals(configurationFiles()), "Final generation performs actual successful Configuration.save");
                }
                Map<String,Object> current = row(f, "material", oldId), load = newLoad(current);
                String newId = (String) load.get("load_id");
                if (priorIntent != null) rejectLateOutcome(f, "material", priorIntent, "late continuation outcome after supersession");
                priorIntent = o(current.get("effective_parent_intent"));
                check(!oldId.equals(newId) && !unknownLoads.containsKey(newId), "Every decision allocates a further new identity");
                check((generation == 3 ? "completed" : "pending").equals(phase(current)), "Only the successfully saved and forced generation is complete");
                for (Map.Entry<String,Map<String,Object>> previous : unknownLoads.entrySet()) {
                    Map<String,Object> retained = o(o(f.e.material.captureReplacementState().get("loads")).get(previous.getKey()));
                    check(NativeFaultedJobReplacement.same(previous.getValue(), retained) && "loading_unknown".equals(retained.get("state")), "Superseded unknown-zero generation is retained exactly as unknown");
                }
                if (generation < 3) {
                    unknownLoads.put(newId, NativeFaultedJobReplacement.frozen(load));
                    long bytes = Files.size(f.e.file); Map<String,String> failedFiles = configurationFiles();
                    task(() -> { refuse("native failure fences its current continuation permit", permit::check); return null; });
                    refuse("native failure cannot automatically retry its step", () -> mutate(f, permit, "material", oldId));
                    check(bytes == Files.size(f.e.file) && failedFiles.equals(configurationFiles()), "Closed current permit does not reset/save or issue another identity");
                }
                check(f.e.events.subList(eventCount, f.e.events.size()).stream().filter(e -> "material_continuation_intent".equals(e.get("type"))).count() == 1, "One new local generation forces exactly one fresh intent");
                preserved(f, original, oldEvents); terminalize(fresh, permit); priorRecoveryOperations.add(fresh.operation);
            }
            Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
            Fresh adoptFresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var adoptionPermit = adoptFresh.begin();
            Map<String,Object> receipt = adopt(f, adoptionPermit, "material", oldId);
            check(((Number) receipt.get("schema_version")).intValue() == 2, "Chained completed adoption identifies its newer parent schema");
            check(counts.equals(counters()) && files.equals(configurationFiles()), "Fresh adoption of final completed generation repeats no native work");
            preserved(f, original, oldEvents); terminalize(adoptFresh, adoptionPermit);
            replay(f, "mutation-successive-native-failures", "material", oldId, "completed");
            Files.writeString(root.resolve("mutation-successive-native-failures-progress.json"), JSON.toJson(f.e.replacement.progress(f.attempt)) + "\n");
            results.add(m("name", "successive-native-failures", "assertions", assertions - start, "unknown_generations_retained", unknownLoads.size(), "local_mutation_decisions", 3));
        }
    }
    static void successiveUnknownBoards() throws Exception {
        int start = assertions;
        try (Fixture f = new Fixture("mutation-successive-board-revocations", "lineage")) {
            f.closeOriginal("outcome_unknown"); Map<String,Object> original = originalFacts(f); List<Map<String,Object>> oldEvents = List.copyOf(f.e.events);
            String oldId = (String) rows(f, "boards").get(0).get("old_load_id");
            Map<String,Map<String,Object>> unknownLoads = new LinkedHashMap<>(); Map<String,Object> priorIntent = null;
            Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
            for (int generation = 1; generation <= 3; generation++) {
                Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin();
                if (generation < 3) {
                    // Bounded failure injection only: preserve the exact real sink/force and then
                    // revoke the actual coordinator permit. No fabricated authority or reducer commit.
                    Field sinkField = NativeBoardLoads.class.getDeclaredField("sink"); sinkField.setAccessible(true);
                    NativeBoardLoads.Sink actualSink = (NativeBoardLoads.Sink) sinkField.get(f.e.boards); int[] intercepted = {0};
                    NativeBoardLoads.Sink revokingSink = (type, payload) -> {
                        actualSink.append(type, payload);
                        if ("board_continuation_intent".equals(type)) { intercepted[0]++; fresh.revoke(); }
                    };
                    sinkField.set(f.e.boards, revokingSink);
                    try { refuse("actual local coordinator revocation after a known forced board intent", () -> mutate(f, permit, "boards", oldId)); }
                    finally { sinkField.set(f.e.boards, actualSink); }
                    check(intercepted[0] == 1, "Exactly one real board intent was forced before local ownership revocation");
                } else mutate(f, permit, "boards", oldId);
                Map<String,Object> current = row(f, "boards", oldId), load = newLoad(current); String newId = (String) load.get("load_id");
                check(!oldId.equals(newId) && !unknownLoads.containsKey(newId), "Every board decision allocates a further new load UUID");
                Set<Object> boardIds = new HashSet<>(((Map<?,?>) load.get("board_ids")).values());
                check(Collections.disjoint(boardIds, ((Map<?,?>) o(current.get("old_load")).get("board_ids")).values()), "New board identity differs from the original physical-load identity");
                for (Map<String,Object> unknown : unknownLoads.values()) check(Collections.disjoint(boardIds, ((Map<?,?>) unknown.get("board_ids")).values()), "Repeated unknown board supersession allocates further distinct board IDs");
                check((generation == 3 ? "completed" : "pending").equals(phase(current)), "Only a forced board outcome completes the new load generation");
                if (priorIntent != null) rejectLateOutcome(f, "boards", priorIntent, "late superseded continuation board outcome");
                priorIntent = o(current.get("effective_parent_intent"));
                Field retainedLoads = NativeBoardLoads.class.getDeclaredField("loads"); retainedLoads.setAccessible(true);
                Map<String,Object> loadRows = NativeFaultedJobReplacement.frozen(o(retainedLoads.get(f.e.boards))); // Read only: public summaries omit board_ids and exact history.
                for (Map.Entry<String,Map<String,Object>> previous : unknownLoads.entrySet()) {
                    Map<String,Object> retained = o(loadRows.get(previous.getKey()));
                    // Check the actual retained raw record, including internal identity/history fields.
                    for (Map.Entry<String,Object> fact : previous.getValue().entrySet()) check(NativeFaultedJobReplacement.same(fact.getValue(), retained.get(fact.getKey())), "Superseded board unknown load retains durable " + fact.getKey());
                    check("loading_unknown".equals(retained.get("state")), "Superseded board generation remains unknown");
                }
                if (generation < 3) unknownLoads.put(newId, NativeFaultedJobReplacement.frozen(load));
                preserved(f, original, oldEvents);
                check(counts.equals(counters()) && files.equals(configurationFiles()), "Board generations perform no tray setter or native configuration save");
                Map<String,Object> terminal = freshOperation(fresh); terminal.put("state", "outcome_unknown"); terminal.put("native_completion", m("native_wrapper_completed", true, "native_wrapper_succeeded", false, "physical_outcome_verified", false));
                f.e.append("operation", terminal); f.e.terminalWrappers.add(fresh.operation); if (generation == 3) fresh.revoke(); permit.close();
            }
            Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin();
            Map<String,Object> adopted = adopt(f, permit, "boards", oldId);
            check(((Number) adopted.get("schema_version")).intValue() == 2, "Completed chained board adoption retains exact new parent schema");
            check(counts.equals(counters()) && files.equals(configurationFiles()), "Board adoption creates no reset/save effects");
            terminalize(fresh, permit); replay(f, "mutation-successive-board-revocations", "boards", oldId, "completed");
            results.add(m("name", "successive-board-revocations", "assertions", assertions - start, "unknown_generations_retained", unknownLoads.size(), "local_mutation_decisions", 3));
        }
    }
    public static void main(String[] args) throws Exception {
        initialize(Path.of(args[0])); Throwable error = null;
        try {
            for (Constructor<?> constructor : NativeFaultedJobReplacement.Step.class.getDeclaredConstructors()) check(Modifier.isPrivate(constructor.getModifiers()), "Effectful Step constructor is private");
            stepAuthorityNegatives();
            for (String prefix : List.of("lineage", "pending-material", "mixed-board")) positive(prefix);
            successiveUnknownMaterial();
            successiveUnknownBoards();
            for (String kind : List.of("material", "boards")) for (String suffix : List.of("intent", "outcome")) for (boolean after : List.of(false, true)) forcedBoundary(kind, suffix, after);
        } catch (Throwable failure) { error = failure; failure.printStackTrace(); }
        finally {
            config.getMachine().close();
            Map<String,Object> proof = m("passed", error == null, "assertions", assertions, "refusals", rejected, "cases", results, "error", error == null ? null : error.toString(), "initial_fault_fixture", "synthetic sensing/action hooks and explicit synthetic TerminalAuthority; actual native models/setters/saves and FileChannel.force", "actual_native_job_initializations", 0, "actual_native_feeds", 0, "actual_native_placements", 0, "bridge_integration_qualified", false, "full_continuation_qualified", false, "restart_reattachment_qualified", false, "hardware_qualified", false);
            Files.writeString(root.resolve("proof.json"), JSON.toJson(proof) + "\n");
            System.out.println("NATIVE_REPLACEMENT_CONTINUATION_MUTATION_RESULT " + JSON.toJson(proof));
        }
        System.exit(error == null ? 0 : 1);
    }
}
