/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;

/** Bounded fresh JVMs isolate singleton native configurations and preserve each failed attempt. */
public final class NativeVacuumBridgeSuiteTest {
    static final Gson JSON=new GsonBuilder().serializeNulls().create();static final List<Object> results=new ArrayList<>();static int checks;static Path root,samples;
    static void child(String label,String main,List<String> args)throws Exception {
        Path output=root.resolve(label);Files.createDirectories(output);Files.createDirectories(output.resolve("tmp"));
        List<String> command=new ArrayList<>(List.of(Path.of(System.getProperty("java.home"),"bin","java").toString(),"-Djava.awt.headless=true","-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory","--add-opens=java.base/java.lang=ALL-UNNAMED","--add-opens=java.desktop/java.awt=ALL-UNNAMED","--add-opens=java.desktop/java.awt.color=ALL-UNNAMED","-Djava.io.tmpdir="+output.resolve("tmp"),"-cp",System.getProperty("java.class.path"),main));command.addAll(args);
        Process p=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(output.resolve("output.log").toFile()).start();boolean forced=false;int exit;
        if(!p.waitFor(90,TimeUnit.SECONDS)){forced=true;p.destroy();if(!p.waitFor(5,TimeUnit.SECONDS)){p.destroyForcibly();p.waitFor();}}
        exit=p.exitValue();Path receipt=output.resolve("state/result.json");JsonObject detail=Files.isRegularFile(receipt)?new JsonParser().parse(Files.readString(receipt)).getAsJsonObject():null;
        if(detail!=null&&detail.has("checks"))checks+=detail.get("checks").getAsInt();
        results.add(Bridge.map("case",label,"exit_code",exit,"pid",p.pid(),"process_alive",p.isAlive(),"forced_cleanup",forced,"receipt",receipt.toString(),"receipt_sha256",Files.isRegularFile(receipt)?GuiBootstrap.hash(Files.readAllBytes(receipt)):null,"log_sha256",GuiBootstrap.hash(Files.readAllBytes(output.resolve("output.log"))),"command",command));
        if(exit!=0||forced||p.isAlive()||detail==null||!detail.get("passed").getAsBoolean())throw new AssertionError("Native vacuum child failed: "+label+"; retain "+output);
    }
    public static void main(String[]args)throws Exception {
        samples=Path.of(args[0]);root=args.length>1?Path.of(args[1]):Files.createTempDirectory("native-vacuum-bridge-suite-");Files.createDirectories(root);Throwable failure=null;
        List<String> producers=List.of("manual","success","unbound","source-drift","missed-pick-retry","retained-after-place","invalid-read","lost-before-place","intent-write","outcome-write","outcome-force","cleanup-write","outcome-error","lease","job-action-lease","job-read-lease","job-valve-lease","job-source-intent","step-resume","step-abort","step-drift","lifecycle-enable","lifecycle-disable","lifecycle-empty","lifecycle-postforce","legacy-start","legacy-step","legacy-disable");
        try{
            for(String mode:producers)child(mode,"org.openpnp.codex.NativeVacuumBridgeTest",List.of(samples.toString(),mode,root.resolve(mode+"/state").toString()));
            for(String mode:producers)if(!Set.of("unbound","source-drift","job-action-lease","step-abort","step-drift").contains(mode))child("recover-"+mode,"org.openpnp.codex.NativeVacuumBridgeRecoveryTest",List.of(samples.toString(),root.resolve(mode+"/state/journal").toString(),root.resolve("recover-"+mode+"/state").toString(),"control"));
            for(String mode:List.of("foreign-request","foreign-operation","foreign-config","foreign-load","duplicate-key"))child(mode,"org.openpnp.codex.NativeVacuumBridgeRecoveryTest",List.of(samples.toString(),root.resolve("success/state/journal").toString(),root.resolve(mode+"/state").toString(),mode));
            for(String mode:List.of("lifecycle-foreign-request","lifecycle-foreign-config"))child(mode,"org.openpnp.codex.NativeVacuumBridgeRecoveryTest",List.of(samples.toString(),root.resolve("lifecycle-empty/state/journal").toString(),root.resolve(mode+"/state").toString(),mode));
        }catch(Throwable e){failure=e;e.printStackTrace();}
        Map<String,Object> summary=Bridge.map("passed",failure==null,"checked_at",Instant.now().toString(),"cases",results.size(),"assertions",checks,"results",results,"failure",failure==null?null:failure.toString(),"scope","actual Bridge operations, native source and job callbacks, forced journal fault boundaries and fresh JVM historical recovery; no public MCP or hardware claim","hardware_qualified",false);
        Files.writeString(root.resolve("result.json"),JSON.toJson(summary));System.out.println("VACUUM_BRIDGE_SUITE "+JSON.toJson(summary));System.exit(failure==null?0:1);
    }
}
