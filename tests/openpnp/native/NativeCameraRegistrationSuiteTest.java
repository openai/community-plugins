/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.TimeUnit;

/** Canonical single sample-root entrypoint for ten isolated native registration cases.
 * Children inherit the qualified parent's JVM options/classpath; environment injection is removed.
 * Registration/history fixtures are synthetic native model caches, not measured fiducials. */
public final class NativeCameraRegistrationSuiteTest {
 private static final Gson G=new Gson();
 private static final String[] CASES={"geometry:top","geometry:bottom","restore:top","restore:bottom","settling:top","late-invalid:top","transform-exception:top","transform-error:top","restore-transform-exception:top","restore-transform-error:top"};
 private static final int[] EXPECTED={33,33,36,36,17,13,18,18,21,21};
 private static String sha(Path path)throws Exception{byte[] digest=MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path));StringBuilder s=new StringBuilder();for(byte b:digest)s.append(String.format("%02x",b));return s.toString();}
 private static JsonObject object(Object...p){return G.toJsonTree(Bridge.map(p)).getAsJsonObject();}
 private static void require(boolean b,String label){if(!b)throw new AssertionError(label);}
 public static void main(String[] args)throws Exception {
  if(args.length!=1)throw new IllegalArgumentException("Expected native sample-root only");
  Path samples=Path.of(args[0]).toAbsolutePath().normalize();require(Files.isDirectory(samples),"Native sample root must exist");
  Path root=Files.createTempDirectory("native-camera-registration68-suite-");JsonArray rows=new JsonArray();int total=0;boolean passed=true;String failure=null;
  List<String> inherited=new ArrayList<>(ManagementFactory.getRuntimeMXBean().getInputArguments());String cp=System.getProperty("java.class.path");String java=Path.of(System.getProperty("java.home"),"bin","java").toString();
  for(int i=0;i<CASES.length;i++){
   String[] fields=CASES[i].split(":");Path out=root.resolve(CASES[i].replace(':','-'));Files.createDirectory(out);Path fixture=out.resolve("fixture"),log=out.resolve("native.log");
   List<String> command=new ArrayList<>();command.add(java);command.addAll(inherited);command.addAll(Arrays.asList("-cp",cp,"org.openpnp.codex.NativeCameraRegistrationTest",samples.toString(),fixture.toString(),fields[0],fields[1]));Files.writeString(out.resolve("command.json"),G.toJson(command)+"\n",StandardOpenOption.CREATE_NEW);
   Process child=null;int exit=-1;boolean forced=false,interrupted=false,alive=false;long start=System.nanoTime();String error=null;JsonObject result=null;int logRecords=0;
   try{
    ProcessBuilder builder=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile());builder.environment().keySet().removeIf(k->{String upper=k.toUpperCase(Locale.ROOT);return Set.of("JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","_JAVA_OPTIONS","CLASSPATH").contains(upper)||upper.startsWith("LD_")||upper.startsWith("DYLD_");});
    child=builder.start();
    if(!child.waitFor(60,TimeUnit.SECONDS)){forced=true;child.destroyForcibly();require(child.waitFor(10,TimeUnit.SECONDS),"Owned child did not exit after watchdog kill");}
    exit=child.exitValue();alive=child.isAlive();Path resultPath=fixture.resolve("result.json");
    if(Files.isRegularFile(resultPath)){require(Files.size(resultPath)<=4*1024*1024,"Bounded result file");result=new JsonParser().parse(Files.readString(resultPath)).getAsJsonObject();}
    require(Files.size(log)<=4*1024*1024,"Bounded native test log");
    for(String line:Files.readAllLines(log,StandardCharsets.UTF_8))if(line.startsWith("CAMERA_REGISTRATION_RESULT ")){logRecords++;require(result!=null&&new JsonParser().parse(line.substring("CAMERA_REGISTRATION_RESULT ".length())).equals(result),"Native log and result receipt agree");}
   }catch(InterruptedException e){interrupted=true;error=e.toString();}
   catch(Throwable e){error=e.toString();}
   finally{
    if(child!=null&&child.isAlive()){forced=true;child.destroyForcibly();try{if(child.waitFor(10,TimeUnit.SECONDS))exit=child.exitValue();}catch(InterruptedException e){interrupted=true;if(error==null)error=e.toString();}}
    if(child!=null)alive=child.isAlive();
   }
   boolean ok=error==null&&child!=null&&!alive&&!forced&&exit==0&&logRecords==1&&result!=null&&result.get("passed").getAsBoolean()&&fields[0].equals(result.get("mode").getAsString())&&fields[1].equals(result.get("camera").getAsString())&&result.get("checks").getAsInt()==EXPECTED[i]&&result.get("native_enabled_events").getAsInt()==0&&result.get("native_head_activity").getAsInt()==0&&result.get("native_feeds").getAsInt()==0&&result.get("native_placements").getAsInt()==0&&!result.get("hardware_qualified").getAsBoolean();
   if(ok)total+=result.get("checks").getAsInt();
   rows.add(object("case",CASES[i],"passed",ok,"exit_code",exit,"pid",child==null?null:child.pid(),"owned_child_reaped",child!=null&&!alive,"forced_cleanup",forced,"elapsed_seconds",(System.nanoTime()-start)/1e9,"expected_checks",EXPECTED[i],"result",result,"error",error,"log_sha256",Files.isRegularFile(log)?sha(log):null,"result_sha256",Files.isRegularFile(fixture.resolve("result.json"))?sha(fixture.resolve("result.json")):null));
   if(interrupted)Thread.currentThread().interrupt();
   if(!ok){passed=false;failure="Failed case "+CASES[i]+"; retained "+out;break;}
  }
  JsonObject receipt=object("passed",passed&&rows.size()==10&&total==246,"cases",rows,"case_count",rows.size(),"assertions",total,"expected_assertions",246,"evidence_directory",root.toString(),"inherited_jvm_options",inherited,"same_parent_classpath",true,"child_environment_sanitized",true,"failure",failure,"physical_qualification",false,"synthetic_registration_prerequisites",true,"new_native_feeds",0,"new_native_placements",0);
  Files.writeString(root.resolve("receipt.json"),G.toJson(receipt)+"\n",StandardOpenOption.CREATE_NEW);System.out.println("OPENPNP_NATIVE_CAMERA_REGISTRATION_SUITE "+G.toJson(receipt));
  if(!receipt.get("passed").getAsBoolean())throw new AssertionError("Camera registration suite failed; retained "+root);
 }
}
