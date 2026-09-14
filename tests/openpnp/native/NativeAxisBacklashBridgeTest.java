/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.machine.reference.*;
import org.openpnp.machine.reference.axis.ReferenceControllerAxis;

/** Actual Bridge journal/executor/Configuration.save, native home/calibration and simulated moves. */
public final class NativeAxisBacklashBridgeTest {
 static final Gson G=new Gson();static Configuration config;static Machine machine;static Bridge bridge;static String session;static ReferenceControllerAxis axis;static int checks;
 static void check(boolean x,String why){checks++;if(!x)throw new AssertionError(why);}
 static JsonObject obj(Object...a){return G.toJsonTree(Bridge.map(a)).getAsJsonObject();}
 @SuppressWarnings("unchecked")static Map<String,Object> call(String name,JsonObject args)throws Exception{return(Map<String,Object>)bridge.call(name,args);}
 static String revision()throws Exception{return(String)call("openpnp_get_status",obj()).get("config_revision");}
 static JsonObject mutation(Object...a)throws Exception{JsonObject j=obj(a);j.addProperty("session_id",session);j.addProperty("request_id",UUID.randomUUID().toString());j.addProperty("expected_config_revision",revision());return j;}
 static void idle()throws Exception{long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(10);while(machine.isBusy()){if(System.nanoTime()>end)throw new AssertionError("busy deadline");Thread.sleep(5);}}
 static <T>T task(Callable<T> f)throws Exception{T t=machine.submit(f,null,true).get(90,TimeUnit.SECONDS);idle();return t;}
 static Map<String,Object> terminal(String method,JsonObject input,String expected)throws Exception{String id=(String)call(method,input).get("operation_id");long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(60);while(true){Map<String,Object> op=call("openpnp_get_operation",obj("operation_id",id));String s=(String)op.get("state");if(Arrays.asList("succeeded","failed","outcome_unknown","cancelled").contains(s)){check(expected.equals(s),method+": "+op);idle();return op;}if(System.nanoTime()>end)throw new AssertionError("operation deadline");Thread.sleep(5);}}
 static JsonObject change(double offset){return obj("type",NativeAxisBacklashSettings.TYPE,"axis_id",axis.getId(),"method","OneSidedPositioning","offset_mm",offset,"speed_factor",0.25,"sneak_up_mm",0,"acceptable_tolerance_mm",0.025);}
 static Map<String,Object> plan(double offset)throws Exception{JsonArray a=new JsonArray();a.add(change(offset));return call("openpnp_plan_configuration",mutation("changes",a));}
 static Map<?,?> result(Map<String,Object> op){return(Map<?,?>)op.get("result");}
 static void apply(Map<String,Object> p)throws Exception{terminal("openpnp_apply_configuration",mutation("plan_id",p.get("plan_id")),"succeeded");}
 public static void main(String[] args)throws Exception {
  Path root=Files.createTempDirectory("native-backlash-bridge59-");Configuration.initialize(root.resolve("config").toFile());config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);machine=config.getMachine();for(Axis a:machine.getAxes())if(a.getClass()==ReferenceControllerAxis.class&&a.getType()==Axis.Type.X)axis=(ReferenceControllerAxis)a;
  Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());bridge=new Bridge(config,token,root.resolve("journal"),Path.of(args[0]),0,true);session=(String)call("openpnp_request_control_session",obj("request_id","backlash-grant","ttl_seconds",300)).get("session_id");
  try{
   ReferenceNozzle nozzle=(ReferenceNozzle)machine.getDefaultHead().getDefaultNozzle();ReferenceNozzleTip tip=(ReferenceNozzleTip)nozzle.getNozzleTip();
   task(()->{machine.setEnabled(true);machine.home();tip.getCalibration().setEnabled(true);nozzle.calibrate();return null;});check(tip.getCalibration().isCalibrated(nozzle),"actual simulator calibration precedes revocation");
   Map<String,Object> p=plan(0.4);String rev=revision();Map<?,?> rejected=result(terminal("openpnp_apply_configuration",mutation("plan_id",p.get("plan_id")),"failed"));check("MACHINE_NOT_QUIESCENT".equals(rejected.get("code")),"enabled apply refused before mutation");check(rev.equals(revision()),"refusal preserves revision");
   task(()->{machine.setEnabled(false);return null;});
   terminal("openpnp_prepare_job",mutation("sample","pnp-test"),"succeeded");
   Job prepared=(Job)NativeMappedAxisBridgeTest.field(bridge,"job");NativeBoardLoads loads=(NativeBoardLoads)NativeMappedAxisBridgeTest.field(bridge,"boardLoads");
   // Synthetic registration/validation cache only; actual camera alignment is not claimed.
   task(()->{loads.registrationCompleted(prepared,revision());for(PlacementsHolderLocation<?> r:prepared.getRootPanelLocation().getChildren()){r.setLocalToParentTransform(java.awt.geom.AffineTransform.getTranslateInstance(1,2));r.setPlacementsTransformStatus(PlacementsHolderLocation.PlacementsTransformStatus.LocallySet);}return null;});
   NativeMappedAxisBridgeTest.set(bridge,"jobState","validated");NativeMappedAxisBridgeTest.set(bridge,"validatedBoardLoadRevision",loads.revision());
   p=plan(0.4);check(G.toJson(p).contains("retained_native_guard\":true"),"original native plan retained");check(G.toJson(p).contains("controller segments may exceed"),"configuration preview exposes controller envelope semantics");apply(p);
   check(!tip.getCalibration().isCalibrated(nozzle),"successful edit invalidates actual runout calibration");check(!machine.isHomed(),"successful edit invalidates native homing");
   check(NativeMappedAxisBridgeTest.field(bridge,"validatedBoardLoadRevision")==null,"backlash revokes board registration validation");check("prepared".equals(NativeMappedAxisBridgeTest.field(bridge,"jobState")),"backlash revokes job validation");for(PlacementsHolderLocation<?> r:prepared.getRootPanelLocation().getChildren())check(r.getPlacementsTransformStatus()==PlacementsHolderLocation.PlacementsTransformStatus.NotSet,"native board transform revoked");check(Files.readString(root.resolve("config/machine.xml")).contains("OneSidedPositioning"),"native saved XML contains method");
   Map<String,Object> backup=terminal("openpnp_backup_configuration",mutation(),"succeeded");String artifact=(String)result(backup).get("artifact_id");if(artifact==null&&result(backup).get("artifact") instanceof Map)artifact=(String)((Map<?,?>)result(backup).get("artifact")).get("artifact_id");check(artifact!=null,"typed backup artifact returned");
   apply(plan(0.8));terminal("openpnp_restore_configuration",mutation("artifact_id",artifact),"succeeded");check(Math.abs(axis.getBacklashOffset().getValue()-0.4)<1e-8,"Bridge restore restores backlash");
   String journal=Files.readString(root.resolve("journal/operations.jsonl"));check(journal.contains("axis-backlash-configuration-model")&&journal.contains("axis-backlash-restore-model"),"native effect intents recorded");
   p=plan(0.7);task(()->{axis.setBacklashSpeedFactor(0.3);return null;});rev=revision();Map<?,?> stale=result(terminal("openpnp_apply_configuration",mutation("plan_id",p.get("plan_id")),"failed"));check("STALE_AXIS_PLAN".equals(stale.get("code")),"same revision native setting drift is rejected");check(rev.equals(revision()),"stale rejection before revision advance");task(()->{axis.setBacklashSpeedFactor(0.25);return null;});
   Map<String,Object> motion=call("openpnp_plan_motion",mutation("x",10,"y",10,"z",-5,"units","mm","speed",0.1));check(G.toJson(motion).contains("backlash_compensation"),"motion plan exposes active compensation behavior");
   terminal("openpnp_set_machine_enabled",mutation("enabled",true),"succeeded");terminal("openpnp_home_machine",mutation(),"succeeded");
   Map<String,Object> executed=terminal("openpnp_execute_motion",mutation("plan_id",motion.get("plan_id")),"succeeded");check(G.toJson(executed).contains("simulator-standstill"),"actual native simulator motion reaches standstill");
   terminal("openpnp_set_machine_enabled",mutation("enabled",false),"succeeded");
   System.out.println("OPENPNP_NATIVE_BACKLASH_BRIDGE_RESULT "+G.toJson(Bridge.map("checks",checks,"root",root.toString(),"simulation_only",true,"physical_qualification",false,"actual_native_calibration_then_revocation",true,"actual_native_motion",true)));bridge.close();machine.close();System.exit(0);
  }catch(Throwable e){e.printStackTrace();bridge.close();machine.close();System.exit(1);}
 }
}
