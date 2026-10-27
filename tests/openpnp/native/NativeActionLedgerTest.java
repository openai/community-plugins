/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.openpnp.model.*;
import org.openpnp.scripting.Scripting;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/**
 * Real native processor/feeder/nozzle effects on the pinned NullDriver fixture. Storage failure
 * closes an actual FileChannel; no mock physical-effect core or generated ledger hook scripts.
 * The explicitly named observer-contract checks dispatch hooks directly and claim only wiring.
 */
public final class NativeActionLedgerTest {
    private static final Gson GSON = new Gson();
    private static final List<String> passed = new ArrayList<>();
    private static int assertions;
    private static int nativeFeedEffects;

    public static void main(String[] args) throws Exception {
        int exit = 0;
        try {
            testScopedObserver();
            nativeScenario("complete", null, null, 2);
            nativeScenario("before-feed-fault", "feed", "intent", 0);
            nativeScenario("after-feed-fault", "feed", "native_hook_returned", 1);
            nativeScenario("after-pick-fault", "pick", "native_hook_returned", 1);
            nativeRetryAfterEffect("feed");
            nativeRetryAfterEffect("pick");
            nativeAuthorizedAbort();
            testObserverContractsAndScriptPolicy();
            System.out.println("OPENPNP_NATIVE_ACTION_LEDGER_RESULT " + GSON.toJson(map(
                "passed", passed, "assertions", assertions, "upstream_commit", NativeActionLedger.PINNED_UPSTREAM,
                "simulation_only", true, "physical_qualification", false,
                "native_feed_effects", nativeFeedEffects, "completed_native_placements", 2,
                "fault_injection", "actual journal FileChannel closed at selected native hook",
                "class_sha256", map("ledger", hashClass(NativeActionLedger.class), "scripting", hashClass(Scripting.class),
                    "native_processor", hashClass(org.openpnp.machine.reference.ReferencePnpJobProcessor.class)))));
        } catch (Throwable error) { error.printStackTrace(); exit = 1; }
        System.exit(exit);
    }

    private static void testScopedObserver() throws Exception {
        Scripting scripting = new Scripting(null);
        List<String> calls = new ArrayList<>();
        Scripting.NativeObserver observer = observer(calls);
        check(scripting.hasNoScript("Job.Placement.BeforeAssembly"), "script-free fast path before registration");
        Scripting.NativeObserverScope scope = Scripting.observeNativeEvents(observer);
        try {
            check(!scripting.hasNoScript("Job.Placement.BeforeAssembly"), "registered observer receives native optimized hooks");
            scripting.on("fixture", Map.of());
            check(calls.equals(List.of("before:fixture", "after:fixture")), "observer works with null scripts directory");
            expect(IllegalStateException.class, () -> Scripting.observeNativeEvents(observer));
            AtomicReference<Throwable> wrongThread = new AtomicReference<>();
            Thread worker = new Thread(() -> {
                try { scripting.on("foreign-thread", Map.of()); scope.close(); }
                catch (Throwable error) { wrongThread.set(error); }
            });
            worker.start(); worker.join(3000);
            check(!worker.isAlive() && wrongThread.get() instanceof IllegalStateException, "observer cannot be closed by another thread");
            check(calls.size() == 2, "observer registration is not inherited by another thread");
        } finally { scope.close(); scope.close(); }
        check(scripting.hasNoScript("Job.Placement.BeforeAssembly"), "scope removes native observer and restores script-free fast path");
        passed.add("bounded per-thread Java observer registration and cleanup");
    }

