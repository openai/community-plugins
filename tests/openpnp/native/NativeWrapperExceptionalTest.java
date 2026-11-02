/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;
import com.google.gson.*;
import java.nio.file.*;
import java.lang.reflect.Field;
import java.util.*;
import java.util.concurrent.*;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.machine.reference.driver.ReferenceAdvancedMotionPlanner;
import org.openpnp.model.Configuration;
import org.openpnp.spi.*;
import org.openpnp.spi.MotionPlanner.CompletionType;

/** Actual native Future, with test-only listeners/gates and reflective cancellation. */
public final class NativeWrapperExceptionalTest {
 static final Gson JSON=new Gson();static int assertions;
 static void check(boolean b,String m){assertions++;if(!b)throw new AssertionError(m);}
 static JsonObject object(Object...pairs){return JSON.toJsonTree(Bridge.map(pairs)).getAsJsonObject();}
 @SuppressWarnings("unchecked")static Map<String,Object> call(Bridge b,String m,JsonObject p)throws Exception{return(Map<String,Object>)b.call(m,p);}
 static final class GatePlanner extends ReferenceAdvancedMotionPlanner{
  final CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);volatile boolean armed;int count;
  @Override public void waitForCompletion(HeadMountable tool,CompletionType type)throws Exception{
   if(armed&&type==CompletionType.CommandJog){armed=false;count++;entered.countDown();check(release.await(4,TimeUnit.SECONDS),"test-only CommandJog gate deadline");}
   super.waitForCompletion(tool,type);
  }
 }
 static Object field(Object o,String name)throws Exception{Field f=o.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(o);}
 static Map<String,Object> await(Bridge bridge,String id)throws Exception{
  long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(4);Map<String,Object> op;
  do{op=call(bridge,"openpnp_get_operation",object("operation_id",id));if(Set.of("succeeded","failed","outcome_unknown").contains(op.get("state")))return op;Thread.sleep(5);}while(System.nanoTime()<deadline);
  throw new AssertionError("native Future was not published "+op);
 }
 static long count(Path journal,String type,String state)throws Exception{
  long count=0;for(String line:Files.readAllLines(journal)){
   JsonObject e=new JsonParser().parse(line).getAsJsonObject();if(type.equals(e.get("type").getAsString())&&(state==null||state.equals(e.getAsJsonObject("payload").get("state").getAsString())))count++;
  }return count;
 }
 public static void main(String[] args)throws Exception{
  if(args.length==1){NativeCompletionCases.run(NativeWrapperExceptionalTest.class,Path.of(args[0]),"WRAPPER_COMPLETION_RESULT ","body-error","busy-finally-error","cancel-after-body","cancel-before-body");return;}
  String mode=args[0];Path root=Path.of(args[1]);Files.createDirectories(root.resolve("config"));
  JsonObject observation=new JsonObject();observation.addProperty("mode",mode);int exit=0;Bridge bridge=null;ReferenceMachine machine=null;GatePlanner planner=new GatePlanner();
  boolean cancellation=mode.startsWith("cancel"),stuckBusy=mode.equals("busy-finally-error");
  CountDownLatch preBodyEntered=new CountDownLatch(1),preBodyRelease=new CountDownLatch(1);int[] disableCallbacks={0},busyCallbacks={0};
  MachineListener listener=new MachineListener.Adapter(){
   @Override public void machineBusy(Machine m,boolean busy){
    busyCallbacks[0]++;
    if(busy&&mode.equals("cancel-before-body")){preBodyEntered.countDown();try{check(preBodyRelease.await(4,TimeUnit.SECONDS),"test pre-body listener gate deadline");}catch(InterruptedException e){throw new AssertionError(e);}}
    if(!busy&&stuckBusy)throw new AssertionError("CONTROLLED_NATIVE_BUSY_FINALLY_ERROR");
   }
   @Override public void machineDisabled(Machine m,String reason){disableCallbacks[0]++;if(mode.equals("body-error"))throw new AssertionError("CONTROLLED_NATIVE_BODY_ERROR_AFTER_DISABLE");}
  };
  try{
   Configuration.initialize(root.resolve("config").toFile());Configuration config=Configuration.get();config.load();SimulatorMain.accelerateFixture(config);
   machine=(ReferenceMachine)config.getMachine();check(!machine.isEnabled(),"fresh disabled simulator");machine.setMotionPlanner(planner);
   Files.writeString(root.resolve("token"),UUID.randomUUID().toString()+UUID.randomUUID(),StandardOpenOption.CREATE_NEW);
   bridge=new Bridge(config,root.resolve("token"),root.resolve("journal"),Path.of(args[2]),0,true);
   machine.addListener(listener);
   String session=(String)call(bridge,"openpnp_request_control_session",object("request_id",UUID.randomUUID().toString(),"ttl_seconds",300)).get("session_id");
   JsonObject input=object("request_id",UUID.randomUUID().toString(),"session_id",session,"enabled",false);
   planner.armed=mode.equals("cancel-after-body");
   Map<String,Object> accepted=call(bridge,"openpnp_set_machine_enabled",input);String id=(String)accepted.get("operation_id");
   if(cancellation){
    check((mode.equals("cancel-after-body")?planner.entered:preBodyEntered).await(3,TimeUnit.SECONDS),"actual native wrapper reached controlled gate");
    Future<?> future=(Future<?>)field(field(bridge,"pendingSubmission"),"future");
    check(future.cancel(false),"test-only cancellation accepted on actual native Future");
    check(machine.isBusy(),"cancelled Future did not stop running native wrapper");
   }
   Map<String,Object> after=await(bridge,id);observation.add("operation",JSON.toJsonTree(after));
   check("outcome_unknown".equals(after.get("state")),"native exceptional path publishes unknown");
   Map<?,?> result=(Map<?,?>)after.get("result");check((cancellation?"NATIVE_FUTURE_CANCELLED":"NATIVE_WRAPPER_FAILED").equals(result.get("code")),"exception classification preserved");
   Map<?,?> known=(Map<?,?>)result.get("known_body_outcome");
   if(mode.equals("body-error")||mode.equals("cancel-before-body"))check(Boolean.FALSE.equals(known.get("captured")),"no fabricated staged body result");
   else{check(Boolean.TRUE.equals(known.get("captured")),"known staged result retained");check(Boolean.FALSE.equals(((Map<?,?>)known.get("result")).get("enabled")),"known disabled state retained");}
   if(mode.equals("body-error")){check(Boolean.TRUE.equals(after.get("native_effect_pending")),"error after disable preserves unmatched native intent");check(((Map<?,?>)known.get("body_failure")).get("type").equals("java.lang.AssertionError"),"body Error diagnostic retained");}
   if(stuckBusy){
    long idleDeadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(3);while(machine.isBusy()&&System.nanoTime()<idleDeadline)Thread.sleep(5);
    check(!machine.isBusy(),"pinned native afterExecute finally clears task thread after listener failure");
    check(busyCallbacks[0]==3,"native listener observed busy plus both failing idle notifications");
    observation.addProperty("native_after_execute_cleared_busy",true);
   }
   long journalSize=Files.size(root.resolve("journal/operations.jsonl"));
   if(cancellation){
    check(((Map<?,?>)call(bridge,"openpnp_get_status",new JsonObject()).get("native_submission")).get("ownership_retained").equals(true),"cancelled submission keeps ownership fence");
    planner.release.countDown();preBodyRelease.countDown();
    long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(3);while(machine.isBusy()&&System.nanoTime()<deadline)Thread.sleep(5);
    check(!machine.isBusy(),"native wrapper eventually returned independently of cancellation");
    Map<String,Object> still=call(bridge,"openpnp_get_operation",object("operation_id",id));check("outcome_unknown".equals(still.get("state")),"no post-cancellation upgrade");
    check(disableCallbacks[0]==(mode.equals("cancel-before-body")?0:1),"cancel before body cannot dispatch effect; after body cannot repeat it");
    if(mode.equals("cancel-after-body"))check(Files.size(root.resolve("journal/operations.jsonl"))==journalSize,"completed cancelled wrapper triggers no extra publication");
    try{bridge.close();throw new AssertionError("cancelled owner closed normally");}catch(Bridge.Fault f){check("BUSY".equals(f.code),"cancelled owner close remains fenced");}
   }
   Map<String,Object> same=call(bridge,"openpnp_set_machine_enabled",input);check(id.equals(same.get("operation_id")),"same request identity preserved without redispatch");
   for(int i=0;i<5;i++)call(bridge,"openpnp_get_operation",object("operation_id",id));
   check(count(root.resolve("journal/operations.jsonl"),"operation","outcome_unknown")==1,"exactly one forced unknown receipt");
   check(count(root.resolve("journal/operations.jsonl"),"operation","succeeded")==0,"no false success record");
   check(!machine.isEnabled()&&!machine.isHomed(),"disabled and unhomed throughout");
   observation.addProperty("disable_callbacks",disableCallbacks[0]);observation.addProperty("busy_callbacks",busyCallbacks[0]);observation.addProperty("test_only_actual_future_cancel",cancellation);
  }catch(Throwable failure){failure.printStackTrace();observation.addProperty("failure",failure.toString());exit=1;}
  finally{
   planner.release.countDown();preBodyRelease.countDown();
   if(machine!=null)machine.removeListener(listener);
   if(bridge!=null&&!cancellation)try{bridge.close();}catch(Exception failure){observation.addProperty("cleanup_bridge_error",failure.toString());exit=1;}
   if(machine!=null&&!cancellation&&!stuckBusy)try{machine.close();}catch(Exception failure){exit=1;}
  }
  observation.addProperty("passed",exit==0);observation.addProperty("assertions",assertions);observation.addProperty("physical_qualification",false);observation.addProperty("motion_commands",0);
  Files.writeString(root.resolve("observation.json"),observation.toString()+"\n",StandardOpenOption.CREATE_NEW);System.out.println("WRAPPER_COMPLETION_RESULT "+observation);System.exit(exit);
 }
}
