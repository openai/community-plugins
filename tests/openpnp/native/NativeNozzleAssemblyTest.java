/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.lang.management.ManagementFactory;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.*;

/** Real native creation, saved references, bounded refusals and fresh-JVM recovery. */
public final class NativeNozzleAssemblyTest {
 static final Gson G=new Gson();static int checks;static Configuration c;static Machine m;
 interface Run{void run()throws Exception;}
 static void check(boolean v,String why){checks++;if(!v)throw new AssertionError(why);}
 static JsonObject obj(Object...a){return G.toJsonTree(Bridge.map(a)).getAsJsonObject();}
 static JsonArray rows(JsonObject...r){JsonArray a=new JsonArray();for(JsonObject o:r)a.add(o);return a;}
 static void expect(String code,Run action)throws Exception{try{action.run();throw new AssertionError("Expected "+code);}catch(Bridge.Fault e){check(code.equals(e.code),"Expected "+code+" got "+e.code);}}
 static <T>T task(Callable<T> action)throws Exception{return m.submit(action,null,true).get(90,TimeUnit.SECONDS);}
 static JsonObject proposal(Configuration c)throws Exception{
  ReferenceNozzle n=(ReferenceNozzle)c.getMachine().getDefaultHead().getDefaultNozzle();return obj("type",NativeNozzleAssembly.TYPE,"head_id",n.getHead().getId(),"driver_id",c.getMachine().getDrivers().get(0).getId(),"x_axis_id",n.getAxisX().getId(),"y_axis_id",n.getAxisY().getId(),"nozzle_name","Simulator assembly61","tip_name","Assembly61 tip","valve_name","Assembly61 valve","head_offsets",Bridge.map("x_mm",20,"y_mm",0,"z_mm",0),"z_axis",Bridge.map("home_mm",0,"low_mm",-100,"high_mm",10,"safe_z_mm",0,"feedrate_mm_per_s",100,"acceleration_mm_per_s2",500,"jerk_mm_per_s3",1000),"rotation_axis",Bridge.map("home_deg",0,"low_deg",-360,"high_deg",360,"feedrate_deg_per_s",500,"acceleration_deg_per_s2",1000,"jerk_deg_per_s3",10000),"tip",Bridge.map("min_part_diameter_mm",0,"max_part_diameter_mm",20,"max_part_height_mm",10,"max_pick_tolerance_mm",0.5,"pick_dwell_ms",0,"place_dwell_ms",0),"pick_dwell_ms",0,"place_dwell_ms",0,"exclusive_package_ids",List.of("R0805"),"simulated_initial_tool_state","installed-on-new-nozzle");
 }
 static void initialize(Path config)throws Exception{Configuration.initialize(config.toFile());c=Configuration.get();c.load();SimulatorMain.accelerateFixture(c);SimulatorMain.settleFreshFixture(config);c=Configuration.get();m=c.getMachine();}
 static void negatives()throws Exception{
  JsonObject good=proposal(c);String before=NativeNozzleAssembly.snapshot(c).values;int count=m.getAxes().size();
  for(Map.Entry<String,JsonElement> e:good.entrySet()){JsonObject j=G.fromJson(good,JsonObject.class);j.remove(e.getKey());expect("INVALID_ARGUMENT",()->NativeSettings.stage(c,rows(j)));}
  for(String k:List.of("z_axis","rotation_axis","tip","head_offsets")){JsonObject j=G.fromJson(good,JsonObject.class);j.getAsJsonObject(k).addProperty("gcode","G0 X100");expect("UNKNOWN_FIELD",()->NativeSettings.stage(c,rows(j)));}
  JsonObject j=G.fromJson(good,JsonObject.class);j.addProperty("nozzle_name",m.getDefaultHead().getDefaultNozzle().getName());expect("NAME_CONFLICT",()->NativeSettings.stage(c,rows(j)));
  JsonObject wrong=G.fromJson(good,JsonObject.class);wrong.addProperty("x_axis_id",good.get("y_axis_id").getAsString());expect("INVALID_AXIS_REFERENCE",()->NativeSettings.stage(c,rows(wrong)));
  JsonObject missing=G.fromJson(good,JsonObject.class);missing.addProperty("driver_id","absent");expect("INVALID_AXIS_REFERENCE",()->NativeSettings.stage(c,rows(missing)));
  JsonObject geometry=G.fromJson(good,JsonObject.class);geometry.getAsJsonObject("z_axis").addProperty("home_mm",1);expect("INVALID_GEOMETRY",()->NativeSettings.stage(c,rows(geometry)));
  JsonObject angle=G.fromJson(good,JsonObject.class);angle.getAsJsonObject("rotation_axis").addProperty("high_deg",90);expect("INVALID_GEOMETRY",()->NativeSettings.stage(c,rows(angle)));
  JsonObject state=G.fromJson(good,JsonObject.class);state.addProperty("simulated_initial_tool_state","physically-installed");expect("INVALID_ARGUMENT",()->NativeSettings.stage(c,rows(state)));
  expect("INVALID_PATCH",()->NativeSettings.stage(c,rows(good,obj("type","set_machine_speed","speed",0.5))));
  for(int i=0;i<25;i++)NativeSettings.stage(c,rows(good));check(before.equals(NativeNozzleAssembly.snapshot(c).values)&&count==m.getAxes().size(),"staging and all refusals preserve native settings/topology");
  NativeSettings.Patch stale=NativeSettings.stage(c,rows(good));ReferenceNozzle old=(ReferenceNozzle)m.getDefaultHead().getDefaultNozzle();old.setPickDwellMilliseconds(old.getPickDwellMilliseconds()+1);expect("STALE_TOPOLOGY_PLAN",stale::apply);old.setPickDwellMilliseconds(old.getPickDwellMilliseconds()-1);
  NativeSettings.Patch angular=NativeSettings.stage(c,rows(good));ReferenceControllerAxis rotation=(ReferenceControllerAxis)old.getAxisRotation();Length prior=rotation.getSoftLimitHigh();rotation.setSoftLimitHigh(new Length(200,LengthUnit.Millimeters));expect("STALE_TOPOLOGY_PLAN",angular::apply);rotation.setSoftLimitHigh(prior);
  NativeSettings.Patch policy=NativeSettings.stage(c,rows(good));ReferenceActuator valve=(ReferenceActuator)old.getVacuumActuator();ReferenceActuator.MachineStateActuation priorPolicy=valve.getHomedActuation();valve.setHomedActuation(ReferenceActuator.MachineStateActuation.ActuateOn);expect("STALE_TOPOLOGY_PLAN",policy::apply);valve.setHomedActuation(priorPolicy);
  JsonObject aliases=G.fromJson(good,JsonObject.class);aliases.add("exclusive_package_ids",G.toJsonTree(List.of("R0805","r0805")));expect("INVALID_ARGUMENT",()->NativeSettings.stage(c,rows(aliases)));
  NativeSettings.Patch enabled=NativeSettings.stage(c,rows(good));m.setEnabled(true);expect("MACHINE_NOT_QUIESCENT",enabled::apply);m.setEnabled(false);
 }
 static void create(Path root)throws Exception{
  initialize(root.resolve("config"));task(()->{negatives();JsonObject proposal=proposal(c);Files.writeString(root.resolve("proposal.json"),G.toJson(proposal));NativePortableConfiguration.export(c,root.resolve("preimage.zip"));
   Nozzle original=m.getDefaultHead().getDefaultNozzle();String oldId=original.getId();int axes=m.getAxes().size(),tips=m.getNozzleTips().size();NativeSettings.Patch plan=NativeSettings.stage(c,rows(proposal));plan.apply();Map<String,Object> result=plan.result();check(!result.isEmpty(),"actual generated identity receipt");check(m.getDefaultHead().getDefaultNozzle()==original,"default nozzle order preserved");check(m.getAxes().size()==axes+2&&m.getNozzleTips().size()==tips+1&&m.getDefaultHead().getNozzles().size()==2,"all dependency objects installed");
   NativeNozzleAssembly.verify(c,result,proposal);expect("PATCH_ALREADY_APPLIED",plan::apply);c.save();Files.writeString(root.resolve("created.json"),G.toJson(result));Files.writeString(root.resolve("old-nozzle-id"),oldId);NativePortableConfiguration.export(c,root.resolve("created.zip"));check(!m.isEnabled()&&!m.isHomed(),"creation grants no execution authority");return null;});m.close();
 }
 static void reload(Path root,boolean adopted)throws Exception{
  Path config=adopted?NativePortableConfiguration.activeConfiguration(root.resolve("adopted")):root.resolve("config");Configuration.initialize(config.toFile());c=Configuration.get();c.load();m=c.getMachine();
  Map<String,Object> result=G.fromJson(Files.readString(root.resolve("created.json")),Map.class);JsonObject proposal=G.fromJson(Files.readString(root.resolve("proposal.json")),JsonObject.class);NativeNozzleAssembly.verify(c,result,proposal);check(m.getDefaultHead().getDefaultNozzle().getId().equals(Files.readString(root.resolve("old-nozzle-id"))),"original default nozzle survived fresh native XML load");check(!m.isHomed()&&!m.isEnabled(),"fresh load does not restore execution state");
  if(adopted){task(()->{m.setEnabled(true);m.home();ReferenceNozzle n=(ReferenceNozzle)m.getDefaultHead().getNozzle((String)result.get("nozzle_id"));n.moveTo(new Location(LengthUnit.Millimeters,25,10,-2,45),0.1);m.getMotionPlanner().waitForCompletion(null,MotionPlanner.CompletionType.WaitForStillstand);check(n.getAxisZ()!=((ReferenceNozzle)m.getDefaultHead().getDefaultNozzle()).getAxisZ(),"new independent Z axis used after adoption");m.setEnabled(false);return null;});}
  m.close();
 }
 static void fault(Path root,String stage)throws Exception{Path dir=root.resolve("fault-"+stage);initialize(dir.resolve("config"));task(()->{NativePortableConfiguration.export(c,dir.resolve("preimage.zip"));int before=m.getAxes().size();NativeNozzleAssembly.Plan p=NativeNozzleAssembly.stage(c,proposal(c),point->{if(stage.equals(point))throw new java.io.IOException("injected after "+point);});try{p.apply();throw new AssertionError("probe not reached");}catch(java.io.IOException expected){checks++;}expect("PATCH_ALREADY_APPLIED",p::apply);check(Files.size(dir.resolve("preimage.zip"))>0,"recovery preimage retained after partial setter failure");check(m.getAxes().size()>=before,"failure did not attempt unsafe listener rollback");c.save();return null;});m.close();}
 static void adopt(Path bundle,Path destination)throws Exception{NativePortableConfiguration.Prepared p=NativePortableConfiguration.prepare(bundle,NativePortableConfiguration.sha(Files.readAllBytes(bundle)),destination);NativePortableConfiguration.validateAndPublish(p);}
 static void recovery(Path root)throws Exception{Path config=NativePortableConfiguration.activeConfiguration(root.resolve("recovered"));Configuration.initialize(config.toFile());c=Configuration.get();c.load();m=c.getMachine();check(m.getDefaultHead().getNozzles().size()==1,"fresh recovery restores pre-creation graph");check(!m.isHomed()&&!m.isEnabled(),"fresh recovery transfers no execution state");check(m.getDefaultHead().getNozzles().stream().noneMatch(n->n.getName().equals("Simulator assembly61")),"partial created nozzle is not replayed");m.close();}
 public static void main(String[] args)throws Exception{
  if(args.length==1){Path root=Files.createTempDirectory("native-topology61-");List<String> phases=new ArrayList<>(List.of("create","reload","adopt","verify-adopted"));for(String s:List.of("before-insertion","z-inserted","rotation-inserted","tip-inserted","valve-inserted","nozzle-inserted","compatibility-assigned","verified"))phases.add("fault:"+s);phases.addAll(List.of("adopt-recovery","verify-recovery"));
   for(String phase:phases){List<String> command=new ArrayList<>();command.add(Path.of(System.getProperty("java.home"),"bin/java").toString());command.addAll(ManagementFactory.getRuntimeMXBean().getInputArguments());command.addAll(List.of("-cp",System.getProperty("java.class.path"),NativeNozzleAssemblyTest.class.getName(),phase,root.toString()));Path log=root.resolve(phase.replace(':','-')+".log");Process child=new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile()).start();check(child.waitFor(90,TimeUnit.SECONDS)&&child.exitValue()==0,"phase "+phase+": "+Files.readString(log));}
   System.out.println("OPENPNP_NATIVE_TOPOLOGY_RESULT "+G.toJson(Bridge.map("phases",phases.size(),"root",root.toString(),"simulation_only",true,"physical_qualification",false)));return;
  }
  String mode=args[0];Path root=Path.of(args[1]);try{if(mode.equals("create"))create(root);else if(mode.equals("reload"))reload(root,false);else if(mode.equals("adopt"))adopt(root.resolve("created.zip"),root.resolve("adopted"));else if(mode.equals("verify-adopted"))reload(root,true);else if(mode.startsWith("fault:"))fault(root,mode.substring(6));else if(mode.equals("adopt-recovery"))adopt(root.resolve("fault-nozzle-inserted/preimage.zip"),root.resolve("recovered"));else if(mode.equals("verify-recovery"))recovery(root);else throw new AssertionError(mode);System.out.println("OPENPNP_TOPOLOGY_PHASE "+G.toJson(Bridge.map("phase",mode,"checks",checks)));System.exit(0);}catch(Throwable e){e.printStackTrace();System.exit(1);}
 }
}
