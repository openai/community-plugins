/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import static org.openpnp.codex.NativeReplacementContinuationTest.*;
import java.io.IOException;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.util.*;

/** Actual native component/documents/forced journal fixture with explicit synthetic initial
 * sensing fault and terminal-wrapper authority. Aggregate completion is not GUI publication,
 * processor admission, physical qualification, or restart reattachment. */
public final class NativeReplacementContinuationCompletionTest {
    static final String OUTCOME = "faulted_job_replacement_continuation_outcome";
    static int assertions, refusals;
    static final List<Map<String,Object>> results = new ArrayList<>();
    interface Checked { void run() throws Exception; }
    static void check(boolean value, String why) { assertions++; if (!value) throw new AssertionError(why); }
    static void refuse(String why, Checked work) throws Exception {
        try { work.run(); throw new AssertionError("Accepted " + why); }
        catch (IOException | Bridge.Fault | NativeJobLineage.Fault expected) { assertions++; refusals++; }
    }
    static List<Map<String,Object>> rows(Fixture f, String kind) throws Exception { return NativeReplacementContinuationMutationTest.rows(f, kind); }
    static String phase(Map<String,Object> row) { return NativeReplacementContinuationMutationTest.phase(row); }
    static void saveDocument(Fixture f, String name) throws Exception {
        Path owned = root.resolve(name + "-owned"); Files.createDirectory(owned);
        NativeReplacementDocuments documents = new NativeReplacementDocuments(config, owned.resolve("documents"), owned);
        task(() -> {
            NativeReplacementDocuments.Context context = new NativeReplacementDocuments.Context(f.e.jobId, f.attempt, f.oldRecovery, f.e.faultCapture.digest);
            NativeReplacementDocuments.Saved saved = documents.save(context, f.e.candidate);
            f.e.replacement.recordDocument(f.e.permit, saved);
            check(Files.isRegularFile(owned.resolve("documents").resolve(saved.documentId + ".zip")), "Actual candidate graph document is persisted before the original permit closes");
            return null;
        });
    }
    static Map<String,Object> complete(Fixture f, NativeFaultedJobReplacement.ContinuationPermit permit) throws Exception {
        return task(() -> f.e.replacement.completeContinuation(permit));
    }
    static void lineage(Fixture f, NativeFaultedJobReplacement.ContinuationPermit permit) throws Exception {
        Map<String,Object> before = o(f.e.replacement.progress(f.attempt).get("lineage"));
        if ("completed".equals(phase(before))) task(() -> f.e.lineage.adoptCompletedReplacement(permit, f.attempt));
        else {
            task(() -> f.e.lineage.continueReplacement(permit, f.attempt));
            Map<String,Object> after = o(f.e.replacement.progress(f.attempt).get("lineage"));
            check("completed".equals(phase(after)), "Fresh continuation creates the actual replacement lineage receipt");
            check(before.get("phase").equals(after.get("phase")) && after.get("receipt_id") == null && after.get("receipt_sha256") == null && after.get("receipt") == null, "Fresh lineage preserves untouched original phase and null original receipt fields");
            long bytes = Files.size(f.e.file); refuse("duplicate fresh lineage creation", () -> task(() -> f.e.lineage.continueReplacement(permit, f.attempt)));
            check(bytes == Files.size(f.e.file), "Duplicate lineage creation is refused before another record");
        }
    }
    static void loads(Fixture f, NativeFaultedJobReplacement.ContinuationPermit permit, String kind, Set<String> skip) throws Exception {
        for (Map<String,Object> row : rows(f, kind)) {
            String oldId = (String) row.get("old_load_id"); if (skip.contains(oldId)) continue;
            if ("completed".equals(phase(row))) NativeReplacementContinuationMutationTest.adopt(f, permit, kind, oldId);
            else NativeReplacementContinuationMutationTest.mutate(f, permit, kind, oldId);
        }
    }
    static void fill(Fixture f, NativeFaultedJobReplacement.ContinuationPermit permit) throws Exception {
        lineage(f, permit); loads(f, permit, "material", Set.of()); loads(f, permit, "boards", Set.of());
    }
    static List<Map<String,Object>> descriptors(Map<String,Object> receipt) throws Exception {
        List<Map<String,Object>> values = new ArrayList<>(); values.add(o(receipt.get("lineage_receipt")));
        for (String field : List.of("material_receipts", "board_receipts")) for (Object raw : (List<?>) receipt.get(field)) values.add(o(raw));
        return values;
    }
    static void selectedRecords(Fixture f, Fresh fresh, Map<String,Object> receipt) throws Exception {
        check(fresh.operation.equals(receipt.get("recovery_operation_id")) && fresh.taskId.equals(receipt.get("task_id")), "Aggregate binds the exact currently active local decision");
        check(f.attempt.equals(receipt.get("replacement_attempt_id")), "Aggregate identifies the retained replacement attempt");
        check(Boolean.TRUE.equals(receipt.get("original_outcomes_preserved")) && Boolean.TRUE.equals(receipt.get("new_attempt_requires_validation")) && Boolean.FALSE.equals(receipt.get("native_job_initialized")) && Boolean.FALSE.equals(receipt.get("execution_authority_restored")), "Aggregate preserves old outcomes and requires fresh execution validation");
        Set<String> selected = new HashSet<>();
        for (Map<String,Object> descriptor : descriptors(receipt)) {
            check(selected.add((String) descriptor.get("receipt_id")), "Each selected component receipt identity occurs once");
            Map<String,Object> forced = null;
            for (Map<String,Object> event : f.e.events) if (descriptor.get("receipt_type").equals(event.get("type")) && descriptor.get("receipt_id").equals(o(event.get("payload")).get("receipt_id"))) forced = o(event.get("payload"));
            check(forced != null, "Selected component receipt exists in the actual forced event stream");
            check(NativeFaultedJobReplacement.digest(forced).equals(descriptor.get("receipt_sha256")), "Selected receipt digest matches the exact owned record");
            check(fresh.operation.equals(forced.get("recovery_operation_id")), "Every selected disposition belongs to this decision, not an earlier permit");
            check(Boolean.TRUE.equals(descriptor.get("old_outcome_preserved")), "Each selected component preserves its old outcome");
        }
        for (String kind : List.of("material", "boards")) {
            Set<Object> required = new HashSet<>(), actual = new HashSet<>();
            for (Map<String,Object> row : rows(f, kind)) required.add(row.get("old_load_id"));
            for (Object raw : (List<?>) receipt.get(kind.equals("material") ? "material_receipts" : "board_receipts")) {
                Map<String,Object> descriptor = o(raw); actual.add(descriptor.get("old_load_id"));
                Map<String,Object> row = NativeReplacementContinuationMutationTest.row(f, kind, (String) descriptor.get("old_load_id"));
                check(NativeReplacementContinuationMutationTest.newLoad(row).get("load_id").equals(descriptor.get("new_load_id")), "Selected load is the exact current completed generation");
            }
            check(required.equals(actual), "Aggregate covers the exact full original " + kind + " union");
        }
        Map<String,Object> progress = f.e.replacement.progress(f.attempt);
        check(o(progress.get("definition")).get("record_sha256").equals(receipt.get("definition_sha256")), "Aggregate binds the forced exact native candidate definition");
        check(o(progress.get("document")).get("record_sha256").equals(receipt.get("document_sha256")), "Aggregate binds the real saved graph document record");
        check(Boolean.FALSE.equals(o(progress.get("document")).get("storage_verified_by_this_read")), "Receipt reading does not claim a fresh storage attestation");
    }
    static void replay(Fixture f, Fresh fresh, String name, boolean hasOutcome) throws Exception {
        Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
        try (Env reduced = new Env(name + "-replay", true)) {
            NativeReplacementContinuationMutationTest.replayEvents(reduced, f.e.events);
            // Lookup by the exact continuation ID recorded in this fresh decision's aggregate/intent.
            String continuation = null;
            for (Object raw : o(reduced.replacement.status().get("continuation_intents")).values()) if (fresh.operation.equals(o(raw).get("recovery_operation_id"))) continuation = (String) o(raw).get("continuation_id");
            check(continuation != null, "Replay retains the exact historical local continuation intent");
            Map<String,Object> receipt = reduced.replacement.continuationReceipt(continuation);
            check((receipt != null) == hasOutcome, "Replay retains only an actually forced aggregate outcome");
            check(counters().equals(counts) && configurationFiles().equals(files) && Files.size(reduced.file) == 0, "Aggregate replay has zero setter/save/journal effects");
            check(!reduced.replacement.ownsCandidate(f.e.candidate), "Aggregate replay reconstructs no native candidate ownership");
            check(Boolean.FALSE.equals(reduced.replacement.status().get("execution_authority_restored")), "Aggregate replay restores no execution authority");
            refuse("aggregate replay cannot mint continuation authority", () -> task(() -> reduced.replacement.captureContinuation(f.attempt, f.e.oldJob)));
            refuse("aggregate replay cannot confirm native boards", () -> task(() -> { reduced.boards.requireReady(f.e.freshJob); return null; }));
            for (Object raw : (List<?>) reduced.material.snapshot().get("loads")) check(Boolean.FALSE.equals(o(raw).get("native_authority")), "Aggregate replay restores no material binding");
        }
    }
    static void malformedReplay(Fixture f, String name) throws Exception {
        int outcomeIndex = -1;
        for (int i = 0; i < f.e.events.size(); i++) if (OUTCOME.equals(f.e.events.get(i).get("type"))) outcomeIndex = i;
        if (outcomeIndex < 0) throw new AssertionError("No actual aggregate to mutate for reducer-negative cases");
        Map<String,Object> actual = o(f.e.events.get(outcomeIndex).get("payload"));
        Map<String,Map<String,Object>> invalid = new LinkedHashMap<>();
        for (String field : List.of("progress_sha256", "definition_sha256", "document_sha256", "continuation_capture_sha256")) { Map<String,Object> changed = NativeFaultedJobReplacement.mutable(actual); changed.put(field, "f".repeat(64)); invalid.put("changed-" + field, changed); }
        for (String field : List.of("material_receipts", "board_receipts")) {
            Map<String,Object> missing = NativeFaultedJobReplacement.mutable(actual); ((List<?>) missing.get(field)).remove(0); invalid.put("missing-" + field, missing);
            Map<String,Object> duplicate = NativeFaultedJobReplacement.mutable(actual); List<Object> records = (List<Object>) duplicate.get(field); records.set(1, NativeFaultedJobReplacement.mutable(o(records.get(0)))); invalid.put("duplicated-" + field, duplicate);
            for (String key : List.of("old_load_id", "new_load_id", "receipt_id", "receipt_type", "receipt_sha256")) {
                Map<String,Object> changed = NativeFaultedJobReplacement.mutable(actual); Map<String,Object> descriptor = o(((List<?>) changed.get(field)).get(0));
                descriptor.put(key, key.endsWith("sha256") ? "f".repeat(64) : key.equals("receipt_type") ? "foreign-component-outcome" : id()); invalid.put(field + "-foreign-" + key, changed);
            }
        }
        Map<String,Object> noLineage = NativeFaultedJobReplacement.mutable(actual); noLineage.remove("lineage_receipt"); invalid.put("missing-lineage", noLineage);
        Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles();
        for (Map.Entry<String,Map<String,Object>> test : invalid.entrySet()) try (Env reduced = new Env(name + "-invalid-" + test.getKey(), true)) {
            NativeReplacementContinuationMutationTest.replayEvents(reduced, f.e.events.subList(0, outcomeIndex));
            refuse("aggregate reducer " + test.getKey(), () -> reduced.replacement.recover(OUTCOME, test.getValue()));
            check(reduced.replacement.continuationReceipt((String) actual.get("continuation_id")) == null && Files.size(reduced.file) == 0, "Invalid aggregate selects no receipt and forces no record");
        }
        check(counts.equals(counters()) && files.equals(configurationFiles()), "Malformed aggregate replay repeats no native effects");
    }
    static void positive(String prefix) throws Exception {
        int start = assertions; String name = "completion-positive-" + prefix;
        try (Fixture f = new Fixture(name, prefix)) {
            saveDocument(f, name); f.closeOriginal("outcome_unknown");
            Map<String,Object> originals = NativeReplacementContinuationMutationTest.originalFacts(f), oldCompound = o(f.e.replacement.progress(f.attempt).get("compound"));
            Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin(); fill(f, permit);
            Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles(); Map<String,Object> nativeBefore = state(f.e);
            Map<String,Object> receipt = complete(f, permit);
            check(NativeFaultedJobReplacement.same(receipt, f.e.replacement.continuationReceipt(permit.continuationId())), "Aggregate reads back as the exact forced owned receipt");
            selectedRecords(f, fresh, receipt);
            check(counts.equals(counters()) && files.equals(configurationFiles()) && NativeFaultedJobReplacement.same(nativeBefore, state(f.e)), "Aggregate creates no native counter/load/lineage/binding/save changes");
            check(NativeFaultedJobReplacement.same(originals, NativeReplacementContinuationMutationTest.originalFacts(f)), "Original operations/actions/load facts/native history remain immutable");
            check(NativeFaultedJobReplacement.same(oldCompound, f.e.replacement.progress(f.attempt).get("compound")), "Continuation completion leaves the original compound receipt unchanged");
            long bytes = Files.size(f.e.file); refuse("duplicate completed aggregate", () -> complete(f, permit));
            for (String kind : List.of("material", "boards")) for (Map<String,Object> row : rows(f, kind)) {
                String oldId = (String) row.get("old_load_id");
                refuse("completed decision cannot add further " + kind + " adoption", () -> NativeReplacementContinuationMutationTest.adopt(f, permit, kind, oldId));
                refuse("completed decision cannot add further " + kind + " mutation", () -> NativeReplacementContinuationMutationTest.mutate(f, permit, kind, oldId));
            }
            refuse("completed decision cannot add lineage adoption", () -> task(() -> f.e.lineage.adoptCompletedReplacement(permit, f.attempt)));
            task(() -> { permit.check(); return null; });
            check(bytes == Files.size(f.e.file), "Duplicate aggregate and post-completion child effects append no records");
            malformedReplay(f, name); NativeReplacementContinuationMutationTest.terminalize(fresh, permit); replay(f, fresh, name, true);
            Files.writeString(root.resolve(name + "-receipt.json"), JSON.toJson(receipt) + "\n"); results.add(m("name", name, "assertions", assertions - start));
        }
    }
    static void convenience() throws Exception {
        int start = assertions; String name = "completion-owned-orchestrator";
        try (Fixture f = new Fixture(name, "definition")) {
            saveDocument(f, name); f.closeOriginal("outcome_unknown");
            Map<String,Object> original = NativeReplacementContinuationMutationTest.originalFacts(f);
            Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin();
            Map<String,Object> receipt = task(() -> f.e.replacement.continueReplacement(permit));
            selectedRecords(f, fresh, receipt);
            check(NativeFaultedJobReplacement.same(original, NativeReplacementContinuationMutationTest.originalFacts(f)), "Owned coordinator orchestration preserves original native/ledger facts");
            check(NativeFaultedJobReplacement.same(receipt, f.e.replacement.continuationReceipt(permit.continuationId())), "Owned orchestration returns the exact forced aggregate");
            long bytes = Files.size(f.e.file); refuse("sealed convenience orchestration cannot repeat native steps", () -> task(() -> f.e.replacement.continueReplacement(permit)));
            check(bytes == Files.size(f.e.file), "Repeated convenience orchestration forces no additional child records");
            NativeReplacementContinuationMutationTest.terminalize(fresh, permit); replay(f, fresh, name, true);
            results.add(m("name", name, "assertions", assertions - start));
        }
    }
    static void incompleteAndPriorDecision() throws Exception {
        int start = assertions;
        try (Fixture f = new Fixture("completion-incomplete", "definition")) {
            saveDocument(f, "completion-incomplete"); f.closeOriginal("outcome_unknown"); Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin();
            long bytes = Files.size(f.e.file); refuse("untouched lineage/material/board union", () -> complete(f, permit)); check(bytes == Files.size(f.e.file), "Incomplete initial union refuses before force");
            lineage(f, permit); loads(f, permit, "material", Set.of());
            String lastBoard = (String) rows(f, "boards").get(1).get("old_load_id"); loads(f, permit, "boards", Set.of(lastBoard));
            bytes = Files.size(f.e.file); refuse("one omitted current board component", () -> complete(f, permit)); check(bytes == Files.size(f.e.file), "Missing one root refuses before force");
            loads(f, permit, "boards", Set.of((String) rows(f, "boards").get(0).get("old_load_id"))); complete(f, permit);
            NativeReplacementContinuationMutationTest.terminalize(fresh, permit);
        }
        try (Fixture f = new Fixture("completion-prior-decision", "definition")) {
            saveDocument(f, "completion-prior-decision"); f.closeOriginal("outcome_unknown"); Fresh first = f.fresh(f.capture(), "continue-faulted-job-replacement"); var firstPermit = first.begin(); fill(f, firstPermit); Map<String,Object> priorCompletion = complete(f, firstPermit);
            NativeReplacementContinuationMutationTest.terminalize(first, firstPermit);
            var laterCapture = f.capture(); check(o(laterCapture.payload.get("prior_completions")).size() == 1, "Later decision captures the exact earlier aggregate");
            Fresh second = f.fresh(laterCapture, "continue-faulted-job-replacement"); var secondPermit = second.begin();
            refuse("fresh decision cannot repeat completed lineage creation", () -> task(() -> f.e.lineage.continueReplacement(secondPermit, f.attempt)));
            Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles(); long bytes = Files.size(f.e.file);
            refuse("prior decision component receipts cannot complete a fresh decision without adoption", () -> complete(f, secondPermit));
            check(bytes == Files.size(f.e.file), "Prior decision aggregate refusal forces no receipt");
            fill(f, secondPermit); Map<String,Object> receipt = complete(f, secondPermit); selectedRecords(f, second, receipt);
            check(NativeFaultedJobReplacement.same(priorCompletion, f.e.replacement.continuationReceipt(firstPermit.continuationId())), "Earlier aggregate remains exact after a later decision");
            check(counts.equals(counters()) && files.equals(configurationFiles()), "Fresh exact adoption of completed prior generations repeats no native effects");
            NativeReplacementContinuationMutationTest.terminalize(second, secondPermit);
        }
        try (Fixture f = new Fixture("completion-missing-document", "completed")) {
            f.closeOriginal("outcome_unknown"); Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin(); fill(f, permit);
            long bytes = Files.size(f.e.file); refuse("missing forced real native candidate document", () -> complete(f, permit)); check(bytes == Files.size(f.e.file), "Missing document refuses before aggregate force");
            NativeReplacementContinuationMutationTest.terminalize(fresh, permit);
        }
        results.add(m("name", "incomplete-prior-and-missing-document", "assertions", assertions - start));
    }
    static void applyLineageDrift(Fixture f, String kind) throws Exception {
        task(() -> {
            switch (kind) {
                case "content": f.e.lineage.contentWillChange(f.attempt, id()); break;
                case "reservation": try (var reservation = f.e.lineage.reserve(f.attempt, 1, id(), "e".repeat(64), List.of("new-native-definition-id"))) {} break;
                case "admission": f.e.append("operation", m("operation_id", id(), "job_id", f.attempt, "job_revision", f.e.boards.jobRevision(), "board_load_revision", f.e.boards.revision(), "request_id", id(), "request_digest", "e".repeat(64), "config_revision", "cfg-2", "method", "openpnp_start_job", "job_lineage", f.e.lineage.admissionFacts(f.attempt), "state", "accepted")); break;
                case "fresh-alias": {
                    var scope = f.e.lineage.snapshot(f.attempt); f.e.lineage.bindDocument(f.attempt, "c".repeat(64)); f.e.lineage.bindReload(id(), "c".repeat(64), scope.lineageId, scope.revision); break;
                }
                case "old-alias": {
                    var scope = f.e.lineage.snapshot(f.e.jobId); f.e.lineage.bindReload(id(), "b".repeat(64), scope.lineageId, scope.revision); break;
                }
                case "foreign-load": {
                    // Actual second native ledger emits new load observations from the same native
                    // candidate; the original coordinator does not own this additional load union.
                    NativeBoardLoads foreign = new NativeBoardLoads(f.e::append); foreign.bindJob(f.e.freshJob, f.attempt, true); break;
                }
                default: throw new AssertionError(kind);
            }
            return null;
        });
    }
    static void lineageDrift(String kind, boolean reorderedReplay) throws Exception {
        int start = assertions; String name = "completion-lineage-drift-" + kind + (reorderedReplay ? "-replay" : "-live");
        try (Fixture f = new Fixture(name, "definition")) {
            saveDocument(f, name); f.closeOriginal("outcome_unknown"); Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin(); fill(f, permit);
            Map<String,Boolean> nativeHistory = new TreeMap<>(f.e.oldJob.getPlacedStatusSnapshot());
            Map<String,Object> forcedAggregate = null;
            if (reorderedReplay) { complete(f, permit); forcedAggregate = f.e.events.get(f.e.events.size() - 1); check(OUTCOME.equals(forcedAggregate.get("type")), "Actual original aggregate precedes later lineage modification in negative replay fixture"); }
            applyLineageDrift(f, kind);
            if (kind.equals("content")) check(((Number) f.e.lineage.describe(f.attempt).get("lineage_revision")).intValue() == 2, "Actual lineage content change advanced the fresh revision");
            Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles(); long bytes = Files.size(f.e.file);
            if (!reorderedReplay) {
                refuse("lineage " + kind + " changed after selected child receipts", () -> complete(f, permit));
                check(bytes == Files.size(f.e.file) && f.e.replacement.continuationReceipt(permit.continuationId()) == null, "Changed lineage is refused before any aggregate record");
            } else {
                List<Map<String,Object>> reordered = new ArrayList<>(f.e.events); reordered.remove(forcedAggregate); reordered.add(forcedAggregate);
                try (Env reduced = new Env(name + "-invalid-order", true)) {
                    if (kind.equals("foreign-load")) {
                        try { NativeReplacementContinuationMutationTest.replayEvents(reduced, reordered); throw new AssertionError("Accepted foreign-ledger native board events"); }
                        catch (IllegalArgumentException earlierBoardFence) { check("Invalid board-load intent transition".equals(earlierBoardFence.getMessage()), "Foreign ledger replay reaches the exact earlier board-intent validation fence"); refusals++; }
                    } else refuse("lineage " + kind + " modification before historical aggregate", () -> NativeReplacementContinuationMutationTest.replayEvents(reduced, reordered));
                    check(reduced.replacement.continuationReceipt(permit.continuationId()) == null && Files.size(reduced.file) == 0, "Changed-lineage replay retains no invalid aggregate or write");
                }
            }
            check(counts.equals(counters()) && files.equals(configurationFiles()), "Lineage refusal/replay performs no counter or configuration save effects");
            check(nativeHistory.equals(f.e.oldJob.getPlacedStatusSnapshot()), "Lineage drift preserves actual old native placed history");
            NativeReplacementContinuationMutationTest.terminalize(fresh, permit); results.add(m("name", name, "assertions", assertions - start));
        }
    }
    static int nativeNozzlePicks, nativeNozzleCleanupPlaces;
    static void alterNativeGate(String kind) throws Exception {
        if (kind.equals("enabled")) config.getMachine().setEnabled(true);
        else { var nozzle = config.getMachine().getDefaultHead().getDefaultNozzle(); config.getMachine().setEnabled(true); try { nozzle.pick(tray.getPart(), tray); nativeNozzlePicks++; } finally { config.getMachine().setEnabled(false); } check(nozzle.getPart() == tray.getPart() && !config.getMachine().isEnabled(), "Actual ReferenceNozzle.pick retains synthetic occupancy with the machine disabled"); }
    }
    static void restoreNativeGate(String kind) throws Exception {
        if (kind.equals("enabled")) config.getMachine().setEnabled(false);
        else { var nozzle = config.getMachine().getDefaultHead().getDefaultNozzle(); if (nozzle.getPart() != null) { config.getMachine().setEnabled(true); try { nozzle.place(); nativeNozzleCleanupPlaces++; } finally { config.getMachine().setEnabled(false); } } check(nozzle.getPart() == null && !config.getMachine().isEnabled(), "Actual ReferenceNozzle.place clears test occupancy and disables during cleanup"); }
    }
    static void nativeGate(String kind, boolean afterForce) throws Exception {
        int start = assertions; String name = "completion-native-" + kind + (afterForce ? "-postforce" : "-entry");
        try (Fixture f = new Fixture(name, "definition")) {
            saveDocument(f, name); f.closeOriginal("outcome_unknown"); Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin(); fill(f, permit);
            long bytes = Files.size(f.e.file); Map<String,Boolean> history = new TreeMap<>(f.e.oldJob.getPlacedStatusSnapshot()); int[] intercepted = {0};
            if (afterForce) {
                Field sinkField = NativeFaultedJobReplacement.class.getDeclaredField("sink"); sinkField.setAccessible(true);
                NativeFaultedJobReplacement.Sink actual = (NativeFaultedJobReplacement.Sink) sinkField.get(f.e.replacement);
                NativeFaultedJobReplacement.Sink changedGate = (type, payload) -> { actual.appendAndForce(type, payload); if (OUTCOME.equals(type)) { intercepted[0]++; alterNativeGate(kind); } };
                sinkField.set(f.e.replacement, changedGate);
                try { refuse("actual native " + kind + " gate changed after known successful aggregate force", () -> complete(f, permit)); }
                finally { sinkField.set(f.e.replacement, actual); task(() -> { restoreNativeGate(kind); return null; }); }
                check(intercepted[0] == 1 && Files.size(f.e.file) > bytes, "Post-force test seam delegates one exact actual forced aggregate");
                check(f.e.replacement.continuationReceipt(permit.continuationId()) != null, "Known forced aggregate survives later native gate failure");
                task(() -> { refuse("post-force native failure revokes current continuation permit", permit::check); return null; });
            } else {
                task(() -> { alterNativeGate(kind); return null; });
                try { refuse("actual native " + kind + " gate at aggregate admission", () -> complete(f, permit)); }
                finally { task(() -> { restoreNativeGate(kind); return null; }); }
                check(bytes == Files.size(f.e.file) && f.e.replacement.continuationReceipt(permit.continuationId()) == null, "Entry native gate refusal precedes aggregate force");
            }
            check(history.equals(f.e.oldJob.getPlacedStatusSnapshot()), "Actual native gate perturbation preserves original job history");
            NativeReplacementContinuationMutationTest.terminalize(fresh, permit); if (afterForce) replay(f, fresh, name, true);
            results.add(m("name", name, "assertions", assertions - start, "synthetic_interruption", "test-only known-force sink callback changes actual native state; restoration is fixture cleanup"));
        }
    }
    static void counterDrift() throws Exception {
        int start = assertions;
        try (Fixture f = new Fixture("completion-native-counter-drift", "definition")) {
            saveDocument(f, "completion-native-counter-drift"); f.closeOriginal("outcome_unknown"); Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin(); fill(f, permit);
            var nativeFeeder = NativeReplacementContinuationMutationTest.nativeTray(rows(f, "material").get(0)); int actual = nativeFeeder.getFeedCount();
            long bytes = Files.size(f.e.file); task(() -> { nativeFeeder.setFeedCount(actual + 1); return null; });
            try { refuse("native counter drift after completed child steps", () -> complete(f, permit)); }
            finally { task(() -> { nativeFeeder.setFeedCount(actual); return null; }); } // Test fixture cleanup only, never a recovery workflow.
            check(bytes == Files.size(f.e.file), "Native drift is refused before aggregate force");
            NativeReplacementContinuationMutationTest.terminalize(fresh, permit); results.add(m("name", "counter-drift", "assertions", assertions - start));
        }
    }
    static void lineageForceBoundary(boolean after) throws Exception {
        int start = assertions; String name = "completion-lineage-force-" + (after ? "after" : "before");
        try (Fixture f = new Fixture(name, "definition")) {
            saveDocument(f, name); f.closeOriginal("outcome_unknown"); Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin();
            Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles(); long bytes = Files.size(f.e.file);
            f.e.failType = "job_lineage_continuation_replacement"; f.e.failAfterForce = after;
            refuse("actual one-record lineage force interruption", () -> task(() -> f.e.lineage.continueReplacement(permit, f.attempt))); f.e.failType = null;
            check((Files.size(f.e.file) > bytes) == after, "Only successful actual force retains lineage creation");
            long failed = Files.size(f.e.file); refuse("failed lineage decision cannot retry creation", () -> task(() -> f.e.lineage.continueReplacement(permit, f.attempt)));
            check(failed == Files.size(f.e.file), "Failed lineage retry emits no second identity"); permit.close();
            try (Env reduced = new Env(name + "-replay", true)) {
                NativeReplacementContinuationMutationTest.replayEvents(reduced, f.e.events);
                Map<String,Object> row = o(reduced.replacement.progress(f.attempt).get("lineage"));
                check((after ? "completed" : "untouched").equals(phase(row)), "Replay creates only the actually forced new lineage");
                check("untouched".equals(row.get("phase")), "Original lineage phase remains untouched after replay");
                check(!reduced.replacement.ownsCandidate(f.e.candidate) && Boolean.FALSE.equals(reduced.replacement.status().get("execution_authority_restored")), "Lineage replay grants no native candidate or execution authority");
                refuse("lineage receipt replay cannot mint native continuation", () -> task(() -> reduced.replacement.captureContinuation(f.attempt, f.e.oldJob)));
                check(Files.size(reduced.file) == 0, "Lineage replay appends no substitute receipt");
            }
            check(counts.equals(counters()) && files.equals(configurationFiles()), "Lineage failure and replay perform no native counter/save effects");
            results.add(m("name", name, "assertions", assertions - start, "record_actually_forced", after));
        }
    }
    static void forceBoundary(boolean after) throws Exception {
        int start = assertions; String name = "completion-force-" + (after ? "after" : "before");
        try (Fixture f = new Fixture(name, "lineage")) {
            saveDocument(f, name); f.closeOriginal("outcome_unknown"); Fresh fresh = f.fresh(f.capture(), "continue-faulted-job-replacement"); var permit = fresh.begin(); fill(f, permit);
            Map<String,Integer> counts = counters(); Map<String,String> files = configurationFiles(); Map<String,Object> before = state(f.e); long bytes = Files.size(f.e.file);
            f.e.failType = OUTCOME; f.e.failAfterForce = after;
            refuse("actual aggregate force interruption", () -> complete(f, permit)); f.e.failType = null;
            check((Files.size(f.e.file) > bytes) == after, "Only successful FileChannel.force retains an aggregate outcome");
            check(Boolean.TRUE.equals(f.e.replacement.status().get("publication_fault")), "Ambiguous aggregate publication fences the current owner");
            check(f.e.replacement.continuationReceipt(permit.continuationId()) == null, "Uncertain append return is not published as known current completion");
            long failedBytes = Files.size(f.e.file); refuse("uncertain aggregate cannot retry", () -> complete(f, permit)); check(failedBytes == Files.size(f.e.file), "Uncertain retry appends no replacement receipt");
            check(counts.equals(counters()) && files.equals(configurationFiles()) && NativeFaultedJobReplacement.same(before, state(f.e)), "Aggregate force failure repeats no component or native work");
            permit.close(); replay(f, fresh, name, after); results.add(m("name", name, "assertions", assertions - start, "record_actually_forced", after));
        }
    }
    public static void main(String[] args) throws Exception {
        initialize(Path.of(args[0])); Throwable error = null;
        try {
            for (String kind : List.of("content", "reservation", "admission", "fresh-alias", "old-alias", "foreign-load")) { lineageDrift(kind, false); lineageDrift(kind, true); }
            for (String kind : List.of("enabled", "occupied")) { nativeGate(kind, false); nativeGate(kind, true); }
            for (String prefix : List.of("definition", "pending-material", "mixed-board", "completed")) positive(prefix);
            convenience(); incompleteAndPriorDecision(); counterDrift(); lineageForceBoundary(false); lineageForceBoundary(true); forceBoundary(false); forceBoundary(true);
        } catch (Throwable failure) { error = failure; failure.printStackTrace(); }
        finally {
            config.getMachine().close(); Map<String,Object> proof = m("passed", error == null, "assertions", assertions, "refusals", refusals, "cases", results, "error", error == null ? null : error.toString(), "initial_fault_fixture", "synthetic sensing/action hooks and explicit synthetic TerminalAuthority; actual native components, candidate document save and FileChannel.force", "actual_native_job_initializations", 0, "actual_native_feeds", 0, "actual_native_placements", 0, "actual_native_job_placements", 0, "actual_native_nozzle_pick_calls", nativeNozzlePicks, "actual_native_nozzle_place_cleanup_calls", nativeNozzleCleanupPlaces, "bridge_integration_qualified", false, "full_continuation_qualified", false, "restart_reattachment_qualified", false, "hardware_qualified", false);
            Files.writeString(root.resolve("proof.json"), JSON.toJson(proof) + "\n"); System.out.println("NATIVE_REPLACEMENT_CONTINUATION_COMPLETION_RESULT " + JSON.toJson(proof));
        }
        System.exit(error == null ? 0 : 1);
    }
}