    private static void nativeScenario(String label, String failedKind, String failedState, int expectedFeeds) throws Exception {
        Path root = Files.createTempDirectory("openpnp-native-actions-");
        Configuration config = fixture(root);
        Machine machine = config.getMachine();
        NativeActionLedger[] ledger = new NativeActionLedger[1];
        try (Recorder recorder = new Recorder(root.resolve("actions.jsonl"), failedKind, failedState)) {
            enableAndHome(machine);
            Part part = config.getPart("R0603-1K");
            ReferenceTrayFeeder feeder = (ReferenceTrayFeeder) machine.getFeeders().stream().filter(f -> f.getPart() == part).findFirst().orElseThrow();
            check(feeder.getFeedCount() == 0, "fresh native finite tray for " + label);
            AtomicReference<Throwable> observed = new AtomicReference<>();
            AtomicReference<Job> nativeJob = new AtomicReference<>();
            machine.submit(() -> {
                Job job = CanonicalJobImporter.load(config, canonical(part));
                nativeJob.set(job);
                Map<String,Object> preflight = NativeJobPreflight.validate(config, job);
                check(Boolean.TRUE.equals(preflight.get("valid")), "native model preflight for " + label);
                ledger[0] = new NativeActionLedger(recorder, label, "job-" + label, "cfg-1", "load-" + label, job);
                machine.getPnpJobProcessor().initialize(job);
                try {
                    boolean more; long step = 0;
                    do {
                        try (NativeActionLedger.StepScope scope = ledger[0].openStep(++step)) {
                            more = machine.getPnpJobProcessor().next();
                            scope.complete();
                        }
                    } while (more);
                } catch (NativeActionLedger.DurabilityFence error) {
                    observed.set(error);
                    // Same integration contract as Bridge: settle only, no processor.abort(),
                    // retry, discard or job cleanup after a durability fence.
                    machine.getMotionPlanner().waitForCompletion(null, MotionPlanner.CompletionType.WaitForStillstand);
                }
                return null;
            }, null, false).get(60, TimeUnit.SECONDS);
            check(feeder.getFeedCount() == expectedFeeds, "native feed count proves no re-feed for " + label);
            nativeFeedEffects += feeder.getFeedCount();
            check(((List<?>)ledger[0].snapshot().get("pending_actions")).size() <= 1, "bounded pending native action snapshot");
            check(Boolean.FALSE.equals(((Map<?,?>)ledger[0].snapshot().get("scope")).get("physical_load_verified")), "logical board load is not physical authority");
            if (failedKind == null) {
                check(observed.get() == null, "complete native run has no durability fence");
                check(number(ledger[0].snapshot().get("native_placed_observed")) == 2, "actual two native placed statuses observed");
                Set<String> keys = new HashSet<>();
                for (Map<String,Object> event : recorder.records) {
                    Map<String,Object> payload = payload(event);
                    if ("native-placement-complete-hook".equals(payload.get("state"))) {
                        Map<?,?> context = (Map<?,?>)payload.get("context");
                        keys.add((String)context.get("placement_key"));
                        check(Boolean.TRUE.equals(context.get("native_placed_status")), "complete checkpoint follows native stored placed status");
                    }
                }
                check(keys.size() == 2, "same reference on two native board instances has distinct stable identities");
                check(recorder.count("native_action_intent", "feed") == 2, "two actual feeds have two native intents");
                check(recorder.count("native_action_intent", "pick") == 2, "two actual picks have two native intents");
                check(recorder.count("native_action_intent", "release") == 2, "two actual releases have two native intents");
                check(number(ledger[0].snapshot().get("independently_verified")) == 0, "native placements are never independently verified");
                Map<String,Object> recovered = NativeActionLedger.recover(readRecords(recorder.path), label);
                check(((List<?>)recovered.get("unresolved_actions")).isEmpty(), "complete disk journal matches every action outcome");
                testRecoveryIntegrity(recorder, label);
            } else {
                check(observed.get() instanceof NativeActionLedger.DurabilityFence, "storage fault bypasses native Exception retry handlers");
                check(Boolean.TRUE.equals(ledger[0].snapshot().get("durability_fault")), "storage fault fences subsequent ledger use");
                expect(NativeActionLedger.DurabilityFence.class, () -> ledger[0].openStep(99999));
                check(recorder.failureAttempts == 1, "failed durable append is never replayed");
                check(number(ledger[0].snapshot().get("native_placed_observed")) == 0, "fault does not invent completed placement");
                Map<String,Object> recovered = NativeActionLedger.recover(readRecords(recorder.path), label);
                int unresolved = ((List<?>)recovered.get("unresolved_actions")).size();
                check(unresolved == ("intent".equals(failedState) ? 0 : 1), "disk recovery preserves exact unresolved action boundary for " + label);
                if ("pick".equals(failedKind)) {
                    check(recorder.count("native_action_intent", "pick") == 1, "native post-pick storage fault never re-enters pick attempt");
                    boolean held = false;
                    for (Head head : machine.getHeads()) for (Nozzle nozzle : head.getNozzles()) held |= nozzle.getPart() == part;
                    check(held, "part remains on native nozzle after fenced pick; no discard cleanup ran");
                } else {
                    check(recorder.count("native_action_intent", "pick") == 0, "feed fence stops before any native pick");
                }
            }
            check(number(ledger[0].snapshot().get("events_committed")) == recorder.records.size(), "ledger publishes only force-completed journal events");
            try (Scripting.NativeObserverScope registration = Scripting.observeNativeEvents(observer(new ArrayList<>()))) {
                check(registration != null, "native step scope detached after " + label);
            }
            passed.add(label + ": actual native processor, finite feeder, durable disk receipts");
        } finally { machine.close(); }
    }

