/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.util.*;
import org.openpnp.model.*;

/**
 * Import-local, existing-parts-only resolution. This is not an alias registry or
 * electrical-equivalence check. It never constructs, installs, or edits a Part.
 *
 * The canonical digest and immutable provenance freeze the proposed resolution;
 * callers must still own the native executor/model while validating and loading.
 * A resolver alone does not validate the complete job graph or authorize its use.
 */
public final class NativePartBindings {
    public static final String PROFILE = "existing-parts-v1";
    private static final int MAX_BINDINGS = 1000;
    private static final long MAX_CANONICAL_BYTES = 8L * 1024 * 1024;
    private final Configuration configuration;
    private final String canonicalSha256;
    private final Map<String, Resolved> resolved;
    private final List<Map<String, Object>> provenance;

    private static final class Resolved {
        final String sourceId, sourceKey, sourcePackageId, requestedId, nativeId, packageId;
        final Part part;
        final org.openpnp.model.Package pkg;
        final LengthUnit heightUnits;
        final double heightValue, heightMm;
        final boolean explicit;

        Resolved(String sourceId, String sourcePackageId, String requestedId, Part part,
                org.openpnp.model.Package pkg, boolean explicit) {
            this.sourceId = sourceId;
            sourceKey = key(sourceId);
            this.sourcePackageId = sourcePackageId;
            this.requestedId = requestedId;
            this.part = part;
            this.pkg = pkg;
            nativeId = part.getId();
            packageId = pkg.getId();
            heightUnits = part.getHeight().getUnits();
            heightValue = part.getHeight().getValue();
            heightMm = part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();
            this.explicit = explicit;
        }

        void validate(Configuration config) throws Bridge.Fault {
            if (!sourceKey.equals(key(sourceId)) || config.getPart(requestedId) != part
                    || !nativeId.equals(part.getId()) || config.getPart(nativeId) != part
                    || part.getPackage() != pkg || !packageId.equals(pkg.getId())
                    || config.getPackage(sourcePackageId) != pkg || config.getPackage(packageId) != pkg
                    || part.getHeight() == null || part.getHeight().getUnits() != heightUnits
                    || Double.doubleToLongBits(part.getHeight().getValue()) != Double.doubleToLongBits(heightValue)) {
                fail("PART_BINDINGS_STALE", "Native part/package identity or height changed: " + sourceId);
            }
        }
    }

    private NativePartBindings(Configuration configuration, String digest,
            Map<String, Resolved> resolved, List<Map<String, Object>> provenance) {
        this.configuration = configuration;
        canonicalSha256 = digest;
        this.resolved = Collections.unmodifiableMap(new LinkedHashMap<>(resolved));
        this.provenance = Collections.unmodifiableList(new ArrayList<>(provenance));
    }

