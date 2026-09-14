/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.beans.PropertyChangeListener;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.openpnp.model.*;
import org.openpnp.spi.Feeder;

/** Regression boundaries exercised against real OpenPnP models and the native executor. */
public final class NativeBoundaryTest {
    private static final Gson GSON = new Gson();
    private static final List<String> passed = new ArrayList<>();
    private static Bridge bridge;
    private static String session;
    private static Path configurationRoot;

    public static void main(String[] args) throws Exception {
        Path root = Files.createTempDirectory("openpnp-native-boundary-");
        configurationRoot = root.resolve("configuration");
        Files.createDirectory(configurationRoot);
        Path token = root.resolve("token");
        Files.writeString(token, UUID.randomUUID().toString() + UUID.randomUUID());
        Configuration.initialize(configurationRoot.toFile());
        Configuration.get().load();
        SimulatorMain.accelerateFixture(Configuration.get());
        bridge = new Bridge(Configuration.get(), token, root.resolve("journal"), Paths.get(args[0]), 0, true);
        int exitCode = 0;
        try {
            session = (String) call("openpnp_request_control_session", "request_id", "boundary-grant", "ttl_seconds", 300).get("session_id");
            succeed("openpnp_set_machine_enabled", mutation("enabled", true));
            succeed("openpnp_home_machine", mutation());
            checkStalePlanners();
            checkDisabledBoardValidation();
            checkControlReceipts();
            checkFailedPersistenceInvalidation();
            System.out.println("OPENPNP_NATIVE_BOUNDARY_RESULT " + GSON.toJson(Bridge.map(
                "passed", passed, "upstream_commit", Bridge.UPSTREAM, "simulation_only", true,
                "fault_injection", "temporary configuration destination becomes a directory after part-height mutation")));
        } catch (Throwable error) {
            error.printStackTrace();
            exitCode = 1;
        } finally {
            bridge.close();
            Configuration.get().getMachine().close();
        }
        System.exit(exitCode);
    }

    private static void checkStalePlanners() throws Exception {
        String before = (String) call("openpnp_get_status").get("config_revision");
        JsonArray changes = new JsonArray();
        changes.add(object("type", "set_machine_speed", "speed", 0.8));
        Map<String,Object> plan = invoke("openpnp_plan_configuration", mutation("changes", changes));
        succeed("openpnp_apply_configuration", mutation("plan_id", plan.get("plan_id")));
        check(!before.equals(call("openpnp_get_status").get("config_revision")), "successful change advances native revision");
        expect("REVISION_CONFLICT", "openpnp_plan_motion", mutation(
            "expected_config_revision", before, "units", "mm", "x", 5, "y", 5, "z", 0));
        expect("REVISION_CONFLICT", "openpnp_plan_configuration", mutation(
            "expected_config_revision", before, "changes", changes));
        passed.add("both planners reject the caller's stale expected revision");
    }

    private static void checkDisabledBoardValidation() throws Exception {
        succeed("openpnp_prepare_job", mutation("sample", "pnp-test"));
        Part fed = fedPart();
        double height = fed.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();
        String absentPartId = "boundary-unfed-part";
        JsonArray parts = new JsonArray();
        parts.add(object("id", fed.getId(), "packageId", fed.getPackage().getId(), "heightMm", height));
        parts.add(object("id", absentPartId, "packageId", fed.getPackage().getId(), "heightMm", height));
        JsonArray boards = new JsonArray();
        boards.add(board("ready-board", fed, fed.getId(), height));
        boards.add(board("x-out-board", fed, absentPartId, height));
        JsonArray instances = new JsonArray();
        instances.add(instance("ready-copy", "ready-board", true));
        instances.add(instance("x-out-copy", "x-out-board", false));
        JsonObject canonical = object("schemaVersion", 1, "id", "boundary-x-out", "units", "mm",
            "coordinateConvention", "openpnp-top-view");
        canonical.add("parts", parts);
        canonical.add("boards", boards);
        canonical.add("panels", new JsonArray());
        canonical.add("instances", instances);
        succeed("openpnp_prepare_job", mutation("canonical_job", canonical));
        Map<String,Object> validation = result(succeed("openpnp_validate_job", mutation()));
        check(Boolean.TRUE.equals(validation.get("valid")), "disabled board's unfed part does not block native model validation");
        Map<?,?> job = (Map<?,?>) validation.get("job");
        check(((Number) job.get("requested")).intValue() == 1, "X-out scope agrees between validation and native job summary");
    }

