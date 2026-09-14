/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.openpnp.model.*;
import org.openpnp.spi.Machine;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;

/** Exercise actual native processor stepping, supervision and durable fault fencing. */
public final class NativeJobSteppingTest {
 static final Gson G=new Gson(); static final List<String> passed=new ArrayList<>();
 static Bridge bridge; static String session; static Machine machine;
 @SuppressWarnings("unchecked") static Map<String,Object> call(String name,JsonObject args)throws Exception{return (Map<String,Object>)bridge.call("openpnp_"+name,args);}
 static Map<String,Object> read(String name,Object... values)throws Exception{return call(name,json(values));}
 static JsonObject json(Object... values){return G.toJsonTree(Bridge.map(values)).getAsJsonObject();}
 static JsonObject mutation(Object... values)throws Exception{JsonObject args=json("request_id",UUID.randomUUID().toString(),"session_id",session,"expected_config_revision",read("get_configuration").get("config_revision"));for(Map.Entry<String,JsonElement> e:json(values).entrySet())args.add(e.getKey(),e.getValue());return args;}
 static void check(boolean value,String label){if(!value)throw new AssertionError(label);passed.add(label);System.out.println("PASS "+label);}
 static long steps(Map<String,Object> op){return ((Number)op.getOrDefault("native_steps_started",0)).longValue();}
 @SuppressWarnings("unchecked") static Map<String,Object> result(Map<String,Object> op){return (Map<String,Object>)op.get("result");}
 static Map<String,Object> await(Map<String,Object> op)throws Exception{String id=(String)op.get("operation_id");long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(30);while(true){op=read("get_operation","operation_id",id);String state=(String)op.get("state");if(!Arrays.asList("accepted","running").contains(state)&&!machine.isBusy())return op;if(System.nanoTime()>end)throw new AssertionError("No terminal boundary for "+id);Thread.sleep(5);}}
 static Map<String,Object> succeed(String name,JsonObject args)throws Exception{Map<String,Object> op=await(call(name,args));check("succeeded".equals(op.get("state")),name+" succeeded");return result(op);}
 static void expect(String expected,String name,JsonObject args)throws Exception{try{call(name,args);}catch(Bridge.Fault e){check(expected.equals(e.code),"native refusal "+expected+" actual="+e.code);return;}throw new AssertionError("Expected "+expected);}
 static JsonObject canonical(Part part){double height=part.getHeight().convertToUnits(LengthUnit.Millimeters).getValue();return json("schemaVersion",1,"id","step-prototype","units","mm","coordinateConvention","openpnp-top-view",
 "parts",List.of(Bridge.map("id",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height)),
 "boards",List.of(Bridge.map("id","board","widthMm",20,"heightMm",20,"placements",List.of(Bridge.map("ref","R1","partId",part.getId(),"packageId",part.getPackage().getId(),"heightMm",height,"x",10,"y",10,"z",0,"rotation",0,"side","top","enabled",true,"type","placement")))),
 "panels",List.of(),"instances",List.of(Bridge.map("id","board-1","kind","board","definitionId","board","x",0,"y",0,"z",0,"rotation",0,"side","top","enabled",true)));}
 public static void main(String[] args)throws Exception{
  Path root=args.length>1?Paths.get(args[1]):Files.createTempDirectory("openpnp-native-job-stepping-");Files.createDirectories(root.resolve("config"));Path token=root.resolve("token");Files.writeString(token,UUID.randomUUID().toString()+UUID.randomUUID());
  Configuration.initialize(root.resolve("config").toFile());Configuration config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);SimulatorMain.configureSustainedWorkload(config);machine=config.getMachine();
  int exit=0;try{
   bridge=new Bridge(config,token,root.resolve("journal"),Paths.get(args[0]),0,true,"sustained-workload");
   session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
   check(((List<?>)read("get_capabilities").get("tools")).contains("openpnp_step_job"),"prototype native capability lists step");
   Part part=config.getPart("R0603-1K");ReferenceTrayFeeder feeder=(ReferenceTrayFeeder)machine.getFeeders().stream().filter(f->f.isEnabled()&&f.getPart()==part).findFirst().orElseThrow();
   check(feeder.getFeedCount()==0,"fresh finite tray");
   Map<String,Object> unprepared=await(call("step_job",mutation("job_id","absent")));
   check("failed".equals(unprepared.get("state"))&&"JOB_NOT_VALIDATED".equals(result(unprepared).get("code")),"step cannot prepare or validate an absent job");
   succeed("set_machine_enabled",mutation("enabled",true));succeed("home_machine",mutation());
   String job=(String)succeed("prepare_job",mutation("canonical_job",canonical(part))).get("job_id");
   check(Boolean.TRUE.equals(succeed("validate_job",mutation()).get("valid")),"actual one-placement job validates");
   succeed("set_machine_enabled",mutation("enabled",false));
   long sequence=((Number)read("get_status").get("through_sequence")).longValue();
   expect("MACHINE_DISABLED","step_job",mutation("job_id",job));
   check(sequence==((Number)read("get_status").get("through_sequence")).longValue(),"disabled step rejected before journal admission");
   succeed("set_machine_enabled",mutation("enabled",true));if(!machine.isHomed())succeed("home_machine",mutation());
   JsonObject first=mutation("job_id",job);Map<String,Object> accepted=call("step_job",first);String id=(String)accepted.get("operation_id");
   check(id.equals(call("step_job",first).get("operation_id")),"in-flight first step request deduplicates");
   Map<String,Object> op=await(accepted);check("paused".equals(op.get("state"))&&steps(op)==1,"first step advances exactly one native next() then pauses");
   check("single-native-step-completed".equals(result(op).get("pause_reason")),"explicit step pause reason");
   check(Boolean.TRUE.equals(((Map<?,?>)result(op).get("native_step_boundary")).get("standstill_confirmed")),"single-step pause waits for native standstill");
   check(steps(call("step_job",first))==1,"completed first-step retry does not advance");
   expect("UNKNOWN_FIELD","step_job",mutation("operation_id",id,"job_id",job));
   expect("NOT_FOUND","step_job",mutation("operation_id","wrong-operation"));
   expect("REVISION_CONFLICT","step_job",mutation("operation_id",id,"expected_config_revision","cfg-999999"));
   check(steps(read("get_operation","operation_id",id))==1,"rejected steps preserve native step count");
   read("renew_control_session","session_id",session,"ttl_seconds",1);Thread.sleep(1200);
   expect("SESSION_REQUIRED","step_job",mutation("operation_id",id));
   check(steps(read("get_operation","operation_id",id))==1&&feeder.getFeedCount()==0,"expired lease cannot advance paused step or consume feed");
   session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
   long previous=1;int paused=1;
   while("paused".equals(op.get("state"))){
    JsonObject command=mutation("operation_id",id);op=await(call("step_job",command));
    check(id.equals(op.get("operation_id"))&&steps(op)==previous+1,"step retains original operation and advances once "+(previous+1));
    check(steps(call("step_job",command))==steps(op),"step receipt replay does not advance "+steps(op));
    if("paused".equals(op.get("state"))){paused++;check("single-native-step-completed".equals(result(op).get("pause_reason")),"step pause reason persists "+steps(op));}
    previous=steps(op);if(previous>100)throw new AssertionError("Native one-part job exceeded bounded step fixture");
   }
   check("succeeded".equals(op.get("state")),"single-stepped native job terminates");
   check(((Number)result(op).get("placed")).intValue()==1&&feeder.getFeedCount()==1,"single stepping places and consumes exactly one part");
   check(((Number)result(op).get("independently_inspected")).intValue()==0,"native placement is not inspection");
   expect("NOT_FOUND","step_job",mutation("operation_id",id));
   String nextJob=(String)succeed("prepare_job",mutation("canonical_job",canonical(part))).get("job_id");
   succeed("validate_job",mutation());op=await(call("step_job",mutation("job_id",nextJob)));String next=(String)op.get("operation_id");
   check("paused".equals(op.get("state"))&&steps(op)==1,"second job single-step initial boundary");
   op=await(call("resume_job",mutation("operation_id",next)));
   check("succeeded".equals(op.get("state"))&&steps(op)>1&&next.equals(op.get("operation_id")),"ordinary resume continues stepped operation to completion");
   check(feeder.getFeedCount()==2&&((Number)result(op).get("placed")).intValue()==1,"resume has one additional feed and placement");
   String abortJob=(String)succeed("prepare_job",mutation("canonical_job",canonical(part))).get("job_id");succeed("validate_job",mutation());
   op=await(call("step_job",mutation("job_id",abortJob)));String abortId=(String)op.get("operation_id");
   while(machine.getHeads().stream().flatMap(h->h.getNozzles().stream()).noneMatch(n->n.getPart()!=null)){
    check("paused".equals(op.get("state"))&&steps(op)<50,"bounded stepping before held-part abort");
    op=await(call("step_job",mutation("operation_id",abortId)));
   }
   check(feeder.getFeedCount()==3,"held-part abort fixture consumed one actual additional feed");
   JsonObject abort=mutation("operation_id",abortId);op=await(call("abort_job",abort));
   check("aborted".equals(op.get("state"))&&((Number)result(op).get("placed")).intValue()==0,"native abort after a stepped pickup does not mark placement complete");
   check(machine.getHeads().stream().flatMap(h->h.getNozzles().stream()).allMatch(n->n.getPart()==null),"native abort clears held simulated part");
   check("aborted".equals(call("abort_job",abort).get("state"))&&feeder.getFeedCount()==3,"repeated abort receipt adds no feed or cleanup replay");
   String faultJob=(String)succeed("prepare_job",mutation("canonical_job",canonical(part))).get("job_id");succeed("validate_job",mutation());
   op=await(call("step_job",mutation("job_id",faultJob)));String faultId=(String)op.get("operation_id");
   check("paused".equals(op.get("state"))&&steps(op)==1,"journal-failure fixture stops at first native boundary");
   java.lang.reflect.Field channel=Bridge.class.getDeclaredField("journal");channel.setAccessible(true);((java.nio.channels.FileChannel)channel.get(bridge)).close();
   boolean writeFailed=false;try{call("step_job",mutation("operation_id",faultId));}catch(java.io.IOException expected){writeFailed=true;}
   check(writeFailed&&steps(read("get_operation","operation_id",faultId))==1&&feeder.getFeedCount()==3,"failed command-intent append prevents the next native step");
   String oldInstance=(String)read("get_capabilities").get("bridge_instance_id");
   // This fixture deliberately closed the channel behind its lock. Preserve the
   // expected close error without mistaking it for automatic recovery or cleanup.
   try{bridge.close();}catch(java.nio.channels.ClosedChannelException expected){check(true,"closed injected journal lock reports expected close failure");}bridge=null;
   bridge=new Bridge(config,token,root.resolve("journal"),Paths.get(args[0]),0,true,"sustained-workload");
   check(!oldInstance.equals(read("get_capabilities").get("bridge_instance_id")),"fresh Bridge instance after stopped stepped job");
   op=read("get_operation","operation_id",faultId);check("outcome_unknown".equals(op.get("state")),"paused stepped operation recovers as unknown, not replayable");
   session=(String)read("request_control_session","request_id",UUID.randomUUID().toString(),"ttl_seconds",300).get("session_id");
   expect("RECOVERY_REQUIRED","step_job",mutation("job_id",faultJob));
   expect("NOT_FOUND","step_job",mutation("operation_id",faultId));
   check(feeder.getFeedCount()==3,"Bridge recovery and refused step commands do not consume material");
   read("release_control_session","session_id",session);
   System.out.println("JOB_STEPPING_PROTOTYPE "+G.toJson(Bridge.map("passed",true,"assertions",passed.size(),"checks",passed,"single_stepped_native_steps",previous,"paused_boundaries",paused,"native_feeds",feeder.getFeedCount(),"native_placements",2,"hardware_qualified",false,"prototype_only",true)));
  }catch(Throwable error){error.printStackTrace();exit=1;}finally{if(bridge!=null)bridge.close();machine.close();}
  System.exit(exit);
 }
}