    private static void testRecoveryIntegrity(Recorder recorder, String operation) throws Exception {
        List<Map<String,Object>> disk = readRecords(recorder.path);
        NativeActionLedger.Replay incremental = new NativeActionLedger.Replay(operation);
        incremental.accept(disk.get(0)); Map<String,Object> prefix = incremental.snapshot();
        for (int i = 1; i < disk.size(); i++) incremental.accept(disk.get(i));
        check(number(prefix.get("events_committed")) == 1, "incremental replay does not mutate earlier snapshots");
        check(incremental.snapshot().equals(NativeActionLedger.recover(disk, operation)), "one-pass replay matches complete journal recovery");
        List<Map<String,Object>> duplicated = new ArrayList<>(disk);
        duplicated.add(disk.get(0));
        check(number(NativeActionLedger.recover(duplicated, operation).get("events_committed")) == disk.size(), "identical replayed durable receipt is idempotent");
        List<Map<String,Object>> collided = new ArrayList<>(disk);
        Map<String,Object> corrupt = cloneMap(disk.get(0));
        payload(corrupt).put("native_step_index", 99999);
        collided.add(corrupt);
        expect(IllegalArgumentException.class, () -> NativeActionLedger.recover(collided, operation));
        expect(IllegalArgumentException.class, () -> NativeActionLedger.recover(disk.subList(1, disk.size()), operation));
        List<Map<String,Object>> mismatch = new ArrayList<>();
        for (Map<String,Object> record : disk) {
            Map<String,Object> copy = cloneMap(record);
            if ("native_action_outcome".equals(copy.get("type")) && mismatch.stream().noneMatch(e -> "native_action_outcome".equals(e.get("type")))) {
                ((Map<String,Object>)payload(copy).get("context")).put("nozzle_id", "other-nozzle");
            }
            mismatch.add(copy);
        }
        expect(IllegalArgumentException.class, () -> NativeActionLedger.recover(mismatch, operation));
        passed.add("read-only replay rejects duplicate collisions, missing prefix, and wrong action context");
    }

