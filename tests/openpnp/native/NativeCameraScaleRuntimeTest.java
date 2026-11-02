/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Manifest refusal tests plus optional actual loaded-runtime admission. No machine is opened. */
public final class NativeCameraScaleRuntimeTest {
    private static int checks;
    private static final Gson JSON=new Gson();
    private interface Checked {void run()throws Exception;}
    private static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    private static void reject(Checked call)throws Exception {
        try{call.run();throw new AssertionError("Malformed runtime provenance was accepted");}
        catch(IllegalArgumentException expected){checks++;}
    }
    private static Map<String,Object> copy(Map<String,Object> value){return NativeJournalJson.copy(value);}
    @SuppressWarnings("unchecked")
    public static void main(String[]args)throws Exception {
        Path root=Files.createTempDirectory("openpnp-scale-runtime-");Path jar=root.resolve("native.jar");Files.write(jar,new byte[]{1,2,3});
        Map<String,Object> flag=Bridge.map("api_version",1,"patch_id",NativeCameraScaleRuntime.PATCH_ID,"patch_sha256",NativeCameraScaleRuntime.PATCH_SHA256,
            "source_sha256",NativeCameraScaleRuntime.SOURCE_SHA256,"class_family_sha256",new LinkedHashMap<>(NativeCameraScaleRuntime.FAMILY));
        Map<String,Object> valid=Bridge.map("upstream_commit",Bridge.UPSTREAM,"gui_jar","native.jar","native_circular_symmetry",flag,
            "files",List.of(Bridge.map("path","native.jar","sha256",GuiBootstrap.hash(Files.readAllBytes(jar)))));
        byte[] bytes=JSON.toJson(valid).getBytes(StandardCharsets.UTF_8);
        check(Boolean.TRUE.equals(NativeCameraScaleRuntime.verifyManifest(valid,jar,bytes).get("available")),"Valid manifest is readable without opening a machine");
        for(String field:List.of("api_version","patch_id","patch_sha256","source_sha256","class_family_sha256")) {
            Map<String,Object> broken=copy(valid);((Map<String,Object>)broken.get("native_circular_symmetry")).remove(field);
            reject(()->NativeCameraScaleRuntime.verifyManifest(broken,jar,bytes));
        }
        for(Object version:List.of(0,2,"1",new java.math.BigDecimal("1.000000000000000001"))) {
            Map<String,Object> broken=copy(valid);((Map<String,Object>)broken.get("native_circular_symmetry")).put("api_version",version);
            reject(()->NativeCameraScaleRuntime.verifyManifest(broken,jar,bytes));
        }
        for(String field:List.of("patch_id","patch_sha256","source_sha256")) {
            Map<String,Object> broken=copy(valid);((Map<String,Object>)broken.get("native_circular_symmetry")).put(field,"wrong");
            reject(()->NativeCameraScaleRuntime.verifyManifest(broken,jar,bytes));
        }
        for(String member:NativeCameraScaleRuntime.FAMILY.keySet()) {
            Map<String,Object> broken=copy(valid);((Map<?,?>)((Map<?,?>)broken.get("native_circular_symmetry")).get("class_family_sha256")).remove(member);
            reject(()->NativeCameraScaleRuntime.verifyManifest(broken,jar,bytes));
        }
        Map<String,Object> missing=copy(valid);missing.remove("native_circular_symmetry");reject(()->NativeCameraScaleRuntime.verifyManifest(missing,jar,bytes));
        Map<String,Object> upstream=copy(valid);upstream.put("upstream_commit","unqualified");reject(()->NativeCameraScaleRuntime.verifyManifest(upstream,jar,bytes));
        Map<String,Object> wrong=copy(valid);wrong.put("gui_jar","other.jar");reject(()->NativeCameraScaleRuntime.verifyManifest(wrong,jar,bytes));
        Map<String,Object> duplicate=copy(valid);List<Object> rows=new ArrayList<>((List<?>)duplicate.get("files"));rows.add(rows.get(0));duplicate.put("files",rows);
        reject(()->NativeCameraScaleRuntime.verifyManifest(duplicate,jar,bytes));
        Map<String,Object> noJar=copy(valid);noJar.put("files",List.of());reject(()->NativeCameraScaleRuntime.verifyManifest(noJar,jar,bytes));
        Files.write(jar,new byte[]{1,2,4});reject(()->NativeCameraScaleRuntime.verifyManifest(valid,jar,bytes));
        boolean loaded=false;
        if(args.length==0||!"stock".equals(args[0])) {check(Boolean.TRUE.equals(NativeCameraScaleRuntime.require().get("available")),"Actual loaded packaged detector family is admitted");loaded=true;}
        else {try{NativeCameraScaleRuntime.require();throw new AssertionError("Stock runtime admitted");}catch(Bridge.Fault expected){check("UNSUPPORTED_CALIBRATION_RUNTIME".equals(expected.code),"Stock or incomplete runtime refused");}}
        System.out.println("OPENPNP_CAMERA_SCALE_RUNTIME_RESULT "+JSON.toJson(Bridge.map("checks",checks,"actual_loaded_patched_runtime",loaded,"machine_opened",false,"physical_qualification",false)));
    }
}