    /** Presence of a nonempty, closed binding array opts the entire import into this profile. */
    public static NativePartBindings resolve(Configuration config, JsonObject canonical,
            JsonArray partBindings) throws Bridge.Fault {
        if (config == null || canonical == null) fail("INVALID_ARGUMENT", "Configuration and canonical job are required");
        if (partBindings == null || partBindings.size() == 0 || partBindings.size() > MAX_BINDINGS)
            fail("INVALID_PART_BINDINGS", "Expected 1..1000 explicit part bindings");
        String digest = fingerprint(canonical);
        Map<String, String> requested = new LinkedHashMap<>();
        for (JsonElement value : partBindings) {
            JsonObject row = object(value, "part binding");
            if (row.entrySet().size() != 2 || !row.has("source_part_id") || !row.has("native_part_id"))
                fail("INVALID_PART_BINDINGS", "Each binding requires only source_part_id and native_part_id");
            String source = id(row, "source_part_id"), target = id(row, "native_part_id");
            if (requested.putIfAbsent(key(source), target) != null)
                fail("DUPLICATE_PART_BINDING", "Duplicate native-case source binding: " + source);
        }
        Map<String, Resolved> resolved = new LinkedHashMap<>();
        Map<String, Double> declaredHeights = new LinkedHashMap<>();
        for (JsonElement value : array(canonical, "parts", 10000)) {
            JsonObject source = object(value, "canonical part");
            String sourceId = id(source, "id"), packageId = id(source, "packageId");
            double height = height(source);
            if (resolved.containsKey(key(sourceId))) fail("DUPLICATE_ID", "Duplicate part: " + sourceId);
            org.openpnp.model.Package pkg = config.getPackage(packageId);
            if (pkg == null) fail("PACKAGE_UNMAPPED", "Map package to an existing native package before import: " + packageId);
            boolean explicit = requested.containsKey(key(sourceId));
            String targetId = explicit ? requested.get(key(sourceId)) : sourceId;
            Part part = config.getPart(targetId);
            if (part == null) fail("PART_UNMAPPED", "Existing-parts import requires a configured native part: " + targetId);
            if (part.getClass() != Part.class || pkg.getClass() != org.openpnp.model.Package.class)
                fail("UNSUPPORTED_NATIVE_PROFILE", "Existing-parts import requires exact native Part and Package models");
            if (part.getId() == null || pkg.getId() == null || config.getPart(part.getId()) != part
                    || config.getPackage(pkg.getId()) != pkg || part.getPackage() != pkg)
                fail("PART_CONFLICT", "Native part/package reference differs from import: " + sourceId);
            if (part.getHeight() == null || part.getHeight().getUnits() == null)
                fail("PART_CONFLICT", "Native part has no compatible height: " + targetId);
            double actual = part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();
            if (!Double.isFinite(actual) || Math.abs(actual - height) > 1e-6)
                fail("PART_CONFLICT", "Native part height differs from import: " + sourceId);
            resolved.put(key(sourceId), new Resolved(sourceId, packageId, targetId, part, pkg, explicit));
            declaredHeights.put(key(sourceId), height);
        }
        for (String sourceKey : requested.keySet())
            if (!resolved.containsKey(sourceKey)) fail("UNKNOWN_PART_BINDING", "Binding source is absent from canonical parts: " + sourceKey);
        Map<String, Integer> counts = new HashMap<>();
        int placements = 0;
        for (JsonElement boardValue : array(canonical, "boards", 100)) {
            JsonObject board = object(boardValue, "canonical board");
            for (JsonElement value : array(board, "placements", 10000)) {
                if (++placements > 10000) fail("INPUT_TOO_LARGE", "At most 10000 definition placements are supported");
                String sourceKey = key(id(object(value, "canonical placement"), "partId"));
                if (!resolved.containsKey(sourceKey)) fail("PART_UNMAPPED", "Placement has no canonical part");
                counts.put(sourceKey, counts.getOrDefault(sourceKey, 0) + 1);
            }
        }
        List<Map<String, Object>> provenance = new ArrayList<>();
        for (Resolved entry : resolved.values()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("source_part_id", entry.sourceId);
            row.put("native_part_id", entry.nativeId);
            row.put("package_id", entry.packageId);
            row.put("canonical_height_mm", declaredHeights.get(entry.sourceKey));
            row.put("native_height_mm", entry.heightMm);
            row.put("explicit_binding", entry.explicit);
            row.put("definition_placement_count", counts.getOrDefault(entry.sourceKey, 0));
            provenance.add(Collections.unmodifiableMap(row));
        }
        NativePartBindings result = new NativePartBindings(config, digest, resolved, provenance);
        result.validate(config, canonical);
        return result;
    }

    public String canonicalSha256() { return canonicalSha256; }
    /** Binding provenance counts definition placements, not expanded instances or completed actions. */
    public List<Map<String, Object>> provenance() { return provenance; }

    void validate(Configuration config, JsonObject canonical) throws Bridge.Fault {
        if (config != configuration || canonical == null || !canonicalSha256.equals(fingerprint(canonical)))
            fail("PART_BINDINGS_STALE", "Part bindings belong to another configuration or canonical input");
        for (Resolved entry : resolved.values()) entry.validate(config);
    }

