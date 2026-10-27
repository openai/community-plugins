/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeSet;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import org.openpnp.machine.reference.camera.ImageCamera;
import org.openpnp.model.Configuration;
import org.openpnp.vision.pipeline.stages.DetectCircularSymmetry;

/** Qualifies the detector prerequisite; this is not an in-process Java sandbox. */
final class NativeCameraScaleRuntime {
    static final String PATCH_ID="native-circular-symmetry-v1";
    static final String PATCH_SHA256="e08ebaa16e357f0055fcfed322830cd5afeb0131c58ac8875983baef80413a20";
    static final String SOURCE_SHA256="98297ed9816b1fd499c93cfa52b6c643f53f5a11528c39d2c3dd6e8a3ecb1d51";
    private static final String PREFIX="org/openpnp/vision/pipeline/stages/DetectCircularSymmetry";
    static final Map<String,String> FAMILY;
    static {
        Map<String,String> hashes=new LinkedHashMap<>();
        hashes.put(PREFIX+".class","cfe9cbdc44661b74b79da111a0e6002c7487e851e26b1c077543b1d0ff6457fc");
        hashes.put(PREFIX+"$1.class","5813425adb8e6316cc9bf776bb0d3262c10afe1f837d926bb33c0573e0c6c213");
        hashes.put(PREFIX+"$2.class","b606772f48550fb28ddb5e0674f30d40f324c7dee4d3a76c473edb9d2dd2e3d1");
        hashes.put(PREFIX+"$ScoreRange.class","9435e518ba23ee9c6fbab4ea6e48adac9216d5b233fc10f7e54caeafccf88eef");
        hashes.put(PREFIX+"$SymmetryCircle.class","117a179aa78b69313125ed62dc4aeb4736ab73bbedc340be6cef1982d3c1a11d");
        hashes.put(PREFIX+"$SymmetryScore.class","33da31f4c3ae782ea412c8eb5052728cfe41cfd12f2a3d94e122e924922e8cbb");
        FAMILY=Collections.unmodifiableMap(hashes);
    }
    private NativeCameraScaleRuntime() { }

    static Map<String,Object> require() throws Bridge.Fault {
        try {
            Path nativeJar=jar(DetectCircularSymmetry.class);
            if(!nativeJar.equals(jar(ImageCamera.class))||!nativeJar.equals(jar(Configuration.class)))
                throw new IllegalArgumentException("Mixed native class origins");
            Path manifest=nativeJar.getParent().resolve("codex-build-manifest.json");
            if(!Files.isRegularFile(manifest,LinkOption.NOFOLLOW_LINKS)||Files.size(manifest)>1024*1024)
                throw new IllegalArgumentException("Bounded runtime manifest required");
            byte[] bytes=Files.readAllBytes(manifest);
            Map<String,Object> document=NativeJournalJson.parseObject(new String(bytes,StandardCharsets.UTF_8));
            Map<String,Object> result=verifyManifest(document,nativeJar,bytes);
            try(ZipFile archive=new ZipFile(nativeJar.toFile())) {
                Set<String> actual=new TreeSet<>();
                for(ZipEntry entry:Collections.list(archive.entries())) {
                    String name=entry.getName();
                    if(name.equals(PREFIX+".class")||(name.startsWith(PREFIX+"$")&&name.endsWith(".class"))) {
                        if(!actual.add(name))throw new IllegalArgumentException("Duplicate detector class");
                    }
                }
                if(!actual.equals(FAMILY.keySet()))throw new IllegalArgumentException("Incomplete detector family");
                for(Map.Entry<String,String> entry:FAMILY.entrySet()) {
                    try(InputStream stream=archive.getInputStream(archive.getEntry(entry.getKey()))) {
                        if(!entry.getValue().equals(hash(stream,1024*1024)))throw new IllegalArgumentException("Detector class differs");
                    }
                    Class<?> loaded=Class.forName(entry.getKey().substring(0,entry.getKey().length()-6).replace('/','.'),false,DetectCircularSymmetry.class.getClassLoader());
                    if(!nativeJar.equals(jar(loaded)))throw new IllegalArgumentException("Mixed detector class origins");
                    try(InputStream stream=loaded.getResourceAsStream("/"+entry.getKey())) {
                        if(stream==null||!entry.getValue().equals(hash(stream,1024*1024)))throw new IllegalArgumentException("Loaded detector resource differs");
                    }
                }
            }
            return result;
        } catch(Exception failure) {
            throw new Bridge.Fault("UNSUPPORTED_CALIBRATION_RUNTIME","Camera planar scale requires the verified native circular-symmetry runtime and complete class family");
        }
    }