    /** Fault adapters call the actual native implementation, then throw before its After hook. */
    private static void nativeRetryAfterEffect(String kind) throws Exception {
        Path root = Files.createTempDirectory("openpnp-native-retry-fence-");
        Configuration config = fixture(root);
        Machine machine = config.getMachine();
        try (Recorder recorder = new Recorder(root.resolve("actions.jsonl"), null, null)) {
            enableAndHome(machine);
            Part part = config.getPart("R0603-1K");
            ReferenceTrayFeeder original = (ReferenceTrayFeeder)machine.getFeeders().stream().filter(f -> f.getPart() == part).findFirst().orElseThrow();
            part.setPickRetryCount(2); original.setPickRetryCount(2); original.setFeedRetryCount(2);
            AtomicInteger nativeCalls = new AtomicInteger();
            ReferenceTrayFeeder feeder;
            if ("feed".equals(kind)) {
                ReferenceTrayFeeder faulting = new ReferenceTrayFeeder() {
                    @Override public void feed(Nozzle nozzle) throws Exception {
                        super.feed(nozzle);
                        nativeCalls.incrementAndGet();
                        throw new IOException("Injected ordinary failure after native tray advance");
                    }
                };
                faulting.setPart(part); faulting.setLocation(original.getPickLocation()); faulting.setOffsets(original.getOffsets());
                faulting.setTrayCountX(10000); faulting.setTrayCountY(1); faulting.setFeedRetryCount(2); faulting.setPickRetryCount(2); faulting.setEnabled(true);
                for (Feeder other : machine.getFeeders()) if (other.getPart() == part) other.setEnabled(false);
                machine.addFeeder(faulting); feeder = faulting;
            } else {
                feeder = original;
                for (Head head : machine.getHeads()) for (Nozzle nozzle : head.getNozzles()) {
                    org.openpnp.machine.reference.ReferenceNozzle nativeNozzle = (org.openpnp.machine.reference.ReferenceNozzle)nozzle;
                    Actuator nativeActuator = nativeNozzle.getExpectedVacuumActuator();
                    nativeNozzle.setVacuumActuator(new org.openpnp.machine.reference.ReferenceActuator() {
                        @Override public void actuate(boolean on) throws Exception {
                            nativeActuator.actuate(on);
                            if (on) { nativeCalls.incrementAndGet(); throw new IOException("Injected ordinary failure after native vacuum actuation"); }
                        }
                        @Override public void actuate(double value) throws Exception {
                            nativeActuator.actuate(value); nativeCalls.incrementAndGet();
                            throw new IOException("Injected ordinary failure after native vacuum actuation");
                        }
                    });
                }
            }
            AtomicReference<NativeActionLedger> ledger = new AtomicReference<>();
            AtomicReference<Throwable> observed = new AtomicReference<>();
            machine.submit(() -> {
                Job job = CanonicalJobImporter.load(config, canonical(part));
                NativeActionLedger actions = new NativeActionLedger(recorder, "ordinary-" + kind, "job-retry", "cfg-1", "load-retry", job);
                ledger.set(actions); machine.getPnpJobProcessor().initialize(job);
                try {
                    boolean more; long step = 0;
                    do {
                        try (NativeActionLedger.StepScope scope = actions.openStep(++step)) {
                            more = machine.getPnpJobProcessor().next(); scope.complete();
                        }
                    } while (more);
                } catch (NativeActionLedger.UnresolvedActionFence error) {
                    observed.set(error);
                    machine.getMotionPlanner().waitForCompletion(null, MotionPlanner.CompletionType.WaitForStillstand);
                }
                return null;
            }, null, false).get(60, TimeUnit.SECONDS);
            check(observed.get() instanceof NativeActionLedger.UnresolvedActionFence, "ordinary post-effect " + kind + " error fences its native retry");
            check(nativeCalls.get() == 1, "actual native " + kind + " effect executes once despite configured native retries");
            check(feeder.getFeedCount() == 1, "ordinary " + kind + " failure retains one consumed native tray slot");
            nativeFeedEffects += feeder.getFeedCount();
            check(recorder.count("native_action_intent", kind) == 1, "retry does not replace prior " + kind + " intent");
            check(Boolean.TRUE.equals(ledger.get().snapshot().get("unresolved_action_fault")), "unknown native action fault remains sticky");
            check(((List<?>)NativeActionLedger.recover(readRecords(recorder.path), "ordinary-" + kind).get("unresolved_actions")).size() == 1, "durable original " + kind + " intent remains unknown");
            expect(NativeActionLedger.UnresolvedActionFence.class, () -> ledger.get().openStep(99999));
            check(recorder.records.stream().anyMatch(e -> "another-native-before-hook-without-matching-after-hook".equals(payload(e).get("reason"))), "retry fence occurs at repeated Before before another effect");
            passed.add("ordinary native post-effect " + kind + " Exception cannot trigger another native effect");
        } finally { machine.close(); }
    }

