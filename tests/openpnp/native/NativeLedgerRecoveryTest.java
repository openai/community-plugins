/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/**
 * Actual native tray feed, failed FileChannel outcome append, then real Bridge restarts.
 * The initial operation admission is a fixture envelope; native action envelopes come from the
 * production helper observing actual processor.next(). This is an in-process recovery contract
 * test, not a process-crash, host-power-loss, GUI, or physical-machine qualification.
 */
public final class NativeLedgerRecoveryTest {
    private static final Gson GSON = new Gson();
    private static final List<String> passed = new ArrayList<>();
    private static Bridge bridge;
    private static String session;

    public static void main(String[] args) throws Exception {
        Path root = Files.createTempDirectory("openpnp-native-ledger-recovery-");
        Path configDir = Files.createDirectory(root.resolve("config"));
        Path token = root.resolve("token");
        Files.writeString(token, UUID.randomUUID().toString() + UUID.randomUUID());
        Configuration.initialize(configDir.toFile());
        Configuration config = Configuration.get(); config.load();
        SimulatorMain.accelerateFixture(config); SimulatorMain.configureSustainedWorkload(config);
        Machine machine = config.getMachine();
        Path journalDir = root.resolve("journal"), journal = journalDir.resolve("operations.jsonl");
        String operationId = UUID.randomUUID().toString(), requestId = UUID.randomUUID().toString();
        int exitCode = 0;
        try {
            bridge = new Bridge(config, token, journalDir, Paths.get(args[0]), 0, true, "sustained-workload");
            String originalInstance = (String) call("openpnp_get_capabilities").get("bridge_instance_id");
            String machineId = (String) call("openpnp_get_capabilities").get("machine_id");
            long sequence = ((Number) call("openpnp_get_status").get("through_sequence")).longValue();
            bridge.close(); bridge = null;
            machine.submit(() -> { machine.setEnabled(true); machine.home(); return null; }, null, true).get(30, TimeUnit.SECONDS);
            Part part = config.getPart("R0603-1K");
            ReferenceTrayFeeder feeder = (ReferenceTrayFeeder) machine.getFeeders().stream()
                .filter(f -> f.isEnabled() && f.getPart() == part).findFirst().orElseThrow();
            check(feeder.getFeedCount() == 0, "fault scenario begins with an unconsumed native finite tray");
            AtomicReference<NativeActionLedger> ledger = new AtomicReference<>();
            AtomicReference<Throwable> failure = new AtomicReference<>();
            try (FailureJournal recorder = new FailureJournal(journal, sequence, originalInstance)) {
                recorder.write("operation", map("operation_id", operationId, "request_id", requestId,
                    "request_digest", "fixture-admission-no-replay", "method", "openpnp_start_job", "state", "running",
                    "bridge_instance_id", originalInstance, "config_revision", "cfg-1", "job_id", "native-ledger-recovery-job"));
                machine.submit(() -> {
                    Job job = CanonicalJobImporter.load(config, canonical(part));
                    ledger.set(new NativeActionLedger(recorder, operationId, "native-ledger-recovery-job", "cfg-1", "logical-load-1", job));
                    machine.getPnpJobProcessor().initialize(job);
                    try {
                        boolean more; long step = 0;
                        do {
                            try (NativeActionLedger.StepScope scope = ledger.get().openStep(++step)) {
                                more = machine.getPnpJobProcessor().next(); scope.complete();
                            }
                        } while (more);
                    } catch (NativeActionLedger.DurabilityFence error) {
                        failure.set(error);
                        machine.getMotionPlanner().waitForCompletion(null, MotionPlanner.CompletionType.WaitForStillstand);
                    }
                    return null;
                }, null, false).get(60, TimeUnit.SECONDS);
                check(failure.get() instanceof NativeActionLedger.DurabilityFence && recorder.failedWrites == 1,
                    "actual post-feed journal failure fences once outside native retry loops");
            }
            check(feeder.getFeedCount() == 1, "exactly one actual native feed occurred before the unknown outcome");
            check(((List<?>) ledger.get().snapshot().get("pending_actions")).size() == 1,
                "live ledger preserves one pending feed intent");
            byte[] originalPrefix = Files.readAllBytes(journal);
            long originalActions = actionCount(journal);
            check(originalActions > 0, "actual helper action envelopes were forced to disk before failure");

            bridge = new Bridge(config, token, journalDir, Paths.get(args[0]), 0, true, "sustained-workload");
            check(!originalInstance.equals(call("openpnp_get_capabilities").get("bridge_instance_id")), "Bridge restart changes the operation owner instance");
            check(machineId.equals(call("openpnp_get_capabilities").get("machine_id")), "Bridge restart preserves the durable machine identity");
            checkPrefix(journal, originalPrefix, "first restart preserves the original forced journal prefix");
            Map<String,Object> recovered = call("openpnp_get_operation", "operation_id", operationId);
            check("outcome_unknown".equals(recovered.get("state")), "unresolved native action forces recovered operation unknown");
            Map<String,Object> firstFacts = facts(recovered);
            assertUnknownFacts(firstFacts);
            session = (String) call("openpnp_request_control_session", "request_id", UUID.randomUUID().toString(), "ttl_seconds", 300).get("session_id");
            expect("RECOVERY_REQUIRED", "openpnp_plan_motion", mutation("units", "mm", "x", 5, "y", 5, "z", 0));
            JsonObject reconcile = mutation("operation_id", operationId, "disposition", "abandon-after-simulator-reset");
            String reconcileId = reconcile.get("request_id").getAsString();
            Map<String,Object> abandoned = invoke("openpnp_reconcile_operation", reconcile);
            assertAbandoned(abandoned);
            check(firstFacts.equals(facts(abandoned)), "explicit abandonment keeps the exact unknown action facts");
            check(invoke("openpnp_reconcile_operation", reconcile).get("state").equals("cancelled"), "lost reconciliation response replays the durable cancelled receipt");
            check(feeder.getFeedCount() == 1 && actionCount(journal) == originalActions, "recovery and explicit abandonment perform no native feed or action replay");
            byte[] abandonedPrefix = Files.readAllBytes(journal);
            bridge.close(); bridge = null;

            bridge = new Bridge(config, token, journalDir, Paths.get(args[0]), 0, true, "sustained-workload");
            Map<String,Object> twiceRecovered = call("openpnp_get_operation", "operation_id", operationId);
            assertAbandoned(twiceRecovered);
            assertUnknownFacts(facts(twiceRecovered));
            check(firstFacts.equals(facts(twiceRecovered)), "second restart preserves the unresolved facts without reopening the abandoned operation");
            checkPrefix(journal, abandonedPrefix, "second restart preserves the complete abandonment journal prefix");
            Map<String,Object> reconciliationReceipt = call("openpnp_get_request_status", "request_id", reconcileId);
            check("cancelled".equals(((Map<?,?>) reconciliationReceipt.get("operation")).get("state")), "second restart exposes the original cancelled reconciliation receipt");
            session = (String) call("openpnp_request_control_session", "request_id", UUID.randomUUID().toString(), "ttl_seconds", 300).get("session_id");
            check(invoke("openpnp_plan_motion", mutation("units", "mm", "x", 5, "y", 5, "z", 0)).get("plan_id") != null,
                "bounded planning admission remains open after second restart and prior explicit abandonment");
            check(feeder.getFeedCount() == 1 && actionCount(journal) == originalActions,
                "two Bridge restarts and planning preserve the one-feed count without action replay");
            checkDisabledAfterValidation(part);
            check(feeder.getFeedCount() == 1 && actionCount(journal) == originalActions,
                "disabled-job admission regression performs no feed or native job action");
            System.out.println("OPENPNP_NATIVE_LEDGER_RECOVERY_RESULT " + GSON.toJson(map("passed", passed,
                "assertions", passed.size(), "upstream_commit", Bridge.UPSTREAM, "simulation_only", true,
                "physical_qualification", false, "process_crash_test", false, "bridge_restarts_after_fault", 2,
                "native_feed_effects", feeder.getFeedCount(), "completed_native_placements", 0,
                "fault_injection", "actual FileChannel closed after native tray feed before outcome append",
                "admission_envelope", "fixture; action envelopes from actual native processor hooks",
                "journal_sha256", hash(Files.readAllBytes(journal)), "evidence_directory", root.toString())));
        } catch (Throwable error) { error.printStackTrace(); exitCode = 1; }
        finally { if (bridge != null) bridge.close(); machine.close(); }
        System.exit(exitCode);
    }