    Part part(String sourceId) throws Bridge.Fault {
        Resolved entry = resolved.get(key(sourceId));
        if (entry == null) fail("PART_BINDINGS_STALE", "Unresolved canonical part: " + sourceId);
        return entry.part;
    }

    // Match pinned Configuration.getPart/getPackage and the existing importer,
    // including native default-locale case behavior. Revalidation fences changes.
    private static String key(String id) { return id.toUpperCase(); }

    private static String id(JsonObject value, String name) throws Bridge.Fault {
        JsonElement field = value.get(name);
        if (field == null || !field.isJsonPrimitive() || !field.getAsJsonPrimitive().isString()
                || !field.getAsString().matches("[A-Za-z0-9_.:+-]{1,128}"))
            fail("INVALID_ID", "Unsupported identifier: " + name);
        return field.getAsString();
    }

    private static double height(JsonObject value) throws Bridge.Fault {
        JsonElement field = value.get("heightMm");
        if (field == null || !field.isJsonPrimitive() || !field.getAsJsonPrimitive().isNumber())
            fail("INVALID_ARGUMENT", "Expected numeric heightMm");
        double height = field.getAsDouble();
        if (!Double.isFinite(height) || height < 0.001 || height > 50)
            fail("OUT_OF_RANGE", "Out of range: heightMm");
        return height;
    }

    private static JsonObject object(JsonElement value, String name) throws Bridge.Fault {
        if (value == null || !value.isJsonObject()) fail("INVALID_ARGUMENT", "Expected object: " + name);
        return value.getAsJsonObject();
    }

    private static JsonArray array(JsonObject value, String name, int max) throws Bridge.Fault {
        JsonElement field = value.get(name);
        if (field == null || !field.isJsonArray() || field.getAsJsonArray().size() > max)
            fail("INVALID_ARRAY", "Expected bounded array: " + name);
        return field.getAsJsonArray();
    }

    // Bound both recursive Gson traversal and output allocation. The digest is of
    // Gson's UTF-8 canonical JSON representation, not an uploaded artifact's bytes.
    private static String fingerprint(JsonObject canonical) throws Bridge.Fault {
        boundedTree(canonical, 0, new int[1]);
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            OutputStream sink = new OutputStream() {
                long count;
                @Override public void write(int value) throws IOException {
                    if (++count > MAX_CANONICAL_BYTES) throw new IOException("canonical JSON exceeds 8 MiB");
                    digest.update((byte) value);
                }
                @Override public void write(byte[] bytes, int offset, int length) throws IOException {
                    if (count + length > MAX_CANONICAL_BYTES) throw new IOException("canonical JSON exceeds 8 MiB");
                    count += length;
                    digest.update(bytes, offset, length);
                }
            };
            try (Writer writer = new OutputStreamWriter(sink, StandardCharsets.UTF_8)) {
                new Gson().toJson(canonical, writer);
            }
            StringBuilder hex = new StringBuilder();
            for (byte b : digest.digest()) hex.append(String.format(Locale.ROOT, "%02x", b & 255));
            return hex.toString();
        } catch (IOException | JsonIOException e) {
            throw new Bridge.Fault("INPUT_TOO_LARGE", "Canonical binding fingerprint exceeds 8 MiB");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    private static void boundedTree(JsonElement value, int depth, int[] count) throws Bridge.Fault {
        if (depth > 64 || ++count[0] > 1000000) fail("INPUT_TOO_LARGE", "Canonical JSON depth or node limit exceeded");
        if (value.isJsonArray()) for (JsonElement child : value.getAsJsonArray()) boundedTree(child, depth + 1, count);
        else if (value.isJsonObject()) for (Map.Entry<String, JsonElement> child : value.getAsJsonObject().entrySet())
            boundedTree(child.getValue(), depth + 1, count);
    }

    private static void fail(String code, String message) throws Bridge.Fault { throw new Bridge.Fault(code, message); }
}