    private static void testObserverContractsAndScriptPolicy() throws Exception {
        Path root = Files.createTempDirectory("openpnp-observer-contract-");
        Configuration config = fixture(root);
        try (Recorder recorder = new Recorder(root.resolve("actions.jsonl"), null, null)) {
            Job job = CanonicalJobImporter.load(config, canonical(config.getPart("R0603-1K")));
            NativeActionLedger ledger = new NativeActionLedger(recorder, "contract", "job-contract", "cfg-1", "load-contract", job);
            Scripting scripting = config.getScripting();
            Nozzle nozzle = config.getMachine().getDefaultHead().getDefaultNozzle();
            Feeder feeder = config.getMachine().getFeeders().stream().filter(f -> f.getPart() == config.getPart("R0603-1K")).findFirst().orElseThrow();
            Map<String,Object> feed = map("nozzle", nozzle, "feeder", feeder, "part", feeder.getPart());
            expect(NativeActionLedger.UnresolvedActionFence.class, () -> {
                try (NativeActionLedger.StepScope step = ledger.openStep(1)) {
                    scripting.on("Feeder.BeforeFeed", feed);
                    scripting.on("Feeder.BeforeFeed", feed);
                }
            });
            NativeActionLedger alignLedger = new NativeActionLedger(recorder, "contract-align", "job-contract", "cfg-1", "load-contract", job);
            org.openpnp.machine.reference.vision.ReferenceBottomVision bottom=(org.openpnp.machine.reference.vision.ReferenceBottomVision)config.getMachine().getPartAlignments().get(0);
            bottom.setEnabled(true);bottom.getInheritedVisionSettings(feeder.getPart()).setEnabled(true);
            config.getMachine().submit(()->{
                try(NativeActionLedger.StepScope step=alignLedger.openStep(1)){
                    BoardLocation board=job.getBoardLocations().get(0);
                    org.openpnp.util.VisionUtils.findPartAlignmentOffsets(bottom,feeder.getPart(),board,board.getBoard().getPlacements().get(0),nozzle);
                    throw new AssertionError("native bottom vision unexpectedly aligned an empty nozzle");
                }catch(Exception failure){
                    check("No part on nozzle.".equals(failure.getMessage()),"actual native bottom vision fails before alignment on empty nozzle");
                    check(Arrays.stream(failure.getSuppressed()).anyMatch(e->e instanceof NativeActionLedger.UnresolvedActionFence),"native finally unknown outcome keeps its close-time safety fence");
                }
                return null;
            },null,true).get(10,TimeUnit.SECONDS);
            check(((List<?>)alignLedger.snapshot().get("pending_actions")).size()==1,"live null-alignment outcome remains explicitly unresolved");
            List<Map<String,Object>> alignmentPrefix=new ArrayList<>();
            for(Map<String,Object> e:readRecords(recorder.path))if("contract-align".equals(payload(e).get("operation_id"))&&!"native_action_gap".equals(e.get("type")))alignmentPrefix.add(e);
            Map<String,Object> explicitRecovery=NativeActionLedger.recover(alignmentPrefix,"contract-align");
            check(Boolean.TRUE.equals(explicitRecovery.get("requires_reconciliation")),"explicit unknown alignment outcome requires recovery even before any later gap record");
            List<?> unresolved=(List<?>)explicitRecovery.get("unresolved_actions");check(unresolved.size()==1&&"alignment-finally-hook-without-result".equals(((Map<?,?>)unresolved.get(0)).get("reason")),"read-only replay retains the exact reported unknown outcome reason");
            check(recorder.records.stream().anyMatch(e -> "another-native-before-hook-without-matching-after-hook".equals(payload(e).get("reason"))), "unpaired prior configured retry remains unknown");
            check(recorder.records.stream().anyMatch(e -> "alignment-finally-hook-without-result".equals(payload(e).get("reason"))), "Vision finally hook with null offsets is not successful alignment");
            NativeActionLedger interrupted = new NativeActionLedger(recorder, "contract-interrupted", "job-contract", "cfg-1", "load-contract", job);
            expect(NativeActionLedger.UnresolvedActionFence.class, () -> {
                try (NativeActionLedger.StepScope step = interrupted.openStep(1)) { scripting.on("Feeder.BeforeFeed", feed); }
            });
            check(recorder.records.stream().anyMatch(e -> "native-step-threw-or-was-interrupted".equals(payload(e).get("reason"))), "scope closes unpaired intent as unknown");
            Map<String,Object> immutable = ledger.snapshot();
            expect(UnsupportedOperationException.class, () -> immutable.put("events_committed", -1));
            expect(UnsupportedOperationException.class, () -> ((Map<String,Object>)immutable.get("scope")).put("job_id", "mutated"));
            check(NativeActionLedger.recover(readRecords(recorder.path), "contract").get("automatic_replay").equals(false), "recovery never authorizes effect replay");

            List<String> ordering = new ArrayList<>();
            Path eventScript = scripting.getEventsDirectory().toPath().resolve("ObserverFixture.js");
            Files.writeString(eventScript, "ordering.add('script');");
            try (Scripting.NativeObserverScope scope = Scripting.observeNativeEvents(observer(ordering))) {
                scripting.on("ObserverFixture", map("ordering", ordering));
            }
            check(ordering.equals(List.of("before:ObserverFixture", "script", "after:ObserverFixture")), "Java observers preserve original script order");
            Files.delete(eventScript);
            Path policyScript = root.resolve("policy.js");
            Files.writeString(policyScript, "observed.set(scripting.getActiveExecutionCount()); counter.incrementAndGet();");
            AtomicInteger counter = new AtomicInteger(), active = new AtomicInteger();
            try (Scripting.ExecutionConstraint guard = scripting.constrainExecution(file -> { throw new IllegalStateException("blocked"); })) {
                expect(Scripting.ExecutionRejected.class, () -> scripting.execute(policyScript.toFile(), map("counter", counter, "observed", active)));
                expect(IllegalStateException.class, () -> scripting.constrainExecution(file -> {}));
            }
            check(counter.get() == 0 && scripting.getActiveExecutionCount() == 0, "policy rejection prevents eval and releases active count");
            scripting.execute(policyScript.toFile(), map("counter", counter, "observed", active));
            check(counter.get() == 1 && active.get() == 1 && scripting.getActiveExecutionCount() == 0, "actual interpreter evaluation is counted and restored");
            Files.writeString(policyScript, "throw new Error('intentional-script-error');");
            expect(Exception.class, () -> scripting.execute(policyScript.toFile(), Map.of()));
            check(scripting.getActiveExecutionCount() == 0, "interpreter exception releases active execution count");
            passed.add("observer-contract: unmatched attempts, alignment finally, immutable observations, script ordering and policy lifecycle");
        } finally { config.getMachine().close(); }
    }

