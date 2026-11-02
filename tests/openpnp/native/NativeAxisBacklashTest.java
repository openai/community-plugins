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
import org.openpnp.machine.reference.driver.*;
import org.openpnp.machine.reference.camera.ReferenceCamera;

/** Real native objects/planner algorithm plus fresh Configuration.load JVM. No physical hardware. */
public final class NativeAxisBacklashTest {
 static final Gson G=new Gson();static Configuration config;static Machine machine;static ReferenceControllerAxis axis;static HeadMountable tool;static int checks;
 interface Run{void run()throws Exception;}
 static void check(boolean x,String why){checks++;if(!x)throw new AssertionError(why);}
 static void near(double a,double b,String why){check(Double.isFinite(a)&&Math.abs(a-b)<1e-7,why+": "+a+" != "+b);}
 static JsonObject obj(Object...args){return G.toJsonTree(Bridge.map(args)).getAsJsonObject();}
 static JsonArray rows(JsonObject...a){JsonArray r=new JsonArray();for(JsonObject x:a)r.add(x);return r;}
 static JsonObject change(String method,double offset,double speed,double sneak){return obj("type",NativeAxisBacklashSettings.TYPE,"axis_id",axis.getId(),"method",method,"offset_mm",offset,"speed_factor",speed,"sneak_up_mm",sneak,"acceptable_tolerance_mm",0.025);}
 static void expect(String code,Run r)throws Exception{try{r.run();throw new AssertionError("expected "+code);}catch(Bridge.Fault e){check(code.equals(e.code),"expected "+code+" got "+e.code);}}
 static <T>T task(Callable<T> f)throws Exception{return machine.submit(f,null,true).get(90,TimeUnit.SECONDS);}
 static double mm(Length l){return l.convertToUnits(LengthUnit.Millimeters).getValue();}
 // Test-only observer subclass calls the real inherited compensation and addMotion algorithm.
 // It is never installed as the native machine planner or accepted by production guards.
 static final class Recorder extends NullMotionPlanner {
  final List<Double> speeds=new ArrayList<>();
  @Override protected Motion addMotion(HeadMountable h,double speed,AxesLocation a,AxesLocation b,int options){speeds.add(speed);return super.addMotion(h,speed,a,b,options);}
  List<Motion> plan(double from,double to,double speed){motionCommands.clear();speeds.clear();createBacklashCompensatedMotion(tool,speed,new AxesLocation(axis,from),new AxesLocation(axis,to));return new ArrayList<>(motionCommands);}
 }
 static void set(String method,double offset,double speed,double sneak)throws Exception{NativeSettings.stage(config,rows(change(method,offset,speed,sneak))).apply();}
 static void planner()throws Exception {
  for(String method:Arrays.asList("None","OneSidedPositioning","OneSidedOptimizedPositioning","DirectionalCompensation","DirectionalSneakUp")){
   set(method,0.5,0.25,1);Recorder p=new Recorder();List<Motion> r=p.plan(0,10,0.8);
   boolean extra=method.contains("Positioning")||method.equals("DirectionalSneakUp");check(r.size()==(extra?2:1),method+" actual segment count");
   near(r.get(0).getLocation0().getCoordinate(axis),0,"start");
   double first=method.equals("DirectionalSneakUp")?9.5:method.equals("None")?10:10.5;
   near(r.get(0).getLocation1().getCoordinate(axis),first,method+" first endpoint");
   double end=method.startsWith("Directional")?10.5:10;near(r.get(r.size()-1).getLocation1().getCoordinate(axis),end,method+" final controller endpoint");
   if(extra){near(p.speeds.get(1),0.25,"native slow speed uses minimum");Recorder slow=new Recorder();slow.plan(0,10,0.1);near(slow.speeds.get(1),0.1,"already slower request is not multiplied");}
   Recorder reverse=new Recorder();List<Motion> back=reverse.plan(10,0,0.8);if(method.equals("OneSidedOptimizedPositioning")||method.equals("DirectionalCompensation"))check(back.size()==1,"optimized/directional opposite travel has one segment");near(back.get(back.size()-1).getLocation1().getCoordinate(axis),0,"opposite travel has no directional endpoint offset");
  }
  set("DirectionalCompensation",-0.5,0.25,0);Recorder signed=new Recorder();near(signed.plan(10,0,0.8).get(0).getLocation1().getCoordinate(axis),-0.5,"negative offset direction is honored");near(signed.plan(0,10,0.8).get(0).getLocation0().getCoordinate(axis),-0.5,"last directional offset carries to next native start");
  set("DirectionalSneakUp",0.5,0.25,1);Recorder shortMove=new Recorder();shortMove.plan(0,10,0.8);List<Motion> small=shortMove.plan(10,10.1,0.8);near(small.get(0).getLocation1().getCoordinate(axis),10.5,"native short same-direction sneak stays at compensated start");
  axis.setSoftLimitLow(new Length(-20,LengthUnit.Millimeters));axis.setSoftLimitHigh(new Length(20,LengthUnit.Millimeters));axis.setSoftLimitLowEnabled(true);axis.setSoftLimitHighEnabled(true);
  check(machine.getMotionPlanner().isValidLocation(tool,new AxesLocation(axis,20)),"logical endpoint at enabled soft limit accepted");check(!machine.getMotionPlanner().isValidLocation(tool,new AxesLocation(axis,20.01)),"logical endpoint outside soft limit rejected");
  set("OneSidedPositioning",0.5,0.25,0);Recorder upper=new Recorder();near(upper.plan(19,20,0.8).get(0).getLocation1().getCoordinate(axis),20.5,"native controller overshoot exceeds logical upper limit");
  set("OneSidedPositioning",-0.5,0.25,0);Recorder lower=new Recorder();near(lower.plan(-19,-20,0.8).get(0).getLocation1().getCoordinate(axis),-20.5,"native controller overshoot exceeds logical lower limit");
  Map<String,Object> view=NativeAxisBacklashSettings.describe(config,axis);near(((Number)view.get("maximum_extra_distance_from_logical_segment_mm")).doubleValue(),0.5,"preview excursion reflects signed offset magnitude");check(view.get("soft_limits_bound").toString().contains("may exceed"),"readback does not claim physical envelope");
  // Parameter bounds are a software resource/travel profile, independent of measured lash.
  set("DirectionalSneakUp",10,0.001,10);near(((Number)NativeAxisBacklashSettings.describe(config,axis).get("maximum_extra_distance_from_logical_segment_mm")).doubleValue(),20,"max admitted conservative excursion");
 }
 static void negatives()throws Exception {
  JsonObject good=change("DirectionalCompensation",0.5,0.25,0);double original=mm(axis.getBacklashOffset());
  for(String key:new ArrayList<>(good.entrySet().stream().map(Map.Entry::getKey).collect(java.util.stream.Collectors.toList()))){JsonObject c=new JsonParser().parse(good.toString()).getAsJsonObject();c.remove(key);expect("INVALID_ARGUMENT",()->NativeSettings.stage(config,rows(c)));}
  JsonObject unknown=change("Bogus",0.5,0.25,0);expect("INVALID_ARGUMENT",()->NativeSettings.stage(config,rows(unknown)));
  for(String key:Arrays.asList("offset_mm","speed_factor","sneak_up_mm","acceptable_tolerance_mm")){JsonObject c=new JsonParser().parse(good.toString()).getAsJsonObject();c.addProperty(key,"0.1");expect("INVALID_ARGUMENT",()->NativeSettings.stage(config,rows(c)));c.addProperty(key,Double.NaN);expect("OUT_OF_RANGE",()->NativeSettings.stage(config,rows(c)));}
  for(JsonObject c:Arrays.asList(change("None",10.01,0.2,0),change("None",0,0,0),change("None",0,1.01,0),change("None",0,0.2,-1),change("None",0,0.2,10.01)))expect("OUT_OF_RANGE",()->NativeSettings.stage(config,rows(c)));
  expect("INVALID_GEOMETRY",()->NativeSettings.stage(config,rows(change("DirectionalCompensation",0,0.25,0))));expect("INVALID_GEOMETRY",()->NativeSettings.stage(config,rows(change("DirectionalSneakUp",0.1,0.25,0))));
  JsonObject extra=change("None",0,0.25,0);extra.addProperty("gcode","G0 X10");expect("UNKNOWN_FIELD",()->NativeSettings.stage(config,rows(extra)));
  JsonObject missing=change("None",0,0.25,0);missing.addProperty("axis_id","absent");expect("AXIS_NOT_FOUND",()->NativeSettings.stage(config,rows(missing)));
  expect("INVALID_PATCH",()->NativeSettings.stage(config,rows(good,good)));near(mm(axis.getBacklashOffset()),original,"late invalid stage never mutates");
  Axis.Type oldType=axis.getType();axis.setType(Axis.Type.Rotation);expect("AXIS_TYPE_MISMATCH",()->NativeSettings.stage(config,rows(good)));axis.setType(oldType);axis.setInvertLinearRotational(true);expect("AXIS_TYPE_MISMATCH",()->NativeSettings.stage(config,rows(good)));axis.setInvertLinearRotational(false);
  Driver driver=axis.getDriver();axis.setDriver(new NullDriver());expect("INVALID_AXIS_REFERENCE",()->NativeSettings.stage(config,rows(good)));axis.setDriver(driver);
  ReferenceMappedAxis mapped=new ReferenceMappedAxis();mapped.setType(axis.getType());mapped.setInputAxis(axis);machine.addAxis(mapped);expect("MAPPED_SOURCE_BACKLASH_UNSUPPORTED",()->NativeSettings.stage(config,rows(good)));machine.removeAxis(mapped);
  NativeSettings.Patch stale=NativeSettings.stage(config,rows(good));axis.setBacklashSpeedFactor(0.3);expect("STALE_AXIS_PLAN",stale::apply);axis.setBacklashSpeedFactor(0.25);
  NativeSettings.Patch enabled=NativeSettings.stage(config,rows(good));machine.setEnabled(true);expect("MACHINE_NOT_QUIESCENT",enabled::apply);machine.setEnabled(false);
  NativeSettings.Patch camera=NativeSettings.stage(config,rows(good));ReferenceCamera c=(ReferenceCamera)machine.getDefaultHead().getDefaultCamera();c.setEnableUnitsPerPixel3D(true);expect("UNSUPPORTED_CAMERA_GEOMETRY",camera::apply);c.setEnableUnitsPerPixel3D(false);
 }
 static void previewQualification()throws Exception {
  set("DirectionalCompensation",0.5,0.25,0);Axis.Type old=axis.getType();
  axis.setType(Axis.Type.Rotation);previewFault("AXIS_TYPE_MISMATCH");axis.setType(old);
  axis.setInvertLinearRotational(true);previewFault("AXIS_TYPE_MISMATCH");axis.setInvertLinearRotational(false);
  ReferenceMappedAxis map=new ReferenceMappedAxis();map.setType(old);map.setInputAxis(axis);machine.addAxis(map);previewFault("MAPPED_SOURCE_BACKLASH_UNSUPPORTED");machine.removeAxis(map);
  axis.setBacklashOffset(new Length(Double.NaN,LengthUnit.Millimeters));previewFault("INVALID_NATIVE_BACKLASH");axis.setBacklashOffset(new Length(0.5,LengthUnit.Millimeters));
  axis.setBacklashCompensationMethod(ReferenceControllerAxis.BacklashCompensationMethod.DirectionalSneakUp);axis.setSneakUpOffset(new Length(-1,LengthUnit.Millimeters));previewFault("INVALID_NATIVE_BACKLASH");axis.setSneakUpOffset(new Length(1,LengthUnit.Millimeters));
 }
 static void previewFault(String expected){Map<String,Object> row=NativeAxisBacklashSettings.motionBehavior(config).stream().filter(r->axis.getId().equals(r.get("axis_id"))).findFirst().get();check(Boolean.FALSE.equals(row.get("backlash_settings_editable")),"unsupported native preview is read-only");check(Boolean.FALSE.equals(row.get("distance_bound_available")),"unsupported preview grants no distance bound");check(!row.containsKey("offset_mm")&&!row.containsKey("maximum_extra_distance_from_logical_segment_mm"),"unsupported preview never labels native angular/unknown values as mm");check(expected.equals(row.get("preview_fault")),"unsupported preview reason "+expected);}
 static void snapshotsAndInvalidation(Path root)throws Exception {
  set("DirectionalSneakUp",0.4,0.2,0.8);NativeConfigurationSnapshots.Snapshot snap=NativeConfigurationSnapshots.capture(config);
  check(G.toJson(snap.document).contains(NativeAxisBacklashSettings.TYPE),"backlash settings are represented in typed backup");
  set("None",0,0.25,0);NativeConfigurationSnapshots.Restore restore=NativeConfigurationSnapshots.prepareRestore(config,NativeConfigurationSnapshots.decode(G.toJsonTree(snap.document).getAsJsonObject()));check(restore.invalidatesAxisDependencies(),"restore advertises invalidation");restore.apply();near(mm(axis.getBacklashOffset()),0.4,"snapshot restores signed offset");near(mm(axis.getSneakUpOffset()),0.8,"snapshot restores sneak distance");near(axis.getBacklashSpeedFactor(),0.2,"snapshot restores speed factor");near(mm(axis.getAcceptableTolerance()),0.025,"snapshot restores tolerance");check(axis.getBacklashCompensationMethod()==ReferenceControllerAxis.BacklashCompensationMethod.DirectionalSneakUp,"snapshot restores method");check(!machine.isHomed(),"snapshot never restores homed state");
  config.save();Files.writeString(root.resolve("axis-id"),axis.getId());
 }
 public static void main(String[] args)throws Exception {
  if(args.length>0&&args[0].equals("reload")){Configuration.initialize(Path.of(args[1]).toFile());Configuration c=Configuration.get();c.load();ReferenceControllerAxis a=(ReferenceControllerAxis)c.getMachine().getAxis(Files.readString(Path.of(args[1],"axis-id")));near(mm(a.getBacklashOffset()),0.4,"fresh JVM native reload offset");near(mm(a.getSneakUpOffset()),0.8,"fresh JVM native reload sneak");near(a.getBacklashSpeedFactor(),0.2,"fresh JVM native reload speed");check(a.getBacklashCompensationMethod()==ReferenceControllerAxis.BacklashCompensationMethod.DirectionalSneakUp,"fresh JVM reload method");check(!c.getMachine().isHomed()&&!c.getMachine().isEnabled(),"reload grants no execution state");c.getMachine().close();System.out.println("BACKLASH_RELOAD_PASS");System.exit(0);}
  Path root=Files.createTempDirectory("native-backlash59-");Configuration.initialize(root.toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);machine=config.getMachine();tool=machine.getDefaultHead().getDefaultNozzle();for(Axis a:machine.getAxes())if(a.getType()==Axis.Type.X&&a.getClass()==ReferenceControllerAxis.class)axis=(ReferenceControllerAxis)a;
  try{task(()->{planner();negatives();previewQualification();snapshotsAndInvalidation(root);return null;});machine.close();List<String> command=new ArrayList<>();command.add(Path.of(System.getProperty("java.home"),"bin/java").toString());command.addAll(ManagementFactory.getRuntimeMXBean().getInputArguments());command.addAll(Arrays.asList("-cp",System.getProperty("java.class.path"),NativeAxisBacklashTest.class.getName(),"reload",root.toString()));Process p=new ProcessBuilder(command).inheritIO().start();check(p.waitFor(60,TimeUnit.SECONDS)&&p.exitValue()==0,"fresh JVM Configuration.load");System.out.println("OPENPNP_NATIVE_BACKLASH_RESULT "+G.toJson(Bridge.map("checks",checks,"simulation_only",true,"physical_qualification",false,"root",root.toString())));System.exit(0);}catch(Throwable e){e.printStackTrace();machine.close();System.exit(1);}
 }
}
