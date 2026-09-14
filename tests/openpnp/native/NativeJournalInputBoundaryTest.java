/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import org.openpnp.model.Configuration;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.feeder.*;

/**
 * Malformed retained journal input against the actual Bridge constructor and native models.
 * Each rejected input is retained byte-for-byte, then only the test journal is repaired so a
 * same-JVM retry proves constructor cleanup released its exclusive journal lock. No RPC
 * mutation, native task, machine motion, process-crash, or hardware qualification is claimed.
 */
public final class NativeJournalInputBoundaryTest {
    private static final Gson GSON = new Gson();
    private static final List<String> assertions = new ArrayList<>();
    private static final List<Map<String,Object>> cases = new ArrayList<>();

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Expected pinned sample root");
        Path root = Files.createTempDirectory("openpnp-native-journal-input-");
        Path configPath = Files.createDirectory(root.resolve("config"));
        Configuration.initialize(configPath.toFile()); Configuration config = Configuration.get(); config.load();
        Path token = root.resolve("token"); Files.writeString(token, UUID.randomUUID().toString() + UUID.randomUUID());
        Path samples = Paths.get(args[0]); int result = 0;
        try {
            // Independent advertised contract: 8 MiB maximum retained record, plus newline.
            byte[] oversized = new byte[8 * 1024 * 1024 + 2];
            Arrays.fill(oversized, (byte) ' '); oversized[oversized.length - 1] = '\n';
            scenario(root, "oversized-single-record", oversized, config, token, samples);
            String deep = "{\"sequence\":1,\"type\":\"fixture\",\"payload\":" + "[".repeat(65) + "0" + "]".repeat(65) + "}\n";
            scenario(root, "nested-beyond-depth-64", deep.getBytes(StandardCharsets.UTF_8), config, token, samples);
            scenario(root, "missing-final-newline", record(1).stripTrailing().getBytes(StandardCharsets.UTF_8), config, token, samples);
            byte[] prefix = "{\"sequence\":1,\"type\":\"fixture\",\"payload\":\"".getBytes(StandardCharsets.UTF_8);
            byte[] suffix = "\"}\n".getBytes(StandardCharsets.UTF_8);
            byte[] invalidUtf8 = Arrays.copyOf(prefix, prefix.length + 2 + suffix.length);
            invalidUtf8[prefix.length] = (byte) 0xc3; invalidUtf8[prefix.length + 1] = (byte) 0x28;
            System.arraycopy(suffix, 0, invalidUtf8, prefix.length + 2, suffix.length);
            scenario(root, "malformed-utf8", invalidUtf8, config, token, samples);
            scenario(root, "global-sequence-gap", (record(1) + record(3)).getBytes(StandardCharsets.UTF_8), config, token, samples);
            System.out.println("OPENPNP_NATIVE_JOURNAL_INPUT_RESULT " + GSON.toJson(Bridge.map(
                "assertions", assertions.size(), "passed", assertions, "cases", cases,
                "upstream_commit", Bridge.UPSTREAM, "simulation_only", true, "physical_qualification", false,
                "native_mutations", 0, "native_feed_effects", 0, "process_crash_test", false,
                "constructor_retry_scope", "same JVM, same native configuration and repaired test journal; original rejected bytes retained",
                "evidence_directory", root.toString())));
        } catch (Throwable failure) { failure.printStackTrace(); result = 1; }
        finally { config.getMachine().close(); }
        System.exit(result);
    }

    private static void scenario(Path root, String label, byte[] input, Configuration config, Path token, Path samples) throws Exception {
        Path evidence = Files.createDirectory(root.resolve(label));
        Path journalDirectory = Files.createDirectory(evidence.resolve("journal"));
        Path journal = journalDirectory.resolve("operations.jsonl");
        Files.write(journal, input, StandardOpenOption.CREATE_NEW);
        Files.write(evidence.resolve("rejected-original.jsonl"), input, StandardOpenOption.CREATE_NEW);
        String originalHash = hash(input);
        Map<String,Object> before = nativeState(config.getMachine());
        Bridge unexpected = null; Exception rejected = null;
        try { unexpected = new Bridge(config, token, journalDirectory, samples, 0, true); }
        catch (Exception failure) { rejected = failure; }
        finally { if (unexpected != null) unexpected.close(); }
        check(rejected instanceof IOException, label + ": constructor reports bounded malformed-journal refusal");
        check(originalHash.equals(hash(Files.readAllBytes(journal))), label + ": rejected journal remains byte-identical");
        check(before.equals(nativeState(config.getMachine())), label + ": refusal has no native enabled/homed/feed/nozzle effect");
        Files.write(journal, record(1).getBytes(StandardCharsets.UTF_8), StandardOpenOption.TRUNCATE_EXISTING);
        try (Bridge repaired = new Bridge(config, token, journalDirectory, samples, 0, true)) {
            Map<?,?> status = (Map<?,?>) repaired.call("openpnp_get_status", new JsonObject());
            check(((Number) status.get("through_sequence")).longValue() == 1 && status.get("active_operation_id") == null,
                label + ": same-JVM repaired retry acquires the journal lock and reads its original sequence");
            check(Boolean.FALSE.equals(status.get("journal_fault")) && Boolean.FALSE.equals(status.get("native_busy")),
                label + ": repaired constructor has no stale fault or native executor work");
        }
        check(before.equals(nativeState(config.getMachine())), label + ": repaired read-only Bridge preserves native model state");
        check(originalHash.equals(hash(Files.readAllBytes(evidence.resolve("rejected-original.jsonl")))), label + ": exact rejected evidence remains retained after repair");
        cases.add(Bridge.map("case", label, "rejected_input_bytes", input.length, "rejected_input_sha256", originalHash,
            "expected_failure_class", rejected.getClass().getSimpleName(), "same_jvm_retry_succeeded", true));
    }
    private static String record(long sequence) {
        return GSON.toJson(Bridge.map("sequence", sequence, "bridge_instance_id", "fixture-input-validation", "type", "fixture-observation", "payload", Bridge.map("no_native_effect", true))) + "\n";
    }
    private static Map<String,Object> nativeState(Machine machine) {
        Map<String,Object> feeders = new LinkedHashMap<>(), nozzles = new LinkedHashMap<>();
        for (Feeder feeder : machine.getFeeders()) {
            if (feeder instanceof ReferenceStripFeeder) feeders.put(feeder.getId(), ((ReferenceStripFeeder) feeder).getFeedCount());
            if (feeder instanceof ReferenceTrayFeeder) feeders.put(feeder.getId(), ((ReferenceTrayFeeder) feeder).getFeedCount());
        }
        for (Head head : machine.getHeads()) for (Nozzle nozzle : head.getNozzles())
            nozzles.put(nozzle.getId(), Bridge.map("part_id", nozzle.getPart() == null ? null : nozzle.getPart().getId(), "tip_id", nozzle.getNozzleTip() == null ? null : nozzle.getNozzleTip().getId()));
        return Bridge.map("enabled", machine.isEnabled(), "homed", machine.isHomed(), "busy", machine.isBusy(), "feed_counts", feeders, "nozzles", nozzles);
    }
    private static String hash(byte[] bytes) throws Exception {
        StringBuilder out = new StringBuilder(); for (byte value : MessageDigest.getInstance("SHA-256").digest(bytes)) out.append(String.format(Locale.ROOT, "%02x", value)); return out.toString();
    }
    private static void check(boolean valid, String message) {
        if (!valid) throw new AssertionError(message); assertions.add(message); System.out.println("PASS " + message);
    }
}
