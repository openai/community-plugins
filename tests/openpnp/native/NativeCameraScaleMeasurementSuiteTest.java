/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.*;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.machine.reference.camera.ImageCamera;
import org.openpnp.vision.pipeline.stages.DetectCircularSymmetry;

/** Canonical sample-root entrypoint for nine fresh native ImageCamera fixtures.
 * JVM options and classpath are inherited; each child has a separate config/home/tmp.
 * Fixture setup enables/homes/moves the simulator. Helper callbacks are test recorders,
 * not Bridge durability qualification or evidence of physical calibration. */
public final class NativeCameraScaleMeasurementSuiteTest {
 private static final Gson G=new GsonBuilder().serializeNulls().create();
 private static final String PREFIX="CAMERA_SCALE_HELPER_RESULT ";
 private static final String[] MODES={"positive","blank","two-circle","admission","callback-persist","callback-after-move","callback-expire","callback-mutate","callback-after-intent"};
 private static final int[] EXPECTED={148,26,26,85,23,32,25,25,21};
 private static void require(boolean ok,String message){if(!ok)throw new AssertionError(message);}
 private static JsonObject object(Object...p){return G.toJsonTree(Bridge.map(p)).getAsJsonObject();}
 private static String source(Class<?> type){return type.getProtectionDomain().getCodeSource().getLocation().toString();}
 private static String sha(Path path)throws Exception{byte[] bytes=MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path));StringBuilder s=new StringBuilder(64);for(byte b:bytes){s.append(Character.forDigit((b>>>4)&15,16));s.append(Character.forDigit(b&15,16));}return s.toString();}
 private static JsonObject read(Path file)throws Exception{require(Files.isRegularFile(file)&&Files.size(file)<=4*1024*1024,"Missing or oversized result");return new JsonParser().parse(Files.readString(file)).getAsJsonObject();}
 private static void verify(JsonObject r,String mode,int expected,long pid,JsonObject logged){
  require(mode.equals(r.get("mode").getAsString()),"Matching mode");require(r.get("checks").getAsInt()==expected,"Exact frozen assertion count");
  require(r.get("pid").getAsLong()==pid,"Matching owned child PID");require(r.get("exit_code").getAsInt()==0&&r.get("closed").getAsBoolean(),"Native machine closed normally");
  require(r.get("configuration_unchanged").getAsBoolean(),"Model/XML unchanged after fixture setup");require(!r.has("failure")&&!r.has("close_failure"),"No hidden fixture failure");
  require(!r.get("hardware_qualified").getAsBoolean()&&!r.get("production_qualified").getAsBoolean()&&!r.get("native_effect_callback_integration_qualified").getAsBoolean(),"Bounded helper-only scope");
  require(logged.equals(object("mode",mode,"checks",expected,"closed",true,"exit",0)),"Stdout summary matches result");
  JsonObject sources=r.getAsJsonObject("code_sources");
  require(source(NativeCameraScaleMeasurement.class).equals(sources.get("helper").getAsString()),"Exact helper code source");
  require(source(Bridge.class).equals(sources.get("bridge").getAsString())&&source(Bridge.class).equals(source(NativeCameraScaleMeasurement.class)),"Helper and Bridge share selected JAR");
  require(source(ImageCamera.class).equals(sources.get("camera").getAsString())&&source(DetectCircularSymmetry.class).equals(sources.get("detector").getAsString()),"Exact native camera/detector code source");
  require(source(ImageCamera.class).equals(source(DetectCircularSymmetry.class)),"Detector is in selected native JAR, no detector overlay");
  JsonObject setup=r.getAsJsonObject("setup");for(String field:List.of("enable_calls","home_calls","move_calls","prewarm_capture_calls"))require(setup.get(field).getAsInt()==1,"Explicit simulator setup "+field);
 }
 public static void main(String[] args)throws Exception{
  if(args.length!=1)throw new IllegalArgumentException("Expected native sample-root only");
  Path samples=Path.of(args[0]).toAbsolutePath().normalize();require(Files.isDirectory(samples),"Native sample root must exist");
  Path root=Files.createTempDirectory("native-camera-scale-measurement-suite-");JsonArray rows=new JsonArray();int checks=0;boolean passed=true;String failure=null;
  List<String> inherited=new ArrayList<>(ManagementFactory.getRuntimeMXBean().getInputArguments());String cp=System.getProperty("java.class.path");String java=Path.of(System.getProperty("java.home"),"bin","java").toString();
  for(int i=0;i<MODES.length;i++){
   String mode=MODES[i];Path out=root.resolve(mode);Files.createDirectory(out);Path home=Files.createDirectory(out.resolve("home")),tmp=Files.createDirectory(out.resolve("tmp")),fixture=out.resolve("fixture"),log=out.resolve("native.log");
   List<String> command=new ArrayList<>();command.add(java);command.addAll(inherited);command.addAll(Arrays.asList("-Duser.home="+home,"-Djava.io.tmpdir="+tmp,"-cp",cp,"org.openpnp.codex.NativeCameraScaleMeasurementTest",fixture.toString(),mode));
   Files.writeString(out.resolve("command.json"),G.toJson(command)+"\n",StandardOpenOption.CREATE_NEW);
   Process child=null;int exit=-1;boolean forced=false,interrupted=false,alive=false;long start=System.nanoTime();String error=null;JsonObject result=null,logged=null;int logRecords=0;
   try{
    ProcessBuilder builder=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile());
    builder.environment().keySet().removeIf(k->{String upper=k.toUpperCase(Locale.ROOT);return Set.of("JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","_JAVA_OPTIONS","CLASSPATH").contains(upper)||upper.startsWith("LD_")||upper.startsWith("DYLD_");});
    child=builder.start();if(!child.waitFor(60,TimeUnit.SECONDS)){forced=true;child.destroyForcibly();require(child.waitFor(10,TimeUnit.SECONDS),"Owned child failed watchdog cleanup");}
    exit=child.exitValue();require(exit==0&&!forced,"Child must exit normally");result=read(fixture.resolve("result.json"));
    require(Files.size(log)<=4*1024*1024,"Bounded native log");for(String line:Files.readAllLines(log,StandardCharsets.UTF_8))if(line.startsWith(PREFIX)){logRecords++;logged=new JsonParser().parse(line.substring(PREFIX.length())).getAsJsonObject();}
    require(logRecords==1,"Exactly one result marker");verify(result,mode,EXPECTED[i],child.pid(),logged);
   }catch(InterruptedException e){interrupted=true;error=e.toString();}catch(Throwable e){error=e.toString();}
   finally{
    if(child!=null&&child.isAlive()){forced=true;child.destroyForcibly();try{if(child.waitFor(10,TimeUnit.SECONDS))exit=child.exitValue();}catch(InterruptedException e){interrupted=true;if(error==null)error=e.toString();}}
    if(child!=null)alive=child.isAlive();
   }
   boolean ok=error==null&&child!=null&&!alive&&!forced&&exit==0;if(ok)checks+=EXPECTED[i];
   rows.add(object("case",mode,"passed",ok,"expected_checks",EXPECTED[i],"exit_code",exit,"pid",child==null?null:child.pid(),"owned_child_reaped",child!=null&&!alive,"forced_cleanup",forced,"elapsed_seconds",(System.nanoTime()-start)/1e9,"result",result,"error",error,"log_sha256",Files.isRegularFile(log)?sha(log):null,"result_sha256",Files.isRegularFile(fixture.resolve("result.json"))?sha(fixture.resolve("result.json")):null));
   if(interrupted)Thread.currentThread().interrupt();if(!ok){passed=false;failure="Failed case "+mode+"; retained "+out;break;}
  }
  JsonObject receipt=object("passed",passed&&rows.size()==9&&checks==411,"case_count",rows.size(),"assertions",checks,"expected_assertions",411,"cases",rows,"evidence_directory",root.toString(),"sample_root",samples.toString(),"inherited_jvm_options",inherited,"same_parent_classpath",true,"child_environment_sanitized",true,"fresh_child_config_home_tmp",true,"failure",failure,"fixture_setup_enables_homes_and_moves",true,"fixture_setup_excluded_from_helper_recipe_counts",true,"callbacks_are_test_recorders_not_bridge_durable_journal",true,"native_effect_callback_integration_qualified",false,"physical_qualification",false,"production_qualification",false);
  Files.writeString(root.resolve("receipt.json"),G.toJson(receipt)+"\n",StandardOpenOption.CREATE_NEW);System.out.println("OPENPNP_NATIVE_CAMERA_SCALE_MEASUREMENT_SUITE "+G.toJson(receipt));
  if(!receipt.get("passed").getAsBoolean())throw new AssertionError("Camera scale measurement suite failed; retained "+root);
 }
}