    private static void checkControlReceipts() throws Exception {
        Map<String,Object> prepared = result(succeed("openpnp_prepare_job", mutation("sample", "pnp-test")));
        check(Boolean.TRUE.equals(result(succeed("openpnp_validate_job", mutation())).get("valid")), "control fixture native model validates");
        String operation;
        JsonObject pauseOne;
        // Hold only the bridge monitor so its real executor cannot race the first pause request.
        synchronized (bridge) {
            operation = (String) invoke("openpnp_start_job", mutation("job_id", prepared.get("job_id"))).get("operation_id");
            pauseOne = mutation("operation_id", operation);
            invoke("openpnp_pause_job", pauseOne);
        }
        Map<String,Object> firstPause = awaitState(operation, "paused", 30_000);
        awaitIdle();
        assertReceipt(pauseOne, operation);

        JsonObject resumeOne = mutation("operation_id", operation);
        JsonObject pauseTwo = mutation("operation_id", operation);
        synchronized (bridge) {
            // Deliberately discard the first response, then issue the next independently identified pause.
            invoke("openpnp_resume_job", resumeOne);
            invoke("openpnp_pause_job", pauseTwo);
        }
        awaitNewPause(operation, firstPause.get("updated_at"));
        awaitIdle();
        assertReceipt(resumeOne, operation);
        assertReceipt(pauseTwo, operation);
        synchronized (bridge) {
            Map<String,Object> replay = invoke("openpnp_resume_job", resumeOne);
            check(operation.equals(replay.get("operation_id")), "resume replay returns the original native job operation");
            check("paused".equals(call("openpnp_get_status").get("job_state")), "replayed first resume cannot undo a later pause");
        }
        expect("REQUEST_ID_CONFLICT", "openpnp_abort_job", resumeOne);
        JsonObject abort = mutation("operation_id", operation);
        invoke("openpnp_abort_job", abort);
        awaitState(operation, "aborted", 30_000);
        awaitIdle();
        assertReceipt(abort, operation);
        passed.add("control request identities reconcile lost responses and reject collisions");
    }

    private static void checkFailedPersistenceInvalidation() throws Exception {
        Map<String,Object> prepared = result(succeed("openpnp_prepare_job", mutation("sample", "pnp-test")));
        check(Boolean.TRUE.equals(result(succeed("openpnp_validate_job", mutation())).get("valid")), "persistence fixture is validated before injected failure");
        Part part = fedPart();
        double changedHeight = part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue() + 0.1;
        String previousRevision = (String) call("openpnp_get_status").get("config_revision");
        JsonArray changes = new JsonArray();
        changes.add(object("type", "set_part_height", "part_id", part.getId(), "height_mm", changedHeight));
        Map<String,Object> plan = invoke("openpnp_plan_configuration", mutation("changes", changes));
        Path destination = configurationRoot.resolve("machine.xml");
        Path original = configurationRoot.resolve("machine-before-injected-failure.xml");
        AtomicBoolean injected = new AtomicBoolean();
        AtomicReference<Throwable> injectionError = new AtomicReference<>();
        AtomicReference<Object> injectedDirectoryKey = new AtomicReference<>();
        PropertyChangeListener fault = change -> {
            if (!injected.compareAndSet(false, true)) return;
            try {
                // This listener runs after the real setter and first save, before the final save.
                Files.move(destination, original);
                Files.createDirectory(destination);
                injectedDirectoryKey.set(Files.readAttributes(destination, java.nio.file.attribute.BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS).fileKey());
                Files.writeString(destination.resolve("blocked"), "private test fixture");
            } catch (Throwable error) { injectionError.set(error); }
        };
        part.addPropertyChangeListener("height", fault);
        Throwable primaryFailure = null;
        try {
            Map<String,Object> receipt = invoke("openpnp_apply_configuration", mutation("plan_id", plan.get("plan_id")));
            awaitState((String) receipt.get("operation_id"), "failed", 30_000);
            awaitIdle();
            check(injected.get() && injectionError.get() == null, "final save fails against a real invalid filesystem destination");
            check(Math.abs(part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue() - changedHeight) < 1e-9,
                "failure occurs after the real native model changed");
            Map<String,Object> status = call("openpnp_get_status");
            check(!previousRevision.equals(status.get("config_revision")), "failed persistence invalidates the old revision");
            check(!"validated".equals(status.get("job_state")), "failed persistence revokes the prior validated job state");
            expect("CONFIGURATION_FAULT", "openpnp_start_job", mutation("job_id", prepared.get("job_id")));
            expect("CONFIGURATION_FAULT", "openpnp_plan_motion", mutation("units", "mm", "x", 5, "y", 5, "z", 0));
            passed.add("configuration fault fences job start and motion planning after partial save");
        } catch (Exception | Error failure) {
            primaryFailure = failure;
            throw failure;
        } finally {
            try {
                part.removePropertyChangeListener("height", fault);
                if (Files.isDirectory(destination, LinkOption.NOFOLLOW_LINKS)) {
                    // Restore access only on the exact directory this fault fixture created.
                    Object currentKey = Files.readAttributes(destination, java.nio.file.attribute.BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS).fileKey();
                    if (!injected.get() || injectedDirectoryKey.get() == null || !injectedDirectoryKey.get().equals(currentKey))
                        throw new java.io.IOException("Injected test directory identity changed before cleanup");
                    Set<java.nio.file.attribute.PosixFilePermission> permissions = Files.getPosixFilePermissions(destination, LinkOption.NOFOLLOW_LINKS);
                    permissions.add(java.nio.file.attribute.PosixFilePermission.OWNER_WRITE);
                    permissions.add(java.nio.file.attribute.PosixFilePermission.OWNER_EXECUTE);
                    Files.setPosixFilePermissions(destination, permissions);
                    Files.deleteIfExists(destination.resolve("blocked"));
                    Files.delete(destination);
                }
                if (Files.exists(original)) Files.move(original, destination, StandardCopyOption.REPLACE_EXISTING);
            } catch (Exception | Error cleanupFailure) {
                if (primaryFailure != null) primaryFailure.addSuppressed(cleanupFailure);
                else throw cleanupFailure;
            }
        }
    }