    private static void nativeAuthorizedAbort() throws Exception {
        Path root = Files.createTempDirectory("openpnp-native-abort-ledger-");
        Configuration config = fixture(root); Machine machine = config.getMachine();
        try (Recorder recorder = new Recorder(root.resolve("actions.jsonl"), null, null)) {
            enableAndHome(machine);
            machine.submit(() -> {
                Job job = CanonicalJobImporter.load(config, canonical(config.getPart("R0603-1K")));
                NativeActionLedger ledger = new NativeActionLedger(recorder, "authorized-abort", "job-abort", "cfg-1", "load-abort", job);
                machine.getPnpJobProcessor().initialize(job); long index = 0;
                while (recorder.count("native_action_outcome", "pick") == 0) {
                    try (NativeActionLedger.StepScope scope = ledger.openStep(++index)) {
                        check(machine.getPnpJobProcessor().next(), "native job remains active before test abort"); scope.complete();
                    }
                }
                try (NativeActionLedger.StepScope scope = ledger.openAbortScope()) {
                    machine.getPnpJobProcessor().abort(); scope.complete();
                }
                check(recorder.count("native_action_intent", "discard") == 1, "authorized native abort journals actual discard intent");
                check(recorder.count("native_action_outcome", "discard") == 1, "authorized native abort journals actual discard return");
                check(recorder.count("native_action_intent", "release") == 1, "native abort captures nested nozzle release");
                for (Head head : machine.getHeads()) for (Nozzle nozzle : head.getNozzles()) check(nozzle.getPart() == null, "authorized abort clears actual native nozzle part");
                final long nextIndex = index;
                check(recorder.records.stream().anyMatch(e -> "processor-abort".equals(payload(e).get("native_call")) && number(payload(e).get("native_step_index")) == nextIndex), "abort records last next index without inventing another next call");
                expect(IllegalStateException.class, ledger::openAbortScope);
                return null;
            }, null, false).get(60, TimeUnit.SECONDS);
            check(((List<?>)NativeActionLedger.recover(readRecords(recorder.path), "authorized-abort").get("unresolved_actions")).isEmpty(), "authorized abort hook journal recovers without unresolved actions");
            int feedCount = machine.getFeeders().stream().filter(f -> f.getPart() == config.getPart("R0603-1K")).mapToInt(f -> ((ReferenceTrayFeeder)f).getFeedCount()).sum();
            check(feedCount == 1, "authorized abort retains exactly one consumed native tray slot"); nativeFeedEffects += feedCount;
            passed.add("ordinary authorized processor.abort has durable nested discard/release observations");
        } finally { machine.close(); }
    }

    private static Scripting.NativeObserver observer(List<String> calls) {
        return new Scripting.NativeObserver() {
            public void beforeScripts(String event, Map<String,Object> globals) { calls.add("before:" + event); }
            public void afterScripts(String event, Map<String,Object> globals) { calls.add("after:" + event); }
        };
    }