    /** Exposed within this package for malformed-manifest tests; performs no native calls. */
    @SuppressWarnings("unchecked")
    static Map<String,Object> verifyManifest(Map<String,Object> document,Path nativeJar,byte[] manifestBytes)throws Exception {
        if(!Objects.equals(document.get("upstream_commit"),Bridge.UPSTREAM))throw new IllegalArgumentException("Wrong upstream");
        Object value=document.get("native_circular_symmetry");
        if(!(value instanceof Map))throw new IllegalArgumentException("Missing detector provenance");
        Map<String,Object> detector=(Map<String,Object>)value;
        if(NativeJournalJson.integer(detector.get("api_version"),1,1)!=1||!PATCH_ID.equals(detector.get("patch_id"))||
           !PATCH_SHA256.equals(detector.get("patch_sha256"))||!SOURCE_SHA256.equals(detector.get("source_sha256"))||
           !FAMILY.equals(detector.get("class_family_sha256")))throw new IllegalArgumentException("Wrong detector provenance");
        if(!nativeJar.getFileName().toString().equals(document.get("gui_jar")))throw new IllegalArgumentException("Native JAR not declared");
        String nativeHash;try(InputStream stream=Files.newInputStream(nativeJar)){nativeHash=hash(stream,128*1024*1024);}
        Object rows=document.get("files");if(!(rows instanceof List))throw new IllegalArgumentException("Missing runtime files");
        int found=0;Set<String> names=new TreeSet<>();
        for(Object row:(List<?>)rows) {
            if(!(row instanceof Map))throw new IllegalArgumentException("Invalid runtime entry");
            Map<?,?> file=(Map<?,?>)row;Object name=file.get("path");
            if(!(name instanceof String)||!names.add((String)name))throw new IllegalArgumentException("Duplicate runtime entry");
            if(nativeJar.getFileName().toString().equals(name)) {
                if(!nativeHash.equals(file.get("sha256")))throw new IllegalArgumentException("Native JAR digest differs");found++;
            }
        }
        if(found!=1)throw new IllegalArgumentException("Missing native JAR binding");
        return Bridge.map("available",true,"patch_id",PATCH_ID,"patch_sha256",PATCH_SHA256,"source_sha256",SOURCE_SHA256,
            "class_family_sha256",new LinkedHashMap<>(FAMILY),"native_jar_sha256",nativeHash,
            "runtime_manifest_sha256",hash(new java.io.ByteArrayInputStream(manifestBytes),1024*1024),"physical_qualification",false);
    }

    private static Path jar(Class<?> type)throws Exception {
        Path path=Paths.get(type.getProtectionDomain().getCodeSource().getLocation().toURI()).toRealPath();
        if(!Files.isRegularFile(path)||!path.getFileName().toString().endsWith(".jar"))throw new IllegalArgumentException("Packaged native classes required");
        return path;
    }
    private static String hash(InputStream stream,long limit)throws Exception {
        MessageDigest digest=MessageDigest.getInstance("SHA-256");byte[] buffer=new byte[8192];long total=0;int count;
        while((count=stream.read(buffer))!=-1){total+=count;if(total>limit)throw new IllegalArgumentException("Runtime input exceeds bound");digest.update(buffer,0,count);}
        StringBuilder result=new StringBuilder();for(byte b:digest.digest())result.append(String.format(java.util.Locale.ROOT,"%02x",b));return result.toString();
    }
}