    private static void checkDisabledAfterValidation(Part part) throws Exception {
        Map<String,Object> prepared = succeed("openpnp_prepare_job", mutation("canonical_job", canonical(part)));
        Map<String,Object> validated = succeed("openpnp_validate_job", mutation());
        check(Boolean.TRUE.equals(validated.get("valid")), "native job validates while the machine is enabled and homed");
        succeed("openpnp_set_machine_enabled", mutation("enabled", false));
        long before = ((Number) call("openpnp_get_status").get("through_sequence")).longValue();
        expect("MACHINE_DISABLED", "openpnp_start_job", mutation("job_id", prepared.get("job_id")));
        Map<String,Object> status = call("openpnp_get_status");
        check(status.get("active_operation_id") == null && !Boolean.TRUE.equals(status.get("native_busy")),
            "disabled start does not strand an accepted operation outside the native wrapper");
        check(((Number) status.get("through_sequence")).longValue() == before,
            "disabled start is refused before durable operation admission");
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> facts(Map<String,Object> operation) {
        return (Map<String,Object>) operation.get("native_action_recovery");
    }
    private static void assertUnknownFacts(Map<String,Object> facts) {
        check(facts != null && Boolean.TRUE.equals(facts.get("requires_reconciliation")) && Boolean.FALSE.equals(facts.get("automatic_replay")),
            "retained action facts continue to deny automatic replay");
        List<?> unresolved = (List<?>) facts.get("unresolved_actions");
        check(unresolved.size() == 1 && "feed".equals(((Map<?,?>) unresolved.get(0)).get("kind")),
            "retained native feed outcome remains explicitly unknown");
    }
    private static void assertAbandoned(Map<String,Object> operation) {
        Map<?,?> result = (Map<?,?>) operation.get("result");
        check("cancelled".equals(operation.get("state")) && result != null && "abandoned-after-simulator-reset".equals(result.get("resolution"))
            && "unknown".equals(result.get("previous_physical_outcome")) && Boolean.FALSE.equals(result.get("repeat_action_performed")),
            "durable abandonment is cancelled while its prior physical outcome stays unknown");
    }
    private static final class FailureJournal implements NativeActionLedger.Sink, AutoCloseable {
        private final FileChannel channel; private final String instance; private long sequence; private int failedWrites;
        FailureJournal(Path path, long sequence, String instance) throws IOException {
            channel = FileChannel.open(path, StandardOpenOption.WRITE, StandardOpenOption.APPEND);
            this.sequence = sequence; this.instance = instance;
        }
        @Override public void append(String type, Map<String,Object> payload) throws Exception {
            if ("native_action_outcome".equals(type) && "feed".equals(payload.get("kind"))) { failedWrites++; channel.close(); }
            write(type, payload);
        }
        void write(String type, Map<String,Object> payload) throws IOException {
            byte[] bytes = (GSON.toJson(map("sequence", sequence + 1, "bridge_instance_id", instance, "occurred_at", Instant.now().toString(), "type", type, "payload", payload)) + "\n").getBytes(StandardCharsets.UTF_8);
            ByteBuffer data = ByteBuffer.wrap(bytes); while (data.hasRemaining()) channel.write(data); channel.force(true); sequence++;
        }
        @Override public void close() throws IOException { channel.close(); }
    }
    private static JsonObject canonical(Part part) {
        double height = part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();
        return GSON.toJsonTree(map("schemaVersion", 1, "id", "ledger-recovery", "units", "mm", "coordinateConvention", "openpnp-top-view",
            "parts", List.of(map("id", part.getId(), "packageId", part.getPackage().getId(), "heightMm", height)),
            "boards", List.of(map("id", "board", "widthMm", 20, "heightMm", 20, "placements", List.of(map("ref", "R1", "partId", part.getId(), "packageId", part.getPackage().getId(), "heightMm", height, "x", 10, "y", 10, "z", 0, "rotation", 0, "side", "top", "enabled", true, "type", "placement")))),
            "panels", List.of(), "instances", List.of(map("id", "board-1", "kind", "board", "definitionId", "board", "x", 0, "y", 0, "z", 0, "rotation", 0, "side", "top", "enabled", true)))).getAsJsonObject();
    }
    private static long actionCount(Path path) throws IOException {
        try (java.util.stream.Stream<String> lines = Files.lines(path)) {
            return lines.map(line -> GSON.fromJson(line, JsonObject.class)).filter(value -> NativeActionLedger.isLedgerEventType(value.get("type").getAsString())).count();
        }
    }
    private static void checkPrefix(Path path, byte[] prefix, String message) throws IOException {
        byte[] bytes = Files.readAllBytes(path); check(bytes.length >= prefix.length && Arrays.equals(prefix, Arrays.copyOf(bytes, prefix.length)), message);
    }
    private static String hash(byte[] bytes) throws Exception {
        StringBuilder text = new StringBuilder(); for (byte value : MessageDigest.getInstance("SHA-256").digest(bytes)) text.append(String.format(Locale.ROOT, "%02x", value)); return text.toString();
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> invoke(String method, JsonObject args) throws Exception { return (Map<String,Object>) bridge.call(method, args); }
    private static Map<String,Object> call(String method, Object... pairs) throws Exception { return invoke(method, GSON.toJsonTree(map(pairs)).getAsJsonObject()); }
    private static JsonObject mutation(Object... pairs) { Map<String,Object> args = map("request_id", UUID.randomUUID().toString(), "session_id", session); args.putAll(map(pairs)); return GSON.toJsonTree(args).getAsJsonObject(); }
    @SuppressWarnings("unchecked") private static Map<String,Object> succeed(String method, JsonObject args) throws Exception {
        String id = (String) invoke(method, args).get("operation_id"); long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(30);
        do {
            Map<String,Object> operation = call("openpnp_get_operation", "operation_id", id); String state = (String) operation.get("state");
            if ("succeeded".equals(state) && !Configuration.get().getMachine().isBusy()) return (Map<String,Object>) operation.get("result");
            if (Arrays.asList("failed", "cancelled", "aborted", "outcome_unknown").contains(state)) throw new AssertionError(operation);
            Thread.sleep(10);
        } while (System.nanoTime() < deadline);
        throw new AssertionError("Operation did not finish: " + id);
    }
    private static void expect(String code, String method, JsonObject args) throws Exception {
        try { invoke(method, args); } catch (Bridge.Fault error) { if (code.equals(error.code)) { check(true, "expected native refusal: " + code); return; } throw error; }
        throw new AssertionError("Expected " + code);
    }
    private static Map<String,Object> map(Object... pairs) { return Bridge.map(pairs); }
    private static void check(boolean condition, String message) { if (!condition) throw new AssertionError(message); passed.add(message); System.out.println("PASS " + message); }
}