    private static Configuration fixture(Path root) throws Exception {
        Path configPath = root.resolve("config"); Files.createDirectory(configPath);
        Configuration.initialize(configPath.toFile()); Configuration config = Configuration.get(); config.load();
        SimulatorMain.accelerateFixture(config); SimulatorMain.configureSustainedWorkload(config);
        return config;
    }
    private static void enableAndHome(Machine machine) throws Exception {
        machine.submit(() -> { machine.setEnabled(true); return null; }, null, true).get(30, TimeUnit.SECONDS);
        machine.execute(() -> { machine.home(); return null; }, false, 5000, 30000);
    }
    private static JsonObject canonical(Part part) {
        double height = part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();
        Map<String,Object> placement = map("ref", "R1", "partId", part.getId(), "packageId", part.getPackage().getId(), "heightMm", height,
            "x", 10, "y", 10, "z", 0, "rotation", 0, "side", "top", "enabled", true, "type", "placement");
        List<Object> instances = new ArrayList<>();
        for (int i=0;i<2;i++) instances.add(map("id", "instance-" + i, "kind", "board", "definitionId", "board", "x", i*25, "y", 0, "z", 0,
            "rotation", 0, "side", "top", "enabled", true));
        return GSON.toJsonTree(map("schemaVersion", 1, "id", "native-ledger-test", "units", "mm", "coordinateConvention", "openpnp-top-view",
            "parts", List.of(map("id", part.getId(), "packageId", part.getPackage().getId(), "heightMm", height)),
            "boards", List.of(map("id", "board", "widthMm", 20, "heightMm", 20, "placements", List.of(placement))),
            "panels", List.of(), "instances", instances)).getAsJsonObject();
    }

    private static final class Recorder implements NativeActionLedger.Sink, AutoCloseable {
        final Path path; final FileChannel channel; final String kind; final String state;
        final List<Map<String,Object>> records = new ArrayList<>(); int failureAttempts;
        Recorder(Path path, String kind, String state) throws Exception {
            this.path = path; this.kind = kind; this.state = state;
            channel = FileChannel.open(path, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
        }
        @Override public void append(String type, Map<String,Object> payload) throws Exception {
            if (kind != null && kind.equals(payload.get("kind")) && state.equals(payload.get("state"))) { failureAttempts++; channel.close(); }
            Map<String,Object> envelope = map("type", type, "payload", payload);
            byte[] bytes = (GSON.toJson(envelope) + "\n").getBytes(StandardCharsets.UTF_8);
            ByteBuffer buffer = ByteBuffer.wrap(bytes);
            while (buffer.hasRemaining()) channel.write(buffer);
            channel.force(true);
            records.add(envelope);
        }
        int count(String type, String kind) { return (int)records.stream().filter(e -> type.equals(e.get("type")) && kind.equals(payload(e).get("kind"))).count(); }
        @Override public void close() throws IOException { channel.close(); }
    }

    @SuppressWarnings("unchecked") private static List<Map<String,Object>> readRecords(Path path) throws Exception {
        List<Map<String,Object>> records = new ArrayList<>();
        for (String line : Files.readAllLines(path)) records.add(GSON.fromJson(line, Map.class));
        return records;
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> cloneMap(Map<String,Object> value) { return GSON.fromJson(GSON.toJson(value), Map.class); }
    @SuppressWarnings("unchecked") private static Map<String,Object> payload(Map<String,Object> envelope) { return (Map<String,Object>)envelope.get("payload"); }
    private static long number(Object value) { return ((Number)value).longValue(); }
    private static Map<String,Object> map(Object... pairs) {
        Map<String,Object> map = new LinkedHashMap<>();
        for (int i=0;i<pairs.length;i+=2) map.put((String)pairs[i], pairs[i+1]);
        return map;
    }
    private interface Checked { void run() throws Exception; }
    private static void expect(Class<? extends Throwable> type, Checked action) throws Exception {
        try { action.run(); } catch (Throwable error) {
            if (type.isInstance(error)) { assertions++; return; }
            throw new AssertionError("Expected " + type.getSimpleName() + " but observed " + error, error);
        }
        throw new AssertionError("Expected " + type.getSimpleName());
    }
    private static void check(boolean value, String label) { if (!value) throw new AssertionError(label); assertions++; }
    private static String hashClass(Class<?> type) throws Exception {
        try (java.io.InputStream stream = type.getResourceAsStream("/" + type.getName().replace('.', '/') + ".class")) {
            StringBuilder encoded = new StringBuilder();
            for (byte value : java.security.MessageDigest.getInstance("SHA-256").digest(stream.readAllBytes()))
                encoded.append(Character.forDigit((value & 255) >>> 4, 16)).append(Character.forDigit(value & 15, 16));
            return encoded.toString();
        }
    }
}