    private static Part fedPart() {
        for (Feeder feeder : Configuration.get().getMachine().getFeeders()) {
            Part part = feeder.getPart();
            if (feeder.isEnabled() && part != null && part.getPackage() != null && !part.isPartHeightUnknown()) return part;
        }
        throw new AssertionError("Native fixture has no enabled feeder with a known-height part");
    }

    private static JsonObject board(String id, Part packageSource, String partId, double height) {
        JsonArray placements = new JsonArray();
        placements.add(object("ref", "R1", "partId", partId, "packageId", packageSource.getPackage().getId(),
            "heightMm", height, "x", 10, "y", 10, "z", 0, "rotation", 0, "side", "top", "enabled", true, "type", "placement"));
        JsonObject board = object("id", id, "widthMm", 100, "heightMm", 100);
        board.add("placements", placements);
        return board;
    }

    private static JsonObject instance(String id, String board, boolean enabled) {
        return object("id", id, "kind", "board", "definitionId", board, "side", "top",
            "x", 0, "y", 0, "z", 0, "rotation", 0, "enabled", enabled);
    }

    private static void assertReceipt(JsonObject command, String operation) throws Exception {
        Map<String,Object> status = call("openpnp_get_request_status", "request_id", command.get("request_id").getAsString());
        check(Boolean.TRUE.equals(status.get("found")) && operation.equals(((Map<?,?>) status.get("operation")).get("operation_id")),
            "control request is discoverable by its original request ID");
    }

    private static Map<String,Object> succeed(String method, JsonObject params) throws Exception {
        Map<String,Object> accepted = invoke(method, params);
        Map<String,Object> operation = awaitState((String) accepted.get("operation_id"), "succeeded", 30_000);
        awaitIdle();
        return operation;
    }

    private static Map<String,Object> awaitState(String id, String target, long timeout) throws Exception {
        long deadline = System.nanoTime() + timeout * 1_000_000;
        Map<String,Object> operation;
        do {
            operation = call("openpnp_get_operation", "operation_id", id);
            String state = (String) operation.get("state");
            if (target.equals(state)) return operation;
            if (Arrays.asList("succeeded", "failed", "aborted", "cancelled", "outcome_unknown").contains(state))
                throw new AssertionError("Expected " + target + ": " + operation);
            Thread.sleep(10);
        } while (System.nanoTime() < deadline);
        throw new AssertionError("Timed out waiting for " + target + ": " + operation);
    }

    private static void awaitIdle() throws Exception {
        long deadline = System.nanoTime() + 10_000_000_000L;
        while (Configuration.get().getMachine().isBusy()) {
            if (System.nanoTime() > deadline) throw new AssertionError("Native executor did not become idle");
            Thread.sleep(10);
        }
    }

    private static void awaitNewPause(String id, Object earlierUpdate) throws Exception {
        long deadline = System.nanoTime() + 30_000_000_000L;
        do {
            Map<String,Object> operation = call("openpnp_get_operation", "operation_id", id);
            if ("paused".equals(operation.get("state")) && !Objects.equals(earlierUpdate, operation.get("updated_at"))) return;
            if (Arrays.asList("succeeded", "failed", "aborted", "outcome_unknown").contains(operation.get("state")))
                throw new AssertionError("Expected a second paused transition: " + operation);
            Thread.sleep(10);
        } while (System.nanoTime() < deadline);
        throw new AssertionError("Second pause did not reach the native executor boundary");
    }

    private static void expect(String code, String method, JsonObject params) throws Exception {
        try { invoke(method, params); }
        catch (Bridge.Fault error) { if (code.equals(error.code)) return; throw error; }
        throw new AssertionError("Expected " + code + " for " + method);
    }

    @SuppressWarnings("unchecked")
    private static Map<String,Object> result(Map<String,Object> operation) { return (Map<String,Object>) operation.get("result"); }
    @SuppressWarnings("unchecked")
    private static Map<String,Object> invoke(String method, JsonObject params) throws Exception { return (Map<String,Object>) bridge.call(method, params); }
    private static Map<String,Object> call(String method, Object... pairs) throws Exception { return invoke(method, object(pairs)); }
    private static JsonObject object(Object... pairs) { return GSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject(); }
    private static JsonObject mutation(Object... pairs) {
        JsonObject params = object("request_id", UUID.randomUUID().toString(), "session_id", session);
        for (Map.Entry<String,JsonElement> field : object(pairs).entrySet()) params.add(field.getKey(), field.getValue());
        return params;
    }
    private static void check(boolean condition, String name) {
        if (!condition) throw new AssertionError(name);
        passed.add(name);
        System.out.println("PASS " + name);
    }
}
